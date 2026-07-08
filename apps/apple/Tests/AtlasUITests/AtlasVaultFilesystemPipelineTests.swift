import XCTest
@testable import AtlasUI

final class AtlasVaultFilesystemPipelineTests: XCTestCase {
    func testEndToEndTempPipelineWritesAndReadsEncryptedLocalStore() throws {
        let rootURL = try temporaryDirectory()
        let vaultID = Self.vaultID
        let locator = try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)
        let storeURL = try locator.localStoreURL(vaultID: vaultID)
        let store = try localStore(records: vectorRecords())

        try preparer.prepareParentDirectory(for: storeURL, under: rootURL)
        XCTAssertTrue(directoryExists(at: storeURL.deletingLastPathComponent()))
        XCTAssertFalse(FileManager.default.fileExists(atPath: storeURL.path))

        try AtlasVaultLocalStoreIO.write(store, to: storeURL)

        let restored = try AtlasVaultLocalStoreIO.read(from: storeURL)
        XCTAssertEqual(restored, store)
        XCTAssertEqual(restored.records, store.records)
    }

    func testPreparerCreatesParentButWriterCreatesFinalStoreFile() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try localStoreURL(under: rootURL)

        try preparer.prepareParentDirectory(for: storeURL, under: rootURL)

        XCTAssertTrue(directoryExists(at: storeURL.deletingLastPathComponent()))
        XCTAssertFalse(FileManager.default.fileExists(atPath: storeURL.path))

        try AtlasVaultLocalStoreIO.write(try localStore(records: [vectorRecord()]), to: storeURL)

        XCTAssertTrue(FileManager.default.fileExists(atPath: storeURL.path))
    }

    func testPathUsesGenericComponentsAndNoPrivateSentinels() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try localStoreURL(under: rootURL)

        XCTAssertEqual(
            storeURL.path,
            rootURL.standardizedFileURL
                .appendingPathComponent("Atlas", isDirectory: true)
                .appendingPathComponent("Vaults", isDirectory: true)
                .appendingPathComponent(Self.vaultID, isDirectory: true)
                .appendingPathComponent("atlasvault-local-store.json", isDirectory: false)
                .path
        )

        for forbidden in try forbiddenPlaintextStrings() + Self.forbiddenRecordTypes {
            XCTAssertFalse(storeURL.path.contains(forbidden), forbidden)
        }
    }

    func testSerializedStoreOmitsPrivateSentinelsAndRecordTypes() throws {
        let serialized = try XCTUnwrap(
            String(data: AtlasVaultLocalStoreIO.encode(localStore(records: vectorRecords())), encoding: .utf8)
        )

        for forbidden in try forbiddenPlaintextStrings() + Self.forbiddenRecordTypes {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testOverwriteBehaviorRemainsExplicit() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try preparedStoreURL(under: rootURL)
        let original = try localStore(storeID: "TEST_ONLY_PIPELINE_STORE_A", records: [vectorRecord()])
        let replacement = try localStore(storeID: "TEST_ONLY_PIPELINE_STORE_B", records: [tamperedButWellFormedRecord()])

        try AtlasVaultLocalStoreIO.write(original, to: storeURL)

        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.write(replacement, to: storeURL)) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .fileExists)
        }

        try AtlasVaultLocalStoreIO.write(replacement, to: storeURL, overwrite: true)

        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: storeURL), replacement)
    }

    func testInvalidVaultIDFailsBeforeDirectoryCreation() throws {
        let rootURL = try temporaryDirectory()
        let locator = try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)

        XCTAssertThrowsError(try locator.localStoreURL(vaultID: "saved_search")) { error in
            XCTAssertEqual(error as? AtlasVaultPathLocatorError, .invalidVaultID)
        }
        XCTAssertFalse(directoryExists(at: rootURL.appendingPathComponent("Atlas", isDirectory: true)))
    }

    func testParentComponentThatIsFileFailsDuringPreparation() throws {
        let rootURL = try temporaryDirectory()
        let atlasFileURL = rootURL.appendingPathComponent("Atlas", isDirectory: false)
        try "TEST_ONLY_PARENT_FILE".write(to: atlasFileURL, atomically: true, encoding: .utf8)
        let storeURL = try localStoreURL(under: rootURL)

        XCTAssertThrowsError(try preparer.prepareParentDirectory(for: storeURL, under: rootURL)) { error in
            XCTAssertEqual(error as? AtlasVaultDirectoryError, .parentExistsAsFile)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: storeURL.path))
    }

    func testOutsideRootURLFailsDuringPreparation() throws {
        let rootURL = try temporaryDirectory()
        let outsideRoot = try temporaryDirectory(named: "atlasvault-filesystem-pipeline-outside")
        let outsideStoreURL = try localStoreURL(under: outsideRoot)

        XCTAssertThrowsError(try preparer.prepareParentDirectory(for: outsideStoreURL, under: rootURL)) { error in
            XCTAssertEqual(error as? AtlasVaultDirectoryError, .pathEscapesRoot)
        }
        XCTAssertFalse(directoryExists(at: outsideStoreURL.deletingLastPathComponent()))
    }

    func testMalformedStoreReadFailsSafely() throws {
        let storeURL = try preparedStoreURL(under: temporaryDirectory())
        try Data(#"{"format":"atlasvault-local-store","#.utf8).write(to: storeURL)

        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.read(from: storeURL)) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .invalidJSON)
        }
    }

    func testNoAtlasVaultArtifactsOrRepositoryPrivateWrites() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try preparedStoreURL(under: rootURL)

        try AtlasVaultLocalStoreIO.write(try localStore(records: [vectorRecord()]), to: storeURL)

        XCTAssertFalse(containsAtlasVaultArtifact(under: rootURL))
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).standardizedFileURL.path
        XCTAssertFalse(storeURL.standardizedFileURL.path.hasPrefix(currentDirectory))
        XCTAssertFalse(storeURL.path.contains("/private/inputs/"))
        XCTAssertFalse(storeURL.path.contains("/private/jobagg/"))
    }

    private static let vaultID = "9f5c7a2e-6e2d-4df5-9f2d-6d5b4a3c2f10"
    private static let forbiddenRecordTypes = [
        "saved_search",
        "saved_job",
        "application_note",
        "profile_snippet",
        "draft_metadata",
    ]
    private let preparer = AtlasFileManagerVaultDirectoryPreparer()

    private func preparedStoreURL(under rootURL: URL) throws -> URL {
        let storeURL = try localStoreURL(under: rootURL)
        try preparer.prepareParentDirectory(for: storeURL, under: rootURL)
        return storeURL
    }

    private func localStoreURL(under rootURL: URL) throws -> URL {
        try AtlasInjectedRootVaultPathLocator(rootURL: rootURL).localStoreURL(vaultID: Self.vaultID)
    }

    private func localStore(
        storeID: String = "TEST_ONLY_PIPELINE_STORE",
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
            revision: "\(record.revision)-pipeline-overwrite",
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

    private func temporaryDirectory(named prefix: String = "atlasvault-filesystem-pipeline-tests") throws -> URL {
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
            domain: "AtlasVaultFilesystemPipelineTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}
