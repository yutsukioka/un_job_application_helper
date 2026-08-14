import Foundation
import Security
import XCTest
@testable import AtlasUI

final class AtlasVaultDeviceIdentityTests: XCTestCase {
    func testDeterministicPrivateKeysReproduceSharedIdentityVectors() throws {
        let root = try loadRoot()

        for name in ["device_a", "device_b"] {
            let vector = try dictionary(root[name], context: name)
            let descriptor = try dictionary(vector["descriptor"], context: "\(name).descriptor")
            let identity = try AtlasVaultDeviceIdentity(
                signingPrivateSeed: try data(vector["signing_private_seed"], context: "signing seed"),
                agreementPrivateKey: try data(vector["agreement_private_key"], context: "agreement key"),
                createdAt: try string(descriptor["created_at"], context: "created_at"),
                keyEpoch: try int(descriptor["key_epoch"], context: "key_epoch")
            )

            XCTAssertEqual(identity.signingPublicKey, try data(vector["signing_public_key"], context: "signing public"))
            XCTAssertEqual(identity.agreementPublicKey, try data(vector["agreement_public_key"], context: "agreement public"))
            XCTAssertEqual(identity.deviceID, try string(vector["device_id"], context: "device_id"))
            XCTAssertEqual(
                try identity.descriptor.canonicalData(),
                try data(vector["descriptor_canonical_json_b64"], context: "descriptor canonical")
            )

            let fresh = try identity.signDescriptor()
            XCTAssertEqual(fresh.signature.count, 64)
            XCTAssertEqual(try fresh.verifiedDescriptor(), identity.descriptor)

            let signed = try AtlasVaultSignedDeviceDescriptor.decodeStrict(
                try data(
                    vector["signed_descriptor_canonical_json_b64"],
                    context: "signed descriptor canonical"
                )
            )
            XCTAssertEqual(
                signed.signature,
                try data(vector["descriptor_signature"], context: "descriptor signature")
            )
            XCTAssertEqual(
                try signed.canonicalData(),
                try data(vector["signed_descriptor_canonical_json_b64"], context: "signed descriptor canonical")
            )
            XCTAssertEqual(try signed.verifiedDescriptor(), identity.descriptor)
        }
    }

    func testSecretBundleIsStrictAndRederivesPublicIdentity() throws {
        let root = try loadRoot()
        let vector = try dictionary(root["device_a"], context: "device_a")
        let secretData = try data(vector["secret_bundle_canonical_json_b64"], context: "secret bundle")
        let secret = try AtlasVaultDeviceIdentitySecret.decodeStrict(secretData)
        let identity = try secret.loadIdentity()

        XCTAssertEqual(try secret.canonicalData(), secretData)
        XCTAssertEqual(identity.deviceID, try string(vector["device_id"], context: "device_id"))

        var object = try dictionary(
            JSONSerialization.jsonObject(with: secretData),
            context: "secret bundle"
        )
        object["platform"] = "test"
        let extra = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        XCTAssertThrowsError(try AtlasVaultDeviceIdentitySecret.decodeStrict(extra))

        object.removeValue(forKey: "platform")
        object["device_id"] = try string(
            dictionary(root["device_b"], context: "device_b")["device_id"],
            context: "device_b.device_id"
        )
        let mismatch = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        XCTAssertThrowsError(try AtlasVaultDeviceIdentitySecret.decodeStrict(mismatch))
    }

    func testStrictIdentityDecodersRejectNoncanonicalJSONBytes() throws {
        let vector = try dictionary(try loadRoot()["device_a"], context: "device_a")
        let descriptor = try data(
            vector["descriptor_canonical_json_b64"],
            context: "descriptor"
        )
        let signed = try data(
            vector["signed_descriptor_canonical_json_b64"],
            context: "signed descriptor"
        )
        let secret = try data(
            vector["secret_bundle_canonical_json_b64"],
            context: "secret bundle"
        )

        XCTAssertThrowsError(
            try AtlasVaultDeviceDescriptor.decodeStrict(Data(" \n".utf8) + descriptor)
        )
        XCTAssertThrowsError(
            try AtlasVaultSignedDeviceDescriptor.decodeStrict(signed + Data("\n".utf8))
        )
        XCTAssertThrowsError(
            try AtlasVaultDeviceIdentitySecret.decodeStrict(Data(" ".utf8) + secret)
        )
    }

