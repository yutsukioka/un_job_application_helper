import XCTest
@testable import AtlasUI

final class AtlasVaultLocalStoreTests: XCTestCase {
    func testLocalStoreEnvelopeEncodesAndDecodes() throws {
        let store = try localStore(records: [vectorRecord()])

        let restored = try AtlasVaultLocalStoreIO.decode(AtlasVaultLocalStoreIO.encode(store))

        XCTAssertEqual(restored, store)
    }

    func testLocalStoreWritesAndReadsFromTempPath() throws {
        let store = try localStore(records: [vectorRecord()])
        let directory = try temporaryDirectory()
        let url = directory.appendingPathComponent("vault-store.json")

        try AtlasVaultLocalStoreIO.write(store, to: url)

        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: url), store)
    }

    func testWriteRefusesOverwriteByDefault() throws {
        let store = try localStore(records: [vectorRecord()])
        let directory = try temporaryDirectory()
        let url = directory.appendingPathComponent("vault-store.json")
        try AtlasVaultLocalStoreIO.write(store, to: url)

        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.write(store, to: url)) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .fileExists)
        }
    }

    func testWriteSucceedsWithExplicitOverwrite() throws {
        let original = try localStore(storeID: "TEST_ONLY_STORE_A", records: [vectorRecord()])
        let replacement = try localStore(storeID: "TEST_ONLY_STORE_B", records: [tamperedButWellFormedRecord()])
        let directory = try temporaryDirectory()
        let url = directory.appendingPathComponent("vault-store.json")
        try AtlasVaultLocalStoreIO.write(original, to: url)

        try AtlasVaultLocalStoreIO.write(replacement, to: url, overwrite: true)

        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: url), replacement)
    }

    func testUnsupportedStoreVersionFails() throws {
        var object = try jsonObject(for: localStore(records: [vectorRecord()]))
        object["version"] = 2

        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.decode(jsonData(from: object))) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .unsupportedStoreVersion)
        }
    }

    func testInvalidFormatFails() throws {
        var object = try jsonObject(for: localStore(records: [vectorRecord()]))
        object["format"] = "wrong-format"

        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.decode(jsonData(from: object))) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .invalidStoreFormat)
        }
    }

    func testMalformedJSONFails() {
        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.decode(Data(#"{"format":"atlasvault-local-store","#.utf8))) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .invalidJSON)
        }
    }

    func testStoreWithEncryptedVectorRecordRoundTrips() throws {
        let record = try vectorRecord()
        let store = try localStore(records: [record])

        let restored = try AtlasVaultLocalStoreIO.decode(AtlasVaultLocalStoreIO.encode(store))

        XCTAssertEqual(restored.records, [record])
        XCTAssertEqual(restored.records.first?.ciphertext, record.ciphertext)
    }

    func testStoreSerializationOmitsPrivateVectorSentinelsAndRecordTypes() throws {
        let store = try localStore(records: try vectorRecords())
        let serialized = try XCTUnwrap(String(data: AtlasVaultLocalStoreIO.encode(store), encoding: .utf8))

        for forbidden in try forbiddenPlaintextStrings() {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testStoreSerializationOmitsRepresentativePrivatePayloadValues() throws {
        let store = try localStore(records: try vectorRecords())
        let serialized = try XCTUnwrap(String(data: AtlasVaultLocalStoreIO.encode(store), encoding: .utf8))

        for forbidden in [
            "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
            "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
            "FAKE_PRIVATE_FILTER_DO_NOT_LEAK",
            "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
            "FAKE_SAVED_JOB_STATUS_DO_NOT_LEAK",
            "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
            "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
            "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK",
            "TOP_SECRET_SENTINEL_DO_NOT_LEAK",
        ] {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testStoreHelperDoesNotDecryptRecords() throws {
        let record = try tamperedButWellFormedRecord()
        let store = try localStore(records: [record])

        let restored = try AtlasVaultLocalStoreIO.decode(AtlasVaultLocalStoreIO.encode(store))

        XCTAssertEqual(restored.records, [record])
    }

    func testInvalidRecordSchemaFails() throws {
        let record = try vectorRecord()
        let unsupportedRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion + 1,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: record.nonce,
            ciphertext: record.ciphertext
        )
        let store = try localStore(records: [unsupportedRecord])

        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.encode(store)) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .invalidRecord)
        }
    }

    func testInvalidRecordBase64Fails() throws {
        let record = try vectorRecord()
        let invalidRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: "not-base64",
            ciphertext: record.ciphertext
        )
        let store = try localStore(records: [invalidRecord])

        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.encode(store)) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .invalidRecord)
        }
    }

    func testRejectsLegacyPrivateSnapshotFields() throws {
        var object = try jsonObject(for: localStore(records: [vectorRecord()]))
        object["savedSearches"] = []
        object["savedJobs"] = []

        XCTAssertThrowsError(try AtlasVaultLocalStoreIO.decode(jsonData(from: object))) { error in
            XCTAssertEqual(error as? AtlasVaultStoreError, .invalidEnvelope)
        }
    }

    func testSourceAvoidsKeychainDefaultsRuntimeWiringAndNetworking() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)

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
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private static let storeID = "TEST_ONLY_STORE_ID"
    private static let createdAt = "2026-01-01T00:00:00Z"
    private static let updatedAt = "2026-01-02T00:00:00Z"

    private func localStore(
        storeID: String = AtlasVaultLocalStoreTests.storeID,
        records: [AtlasVaultEncryptedRecordEnvelope]
    ) throws -> AtlasVaultLocalStoreEnvelope {
        AtlasVaultLocalStoreEnvelope(
            storeID: storeID,
            createdAt: Self.createdAt,
            updatedAt: Self.updatedAt,
            vaultMetadata: [
                "format": .string("atlas-vault"),
                "version": .number(1),
                "vault_id": .string("TEST_ONLY_VAULT_ID"),
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
            revision: "\(record.revision)-tampered",
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: Data(repeating: 3, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString(),
            ciphertext: Data(repeating: 4, count: AtlasVaultRecordCrypto.gcmTagByteCount).base64EncodedString()
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
        return try decoder.decode(
            AtlasVaultEncryptedRecordEnvelope.self,
            from: JSONSerialization.data(withJSONObject: record, options: [.sortedKeys])
        )
    }

    private func jsonObject(for store: AtlasVaultLocalStoreEnvelope) throws -> [String: Any] {
        try dictionary(
            JSONSerialization.jsonObject(with: AtlasVaultLocalStoreIO.encode(store)),
            context: "local store"
        )
    }

    private func jsonData(from object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    }

    private func temporaryDirectory() throws -> URL {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("atlasvault-store-tests-\(UUID().uuidString)", isDirectory: true)
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

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/AtlasVaultLocalStore.swift"),
            sourceDirectory.appendingPathComponent("../../../../apps/apple/Sources/AtlasUI/AtlasVaultLocalStore.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find AtlasVaultLocalStore.swift")
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
            domain: "AtlasVaultLocalStoreTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}

private let decoder = JSONDecoder()
