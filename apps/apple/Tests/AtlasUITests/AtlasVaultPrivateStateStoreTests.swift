import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultPrivateStateStoreTests: XCTestCase {
    func testConstructionIsEmptyAndSideEffectFree() async {
        let store = AtlasVaultPrivateStateStore()

        let isEmpty = await store.isEmpty
        XCTAssertTrue(isEmpty)
    }

    func testStagedStateIsUnavailableUntilMatchingCommit() async throws {
        let store = AtlasVaultPrivateStateStore()
        let generation = AtlasVaultPrivateStateGeneration()
        let expected = privateState()

        try await store.stage(expected, generation: generation)

        await assertSnapshotFailure(store, generation: generation, expected: .unavailable)
        try await store.commit(generation: generation)
        let snapshot = try await store.snapshot(generation: generation)
        XCTAssertEqual(snapshot, expected)
    }

    func testCommitWithoutStagedStateFailsSafely() async {
        let store = AtlasVaultPrivateStateStore()

        await assertCommitFailure(
            store,
            generation: AtlasVaultPrivateStateGeneration(),
            expected: .noStagedState
        )
    }

    func testDuplicateStageForSameGenerationFailsWithoutReplacingState() async throws {
        let store = AtlasVaultPrivateStateStore()
        let generation = AtlasVaultPrivateStateGeneration()
        let expected = privateState(marker: "ORIGINAL")

        try await store.stage(expected, generation: generation)
        do {
            try await store.stage(privateState(marker: "REPLACEMENT"), generation: generation)
            XCTFail("Expected duplicate stage to fail")
        } catch let error as AtlasVaultPrivateStateStoreError {
            XCTAssertEqual(error, .invalidGeneration)
        } catch {
            XCTFail("Unexpected error type: \(type(of: error))")
        }

        try await store.commit(generation: generation)
        let snapshot = try await store.snapshot(generation: generation)
        XCTAssertEqual(snapshot, expected)
    }

    func testStaleGenerationCannotCommitOrClearNewerActiveState() async throws {
        let store = AtlasVaultPrivateStateStore()
        let staleGeneration = AtlasVaultPrivateStateGeneration()
        let currentGeneration = AtlasVaultPrivateStateGeneration()
        let expected = privateState(marker: "CURRENT")

        try await store.stage(expected, generation: currentGeneration)
        await assertCommitFailure(
            store,
            generation: staleGeneration,
            expected: .staleGeneration
        )
        try await store.commit(generation: currentGeneration)

        await store.clear(generation: staleGeneration)

        let snapshot = try await store.snapshot(generation: currentGeneration)
        XCTAssertEqual(snapshot, expected)
    }

    func testGenerationScopedClearRemovesStagedAndActiveState() async throws {
        let store = AtlasVaultPrivateStateStore()
        let stagedGeneration = AtlasVaultPrivateStateGeneration()
        try await store.stage(privateState(marker: "STAGED"), generation: stagedGeneration)

        await store.clear(generation: stagedGeneration)

        let isEmptyAfterStagedClear = await store.isEmpty
        XCTAssertTrue(isEmptyAfterStagedClear)
        await assertCommitFailure(
            store,
            generation: stagedGeneration,
            expected: .noStagedState
        )

        let activeGeneration = AtlasVaultPrivateStateGeneration()
        try await store.stage(privateState(marker: "ACTIVE"), generation: activeGeneration)
        try await store.commit(generation: activeGeneration)
        await store.clear(generation: activeGeneration)

        let isEmptyAfterActiveClear = await store.isEmpty
        XCTAssertTrue(isEmptyAfterActiveClear)
        await assertSnapshotFailure(store, generation: activeGeneration, expected: .unavailable)
    }

    func testClearAllIsIdempotentAndDropsEveryPrivateCollection() async throws {
        let store = AtlasVaultPrivateStateStore()
        let generation = AtlasVaultPrivateStateGeneration()
        try await store.stage(privateState(), generation: generation)
        try await store.commit(generation: generation)

        await store.clearAll()
        await store.clearAll()

        let isEmpty = await store.isEmpty
        XCTAssertTrue(isEmpty)
        await assertSnapshotFailure(store, generation: generation, expected: .unavailable)
    }

    func testConcurrentSnapshotsAndClearPreserveStateMachineInvariant() async throws {
        let store = AtlasVaultPrivateStateStore()
        let generation = AtlasVaultPrivateStateGeneration()
        let expected = privateState()
        try await store.stage(expected, generation: generation)
        try await store.commit(generation: generation)

        let readers = (0..<24).map { _ in
            Task {
                try? await store.snapshot(generation: generation)
            }
        }
        await store.clear(generation: generation)

        for reader in readers {
            if let snapshot = await reader.value {
                XCTAssertEqual(snapshot, expected)
            }
        }
        let isEmpty = await store.isEmpty
        XCTAssertTrue(isEmpty)
    }

    func testDescriptionsAndErrorsContainNoPrivateValues() {
        let store = AtlasVaultPrivateStateStore()
        let generation = AtlasVaultPrivateStateGeneration()
        let descriptions = [
            String(describing: store),
            String(reflecting: store),
            String(describing: generation),
            String(reflecting: generation),
            String(describing: AtlasVaultPrivateStateStoreError.invalidGeneration),
            String(reflecting: AtlasVaultPrivateStateStoreError.staleGeneration),
        ]

        for description in descriptions {
            for privateValue in privateSentinels {
                XCTAssertFalse(description.contains(privateValue))
            }
        }
    }

    func testSourceHasNoRuntimePersistenceUIOrLoggingDependency() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)
        for forbidden in [
            "SwiftUI",
            "SearchViewModel",
            "AtlasLocalCache",
            "AtlasPublicLocalSnapshot",
            "URLSession",
            "Keychain",
            "SecItem",
            "FileManager",
            "Data.write",
            "createFile",
            "print(",
            "Logger",
            "os_log",
        ] {
            XCTAssertFalse(source.contains(forbidden), "Unexpected source reference: \(forbidden)")
        }
    }

    private func assertCommitFailure(
        _ store: AtlasVaultPrivateStateStore,
        generation: AtlasVaultPrivateStateGeneration,
        expected: AtlasVaultPrivateStateStoreError,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        do {
            try await store.commit(generation: generation)
            XCTFail("Expected commit failure", file: file, line: line)
        } catch let error as AtlasVaultPrivateStateStoreError {
            XCTAssertEqual(error, expected, file: file, line: line)
        } catch {
            XCTFail("Unexpected error type: \(type(of: error))", file: file, line: line)
        }
    }

    private func assertSnapshotFailure(
        _ store: AtlasVaultPrivateStateStore,
        generation: AtlasVaultPrivateStateGeneration,
        expected: AtlasVaultPrivateStateStoreError,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        do {
            _ = try await store.snapshot(generation: generation)
            XCTFail("Expected snapshot failure", file: file, line: line)
        } catch let error as AtlasVaultPrivateStateStoreError {
            XCTAssertEqual(error, expected, file: file, line: line)
        } catch {
            XCTFail("Unexpected error type: \(type(of: error))", file: file, line: line)
        }
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent(
                "../../Sources/AtlasUI/AtlasVaultPrivateStateStore.swift"
            ),
            sourceDirectory.appendingPathComponent(
                "../../../../apps/apple/Sources/AtlasUI/AtlasVaultPrivateStateStore.swift"
            ),
        ].map(\.standardizedFileURL)
        guard let sourceURL = candidates.first(where: {
            FileManager.default.fileExists(atPath: $0.path)
        }) else {
            throw NSError(
                domain: "AtlasVaultPrivateStateStoreTests",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Could not find private-state store source"]
            )
        }
        return sourceURL
    }
}

