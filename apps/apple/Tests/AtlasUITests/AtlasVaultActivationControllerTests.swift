import Foundation
import XCTest
@testable import AtlasUI

private let activationTestVaultKey = Data(repeating: 0x31, count: 32)

private func activationTestStore() -> AtlasVaultLocalStoreEnvelope {
    AtlasVaultLocalStoreEnvelope(
        storeID: "fake-store-31",
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
        vaultMetadata: [:],
        records: []
    )
}

private func activationPrivateState(_ marker: String) -> AtlasVaultHydratedState {
    let timestamp = "2026-01-01T00:00:00Z"
    let metadata = AtlasHydratedRecordMetadata(
        id: "fake-record-\(marker)",
        revision: "fake-revision-\(marker)",
        parentRevision: nil,
        deleted: false,
        keyID: "fake-key-id"
    )
    return AtlasVaultHydratedState(savedSearches: [AtlasHydratedSavedSearch(
        metadata: metadata,
        payload: AtlasSavedSearchVaultPayload(
            name: "FAKE_PRIVATE_SEARCH_\(marker)",
            summary: "fake summary",
            request: AtlasSearchRequest(text: "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK")
        ),
        clientCreatedAt: timestamp,
        clientUpdatedAt: timestamp
    )])
}

@MainActor
final class AtlasVaultActivationControllerTests: XCTestCase {
    private static let vaultID = "vault_test_31"
    private static let vaultKey = activationTestVaultKey

