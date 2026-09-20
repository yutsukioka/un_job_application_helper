import XCTest
@testable import AtlasUI

final class AtlasVaultPersistenceCoordinatorSaveTests: XCTestCase {
    func testSaverOutputMergesIntoExistingStoreAndWritesEncryptedJSONUnderTempRoot() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let untouched = encryptedRecord(id: "record-untouched", revision: "revision-untouched")
        let original = localStore(records: [untouched])
        try coordinator.saveEncryptedStore(original, for: session(), overwrite: false)

        let savedRecords = try savedSearchRecords()
        try coordinator.saveEncryptedRecords(
            savedRecords,
            for: session(),
            overwrite: true,
            merger: merger()
        )

        let loaded = try XCTUnwrap(coordinator.loadEncryptedStore(for: session()))
        XCTAssertEqual(loaded.records, [untouched] + savedRecords)
        XCTAssertEqual(loaded.updatedAt, Self.mergedAt)

        let serialized = try XCTUnwrap(String(data: Data(contentsOf: try localStoreURL(rootURL: rootURL)), encoding: .utf8))
        for forbidden in Self.forbiddenPlaintext {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testSavePreservesUntouchedRecords() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let active = encryptedRecord(id: "record-active", revision: "revision-active")
        let untouched = encryptedRecord(id: "record-untouched", revision: "revision-untouched", nonceByte: 3)
        try coordinator.saveEncryptedStore(localStore(records: [active, untouched]), for: session(), overwrite: false)

        let update = encryptedRecord(
            id: "record-active",
            revision: "revision-active-next",
            parentRevision: "revision-active",
            nonceByte: 4
        )
        try coordinator.saveEncryptedRecords([update], for: session(), overwrite: true, merger: merger())

        let loaded = try XCTUnwrap(coordinator.loadEncryptedStore(for: session()))
        XCTAssertEqual(loaded.records, [update, untouched])
    }

    func testSaveTombstoneReplacesActiveRecord() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let active = encryptedRecord(id: "record-delete", revision: "revision-live")
        try coordinator.saveEncryptedStore(localStore(records: [active]), for: session(), overwrite: false)

        let tombstone = encryptedRecord(
            id: "record-delete",
            revision: "revision-deleted",
            parentRevision: "revision-live",
            deleted: true,
            nonceByte: 5
        )
        try coordinator.saveEncryptedRecords([tombstone], for: session(), overwrite: true, merger: merger())

