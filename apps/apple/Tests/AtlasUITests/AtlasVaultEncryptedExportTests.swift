import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultEncryptedExportTests: XCTestCase {
    func testStrictCanonicalEncryptedExportSurfaceExists() throws {
        let source = try phaseSource("AtlasVaultEncryptedExport.swift")

        for required in [
            "atlasvault-export",
            "supportedVersion = 1",
            "export_id",
            "created_at",
            "vault_metadata",
            "records",
            "sortedKeys",
            "AtlasVault-Encrypted-Backup.atlasvault",
            "FileDocument",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "store_id",
            "selected_vault",
            "Keychain",
            "plaintext",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testSwiftCanonicalExportMatchesSharedPythonBytes() throws {
        let vector = try loadVector()
        let expected = try strictBase64(
            try string(vector["canonical_export_json_b64"])
        )
        let exportObject = try dictionary(vector["export"])
        let input = try JSONSerialization.data(
            withJSONObject: exportObject,
            options: [.sortedKeys]
        )

        let envelope = try AtlasVaultEncryptedExportEnvelope.decodeStrict(
            input
        )
        let canonical = try envelope.canonicalData()

        XCTAssertEqual(canonical, expected)
        XCTAssertEqual(envelope.format, "atlasvault-export")
        XCTAssertEqual(envelope.version, 1)
        XCTAssertEqual(envelope.records, [])
        XCTAssertEqual(
            envelope.vaultMetadata.vaultID,
            try string(vector["vault_id"])
        )
        XCTAssertNotNil(envelope.vaultMetadata.recoveryKeyWrap)
    }

    func testStrictDecoderAcceptsPassphraseOnlyV1Metadata() throws {
        let vector = try loadVector()
        var exportObject = try dictionary(vector["export"])
        exportObject["vault_metadata"] = try loadPassphraseMetadata()
        let input = try JSONSerialization.data(
            withJSONObject: exportObject,
            options: [.sortedKeys]
        )

        let envelope = try AtlasVaultEncryptedExportEnvelope.decodeStrict(
            input
        )
        let canonical = try envelope.canonicalData()
        let document = try AtlasVaultEncryptedDocument(
            verifiedEncryptedData: canonical
        )

        XCTAssertNil(envelope.vaultMetadata.recoveryKeyWrap)
        XCTAssertEqual(envelope.vaultMetadata.keyWraps.count, 1)
        guard case .passphrase = envelope.vaultMetadata.keyWraps[0] else {
            return XCTFail("Expected historical passphrase v1 metadata")
        }
        XCTAssertEqual(document.encryptedData, canonical)
    }

    func testCanonicalExportUsesPythonASCIIEscapingForRecordText() throws {
        let vector = try loadVector()
        let exportObject = try dictionary(vector["export"])
        let input = try JSONSerialization.data(
            withJSONObject: exportObject,
            options: [.sortedKeys]
        )
        let base = try AtlasVaultEncryptedExportEnvelope.decodeStrict(input)
        let nonce = Data(
            repeating: 1,
            count: AtlasVaultRecordCrypto.nonceByteCount
        ).base64EncodedString()
        let ciphertext = Data(
            repeating: 2,
            count: AtlasVaultRecordCrypto.gcmTagByteCount + 1
        ).base64EncodedString()
        let record = AtlasVaultEncryptedRecordEnvelope(
            id: "record/\u{00E9}",
            schemaVersion: 1,
            revision: "revision/\u{00E9}",
            parentRevision: "parent/\u{1F680}",
            deleted: false,
            keyID: "recovery/key-\u{00E9}",
            nonce: nonce,
            ciphertext: ciphertext
        )
        let envelope = try AtlasVaultEncryptedExportEnvelope(
            exportID: base.exportID,
            createdAt: base.createdAt,
            vaultMetadata: base.vaultMetadata,
            records: [record]
        )
        let canonical = try envelope.canonicalData()
        let sharedCanonical = try XCTUnwrap(
            String(
                data: try strictBase64(
                    try string(vector["canonical_export_json_b64"])
                ),
                encoding: .utf8
            )
        )
        let expectedRecord =
            #"{"ciphertext":"\#(ciphertext)","deleted":false,"id":"record/\u00e9","key_id":"recovery/key-\u00e9","nonce":"\#(nonce)","parent_revision":"parent/\ud83d\ude80","revision":"revision/\u00e9","schema_version":1}"#
        let expected = sharedCanonical.replacingOccurrences(
            of: "\"records\":[]",
            with: "\"records\":[\(expectedRecord)]"
        )

        XCTAssertNotEqual(expected, sharedCanonical)
        XCTAssertEqual(canonical, Data(expected.utf8))
        XCTAssertFalse(canonical.contains(Data("\u{00E9}".utf8)))
        XCTAssertFalse(canonical.contains(Data("\u{1F680}".utf8)))
        XCTAssertNoThrow(
            try AtlasVaultEncryptedExportEnvelope.decodeStrict(canonical)
        )
    }

    func testExportBytesContainNoLocalOrRawSecretMaterial() throws {
        let vector = try loadVector()
        let canonical = try strictBase64(
            try string(vector["canonical_export_json_b64"])
        )
        let text = try XCTUnwrap(String(data: canonical, encoding: .utf8))

        for excluded in [
            "store_id",
            "selected_vault",
            "Keychain",
            try string(vector["test_only_recovery_key_b64"]),
            try string(vector["test_only_vault_key_b64"]),
            try string(vector["canonical_recovery_text"]),
        ] {
            XCTAssertFalse(text.contains(excluded))
        }
    }

    func testStrictDecoderRejectsUnknownTopLevelAndMetadataKeys() throws {
        let vector = try loadVector()
        for path in ["top", "metadata", "crypto", "wrap", "wrapKDF"] {
            var object = try dictionary(vector["export"])
            switch path {
            case "top":
                object["unexpected"] = true
            case "metadata":
                var metadata = try dictionary(object["vault_metadata"])
                metadata["unexpected"] = true
                object["vault_metadata"] = metadata
            case "crypto":
                var metadata = try dictionary(object["vault_metadata"])
                var crypto = try dictionary(metadata["crypto"])
                crypto["unexpected"] = true
                metadata["crypto"] = crypto
                object["vault_metadata"] = metadata
            case "wrapKDF":
                var metadata = try dictionary(object["vault_metadata"])
                var wraps = try array(metadata["key_wraps"])
                var wrap = try dictionary(wraps[0])
                var kdf = try dictionary(wrap["kdf"])
                kdf["unexpected"] = true
                wrap["kdf"] = kdf
                wraps[0] = wrap
                metadata["key_wraps"] = wraps
                object["vault_metadata"] = metadata
            default:
                var metadata = try dictionary(object["vault_metadata"])
                var wraps = try array(metadata["key_wraps"])
                var wrap = try dictionary(wraps[0])
                wrap["unexpected"] = true
                wraps[0] = wrap
                metadata["key_wraps"] = wraps
                object["vault_metadata"] = metadata
            }
            let data = try JSONSerialization.data(
                withJSONObject: object,
                options: [.sortedKeys]
            )

            XCTAssertThrowsError(
                try AtlasVaultEncryptedExportEnvelope.decodeStrict(data)
            )
        }
    }

    func testStrictDecoderRejectsInvalidVersionIdentifierAndTimestamp() throws {
        let vector = try loadVector()
        for (field, value): (String, Any) in [
            ("version", 2),
            ("export_id", "not-a-uuid"),
            ("export_id", "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"),
            ("created_at", "2026-01-02T03:04:05.000Z"),
            ("created_at", "2026-01-02T03:04:05+00:00"),
        ] {
            var object = try dictionary(vector["export"])
            object[field] = value
            let data = try JSONSerialization.data(
                withJSONObject: object,
                options: [.sortedKeys]
            )

            XCTAssertThrowsError(
                try AtlasVaultEncryptedExportEnvelope.decodeStrict(data)
            )
        }
    }

    func testStrictDecoderRejectsFloatingPointVersionTokensAtEveryLevel()
        throws
    {
        let vector = try loadVector()
        let canonical = try XCTUnwrap(
            String(
                data: try strictBase64(
                    try string(vector["canonical_export_json_b64"])
                ),
                encoding: .utf8
            )
        )
        let rootSuffix = #""version":1}"#
        XCTAssertTrue(canonical.hasSuffix(rootSuffix))
        let rootFloat =
            String(canonical.dropLast(rootSuffix.count)) + #""version":1.0}"#
        let metadataFloat = canonical.replacingOccurrences(
            of: #""version":1},"version":1}"#,
            with: #""version":1.0},"version":1}"#
        )
        let wrapFloat = canonical.replacingOccurrences(
            of: #""wrap_version":2}"#,
            with: #""wrap_version":2.0}"#
        )

        XCTAssertNotEqual(rootFloat, canonical)
        XCTAssertNotEqual(metadataFloat, canonical)
        XCTAssertNotEqual(wrapFloat, canonical)
        for malformed in [rootFloat, metadataFloat, wrapFloat] {
            XCTAssertThrowsError(
                try AtlasVaultEncryptedExportEnvelope.decodeStrict(
                    Data(malformed.utf8)
                )
            ) { error in
                XCTAssertFalse(String(describing: error).contains("1.0"))
                XCTAssertFalse(String(describing: error).contains("2.0"))
            }
        }
    }

    func testEncryptedDocumentUsesOnlyVerifiedCanonicalBytes() throws {
        let vector = try loadVector()
        let canonical = try strictBase64(
            try string(vector["canonical_export_json_b64"])
        )

        let document = try AtlasVaultEncryptedDocument(
            verifiedEncryptedData: canonical
        )

        XCTAssertEqual(document.encryptedData, canonical)
        XCTAssertEqual(
            AtlasVaultEncryptedExportEnvelope.defaultFilename,
            "AtlasVault-Encrypted-Backup.atlasvault"
        )
    }

    private func loadVector() throws -> [String: Any] {
        let data = try Data(
            contentsOf: try vectorFileURL(
                named: "atlasvault_recovery_export_vectors_v2.json"
            )
        )
        let root = try dictionary(
            try JSONSerialization.jsonObject(with: data)
        )
        return try dictionary(try array(root["vectors"]).first)
    }

    private func loadPassphraseMetadata() throws -> [String: Any] {
        let data = try Data(
            contentsOf: try vectorFileURL(
                named: "atlasvault_key_wrap_vectors_v1.json"
            )
        )
        let root = try dictionary(
            try JSONSerialization.jsonObject(with: data)
        )
        let vector = try dictionary(try array(root["vectors"]).first)
        return try dictionary(vector["vault_metadata"])
    }

    private func vectorFileURL(named name: String) throws -> URL {
        let current = URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath
        )
        let source = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
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