    func testMaximumDeviceKeyEpochIsTheSharedSigned64BitBound() throws {
        let vector = try dictionary(try loadRoot()["device_a"], context: "device_a")
        let maximumKeyEpoch = Int.max
        let identity = try AtlasVaultDeviceIdentity(
            signingPrivateSeed: try data(vector["signing_private_seed"], context: "signing seed"),
            agreementPrivateKey: try data(vector["agreement_private_key"], context: "agreement key"),
            createdAt: try string(
                try dictionary(vector["descriptor"], context: "descriptor")["created_at"],
                context: "created_at"
            ),
            keyEpoch: maximumKeyEpoch
        )
        XCTAssertEqual(identity.descriptor.keyEpoch, maximumKeyEpoch)
    }

    func testDeviceIDDerivationIsDomainSeparatedAndOrdered() throws {
        let vector = try dictionary(try loadRoot()["device_a"], context: "device_a")
        let signing = try data(vector["signing_public_key"], context: "signing public")
        let agreement = try data(vector["agreement_public_key"], context: "agreement public")

        XCTAssertEqual(
            try AtlasVaultDeviceIdentity.deriveDeviceID(
                signingPublicKey: signing,
                agreementPublicKey: agreement
            ),
            try string(vector["device_id"], context: "device_id")
        )
        XCTAssertNotEqual(
            try AtlasVaultDeviceIdentity.deriveDeviceID(
                signingPublicKey: agreement,
                agreementPublicKey: signing
            ),
            try string(vector["device_id"], context: "device_id")
        )
    }

    func testDescriptorRejectsTamperUnknownFieldsAndNoncanonicalValues() throws {
        let root = try loadRoot()
        let vector = try dictionary(root["device_a"], context: "device_a")
        let descriptorData = try data(vector["descriptor_canonical_json_b64"], context: "descriptor")
        let original = try dictionary(JSONSerialization.jsonObject(with: descriptorData), context: "descriptor")
        let other = try dictionary(root["device_b"], context: "device_b")
        var invalidObjects: [[String: Any]] = []

        var mismatch = original
        mismatch["device_id"] = other["device_id"]
        invalidObjects.append(mismatch)
        var extra = original
        extra["device_label"] = "private label"
        invalidObjects.append(extra)
        var booleanEpoch = original
        booleanEpoch["key_epoch"] = true
        invalidObjects.append(booleanEpoch)
        var fractionalTime = original
        fractionalTime["created_at"] = "2026-01-15T12:00:00.000Z"
        invalidObjects.append(fractionalTime)
        var unpaddedKey = original
        unpaddedKey["signing_public_key"] = try string(
            original["signing_public_key"],
            context: "signing_public_key"
        ).replacingOccurrences(of: "=", with: "")
        invalidObjects.append(unpaddedKey)

        for invalid in invalidObjects {
            let encoded = try JSONSerialization.data(withJSONObject: invalid, options: [.sortedKeys])
            XCTAssertThrowsError(try AtlasVaultDeviceDescriptor.decodeStrict(encoded))
        }
    }

    func testSignatureAndKeySubstitutionFailClosed() throws {
        let root = try loadRoot()
        let vector = try dictionary(root["device_a"], context: "device_a")
        let other = try dictionary(root["device_b"], context: "device_b")
        let signedData = try data(vector["signed_descriptor_canonical_json_b64"], context: "signed descriptor")
        let original = try dictionary(JSONSerialization.jsonObject(with: signedData), context: "signed descriptor")

        var tampered = original
        var changedSignature = try data(tampered["signature"], context: "signature")
        changedSignature[0] ^= 1
        tampered["signature"] = changedSignature.base64EncodedString()

        var substituted = original
        var descriptor = try dictionary(substituted["descriptor"], context: "descriptor")
        descriptor["signing_public_key"] = other["signing_public_key"]
        descriptor["device_id"] = try AtlasVaultDeviceIdentity.deriveDeviceID(
            signingPublicKey: try data(other["signing_public_key"], context: "other signing"),
            agreementPublicKey: try data(descriptor["agreement_public_key"], context: "agreement")
        )
        substituted["descriptor"] = descriptor

        for invalid in [tampered, substituted] {
            let encoded = try JSONSerialization.data(withJSONObject: invalid, options: [.sortedKeys])
            XCTAssertThrowsError(try AtlasVaultSignedDeviceDescriptor.decodeStrict(encoded).verifiedDescriptor())
        }
    }

