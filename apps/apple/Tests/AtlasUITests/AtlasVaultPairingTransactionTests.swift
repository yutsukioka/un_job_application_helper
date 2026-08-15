import CryptoKit
import Foundation
import Security
@testable import AtlasUI
import XCTest

final class AtlasVaultPairingTransactionTests: XCTestCase {
    func testStrictTransactionAndKeychainStoresUseDeviceOnlyServices() throws {
        let transaction = try AtlasVaultPairingTransaction.decodeStrict(
            Data(Self.transactionJSON.utf8)
        )
        XCTAssertEqual(transaction.role, .invitee)
        XCTAssertEqual(transaction.stage, .acceptanceCreated)
        XCTAssertEqual(
            try transaction.canonicalData(),
            Data(Self.transactionJSON.utf8)
        )

        let client = PairingStateFakeKeychainClient()
        let registry = AtlasKeychainTrustedDeviceRegistryStore(client: client)
        let replay = AtlasKeychainPairingReplayStore(client: client)
        let journal = AtlasKeychainPairingTransactionStore(client: client)

        XCTAssertEqual(type(of: registry).service, "com.atlasvault.trusted-devices.v1")
        XCTAssertEqual(type(of: replay).service, "com.atlasvault.pairing-replay.v1")
        XCTAssertEqual(type(of: journal).service, "com.atlasvault.pairing-transaction.v1")

        try journal.create(transaction)
        XCTAssertEqual(try journal.load(), transaction)
        XCTAssertEqual(client.lastAdded?.accessibility, .afterFirstUnlockThisDeviceOnly)
        XCTAssertEqual(client.lastAdded?.account, "pending-v1")
        XCTAssertThrowsError(try journal.create(transaction)) { error in
            XCTAssertEqual(error as? AtlasVaultPairingStateStoreError, .collision)
        }
        try journal.replace(
            transaction,
            expectedSHA256: transaction.sha256Hex()
        )
        XCTAssertThrowsError(
            try journal.replace(
                transaction,
                expectedSHA256: String(repeating: "0", count: 64)
            )
        ) { error in
            XCTAssertEqual(error as? AtlasVaultPairingStateStoreError, .stale)
        }
        try journal.delete(expectedSHA256: try transaction.sha256Hex())
        XCTAssertNil(try journal.load())
        client.overwrite(
            service: type(of: journal).service,
            account: "pending-v1",
            data: Data("{}".utf8)
        )
        XCTAssertThrowsError(try journal.load())

        XCTAssertThrowsError(
            try AtlasVaultPairingTransaction.decodeStrict(
                Data(repeating: 0, count: AtlasVaultPairingTransaction.maximumByteCount + 1)
            )
        )
    }

    func testRegistryAndReplayUseCreateOnlyCASAndVerifyDescriptors() throws {
        let registry = try AtlasVaultTrustedDeviceRegistry.decodeStrict(
            try vectorData(key: "trusted_registry_canonical_b64")
        )
        let replay = try AtlasVaultPairingReplayStore.decodeStrict(
            try vectorData(key: "replay_store_canonical_b64")
        )
        let client = PairingStateFakeKeychainClient()
        let registryStore = AtlasKeychainTrustedDeviceRegistryStore(client: client)
        let replayStore = AtlasKeychainPairingReplayStore(client: client)

        try registryStore.create(registry)
        XCTAssertEqual(try registryStore.load(), registry)
        XCTAssertEqual(client.lastAdded?.service, "com.atlasvault.trusted-devices.v1")
        XCTAssertEqual(client.lastAdded?.account, "state-v1")
        XCTAssertEqual(client.lastAdded?.accessibility, .afterFirstUnlockThisDeviceOnly)
        XCTAssertThrowsError(try registryStore.create(registry))
        try registryStore.replace(
            registry,
            expectedSHA256: sha256Hex(try registry.canonicalData())
        )
        XCTAssertThrowsError(
            try registryStore.replace(
                registry,
                expectedSHA256: String(repeating: "0", count: 64)
            )
        )

        try replayStore.create(replay)
        XCTAssertEqual(try replayStore.load(), replay)
        try replayStore.replace(
            replay,
            expectedSHA256: sha256Hex(try replay.canonicalData())
        )
        XCTAssertThrowsError(
            try replayStore.replace(
                replay,
                expectedSHA256: String(repeating: "0", count: 64)
            )
        )
    }

