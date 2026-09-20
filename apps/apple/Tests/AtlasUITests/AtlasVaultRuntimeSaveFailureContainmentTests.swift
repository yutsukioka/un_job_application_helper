import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRuntimeSaveFailureContainmentTests: XCTestCase {
    private static let vaultID = "vault-save-containment-001"
    private static let privateSentinel = "FAKE_PRIVATE_SAVE_CONTAINMENT_SENTINEL"
    private static let fakeKey = Data(
        repeating: 0xC7,
        count: AtlasVaultRecordCrypto.vaultKeyByteCount
    )

    func testRecoverablePreCommitFailureLeavesStateUnchangedAndUnlocked() async throws {
        let initial = privateState("initial")
        let harness = SaveContainmentHarness(initialState: initial)
        await harness.setSaveMode(.recoverablePreCommit)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        do {
            _ = try await facade.apply(mutationRequest())
            XCTFail("Expected recoverable save failure")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .saveFailed)
        }

        let status = await facade.status()
        let state = try await facade.privateState().state
        let events = await harness.events()
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(state, initial)
        XCTAssertFalse(events.contains("lock"))
    }

    func testCommittedDurabilityWarningUpdatesStateAndPresentation() async throws {
        let initial = privateState("initial")
        let updated = privateState("committed")
        let harness = SaveContainmentHarness(
            initialState: initial,
            postSaveState: updated
        )
        await harness.setSaveMode(.committedDurabilityUnconfirmed)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        let outcome = try await facade.apply(mutationRequest())
        let installed = try await facade.privateState().state
        let presentation = AtlasVaultPresentationAdapter().makeSnapshot(
            runtimeStatus: await facade.status(),
            privateState: installed,
            generation: AtlasVaultPresentationGeneration(),
            commandState: .saveDurabilityUnconfirmed
        )

        XCTAssertEqual(outcome, .committedDurabilityUnconfirmed)
        XCTAssertEqual(installed, updated)
        XCTAssertEqual(presentation.status, .saveDurabilityUnconfirmed)
        XCTAssertNotNil(presentation.privateState)
        let events = await harness.events()
        XCTAssertFalse(events.contains("lock"))
    }

    func testTypedIntegrityUnknownFailureFailsClosed() async throws {
        let harness = SaveContainmentHarness(initialState: privateState("initial"))
        await harness.setSaveMode(.integrityUnknown)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        do {
            _ = try await facade.apply(mutationRequest())
            XCTFail("Expected integrity-unknown failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .saveIntegrityUnknown
            )
        }

        await assertFailedClosed(facade, harness: harness)
    }

    func testUnclassifiedFailureFailsClosed() async throws {
        let harness = SaveContainmentHarness(initialState: privateState("initial"))
        await harness.setSaveMode(.unclassified)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        do {
            _ = try await facade.apply(mutationRequest())
            XCTFail("Expected unclassified failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .saveIntegrityUnknown
            )
        }

        await assertFailedClosed(facade, harness: harness)
    }

    func testFailClosedTransitionHidesPrivateStateWhileCleanupRuns() async throws {
        let gate = SaveContainmentGate()
        let harness = SaveContainmentHarness(
            initialState: privateState("initial"),
            lockGate: gate
        )
        await harness.setSaveMode(.integrityUnknown)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        let request = mutationRequest()

        let save = Task { try await facade.apply(request) }
        let didEnter = await gate.waitUntilEntered()
        let cleaningStatus = await facade.status()
        XCTAssertTrue(didEnter)
        XCTAssertEqual(cleaningStatus, .locking)
        do {
            _ = try await facade.privateState()
            XCTFail("Expected private state to be unavailable during fail-closed cleanup")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .privateStateUnavailable
            )
        }

        await gate.open()
        do {
            _ = try await save.value
            XCTFail("Expected integrity-unknown failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .saveIntegrityUnknown
            )
        }
        let finalStatus = await facade.status()
        XCTAssertEqual(finalStatus, .locked)
    }

    func testRepeatedLockCannotPermitReactivationBeforeFatalCleanupCompletes() async throws {
        let gate = SaveContainmentGate()
        let harness = SaveContainmentHarness(
            initialState: privateState("initial"),
            lockGate: gate
        )
        await harness.setSaveMode(.integrityUnknown)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        let mutation = mutationRequest()
        let activation = activationRequest()

        let save = Task { try await facade.apply(mutation) }
        let didEnterCleanup = await gate.waitUntilEntered()
        XCTAssertTrue(didEnterCleanup)
        let repeatedLock = Task { await facade.lock() }
        for _ in 0..<10 {
            await Task.yield()
        }
        let lockCount = await harness.lockCount()
        XCTAssertEqual(lockCount, 1)

        do {
            try await facade.activate(activation)
            XCTFail("Expected reactivation to remain blocked during cleanup")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .operationInProgress
            )
        }
        let cleaningStatus = await facade.status()
        XCTAssertEqual(cleaningStatus, .locking)

        await gate.open()
        await repeatedLock.value
        _ = try? await save.value
        let finalStatus = await facade.status()
        XCTAssertEqual(finalStatus, .locked)
    }

    func testReactivationAfterFatalFailureUsesFreshState() async throws {
        let harness = SaveContainmentHarness(initialState: privateState("initial"))
        await harness.setSaveMode(.integrityUnknown)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        _ = try? await facade.apply(mutationRequest())

        let reactivated = privateState("reactivated")
        await harness.setInstalledState(reactivated)
        await harness.setSaveMode(.committed)
        try await facade.activate(activationRequest())

        let status = await facade.status()
        let state = try await facade.privateState().state
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(state, reactivated)
    }

    func testRepeatedLockAfterFatalFailureIsIdempotent() async throws {
        let harness = SaveContainmentHarness(initialState: privateState("initial"))
        await harness.setSaveMode(.integrityUnknown)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        _ = try? await facade.apply(mutationRequest())

        await facade.lock()
        await facade.lock()

        let status = await facade.status()
        XCTAssertEqual(status, .locked)
        do {
            _ = try await facade.privateState()
            XCTFail("Expected private state to remain unavailable")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .privateStateUnavailable
            )
        }
    }

    func testFatalFailureDoesNotMutatePublicSnapshot() async throws {
        let snapshot = try publicSnapshot()
        let before = try encodedPublicSnapshot(snapshot)
        let harness = SaveContainmentHarness(initialState: privateState("initial"))
        await harness.setSaveMode(.integrityUnknown)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        _ = try? await facade.apply(mutationRequest())

        XCTAssertEqual(try encodedPublicSnapshot(snapshot), before)
    }

    func testFatalPresentationAndDiagnosticsAreNonSensitive() async throws {
        let state = privateState(Self.privateSentinel)
        let snapshot = AtlasVaultPresentationAdapter().makeSnapshot(
            runtimeStatus: .locked,
            privateState: state,
            generation: AtlasVaultPresentationGeneration(),
            commandState: .saveFailed
        )
        let rendered = [
            String(describing: AtlasVaultRuntimeFacadeError.saveIntegrityUnknown),
            String(reflecting: AtlasVaultRuntimeFacadeError.saveIntegrityUnknown),
            String(describing: AtlasVaultRuntimeSaveFailure.integrityUnknown),
            String(reflecting: AtlasVaultRuntimeSaveFailure.integrityUnknown),
            String(describing: snapshot),
            String(reflecting: snapshot),
        ].joined(separator: " ")
        let keyBase64 = Self.fakeKey.base64EncodedString()
        let keyHex = Self.fakeKey.map { String(format: "%02x", $0) }.joined()

        XCTAssertEqual(snapshot.status, .locked)
        XCTAssertNil(snapshot.privateState)
        for forbidden in [
            Self.privateSentinel,
            Self.vaultID,
            keyBase64,
            keyHex,
            "/tmp/fake-private-save-path",
        ] {
            XCTAssertFalse(rendered.contains(forbidden), forbidden)
        }
    }

    func testSourcesContainNoRuntimeUIKeychainFilesystemOrNetworkCoupling() throws {
        let sources = try [
            source(named: "AtlasVaultRuntimeFacade.swift"),
            source(named: "AtlasVaultPresentationAdapter.swift"),
        ].map { try String(contentsOf: $0, encoding: .utf8) }
        for source in sources {
            for forbidden in [
                "SwiftUI",
                "SearchViewModel",
                "AtlasLocalCache",
                "UserDefaults",
                "SecItem",
                "LAContext",
                "LocalAuthentication",
                "URLSession",
                "@main",
                "FileManager.default",
                "Data.write",
                "createFile",
            ] {
                XCTAssertFalse(
                    source.contains(forbidden),
                    "Unexpected source reference: \(forbidden)"
                )
            }
        }
    }

    private func assertFailedClosed(
        _ facade: AtlasVaultRuntimeFacade,
        harness: SaveContainmentHarness,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let status = await facade.status()
        let events = await harness.events()
        XCTAssertEqual(status, .locked, file: file, line: line)
        XCTAssertTrue(
            events.contains("lock"),
            file: file,
            line: line
        )
        do {
            _ = try await facade.privateState()
            XCTFail("Expected private state to be unavailable", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .privateStateUnavailable,
                file: file,
                line: line
            )
        }
        do {
            _ = try await facade.apply(mutationRequest())
            XCTFail("Expected subsequent save to require reactivation", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .locked,
                file: file,
                line: line
            )
        }
    }

    private func activationRequest() -> AtlasVaultRuntimeActivationRequest {
        AtlasVaultRuntimeActivationRequest(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.fakeKey
        )
    }

    private func mutationRequest() -> AtlasVaultRuntimeMutationRequest {
        AtlasVaultRuntimeMutationRequest(
            expectedVaultID: Self.vaultID,
            mutations: AtlasVaultMutationSet()
        )
    }

    private func privateState(_ suffix: String) -> AtlasVaultHydratedState {
        AtlasVaultHydratedState(tombstones: [AtlasHydratedTombstone(
            metadata: AtlasHydratedRecordMetadata(
                id: "\(Self.privateSentinel)-\(suffix)",
                revision: "FAKE_REVISION",
                parentRevision: nil,
                deleted: true,
                keyID: "FAKE_KEY_ID"
            )
        )])
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

    private func encodedPublicSnapshot(
        _ snapshot: AtlasPublicLocalSnapshot
    ) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(snapshot)
    }

    private func source(named name: String) throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/\(name)"),
            sourceDirectory.appendingPathComponent(
                "../../../../apps/apple/Sources/AtlasUI/\(name)"
            ),
        ].map(\.standardizedFileURL)
        guard let source = candidates.first(where: {
            FileManager.default.fileExists(atPath: $0.path)
        }) else {
            throw NSError(
                domain: "AtlasVaultRuntimeSaveFailureContainmentTests",
                code: 1
            )
        }
        return source
    }
}