        let loaded = try XCTUnwrap(coordinator.loadEncryptedStore(for: session()))
        XCTAssertEqual(loaded.records, [tombstone])
        XCTAssertTrue(try XCTUnwrap(loaded.records.first).deleted)
    }

    func testSaveDuplicateIncomingIDsFailsBeforeWrite() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let original = localStore(records: [encryptedRecord(id: "record-original", revision: "revision-original")])
        try coordinator.saveEncryptedStore(original, for: session(), overwrite: false)

        let incomingA = encryptedRecord(id: "record-duplicate", revision: "revision-a")
        let incomingB = encryptedRecord(id: "record-duplicate", revision: "revision-b", nonceByte: 6)

        XCTAssertThrowsError(try coordinator.saveEncryptedRecords(
            [incomingA, incomingB],
            for: session(),
            overwrite: true,
            merger: merger()
        )) { error in
            XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .duplicateIncomingRecordID)
        }
        XCTAssertEqual(try coordinator.loadEncryptedStore(for: session()), original)
    }

    func testSaveStaleRevisionFailsBeforeWrite() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let originalRecord = encryptedRecord(id: "record-stale", revision: "revision-current")
        let original = localStore(records: [originalRecord])
        try coordinator.saveEncryptedStore(original, for: session(), overwrite: false)

        let stale = encryptedRecord(
            id: "record-stale",
            revision: "revision-next",
            parentRevision: "revision-old",
            nonceByte: 7
        )

        XCTAssertThrowsError(try coordinator.saveEncryptedRecords(
            [stale],
            for: session(),
            overwrite: true,
            merger: merger()
        )) { error in
            XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .staleParentRevision)
        }
        XCTAssertEqual(try coordinator.loadEncryptedStore(for: session()), original)
    }

    func testStoreWriterOverwriteBehaviorRemainsExplicit() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let original = localStore(records: [encryptedRecord(id: "record-original", revision: "revision-original")])
        try coordinator.saveEncryptedStore(original, for: session(), overwrite: false)
        let incoming = encryptedRecord(id: "record-new", revision: "revision-new", nonceByte: 8)

        XCTAssertThrowsError(try coordinator.saveEncryptedRecords(
            [incoming],
            for: session(),
            overwrite: false,
            merger: merger()
        )) { error in
            XCTAssertEqual(error as? AtlasVaultPersistenceError, .fileExists)
        }
        XCTAssertEqual(try coordinator.loadEncryptedStore(for: session()), original)

        try coordinator.saveEncryptedRecords([incoming], for: session(), overwrite: true, merger: merger())
        let loaded = try XCTUnwrap(coordinator.loadEncryptedStore(for: session()))
        XCTAssertEqual(loaded.records, original.records + [incoming])
    }

    func testMalformedStoreFailsBeforeMerge() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = try preparedStoreURL(rootURL: rootURL)
        try Data(#"{"format":"atlasvault-local-store","#.utf8).write(to: storeURL)

        XCTAssertThrowsError(try coordinator(rootURL: rootURL).saveEncryptedRecords(
            [encryptedRecord(id: "record-new", revision: "revision-new")],
            for: session(),
            overwrite: true,
            merger: merger()
        )) { error in
            XCTAssertEqual(error as? AtlasVaultPersistenceError, .corruptStore)
        }
    }

    func testNoPublicSnapshotMutationOccurs() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)
        let snapshot = try publicSnapshot()
        let before = try encodedSnapshot(snapshot)

        try coordinator.saveEncryptedStore(localStore(records: []), for: session(), overwrite: false)
        try coordinator.saveEncryptedRecords(
            [encryptedRecord(id: "record-new", revision: "revision-new", nonceByte: 9)],
            for: session(),
            overwrite: true,
            merger: merger()
        )

        XCTAssertEqual(try encodedSnapshot(snapshot), before)
    }

    func testNoAtlasVaultArtifactsAreCreated() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try coordinator(rootURL: rootURL)

        try coordinator.saveEncryptedStore(localStore(records: []), for: session(), overwrite: false)
        try coordinator.saveEncryptedRecords(
            [encryptedRecord(id: "record-new", revision: "revision-new", nonceByte: 10)],
            for: session(),
            overwrite: true,
            merger: merger()
        )

        XCTAssertFalse(containsAtlasVaultArtifact(under: rootURL))
    }

    private static let vaultID = "00000000-0000-4000-8000-000000000220"
    private static let keyID = "phase2d22-test-key"
    private static let storeID = "TEST_ONLY_COORDINATOR_SAVE_STORE"
    private static let createdAt = "2026-01-01T00:00:00Z"
    private static let updatedAt = "2026-01-02T00:00:00Z"
    private static let mergedAt = "2026-01-03T00:00:00Z"
    private static let vaultKey = Data((0..<AtlasVaultRecordCrypto.vaultKeyByteCount).map { UInt8($0) })
    private static let forbiddenPlaintext = [
        "saved_search",
        "saved_job",
        "application_note",
        "profile_snippet",
        "draft_metadata",
        "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
        "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
        "FAKE_PRIVATE_FILTER_DO_NOT_LEAK",
        "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
        "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
        "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
        "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK",
        "TOP_SECRET_SENTINEL_DO_NOT_LEAK",
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

    private func merger() -> AtlasVaultLocalStoreMerger {
        AtlasVaultLocalStoreMerger(updatedAtProvider: { Self.mergedAt })
    }

    private func session() throws -> AtlasVaultUnlockedSession {
        try AtlasVaultUnlockedSession(vaultID: Self.vaultID, vaultKey: Self.vaultKey)
    }

    private func savedSearchRecords() throws -> [AtlasVaultEncryptedRecordEnvelope] {
        let saver = AtlasVaultRecordSaver(
            recordIDGenerator: { "record-created-001" },
            revisionIDGenerator: { "revision-created-001" },
            nonceGenerator: {
                Data(repeating: 42, count: AtlasVaultRecordCrypto.nonceByteCount)
            }
        )
        return try saver.save(
            mutations: AtlasVaultMutationSet(creates: [
                AtlasVaultCreateMutation(payload: savedSearchPayload(), keyID: Self.keyID),
            ]),
            session: session()
        )
    }

    private func savedSearchPayload() -> AtlasVaultSavePayload {
        .savedSearch(AtlasSavedSearchVaultRecordPayload(
            type: .savedSearch,
            payload: AtlasSavedSearchVaultPayload(
                name: "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
                summary: "FAKE_PRIVATE_FILTER_DO_NOT_LEAK",
                description: nil,
                request: AtlasSearchRequest(text: "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK"),
                createdAt: Self.createdAt,
                updatedAt: Self.updatedAt
            ),
            clientCreatedAt: Self.createdAt,
            clientUpdatedAt: Self.updatedAt
        ))
    }

    private func localStore(records: [AtlasVaultEncryptedRecordEnvelope]) -> AtlasVaultLocalStoreEnvelope {
        AtlasVaultLocalStoreEnvelope(
            storeID: Self.storeID,
            createdAt: Self.createdAt,
            updatedAt: Self.updatedAt,
            vaultMetadata: [
                "format": .string("atlas-vault"),
                "version": .number(1),
                "vault_id": .string(Self.vaultID),
                "key_wraps": .array([]),
            ],
            records: records
        )
    }

    private func encryptedRecord(
        id: String,
        revision: String,
        parentRevision: String? = nil,
        deleted: Bool = false,
        nonceByte: UInt8 = 1,
        ciphertextByte: UInt8 = 2
    ) -> AtlasVaultEncryptedRecordEnvelope {
        AtlasVaultEncryptedRecordEnvelope(
            id: id,
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion,
            revision: revision,
            parentRevision: parentRevision,
            deleted: deleted,
            keyID: Self.keyID,
            nonce: Data(repeating: nonceByte, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString(),
            ciphertext: Data(repeating: ciphertextByte, count: AtlasVaultRecordCrypto.gcmTagByteCount).base64EncodedString()
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

    private func temporaryDirectory() throws -> URL {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("atlasvault-coordinator-save-tests-\(UUID().uuidString)", isDirectory: true)
            .standardizedFileURL
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: url)
        }
        return url
    }

    private func publicSnapshot() throws -> AtlasPublicLocalSnapshot {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(AtlasPublicLocalSnapshot.self, from: Data("""
        {
          "savedAt": "2026-01-07T00:00:00Z",
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

    private func encodedSnapshot(_ snapshot: AtlasPublicLocalSnapshot) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(snapshot)
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
}