    func testSecretDescriptionsAndErrorsAreRedacted() throws {
        let vector = try dictionary(try loadRoot()["device_a"], context: "device_a")
        let secret = try AtlasVaultDeviceIdentitySecret.decodeStrict(
            try data(vector["secret_bundle_canonical_json_b64"], context: "secret bundle")
        )
        let identity = try secret.loadIdentity()
        let forbidden = [
            try string(vector["signing_private_seed"], context: "signing seed"),
            try string(vector["agreement_private_key"], context: "agreement key"),
        ]

        for rendered in [secret.description, identity.description] {
            for value in forbidden {
                XCTAssertFalse(rendered.contains(value))
            }
        }
    }

    func testKeychainCustodyIsCreateOnlyStrictAndDeviceOnly() throws {
        let vector = try dictionary(try loadRoot()["device_a"], context: "device_a")
        let bundle = try data(
            vector["secret_bundle_canonical_json_b64"],
            context: "secret bundle"
        )
        let client = DeviceIdentityFakeKeychainClient()
        let store = AtlasKeychainDeviceIdentityStore(
            client: client,
            service: "com.atlasvault.tests.device-identity",
            account: "primary"
        )

        XCTAssertNil(try store.loadPrimaryIdentity())
        XCTAssertFalse(try store.containsPrimaryIdentity())
        try store.createPrimaryIdentity(bundle)
        XCTAssertEqual(try store.loadPrimaryIdentity(), bundle)
        XCTAssertTrue(try store.containsPrimaryIdentity())
        XCTAssertEqual(client.addedItems, [
            AtlasKeychainItem(
                service: "com.atlasvault.tests.device-identity",
                account: "primary",
                valueData: bundle,
                accessibility: .afterFirstUnlockThisDeviceOnly
            ),
        ])
        XCTAssertTrue(client.updatedItems.isEmpty)

        XCTAssertThrowsError(try store.createPrimaryIdentity(bundle)) { error in
            XCTAssertEqual(
                error as? AtlasKeychainDeviceIdentityStoreError,
                .collision
            )
        }
        XCTAssertTrue(client.updatedItems.isEmpty)

        try store.deletePrimaryIdentity()
        try store.deletePrimaryIdentity()
        XCTAssertNil(try store.loadPrimaryIdentity())
    }

