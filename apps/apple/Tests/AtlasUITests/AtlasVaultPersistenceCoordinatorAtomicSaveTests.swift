import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultPersistenceCoordinatorAtomicSaveTests: XCTestCase {
    func testNewStoreSaveUsesInjectedAtomicWriterInsteadOfDirectStoreIO() throws {
        let rootURL = try temporaryDirectory()
        let atomicWriter = RecordingAtomicStoreWriter()
        let localStoreIO = RecordingAtomicSaveLocalStoreIO()
        let coordinator = AtlasVaultPersistenceCoordinator(
            environment: AtlasVaultPersistenceEnvironment(
                rootDirectory: rootURL,
                pathLocator: try AtlasInjectedRootVaultPathLocator(rootURL: rootURL),
                directoryPreparer: AtlasFileManagerVaultDirectoryPreparer(),
                localStoreIO: localStoreIO,
                atomicStoreWriter: atomicWriter
            )
        )
        let store = localStore(records: [])

        let result = try coordinator.saveEncryptedStoreAtomically(
            store,
            for: session(),
            overwrite: false
        )

        XCTAssertEqual(result.commitState, .committed)
        XCTAssertEqual(atomicWriter.callCount, 1)
        XCTAssertEqual(atomicWriter.writtenStore, store)
        XCTAssertEqual(atomicWriter.overwrite, false)
        XCTAssertEqual(localStoreIO.writeCallCount, 0)
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: try localStoreURL(rootURL: rootURL).deletingLastPathComponent().path
        ))
    }

    func testExistingStoreUpdateMergesBeforeAtomicWriterInvocation() throws {
        let rootURL = try temporaryDirectory()
        let active = encryptedRecord(id: "record-active", revision: "revision-current")
        let untouched = encryptedRecord(id: "record-untouched", revision: "revision-untouched", nonceByte: 3)
        try seedStore(localStore(records: [active, untouched]), rootURL: rootURL)
        let update = encryptedRecord(
            id: "record-active",
            revision: "revision-next",
            parentRevision: "revision-current",
            nonceByte: 4
        )
        let atomicWriter = RecordingAtomicStoreWriter()

        let result = try coordinator(rootURL: rootURL, atomicWriter: atomicWriter)
            .saveEncryptedRecordsAtomically(
                [update],
                for: session(),
                overwrite: true,
                merger: deterministicMerger()
            )

        XCTAssertEqual(result.commitState, .committed)
        XCTAssertEqual(atomicWriter.callCount, 1)
        XCTAssertEqual(atomicWriter.writtenStore?.records, [update, untouched])
        XCTAssertEqual(atomicWriter.writtenStore?.updatedAt, Self.mergedAt)
        XCTAssertEqual(atomicWriter.overwrite, true)
    }

    func testDuplicateIncomingIDsFailBeforeAtomicWriterCall() throws {
        let rootURL = try temporaryDirectory()
        let original = localStore(records: [encryptedRecord(id: "record-original", revision: "revision-original")])
        try seedStore(original, rootURL: rootURL)
        let incomingA = encryptedRecord(id: "record-duplicate", revision: "revision-a")
        let incomingB = encryptedRecord(id: "record-duplicate", revision: "revision-b", nonceByte: 5)
        let atomicWriter = RecordingAtomicStoreWriter()

        XCTAssertThrowsError(try coordinator(rootURL: rootURL, atomicWriter: atomicWriter)
            .saveEncryptedRecordsAtomically(
                [incomingA, incomingB],
                for: session(),
                overwrite: true,
                merger: deterministicMerger()
            )) { error in
                XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .duplicateIncomingRecordID)
            }
        XCTAssertEqual(atomicWriter.callCount, 0)
        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: localStoreURL(rootURL: rootURL)), original)
    }

    func testStaleParentRevisionFailsBeforeAtomicWriterCall() throws {
        let rootURL = try temporaryDirectory()
        let original = localStore(records: [encryptedRecord(id: "record-stale", revision: "revision-current")])
        try seedStore(original, rootURL: rootURL)
        let stale = encryptedRecord(
            id: "record-stale",
            revision: "revision-next",
            parentRevision: "revision-old",
            nonceByte: 6
        )
        let atomicWriter = RecordingAtomicStoreWriter()

        XCTAssertThrowsError(try coordinator(rootURL: rootURL, atomicWriter: atomicWriter)
            .saveEncryptedRecordsAtomically(
                [stale],
                for: session(),
                overwrite: true,
                merger: deterministicMerger()
            )) { error in
                XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .staleParentRevision)
            }
        XCTAssertEqual(atomicWriter.callCount, 0)
        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: localStoreURL(rootURL: rootURL)), original)
    }

    func testPreCommitWriteFailurePreservesPreviousDestination() throws {
        try assertAtomicFailurePreservesDestination(.writeFailed)
    }

    func testValidationFailurePreservesPreviousDestination() throws {
        try assertAtomicFailurePreservesDestination(.validationFailed)
    }

    func testReplacementFailurePreservesPreviousDestination() throws {
        try assertAtomicFailurePreservesDestination(.replacementFailed)
    }

    func testCommittedDurabilityUnconfirmedResultPropagatesWithoutRemapping() throws {
        let rootURL = try temporaryDirectory()
        let atomicWriter = RecordingAtomicStoreWriter(
            result: AtlasVaultAtomicWriteResult(commitState: .committedDurabilityUnconfirmed)
        )
        let store = localStore(records: [])

        let result = try coordinator(rootURL: rootURL, atomicWriter: atomicWriter)
            .saveEncryptedStoreAtomically(store, for: session(), overwrite: false)

        XCTAssertEqual(result.commitState, .committedDurabilityUnconfirmed)
        XCTAssertEqual(atomicWriter.callCount, 1)
    }

    func testAtomicSaveCanonicalizesAcceptedSymlinkedRootBeforeWriterInvocation() throws {
        let roots = try symlinkedRoot()
        let atomicWriter = RecordingAtomicStoreWriter()

        _ = try coordinator(rootURL: roots.injected, atomicWriter: atomicWriter)
            .saveEncryptedStoreAtomically(
                localStore(records: []),
                for: session(),
                overwrite: false
            )

        XCTAssertEqual(atomicWriter.destinationURL, try localStoreURL(rootURL: roots.canonical))
    }

    func testAtomicSaveDoesNotResolveDestinationSymlink() throws {
        let roots = try symlinkedRoot()
        let storeURL = try localStoreURL(rootURL: roots.injected)
        try AtlasFileManagerVaultDirectoryPreparer().prepareParentDirectory(
            for: storeURL,
            under: roots.injected
        )
        let canonicalStoreURL = try localStoreURL(rootURL: roots.canonical)
        let outsideURL = try temporaryDirectory().appendingPathComponent("outside-store.json")
        let outsideData = Data("OUTSIDE_ATOMIC_COORDINATOR_SENTINEL".utf8)
        try outsideData.write(to: outsideURL)
        do {
            try FileManager.default.createSymbolicLink(
                at: canonicalStoreURL,
                withDestinationURL: outsideURL
            )
        } catch {
            throw XCTSkip("Symbolic links are unavailable in this environment")
        }

        XCTAssertThrowsError(try realCoordinator(rootURL: roots.injected)
            .saveEncryptedStoreAtomically(
                localStore(records: []),
                for: session(),
                overwrite: true
            )) { error in
                XCTAssertEqual(error as? AtlasVaultAtomicWriteError, .unsafePath)
            }
        XCTAssertEqual(try Data(contentsOf: outsideURL), outsideData)
    }

    func testSuccessfulAtomicSaveCanBeReloaded() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try realCoordinator(rootURL: rootURL)
        let store = localStore(records: [encryptedRecord(id: "record-new", revision: "revision-new")])

        let result = try coordinator.saveEncryptedStoreAtomically(
            store,
            for: session(),
            overwrite: false
        )

        XCTAssertEqual(result.commitState, .committed)
        XCTAssertEqual(try coordinator.loadEncryptedStore(for: session()), store)
    }

    func testAtomicRecordSavePreservesUntouchedRecordsAndTombstone() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try realCoordinator(rootURL: rootURL)
        let active = encryptedRecord(id: "record-delete", revision: "revision-live")
        let untouched = encryptedRecord(id: "record-untouched", revision: "revision-untouched", nonceByte: 7)
        _ = try coordinator.saveEncryptedStoreAtomically(
            localStore(records: [active, untouched]),
            for: session(),
            overwrite: false
        )
        let tombstone = encryptedRecord(
            id: "record-delete",
            revision: "revision-deleted",
            parentRevision: "revision-live",
            deleted: true,
            nonceByte: 8
        )

        _ = try coordinator.saveEncryptedRecordsAtomically(
            [tombstone],
            for: session(),
            overwrite: true,
            merger: deterministicMerger()
        )

        let loaded = try XCTUnwrap(coordinator.loadEncryptedStore(for: session()))
        XCTAssertEqual(loaded.records, [tombstone, untouched])
        XCTAssertTrue(try XCTUnwrap(loaded.records.first).deleted)
    }

    func testAtomicRecordSavePreservesExplicitOverwriteBehavior() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try realCoordinator(rootURL: rootURL)
        let original = localStore(records: [])
        _ = try coordinator.saveEncryptedStoreAtomically(original, for: session(), overwrite: false)
        let incoming = encryptedRecord(id: "record-new", revision: "revision-new", nonceByte: 9)

        XCTAssertThrowsError(try coordinator.saveEncryptedRecordsAtomically(
            [incoming],
            for: session(),
            overwrite: false,
            merger: deterministicMerger()
        )) { error in
            XCTAssertEqual(error as? AtlasVaultAtomicWriteError, .destinationExists)
        }
        XCTAssertEqual(try coordinator.loadEncryptedStore(for: session()), original)
    }

    func testAtomicSaveWritesNoPrivateSentinelsOrPlaintextRecordTypes() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try realCoordinator(rootURL: rootURL)
        _ = try coordinator.saveEncryptedStoreAtomically(
            localStore(records: []),
            for: session(),
            overwrite: false
        )
        _ = try coordinator.saveEncryptedRecordsAtomically(
            try savedSearchRecords(),
            for: session(),
            overwrite: true,
            merger: deterministicMerger()
        )

        let serialized = try XCTUnwrap(
            String(data: Data(contentsOf: localStoreURL(rootURL: rootURL)), encoding: .utf8)
        )
        for forbidden in Self.forbiddenPlaintext {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testAtomicSaveDoesNotMutatePublicSnapshot() throws {
        let rootURL = try temporaryDirectory()
        let coordinator = try realCoordinator(rootURL: rootURL)
        let snapshot = try publicSnapshot()
        let before = try encodedSnapshot(snapshot)

        _ = try coordinator.saveEncryptedStoreAtomically(
            localStore(records: []),
            for: session(),
            overwrite: false
        )

        XCTAssertEqual(try encodedSnapshot(snapshot), before)
    }

    func testAtomicSaveCreatesNoAtlasVaultArtifacts() throws {
        let rootURL = try temporaryDirectory()
        _ = try realCoordinator(rootURL: rootURL).saveEncryptedStoreAtomically(
            localStore(records: []),
            for: session(),
            overwrite: false
        )

        XCTAssertFalse(try allURLs(under: rootURL).contains { $0.pathExtension == "atlasvault" })
    }

    func testCoordinatorSourceAvoidsRuntimeKeyRetrievalDefaultsAndNetworking() throws {
        let source = try String(contentsOf: sourceFileURL(), encoding: .utf8)
        for forbidden in [
            "SwiftUI",
            "SearchViewModel",
            "AtlasLocalCache",
            "UserDefaults",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "URLSession",
            "ApplicationSupport",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private static let vaultID = "00000000-0000-4000-8000-000000000225"
    private static let keyID = "phase2d25-test-key"
    private static let storeID = "TEST_ONLY_ATOMIC_COORDINATOR_STORE"
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

    private func assertAtomicFailurePreservesDestination(
        _ atomicError: AtlasVaultAtomicWriteError,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        let rootURL = try temporaryDirectory()
        let original = localStore(records: [encryptedRecord(id: "record-original", revision: "revision-original")])
        try seedStore(original, rootURL: rootURL)
        let before = try Data(contentsOf: localStoreURL(rootURL: rootURL))
        let atomicWriter = RecordingAtomicStoreWriter(error: atomicError)

        XCTAssertThrowsError(try coordinator(rootURL: rootURL, atomicWriter: atomicWriter)
            .saveEncryptedRecordsAtomically(
                [encryptedRecord(id: "record-new", revision: "revision-new", nonceByte: 10)],
                for: session(),
                overwrite: true,
                merger: deterministicMerger()
            ), file: file, line: line) { error in
                XCTAssertEqual(error as? AtlasVaultAtomicWriteError, atomicError, file: file, line: line)
            }
        XCTAssertEqual(atomicWriter.callCount, 1, file: file, line: line)
        XCTAssertEqual(try Data(contentsOf: localStoreURL(rootURL: rootURL)), before, file: file, line: line)
        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: localStoreURL(rootURL: rootURL)), original, file: file, line: line)
    }

    private func coordinator(
        rootURL: URL,
        atomicWriter: RecordingAtomicStoreWriter
    ) throws -> AtlasVaultPersistenceCoordinator<
        AtlasInjectedRootVaultPathLocator,
        AtlasFileManagerVaultDirectoryPreparer,
        AtlasVaultLocalStoreFileIO
    > {
        AtlasVaultPersistenceCoordinator(
            environment: AtlasVaultPersistenceEnvironment(
                rootDirectory: rootURL,
                pathLocator: try AtlasInjectedRootVaultPathLocator(rootURL: rootURL),
                directoryPreparer: AtlasFileManagerVaultDirectoryPreparer(),
                atomicStoreWriter: atomicWriter
            )
        )
    }

    private func realCoordinator(
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

    private func seedStore(_ store: AtlasVaultLocalStoreEnvelope, rootURL: URL) throws {
        let url = try localStoreURL(rootURL: rootURL)
        try AtlasFileManagerVaultDirectoryPreparer().prepareParentDirectory(for: url, under: rootURL)
        try AtlasVaultLocalStoreIO.write(store, to: url, overwrite: false)
    }

    private func localStoreURL(rootURL: URL) throws -> URL {
        try AtlasInjectedRootVaultPathLocator(rootURL: rootURL).localStoreURL(vaultID: Self.vaultID)
    }

    private func session() throws -> AtlasVaultUnlockedSession {
        try AtlasVaultUnlockedSession(vaultID: Self.vaultID, vaultKey: Self.vaultKey)
    }

    private func deterministicMerger() -> AtlasVaultLocalStoreMerger {
        AtlasVaultLocalStoreMerger(updatedAtProvider: { Self.mergedAt })
    }

    private func symlinkedRoot() throws -> (injected: URL, canonical: URL) {
        let targetContainer = try temporaryDirectory()
        let canonicalRoot = targetContainer.appendingPathComponent("root", isDirectory: true)
        try FileManager.default.createDirectory(at: canonicalRoot, withIntermediateDirectories: true)
        let linkContainer = try temporaryDirectory()
        let linkURL = linkContainer.appendingPathComponent("root-link", isDirectory: true)
        do {
            try FileManager.default.createSymbolicLink(at: linkURL, withDestinationURL: targetContainer)
        } catch {
            throw XCTSkip("Symbolic links are unavailable in this environment")
        }
        return (
            injected: linkURL.appendingPathComponent("root", isDirectory: true),
            canonical: canonicalRoot
        )
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
        nonceByte: UInt8 = 1
    ) -> AtlasVaultEncryptedRecordEnvelope {
        AtlasVaultEncryptedRecordEnvelope(
            id: id,
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion,
            revision: revision,
            parentRevision: parentRevision,
            deleted: deleted,
            keyID: Self.keyID,
            nonce: Data(repeating: nonceByte, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString(),
            ciphertext: Data(repeating: nonceByte + 1, count: AtlasVaultRecordCrypto.gcmTagByteCount).base64EncodedString()
        )
    }

    private func savedSearchRecords() throws -> [AtlasVaultEncryptedRecordEnvelope] {
        let saver = AtlasVaultRecordSaver(
            recordIDGenerator: { "record-private-test" },
            revisionIDGenerator: { "revision-private-test" },
            nonceGenerator: { Data(repeating: 25, count: AtlasVaultRecordCrypto.nonceByteCount) }
        )
        return try saver.save(
            mutations: AtlasVaultMutationSet(creates: [
                AtlasVaultCreateMutation(
                    payload: .savedSearch(AtlasSavedSearchVaultRecordPayload(
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
                    )),
                    keyID: Self.keyID
                ),
            ]),
            session: session()
        )
    }

    private func temporaryDirectory() throws -> URL {
        let url = try AtlasVaultTestFileSystemSupport.canonicalTemporaryRoot()
            .appendingPathComponent("atlasvault-atomic-coordinator-tests-\(UUID().uuidString)", isDirectory: true)
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

    private func allURLs(under rootURL: URL) throws -> [URL] {
        guard let enumerator = FileManager.default.enumerator(
            at: rootURL,
            includingPropertiesForKeys: nil
        ) else {
            return []
        }
        return enumerator.compactMap { $0 as? URL }
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
        throw NSError(
            domain: "AtlasVaultPersistenceCoordinatorAtomicSaveTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Could not find coordinator source"]
        )
    }
}

private final class RecordingAtomicStoreWriter: AtlasVaultAtomicStoreWriting, @unchecked Sendable {
    private let result: AtlasVaultAtomicWriteResult
    private let error: AtlasVaultAtomicWriteError?

    private(set) var callCount = 0
    private(set) var writtenStore: AtlasVaultLocalStoreEnvelope?
    private(set) var destinationURL: URL?
    private(set) var overwrite: Bool?

    init(
        result: AtlasVaultAtomicWriteResult = AtlasVaultAtomicWriteResult(commitState: .committed),
        error: AtlasVaultAtomicWriteError? = nil
    ) {
        self.result = result
        self.error = error
    }

    func write(
        _ store: AtlasVaultLocalStoreEnvelope,
        to destinationURL: URL,
        overwrite: Bool
    ) throws -> AtlasVaultAtomicWriteResult {
        callCount += 1
        writtenStore = store
        self.destinationURL = destinationURL
        self.overwrite = overwrite
        if let error {
            throw error
        }
        return result
    }
}

private final class RecordingAtomicSaveLocalStoreIO: AtlasVaultLocalStoreProviding, @unchecked Sendable {
    private(set) var writeCallCount = 0

    func read(from url: URL) throws -> AtlasVaultLocalStoreEnvelope {
        throw AtlasVaultStoreError.readFailed
    }

    func write(_ store: AtlasVaultLocalStoreEnvelope, to url: URL, overwrite: Bool) throws {
        writeCallCount += 1
    }
}
