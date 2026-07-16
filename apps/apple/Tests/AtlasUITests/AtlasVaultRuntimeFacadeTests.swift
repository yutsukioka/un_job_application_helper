import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRuntimeFacadeTests: XCTestCase {
    private static let vaultID = "vault-runtime-facade-001"
    private static let otherVaultID = "vault-runtime-facade-002"
    private static let vaultKey = Data(repeating: 0xA7, count: AtlasVaultRecordCrypto.vaultKeyByteCount)
    private static let privateSentinel = "FAKE_PRIVATE_RUNTIME_FACADE_SENTINEL_DO_NOT_LEAK"

    func testConstructionIsLockedAndInvokesNoDependencies() async {
        let harness = FacadeHarness()
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())

        let status = await facade.status()
        let events = await harness.events()
        XCTAssertEqual(status, .locked)
        XCTAssertEqual(events, [])
    }

    func testProductionGraphConstructionIsSideEffectFree() throws {
        let rootURL = try temporaryRoot()
        addTeardownBlock { try? FileManager.default.removeItem(at: rootURL) }
        let recorder = RuntimeFacadeRecorder()
        let box = RuntimeStoreBox(store: emptyStore())
        let before = try recursiveRelativePaths(at: rootURL)
        let services: AtlasVaultRuntimeServices<
            RuntimeDirectoryPreparer,
            RuntimeLocalStoreIO
        > = AtlasVaultRuntimeFactory.makeServices(
            rootDirectoryProvider: RuntimeRootProvider(rootURL: rootURL, recorder: recorder),
            keyStore: RuntimeKeyStore(key: Self.vaultKey, recorder: recorder),
            directoryPreparer: RuntimeDirectoryPreparer(recorder: recorder),
            localStoreIO: RuntimeLocalStoreIO(box: box, recorder: recorder),
            atomicStoreWriter: RuntimeAtomicWriter(box: box, recorder: recorder),
            localStoreMerger: RuntimeMerger(recorder: recorder),
            recordSaver: RuntimeSaver(recorder: recorder),
            recordHydrator: RuntimeHydrator(recorder: recorder)
        )

        _ = AtlasVaultRuntimeFacade.runtimeServices(services)

        XCTAssertEqual(recorder.events, [])
        XCTAssertEqual(try recursiveRelativePaths(at: rootURL), before)
    }

    func testActivationSuccessMakesPrivateStateAvailable() async throws {
        let expected = privateState(recordID: "private-record")
        let harness = FacadeHarness(privateState: expected)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())

        try await facade.activate(activationRequest())

        let status = await facade.status()
        let state = try await facade.privateState()
        let events = await harness.events()
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(state.state, expected)
        XCTAssertEqual(events, ["activate", "privateState"])
    }

    func testActivationFailureUsesNonSensitiveCategory() async {
        let harness = FacadeHarness()
        await harness.setActivationFailure(.authenticationFailed)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())

        do {
            try await facade.activate(activationRequest())
            XCTFail("Expected activation failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .activationFailed(.authenticationFailed)
            )
        }
        let status = await facade.status()
        XCTAssertEqual(status, .failed(.activation(.authenticationFailed)))
    }

    func testPrivateStateIsUnavailableWhileLocked() async {
        let harness = FacadeHarness(privateState: privateState(recordID: "private-record"))
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())

        do {
            _ = try await facade.privateState()
            XCTFail("Expected private state to be unavailable")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .privateStateUnavailable
            )
        }
        let events = await harness.events()
        XCTAssertEqual(events, [])
    }

    func testPrivateStateDependencyCancellationRemainsCancellation() async throws {
        let harness = FacadeHarness(privateState: privateState(recordID: "private-record"))
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        await harness.setPrivateStateCancels(true)

        do {
            _ = try await facade.privateState()
            XCTFail("Expected private-state cancellation")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .cancelled)
        }
    }

    func testLockClearsStateAndRepeatedLockIsSafe() async throws {
        let harness = FacadeHarness(privateState: privateState(recordID: "private-record"))
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        await facade.lock()
        await facade.lock()

        let status = await facade.status()
        let lockCount = await harness.events().filter { $0 == "lock" }.count
        XCTAssertEqual(status, .locked)
        XCTAssertEqual(lockCount, 2)
        do {
            _ = try await facade.privateState()
            XCTFail("Expected private state to be cleared")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .privateStateUnavailable
            )
        }
    }

    func testSaveWhileLockedFailsBeforeSaveDependency() async {
        let harness = FacadeHarness()
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())

        do {
            _ = try await facade.apply(mutationRequest())
            XCTFail("Expected locked save failure")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .locked)
        }
        let events = await harness.events()
        XCTAssertEqual(events, [])
    }

    func testSuccessfulSaveUpdatesPrivateStateAfterCommit() async throws {
        let initial = privateState(recordID: "initial-private-record")
        let updated = privateState(recordID: "updated-private-record")
        let harness = FacadeHarness(privateState: initial, postSaveState: updated)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        let outcome = try await facade.apply(mutationRequest())
        let status = await facade.status()
        let state = try await facade.privateState()

        XCTAssertEqual(outcome, .committed)
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(state.state, updated)
    }

    func testDurabilityUnconfirmedOutcomeRemainsSuccessful() async throws {
        let harness = FacadeHarness()
        await harness.setSaveResult(
            AtlasVaultAtomicWriteResult(commitState: .committedDurabilityUnconfirmed)
        )
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        let outcome = try await facade.apply(mutationRequest())
        let status = await facade.status()
        XCTAssertEqual(outcome, .committedDurabilityUnconfirmed)
        XCTAssertEqual(status, .unlocked)
    }

    func testFailedEncryptedSaveLeavesPrivateStateUnchanged() async throws {
        let initial = privateState(recordID: "initial-private-record")
        let harness = FacadeHarness(privateState: initial)
        await harness.setSaveFailure(.saveFailed)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        do {
            _ = try await facade.apply(mutationRequest())
            XCTFail("Expected save failure")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .saveFailed)
        }

        let status = await facade.status()
        let state = try await facade.privateState()
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(state.state, initial)
    }

    func testCallerCancellationBeforeSaveCommitKeepsFacadeUnlocked() async throws {
        let initial = privateState(recordID: "initial-private-record")
        let gate = FacadeGate()
        let harness = FacadeHarness(privateState: initial, saveGate: gate)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        let request = mutationRequest()

        let save = Task { try await facade.apply(request) }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            save.cancel()
            await gate.open()
            _ = try? await save.value
            return
        }

        save.cancel()
        await gate.open()

        do {
            _ = try await save.value
            XCTFail("Expected pre-commit cancellation")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .cancelled)
        }
        let status = await facade.status()
        let state = try await facade.privateState()
        let lockCount = await harness.events().filter { $0 == "lock" }.count
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(state.state, initial)
        XCTAssertEqual(lockCount, 0)
    }

    func testSessionMismatchLeavesPrivateStateUnchanged() async throws {
        let initial = privateState(recordID: "initial-private-record")
        let harness = FacadeHarness(privateState: initial)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        do {
            _ = try await facade.apply(
                AtlasVaultRuntimeMutationRequest(
                    expectedVaultID: Self.otherVaultID,
                    mutations: AtlasVaultMutationSet()
                )
            )
            XCTFail("Expected session mismatch")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .sessionMismatch)
        }

        let status = await facade.status()
        let state = try await facade.privateState()
        let saveCount = await harness.successfulSaveCount()
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(state.state, initial)
        XCTAssertEqual(saveCount, 0)
    }

    func testActivationWhileUnlockedIsRejectedWithoutDependencyCall() async throws {
        let harness = FacadeHarness()
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        do {
            try await facade.activate(activationRequest())
            XCTFail("Expected already-unlocked failure")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .alreadyUnlocked)
        }
        let activationCount = await harness.events().filter { $0 == "activate" }.count
        XCTAssertEqual(activationCount, 1)
    }

    func testActivationAfterLockSucceeds() async throws {
        let harness = FacadeHarness()
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        await facade.lock()

        try await facade.activate(activationRequest())

        let status = await facade.status()
        let activationCount = await harness.events().filter { $0 == "activate" }.count
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(activationCount, 2)
    }

    func testOverlappingSaveIsRejectedAndSequentialSaveIsOrdered() async throws {
        let gate = FacadeGate()
        let harness = FacadeHarness(saveGate: gate)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        let request = mutationRequest()

        let first = Task { try await facade.apply(request) }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            first.cancel()
            await gate.open()
            _ = try? await first.value
            return
        }
        do {
            _ = try await facade.apply(mutationRequest())
            XCTFail("Expected overlapping save rejection")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .operationInProgress
            )
        }
        await gate.open()
        let firstOutcome = try await first.value
        XCTAssertEqual(firstOutcome, .committed)

        await harness.setSaveGate(nil)
        let secondOutcome = try await facade.apply(mutationRequest())
        let saveCount = await harness.successfulSaveCount()
        XCTAssertEqual(secondOutcome, .committed)
        XCTAssertEqual(saveCount, 2)
    }

    func testLockDuringCommittedSavePreservesOutcomeAndRemainsLocked() async throws {
        let gate = FacadeGate()
        let harness = FacadeHarness(postCommitSaveGate: gate)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        let request = mutationRequest()

        let save = Task { try await facade.apply(request) }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            save.cancel()
            await gate.open()
            _ = try? await save.value
            return
        }
        await facade.lock()
        await gate.open()

        let outcome = try await save.value
        let status = await facade.status()
        let saveCount = await harness.successfulSaveCount()
        XCTAssertEqual(outcome, .committed)
        XCTAssertEqual(status, .locked)
        XCTAssertEqual(saveCount, 1)
    }

    func testRuntimeControllerLockAfterCommitPreservesOutcome() async throws {
        let privateStateStore = SaveRefreshGatedPrivateStateStore()
        let controller = try saveRaceController(privateStateStore: privateStateStore)
        let facade = AtlasVaultRuntimeFacade(activationController: controller)
        try await facade.activate(activationRequest())
        await privateStateStore.gateNextClear()
        let request = mutationRequest()

        let save = Task { try await facade.apply(request) }
        let didEnter = await privateStateStore.waitUntilClearEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            save.cancel()
            await privateStateStore.openClear()
            _ = try? await save.value
            return
        }

        await facade.lock()
        await privateStateStore.openClear()

        let outcome = try await save.value
        let facadeStatus = await facade.status()
        let controllerState = await controller.state
        XCTAssertEqual(outcome, .committed)
        XCTAssertEqual(facadeStatus, .locked)
        XCTAssertEqual(controllerState, .locked)
    }

    func testStaleCommittedSaveCannotLockReactivatedSession() async throws {
        let privateStateStore = SaveRefreshGatedPrivateStateStore()
        let controller = try saveRaceController(privateStateStore: privateStateStore)
        let facade = AtlasVaultRuntimeFacade(activationController: controller)
        try await facade.activate(activationRequest())
        await privateStateStore.gateNextClear()
        let request = mutationRequest()

        let save = Task { try await facade.apply(request) }
        let didEnter = await privateStateStore.waitUntilClearEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            save.cancel()
            await privateStateStore.openClear()
            _ = try? await save.value
            return
        }

        await facade.lock()
        try await facade.activate(activationRequest())
        await privateStateStore.openClear()

        let outcome = try await save.value
        let facadeStatus = await facade.status()
        let controllerState = await controller.state
        _ = try await facade.privateState()
        XCTAssertEqual(outcome, .committed)
        XCTAssertEqual(facadeStatus, .unlocked)
        XCTAssertEqual(controllerState, .unlocked)
    }

    func testActivationCancellationLocksAndClears() async {
        let harness = FacadeHarness()
        await harness.setActivationSleepsUntilCancelled(true)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        let request = activationRequest()
        let activation = Task { try await facade.activate(request) }
        let didStart = await harness.waitUntilActivationStarted()
        XCTAssertTrue(didStart)
        guard didStart else {
            activation.cancel()
            _ = try? await activation.value
            return
        }

        activation.cancel()

        do {
            try await activation.value
            XCTFail("Expected cancellation")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .cancelled)
        }
        let status = await facade.status()
        XCTAssertEqual(status, .locked)
        let events = await harness.events()
        XCTAssertTrue(events.contains("cancelActivation"))
        XCTAssertTrue(events.contains("lock"))
    }

    func testCommittedStateRefreshFailureLocksAndReportsCommit() async throws {
        let harness = FacadeHarness()
        await harness.setSaveFailure(
            .committedStateUnavailable(
                AtlasVaultAtomicWriteResult(commitState: .committed)
            )
        )
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())

        do {
            _ = try await facade.apply(mutationRequest())
            XCTFail("Expected committed-state failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .committedStateUnavailable(.committed)
            )
        }
        let status = await facade.status()
        XCTAssertEqual(status, .locked)
    }

    func testExplicitLockDuringCommittedFailurePreservesCommitAwareError() async throws {
        let gate = FacadeGate()
        let harness = FacadeHarness(firstLockGate: gate)
        await harness.setSaveFailure(
            .committedStateUnavailable(
                AtlasVaultAtomicWriteResult(commitState: .committed)
            )
        )
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        let request = mutationRequest()

        let save = Task { try await facade.apply(request) }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            save.cancel()
            await gate.open()
            _ = try? await save.value
            return
        }

        await facade.lock()
        await gate.open()

        do {
            _ = try await save.value
            XCTFail("Expected committed-state failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .committedStateUnavailable(.committed)
            )
        }
        let status = await facade.status()
        XCTAssertEqual(status, .locked)
    }

    func testLockBeforeCommittedFailureDeliveryPreservesCommitAwareError() async throws {
        let gate = FacadeGate()
        let harness = FacadeHarness(saveFailureGate: gate)
        await harness.setSaveFailure(
            .committedStateUnavailable(
                AtlasVaultAtomicWriteResult(commitState: .committed)
            )
        )
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(activationRequest())
        let request = mutationRequest()

        let save = Task { try await facade.apply(request) }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            save.cancel()
            await gate.open()
            _ = try? await save.value
            return
        }

        await facade.lock()
        await gate.open()

        do {
            _ = try await save.value
            XCTFail("Expected committed-state failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .committedStateUnavailable(.committed)
            )
        }
        let status = await facade.status()
        XCTAssertEqual(status, .locked)
    }

    func testRuntimeGraphSaveEncryptsPersistsAndRehydratesUnderTempRoot() async throws {
        let rootURL = try temporaryRoot()
        addTeardownBlock { try? FileManager.default.removeItem(at: rootURL) }
        let recorder = RuntimeFacadeRecorder()
        let box = RuntimeStoreBox(store: emptyStore())
        let storeURL = try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)
            .localStoreURL(vaultID: Self.vaultID)
        try FileManager.default.createDirectory(
            at: storeURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        XCTAssertTrue(FileManager.default.createFile(atPath: storeURL.path, contents: Data()))
        let services: AtlasVaultRuntimeServices<
            RuntimeDirectoryPreparer,
            RuntimeLocalStoreIO
        > = AtlasVaultRuntimeFactory.makeServices(
            rootDirectoryProvider: RuntimeRootProvider(rootURL: rootURL, recorder: recorder),
            keyStore: RuntimeKeyStore(key: Self.vaultKey, recorder: recorder),
            directoryPreparer: RuntimeDirectoryPreparer(recorder: recorder),
            localStoreIO: RuntimeLocalStoreIO(box: box, recorder: recorder),
            atomicStoreWriter: RuntimeAtomicWriter(box: box, recorder: recorder),
            localStoreMerger: RuntimeMerger(recorder: recorder),
            recordSaver: RuntimeSaver(recorder: recorder),
            recordHydrator: RuntimeHydrator(recorder: recorder)
        )
        let facade = AtlasVaultRuntimeFacade.runtimeServices(services)
        let publicSnapshot = try publicSnapshot()
        let publicBefore = try encodedSnapshot(publicSnapshot)

        try await facade.activate(activationRequest(suppliedKey: nil))
        let outcome = try await facade.apply(
            AtlasVaultRuntimeMutationRequest(
                expectedVaultID: Self.vaultID,
                mutations: AtlasVaultMutationSet(
                    creates: [AtlasVaultCreateMutation(
                        payload: .savedSearch(savedSearchEnvelope()),
                        keyID: "fake-key-id"
                    )]
                )
            )
        )

        XCTAssertEqual(outcome, .committed)
        let snapshot = try await facade.privateState()
        XCTAssertEqual(snapshot.state.savedSearches.count, 1)
        XCTAssertEqual(snapshot.state.savedSearches[0].payload.name, Self.privateSentinel)
        let serializedStore = String(
            decoding: try AtlasVaultLocalStoreIO.encode(box.store),
            as: UTF8.self
        )
        XCTAssertFalse(serializedStore.contains(Self.privateSentinel))
        XCTAssertFalse(serializedStore.contains("saved_search"))
        XCTAssertEqual(try encodedSnapshot(publicSnapshot), publicBefore)
        XCTAssertTrue(recorder.events.contains("recordSaver"))
        XCTAssertTrue(recorder.events.contains("merger"))
        XCTAssertTrue(recorder.events.contains("atomicWrite"))
        XCTAssertGreaterThanOrEqual(recorder.events.filter { $0 == "hydrate" }.count, 2)
        XCTAssertFalse(try recursiveRelativePaths(at: rootURL).contains { $0.hasSuffix(".atlasvault") })
    }

    func testRuntimeControllerRejectsVaultMismatchBeforeSaverCall() async throws {
        let recorder = RuntimeFacadeRecorder()
        let scope = try AtlasVaultActivationScope(
            vaultID: Self.vaultID,
            loadEncryptedStore: { _ in Self.emptyStore() },
            hydrateRecords: { _, _ in AtlasVaultHydratedState() },
            saveMutations: { _, _ in
                recorder.record("save")
                return AtlasVaultAtomicWriteResult(commitState: .committed)
            }
        )
        let environment = AtlasVaultActivationEnvironment(
            loadStoredVaultKey: { _ in Self.vaultKey },
            resolveRootDirectory: { URL(fileURLWithPath: "/tmp/fake-runtime-facade-root") },
            makeScope: { _, _ in scope }
        )
        let facade = AtlasVaultRuntimeFacade(
            activationController: AtlasVaultActivationController(environment: environment)
        )
        try await facade.activate(activationRequest(suppliedKey: nil))

        do {
            _ = try await facade.apply(
                AtlasVaultRuntimeMutationRequest(
                    expectedVaultID: Self.otherVaultID,
                    mutations: AtlasVaultMutationSet()
                )
            )
            XCTFail("Expected session mismatch")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .sessionMismatch)
        }
        XCTAssertFalse(recorder.events.contains("save"))
    }

    func testRuntimeControllerHonorsQueuedCancellationBeforeSaverCall() async throws {
        let recorder = RuntimeFacadeRecorder()
        let scope = try AtlasVaultActivationScope(
            vaultID: Self.vaultID,
            loadEncryptedStore: { _ in Self.emptyStore() },
            hydrateRecords: { _, _ in AtlasVaultHydratedState() },
            saveMutations: { _, _ in
                recorder.record("save")
                return AtlasVaultAtomicWriteResult(commitState: .committed)
            }
        )
        let environment = AtlasVaultActivationEnvironment(
            loadStoredVaultKey: { _ in Self.vaultKey },
            resolveRootDirectory: { URL(fileURLWithPath: "/tmp/fake-runtime-facade-root") },
            makeScope: { _, _ in scope }
        )
        let controller = AtlasVaultActivationController(environment: environment)
        try await controller.activate(vaultID: Self.vaultID)
        let gate = FacadeGate()
        let vaultID = Self.vaultID
        let mutations = AtlasVaultMutationSet()
        let task = Task {
            await gate.enter()
            return try await controller.saveRuntimeMutations(
                mutations,
                expectedVaultID: vaultID
            )
        }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            task.cancel()
            await gate.open()
            _ = try? await task.value
            return
        }

        task.cancel()
        await gate.open()

        do {
            _ = try await task.value
            XCTFail("Expected cancellation before save")
        } catch {
            XCTAssertEqual(error as? AtlasVaultActivatedOperationError, .cancelled)
        }
        XCTAssertFalse(recorder.events.contains("save"))
    }

    func testRuntimeControllerCancellationAfterMergeStopsBeforeAtomicCommit() async throws {
        let rootURL = try temporaryRoot()
        addTeardownBlock { try? FileManager.default.removeItem(at: rootURL) }
        let recorder = RuntimeFacadeRecorder()
        let box = RuntimeStoreBox(store: Self.emptyStore())
        let storeURL = try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)
            .localStoreURL(vaultID: Self.vaultID)
        try FileManager.default.createDirectory(
            at: storeURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        XCTAssertTrue(FileManager.default.createFile(atPath: storeURL.path, contents: Data()))
        let services: AtlasVaultRuntimeServices<
            RuntimeDirectoryPreparer,
            RuntimeLocalStoreIO
        > = AtlasVaultRuntimeFactory.makeServices(
            rootDirectoryProvider: RuntimeRootProvider(
                rootURL: rootURL,
                recorder: recorder
            ),
            keyStore: RuntimeKeyStore(key: Self.vaultKey, recorder: recorder),
            directoryPreparer: RuntimeDirectoryPreparer(recorder: recorder),
            localStoreIO: RuntimeLocalStoreIO(box: box, recorder: recorder),
            atomicStoreWriter: RuntimeAtomicWriter(box: box, recorder: recorder),
            localStoreMerger: RuntimeCancellingMerger(recorder: recorder),
            recordSaver: RuntimeSaver(recorder: recorder),
            recordHydrator: RuntimeHydrator(recorder: recorder)
        )
        let controller = AtlasVaultActivationController(
            environment: .runtimeServices(services)
        )
        try await controller.activate(vaultID: Self.vaultID)
        let vaultID = Self.vaultID
        let mutations = AtlasVaultMutationSet(
            creates: [AtlasVaultCreateMutation(
                payload: .savedSearch(savedSearchEnvelope()),
                keyID: "fake-key-id"
            )]
        )

        let save = Task {
            try await controller.saveRuntimeMutations(
                mutations,
                expectedVaultID: vaultID
            )
        }

        do {
            _ = try await save.value
            XCTFail("Expected cancellation before atomic commit")
        } catch {
            XCTAssertEqual(error as? AtlasVaultActivatedOperationError, .cancelled)
        }
        let state = await controller.state
        XCTAssertEqual(state, .unlocked)
        XCTAssertTrue(recorder.events.contains("merger"))
        XCTAssertFalse(recorder.events.contains("atomicWrite"))
    }

    func testLockCancelsSynchronousPreCommitSaveBeforeAtomicWrite() async throws {
        let rootURL = try temporaryRoot()
        addTeardownBlock { try? FileManager.default.removeItem(at: rootURL) }
        let recorder = RuntimeFacadeRecorder()
        let box = RuntimeStoreBox(store: Self.emptyStore())
        let gate = RuntimePreCommitGate()
        let storeURL = try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)
            .localStoreURL(vaultID: Self.vaultID)
        try FileManager.default.createDirectory(
            at: storeURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        XCTAssertTrue(FileManager.default.createFile(atPath: storeURL.path, contents: Data()))
        let services: AtlasVaultRuntimeServices<
            RuntimeDirectoryPreparer,
            RuntimeLocalStoreIO
        > = AtlasVaultRuntimeFactory.makeServices(
            rootDirectoryProvider: RuntimeRootProvider(
                rootURL: rootURL,
                recorder: recorder
            ),
            keyStore: RuntimeKeyStore(key: Self.vaultKey, recorder: recorder),
            directoryPreparer: RuntimeDirectoryPreparer(recorder: recorder),
            localStoreIO: RuntimeLocalStoreIO(box: box, recorder: recorder),
            atomicStoreWriter: RuntimeAtomicWriter(box: box, recorder: recorder),
            localStoreMerger: RuntimeBlockingMerger(
                recorder: recorder,
                gate: gate
            ),
            recordSaver: RuntimeSaver(recorder: recorder),
            recordHydrator: RuntimeHydrator(recorder: recorder)
        )
        let facade = AtlasVaultRuntimeFacade.runtimeServices(services)
        try await facade.activate(activationRequest(suppliedKey: nil))
        let request = AtlasVaultRuntimeMutationRequest(
            expectedVaultID: Self.vaultID,
            mutations: AtlasVaultMutationSet(creates: [AtlasVaultCreateMutation(
                payload: .savedSearch(savedSearchEnvelope()),
                keyID: "fake-key-id"
            )])
        )

        let save = Task { try await facade.apply(request) }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)
        guard didEnter else {
            save.cancel()
            gate.open()
            _ = try? await save.value
            return
        }

        let lock = Task { await facade.lock() }
        let didObserveCancellation = await gate.waitUntilCancellationObserved()
        XCTAssertTrue(didObserveCancellation)
        gate.open()
        await lock.value

        do {
            _ = try await save.value
            XCTFail("Expected lock-priority cancellation")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .cancelled)
        }
        let status = await facade.status()
        XCTAssertEqual(status, .locked)
        XCTAssertTrue(recorder.events.contains("merger"))
        XCTAssertFalse(recorder.events.contains("atomicWrite"))
    }

    func testDiagnosticsContainNoKeyPathOrPrivateSentinel() async {
        let harness = FacadeHarness()
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        let request = activationRequest()
        let mutation = AtlasVaultRuntimeMutationRequest(
            expectedVaultID: Self.vaultID,
            mutations: AtlasVaultMutationSet(
                deletes: [AtlasVaultDeleteMutation(
                    recordID: Self.privateSentinel,
                    currentRevision: "private-revision",
                    keyID: "private-key-id"
                )]
            )
        )
        let rendered = [
            String(describing: facade),
            String(reflecting: facade),
            String(describing: request),
            String(reflecting: request),
            String(describing: mutation),
            String(reflecting: mutation),
            String(describing: AtlasVaultRuntimeStatus.saving),
            String(describing: AtlasVaultRuntimeFacadeError.saveFailed),
            String(describing: AtlasVaultSaveOutcome.committed),
        ].joined(separator: " ")
        let keyBase64 = Self.vaultKey.base64EncodedString()
        let keyHex = Self.vaultKey.map { String(format: "%02x", $0) }.joined()

        for forbidden in [
            Self.privateSentinel,
            Self.vaultID,
            keyBase64,
            keyHex,
            "/tmp/fake-private-path",
        ] {
            XCTAssertFalse(rendered.contains(forbidden), forbidden)
        }
    }

    func testSourceHasNoUIAppCacheKeychainFilesystemOrNetworkIntegration() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)
        for forbidden in [
            "SwiftUI",
            "ObservableObject",
            "@Published",
            "@State",
            "@Environment",
            "SearchViewModel",
            "AtlasLocalCache",
            "AtlasPublicLocalSnapshot",
            "URLSession",
            "@main",
            "UserDefaults",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "FileManager.default",
            "Data.write",
            "createFile",
        ] {
            XCTAssertFalse(source.contains(forbidden), "Unexpected source reference: \(forbidden)")
        }
    }

    private func activationRequest() -> AtlasVaultRuntimeActivationRequest {
        activationRequest(suppliedKey: Self.vaultKey)
    }

    private func activationRequest(
        suppliedKey: Data?
    ) -> AtlasVaultRuntimeActivationRequest {
        AtlasVaultRuntimeActivationRequest(
            vaultID: Self.vaultID,
            suppliedVaultKey: suppliedKey
        )
    }

    private func mutationRequest() -> AtlasVaultRuntimeMutationRequest {
        AtlasVaultRuntimeMutationRequest(
            expectedVaultID: Self.vaultID,
            mutations: AtlasVaultMutationSet(
                deletes: [AtlasVaultDeleteMutation(
                    recordID: "fake-record-id",
                    currentRevision: "fake-revision",
                    keyID: "fake-key-id"
                )]
            )
        )
    }

    private static func privateState(recordID: String) -> AtlasVaultHydratedState {
        AtlasVaultHydratedState(tombstones: [
            AtlasHydratedTombstone(metadata: AtlasHydratedRecordMetadata(
                id: recordID,
                revision: "fake-revision",
                parentRevision: nil,
                deleted: true,
                keyID: "fake-key-id"
            )),
        ])
    }

    private func privateState(recordID: String) -> AtlasVaultHydratedState {
        Self.privateState(recordID: recordID)
    }

    private static func emptyStore() -> AtlasVaultLocalStoreEnvelope {
        AtlasVaultLocalStoreEnvelope(
            storeID: "fake-store-id",
            createdAt: "2026-01-01T00:00:00Z",
            updatedAt: "2026-01-01T00:00:00Z",
            vaultMetadata: [:],
            records: []
        )
    }

    private func emptyStore() -> AtlasVaultLocalStoreEnvelope {
        Self.emptyStore()
    }

    private func temporaryRoot() throws -> URL {
        let rootURL = try AtlasVaultTestFileSystemSupport.canonicalTemporaryRoot()
            .appendingPathComponent(
                "atlasvault-runtime-facade-tests-\(UUID().uuidString)",
                isDirectory: true
            )
            .standardizedFileURL
        try FileManager.default.createDirectory(at: rootURL, withIntermediateDirectories: true)
        return rootURL
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

    private func savedSearchEnvelope() -> AtlasSavedSearchVaultRecordPayload {
        .savedSearch(
            AtlasSavedSearchVaultPayload(
                name: Self.privateSentinel,
                summary: "fake summary",
                request: AtlasSearchRequest(text: "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK")
            ),
            clientCreatedAt: "2026-01-01T00:00:00Z",
            clientUpdatedAt: "2026-01-01T00:00:00Z"
        )
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

    private func saveRaceController(
        privateStateStore: any AtlasVaultPrivateStateStoring
    ) throws -> AtlasVaultActivationController {
        let hydratedState = privateState(recordID: "save-race-private-record")
        let scope = try AtlasVaultActivationScope(
            vaultID: Self.vaultID,
            loadEncryptedStore: { _ in Self.emptyStore() },
            hydrateRecords: { _, _ in hydratedState },
            saveMutations: { _, _ in
                AtlasVaultAtomicWriteResult(commitState: .committed)
            }
        )
        let environment = AtlasVaultActivationEnvironment(
            loadStoredVaultKey: { _ in Self.vaultKey },
            resolveRootDirectory: {
                URL(fileURLWithPath: "/tmp/fake-runtime-facade-save-race-root")
            },
            makeScope: { _, _ in scope }
        )
        return AtlasVaultActivationController(
            environment: environment,
            privateStateStore: privateStateStore
        )
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/AtlasVaultRuntimeFacade.swift"),
            sourceDirectory.appendingPathComponent("../../../../apps/apple/Sources/AtlasUI/AtlasVaultRuntimeFacade.swift"),
        ].map(\.standardizedFileURL)
        guard let sourceURL = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) else {
            throw NSError(
                domain: "AtlasVaultRuntimeFacadeTests",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Could not find runtime facade source"]
            )
        }
        return sourceURL
    }
}