    func testKeychainCustodyRejectsOversizeAndInvalidLoadedBundle() throws {
        let client = DeviceIdentityFakeKeychainClient()
        let store = AtlasKeychainDeviceIdentityStore(
            client: client,
            service: "com.atlasvault.tests.device-identity",
            account: "primary"
        )

        XCTAssertThrowsError(
            try store.createPrimaryIdentity(Data(repeating: 1, count: 16 * 1024 + 1))
        ) { error in
            XCTAssertEqual(
                error as? AtlasKeychainDeviceIdentityStoreError,
                .invalidSecret
            )
        }
        XCTAssertTrue(client.addedItems.isEmpty)

        client.setRawValue(
            Data(#"{"format":"private-sentinel"}"#.utf8),
            service: "com.atlasvault.tests.device-identity",
            account: "primary"
        )
        XCTAssertThrowsError(try store.loadPrimaryIdentity()) { error in
            XCTAssertFalse(String(describing: error).contains("private-sentinel"))
        }
    }

    func testRealKeychainCustodyRoundTripWhenAvailable() throws {
        let vector = try dictionary(try loadRoot()["device_a"], context: "device_a")
        let bundle = try data(
            vector["secret_bundle_canonical_json_b64"],
            context: "secret bundle"
        )
        let service = "com.atlasvault.tests.device-identity.\(UUID().uuidString)"
        let store = AtlasKeychainDeviceIdentityStore(service: service)

        do {
            try store.deletePrimaryIdentity()
            try store.createPrimaryIdentity(bundle)
            XCTAssertEqual(try store.loadPrimaryIdentity(), bundle)
            let loaded = try XCTUnwrap(try store.loadPrimaryIdentity())
            XCTAssertEqual(
                try AtlasVaultDeviceIdentitySecret.decodeStrict(loaded)
                    .loadIdentity().deviceID,
                try string(vector["device_id"], context: "device_id")
            )
            XCTAssertThrowsError(try store.createPrimaryIdentity(bundle))
            try store.deletePrimaryIdentity()
            XCTAssertNil(try store.loadPrimaryIdentity())
        } catch AtlasKeychainDeviceIdentityStoreError.unavailable {
            throw XCTSkip("The test Keychain is unavailable in this environment.")
        } catch {
            try? store.deletePrimaryIdentity()
            throw error
        }
    }

    private func loadRoot() throws -> [String: Any] {
        let data = try Data(contentsOf: try vectorURL())
        let root = try dictionary(JSONSerialization.jsonObject(with: data), context: "vector root")
        XCTAssertEqual(try string(root["_warning"], context: "warning"), "FAKE TEST DATA ONLY")
        XCTAssertEqual(try string(root["format"], context: "format"), "atlasvault-device-identity-pairing-v1")
        XCTAssertEqual(try int(root["version"], context: "version"), 1)
        return root
    }

    private func vectorURL() throws -> URL {
        let current = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            current.appendingPathComponent("../../contracts/sync/test_vectors/atlasvault_device_identity_pairing_vectors_v1.json"),
            current.appendingPathComponent("contracts/sync/test_vectors/atlasvault_device_identity_pairing_vectors_v1.json"),
            source.appendingPathComponent("../../../../contracts/sync/test_vectors/atlasvault_device_identity_pairing_vectors_v1.json"),
        ].map(\.standardizedFileURL)
        if let result = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) {
            return result
        }
        throw NSError(domain: "AtlasVaultDeviceIdentityTests", code: 1)
    }

    private func dictionary(_ value: Any?, context: String) throws -> [String: Any] {
        guard let value = value as? [String: Any] else {
            throw NSError(domain: context, code: 1)
        }
        return value
    }

    private func string(_ value: Any?, context: String) throws -> String {
        guard let value = value as? String else {
            throw NSError(domain: context, code: 1)
        }
        return value
    }

    private func int(_ value: Any?, context: String) throws -> Int {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else {
            throw NSError(domain: context, code: 1)
        }
        return number.intValue
    }

    private func data(_ value: Any?, context: String) throws -> Data {
        guard
            let text = value as? String,
            let data = Data(base64Encoded: text),
            data.base64EncodedString() == text
        else {
            throw NSError(domain: context, code: 1)
        }
        return data
    }
}

private final class DeviceIdentityFakeKeychainClient:
    AtlasKeychainClient,
    @unchecked Sendable
{
    struct UpdateCall: Equatable {
        let query: AtlasKeychainQuery
        let attributes: AtlasKeychainUpdate
    }

    private struct StorageKey: Hashable {
        let service: String
        let account: String
    }

    private var values: [StorageKey: Data] = [:]
    private(set) var addedItems: [AtlasKeychainItem] = []
    private(set) var updatedItems: [UpdateCall] = []

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        addedItems.append(item)
        let key = StorageKey(service: item.service, account: item.account)
        guard values[key] == nil else { return errSecDuplicateItem }
        values[key] = item.valueData
        return errSecSuccess
    }

    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        let key = StorageKey(service: query.service, account: query.account)
        guard let value = values[key] else {
            return AtlasKeychainCopyResult(
                status: errSecItemNotFound,
                valueData: nil
            )
        }
        return AtlasKeychainCopyResult(status: errSecSuccess, valueData: value)
    }

    func update(
        _ query: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        updatedItems.append(UpdateCall(query: query, attributes: attributes))
        return errSecSuccess
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        let key = StorageKey(service: query.service, account: query.account)
        return values.removeValue(forKey: key) == nil
            ? errSecItemNotFound
            : errSecSuccess
    }

    func setRawValue(_ value: Data, service: String, account: String) {
        values[StorageKey(service: service, account: account)] = value
    }
}
