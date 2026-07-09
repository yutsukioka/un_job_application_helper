import XCTest
@testable import AtlasUI

final class AtlasVaultPersistenceCoordinatorTests: XCTestCase {
    func testInvalidSessionKeyLengthFails() {
        XCTAssertThrowsError(try AtlasVaultUnlockedSession(
            vaultID: Self.vaultID,
            vaultKey: Data(repeating: 1, count: AtlasVaultRecordCrypto.vaultKeyByteCount - 1)
        )) { error in
            XCTAssertEqual(error as? AtlasVaultPersistenceError, .invalidSession)
        }
    }

    func testLoadMissingStoreReturnsNil() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)

        XCTAssertNil(try coordinator.loadEncryptedStore(for: session()))
        XCTAssertFalse(directoryExists(at: rootURL.appendingPathComponent("Atlas", isDirectory: true)))
    }

    func testSaveEncryptedStoreCreatesParentDirectoriesThroughPreparer() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let storeURL = try localStoreURL(rootURL: rootURL)

        XCTAssertFalse(directoryExists(at: storeURL.deletingLastPathComponent()))

        try coordinator.saveEncryptedStore(try localStore(records: [vectorRecord()]), for: session(), overwrite: false)

        XCTAssertTrue(directoryExists(at: storeURL.deletingLastPathComponent()))
        XCTAssertTrue(FileManager.default.fileExists(atPath: storeURL.path))
    }

    func testSaveUsesInjectedPreparerAndLocalStoreIO() throws {
        let rootURL = try temporaryDirectory()
        let pathLocator = try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)
        let directoryPreparer = RecordingDirectoryPreparer()
        let localStoreIO = RecordingLocalStoreIO()
        let coordinator = AtlasVaultPersistenceCoordinator(
            environment: AtlasVaultPersistenceEnvironment(
                rootDirectory: rootURL,
                pathLocator: pathLocator,
                directoryPreparer: directoryPreparer,
                localStoreIO: localStoreIO
            )
        )
        let store = try localStore(records: [vectorRecord()])
        let expectedStoreURL = try pathLocator.localStoreURL(vaultID: Self.vaultID)

        try coordinator.saveEncryptedStore(store, for: session(), overwrite: false)

        XCTAssertEqual(directoryPreparer.storeURL?.path, expectedStoreURL.path)
        XCTAssertEqual(directoryPreparer.rootDirectory?.path, rootURL.path)
        XCTAssertEqual(localStoreIO.writtenStore, store)
        XCTAssertEqual(localStoreIO.writtenURL?.path, expectedStoreURL.path)
        XCTAssertEqual(localStoreIO.writtenOverwrite, false)
        XCTAssertFalse(FileManager.default.fileExists(atPath: expectedStoreURL.path))
    }

    func testSaveEncryptedStoreWritesLocalStoreJSON() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try localStoreURL(rootURL: rootURL)

        try coordinator(rootURL: rootURL).saveEncryptedStore(
            try localStore(records: [vectorRecord()]),
            for: session(),
            overwrite: false
        )

        let serialized = try XCTUnwrap(String(data: Data(contentsOf: storeURL), encoding: .utf8))
        XCTAssertTrue(serialized.contains(AtlasVaultLocalStoreIO.localStoreFormat))
        XCTAssertTrue(serialized.contains("TEST_ONLY_PERSISTENCE_STORE"))
    }

    func testLoadAfterSaveReturnsSameEncryptedRecords() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let store = try localStore(records: vectorRecords())

        try coordinator.saveEncryptedStore(store, for: session(), overwrite: false)

        let loaded = try XCTUnwrap(coordinator.loadEncryptedStore(for: session()))
        XCTAssertEqual(loaded, store)
        XCTAssertEqual(loaded.records, store.records)
    }

    func testSaveWithoutOverwriteFailsWhenFileExists() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let original = try localStore(storeID: "TEST_ONLY_PERSISTENCE_STORE_A", records: [vectorRecord()])
        let replacement = try localStore(storeID: "TEST_ONLY_PERSISTENCE_STORE_B", records: [tamperedButWellFormedRecord()])

        try coordinator.saveEncryptedStore(original, for: session(), overwrite: false)

        XCTAssertThrowsError(try coordinator.saveEncryptedStore(replacement, for: session(), overwrite: false)) { error in
            XCTAssertEqual(error as? AtlasVaultPersistenceError, .fileExists)
        }
    }

    func testSaveWithOverwriteSucceeds() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let original = try localStore(storeID: "TEST_ONLY_PERSISTENCE_STORE_A", records: [vectorRecord()])
        let replacement = try localStore(storeID: "TEST_ONLY_PERSISTENCE_STORE_B", records: [tamperedButWellFormedRecord()])

        try coordinator.saveEncryptedStore(original, for: session(), overwrite: false)
        try coordinator.saveEncryptedStore(replacement, for: session(), overwrite: true)

        XCTAssertEqual(try coordinator.loadEncryptedStore(for: session()), replacement)
    }

    func testCorruptStoreReadFailsSafely() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try preparedStoreURL(rootURL: rootURL)
        try Data(#"{"format":"atlasvault-local-store","#.utf8).write(to: storeURL)

        XCTAssertThrowsError(try coordinator(rootURL: rootURL).loadEncryptedStore(for: session())) { error in
            XCTAssertEqual(error as? AtlasVaultPersistenceError, .corruptStore)
        }
    }

    func testCoordinatorDoesNotDecryptRecords() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let store = try localStore(records: [tamperedButWellFormedRecord()])

        try coordinator.saveEncryptedStore(store, for: session(), overwrite: false)

        let loaded = try XCTUnwrap(coordinator.loadEncryptedStore(for: session()))
        XCTAssertEqual(loaded.records, store.records)
    }

    func testSerializedStoreDoesNotContainPrivateSentinelsOrRecordTypes() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try localStoreURL(rootURL: rootURL)

        try coordinator(rootURL: rootURL).saveEncryptedStore(
            try localStore(records: vectorRecords()),
            for: session(),
            overwrite: false
        )
        let serialized = try XCTUnwrap(String(data: Data(contentsOf: storeURL), encoding: .utf8))

        for forbidden in try forbiddenPlaintextStrings() + Self.forbiddenRecordTypes {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testSourceAvoidsRuntimeWiringAndKeyRetrievalSurfaces() throws {
        let source = try String(contentsOf: sourceFileURL(), encoding: .utf8)
        for forbidden in [
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "UserDefaults",
            "ApplicationSupport",
            "AtlasLocalCache",
            "SearchViewModel",
            "URLSession",
            "SwiftUI",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testNoAtlasVaultArtifactsOrPrivateWrites() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try localStoreURL(rootURL: rootURL)

        try coordinator(rootURL: rootURL).saveEncryptedStore(
            try localStore(records: [vectorRecord()]),
            for: session(),
            overwrite: false
        )

        XCTAssertFalse(containsAtlasVaultArtifact(under: rootURL))
        XCTAssertFalse(storeURL.path.contains("/private/inputs/"))
        XCTAssertFalse(storeURL.path.contains("/private/jobagg/"))
    }

    func testSessionStringOutputDoesNotLeakVaultKey() throws {
        let key = Data(0..<UInt8(AtlasVaultRecordCrypto.vaultKeyByteCount))
        let session = try AtlasVaultUnlockedSession(vaultID: Self.vaultID, vaultKey: key)
        let base64Key = key.base64EncodedString()
        let hexKey = key.map { String(format: "%02x", $0) }.joined()

        XCTAssertFalse(String(describing: session).contains(base64Key))
        XCTAssertFalse(String(reflecting: session).contains(base64Key))
        XCTAssertFalse(String(describing: session).contains(hexKey))
        XCTAssertFalse(String(reflecting: session).contains(hexKey))
        XCTAssertTrue(String(describing: session).contains("<redacted"))
    }

    private static let vaultID = "9f5c7a2e-6e2d-4df5-9f2d-6d5b4a3c2f10"
    private static let forbiddenRecordTypes = [
        "saved_search",
        "saved_job",
        "application_note",
        "profile_snippet",
        "draft_metadata",
    ]

    private func coordinator(
        rootURL: URL
    ) throws -> AtlasVaultPersistenceCoordinator<
        AtlasInjectedRootVaultPathLocator,
        AtlasFileManagerVaultDirectoryPreparer,
        AtlasVaultLocalStoreFileIO
    > {
        AtlasVaultPersistenceCoordinator(
            environment: AtlasVaultPersistenceEnvironment(
                rootDirectory: rootURL,
                pathLocator: try AtlasInjectedRootVaultPathLocator(rootURL: rootURL),
                directoryPreparer: AtlasFileManagerVaultDirectoryPreparer()
            )
        )
    }

    private func session() throws -> AtlasVaultUnlockedSession {
        try AtlasVaultUnlockedSession(
            vaultID: Self.vaultID,
            vaultKey: Data(repeating: 1, count: AtlasVaultRecordCrypto.vaultKeyByteCount)
        )
    }

    private func preparedStoreURL(rootURL: URL) throws -> URL {
        let url = try localStoreURL(rootURL: rootURL)
        try AtlasFileManagerVaultDirectoryPreparer().prepareParentDirectory(for: url, under: rootURL)
        return url
    }

    private func localStoreURL(rootURL: URL) throws -> URL {
        try AtlasInjectedRootVaultPathLocator(rootURL: rootURL).localStoreURL(vaultID: Self.vaultID)
    }

    private func localStore(
        storeID: String = "TEST_ONLY_PERSISTENCE_STORE",
        records: [AtlasVaultEncryptedRecordEnvelope]
    ) throws -> AtlasVaultLocalStoreEnvelope {
        AtlasVaultLocalStoreEnvelope(
            storeID: storeID,
            createdAt: "2026-01-01T00:00:00Z",
            updatedAt: "2026-01-02T00:00:00Z",
            vaultMetadata: [
                "format": .string("atlas-vault"),
                "version": .number(1),
                "vault_id": .string(Self.vaultID),
                "crypto": .object([
                    "record_aead": .string("AES-256-GCM"),
                    "kdf": .string("Argon2id"),
                    "subkey_kdf": .string("HKDF-SHA256"),
                    "key_wrap_aead": .string("AES-256-GCM"),
                ]),
                "key_wraps": .array([]),
            ],
            records: records
        )
    }

    private func vectorRecords() throws -> [AtlasVaultEncryptedRecordEnvelope] {
        try vectors().map(recordEnvelope)
    }

    private func vectorRecord() throws -> AtlasVaultEncryptedRecordEnvelope {
        try XCTUnwrap(vectorRecords().first)
    }

    private func tamperedButWellFormedRecord() throws -> AtlasVaultEncryptedRecordEnvelope {
        let record = try vectorRecord()
        return AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: "\(record.revision)-persistence-overwrite",
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: Data(repeating: 7, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString(),
            ciphertext: Data(repeating: 8, count: AtlasVaultRecordCrypto.gcmTagByteCount).base64EncodedString()
        )
    }

    private func forbiddenPlaintextStrings() throws -> [String] {
        try vectors().flatMap { vector in
            try stringArray(vector["forbidden_plaintext_strings"], context: "forbidden_plaintext_strings")
        }
    }

    private func vectors() throws -> [[String: Any]] {
        let data = try Data(contentsOf: vectorFileURL(fileName: "atlasvault_crypto_vectors_v1.json"))
        let object = try dictionary(JSONSerialization.jsonObject(with: data), context: "crypto vector root")
        guard let vectors = object["vectors"] as? [[String: Any]], !vectors.isEmpty else {
            throw testError("vectors must be a non-empty object array")
        }
        return vectors
    }

    private func recordEnvelope(_ vector: [String: Any]) throws -> AtlasVaultEncryptedRecordEnvelope {
        let record = try dictionary(vector["record"], context: "record")
        return try JSONDecoder().decode(
            AtlasVaultEncryptedRecordEnvelope.self,
            from: JSONSerialization.data(withJSONObject: record, options: [.sortedKeys])
        )
    }

    private func temporaryDirectory(named prefix: String = "atlasvault-persistence-coordinator-tests") throws -> URL {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("\(prefix)-\(UUID().uuidString)", isDirectory: true)
            .standardizedFileURL
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: url)
        }
        return url
    }

    private func vectorFileURL(fileName: String) throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            currentDirectory.appendingPathComponent("../../contracts/sync/test_vectors/\(fileName)"),
            currentDirectory.appendingPathComponent("contracts/sync/test_vectors/\(fileName)"),
            sourceDirectory.appendingPathComponent("../../../../contracts/sync/test_vectors/\(fileName)"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find shared AtlasVault vector file \(fileName)")
    }

    private func sourceFileURL() throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let candidates = [
            currentDirectory.appendingPathComponent("Sources/AtlasUI/AtlasVaultPersistenceCoordinator.swift"),
            currentDirectory.appendingPathComponent("apps/apple/Sources/AtlasUI/AtlasVaultPersistenceCoordinator.swift"),
            URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .appendingPathComponent("../../Sources/AtlasUI/AtlasVaultPersistenceCoordinator.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find AtlasVaultPersistenceCoordinator.swift")
    }

    private func containsAtlasVaultArtifact(under rootURL: URL) -> Bool {
        guard let enumerator = FileManager.default.enumerator(at: rootURL, includingPropertiesForKeys: nil) else {
            return false
        }
        for case let url as URL in enumerator where url.pathExtension == "atlasvault" {
            return true
        }
        return false
    }

    private func directoryExists(at url: URL) -> Bool {
        var isDirectory = ObjCBool(false)
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) && isDirectory.boolValue
    }

    private func dictionary(_ value: Any?, context: String) throws -> [String: Any] {
        guard let dictionary = value as? [String: Any] else {
            throw testError("\(context) must be an object")
        }
        return dictionary
    }

    private func stringArray(_ value: Any?, context: String) throws -> [String] {
        guard let array = value as? [String] else {
            throw testError("\(context) must be a string array")
        }
        return array
    }

    private func testError(_ message: String) -> NSError {
        NSError(
            domain: "AtlasVaultPersistenceCoordinatorTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}

private final class RecordingDirectoryPreparer: AtlasVaultDirectoryPreparer, @unchecked Sendable {
    private(set) var storeURL: URL?
    private(set) var rootDirectory: URL?

    func prepareParentDirectory(for storeURL: URL, under rootDirectory: URL) throws {
        self.storeURL = storeURL
        self.rootDirectory = rootDirectory
    }
}

private final class RecordingLocalStoreIO: AtlasVaultLocalStoreProviding, @unchecked Sendable {
    private(set) var writtenStore: AtlasVaultLocalStoreEnvelope?
    private(set) var writtenURL: URL?
    private(set) var writtenOverwrite: Bool?

    func read(from url: URL) throws -> AtlasVaultLocalStoreEnvelope {
        throw AtlasVaultPersistenceError.readFailed
    }

    func write(_ store: AtlasVaultLocalStoreEnvelope, to url: URL, overwrite: Bool) throws {
        writtenStore = store
        writtenURL = url
        writtenOverwrite = overwrite
    }
}
