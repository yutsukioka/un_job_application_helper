import XCTest
@testable import AtlasUI

final class AtlasVaultKeyStoreTests: XCTestCase {
    func testMockStoreSavesLoadsAndDeletesTestOnlyVaultKey() throws {
        let store = MockVaultKeyStore()

        try store.saveVaultKey(Self.testOnlyVaultKey, for: Self.vaultID)

        XCTAssertEqual(try store.loadVaultKey(for: Self.vaultID), Self.testOnlyVaultKey)
        XCTAssertEqual(store.saveCalls, [Self.vaultID])
        XCTAssertEqual(store.loadCalls, [Self.vaultID])

        try store.deleteVaultKey(for: Self.vaultID)

        XCTAssertNil(try store.loadVaultKey(for: Self.vaultID))
        XCTAssertEqual(store.deleteCalls, [Self.vaultID])
    }

    func testUnlocksWithCallerProvidedTestOnlyVaultKey() throws {
        var service = AtlasVaultUnlockService(keyStore: MockVaultKeyStore())

        let state = service.unlock(vaultID: Self.vaultID, vaultKey: Self.testOnlyVaultKey)

        XCTAssertEqual(state, .unlocked)
        XCTAssertEqual(service.state, .unlocked)
        let session = try XCTUnwrap(service.session)
        XCTAssertEqual(session.vaultID, Self.vaultID)
        XCTAssertEqual(session.vaultKeyByteCount, AtlasVaultRecordCrypto.vaultKeyByteCount)
        XCTAssertEqual(session.withVaultKey { $0 }, Self.testOnlyVaultKey)
    }

    func testUnlocksWithStoredTestOnlyVaultKey() throws {
        let store = MockVaultKeyStore()
        try store.saveVaultKey(Self.testOnlyVaultKey, for: Self.vaultID)
        var service = AtlasVaultUnlockService(keyStore: store)

        XCTAssertEqual(service.unlockWithStoredKey(for: Self.vaultID), .unlocked)

        let session = try XCTUnwrap(service.session)
        XCTAssertEqual(session.withVaultKey { $0 }, Self.testOnlyVaultKey)
        XCTAssertEqual(store.loadCalls, [Self.vaultID])
    }

    func testRejectsInvalidVaultKeyLengthWithoutCreatingSession() throws {
        var service = AtlasVaultUnlockService(keyStore: MockVaultKeyStore())

        XCTAssertEqual(
            service.unlock(vaultID: Self.vaultID, vaultKey: Data(repeating: 7, count: 31)),
            .invalidKey
        )
        XCTAssertNil(service.session)
        XCTAssertEqual(service.state, .invalidKey)
        XCTAssertThrowsError(try service.saveVaultKey(Data(repeating: 7, count: 31), for: Self.vaultID)) { error in
            XCTAssertEqual(error as? AtlasVaultUnlockError, .invalidVaultKeyLength)
        }
    }

    func testLockClearsUnlockedSession() throws {
        var service = AtlasVaultUnlockService(keyStore: MockVaultKeyStore())
        XCTAssertEqual(service.unlock(vaultID: Self.vaultID, vaultKey: Self.testOnlyVaultKey), .unlocked)

        service.lock()

        XCTAssertEqual(service.state, .locked)
        XCTAssertNil(service.session)
    }

    func testUnavailableOrDeletedKeyLeavesNoSession() throws {
        let store = MockVaultKeyStore()
        var service = AtlasVaultUnlockService(keyStore: store)

        XCTAssertEqual(service.unlockWithStoredKey(for: Self.vaultID), .keyUnavailable)
        XCTAssertNil(service.session)

        try store.saveVaultKey(Self.testOnlyVaultKey, for: Self.vaultID)
        XCTAssertEqual(service.unlockWithStoredKey(for: Self.vaultID), .unlocked)
        service.lock()
        try service.deleteVaultKey(for: Self.vaultID)

        XCTAssertEqual(service.unlockWithStoredKey(for: Self.vaultID), .keyUnavailable)
        XCTAssertNil(service.session)
    }

    func testPublicSnapshotSerializationIsUnchangedByMockUnlockSession() throws {
        var service = AtlasVaultUnlockService(keyStore: MockVaultKeyStore())
        XCTAssertEqual(
            service.unlock(vaultID: Self.privateSentinel, vaultKey: Self.testOnlyVaultKey),
            .unlocked
        )

        let snapshot = try decodedPublicSnapshot()
        let json = try encodedSnapshotString(snapshot)

        XCTAssertFalse(json.contains(Self.privateSentinel))
        XCTAssertFalse(json.contains(Self.testOnlyVaultKey.base64EncodedString()))
        XCTAssertFalse(json.contains("savedSearches"))
        XCTAssertFalse(json.contains("savedJobs"))
    }

    func testSourceAvoidsRuntimePlatformStorageAndAppIntegrationAPIs() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)

        for forbidden in [
            "SecItem",
            "LocalAuthentication",
            "LAContext",
            "FileManager",
            "Data(contentsOf",
            ".write(",
            "UserDefaults",
            "AtlasLocalCache",
            "SearchViewModel",
            "AtlasAPIClient",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private static let vaultID = "TEST_ONLY_VAULT_ID"
    private static let privateSentinel = "TOP_SECRET_SENTINEL_DO_NOT_LEAK"
    private static let testOnlyVaultKey = Data((0..<AtlasVaultRecordCrypto.vaultKeyByteCount).map(UInt8.init))

    private func decodedPublicSnapshot() throws -> AtlasPublicLocalSnapshot {
        try decoder.decode(AtlasPublicLocalSnapshot.self, from: Data("""
        {
          "savedAt": "2026-01-01T00:00:00Z",
          "baseURL": "http://127.0.0.1:8765",
          "health": {
            "status": "ok",
            "db_path": null,
            "schema_version": "test",
            "open_jobs": 0,
            "enabled_sources": 0,
            "last_sync_at": null
          },
          "searchResponse": {
            "total": 0,
            "limit": 0,
            "offset": 0,
            "results": [],
            "facets": {},
            "facet_labels": {},
            "unclassified_count": 0
          },
          "sources": [],
          "recentRuns": []
        }
        """.utf8))
    }

    private func encodedSnapshotString(_ snapshot: AtlasPublicLocalSnapshot) throws -> String {
        let data = try AtlasLocalCache.encodedSnapshotData(snapshot)
        return try XCTUnwrap(String(data: data, encoding: .utf8))
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/AtlasVaultKeyStore.swift"),
            sourceDirectory.appendingPathComponent("../../../../apps/apple/Sources/AtlasUI/AtlasVaultKeyStore.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw NSError(
            domain: "AtlasVaultKeyStoreTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Could not find AtlasVaultKeyStore.swift"]
        )
    }

    private var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

private final class MockVaultKeyStore: AtlasVaultKeyStore, @unchecked Sendable {
    private var keys: [String: Data] = [:]
    private(set) var loadCalls: [String] = []
    private(set) var saveCalls: [String] = []
    private(set) var deleteCalls: [String] = []

    func loadVaultKey(for vaultID: String) throws -> Data? {
        loadCalls.append(vaultID)
        return keys[vaultID].map { Data($0) }
    }

    func saveVaultKey(_ key: Data, for vaultID: String) throws {
        saveCalls.append(vaultID)
        keys[vaultID] = Data(key)
    }

    func deleteVaultKey(for vaultID: String) throws {
        deleteCalls.append(vaultID)
        keys.removeValue(forKey: vaultID)
    }
}
