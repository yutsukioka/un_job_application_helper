import CryptoKit
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultHPKEKeyDeliveryTests: XCTestCase {
    func testHPKEV2MatchesCrossLanguageSingleShotVector() throws {
        let vector = try loadVector()
        let expected = AtlasVaultHPKESealedVaultKeyV2(
            encapsulatedKey: try data(vector, "encapsulated_key_hex"),
            ciphertext: try data(vector, "ciphertext_hex")
        )

        XCTAssertEqual(
            try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                recipientPrivateKey: try data(vector, "recipient_private_key_hex"),
                sealed: expected,
                context: try data(vector, "context_hex")
            ),
            try data(vector, "vault_key_hex")
        )
    }

    func testHPKEV2MatchesOfficialRFC9180VectorByteExact() throws {
        let vector = try loadOfficialVector()
        let sender = try Curve25519.KeyAgreement.PrivateKey(
            rawRepresentation: data(vector, "sender_ephemeral_private_key_hex")
        )
        let recipient = try Curve25519.KeyAgreement.PublicKey(
            rawRepresentation: data(vector, "recipient_public_key_hex")
        )
        let encapsulated = sender.publicKey.rawRepresentation
        let dh = try sender.sharedSecretFromKeyAgreement(with: recipient)
            .withUnsafeBytes { Data($0) }
        let kemSuite = Data("KEM".utf8) + i2osp(0x0020, length: 2)
        let eaePRK = labeledExtract(
            suite: kemSuite,
            salt: Data(),
            label: "eae_prk",
            input: dh
        )
        let sharedSecret = labeledExpand(
            suite: kemSuite,
            key: eaePRK,
            label: "shared_secret",
            info: encapsulated + recipient.rawRepresentation,
            length: 32
        )
        let suite = Data("HPKE".utf8)
            + i2osp(0x0020, length: 2)
            + i2osp(0x0001, length: 2)
            + i2osp(0x0002, length: 2)
        let pskIDHash = labeledExtract(
            suite: suite,
            salt: Data(),
            label: "psk_id_hash",
            input: Data()
        )
        let infoHash = labeledExtract(
            suite: suite,
            salt: Data(),
            label: "info_hash",
            input: try data(vector, "info_hex")
        )
        let scheduleContext = Data([0]) + pskIDHash + infoHash
        let secret = labeledExtract(
            suite: suite,
            salt: sharedSecret,
            label: "secret",
            input: Data()
        )
        let key = labeledExpand(
            suite: suite,
            key: secret,
            label: "key",
            info: scheduleContext,
            length: 32
        )
        let baseNonce = labeledExpand(
            suite: suite,
            key: secret,
            label: "base_nonce",
            info: scheduleContext,
            length: 12
        )

        XCTAssertEqual(encapsulated, try data(vector, "encapsulated_key_hex"))
        XCTAssertEqual(sharedSecret, try data(vector, "shared_secret_hex"))
        XCTAssertEqual(key, try data(vector, "key_hex"))
        XCTAssertEqual(baseNonce, try data(vector, "base_nonce_hex"))
        let sealed = try AES.GCM.seal(
            data(vector, "plaintext_hex"),
            using: SymmetricKey(data: key),
            nonce: AES.GCM.Nonce(data: baseNonce),
            authenticating: data(vector, "aad_hex")
        )
        XCTAssertEqual(
            sealed.ciphertext + sealed.tag,
            try data(vector, "ciphertext_hex")
        )

        let nativeSuite = HPKE.Ciphersuite(
            kem: .Curve25519_HKDF_SHA256,
            kdf: .HKDF_SHA256,
            aead: .AES_GCM_256
        )
        let recipientPrivate = try Curve25519.KeyAgreement.PrivateKey(
            rawRepresentation: data(vector, "recipient_private_key_hex")
        )
        var nativeRecipient = try HPKE.Recipient(
            privateKey: recipientPrivate,
            ciphersuite: nativeSuite,
            info: data(vector, "info_hex"),
            encapsulatedKey: encapsulated
        )
        XCTAssertEqual(
            try nativeRecipient.open(
                data(vector, "ciphertext_hex"),
                authenticating: data(vector, "aad_hex")
            ),
            try data(vector, "plaintext_hex")
        )
    }

    func testHPKEV2RevisionStressAndCrashRetryOwnFreshEntropy() throws {
        let vector = try loadVector()
        let attempts = try (0..<96).map { _ in try seal(vector) }
        XCTAssertEqual(Set(attempts.map(\.encapsulatedKey)).count, 96)
        XCTAssertEqual(Set(attempts.map(\.ciphertext)).count, 96)

        let abandoned = try seal(vector)
        let recovered = try seal(vector)
        XCTAssertNotEqual(abandoned.encapsulatedKey, recovered.encapsulatedKey)
        XCTAssertNotEqual(abandoned.ciphertext, recovered.ciphertext)
    }

    func testHPKEV2ConcurrentAttemptsAreUniqueAndOpen() async throws {
        let vector = try loadVector()
        let recipientPublicKey = try data(vector, "recipient_public_key_hex")
        let recipientPrivateKey = try data(vector, "recipient_private_key_hex")
        let vaultKey = try data(vector, "vault_key_hex")
        let context = try data(vector, "context_hex")
        let attempts = try await withThrowingTaskGroup(
            of: AtlasVaultHPKESealedVaultKeyV2.self
        ) { group in
            for _ in 0..<48 {
                group.addTask {
                    try AtlasVaultHPKEKeyDelivery.sealVaultKeyV2(
                        recipientPublicKey: recipientPublicKey,
                        vaultKey: vaultKey,
                        context: context
                    )
                }
            }
            var values: [AtlasVaultHPKESealedVaultKeyV2] = []
            for try await value in group {
                values.append(value)
            }
            return values
        }

        XCTAssertEqual(Set(attempts.map(\.encapsulatedKey)).count, 48)
        XCTAssertEqual(Set(attempts.map(\.ciphertext)).count, 48)
        for sealed in attempts {
            XCTAssertEqual(
                try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                    recipientPrivateKey: recipientPrivateKey,
                    sealed: sealed,
                    context: context
                ),
                vaultKey
            )
        }
    }

    func testHPKEV2RejectsWrongContextAndCiphertextTamper() throws {
        let vector = try loadVector()
        let sealed = try seal(vector)
        XCTAssertThrowsError(
            try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                recipientPrivateKey: data(vector, "recipient_private_key_hex"),
                sealed: sealed,
                context: Data("wrong-context".utf8)
            )
        )

        var ciphertext = sealed.ciphertext
        ciphertext[ciphertext.index(before: ciphertext.endIndex)] ^= 1
        XCTAssertThrowsError(
            try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                recipientPrivateKey: data(vector, "recipient_private_key_hex"),
                sealed: AtlasVaultHPKESealedVaultKeyV2(
                    encapsulatedKey: sealed.encapsulatedKey,
                    ciphertext: ciphertext
                ),
                context: data(vector, "context_hex")
            )
        )
    }

    func testHPKEV2PropertyRoundTripsDeterministicInputs() throws {
        for index in 0..<32 {
            let recipientPrivate = seed("recipient-\(index)")
            let recipient = try Curve25519.KeyAgreement.PrivateKey(
                rawRepresentation: recipientPrivate
            )
            let vaultKey = seed("vault-key-\(index)")
            let context = seed("context-\(index)").prefix(index + 1)
            let sealed = try AtlasVaultHPKEKeyDelivery.sealVaultKeyV2(
                recipientPublicKey: recipient.publicKey.rawRepresentation,
                vaultKey: vaultKey,
                context: Data(context)
            )

            XCTAssertEqual(
                try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                    recipientPrivateKey: recipientPrivate,
                    sealed: sealed,
                    context: Data(context)
                ),
                vaultKey
            )
        }
    }

    func testHPKEV2RejectsCompoundMutationsAndWrongRecipient() throws {
        let vector = try loadVector()
        let sealed = try seal(vector)
        let recipient = try data(vector, "recipient_private_key_hex")
        let context = try data(vector, "context_hex")

        for index in 0..<64 {
            var payload = sealed.ciphertext
            let first = payload.index(
                payload.startIndex,
                offsetBy: index % payload.count
            )
            let second = payload.index(
                payload.startIndex,
                offsetBy: (index * 13 + 7) % payload.count
            )
            payload[first] ^= UInt8(1 << (index % 8))
            payload[second] ^= UInt8(1 << ((index + 3) % 8))
            XCTAssertThrowsError(
                try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                    recipientPrivateKey: recipient,
                    sealed: AtlasVaultHPKESealedVaultKeyV2(
                        encapsulatedKey: sealed.encapsulatedKey,
                        ciphertext: payload
                    ),
                    context: context
                )
            )
        }

        for index in 0..<32 {
            var encapsulated = sealed.encapsulatedKey
            let position = encapsulated.index(
                encapsulated.startIndex,
                offsetBy: index
            )
            encapsulated[position] ^= UInt8(1 << (index % 8))
            XCTAssertThrowsError(
                try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                    recipientPrivateKey: recipient,
                    sealed: AtlasVaultHPKESealedVaultKeyV2(
                        encapsulatedKey: encapsulated,
                        ciphertext: sealed.ciphertext
                    ),
                    context: context
                )
            )
        }

        XCTAssertThrowsError(
            try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                recipientPrivateKey: seed("wrong-recipient"),
                sealed: sealed,
                context: context
            )
        )
    }

    func testHPKEV2RejectsMalformedLengthsAndContextBoundaries() throws {
        let vector = try loadVector()
        let recipientPublic = try data(vector, "recipient_public_key_hex")
        let recipientPrivate = try data(vector, "recipient_private_key_hex")
        let vaultKey = try data(vector, "vault_key_hex")

        for length in [0, 1, 31, 33] {
            XCTAssertThrowsError(
                try AtlasVaultHPKEKeyDelivery.sealVaultKeyV2(
                    recipientPublicKey: Data(repeating: 0, count: length),
                    vaultKey: vaultKey,
                    context: Data([1])
                )
            )
            XCTAssertThrowsError(
                try AtlasVaultHPKEKeyDelivery.sealVaultKeyV2(
                    recipientPublicKey: recipientPublic,
                    vaultKey: Data(repeating: 0, count: length),
                    context: Data([1])
                )
            )
        }

        for context in [Data(), Data(repeating: 0, count: 4_097)] {
            XCTAssertThrowsError(
                try AtlasVaultHPKEKeyDelivery.sealVaultKeyV2(
                    recipientPublicKey: recipientPublic,
                    vaultKey: vaultKey,
                    context: context
                )
            )
        }

        let context = Data(repeating: 0, count: 4_096)
        let sealed = try AtlasVaultHPKEKeyDelivery.sealVaultKeyV2(
            recipientPublicKey: recipientPublic,
            vaultKey: vaultKey,
            context: context
        )
        XCTAssertEqual(
            try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                recipientPrivateKey: recipientPrivate,
                sealed: sealed,
                context: context
            ),
            vaultKey
        )
    }

    private func seal(
        _ vector: [String: Any]
    ) throws -> AtlasVaultHPKESealedVaultKeyV2 {
        try AtlasVaultHPKEKeyDelivery.sealVaultKeyV2(
            recipientPublicKey: data(vector, "recipient_public_key_hex"),
            vaultKey: data(vector, "vault_key_hex"),
            context: data(vector, "context_hex")
        )
    }

    private func loadVector() throws -> [String: Any] {
        let root = try loadVectorRoot()
        guard let vector = root["single_shot"] as? [String: Any] else {
            throw NSError(domain: "AtlasVaultHPKETests", code: 2)
        }
        return vector
    }

    private func loadOfficialVector() throws -> [String: Any] {
        let root = try loadVectorRoot()
        guard let vector = root["official_rfc9180"] as? [String: Any] else {
            throw NSError(domain: "AtlasVaultHPKETests", code: 2)
        }
        return vector
    }

    private func loadVectorRoot() throws -> [String: Any] {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent(
                    "../../contracts/sync/test_vectors/atlasvault_hpke_key_delivery_vectors_v2.json"
                ),
            source.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/atlasvault_hpke_key_delivery_vectors_v2.json"
            ),
        ]
        guard
            let url = candidates.first(where: {
                FileManager.default.fileExists(atPath: $0.path)
            })
        else {
            throw NSError(domain: "AtlasVaultHPKETests", code: 1)
        }
        guard
            let root = try JSONSerialization.jsonObject(
                with: Data(contentsOf: url)
            ) as? [String: Any],
            root["format"] as? String == "atlasvault-hpke-key-delivery-vectors",
            root["version"] as? Int == 2
        else {
            throw NSError(domain: "AtlasVaultHPKETests", code: 2)
        }
        return root
    }

    private func data(_ vector: [String: Any], _ field: String) throws -> Data {
        guard let value = vector[field] as? String, value.count.isMultiple(of: 2) else {
            throw NSError(domain: "AtlasVaultHPKETests", code: 3)
        }
        var result = Data()
        var index = value.startIndex
        while index < value.endIndex {
            let end = value.index(index, offsetBy: 2)
            guard let byte = UInt8(value[index..<end], radix: 16) else {
                throw NSError(domain: "AtlasVaultHPKETests", code: 4)
            }
            result.append(byte)
            index = end
        }
        return result
    }

    private func seed(_ label: String) -> Data {
        Data(SHA256.hash(data: Data(label.utf8)))
    }

    private func labeledExtract(
        suite: Data,
        salt: Data,
        label: String,
        input: Data
    ) -> Data {
        let key = SymmetricKey(
            data: salt.isEmpty ? Data(repeating: 0, count: 32) : salt
        )
        return Data(HMAC<SHA256>.authenticationCode(
            for: Data("HPKE-v1".utf8) + suite + Data(label.utf8) + input,
            using: key
        ))
    }

    private func labeledExpand(
        suite: Data,
        key: Data,
        label: String,
        info: Data,
        length: Int
    ) -> Data {
        let labeled = i2osp(length, length: 2)
            + Data("HPKE-v1".utf8)
            + suite
            + Data(label.utf8)
            + info
        var output = Data()
        var previous = Data()
        var counter: UInt8 = 1
        while output.count < length {
            previous = Data(HMAC<SHA256>.authenticationCode(
                for: previous + labeled + Data([counter]),
                using: SymmetricKey(data: key)
            ))
            output.append(previous)
            counter += 1
        }
        return output.prefix(length)
    }

    private func i2osp(_ value: Int, length: Int) -> Data {
        Data((0..<length).reversed().map { shift in
            UInt8((value >> (shift * 8)) & 0xff)
        })
    }
}