private actor FacadeHarness {
    private var recordedEvents: [String] = []
    private var activeVaultID: String?
    private var installedState: AtlasVaultHydratedState
    private var stateAfterSave: AtlasVaultHydratedState
    private var activationFailure: AtlasVaultActivationFailure?
    private var privateStateCancels = false
    private var saveFailure: AtlasVaultActivatedOperationError?
    private var saveResult = AtlasVaultAtomicWriteResult(commitState: .committed)
    private var saveGate: FacadeGate?
    private var saveFailureGate: FacadeGate?
    private var postCommitSaveGate: FacadeGate?
    private var firstLockGate: FacadeGate?
    private var activationSleepsUntilCancelled = false
    private var completedSaves = 0

    init(
        privateState: AtlasVaultHydratedState = AtlasVaultHydratedState(),
        postSaveState: AtlasVaultHydratedState? = nil,
        saveGate: FacadeGate? = nil,
        saveFailureGate: FacadeGate? = nil,
        postCommitSaveGate: FacadeGate? = nil,
        firstLockGate: FacadeGate? = nil
    ) {
        self.installedState = privateState
        self.stateAfterSave = postSaveState ?? privateState
        self.saveGate = saveGate
        self.saveFailureGate = saveFailureGate
        self.postCommitSaveGate = postCommitSaveGate
        self.firstLockGate = firstLockGate
    }

    nonisolated func environment() -> AtlasVaultRuntimeFacadeEnvironment {
        AtlasVaultRuntimeFacadeEnvironment(
            activate: { [self] vaultID, _ in
                try await activate(vaultID: vaultID)
            },
            cancelActivation: { [self] in
                await cancelActivation()
            },
            lock: { [self] in
                await lock()
            },
            privateState: { [self] in
                try await privateState()
            },
            save: { [self] mutations, expectedVaultID in
                try await save(mutations: mutations, expectedVaultID: expectedVaultID)
            }
        )
    }

    func setActivationFailure(_ failure: AtlasVaultActivationFailure?) {
        activationFailure = failure
    }

    func setActivationSleepsUntilCancelled(_ value: Bool) {
        activationSleepsUntilCancelled = value
    }

    func setSaveFailure(_ failure: AtlasVaultActivatedOperationError?) {
        saveFailure = failure
    }

    func setSaveResult(_ result: AtlasVaultAtomicWriteResult) {
        saveResult = result
    }

    func setSaveGate(_ gate: FacadeGate?) {
        saveGate = gate
    }

    func setPrivateStateCancels(_ value: Bool) {
        privateStateCancels = value
    }

    func events() -> [String] {
        recordedEvents
    }

    func successfulSaveCount() -> Int {
        completedSaves
    }

    func waitUntilActivationStarted() async -> Bool {
        for _ in 0..<500 {
            if recordedEvents.contains("activate") {
                return true
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return recordedEvents.contains("activate")
    }

    private func activate(vaultID: String) async throws {
        recordedEvents.append("activate")
        if activationSleepsUntilCancelled {
            try await Task.sleep(for: .seconds(5))
        }
        if let activationFailure {
            throw activationFailure
        }
        activeVaultID = vaultID
    }

    private func cancelActivation() -> Bool {
        recordedEvents.append("cancelActivation")
        activeVaultID = nil
        return true
    }

    private func lock() async {
        recordedEvents.append("lock")
        activeVaultID = nil
        if let firstLockGate {
            self.firstLockGate = nil
            await firstLockGate.enter()
        }
    }

    private func privateState() throws -> AtlasVaultHydratedState {
        recordedEvents.append("privateState")
        if privateStateCancels {
            throw CancellationError()
        }
        guard activeVaultID != nil else {
            throw AtlasVaultPrivateStateStoreError.unavailable
        }
        return installedState
    }

    private func save(
        mutations _: AtlasVaultMutationSet,
        expectedVaultID: String
    ) async throws -> AtlasVaultAtomicWriteResult {
        recordedEvents.append("save")
        if let saveGate {
            await saveGate.enter()
        }
        guard !Task.isCancelled else {
            throw AtlasVaultActivatedOperationError.cancelled
        }
        guard activeVaultID == expectedVaultID else {
            throw AtlasVaultActivatedOperationError.sessionMismatch
        }
        if let saveFailure {
            if case .committedStateUnavailable = saveFailure {
                if let saveFailureGate {
                    await saveFailureGate.enter()
                }
                activeVaultID = nil
            }
            throw saveFailure
        }
        installedState = stateAfterSave
        completedSaves += 1
        if let postCommitSaveGate {
            await postCommitSaveGate.enter()
        }
        return saveResult
    }
}

private actor FacadeGate {
    private var entered = false
    private var isOpen = false
    private var entryContinuation: CheckedContinuation<Void, Never>?

    func enter() async {
        entered = true
        guard !isOpen else {
            return
        }
        precondition(entryContinuation == nil, "FacadeGate does not support overlapping entries")
        await withCheckedContinuation { continuation in
            entryContinuation = continuation
        }
    }

    func waitUntilEntered() async -> Bool {
        for _ in 0..<500 {
            if entered {
                return true
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return entered
    }

    func open() {
        isOpen = true
        entryContinuation?.resume()
        entryContinuation = nil
    }
}

private actor SaveRefreshGatedPrivateStateStore: AtlasVaultPrivateStateStoring {
    private let store = AtlasVaultPrivateStateStore()
    private var shouldGateNextClear = false
    private var clearEntered = false
    private var clearContinuation: CheckedContinuation<Void, Never>?

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
        guard shouldGateNextClear else {
            return
        }
        shouldGateNextClear = false
        clearEntered = true
        await withCheckedContinuation { continuation in
            precondition(clearContinuation == nil, "Only one save refresh may be gated")
            clearContinuation = continuation
        }
    }

    func clearAll() async {
        await store.clearAll()
    }

    func gateNextClear() {
        precondition(clearContinuation == nil, "A save refresh is already gated")
        clearEntered = false
        shouldGateNextClear = true
    }

    func waitUntilClearEntered() async -> Bool {
        for _ in 0..<500 {
            if clearEntered {
                return true
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return clearEntered
    }

    func openClear() {
        clearContinuation?.resume()
        clearContinuation = nil
    }
}

private final class RuntimeFacadeRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var recordedEvents: [String] = []

    var events: [String] {
        lock.lock()
        defer { lock.unlock() }
        return recordedEvents
    }

    func record(_ event: String) {
        lock.lock()
        recordedEvents.append(event)
        lock.unlock()
    }
}

private final class RuntimeStoreBox: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: AtlasVaultLocalStoreEnvelope

    init(store: AtlasVaultLocalStoreEnvelope) {
        self.stored = store
    }

    var store: AtlasVaultLocalStoreEnvelope {
        get {
            lock.lock()
            defer { lock.unlock() }
            return stored
        }
        set {
            lock.lock()
            stored = newValue
            lock.unlock()
        }
    }
}

private struct RuntimeRootProvider: AtlasVaultRootDirectoryProviding {
    let rootURL: URL
    let recorder: RuntimeFacadeRecorder

    func rootDirectory() throws -> URL {
        recorder.record("root")
        return rootURL
    }
}

private struct RuntimeKeyStore: AtlasVaultKeyStore {
    let key: Data
    let recorder: RuntimeFacadeRecorder

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
    let recorder: RuntimeFacadeRecorder

    func prepareParentDirectory(for storeURL: URL, under rootDirectory: URL) throws {
        recorder.record("prepare")
    }
}

private enum RuntimeFacadeTestError: Error {
    case unexpectedDirectWrite
}

private struct RuntimeLocalStoreIO: AtlasVaultLocalStoreProviding {
    let box: RuntimeStoreBox
    let recorder: RuntimeFacadeRecorder

    func read(from url: URL) throws -> AtlasVaultLocalStoreEnvelope {
        recorder.record("read")
        return box.store
    }

    func write(_ store: AtlasVaultLocalStoreEnvelope, to url: URL, overwrite: Bool) throws {
        recorder.record("directWrite")
        throw RuntimeFacadeTestError.unexpectedDirectWrite
    }
}

private struct RuntimeAtomicWriter: AtlasVaultAtomicStoreWriting {
    let box: RuntimeStoreBox
    let recorder: RuntimeFacadeRecorder

    func write(
        _ store: AtlasVaultLocalStoreEnvelope,
        to destinationURL: URL,
        overwrite: Bool
    ) throws -> AtlasVaultAtomicWriteResult {
        recorder.record("atomicWrite")
        box.store = store
        return AtlasVaultAtomicWriteResult(commitState: .committed)
    }
}

private struct RuntimeMerger: AtlasVaultLocalStoreMerging {
    let recorder: RuntimeFacadeRecorder

    func merge(
        records incoming: [AtlasVaultEncryptedRecordEnvelope],
        into store: AtlasVaultLocalStoreEnvelope
    ) throws -> AtlasVaultLocalStoreEnvelope {
        recorder.record("merger")
        return try AtlasVaultLocalStoreMerger(
            updatedAtProvider: { "2026-01-02T00:00:00Z" }
        ).merge(records: incoming, into: store)
    }
}

private struct RuntimeCancellingMerger: AtlasVaultLocalStoreMerging {
    let recorder: RuntimeFacadeRecorder

    func merge(
        records incoming: [AtlasVaultEncryptedRecordEnvelope],
        into store: AtlasVaultLocalStoreEnvelope
    ) throws -> AtlasVaultLocalStoreEnvelope {
        recorder.record("merger")
        withUnsafeCurrentTask { task in
            task?.cancel()
        }
        return try AtlasVaultLocalStoreMerger(
            updatedAtProvider: { "2026-01-02T00:00:00Z" }
        ).merge(records: incoming, into: store)
    }
}

private struct RuntimeBlockingMerger: AtlasVaultLocalStoreMerging {
    let recorder: RuntimeFacadeRecorder
    let gate: RuntimePreCommitGate

    func merge(
        records incoming: [AtlasVaultEncryptedRecordEnvelope],
        into store: AtlasVaultLocalStoreEnvelope
    ) throws -> AtlasVaultLocalStoreEnvelope {
        recorder.record("merger")
        gate.blockUntilOpened()
        return try AtlasVaultLocalStoreMerger(
            updatedAtProvider: { "2026-01-02T00:00:00Z" }
        ).merge(records: incoming, into: store)
    }
}

private final class RuntimePreCommitGate: @unchecked Sendable {
    private let condition = NSCondition()
    private var entered = false
    private var cancellationObserved = false
    private var isOpen = false

    func blockUntilOpened() {
        condition.lock()
        entered = true
        condition.broadcast()
        while !isOpen {
            let isCancelled = withUnsafeCurrentTask { task in
                task?.isCancelled ?? false
            }
            if isCancelled {
                cancellationObserved = true
                condition.broadcast()
            }
            _ = condition.wait(until: Date(timeIntervalSinceNow: 0.01))
        }
        condition.unlock()
    }

    func waitUntilEntered() async -> Bool {
        for _ in 0..<500 {
            if snapshot(\.entered) {
                return true
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return snapshot(\.entered)
    }

    func waitUntilCancellationObserved() async -> Bool {
        for _ in 0..<500 {
            if snapshot(\.cancellationObserved) {
                return true
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return snapshot(\.cancellationObserved)
    }

    func open() {
        condition.lock()
        isOpen = true
        condition.broadcast()
        condition.unlock()
    }

    private func snapshot(_ keyPath: KeyPath<RuntimePreCommitGate, Bool>) -> Bool {
        condition.lock()
        defer { condition.unlock() }
        return self[keyPath: keyPath]
    }
}

private struct RuntimeSaver: AtlasVaultRecordSaving {
    let recorder: RuntimeFacadeRecorder

    func save(
        mutations: AtlasVaultMutationSet,
        session: AtlasVaultUnlockedSession
    ) throws -> [AtlasVaultEncryptedRecordEnvelope] {
        recorder.record("recordSaver")
        return try AtlasVaultRecordSaver().save(
            mutations: mutations,
            session: session
        )
    }
}

private struct RuntimeHydrator: AtlasVaultRecordHydrating {
    let recorder: RuntimeFacadeRecorder

    func hydrate(
        records: [AtlasVaultEncryptedRecordEnvelope],
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultHydratedState {
        recorder.record("hydrate")
        return try AtlasVaultRecordHydrator().hydrate(
            records: records,
            session: session
        )
    }
}
