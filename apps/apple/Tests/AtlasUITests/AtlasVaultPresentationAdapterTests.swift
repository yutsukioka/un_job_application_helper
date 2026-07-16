import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultPresentationAdapterTests: XCTestCase {
    private static let fakeKey = Data(repeating: 0xA9, count: 32)
    private static let fakePath = "/tmp/FAKE_PRIVATE_VAULT_PATH_DO_NOT_LEAK"

    func testConstructionIsSideEffectFreeAndInitiallyProjectsLocked() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let before = try relativePaths(at: root)

        let adapter = AtlasVaultPresentationAdapter()
        let snapshot = adapter.makeSnapshot(runtimeStatus: .locked, privateState: nil)

        XCTAssertEqual(snapshot, AtlasVaultPresentationSnapshot(status: .locked, privateState: nil))
        XCTAssertTrue(Mirror(reflecting: adapter).children.isEmpty)
        XCTAssertEqual(try relativePaths(at: root), before)
    }

    func testNoVaultSnapshot() {
        let snapshot = makeSnapshot(failure: .storeMissing)

        XCTAssertEqual(snapshot.status, .noVault)
        XCTAssertNil(snapshot.privateState)
    }

    func testActivatingSnapshotClearsSuppliedPrivateState() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .activating,
            privateState: hydratedState()
        )

        XCTAssertEqual(snapshot.status, .activating)
        XCTAssertNil(snapshot.privateState)
    }

    func testUnlockedSnapshotProjectsPrivateState() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState()
        )

        XCTAssertEqual(snapshot.status, .unlocked)
        XCTAssertNotNil(snapshot.privateState)
    }

    func testKeyUnavailableFailuresUseOneRedactedStatus() {
        for failure in [
            AtlasVaultActivationFailure.keyUnavailable,
            .keyStoreFailure,
            .invalidVaultKey,
        ] {
            XCTAssertEqual(makeSnapshot(failure: failure).status, .keyUnavailable)
        }
    }

    func testCorruptVaultFailuresUseOneRedactedStatus() {
        for failure in [
            AtlasVaultActivationFailure.authenticationFailed,
            .corruptStore,
        ] {
            XCTAssertEqual(makeSnapshot(failure: failure).status, .corruptStore)
        }
    }

    func testUnsupportedVersionRemainsDistinct() {
        XCTAssertEqual(makeSnapshot(failure: .unsupportedVersion).status, .unsupportedVersion)
    }

    func testGenericActivationFailuresUseNonSensitiveFallback() {
        for failure in [
            AtlasVaultActivationFailure.invalidVaultID,
            .vaultUnavailable,
            .activationInProgress,
            .alreadyUnlocked,
        ] {
            XCTAssertEqual(makeSnapshot(failure: failure).status, .failed)
        }
    }

    func testCancelledSnapshotContainsNoPrivateStateWhileLocked() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .locked,
            privateState: hydratedState(),
            commandState: .cancelled
        )

        XCTAssertEqual(snapshot.status, .cancelled)
        XCTAssertNil(snapshot.privateState)
        XCTAssertEqual(makeSnapshot(failure: .cancelled).status, .cancelled)
    }

    func testSaveInProgressRetainsOnlySuppliedInMemoryProjection() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .saving,
            privateState: hydratedState()
        )

        XCTAssertEqual(snapshot.status, .saveInProgress)
        XCTAssertNotNil(snapshot.privateState)
    }

    func testSaveFailedRetainsCurrentUnlockedProjection() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState(),
            commandState: .saveFailed
        )

        XCTAssertEqual(snapshot.status, .saveFailed)
        XCTAssertNotNil(snapshot.privateState)
    }

    func testSaveFailedCannotOverrideLockedClearing() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .locked,
            privateState: hydratedState(),
            commandState: .saveFailed
        )

        XCTAssertEqual(snapshot.status, .locked)
        XCTAssertNil(snapshot.privateState)
    }

    func testFailedActivationClearsPriorProjection() {
        let adapter = adapter()
        let unlocked = adapter.makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState()
        )
        XCTAssertNotNil(unlocked.privateState)

        let failed = adapter.makeSnapshot(
            runtimeStatus: .failed(.activation(.authenticationFailed)),
            privateState: hydratedState()
        )

        XCTAssertNil(failed.privateState)
    }

    func testLockAndLockingClearPriorProjection() {
        let adapter = adapter()
        for status in [AtlasVaultRuntimeStatus.locking, .locked] {
            let snapshot = adapter.makeSnapshot(
                runtimeStatus: status,
                privateState: hydratedState()
            )
            XCTAssertNil(snapshot.privateState)
        }
    }

    func testSavedSearchProjection() throws {
        let value = try XCTUnwrap(projectedState().savedSearches.first)

        XCTAssertEqual(value.id, AtlasVaultPresentationID(recordID: "record-search"))
        XCTAssertEqual(value.name, "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK")
        XCTAssertEqual(value.summary, "FAKE_SEARCH_SUMMARY_DO_NOT_LEAK")
        XCTAssertEqual(value.details, "FAKE_SEARCH_DETAILS_DO_NOT_LEAK")
        XCTAssertEqual(value.request.text, "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK")
        XCTAssertEqual(value.request.sourceIDs, ["FAKE_PRIVATE_FILTER_DO_NOT_LEAK"])
        XCTAssertEqual(value.createdAt, "2026-01-01T00:00:00Z")
        XCTAssertEqual(value.updatedAt, "2026-01-02T00:00:00Z")
    }

    func testSavedJobProjection() throws {
        let value = try XCTUnwrap(projectedState().savedJobs.first)

        XCTAssertEqual(value.id, AtlasVaultPresentationID(recordID: "record-job"))
        XCTAssertEqual(value.applicationID, "FAKE_APPLICATION_ID_DO_NOT_LEAK")
        XCTAssertEqual(value.jobKey, "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK")
        XCTAssertEqual(value.status, "FAKE_APPLICATION_STATUS_DO_NOT_LEAK")
        XCTAssertEqual(value.notes, "FAKE_SAVED_JOB_NOTE_DO_NOT_LEAK")
        XCTAssertEqual(value.appliedAt, "2026-01-03T00:00:00Z")
        XCTAssertEqual(value.updatedAt, "2026-01-04T00:00:00Z")
    }

    func testApplicationNoteProjection() throws {
        let value = try XCTUnwrap(projectedState().applicationNotes.first)

        XCTAssertEqual(value.id, AtlasVaultPresentationID(recordID: "record-note"))
        XCTAssertEqual(value.title, "FAKE_NOTE_TITLE_DO_NOT_LEAK")
        XCTAssertEqual(value.body, "FAKE_PRIVATE_NOTE_DO_NOT_LEAK")
        XCTAssertEqual(value.noteKind, "FAKE_NOTE_KIND_DO_NOT_LEAK")
        XCTAssertEqual(value.linkedJobKey, "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK")
        XCTAssertEqual(
            value.linkedSavedJobID,
            AtlasVaultPresentationID(recordID: "record-job")
        )
        XCTAssertEqual(value.isPinned, true)
        XCTAssertEqual(value.sortOrder, 7)
    }

    func testProfileSnippetProjection() throws {
        let value = try XCTUnwrap(projectedState().profileSnippets.first)

        XCTAssertEqual(value.id, AtlasVaultPresentationID(recordID: "record-snippet"))
        XCTAssertEqual(value.title, "FAKE_SNIPPET_TITLE_DO_NOT_LEAK")
        XCTAssertEqual(value.body, "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK")
        XCTAssertEqual(value.targetSystem, "FAKE_TARGET_SYSTEM_DO_NOT_LEAK")
        XCTAssertEqual(value.fieldHint, "FAKE_FIELD_HINT_DO_NOT_LEAK")
        XCTAssertEqual(value.tags, ["FAKE_TAG_DO_NOT_LEAK"])
        XCTAssertEqual(value.provenanceNotes, "FAKE_PROVENANCE_DO_NOT_LEAK")
    }

    func testDraftMetadataProjection() throws {
        let value = try XCTUnwrap(projectedState().draftMetadata.first)

        XCTAssertEqual(value.id, AtlasVaultPresentationID(recordID: "record-draft"))
        XCTAssertEqual(value.linkedJobKey, "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK")
        XCTAssertEqual(
            value.linkedSavedJobID,
            AtlasVaultPresentationID(recordID: "record-job")
        )
        XCTAssertEqual(value.targetSystem, "FAKE_DRAFT_TARGET_DO_NOT_LEAK")
        XCTAssertEqual(value.documentType, "FAKE_DOCUMENT_TYPE_DO_NOT_LEAK")
        XCTAssertEqual(value.generatedDocumentReference, "FAKE_DOCUMENT_REFERENCE_DO_NOT_LEAK")
        XCTAssertEqual(value.draftStatus, "FAKE_DRAFT_STATUS_DO_NOT_LEAK")
        XCTAssertEqual(value.personalContextReference, "FAKE_CONTEXT_REFERENCE_DO_NOT_LEAK")
        XCTAssertEqual(value.contextSummary, "FAKE_CONTEXT_SUMMARY_DO_NOT_LEAK")
    }

    func testTombstonesAreNotProjected() {
        let state = hydratedState(includeTombstone: true)
        let projection = adapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: state
        ).privateState

        XCTAssertEqual(projection, projectedState())
    }

    func testOpaquePresentationIDsDoNotRetainRawRecordIDStrings() {
        let projection = projectedState()
        let identifiers = [
            tryUnwrap(projection.savedSearches.first).id,
            tryUnwrap(projection.savedJobs.first).id,
            tryUnwrap(projection.applicationNotes.first).id,
            tryUnwrap(projection.applicationNotes.first?.linkedSavedJobID),
            tryUnwrap(projection.profileSnippets.first).id,
            tryUnwrap(projection.draftMetadata.first).id,
            tryUnwrap(projection.draftMetadata.first?.linkedSavedJobID),
        ]

        for identifier in identifiers {
            XCTAssertFalse(
                Mirror(reflecting: identifier).children.contains { $0.value is String }
            )
            XCTAssertFalse(String(reflecting: identifier).contains("record-"))
        }
    }

    func testPublicAndPrivateDescriptionsAreRedacted() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState()
        )
        let values: [Any] = [
            snapshot,
            tryUnwrap(snapshot.privateState),
            tryUnwrap(snapshot.privateState?.savedSearches.first),
            tryUnwrap(snapshot.privateState?.savedJobs.first),
            tryUnwrap(snapshot.privateState?.applicationNotes.first),
            tryUnwrap(snapshot.privateState?.profileSnippets.first),
            tryUnwrap(snapshot.privateState?.draftMetadata.first),
        ]

        for value in values {
            assertDescriptionIsRedacted(value)
        }
        for status in allPresentationStatuses() {
            assertContainsNoPrivateValue("\(status) \(String(reflecting: status))")
        }
    }

    func testPresentationTypesDoNotConformToEncodingProtocols() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState()
        )

        XCTAssertFalse(isEncodable(snapshot))
        XCTAssertFalse(isDecodable(AtlasVaultPresentationSnapshot.self))
        XCTAssertFalse(isEncodable(tryUnwrap(snapshot.privateState)))
        XCTAssertFalse(isEncodable(tryUnwrap(snapshot.privateState?.savedSearches.first).request))
    }

    func testPresentationContainsNoVaultKeyEnvelopeOrFilesystemPath() {
        let snapshot = adapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState()
        )
        let rendered = "\(snapshot) \(String(reflecting: snapshot))"
        let keyBase64 = Self.fakeKey.base64EncodedString()
        let keyHex = Self.fakeKey.map { String(format: "%02x", $0) }.joined()

        XCTAssertFalse(rendered.contains(keyBase64))
        XCTAssertFalse(rendered.contains(keyHex))
        XCTAssertFalse(rendered.contains(Self.fakePath))
    }

    func testProjectionDoesNotMutatePublicSnapshot() throws {
        let publicSnapshot = try makePublicSnapshot()
        let before = try encodedPublicSnapshot(publicSnapshot)

        _ = adapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState()
        )

        XCTAssertEqual(try encodedPublicSnapshot(publicSnapshot), before)
    }

    func testSourceAvoidsUIRuntimePersistenceKeychainCryptoAndNetworkCoupling() throws {
        let source = try String(contentsOf: sourceFileURL(), encoding: .utf8)
        for forbidden in [
            "SwiftUI",
            "ObservableObject",
            "@Published",
            "@State",
            "@Environment",
            "@AppStorage",
            "@SceneStorage",
            "UserDefaults",
            "FileManager",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "URLSession",
            "Data.write",
            "createFile",
            "Codable",
            "SearchViewModel",
            "AtlasLocalCache",
            "AtlasVaultRecordCrypto",
            "AtlasVaultEncryptedRecordEnvelope",
            "AtlasVaultUnlockedSession",
            "vaultKey",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private func adapter() -> AtlasVaultPresentationAdapter {
        AtlasVaultPresentationAdapter()
    }

    private func makeSnapshot(
        failure: AtlasVaultActivationFailure
    ) -> AtlasVaultPresentationSnapshot {
        adapter().makeSnapshot(
            runtimeStatus: .failed(.activation(failure)),
            privateState: hydratedState()
        )
    }

    private func projectedState() -> AtlasVaultPrivatePresentationState {
        adapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState()
        ).privateState!
    }

    private func hydratedState(includeTombstone: Bool = false) -> AtlasVaultHydratedState {
        AtlasVaultHydratedState(
            savedSearches: [AtlasHydratedSavedSearch(
                metadata: metadata(id: "record-search"),
                payload: AtlasSavedSearchVaultPayload(
                    name: "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
                    summary: "FAKE_SEARCH_SUMMARY_DO_NOT_LEAK",
                    description: "FAKE_SEARCH_DETAILS_DO_NOT_LEAK",
                    request: AtlasSearchRequest(
                        text: "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
                        sourceIDs: ["FAKE_PRIVATE_FILTER_DO_NOT_LEAK"]
                    ),
                    createdAt: "2026-01-01T00:00:00Z",
                    updatedAt: "2026-01-02T00:00:00Z"
                ),
                clientCreatedAt: "2026-01-01T00:00:00Z",
                clientUpdatedAt: "2026-01-02T00:00:00Z"
            )],
            savedJobs: [AtlasHydratedSavedJob(
                metadata: metadata(id: "record-job"),
                payload: AtlasSavedJobVaultPayload(
                    id: "FAKE_APPLICATION_ID_DO_NOT_LEAK",
                    jobKey: "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
                    status: "FAKE_APPLICATION_STATUS_DO_NOT_LEAK",
                    notes: "FAKE_SAVED_JOB_NOTE_DO_NOT_LEAK",
                    appliedAt: "2026-01-03T00:00:00Z",
                    updatedAt: "2026-01-04T00:00:00Z"
                ),
                clientCreatedAt: "2026-01-03T00:00:00Z",
                clientUpdatedAt: "2026-01-04T00:00:00Z"
            )],
            applicationNotes: [AtlasHydratedApplicationNote(
                metadata: metadata(id: "record-note"),
                payload: AtlasApplicationNoteVaultPayload(
                    title: "FAKE_NOTE_TITLE_DO_NOT_LEAK",
                    body: "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
                    noteKind: "FAKE_NOTE_KIND_DO_NOT_LEAK",
                    linkedJobKey: "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
                    linkedSavedJobRecordID: "record-job",
                    createdAt: "2026-01-05T00:00:00Z",
                    updatedAt: "2026-01-06T00:00:00Z",
                    isPinned: true,
                    sortOrder: 7
                ),
                clientCreatedAt: "2026-01-05T00:00:00Z",
                clientUpdatedAt: "2026-01-06T00:00:00Z"
            )],
            profileSnippets: [AtlasHydratedProfileSnippet(
                metadata: metadata(id: "record-snippet"),
                payload: AtlasProfileSnippetVaultPayload(
                    title: "FAKE_SNIPPET_TITLE_DO_NOT_LEAK",
                    body: "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
                    targetSystem: "FAKE_TARGET_SYSTEM_DO_NOT_LEAK",
                    fieldHint: "FAKE_FIELD_HINT_DO_NOT_LEAK",
                    tags: ["FAKE_TAG_DO_NOT_LEAK"],
                    provenanceNotes: "FAKE_PROVENANCE_DO_NOT_LEAK",
                    createdAt: "2026-01-07T00:00:00Z",
                    updatedAt: "2026-01-08T00:00:00Z"
                ),
                clientCreatedAt: "2026-01-07T00:00:00Z",
                clientUpdatedAt: "2026-01-08T00:00:00Z"
            )],
            draftMetadata: [AtlasHydratedDraftMetadata(
                metadata: metadata(id: "record-draft"),
                payload: AtlasDraftMetadataVaultPayload(
                    linkedJobKey: "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
                    linkedSavedJobRecordID: "record-job",
                    targetSystem: "FAKE_DRAFT_TARGET_DO_NOT_LEAK",
                    documentType: "FAKE_DOCUMENT_TYPE_DO_NOT_LEAK",
                    generatedDocumentReference: "FAKE_DOCUMENT_REFERENCE_DO_NOT_LEAK",
                    draftStatus: "FAKE_DRAFT_STATUS_DO_NOT_LEAK",
                    generatedAt: "2026-01-09T00:00:00Z",
                    reviewedAt: "2026-01-10T00:00:00Z",
                    submittedAt: nil,
                    archivedAt: nil,
                    personalContextReference: "FAKE_CONTEXT_REFERENCE_DO_NOT_LEAK",
                    contextSummary: "FAKE_CONTEXT_SUMMARY_DO_NOT_LEAK"
                ),
                clientCreatedAt: "2026-01-09T00:00:00Z",
                clientUpdatedAt: "2026-01-10T00:00:00Z"
            )],
            tombstones: includeTombstone
                ? [AtlasHydratedTombstone(metadata: metadata(id: "record-deleted", deleted: true))]
                : []
        )
    }

    private func metadata(
        id: String,
        deleted: Bool = false
    ) -> AtlasHydratedRecordMetadata {
        AtlasHydratedRecordMetadata(
            id: id,
            revision: "FAKE_REVISION_DO_NOT_LEAK",
            parentRevision: "FAKE_PARENT_REVISION_DO_NOT_LEAK",
            deleted: deleted,
            keyID: "FAKE_KEY_ID_DO_NOT_LEAK"
        )
    }

    private func allPresentationStatuses() -> [AtlasVaultPresentationStatus] {
        [
            .locked, .noVault, .activating, .locking, .unlocked,
            .keyUnavailable, .corruptStore, .unsupportedVersion,
            .saveInProgress, .saveFailed, .cancelled, .failed,
        ]
    }

    private func assertDescriptionIsRedacted(
        _ value: Any,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        assertContainsNoPrivateValue(
            "\(String(describing: value)) \(String(reflecting: value))",
            file: file,
            line: line
        )
    }

    private func assertContainsNoPrivateValue(
        _ rendered: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        for forbidden in [
            "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
            "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
            "FAKE_PRIVATE_FILTER_DO_NOT_LEAK",
            "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
            "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
            "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
            "FAKE_DOCUMENT_REFERENCE_DO_NOT_LEAK",
            "FAKE_KEY_ID_DO_NOT_LEAK",
            "FAKE_REVISION_DO_NOT_LEAK",
            Self.fakePath,
        ] {
            XCTAssertFalse(rendered.contains(forbidden), forbidden, file: file, line: line)
        }
    }

    private func tryUnwrap<T>(_ value: T?) -> T {
        guard let value else {
            XCTFail("Expected test value")
            fatalError("Missing test value")
        }
        return value
    }

    private func isEncodable(_ value: Any) -> Bool {
        value is any Encodable
    }

    private func isDecodable(_ type: Any.Type) -> Bool {
        type is any Decodable.Type
    }

    private func makePublicSnapshot() throws -> AtlasPublicLocalSnapshot {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(AtlasPublicLocalSnapshot.self, from: Data("""
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

    private func encodedPublicSnapshot(
        _ snapshot: AtlasPublicLocalSnapshot
    ) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(snapshot)
    }

    private func temporaryRoot() throws -> URL {
        let root = try AtlasVaultTestFileSystemSupport.canonicalTemporaryRoot()
            .appendingPathComponent("atlas-presentation-adapter-tests")
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        return root
    }

    private func relativePaths(at root: URL) throws -> [String] {
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil
        ) else {
            return []
        }
        return enumerator.compactMap { value in
            guard let url = value as? URL else { return nil }
            return String(url.path.dropFirst(root.path.count + 1))
        }.sorted()
    }

    private func sourceFileURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI/AtlasVaultPresentationAdapter.swift")
    }
}