private enum SaveContainmentMode: Sendable {
    case committed
    case committedDurabilityUnconfirmed
    case recoverablePreCommit
    case integrityUnknown
    case unclassified
}

private enum SaveContainmentUnclassifiedError: Error {
    case failed
}

private actor SaveContainmentHarness {
    private var active = false
    private var installedState: AtlasVaultHydratedState
    private var postSaveState: AtlasVaultHydratedState
    private var saveMode: SaveContainmentMode = .committed
    private var recordedEvents: [String] = []
    private var lockGate: SaveContainmentGate?

    init(
        initialState: AtlasVaultHydratedState,
        postSaveState: AtlasVaultHydratedState? = nil,
        lockGate: SaveContainmentGate? = nil
    ) {
        self.installedState = initialState
        self.postSaveState = postSaveState ?? initialState
        self.lockGate = lockGate
    }

    nonisolated func environment() -> AtlasVaultRuntimeFacadeEnvironment {
        AtlasVaultRuntimeFacadeEnvironment(
            activate: { [self] _, _ in await activate() },
            cancelActivation: { false },
            lock: { [self] in await lock() },
            privateState: { [self] in try await privateState() },
            save: { [self] _, _ in try await save() }
        )
    }

    func setSaveMode(_ mode: SaveContainmentMode) {
        saveMode = mode
    }

    func setInstalledState(_ state: AtlasVaultHydratedState) {
        installedState = state
        postSaveState = state
    }

    func events() -> [String] {
        recordedEvents
    }

    func lockCount() -> Int {
        recordedEvents.filter({ $0 == "lock" }).count
    }

    private func activate() {
        recordedEvents.append("activate")
        active = true
    }

    private func lock() async {
        recordedEvents.append("lock")
        active = false
        installedState = AtlasVaultHydratedState()
        if let lockGate {
            self.lockGate = nil
            await lockGate.enter()
        }
    }

    private func privateState() throws -> AtlasVaultHydratedState {
        recordedEvents.append("privateState")
        guard active else {
            throw AtlasVaultPrivateStateStoreError.unavailable
        }
        return installedState
    }

    private func save() throws -> AtlasVaultAtomicWriteResult {
        recordedEvents.append("save")
        guard active else {
            throw AtlasVaultActivatedOperationError.locked
        }
        switch saveMode {
        case .committed:
            installedState = postSaveState
            return AtlasVaultAtomicWriteResult(commitState: .committed)
        case .committedDurabilityUnconfirmed:
            installedState = postSaveState
            return AtlasVaultAtomicWriteResult(
                commitState: .committedDurabilityUnconfirmed
            )
        case .recoverablePreCommit:
            throw AtlasVaultActivatedOperationError.saveFailed
        case .integrityUnknown:
            throw AtlasVaultRuntimeSaveFailure.integrityUnknown
        case .unclassified:
            throw SaveContainmentUnclassifiedError.failed
        }
    }
}

private actor SaveContainmentGate {
    private var entered = false
    private var isOpen = false
    private var continuation: CheckedContinuation<Void, Never>?

    func enter() async {
        entered = true
        guard !isOpen else { return }
        await withCheckedContinuation { continuation in
            self.continuation = continuation
        }
    }

    func waitUntilEntered() async -> Bool {
        for _ in 0..<500 {
            if entered { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return entered
    }

    func open() {
        isOpen = true
        continuation?.resume()
        continuation = nil
    }
}
