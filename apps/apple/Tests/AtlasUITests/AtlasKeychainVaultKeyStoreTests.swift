import Security
import XCTest
@testable import AtlasUI

final class AtlasKeychainVaultKeyStoreTests: XCTestCase {
    func testCreateVaultKeyAddsOnlyAndDuplicateNeverUpdates()
        throws
    {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(
            client: client,
            service: Self.service
        )

        try store.createVaultKey(
            Self.testOnlyVaultKey,
            for: Self.vaultID
        )
        XCTAssertEqual(client.addedItems.count, 1)
        XCTAssertTrue(client.updatedItems.isEmpty)

        XCTAssertThrowsError(
            try store.createVaultKey(
                Data(
                    repeating: 7,
                    count: AtlasVaultRecordCrypto.vaultKeyByteCount
                ),
                for: Self.vaultID
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasKeychainVaultKeyStoreError,
                .collision
            )
        }
        XCTAssertEqual(client.addedItems.count, 2)
        XCTAssertTrue(client.updatedItems.isEmpty)
        XCTAssertEqual(
            try store.loadVaultKey(for: Self.vaultID),
            Self.testOnlyVaultKey
        )
    }

    func testSaveAddsKeychainGenericPasswordItem() throws {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(
            client: client,
            service: Self.service,
            accessibility: .afterFirstUnlockThisDeviceOnly
        )

        try store.saveVaultKey(Self.testOnlyVaultKey, for: Self.vaultID)

        XCTAssertEqual(client.addedItems, [
            AtlasKeychainItem(
                service: Self.service,
                account: Self.vaultID,
                valueData: Self.testOnlyVaultKey,
                accessibility: .afterFirstUnlockThisDeviceOnly
            ),
        ])
        XCTAssertEqual(try store.loadVaultKey(for: Self.vaultID), Self.testOnlyVaultKey)
    }

    func testDuplicateSaveUpdatesExistingKeychainItem() throws {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)
        try store.saveVaultKey(Self.testOnlyVaultKey, for: Self.vaultID)
        let updatedKey = Data(repeating: 7, count: AtlasVaultRecordCrypto.vaultKeyByteCount)

        try store.saveVaultKey(updatedKey, for: Self.vaultID)

        XCTAssertEqual(client.addedItems.count, 2)
        XCTAssertEqual(client.updatedItems, [
            FakeKeychainClient.UpdateCall(
                query: AtlasKeychainQuery(service: Self.service, account: Self.vaultID),
                attributes: AtlasKeychainUpdate(valueData: updatedKey)
            ),
        ])
        XCTAssertEqual(try store.loadVaultKey(for: Self.vaultID), updatedKey)
    }

    func testLoadReturnsNilWhenItemIsNotFound() throws {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)

        XCTAssertNil(try store.loadVaultKey(for: Self.vaultID))

        XCTAssertEqual(client.copiedQueries, [
            AtlasKeychainQuery(service: Self.service, account: Self.vaultID),
        ])
    }

    func testDeleteRemovesItemAndTreatsMissingItemAsNoOp() throws {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)
        try store.saveVaultKey(Self.testOnlyVaultKey, for: Self.vaultID)

        try store.deleteVaultKey(for: Self.vaultID)
        try store.deleteVaultKey(for: Self.vaultID)

        XCTAssertNil(try store.loadVaultKey(for: Self.vaultID))
        XCTAssertEqual(client.deletedQueries, [
            AtlasKeychainQuery(service: Self.service, account: Self.vaultID),
            AtlasKeychainQuery(service: Self.service, account: Self.vaultID),
        ])
    }

    func testRejectsInvalidVaultKeyLengthBeforeCallingKeychain() throws {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)

        XCTAssertThrowsError(try store.saveVaultKey(Data(repeating: 1, count: 31), for: Self.vaultID)) { error in
            XCTAssertEqual(error as? AtlasKeychainVaultKeyStoreError, .invalidVaultKeyLength)
        }

        XCTAssertTrue(client.addedItems.isEmpty)
    }

    func testRejectsInvalidVaultIDBeforeCallingKeychain() throws {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)

        for invalidVaultID in ["", "   ", "\n\t"] {
            XCTAssertThrowsError(try store.saveVaultKey(Self.testOnlyVaultKey, for: invalidVaultID)) { error in
                XCTAssertEqual(error as? AtlasKeychainVaultKeyStoreError, .invalidVaultID)
            }
            XCTAssertThrowsError(try store.loadVaultKey(for: invalidVaultID)) { error in
                XCTAssertEqual(error as? AtlasKeychainVaultKeyStoreError, .invalidVaultID)
            }
            XCTAssertThrowsError(try store.deleteVaultKey(for: invalidVaultID)) { error in
                XCTAssertEqual(error as? AtlasKeychainVaultKeyStoreError, .invalidVaultID)
            }
        }

        XCTAssertTrue(client.addedItems.isEmpty)
        XCTAssertTrue(client.copiedQueries.isEmpty)
        XCTAssertTrue(client.deletedQueries.isEmpty)
    }

    func testTrimsVaultIDBeforeUsingKeychainAccount() throws {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)

        try store.saveVaultKey(Self.testOnlyVaultKey, for: "  \(Self.vaultID) \n")

        XCTAssertEqual(client.addedItems.first?.account, Self.vaultID)
        XCTAssertEqual(try store.loadVaultKey(for: "\t\(Self.vaultID)  "), Self.testOnlyVaultKey)
        XCTAssertEqual(client.copiedQueries, [
            AtlasKeychainQuery(service: Self.service, account: Self.vaultID),
        ])
    }

    func testRejectsLoadedKeyWithUnexpectedLength() throws {
        let client = FakeKeychainClient()
        client.setRawValue(Data(repeating: 2, count: 16), for: Self.vaultID, service: Self.service)
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)

        XCTAssertThrowsError(try store.loadVaultKey(for: Self.vaultID)) { error in
            XCTAssertEqual(error as? AtlasKeychainVaultKeyStoreError, .invalidVaultKeyLength)
        }
    }

    func testSurfacesNonSensitiveKeychainErrors() throws {
        let client = FakeKeychainClient()
        client.forcedAddStatus = errSecNotAvailable
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)

        XCTAssertThrowsError(try store.saveVaultKey(Self.testOnlyVaultKey, for: Self.vaultID)) { error in
            XCTAssertEqual(error as? AtlasKeychainVaultKeyStoreError, .keychainError(errSecNotAvailable))
            XCTAssertFalse(String(describing: error).contains(Self.testOnlyVaultKey.base64EncodedString()))
        }
    }

    func testKeychainAttributesDoNotContainPrivateRecordMeaning() throws {
        let client = FakeKeychainClient()
        let store = AtlasKeychainVaultKeyStore(client: client, service: Self.service)

        try store.saveVaultKey(Self.testOnlyVaultKey, for: Self.vaultID)

        let serializedAttributes = String(describing: try XCTUnwrap(client.addedItems.first))
        for forbidden in [
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata",
            "TOP_SECRET_SENTINEL_DO_NOT_LEAK",
            "TEST_ONLY_SEARCH_TEXT",
            "TEST_ONLY_JOB_KEY",
        ] {
            XCTAssertFalse(serializedAttributes.contains(forbidden), forbidden)
        }
        XCTAssertTrue(serializedAttributes.contains(Self.vaultID))
    }

    func testSourceAvoidsRuntimeUnlockAndFileIOBoundaries() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)

        for forbidden in [
            "LocalAuthentication",
            "LAContext",
            "kSecAttrAccessControl",
            "SecAccessControl",
            "UserDefaults",
            "FileManager",
            "Data(contentsOf",
            ".write(",
            "AtlasLocalCache",
            "SearchViewModel",
            "AtlasAPIClient",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        XCTAssertTrue(source.contains("SecItemAdd"))
        XCTAssertTrue(source.contains("SecItemCopyMatching"))
        XCTAssertTrue(source.contains("SecItemUpdate"))
        XCTAssertTrue(source.contains("SecItemDelete"))
    }

    private static let service = "com.atlasvault.tests.vault-key"
    private static let vaultID = "TEST_ONLY_RANDOM_VAULT_ID"
    private static let testOnlyVaultKey = Data((0..<AtlasVaultRecordCrypto.vaultKeyByteCount).map(UInt8.init))

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/AtlasKeychainVaultKeyStore.swift"),
            sourceDirectory.appendingPathComponent("../../../../apps/apple/Sources/AtlasUI/AtlasKeychainVaultKeyStore.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw NSError(
            domain: "AtlasKeychainVaultKeyStoreTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Could not find AtlasKeychainVaultKeyStore.swift"]
        )
    }
}