    func testConstructionIsLockedAndSideEffectFree() async throws {
        let rootURL = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: rootURL) }
        let dependencies = ActivationDependencies(rootURL: rootURL)
        let controller = AtlasVaultActivationController(
            environment: dependencies.environment(),
            keyReleaseObserver: { dependencies.recorder.record("release") }
        )

        await assertState(controller, .locked)
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.events, [])
        XCTAssertEqual(try directoryEntries(at: rootURL), [])
    }

    func testInvalidVaultIDFailsBeforeKeyOrRootAccess() async throws {
        let dependencies = try makeDependencies()
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(vaultID: "saved_search")
        }

        XCTAssertEqual(failure, .invalidVaultID)
        await assertState(controller, .failed(.invalidVaultID))
        XCTAssertEqual(dependencies.recorder.events, [])
        await assertInstalledState(controller, false)
    }

    func testSuppliedRawKeyHasPriorityAndSkipsStoredKeyLookup() async throws {
        let dependencies = try makeDependencies()
        dependencies.storedKeyFailure = .keyStore
        let controller = makeController(dependencies)

        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )

        await assertState(controller, .unlocked)
        XCTAssertEqual(
            dependencies.recorder.events,
            ["root", "scope", "load", "hydrate"]
        )
    }

    func testStoredKeyIsLoadedOnlyWhenExplicitKeyIsAbsent() async throws {
        let dependencies = try makeDependencies()
        let controller = makeController(dependencies)

        try await controller.activate(vaultID: Self.vaultID)

        await assertState(controller, .unlocked)
        XCTAssertEqual(
            dependencies.recorder.events,
            ["key", "root", "scope", "load", "hydrate"]
        )
    }

    func testMissingStoredKeyIsDistinctAndStopsBeforeRoot() async throws {
        let dependencies = try makeDependencies()
        dependencies.storedKey = nil
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(vaultID: Self.vaultID)
        }

        XCTAssertEqual(failure, .keyUnavailable)
        await assertState(controller, .failed(.keyUnavailable))
        XCTAssertEqual(dependencies.recorder.events, ["key"])
        XCTAssertEqual(dependencies.recorder.releaseCount, 0)
    }

    func testStoredKeyFailureIsDistinctAndStopsBeforeRoot() async throws {
        let dependencies = try makeDependencies()
        dependencies.storedKeyFailure = .keyStore
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(vaultID: Self.vaultID)
        }

        XCTAssertEqual(failure, .keyStoreFailure)
        await assertState(controller, .failed(.keyStoreFailure))
        XCTAssertEqual(dependencies.recorder.events, ["key"])
        XCTAssertEqual(dependencies.recorder.releaseCount, 0)
    }

    func testInvalidStoredKeyIsDistinctAndStopsBeforeRoot() async throws {
        let dependencies = try makeDependencies()
        dependencies.storedKey = Data(repeating: 0x31, count: 31)
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(vaultID: Self.vaultID)
        }

        XCTAssertEqual(failure, .invalidVaultKey)
        await assertState(controller, .failed(.invalidVaultKey))
        XCTAssertEqual(dependencies.recorder.events, ["key"])
        XCTAssertEqual(dependencies.recorder.releaseCount, 0)
    }

    func testInvalidSuppliedKeyDoesNotFallBackToStoredKey() async throws {
        let dependencies = try makeDependencies()
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Data(repeating: 0x31, count: 31)
            )
        }

        XCTAssertEqual(failure, .invalidVaultKey)
        XCTAssertEqual(dependencies.recorder.events, [])
        await assertState(controller, .failed(.invalidVaultKey))
    }

    func testActivationOrdersRootScopeLoadAndHydration() async throws {
        let dependencies = try makeDependencies()
        let controller = makeController(dependencies)

        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )

        XCTAssertEqual(
            dependencies.recorder.events,
            ["root", "scope", "load", "hydrate"]
        )
        await assertState(controller, .unlocked)
        await assertInstalledState(controller, true)
        XCTAssertEqual(dependencies.recorder.releaseCount, 0)
    }

    func testActivationCommitsHydratedStateBeforeUnlockedAccess() async throws {
        let dependencies = try makeDependencies()
        let privateStateStore = AtlasVaultPrivateStateStore()
        let expected = activationPrivateState("COMMITTED")
        dependencies.hydratedState = expected
        let controller = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )

        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )

        await assertState(controller, .unlocked)
        let snapshot = try await controller.privateStateSnapshot()
        XCTAssertEqual(snapshot, expected)
    }

    func testMissingStoreInstallsNoPrivateStateAndCreatesNoArtifact() async throws {
        let dependencies = try makeDependencies()
        dependencies.store = nil
        let before = try directoryEntries(at: dependencies.rootURL)
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .storeMissing)
        await assertState(controller, .failed(.storeMissing))
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.events, ["root", "scope", "load", "release"])
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
        XCTAssertEqual(try directoryEntries(at: dependencies.rootURL), before)
    }

    func testRootFailureReleasesKeyAndPublishesVaultUnavailable() async throws {
        let dependencies = try makeDependencies()
        dependencies.rootFailure = true
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .vaultUnavailable)
        await assertState(controller, .failed(.vaultUnavailable))
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.events, ["root", "release"])
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testCorruptStoreFailureReleasesKeyAndInstallsNoState() async throws {
        let dependencies = try makeDependencies()
        dependencies.persistenceFailure = .corruptStore
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .corruptStore)
        await assertState(controller, .failed(.corruptStore))
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testUnsupportedStoreFailureIsNonSensitive() async throws {
        let dependencies = try makeDependencies()
        dependencies.persistenceFailure = .unsupportedStoreVersion
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .unsupportedVersion)
        await assertState(controller, .failed(.unsupportedVersion))
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testAuthenticationFailureDoesNotFallBackOrInstallPartialState() async throws {
        let dependencies = try makeDependencies()
        dependencies.hydrationFailure = .authenticationFailed
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .authenticationFailed)
        await assertState(controller, .failed(.authenticationFailed))
        await assertInstalledState(controller, false)
        XCTAssertFalse(dependencies.recorder.events.contains("key"))
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testMalformedHydrationFailsClosedWithoutPartialState() async throws {
        let dependencies = try makeDependencies()
        dependencies.hydrationFailure = .malformedPayload
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .corruptStore)
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testUnsupportedHydrationVersionFailsClosed() async throws {
        let dependencies = try makeDependencies()
        dependencies.hydrationFailure = .unsupportedRecordVersion
        let controller = makeController(dependencies)

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .unsupportedVersion)
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testLockClearsInstalledStateAndReleasesKeyExactlyOnce() async throws {
        let dependencies = try makeDependencies()
        let controller = makeController(dependencies)
        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )

        await controller.lock()
        await controller.lock()

        await assertState(controller, .locked)
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testLockClearsCommittedPrivateStateBeforePublishingLocked() async throws {
        let dependencies = try makeDependencies()
        let privateStateStore = AtlasVaultPrivateStateStore()
        dependencies.hydratedState = activationPrivateState("LOCK")
        let controller = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )
        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )

        await controller.lock()

        await assertState(controller, .locked)
        let isEmpty = await privateStateStore.isEmpty
        XCTAssertTrue(isEmpty)
        do {
            _ = try await controller.privateStateSnapshot()
            XCTFail("Expected locked private state to be unavailable")
        } catch let error as AtlasVaultPrivateStateStoreError {
            XCTAssertEqual(error, .unavailable)
        }
    }

    func testLockPublishesTransitionStateWhilePrivateClearIsInFlight() async throws {
        let dependencies = try makeDependencies()
        let privateStateStore = ClearAllGatedPrivateStateStore()
        dependencies.hydratedState = activationPrivateState("LOCK_TRANSITION")
        let controller = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )
        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )
        let locking = Task {
            await controller.lock()
        }
        await privateStateStore.waitUntilClearEntered()

        await assertState(controller, .locking)
        let activationFailure = await capturedFailure {
            try await controller.activate(
                vaultID: "vault_other_33",
                suppliedVaultKey: Self.vaultKey
            )
        }
        XCTAssertEqual(activationFailure, .activationInProgress)
        do {
            _ = try await controller.privateStateSnapshot()
            XCTFail("Expected private state to be unavailable while locking")
        } catch let error as AtlasVaultPrivateStateStoreError {
            XCTAssertEqual(error, .unavailable)
        }

        await privateStateStore.openClear()
        await locking.value

        await assertState(controller, .locked)
        let isEmpty = await privateStateStore.isEmpty()
        XCTAssertTrue(isEmpty)
    }

    func testLockWhileAlreadyLockedRemainsObserverIdempotentDuringClear() async throws {
        let dependencies = try makeDependencies()
        let privateStateStore = ClearAllGatedPrivateStateStore()
        let controller = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )

        let locking = Task {
            await controller.lock()
        }
        await privateStateStore.waitUntilClearEntered()

        await assertState(controller, .locked)

        await privateStateStore.openClear()
        await locking.value
        await assertState(controller, .locked)
    }

    func testSnapshotStartedBeforeLockCannotReturnPrivateStateAfterLock() async throws {
        let dependencies = try makeDependencies()
        let privateStateStore = SnapshotGatedPrivateStateStore()
        dependencies.hydratedState = activationPrivateState("SNAPSHOT_LOCK")
        let controller = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )
        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )
        let snapshot = Task {
            try await controller.privateStateSnapshot()
        }
        await privateStateStore.waitUntilSnapshotEntered()

        await controller.lock()
        await privateStateStore.openSnapshot()

        do {
            _ = try await snapshot.value
            XCTFail("Expected snapshot started before lock to fail")
        } catch let error as AtlasVaultPrivateStateStoreError {
            XCTAssertEqual(error, .unavailable)
        } catch {
            XCTFail("Unexpected error type: \(type(of: error))")
        }
        await assertState(controller, .locked)
        let isEmpty = await privateStateStore.isEmpty()
        XCTAssertTrue(isEmpty)
    }

    func testCancellationDuringPrivateStateCommitClearsGenerationAndCannotUnlock() async throws {
        let dependencies = try makeDependencies()
        dependencies.hydratedState = activationPrivateState("CANCEL_COMMIT")
        let privateStateStore = CommitGatedPrivateStateStore()
        let controller = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )
        let activation = Task {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }
        await privateStateStore.waitUntilCommitEntered()

        await assertState(controller, .activating)
        do {
            _ = try await controller.privateStateSnapshot()
            XCTFail("Expected committed state to remain unavailable before unlocked")
        } catch let error as AtlasVaultPrivateStateStoreError {
            XCTAssertEqual(error, .unavailable)
        }

        let didCancel = await controller.cancelActivation()
        XCTAssertTrue(didCancel)
        await privateStateStore.openCommit()

        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        let isEmpty = await privateStateStore.isEmpty()
        XCTAssertTrue(isEmpty)
        await assertInstalledState(controller, false)
    }

    func testLockDuringPrivateStateCommitClearsGenerationAndCannotUnlock() async throws {
        let dependencies = try makeDependencies()
        dependencies.hydratedState = activationPrivateState("LOCK_COMMIT")
        let privateStateStore = CommitGatedPrivateStateStore()
        let controller = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )
        let activation = Task {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }
        await privateStateStore.waitUntilCommitEntered()

        await controller.lock()
        await privateStateStore.openCommit()

        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        let isEmpty = await privateStateStore.isEmpty()
        XCTAssertTrue(isEmpty)
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testReactivationCannotExposePriorPrivateStateGeneration() async throws {
        let dependencies = try makeDependencies()
        let privateStateStore = AtlasVaultPrivateStateStore()
        let controller = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )
        dependencies.hydratedState = activationPrivateState("FIRST")
        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )
        let firstSnapshot = try await controller.privateStateSnapshot()
        XCTAssertEqual(firstSnapshot, activationPrivateState("FIRST"))
        await controller.lock()

        dependencies.hydratedState = activationPrivateState("SECOND")
        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )

        let secondSnapshot = try await controller.privateStateSnapshot()
        XCTAssertEqual(secondSnapshot, activationPrivateState("SECOND"))
    }

    func testReentrantActivationReturnsErrorWithoutMutatingUnlockedState() async throws {
        let dependencies = try makeDependencies()
        let controller = makeController(dependencies)
        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )
        let eventsBeforeReentry = dependencies.recorder.events

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: "vault_other_31",
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .alreadyUnlocked)
        await assertState(controller, .unlocked)
        await assertInstalledState(controller, true)
        XCTAssertEqual(dependencies.recorder.events, eventsBeforeReentry)
    }

    func testConcurrentActivationReturnsErrorWithoutReplacingOriginalAttempt() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationGate()
        dependencies.storedKeyGate = gate
        let controller = makeController(dependencies)
        let first = Task {
            try await controller.activate(vaultID: Self.vaultID)
        }
        await gate.waitUntilEntered()

        let failure = await capturedFailure {
            try await controller.activate(
                vaultID: "vault_other_31",
                suppliedVaultKey: Self.vaultKey
            )
        }

        XCTAssertEqual(failure, .activationInProgress)
        await assertState(controller, .activating)
        await gate.open()
        try await first.value
        await assertState(controller, .unlocked)
    }

    func testCancellationAfterKeySelectionWipesAndRejectsLateResult() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationGate()
        dependencies.rootGate = gate
        let controller = makeController(dependencies)
        let activation = Task {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }
        await gate.waitUntilEntered()

        let didCancel = await controller.cancelActivation()
        XCTAssertTrue(didCancel)
        await assertState(controller, .locked)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
        await gate.open()

        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        await assertInstalledState(controller, false)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testCancellationDuringStoredKeyLoadDoesNotRetainLateKeyOwner() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationGate()
        dependencies.storedKeyGate = gate
        let controller = makeController(dependencies)
        let activation = Task {
            try await controller.activate(vaultID: Self.vaultID)
        }
        await gate.waitUntilEntered()

        let didCancel = await controller.cancelActivation()
        XCTAssertTrue(didCancel)
        await assertState(controller, .locked)
        XCTAssertEqual(dependencies.recorder.releaseCount, 0)

        await gate.open()
        let failure = await taskFailure(activation)

        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        await assertInstalledState(controller, false)
        await controller.lock()
        XCTAssertEqual(dependencies.recorder.releaseCount, 0)
    }

    func testCancellationWinsOverLateStoredKeyFailure() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationGate()
        dependencies.storedKeyGate = gate
        dependencies.storedKeyFailure = .keyStore
        let controller = makeController(dependencies)
        let activation = Task {
            try await controller.activate(vaultID: Self.vaultID)
        }
        await gate.waitUntilEntered()

        let didCancel = await controller.cancelActivation()
        XCTAssertTrue(didCancel)
        await gate.open()

        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        XCTAssertEqual(dependencies.recorder.releaseCount, 0)
    }

    func testCancellationWinsOverLateRootFailure() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationGate()
        dependencies.rootGate = gate
        dependencies.rootFailure = true
        let controller = makeController(dependencies)
        let activation = Task {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }
        await gate.waitUntilEntered()

        let didCancel = await controller.cancelActivation()
        XCTAssertTrue(didCancel)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
        await gate.open()

        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testCancellationWinsOverLateScopeFailure() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationGate()
        dependencies.scopeGate = gate
        dependencies.scopeFailure = true
        let controller = makeController(dependencies)
        let activation = Task {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }
        await gate.waitUntilEntered()

        let didCancel = await controller.cancelActivation()
        XCTAssertTrue(didCancel)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
        await gate.open()

        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testQueuedCancellationWinsOverSynchronousStoreFailure() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationSynchronousGate()
        dependencies.loadGate = gate
        dependencies.persistenceFailure = .readFailed
        let controller = makeController(dependencies)
        let activation = Task {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }
        await waitUntil { gate.hasEntered }
        XCTAssertTrue(gate.hasEntered)

        let cancellation = await queuedCancellation(for: controller)
        gate.open()

        let didCancel = await cancellation.value
        XCTAssertTrue(didCancel)
        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testQueuedCancellationWinsOverSynchronousMissingStoreResult() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationSynchronousGate()
        dependencies.loadGate = gate
        dependencies.store = nil
        let controller = makeController(dependencies)
        let activation = Task {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }
        await waitUntil { gate.hasEntered }
        XCTAssertTrue(gate.hasEntered)

        let cancellation = await queuedCancellation(for: controller)
        gate.open()

        let didCancel = await cancellation.value
        XCTAssertTrue(didCancel)
        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testQueuedCancellationWinsOverSynchronousHydrationFailure() async throws {
        let dependencies = try makeDependencies()
        let gate = ActivationSynchronousGate()
        dependencies.hydrationGate = gate
        dependencies.hydrationFailure = .corruptRecord
        let controller = makeController(dependencies)
        let activation = Task {
            try await controller.activate(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        }
        await waitUntil { gate.hasEntered }
        XCTAssertTrue(gate.hasEntered)

        let cancellation = await queuedCancellation(for: controller)
        gate.open()

        let didCancel = await cancellation.value
        XCTAssertTrue(didCancel)
        let failure = await taskFailure(activation)
        XCTAssertEqual(failure, .cancelled)
        await assertState(controller, .locked)
        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testControllerDeinitReleasesInstalledKey() async throws {
        let dependencies = try makeDependencies()
        var controller: AtlasVaultActivationController? = makeController(dependencies)
        try await controller?.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )
        XCTAssertEqual(dependencies.recorder.releaseCount, 0)

        controller = nil
        await waitUntil { dependencies.recorder.releaseCount == 1 }

        XCTAssertEqual(dependencies.recorder.releaseCount, 1)
    }

    func testControllerDeinitClearsInjectedPrivateStateStore() async throws {
        let dependencies = try makeDependencies()
        let privateStateStore = AtlasVaultPrivateStateStore()
        dependencies.hydratedState = activationPrivateState("TEARDOWN")
        var controller: AtlasVaultActivationController? = makeController(
            dependencies,
            privateStateStore: privateStateStore
        )
        try await controller?.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )
        let wasEmptyBeforeTeardown = await privateStateStore.isEmpty
        XCTAssertFalse(wasEmptyBeforeTeardown)

        controller = nil
        await waitUntilAsync { await privateStateStore.isEmpty }

        let isEmptyAfterTeardown = await privateStateStore.isEmpty
        XCTAssertTrue(isEmptyAfterTeardown)
    }

    func testDescriptionsAndErrorsContainNoPrivateValues() async throws {
        let dependencies = try makeDependencies()
        let environment = dependencies.environment()
        let scope = try dependencies.scope(vaultID: Self.vaultID)
        let controller = AtlasVaultActivationController(environment: environment)
        let privateValues = [
            Self.vaultID,
            Self.vaultKey.base64EncodedString(),
            Self.vaultKey.map { String(format: "%02x", $0) }.joined(),
            dependencies.rootURL.path,
            "TEST_PRIVATE_SEARCH_SENTINEL",
            "TEST_PRIVATE_JOB_SENTINEL",
        ]
        let descriptions = [
            String(describing: environment),
            String(reflecting: environment),
            String(describing: scope),
            String(reflecting: scope),
            String(describing: controller),
            String(reflecting: controller),
            String(describing: AtlasVaultActivationFailure.authenticationFailed),
            String(describing: AtlasVaultActivationState.failed(.corruptStore)),
        ]

        for description in descriptions {
            for privateValue in privateValues {
                XCTAssertFalse(description.contains(privateValue))
            }
        }
    }

    func testActivationDoesNotMutatePublicSnapshot() async throws {
        let dependencies = try makeDependencies()
        let controller = makeController(dependencies)
        let snapshot = try publicSnapshot()
        let before = try encodedSnapshot(snapshot)

        try await controller.activate(
            vaultID: Self.vaultID,
            suppliedVaultKey: Self.vaultKey
        )
        await controller.lock()

        XCTAssertEqual(try encodedSnapshot(snapshot), before)
    }

    func testRuntimeServicesAdapterLoadsUnderTempRootWithoutWriting() async throws {
        let rootURL = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: rootURL) }
        let recorder = ActivationRecorder()
        let store = makeStore()
        let storeURL = rootURL
            .appendingPathComponent(AtlasInjectedRootVaultPathLocator.atlasDirectoryName, isDirectory: true)
            .appendingPathComponent(AtlasInjectedRootVaultPathLocator.vaultsDirectoryName, isDirectory: true)
            .appendingPathComponent(Self.vaultID, isDirectory: true)
            .appendingPathComponent(AtlasInjectedRootVaultPathLocator.localStoreFileName)
        try FileManager.default.createDirectory(
            at: storeURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        XCTAssertTrue(FileManager.default.createFile(atPath: storeURL.path, contents: Data("fixture".utf8)))
        let before = try recursiveRelativePaths(at: rootURL)
        let runtimeServices = AtlasVaultRuntimeFactory.makeServices(
            rootDirectoryProvider: RuntimeRootProvider(rootURL: rootURL, recorder: recorder),
            keyStore: RuntimeKeyStore(key: Self.vaultKey, recorder: recorder),
            directoryPreparer: RuntimeDirectoryPreparer(recorder: recorder),
            localStoreIO: RuntimeLocalStoreIO(store: store, recorder: recorder),
            atomicStoreWriter: RuntimeAtomicWriter(recorder: recorder),
            localStoreMerger: AtlasVaultLocalStoreMerger(),
            recordSaver: AtlasVaultRecordSaver(),
            recordHydrator: RuntimeHydrator(recorder: recorder)
        )
        let environment = AtlasVaultActivationEnvironment.runtimeServices(runtimeServices)
        let controller = AtlasVaultActivationController(environment: environment)

        XCTAssertEqual(recorder.events, [])
        try await controller.activate(vaultID: Self.vaultID)

        await assertState(controller, .unlocked)
        XCTAssertEqual(recorder.events, ["key", "root", "prepare", "read", "hydrate"])
        XCTAssertFalse(recorder.events.contains("write"))
        XCTAssertFalse(recorder.events.contains("atomicWrite"))
        XCTAssertEqual(try recursiveRelativePaths(at: rootURL), before)
    }

    func testSourceHasNoAppRuntimeFileWriteOrNetworkCallSite() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)
        for forbidden in [
            "SwiftUI",
            "SearchViewModel",
            "AtlasLocalCache",
            "AtlasPublicLocalSnapshot",
            "URLSession",
            "LocalAuthentication",
            "LAContext",
            "SecItem",
            "@main",
            "FileManager.default",
            "Data.write",
            "createFile",
        ] {
            XCTAssertFalse(source.contains(forbidden), "Unexpected source reference: \(forbidden)")
        }
    }

    private func makeDependencies() throws -> ActivationDependencies {
        let rootURL = try temporaryRoot()
        addTeardownBlock { try? FileManager.default.removeItem(at: rootURL) }
        return ActivationDependencies(rootURL: rootURL)
    }

    private func makeController(
        _ dependencies: ActivationDependencies
    ) -> AtlasVaultActivationController {
        AtlasVaultActivationController(
            environment: dependencies.environment(),
            keyReleaseObserver: { dependencies.recorder.record("release") }
        )
    }

    private func makeController(
        _ dependencies: ActivationDependencies,
        privateStateStore: any AtlasVaultPrivateStateStoring
    ) -> AtlasVaultActivationController {
        AtlasVaultActivationController(
            environment: dependencies.environment(),
            privateStateStore: privateStateStore,
            keyReleaseObserver: { dependencies.recorder.record("release") }
        )
    }

    private func capturedFailure(
        _ operation: () async throws -> Void
    ) async -> AtlasVaultActivationFailure? {
        do {
            try await operation()
            XCTFail("Expected activation failure")
            return nil
        } catch let failure as AtlasVaultActivationFailure {
            return failure
        } catch {
            XCTFail("Unexpected error type: \(type(of: error))")
            return nil
        }
    }

    private func taskFailure(
        _ task: Task<Void, Error>
    ) async -> AtlasVaultActivationFailure? {
        do {
            try await task.value
            XCTFail("Expected task failure")
            return nil
        } catch let failure as AtlasVaultActivationFailure {
            return failure
        } catch {
            XCTFail("Unexpected error type: \(type(of: error))")
            return nil
        }
    }

    private func waitUntil(
        _ predicate: @escaping @Sendable () -> Bool
    ) async {
        for _ in 0..<500 {
            if predicate() {
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    private func waitUntilAsync(
        _ predicate: @escaping @Sendable () async -> Bool,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<500 {
            if await predicate() {
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Timed out waiting for asynchronous condition", file: file, line: line)
    }

    private func queuedCancellation(
        for controller: AtlasVaultActivationController
    ) async -> Task<Bool, Never> {
        let cancellation = Task(priority: .high) {
            await controller.cancelActivation()
        }
        try? await Task.sleep(nanoseconds: 20_000_000)
        return cancellation
    }

    private func temporaryRoot() throws -> URL {
        let rootURL = try AtlasVaultTestFileSystemSupport.canonicalTemporaryRoot()
            .appendingPathComponent(
                "atlasvault-activation-tests-\(UUID().uuidString)",
                isDirectory: true
            )
            .standardizedFileURL
        try FileManager.default.createDirectory(at: rootURL, withIntermediateDirectories: true)
        return rootURL
    }

    private func directoryEntries(at url: URL) throws -> [String] {
        try FileManager.default.contentsOfDirectory(atPath: url.path).sorted()
    }

    private func recursiveRelativePaths(at rootURL: URL) throws -> [String] {
        guard let enumerator = FileManager.default.enumerator(
            at: rootURL,
            includingPropertiesForKeys: nil
        ) else {
            return []
        }
        return enumerator.compactMap { element in
            guard let url = element as? URL else { return nil }
            return String(url.path.dropFirst(rootURL.path.count + 1))
        }.sorted()
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

    private func assertState(
        _ controller: AtlasVaultActivationController,
        _ expected: AtlasVaultActivationState,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let state = await controller.state
        XCTAssertEqual(state, expected, file: file, line: line)
    }

    private func assertInstalledState(
        _ controller: AtlasVaultActivationController,
        _ expected: Bool,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let isInstalled = await controller.hasInstalledPrivateStateForTesting
        XCTAssertEqual(isInstalled, expected, file: file, line: line)
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent(
                "../../Sources/AtlasUI/AtlasVaultActivationController.swift"
            ),
            sourceDirectory.appendingPathComponent(
                "../../../../apps/apple/Sources/AtlasUI/AtlasVaultActivationController.swift"
            ),
        ].map(\.standardizedFileURL)
        guard let sourceURL = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) else {
            throw NSError(
                domain: "AtlasVaultActivationControllerTests",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Could not find activation controller source"]
            )
        }
        return sourceURL
    }

    private static func makeStore() -> AtlasVaultLocalStoreEnvelope {
        activationTestStore()
    }

    private func makeStore() -> AtlasVaultLocalStoreEnvelope {
        Self.makeStore()
    }
}

private enum StoredKeyFailure: Error, Sendable {
    case keyStore
}

private enum RuntimeTestError: Error, Sendable {
    case root
    case write
}

private final class ActivationDependencies: @unchecked Sendable {
    let rootURL: URL
    let recorder = ActivationRecorder()
    var storedKey: Data? = activationTestVaultKey
    var storedKeyFailure: StoredKeyFailure?
    var rootFailure = false
    var scopeFailure = false
    var store: AtlasVaultLocalStoreEnvelope? = activationTestStore()
    var persistenceFailure: AtlasVaultPersistenceError?
    var hydrationFailure: AtlasVaultHydrationError?
    var hydratedState = AtlasVaultHydratedState()
    var scopeVaultIDOverride: String?
    var storedKeyGate: ActivationGate?
    var rootGate: ActivationGate?
    var scopeGate: ActivationGate?
    var loadGate: ActivationSynchronousGate?
    var hydrationGate: ActivationSynchronousGate?

    init(rootURL: URL) {
        self.rootURL = rootURL
    }

    func environment() -> AtlasVaultActivationEnvironment {
        AtlasVaultActivationEnvironment(
            loadStoredVaultKey: { [self] _ in
                recorder.record("key")
                if let storedKeyGate {
                    await storedKeyGate.suspend()
                }
                if let storedKeyFailure {
                    throw storedKeyFailure
                }
                return storedKey
            },
            resolveRootDirectory: { [self] in
                recorder.record("root")
                if let rootGate {
                    await rootGate.suspend()
                }
                if rootFailure {
                    throw RuntimeTestError.root
                }
                return rootURL
            },
            makeScope: { [self] _, vaultID in
                recorder.record("scope")
                if let scopeGate {
                    await scopeGate.suspend()
                }
                if scopeFailure {
                    throw RuntimeTestError.root
                }
                return try scope(vaultID: scopeVaultIDOverride ?? vaultID)
            }
        )
    }

    func scope(vaultID: String) throws -> AtlasVaultActivationScope {
        try AtlasVaultActivationScope(
            vaultID: vaultID,
            loadEncryptedStore: { [self] _ in
                recorder.record("load")
                loadGate?.suspend()
                if let persistenceFailure {
                    throw persistenceFailure
                }
                return store
            },
            hydrateRecords: { [self] _, _ in
                recorder.record("hydrate")
                hydrationGate?.suspend()
                if let hydrationFailure {
                    throw hydrationFailure
                }
                return hydratedState
            }
        )
    }
}

private final class ActivationRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var recordedEvents: [String] = []

    var events: [String] {
        lock.lock()
        defer { lock.unlock() }
        return recordedEvents
    }

    var releaseCount: Int {
        events.filter { $0 == "release" }.count
    }

    func record(_ event: String) {
        lock.lock()
        recordedEvents.append(event)
        lock.unlock()
    }
}

private actor CommitGatedPrivateStateStore: AtlasVaultPrivateStateStoring {
    private let store = AtlasVaultPrivateStateStore()
    private var commitEntered = false
    private var commitWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseContinuation: CheckedContinuation<Void, Never>?

    func stage(
        _ state: AtlasVaultHydratedState,
        generation: AtlasVaultPrivateStateGeneration
    ) async throws {
        try await store.stage(state, generation: generation)
    }

    func commit(generation: AtlasVaultPrivateStateGeneration) async throws {
        try await store.commit(generation: generation)
        commitEntered = true
        let waiters = commitWaiters
        commitWaiters.removeAll()
        waiters.forEach { $0.resume() }
        await withCheckedContinuation { continuation in
            releaseContinuation = continuation
        }
    }

    func snapshot(
        generation: AtlasVaultPrivateStateGeneration
    ) async throws -> AtlasVaultHydratedState {
        try await store.snapshot(generation: generation)
    }

    func clear(generation: AtlasVaultPrivateStateGeneration) async {
        await store.clear(generation: generation)
    }

    func clearAll() async {
        await store.clearAll()
    }

    func waitUntilCommitEntered() async {
        guard !commitEntered else { return }
        await withCheckedContinuation { continuation in
            commitWaiters.append(continuation)
        }
    }

    func openCommit() {
        releaseContinuation?.resume()
        releaseContinuation = nil
    }

    func isEmpty() async -> Bool {
        await store.isEmpty
    }
}

private actor ClearAllGatedPrivateStateStore: AtlasVaultPrivateStateStoring {
    private let store = AtlasVaultPrivateStateStore()
    private var clearEntered = false
    private var clearWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseContinuation: CheckedContinuation<Void, Never>?

    func stage(
        _ state: AtlasVaultHydratedState,
        generation: AtlasVaultPrivateStateGeneration
    ) async throws {
        try await store.stage(state, generation: generation)
    }

    func commit(generation: AtlasVaultPrivateStateGeneration) async throws {
        try await store.commit(generation: generation)
    }

    func snapshot(
        generation: AtlasVaultPrivateStateGeneration
    ) async throws -> AtlasVaultHydratedState {
        try await store.snapshot(generation: generation)
    }

    func clear(generation: AtlasVaultPrivateStateGeneration) async {
        await store.clear(generation: generation)
    }

    func clearAll() async {
        await store.clearAll()
        clearEntered = true
        let waiters = clearWaiters
        clearWaiters.removeAll()
        waiters.forEach { $0.resume() }
        await withCheckedContinuation { continuation in
            releaseContinuation = continuation
        }
    }

    func waitUntilClearEntered() async {
        guard !clearEntered else { return }
        await withCheckedContinuation { continuation in
            clearWaiters.append(continuation)
        }
    }

    func openClear() {
        releaseContinuation?.resume()
        releaseContinuation = nil
    }

    func isEmpty() async -> Bool {
        await store.isEmpty
    }
}

private actor SnapshotGatedPrivateStateStore: AtlasVaultPrivateStateStoring {
    private let store = AtlasVaultPrivateStateStore()
    private var snapshotEntered = false
    private var snapshotWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseContinuation: CheckedContinuation<Void, Never>?

    func stage(
        _ state: AtlasVaultHydratedState,
        generation: AtlasVaultPrivateStateGeneration
    ) async throws {
        try await store.stage(state, generation: generation)
    }

    func commit(generation: AtlasVaultPrivateStateGeneration) async throws {
        try await store.commit(generation: generation)
    }

    func snapshot(
        generation: AtlasVaultPrivateStateGeneration
    ) async throws -> AtlasVaultHydratedState {
        let snapshot = try await store.snapshot(generation: generation)
        snapshotEntered = true
        let waiters = snapshotWaiters
        snapshotWaiters.removeAll()
        waiters.forEach { $0.resume() }
        await withCheckedContinuation { continuation in
            releaseContinuation = continuation
        }
        return snapshot
    }

    func clear(generation: AtlasVaultPrivateStateGeneration) async {
        await store.clear(generation: generation)
    }

    func clearAll() async {
        await store.clearAll()
    }

    func waitUntilSnapshotEntered() async {
        guard !snapshotEntered else { return }
        await withCheckedContinuation { continuation in
            snapshotWaiters.append(continuation)
        }
    }

    func openSnapshot() {
        releaseContinuation?.resume()
        releaseContinuation = nil
    }

    func isEmpty() async -> Bool {
        await store.isEmpty
    }
}

private actor ActivationGate {
    private var entered = false
    private var enteredWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseContinuation: CheckedContinuation<Void, Never>?

    func suspend() async {
        entered = true
        let waiters = enteredWaiters
        enteredWaiters.removeAll()
        waiters.forEach { $0.resume() }
        await withCheckedContinuation { continuation in
            releaseContinuation = continuation
        }
    }

    func waitUntilEntered() async {
        guard !entered else { return }
        await withCheckedContinuation { continuation in
            enteredWaiters.append(continuation)
        }
    }

    func open() {
        releaseContinuation?.resume()
        releaseContinuation = nil
    }
}

private final class ActivationSynchronousGate: @unchecked Sendable {
    private let condition = NSCondition()
    private var entered = false
    private var isOpen = false

    var hasEntered: Bool {
        condition.lock()
        defer { condition.unlock() }
        return entered
    }

    func suspend() {
        condition.lock()
        entered = true
        condition.broadcast()
        while !isOpen {
            condition.wait()
        }
        condition.unlock()
    }

    func open() {
        condition.lock()
        isOpen = true
        condition.broadcast()
        condition.unlock()
    }
}

private struct RuntimeRootProvider: AtlasVaultRootDirectoryProviding {
    let rootURL: URL
    let recorder: ActivationRecorder

    func rootDirectory() throws -> URL {
        recorder.record("root")
        return rootURL
    }
}

private struct RuntimeKeyStore: AtlasVaultKeyStore {
    let key: Data
    let recorder: ActivationRecorder

    func loadVaultKey(for vaultID: String) throws -> Data? {
        recorder.record("key")
        return key
    }

    func saveVaultKey(_ key: Data, for vaultID: String) throws {
        recorder.record("saveKey")
    }

    func deleteVaultKey(for vaultID: String) throws {
        recorder.record("deleteKey")
    }
}

private struct RuntimeDirectoryPreparer: AtlasVaultDirectoryPreparer {
    let recorder: ActivationRecorder

    func prepareParentDirectory(for storeURL: URL, under rootDirectory: URL) throws {
        recorder.record("prepare")
    }
}

private struct RuntimeLocalStoreIO: AtlasVaultLocalStoreProviding {
    let store: AtlasVaultLocalStoreEnvelope
    let recorder: ActivationRecorder

    func read(from url: URL) throws -> AtlasVaultLocalStoreEnvelope {
        recorder.record("read")
        return store
    }

    func write(_ store: AtlasVaultLocalStoreEnvelope, to url: URL, overwrite: Bool) throws {
        recorder.record("write")
        throw RuntimeTestError.write
    }
}

private struct RuntimeAtomicWriter: AtlasVaultAtomicStoreWriting {
    let recorder: ActivationRecorder

    func write(
        _ store: AtlasVaultLocalStoreEnvelope,
        to destinationURL: URL,
        overwrite: Bool
    ) throws -> AtlasVaultAtomicWriteResult {
        recorder.record("atomicWrite")
        throw RuntimeTestError.write
    }
}

private struct RuntimeHydrator: AtlasVaultRecordHydrating {
    let recorder: ActivationRecorder

    func hydrate(
        records: [AtlasVaultEncryptedRecordEnvelope],
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultHydratedState {
        recorder.record("hydrate")
        return AtlasVaultHydratedState()
    }
}
