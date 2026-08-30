import CryptoKit
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultKeyEpochRingTests: XCTestCase {
    func testEpochRingMatchesSharedMetadataAndRecordDerivation() throws {
        let root = try loadRoot()
        let ring = try makeRing(root)
        let derivation = try object(root, "record_derivation")

        XCTAssertEqual(AtlasVaultKeyEpochRing.maximumEntries, root["maximum_ring_entries"] as? Int)
        XCTAssertTrue(
            NSDictionary(dictionary: ring.metadata.jsonObject).isEqual(
                to: try object(try object(root, "ring"), "metadata")))
        XCTAssertEqual(ring.currentKeyEpoch, 3)
        XCTAssertEqual(
            sha256Hex(
                try ring.deriveRecordKey(
                    keyEpoch: 1,
                    vaultID: try string(derivation, "vault_id"),
                    recordID: try string(derivation, "record_id")
                )),
            try string(derivation, "expected_epoch_1_key_sha256")
        )
        XCTAssertEqual(
            sha256Hex(
                try ring.deriveRecordKey(
                    keyEpoch: 3,
                    vaultID: try string(derivation, "vault_id"),
                    recordID: try string(derivation, "record_id")
                )),
            try string(derivation, "expected_epoch_3_key_sha256")
        )
    }

    func testEpochRingMigratesLegacyAndRecoversRetainedKey() throws {
        let root = try loadRoot()
        let legacy = try object(root, "legacy_migration")
        let legacyKey = seed(try string(legacy, "vault_key_label"))
        let migrated = try AtlasVaultKeyEpochRing.fromLegacy(
            legacyKey,
            keyEpoch: try integer(legacy, "key_epoch")
        )

        XCTAssertTrue(
            NSDictionary(dictionary: migrated.metadata.jsonObject).isEqual(
                to: try object(legacy, "expected_metadata")))
        XCTAssertEqual(migrated.currentVaultKey, legacyKey)
        XCTAssertEqual(try makeRing(root).vaultKey(for: 1), legacyKey)
        XCTAssertThrowsError(try makeRing(root).vaultKey(for: 4))
    }

    func testCurrentEpochHPKEOpensVectorAndRejectsEpochTamper() throws {
        let root = try loadRoot()
        let vector = try object(root, "hpke_v2_epoch_delivery")
        let sealed = AtlasVaultKeyEpochHPKESealedVaultKeyV2(
            keyEpoch: try integer(vector, "key_epoch"),
            encapsulatedKey: try data(vector, "encapsulated_key_hex"),
            ciphertext: try data(vector, "ciphertext_hex")
        )
        let opened = try AtlasVaultKeyEpochHPKE.open(
            recipientPrivateKey: seed(try string(vector, "recipient_seed_label")),
            sealed: sealed,
            context: try data(vector, "context_hex"),
            minimumKeyEpoch: 3
        )

        XCTAssertEqual(opened.keyEpoch, 3)
        XCTAssertEqual(opened.vaultKey, try makeRing(root).currentVaultKey)
        XCTAssertThrowsError(
            try AtlasVaultKeyEpochHPKE.open(
                recipientPrivateKey: seed(try string(vector, "recipient_seed_label")),
                sealed: AtlasVaultKeyEpochHPKESealedVaultKeyV2(
                    keyEpoch: 2,
                    encapsulatedKey: sealed.encapsulatedKey,
                    ciphertext: sealed.ciphertext
                ),
                context: try data(vector, "context_hex")
            )
        )
    }

    func testOnlyCurrentEpochIsWritableAndStaleDeliveryIsRejected() throws {
        let root = try loadRoot()
        let vector = try object(root, "hpke_v2_epoch_delivery")
        let ring = try makeRing(root)
        let sealed = try ring.sealCurrentHPKEV2(
            recipientPublicKey: try data(vector, "recipient_public_key_hex"),
            context: try data(vector, "context_hex")
        )

        XCTAssertEqual(sealed.keyEpoch, 3)
        XCTAssertThrowsError(
            try AtlasVaultKeyEpochHPKE.open(
                recipientPrivateKey: seed(try string(vector, "recipient_seed_label")),
                sealed: sealed,
                context: try data(vector, "context_hex"),
                minimumKeyEpoch: 4
            )
        )
    }

    func testEpochRingRejectsAmbiguousOrUnboundedState() throws {
        let root = try loadRoot()
        let keys = Dictionary(
            uniqueKeysWithValues: (1...33).map {
                (Int64($0), seed("epoch-\($0)"))
            })
        XCTAssertThrowsError(
            try AtlasVaultKeyEpochRing(
                metadata: AtlasVaultKeyRingMetadata(
                    jsonObject: try object(try object(root, "ring"), "metadata")
                ),
                keys: [1: keys[1]!, 3: keys[3]!]
            )
        )
        XCTAssertThrowsError(
            try AtlasVaultKeyEpochRing.fromEntries(
                currentKeyEpoch: 3,
                keys: [1: keys[1]!, 2: keys[1]!, 3: keys[3]!]
            )
        )
        XCTAssertThrowsError(
            try AtlasVaultKeyEpochRing.fromEntries(
                currentKeyEpoch: Int64(AtlasVaultKeyEpochRing.maximumEntries + 1),
                keys: keys
            )
        )
    }

    func testEpochMetadataRejectsNonIntegerJSONNumbers() throws {
        let base: [String: Any] = [
            "format": "atlasvault-vault-key-ring",
            "version": 1,
            "current_key_epoch": 3,
            "retained_key_epochs": [1, 2],
        ]
        let malformed: [[String: Any]] = [
            merging(base, "version", true),
            merging(base, "version", 1.0),
            merging(
                merging(base, "current_key_epoch", true),
                "retained_key_epochs",
                []
            ),
            merging(base, "current_key_epoch", 3.0),
            merging(base, "retained_key_epochs", [1, 2.0]),
        ]
        for value in malformed {
            XCTAssertThrowsError(try AtlasVaultKeyRingMetadata(jsonObject: value))
        }
    }

    func testEpochRingPropertiesHoldAcrossBoundedSizes() throws {
        for currentEpoch in 1...AtlasVaultKeyEpochRing.maximumEntries {
            let keys = Dictionary(
                uniqueKeysWithValues: (1...currentEpoch).map {
                    (Int64($0), seed("property-\(currentEpoch)-\($0)"))
                })
            let ring = try AtlasVaultKeyEpochRing.fromEntries(
                currentKeyEpoch: Int64(currentEpoch),
                keys: keys
            )
            XCTAssertEqual(
                ring.metadata.retainedKeyEpochs,
                (1..<currentEpoch).map(Int64.init)
            )
            XCTAssertEqual(ring.currentVaultKey, keys[Int64(currentEpoch)])
            let derived = try Set(
                keys.keys.map { epoch in
                    try ring.deriveRecordKey(
                        keyEpoch: epoch,
                        vaultID: "vault-\(currentEpoch)",
                        recordID: "record-\(epoch)"
                    )
                })
            XCTAssertEqual(derived.count, keys.count)
        }
    }

    func testEpochMetadataAndDeliveryTamperFailClosed() throws {
        let base: [String: Any] = [
            "format": "atlasvault-vault-key-ring",
            "version": 1,
            "current_key_epoch": 3,
            "retained_key_epochs": [1, 2],
        ]
        var missing = base
        missing.removeValue(forKey: "format")
        let malformed: [[String: Any]] = [
            missing,
            merging(base, "unexpected", 1),
            merging(base, "retained_key_epochs", [2, 1]),
            merging(base, "retained_key_epochs", [1, 1]),
            merging(base, "retained_key_epochs", [1, 3]),
        ]
        for value in malformed {
            XCTAssertThrowsError(try AtlasVaultKeyRingMetadata(jsonObject: value))
        }

        let root = try loadRoot()
        let vector = try object(root, "hpke_v2_epoch_delivery")
        let ring = try makeRing(root)
        let sealed = try ring.sealCurrentHPKEV2(
            recipientPublicKey: try data(vector, "recipient_public_key_hex"),
            context: try data(vector, "context_hex")
        )
        let recipient = seed(try string(vector, "recipient_seed_label"))
        let context = try data(vector, "context_hex")
        var encapsulated = sealed.encapsulatedKey
        encapsulated[encapsulated.startIndex] ^= 1
        var ciphertext = sealed.ciphertext
        ciphertext[ciphertext.index(before: ciphertext.endIndex)] ^= 1
        let variants = [
            AtlasVaultKeyEpochHPKESealedVaultKeyV2(
                keyEpoch: sealed.keyEpoch,
                encapsulatedKey: encapsulated,
                ciphertext: sealed.ciphertext
            ),
            AtlasVaultKeyEpochHPKESealedVaultKeyV2(
                keyEpoch: sealed.keyEpoch,
                encapsulatedKey: sealed.encapsulatedKey,
                ciphertext: ciphertext
            ),
        ]
        for value in variants {
            XCTAssertThrowsError(
                try AtlasVaultKeyEpochHPKE.open(
                    recipientPrivateKey: recipient,
                    sealed: value,
                    context: context
                )
            )
        }
        XCTAssertThrowsError(
            try AtlasVaultKeyEpochHPKE.open(
                recipientPrivateKey: seed("wrong-recipient"),
                sealed: sealed,
                context: context
            )
        )
    }

    private func makeRing(_ root: [String: Any]) throws -> AtlasVaultKeyEpochRing {
        let vector = try object(root, "ring")
        guard let entries = vector["entries"] as? [[String: Any]] else {
            throw testError(2)
        }
        return try AtlasVaultKeyEpochRing(
            metadata: AtlasVaultKeyRingMetadata(jsonObject: try object(vector, "metadata")),
            keys: Dictionary(
                uniqueKeysWithValues: try entries.map { entry in
                    (
                        try integer(entry, "key_epoch"),
                        seed(try string(entry, "vault_key_label"))
                    )
                })
        )
    }

    private func seed(_ label: String) -> Data {
        Data(SHA256.hash(data: Data(label.utf8)))
    }

    private func sha256Hex(_ value: Data) -> String {
        SHA256.hash(data: value).map { String(format: "%02x", $0) }.joined()
    }

    private func loadRoot() throws -> [String: Any] {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent(
                    "../../contracts/sync/test_vectors/atlasvault_key_epoch_vectors_v1.json"),
            source.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/atlasvault_key_epoch_vectors_v1.json"),
        ]
        guard
            let url = candidates.first(where: {
                FileManager.default.fileExists(atPath: $0.path)
            })
        else { throw testError(1) }
        guard
            let root = try JSONSerialization.jsonObject(with: Data(contentsOf: url))
                as? [String: Any],
            root["format"] as? String == "atlasvault-key-epoch-vectors",
            root["version"] as? Int == 1
        else { throw testError(2) }
        return root
    }

    private func object(_ value: [String: Any], _ field: String) throws -> [String: Any] {
        guard let result = value[field] as? [String: Any] else { throw testError(3) }
        return result
    }

    private func string(_ value: [String: Any], _ field: String) throws -> String {
        guard let result = value[field] as? String else { throw testError(4) }
        return result
    }

    private func integer(_ value: [String: Any], _ field: String) throws -> Int64 {
        guard let result = value[field] as? NSNumber else { throw testError(5) }
        return result.int64Value
    }

    private func data(_ value: [String: Any], _ field: String) throws -> Data {
        let source = try string(value, field)
        guard source.count.isMultiple(of: 2) else { throw testError(6) }
        var result = Data()
        var index = source.startIndex
        while index < source.endIndex {
            let end = source.index(index, offsetBy: 2)
            guard let byte = UInt8(source[index..<end], radix: 16) else {
                throw testError(7)
            }
            result.append(byte)
            index = end
        }
        return result
    }

    private func testError(_ code: Int) -> NSError {
        NSError(domain: "AtlasVaultKeyEpochRingTests", code: code)
    }

    private func merging(
        _ base: [String: Any],
        _ key: String,
        _ value: Any
    ) -> [String: Any] {
        var result = base
        result[key] = value
        return result
    }
}
