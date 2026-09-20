import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRecoveryKeyTests: XCTestCase {
    func testRecoveryKeyCodecAndVaultBoundWrapSurfaceExists() throws {
        let source = try phaseSource("AtlasVaultRecoveryKey.swift")

        for required in [
            "SecRandomCopyBytes",
            "AVRK1-",
            "atlasvault-recovery-key-v1:",
            "HKDF<SHA256>",
            "AES.GCM",
            "atlas-vault-recovery-wrap-v2",
            "vault_id",
            "constantTime",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "UInt8.random",
            "Task.detached",
            "print(",
            "Logger",
            "os_log",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testCodecMatchesSharedVectorAndAcceptedNormalization() throws {
        let vector = try loadVector()
        let raw = try strictBase64(try string(
            vector["test_only_recovery_key_b64"]
        ))
        let canonical = try string(vector["canonical_recovery_text"])

        XCTAssertEqual(raw.count, 32)
        XCTAssertEqual(
            try AtlasVaultRecoveryKeyCodec.canonicalText(for: raw),
            canonical
        )
        XCTAssertEqual(
            try AtlasVaultRecoveryKeyCodec.parse(canonical),
            raw
        )
        XCTAssertEqual(
            try AtlasVaultRecoveryKeyCodec.parse(
                " \t" + canonical.lowercased()
                    .replacingOccurrences(of: "-", with: " ") + "\r\n"
            ),
            raw
        )
        XCTAssertEqual(
            canonical.dropFirst("AVRK1-".count)
                .filter { $0 != "-" }
                .count,
            60
        )
        XCTAssertEqual(canonical.split(separator: "-").dropFirst().count, 15)
    }

    func testCodecRejectsMalformedAndUnicodeInputWithoutEcho() throws {
        let canonical = try string(
            loadVector()["canonical_recovery_text"]
        )
        let invalid = [
            "-" + canonical,
            canonical + "-",
            canonical.replacingOccurrences(of: "AVRK1", with: "AVRK2"),
            canonical + "=",
            canonical.replacingOccurrences(
                of: "AAAQ",
                with: "AAA0"
            ),
            canonical.replacingOccurrences(
                of: "AAAQ",
                with: "AAA1"
            ),
            canonical.replacingOccurrences(
                of: "AAAQ",
                with: "AAA8"
            ),
            String(canonical.dropLast()),
            canonical + "A",
            String(canonical.dropLast()) + "\u{041E}",
        ]

        for value in invalid {
            XCTAssertThrowsError(try AtlasVaultRecoveryKeyCodec.parse(value)) {
                error in
                XCTAssertEqual(
                    error as? AtlasVaultRecoveryKeyError,
                    .invalidRecoveryKey
                )
                XCTAssertFalse(String(describing: error).contains(value))
            }
        }
    }

    func testRecoveryWrapFailureUsesFixedHyphenatedWording() {
        XCTAssertEqual(
            AtlasVaultRecoveryKeyError.invalidWrap.description,
            "Recovery key-wrap is invalid."
        )
        XCTAssertFalse(
            AtlasVaultRecoveryKeyError.invalidWrap.description.contains(
                "key wrap"
            )
        )
    }

    func testChecksumMismatchFailsAndGeneratedKeyHasRequiredLength() throws {
        let canonical = try string(
            loadVector()["canonical_recovery_text"]
        )
        let replacement = canonical.last == "A" ? "B" : "A"
        let changed = String(canonical.dropLast()) + replacement

        XCTAssertThrowsError(try AtlasVaultRecoveryKeyCodec.parse(changed))
        XCTAssertEqual(
            try AtlasVaultRecoveryKeyCodec.generate().count,
            AtlasVaultRecoveryKeyCodec.rawByteCount
        )
    }

    func testWrapAADAndCiphertextMatchSharedPythonVector() throws {
        let vector = try loadVector()
        let recoveryKey = try strictBase64(try string(
            vector["test_only_recovery_key_b64"]
        ))
        let vaultKey = try strictBase64(try string(
            vector["test_only_vault_key_b64"]
        ))
        let wrap = try AtlasVaultRecoveryWrapCrypto.wrap(
            vaultKey: vaultKey,
            recoveryKey: recoveryKey,
            vaultID: try string(vector["vault_id"]),
            salt: try strictBase64(try string(vector["salt_b64"])),
            nonce: try strictBase64(try string(vector["nonce_b64"]))
        )
        let expectedWrap = try decodeWrap(vector["recovery_wrap"])
        let aad = try AtlasVaultRecoveryWrapCrypto.associatedData(
            vaultID: try string(vector["vault_id"]),
            wrap: wrap
        )

        XCTAssertEqual(wrap, expectedWrap)
        XCTAssertEqual(
            aad,
            try strictBase64(try string(vector["key_wrap_aad_b64"]))
        )
        XCTAssertEqual(
            try JSONSerialization.jsonObject(with: aad) as? NSDictionary,
            try dictionary(vector["key_wrap_aad_json"]) as NSDictionary
        )
        XCTAssertEqual(
            try AtlasVaultRecoveryWrapCrypto.unwrap(
                wrap,
                recoveryKey: recoveryKey,
                vaultID: try string(vector["vault_id"])
            ),
            vaultKey
        )
    }

    func testWrongKeyAndWrongVaultFailWithFixedError() throws {
        let vector = try loadVector()
        let wrap = try decodeWrap(vector["recovery_wrap"])
        let wrongKey = try AtlasVaultRecoveryKeyCodec.parse(
            try string(vector["wrong_canonical_recovery_text"])
        )
        let correctKey = try strictBase64(try string(
            vector["test_only_recovery_key_b64"]
        ))

        for attempt in [
            {
                try AtlasVaultRecoveryWrapCrypto.unwrap(
                    wrap,
                    recoveryKey: wrongKey,
                    vaultID: try self.string(vector["vault_id"])
                )
            },
            {
                try AtlasVaultRecoveryWrapCrypto.unwrap(
                    wrap,
                    recoveryKey: correctKey,
                    vaultID: "21111111-2222-3333-4444-555555555555"
                )
            },
        ] {
            XCTAssertThrowsError(try attempt()) { error in
                XCTAssertEqual(
                    error as? AtlasVaultRecoveryKeyError,
                    .authenticationFailed
                )
                XCTAssertEqual(
                    String(describing: error),
                    "Recovery key verification failed."
                )
            }
        }
    }

    func testValidLengthSaltNonceAndCiphertextMutationsFailAuthentication()
        throws
    {
        let vector = try loadVector()
        let wrap = try decodeWrap(vector["recovery_wrap"])
        let recoveryKey = try strictBase64(try string(
            vector["test_only_recovery_key_b64"]
        ))
        let vaultID = try string(vector["vault_id"])
        var salt = wrap.kdf.salt
        salt[0] ^= 0x01
        var nonce = wrap.nonce
        nonce[0] ^= 0x01
        var ciphertext = wrap.ciphertext
        ciphertext[0] ^= 0x01
        let candidates = [
            try AtlasVaultRecoveryWrappedKeyEnvelope(
                kdf: AtlasVaultRecoveryWrapKDFParameters(salt: salt),
                nonce: wrap.nonce,
                ciphertext: wrap.ciphertext
            ),
            try AtlasVaultRecoveryWrappedKeyEnvelope(
                kdf: wrap.kdf,
                nonce: nonce,
                ciphertext: wrap.ciphertext
            ),
            try AtlasVaultRecoveryWrappedKeyEnvelope(
                kdf: wrap.kdf,
                nonce: wrap.nonce,
                ciphertext: ciphertext
            ),
        ]

        for candidate in candidates {
            XCTAssertThrowsError(
                try AtlasVaultRecoveryWrapCrypto.unwrap(
                    candidate,
                    recoveryKey: recoveryKey,
                    vaultID: vaultID
                )
            ) { error in
                XCTAssertEqual(
                    error as? AtlasVaultRecoveryKeyError,
                    .authenticationFailed
                )
            }
        }
    }

    func testSecretBuffersCanBeBestEffortWiped() {
        var buffer = Data(repeating: 0xa5, count: 32)

        AtlasVaultRecoveryKeyCodec.bestEffortWipe(&buffer)

        XCTAssertTrue(buffer.isEmpty)
    }

    private func loadVector() throws -> [String: Any] {
        let data = try Data(contentsOf: vectorFileURL())
        let root = try dictionary(
            try JSONSerialization.jsonObject(with: data)
        )
        return try dictionary(try array(root["vectors"]).first)
    }

    private func decodeWrap(
        _ value: Any?
    ) throws -> AtlasVaultRecoveryWrappedKeyEnvelope {
        try JSONDecoder().decode(
            AtlasVaultRecoveryWrappedKeyEnvelope.self,
            from: JSONSerialization.data(
                withJSONObject: try dictionary(value),
                options: [.sortedKeys]
            )
        )
    }

    private func vectorFileURL() throws -> URL {
        let current = URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath
        )
        let source = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let name = "atlasvault_recovery_export_vectors_v2.json"
        let candidates = [
            current.appendingPathComponent(
                "../../contracts/sync/test_vectors/\(name)"
            ),
            current.appendingPathComponent(
                "contracts/sync/test_vectors/\(name)"
            ),
            source.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/\(name)"
            ),
        ].map(\.standardizedFileURL)
        return try XCTUnwrap(
            candidates.first {
                FileManager.default.fileExists(atPath: $0.path)
            }
        )
    }

    private func strictBase64(_ value: String) throws -> Data {
        let data = try XCTUnwrap(Data(base64Encoded: value))
        XCTAssertEqual(data.base64EncodedString(), value)
        return data
    }

    private func dictionary(_ value: Any?) throws -> [String: Any] {
        try XCTUnwrap(value as? [String: Any])
    }

    private func array(_ value: Any?) throws -> [Any] {
        try XCTUnwrap(value as? [Any])
    }

    private func string(_ value: Any?) throws -> String {
        try XCTUnwrap(value as? String)
    }

    private func phaseSource(_ name: String) throws -> String {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
        return try String(contentsOf: sourceURL, encoding: .utf8)
    }
}