private let privateSentinels = [
    "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
    "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
    "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
    "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
    "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK",
]

private func privateState(marker: String = "STATE") -> AtlasVaultHydratedState {
    let timestamp = "2026-01-01T00:00:00Z"
    func metadata(_ suffix: String, deleted: Bool = false) -> AtlasHydratedRecordMetadata {
        AtlasHydratedRecordMetadata(
            id: "fake-record-\(marker)-\(suffix)",
            revision: "fake-revision-\(marker)-\(suffix)",
            parentRevision: nil,
            deleted: deleted,
            keyID: "fake-key-id"
        )
    }

    return AtlasVaultHydratedState(
        savedSearches: [AtlasHydratedSavedSearch(
            metadata: metadata("search"),
            payload: AtlasSavedSearchVaultPayload(
                name: "FAKE_SAVED_SEARCH_\(marker)",
                summary: "fake summary",
                request: AtlasSearchRequest(text: "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK")
            ),
            clientCreatedAt: timestamp,
            clientUpdatedAt: timestamp
        )],
        savedJobs: [AtlasHydratedSavedJob(
            metadata: metadata("job"),
            payload: AtlasSavedJobVaultPayload(
                jobKey: "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
                status: "fake-status",
                notes: "FAKE_PRIVATE_NOTE_DO_NOT_LEAK"
            ),
            clientCreatedAt: timestamp,
            clientUpdatedAt: timestamp
        )],
        applicationNotes: [AtlasHydratedApplicationNote(
            metadata: metadata("note"),
            payload: AtlasApplicationNoteVaultPayload(
                body: "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
                noteKind: "fake-kind",
                createdAt: timestamp,
                updatedAt: timestamp
            ),
            clientCreatedAt: timestamp,
            clientUpdatedAt: timestamp
        )],
        profileSnippets: [AtlasHydratedProfileSnippet(
            metadata: metadata("snippet"),
            payload: AtlasProfileSnippetVaultPayload(
                title: "fake title",
                body: "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
                createdAt: timestamp,
                updatedAt: timestamp
            ),
            clientCreatedAt: timestamp,
            clientUpdatedAt: timestamp
        )],
        draftMetadata: [AtlasHydratedDraftMetadata(
            metadata: metadata("draft"),
            payload: AtlasDraftMetadataVaultPayload(
                targetSystem: "fake-system",
                documentType: "fake-document",
                generatedDocumentReference: "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK",
                draftStatus: "fake-draft",
                generatedAt: timestamp
            ),
            clientCreatedAt: timestamp,
            clientUpdatedAt: timestamp
        )],
        tombstones: [AtlasHydratedTombstone(metadata: metadata("deleted", deleted: true))]
    )
}