private final class FakeKeychainClient: AtlasKeychainClient, @unchecked Sendable {
    struct UpdateCall: Equatable {
        let query: AtlasKeychainQuery
        let attributes: AtlasKeychainUpdate
    }

    private var values: [StorageKey: Data] = [:]
    private(set) var addedItems: [AtlasKeychainItem] = []
    private(set) var copiedQueries: [AtlasKeychainQuery] = []
    private(set) var updatedItems: [UpdateCall] = []
    private(set) var deletedQueries: [AtlasKeychainQuery] = []

    var forcedAddStatus: OSStatus?
    var forcedCopyResult: AtlasKeychainCopyResult?
    var forcedUpdateStatus: OSStatus?
    var forcedDeleteStatus: OSStatus?

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        addedItems.append(item)
        if let forcedAddStatus {
            return forcedAddStatus
        }
        let key = StorageKey(service: item.service, account: item.account)
        guard values[key] == nil else {
            return errSecDuplicateItem
        }
        values[key] = item.valueData
        return errSecSuccess
    }

    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        copiedQueries.append(query)
        if let forcedCopyResult {
            return forcedCopyResult
        }
        let key = StorageKey(service: query.service, account: query.account)
        guard let value = values[key] else {
            return AtlasKeychainCopyResult(status: errSecItemNotFound, valueData: nil)
        }
        return AtlasKeychainCopyResult(status: errSecSuccess, valueData: value)
    }

    func update(_ query: AtlasKeychainQuery, with attributes: AtlasKeychainUpdate) -> OSStatus {
        updatedItems.append(UpdateCall(query: query, attributes: attributes))
        if let forcedUpdateStatus {
            return forcedUpdateStatus
        }
        let key = StorageKey(service: query.service, account: query.account)
        guard values[key] != nil else {
            return errSecItemNotFound
        }
        values[key] = attributes.valueData
        return errSecSuccess
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        deletedQueries.append(query)
        if let forcedDeleteStatus {
            return forcedDeleteStatus
        }
        let key = StorageKey(service: query.service, account: query.account)
        guard values.removeValue(forKey: key) != nil else {
            return errSecItemNotFound
        }
        return errSecSuccess
    }

    func setRawValue(_ value: Data, for vaultID: String, service: String) {
        values[StorageKey(service: service, account: vaultID)] = value
    }

    private struct StorageKey: Hashable {
        let service: String
        let account: String
    }
}