    func testSandboxStageIsHashBoundAndCleansUp() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(
            UUID().uuidString,
            isDirectory: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try AtlasVaultPairingArtifactStageStore(root: root)
        let artifact = try AtlasVaultPairingArtifact.decodeStrict(
            try vectorArtifact(kind: "offer")
        )
        try store.create(artifact)
        XCTAssertThrowsError(try store.create(artifact))
        XCTAssertEqual(
            try store.read(kind: .offer)?.canonicalData(),
            try artifact.canonicalData()
        )
        XCTAssertThrowsError(
            try store.delete(
                kind: .offer,
                expectedSHA256: String(repeating: "0", count: 64)
            )
        )
        XCTAssertNotNil(try store.read(kind: .offer))

        let staged = root.appendingPathComponent("offer.atlaspair")
        var tampered = try Data(contentsOf: staged)
        tampered[tampered.startIndex] ^= 1
        try tampered.write(to: staged, options: .atomic)
        XCTAssertThrowsError(try store.read(kind: .offer))
        try artifact.canonicalData().write(to: staged, options: .atomic)
        try store.delete(
            kind: .offer,
            expectedSHA256: try artifact.sha256Hex()
        )
        XCTAssertNil(try store.read(kind: .offer))
    }

    private func vectorArtifact(kind: String) throws -> Data {
        let root = try JSONSerialization.jsonObject(
            with: Data(contentsOf: vectorURL)
        ) as? [String: Any]
        let artifacts = root?["artifacts"] as? [String: Any]
        let artifact = artifacts?[kind] as? [String: Any]
        guard
            let encoded = artifact?["canonical_b64"] as? String,
            let data = Data(base64Encoded: encoded)
        else { throw PairingTransactionTestError.invalidVector }
        return data
    }

    private func vectorData(key: String) throws -> Data {
        let root = try JSONSerialization.jsonObject(
            with: Data(contentsOf: vectorURL)
        ) as? [String: Any]
        guard
            let encoded = root?[key] as? String,
            let data = Data(base64Encoded: encoded)
        else { throw PairingTransactionTestError.invalidVector }
        return data
    }

    private func sha256Hex(_ data: Data) -> String {
        Data(SHA256.hash(data: data))
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private var vectorURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("contracts/sync/test_vectors")
            .appendingPathComponent(
                "atlasvault_trusted_pairing_delivery_vectors_v1.json"
            )
    }

    private static let transactionJSON =
        #"{"acceptance_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","acknowledgement_sha256":null,"bootstrap_sha256":null,"created_at":"2026-08-15T10:00:00Z","delivery_sha256":null,"ephemeral_private_key":"AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=","format":"atlasvault-pairing-transaction","key_epoch":null,"local_device_id":"avd1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","offer_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","parent_revision":null,"peer_device_id":"avd1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","revision":"42000000-0000-4000-8000-000000000002","role":"invitee","selection_committed":false,"stage":"acceptance_created","staged_artifacts":[],"store_sha256":null,"transaction_id":"42000000-0000-4000-8000-000000000001","transcript_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","updated_at":"2026-08-15T10:01:00Z","vault_id":null,"vault_key_sha256":null,"version":1}"#
}

private enum PairingTransactionTestError: Error {
    case invalidVector
}

private final class PairingStateFakeKeychainClient:
    AtlasKeychainClient,
    @unchecked Sendable
{
    private var values: [String: Data] = [:]
    private(set) var lastAdded: AtlasKeychainItem?

    func overwrite(service: String, account: String, data: Data) {
        values[Self.key(service: service, account: account)] = data
    }

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        let key = Self.key(service: item.service, account: item.account)
        guard values[key] == nil else { return errSecDuplicateItem }
        values[key] = item.valueData
        lastAdded = item
        return errSecSuccess
    }

    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        guard let value = values[Self.key(query)] else {
            return AtlasKeychainCopyResult(status: errSecItemNotFound, valueData: nil)
        }
        return AtlasKeychainCopyResult(status: errSecSuccess, valueData: value)
    }

    func update(
        _ query: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        let key = Self.key(query)
        guard values[key] != nil else { return errSecItemNotFound }
        values[key] = attributes.valueData
        return errSecSuccess
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        values.removeValue(forKey: Self.key(query)) == nil
            ? errSecItemNotFound
            : errSecSuccess
    }

    private static func key(_ query: AtlasKeychainQuery) -> String {
        key(service: query.service, account: query.account)
    }

    private static func key(service: String, account: String) -> String {
        "\(service)\u{0}\(account)"
    }
}
