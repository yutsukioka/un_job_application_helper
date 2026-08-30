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
        let attempts = try await withThrowingTaskGroup(
            of: AtlasVaultHPKESealedVaultKeyV2.self
        ) { group in
            for _ in 0..<48 {
                group.addTask { try self.seal(vector) }
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
                    recipientPrivateKey: data(vector, "recipient_private_key_hex"),
                    sealed: sealed,
                    context: data(vector, "context_hex")
                ),
                try data(vector, "vault_key_hex")
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
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("../../contracts/sync/test_vectors/atlasvault_hpke_key_delivery_vectors_v2.json"),
            source.appendingPathComponent("../../../../contracts/sync/test_vectors/atlasvault_hpke_key_delivery_vectors_v2.json"),
        ]
        guard let url = candidates.first(where: {
            FileManager.default.fileExists(atPath: $0.path)
        }) else {
            throw NSError(domain: "AtlasVaultHPKETests", code: 1)
        }
        guard
            let root = try JSONSerialization.jsonObject(
                with: Data(contentsOf: url)
            ) as? [String: Any],
            root["format"] as? String == "atlasvault-hpke-key-delivery-vectors",
            root["version"] as? Int == 2,
            let vector = root["single_shot"] as? [String: Any]
        else {
            throw NSError(domain: "AtlasVaultHPKETests", code: 2)
        }
        return vector
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
}
