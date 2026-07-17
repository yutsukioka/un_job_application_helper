import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultUnlockRequestCoordinatorTests: XCTestCase {
    fileprivate static let fakeVaultID = "00000000-0000-4000-8000-000000000042"
    fileprivate static let fakePassphrase = Data("FAKE_PASSPHRASE_DO_NOT_LEAK".utf8)
    fileprivate static let fakeRecoveryKey = Data("FAKE_RECOVERY_KEY_DO_NOT_LEAK".utf8)
    fileprivate static let fakeVaultKey = Data(repeating: 0x42, count: 32)
    private static let privateSentinels = [
        "FAKE_PASSPHRASE_DO_NOT_LEAK",
        "FAKE_RECOVERY_KEY_DO_NOT_LEAK",
        "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
        "FAKE_PRIVATE_JOB_KEY_DO_NOT_LEAK",
    ]

    func testConstructionInvokesNothing() async {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
        XCTAssertEqual(
            coordinator.description,
            "AtlasVaultUnlockRequestCoordinator(state: <redacted>)"
        )
    }

    func testPassphraseDispatchUsesInjectedDerivationAndClearsBuffer() async throws {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))

        try await coordinator.dispatch(request)

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.passphraseCalls, 1)
        XCTAssertTrue(snapshot.passphraseMatched)
        XCTAssertEqual(snapshot.recoveryCalls, 0)
        XCTAssertEqual(snapshot.activationCalls, 1)
        XCTAssertTrue(snapshot.activationKeyMatched)
        XCTAssertFalse(snapshot.activationUsedLocalKey)
        let isCleared = await buffer.isClearedForTesting
        XCTAssertTrue(isCleared)
    }

    func testEmptyPassphraseFailsBeforeDependenciesAndClearsBuffer() async {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Data())

        await assertDispatchThrows(.invalidRequest) {
            try await coordinator.dispatch(
                self.request(input: .passphrase(buffer))
            )
        }

        let isCleared = await buffer.isClearedForTesting
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
    }

    func testRecoveryKeyDispatchUsesInjectedDerivationAndClearsBuffer() async throws {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakeRecoveryKey)
        let request = request(input: .recoveryKey(buffer))

        try await coordinator.dispatch(request)

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.recoveryCalls, 1)
        XCTAssertTrue(snapshot.recoveryMatched)
        XCTAssertEqual(snapshot.passphraseCalls, 0)
        XCTAssertEqual(snapshot.activationCalls, 1)
        XCTAssertTrue(snapshot.activationKeyMatched)
        let isCleared = await buffer.isClearedForTesting
        XCTAssertTrue(isCleared)
    }

    func testLocalKeyDispatchCarriesNoSecret() async throws {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)

        try await coordinator.dispatch(request(input: .localKey))

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.passphraseCalls, 0)
        XCTAssertEqual(snapshot.recoveryCalls, 0)
        XCTAssertEqual(snapshot.activationCalls, 1)
        XCTAssertTrue(snapshot.activationUsedLocalKey)
        XCTAssertTrue(snapshot.vaultIDMatched)
    }

    func testSuppliedTestVaultKeyDispatchesAndClearsBuffer() async throws {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakeVaultKey)

        try await coordinator.dispatch(
            suppliedTestKeyRequest(buffer: buffer)
        )

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.passphraseCalls, 0)
        XCTAssertEqual(snapshot.recoveryCalls, 0)
        XCTAssertEqual(snapshot.activationCalls, 1)
        XCTAssertTrue(snapshot.activationKeyMatched)
        let isCleared = await buffer.isClearedForTesting
        XCTAssertTrue(isCleared)
    }

    func testRequestIsSingleUseAfterSuccess() async throws {
        let coordinator = makeCoordinator(spy: UnlockDependencySpy())
        let request = request(input: .localKey)

        try await coordinator.dispatch(request)
        await assertDispatchThrows(.alreadyUsed) {
            try await coordinator.dispatch(request)
        }
    }

    func testRequestCopiesShareSingleUseState() async throws {
        let coordinator = makeCoordinator(spy: UnlockDependencySpy())
        let original = request(input: .localKey)
        let copy = original

        try await coordinator.dispatch(original)
        await assertDispatchThrows(.alreadyUsed) {
            try await coordinator.dispatch(copy)
        }
    }

    func testConcurrentDoubleDispatchInvokesDependenciesOnce() async throws {
        let gate = UnlockGate(honorCancellation: true)
        let spy = UnlockDependencySpy(passphraseGate: gate)
        let coordinator = makeCoordinator(spy: spy)
        let request = request(
            input: .passphrase(
                AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
            )
        )

        let first = Task {
            try await coordinator.dispatch(request)
        }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)

        await assertDispatchThrows(.alreadyUsed) {
            try await coordinator.dispatch(request)
        }
        await gate.open()
        try await first.value

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.passphraseCalls, 1)
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testConcurrentCopiesStaySingleUseDuringStorageClaim() async throws {
        let claimGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(
            spy: spy,
            storageClaimGate: claimGate
        )
        let original = request(input: .localKey)
        let copy = original

        let first = Task {
            try await coordinator.dispatch(original)
        }
        let didReachClaimGate = await claimGate.waitUntilEntered()
        XCTAssertTrue(didReachClaimGate)

        await assertDispatchThrows(.alreadyUsed) {
            try await coordinator.dispatch(copy)
        }
        await claimGate.open()
        try await first.value

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testCancellationBeforeDispatchClearsBufferAndInvokesNothing() async {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))

        let didCancel = await coordinator.cancel(request)
        let isCleared = await waitUntilCleared(buffer)
        XCTAssertTrue(didCancel)
        XCTAssertTrue(isCleared)
        await assertDispatchThrows(.cancelled) {
            try await coordinator.dispatch(request)
        }
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
    }

    func testCancellationBeforeDispatchDoesNotWaitForSlowSecretCleanup() async {
        let clearGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let buffer = UnlockBlockingClearSecretBuffer(
            bytes: Self.fakePassphrase,
            clearGate: clearGate
        )
        let request = request(input: .passphrase(buffer))
        let cancellationResult = UnlockBooleanResultProbe()

        let cancellation = Task {
            let result = await coordinator.cancel(request)
            await cancellationResult.record(result)
            return result
        }

        let didStartCleanup = await clearGate.waitUntilEntered()
        let didReturnBeforeCleanup = await cancellationResult.waitUntilRecorded()
        XCTAssertTrue(didStartCleanup)
        XCTAssertTrue(didReturnBeforeCleanup)
        await assertDispatchThrows(.cancelled) {
            try await coordinator.dispatch(request)
        }
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())

        await clearGate.open()
        let didCancel = await cancellation.value
        XCTAssertTrue(didCancel)
    }

    func testCancellationDuringStorageClaimCannotBeLost() async {
        let claimGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(
            spy: spy,
            storageClaimGate: claimGate
        )
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReachClaimGate = await claimGate.waitUntilEntered()
        XCTAssertTrue(didReachClaimGate)

        let didCancel = await coordinator.cancel(request)
        XCTAssertTrue(didCancel)
        await claimGate.open()

        await assertTaskThrows(.cancelled, task: dispatch)
        let isCleared = await waitUntilCleared(buffer)
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
    }

    func testCallerCancellationDuringStorageClaimCannotBeLost() async {
        let claimGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(
            spy: spy,
            storageClaimGate: claimGate
        )
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReachClaimGate = await claimGate.waitUntilEntered()
        XCTAssertTrue(didReachClaimGate)

        dispatch.cancel()
        await claimGate.open()

        await assertTaskThrows(.cancelled, task: dispatch)
        let isCleared = await waitUntilCleared(buffer)
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
    }

    func testCancellationDuringDispatchClearsAndPreventsActivation() async {
        let gate = UnlockGate(honorCancellation: true)
        let spy = UnlockDependencySpy(passphraseGate: gate)
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))

        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didEnter = await gate.waitUntilEntered()
        let didCancel = await coordinator.cancel(request)
        XCTAssertTrue(didEnter)
        XCTAssertTrue(didCancel)

        await assertTaskThrows(.cancelled, task: dispatch)
        let isCleared = await buffer.isClearedForTesting
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testExplicitCancellationDoesNotWaitForSlowSecretCleanup() async {
        let derivationGate = UnlockGate(honorCancellation: false)
        let clearGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy(passphraseGate: derivationGate)
        let coordinator = makeCoordinator(spy: spy)
        let buffer = UnlockBlockingClearSecretBuffer(
            bytes: Self.fakePassphrase,
            clearGate: clearGate
        )
        let request = request(input: .passphrase(buffer))
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didStartDerivation = await derivationGate.waitUntilEntered()
        XCTAssertTrue(didStartDerivation)

        let cancellationResult = UnlockBooleanResultProbe()
        let cancellation = Task {
            let result = await coordinator.cancel(request)
            await cancellationResult.record(result)
            return result
        }
        let didStartCleanup = await clearGate.waitUntilEntered()
        let didReturnBeforeCleanup = await cancellationResult.waitUntilRecorded()
        let didObserveCancellation = await derivationGate
            .waitUntilCancellationObserved()

        XCTAssertTrue(didStartCleanup)
        XCTAssertTrue(didReturnBeforeCleanup)
        XCTAssertTrue(didObserveCancellation)
        await clearGate.open()
        await derivationGate.open()
        let didCancel = await cancellation.value
        XCTAssertTrue(didCancel)
        await assertTaskThrows(.cancelled, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testCancelledDispatchReleasesActiveStateBeforeSlowCleanupFinishes() async {
        let derivationGate = UnlockGate(honorCancellation: true)
        let clearGate = UnlockMultiWaiterGate()
        let spy = UnlockDependencySpy(passphraseGate: derivationGate)
        let coordinator = makeCoordinator(spy: spy)
        let buffer = UnlockBlockingEveryClearSecretBuffer(
            bytes: Self.fakePassphrase,
            clearGate: clearGate
        )
        let request = request(input: .passphrase(buffer))
        let dispatchResult = UnlockDispatchResultProbe()
        let dispatch = Task {
            let result: UnlockDispatchResult
            do {
                try await coordinator.dispatch(request)
                result = .succeeded
            } catch {
                result = .failed(
                    error as? AtlasVaultUnlockRequestError ?? .unlockFailed
                )
            }
            await dispatchResult.record(result)
        }

        let didStartDerivation = await derivationGate.waitUntilEntered()
        XCTAssertTrue(didStartDerivation)
        let didCancel = await coordinator.cancel(request)
        XCTAssertTrue(didCancel)

        let didStartCleanup = await clearGate.waitUntilEntered()
        let didFinishDispatchBeforeCleanup = await dispatchResult.waitUntilRecorded()
        let result = await dispatchResult.snapshot()
        XCTAssertTrue(didStartCleanup)
        XCTAssertTrue(didFinishDispatchBeforeCleanup)
        XCTAssertEqual(result, .failed(.cancelled))

        let didCancelAgain = await coordinator.cancel(request)
        XCTAssertFalse(didCancelAgain)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 0)

        await clearGate.open()
        await dispatch.value
    }

    func testCoordinatorCancellationBeforeOperationStartClearsClaimedBuffer() async {
        let operationStartGate = UnlockGate(honorCancellation: false)
        let coordinator = makeCoordinator(
            spy: UnlockDependencySpy(),
            operationStartGate: operationStartGate
        )
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReachStartGate = await operationStartGate.waitUntilEntered()
        XCTAssertTrue(didReachStartGate)

        let didCancel = await coordinator.cancel(request)

        XCTAssertTrue(didCancel)
        let isCleared = await waitUntilCleared(buffer)
        XCTAssertTrue(isCleared)
        await operationStartGate.open()
        await assertTaskThrows(.cancelled, task: dispatch)
    }

    func testCallerTaskCancellationClearsAndPreventsActivation() async {
        let gate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy(passphraseGate: gate)
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))

        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didEnter = await gate.waitUntilEntered()
        XCTAssertTrue(didEnter)
        dispatch.cancel()
        await gate.open()

        await assertTaskThrows(.cancelled, task: dispatch)
        let isCleared = await buffer.isClearedForTesting
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testCallerCancellationBeforeOperationStartClearsClaimedBuffer() async {
        let operationStartGate = UnlockGate(honorCancellation: false)
        let coordinator = makeCoordinator(
            spy: UnlockDependencySpy(),
            operationStartGate: operationStartGate
        )
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReachStartGate = await operationStartGate.waitUntilEntered()
        XCTAssertTrue(didReachStartGate)

        dispatch.cancel()

        let isCleared = await waitUntilCleared(buffer)
        XCTAssertTrue(isCleared)
        await operationStartGate.open()
        await assertTaskThrows(.cancelled, task: dispatch)
    }

    func testCallerCancellationBeforeHandlerInstallationPreventsActivation() async {
        let cancellationHandlerGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(
            spy: spy,
            beforeCancellationHandlerGate: cancellationHandlerGate
        )
        let request = request(input: .localKey)
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReachHandlerGate = await cancellationHandlerGate.waitUntilEntered()
        XCTAssertTrue(didReachHandlerGate)

        dispatch.cancel()
        await cancellationHandlerGate.open()

        await assertTaskThrows(.cancelled, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
    }

    func testAlreadyCancelledCallerCannotStartActivation() async {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let request = request(input: .localKey)
        let dispatch = Task {
            withUnsafeCurrentTask { task in
                task?.cancel()
            }
            try await coordinator.dispatch(request)
        }

        await assertTaskThrows(.cancelled, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
    }

    func testCallerCancellationReachesActivationButCommittedSuccessWins() async throws {
        let activationGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy(activationGate: activationGate)
        let coordinator = makeCoordinator(spy: spy)
        let request = request(input: .localKey)

        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didStartActivation = await activationGate.waitUntilEntered()
        XCTAssertTrue(didStartActivation)

        dispatch.cancel()
        let didObserveCancellation = await activationGate
            .waitUntilCancellationObserved()
        XCTAssertTrue(didObserveCancellation)
        await activationGate.open()
        try await dispatch.value

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testCallerCancellationDuringActivationCancelsSafeDependency() async {
        let activationGate = UnlockGate(honorCancellation: true)
        let spy = UnlockDependencySpy(activationGate: activationGate)
        let coordinator = makeCoordinator(spy: spy)
        let request = request(input: .localKey)
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didStartActivation = await activationGate.waitUntilEntered()
        XCTAssertTrue(didStartActivation)

        dispatch.cancel()

        let didObserveCancellation = await activationGate
            .waitUntilCancellationObserved()
        XCTAssertTrue(didObserveCancellation)
        await assertTaskThrows(.cancelled, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testExplicitCancellationDuringActivationCancelsSafeDependency() async {
        let activationGate = UnlockGate(honorCancellation: true)
        let spy = UnlockDependencySpy(activationGate: activationGate)
        let coordinator = makeCoordinator(spy: spy)
        let request = request(input: .localKey)
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didStartActivation = await activationGate.waitUntilEntered()
        XCTAssertTrue(didStartActivation)

        let didCancel = await coordinator.cancel(request)

        let didObserveCancellation = await activationGate
            .waitUntilCancellationObserved()
        XCTAssertTrue(didCancel)
        XCTAssertTrue(didObserveCancellation)
        await assertTaskThrows(.cancelled, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testExplicitCancellationReachesActivationButCommittedSuccessWins() async throws {
        let activationGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy(activationGate: activationGate)
        let coordinator = makeCoordinator(spy: spy)
        let request = request(input: .localKey)
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didStartActivation = await activationGate.waitUntilEntered()
        XCTAssertTrue(didStartActivation)

        let didCancel = await coordinator.cancel(request)
        let didObserveCancellation = await activationGate
            .waitUntilCancellationObserved()
        XCTAssertTrue(didCancel)
        XCTAssertTrue(didObserveCancellation)
        await activationGate.open()
        try await dispatch.value

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testNonOwningCoordinatorCannotCancelActiveDispatch() async {
        let derivationGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy(passphraseGate: derivationGate)
        let owner = makeCoordinator(spy: spy)
        let nonOwner = makeCoordinator(spy: UnlockDependencySpy())
        let request = request(
            input: .passphrase(
                AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
            )
        )
        let dispatch = Task {
            try await owner.dispatch(request)
        }
        let didEnter = await derivationGate.waitUntilEntered()
        XCTAssertTrue(didEnter)

        let nonOwnerDidCancel = await nonOwner.cancel(request)
        let ownerDidCancel = await owner.cancel(request)
        await derivationGate.open()

        XCTAssertFalse(nonOwnerDidCancel)
        XCTAssertTrue(ownerDidCancel)
        await assertTaskThrows(.cancelled, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testLateDerivationCompletionAfterCancellationCannotActivate() async {
        let gate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy(passphraseGate: gate)
        let coordinator = makeCoordinator(spy: spy)
        let request = request(
            input: .passphrase(
                AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
            )
        )

        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didEnter = await gate.waitUntilEntered()
        let didCancel = await coordinator.cancel(request)
        XCTAssertTrue(didEnter)
        XCTAssertTrue(didCancel)
        await gate.open()

        await assertTaskThrows(.cancelled, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testTimeoutExpiresRequestAndClearsSecret() async {
        let derivationGate = UnlockGate(honorCancellation: true)
        let sleeper = UnlockManualSleeper()
        let spy = UnlockDependencySpy(passphraseGate: derivationGate)
        let coordinator = makeCoordinator(spy: spy, sleeper: sleeper)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(
            input: .passphrase(buffer),
            timeout: .seconds(30)
        )

        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didEnter = await derivationGate.waitUntilEntered()
        let didSleep = await sleeper.waitUntilSleeping()
        XCTAssertTrue(didEnter)
        XCTAssertTrue(didSleep)
        await sleeper.fire()

        await assertTaskThrows(.expired, task: dispatch)
        let isCleared = await buffer.isClearedForTesting
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testTimeoutCancelsOperationBeforeSlowSecretCleanupCompletes() async {
        let derivationGate = UnlockGate(honorCancellation: false)
        let clearGate = UnlockGate(honorCancellation: false)
        let sleeper = UnlockManualSleeper()
        let spy = UnlockDependencySpy(passphraseGate: derivationGate)
        let coordinator = makeCoordinator(spy: spy, sleeper: sleeper)
        let buffer = UnlockBlockingClearSecretBuffer(
            bytes: Self.fakePassphrase,
            clearGate: clearGate
        )
        let request = request(
            input: .passphrase(buffer),
            timeout: .seconds(30)
        )
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didStartDerivation = await derivationGate.waitUntilEntered()
        let didStartSleeping = await sleeper.waitUntilSleeping()
        XCTAssertTrue(didStartDerivation)
        XCTAssertTrue(didStartSleeping)

        await sleeper.fire()

        let didStartCleanup = await clearGate.waitUntilEntered()
        let didObserveCancellation = await derivationGate
            .waitUntilCancellationObserved()
        XCTAssertTrue(didStartCleanup)
        XCTAssertTrue(didObserveCancellation)
        await clearGate.open()
        await derivationGate.open()
        await assertTaskThrows(.expired, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testTimeoutDuringActivationCancelsAndExpiresDispatch() async {
        let activationGate = UnlockGate(honorCancellation: true)
        let sleeper = UnlockManualSleeper()
        let spy = UnlockDependencySpy(activationGate: activationGate)
        let coordinator = makeCoordinator(spy: spy, sleeper: sleeper)
        let request = request(input: .localKey, timeout: .seconds(30))
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didStartActivation = await activationGate.waitUntilEntered()
        let didSleep = await sleeper.waitUntilSleeping()
        XCTAssertTrue(didStartActivation)
        XCTAssertTrue(didSleep)

        await sleeper.fire()

        await assertTaskThrows(.expired, task: dispatch)
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testCompletedActivationCannotBeRelabelledByLateTimeout() async throws {
        let sleeper = UnlockManualSleeper()
        let successCommitGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(
            spy: spy,
            sleeper: sleeper,
            successCommitGate: successCommitGate
        )
        let request = request(input: .localKey, timeout: .seconds(30))
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReachCommit = await successCommitGate.waitUntilEntered()
        let didSleep = await sleeper.waitUntilSleeping()
        XCTAssertTrue(didReachCommit)
        XCTAssertTrue(didSleep)

        await sleeper.fire()
        await Task.yield()
        await successCommitGate.open()

        try await dispatch.value
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testReturnedActivationWinsTimeoutBeforeCompletionRecording() async throws {
        let sleeper = UnlockManualSleeper()
        let activationReturnGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(
            spy: spy,
            sleeper: sleeper,
            activationReturnGate: activationReturnGate
        )
        let request = request(input: .localKey, timeout: .seconds(30))
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReturnFromActivation = await activationReturnGate.waitUntilEntered()
        let didSleep = await sleeper.waitUntilSleeping()
        XCTAssertTrue(didReturnFromActivation)
        XCTAssertTrue(didSleep)

        await sleeper.fire()
        let didObserveCancellation = await activationReturnGate
            .waitUntilCancellationObserved()
        XCTAssertTrue(didObserveCancellation)
        await activationReturnGate.open()

        try await dispatch.value
        await assertDispatchThrows(.alreadyUsed) {
            try await coordinator.dispatch(request)
        }
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testNonPositiveTimeoutExpiresBeforeLocalKeyActivation() async {
        for timeout in [Duration.zero, .seconds(-1)] {
            let spy = UnlockDependencySpy()
            let coordinator = makeCoordinator(spy: spy)
            let request = request(input: .localKey, timeout: timeout)

            await assertDispatchThrows(.expired) {
                try await coordinator.dispatch(request)
            }

            let snapshot = await spy.snapshot()
            XCTAssertEqual(snapshot, UnlockDependencySnapshot())
        }
    }

    func testNonPositiveTimeoutClearsSecretWithoutInvokingDependencies() async {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(
            input: .passphrase(buffer),
            timeout: .zero
        )

        await assertDispatchThrows(.expired) {
            try await coordinator.dispatch(request)
        }

        let isCleared = await waitUntilCleared(buffer)
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
    }

    func testTimeoutBeforeOperationStartClearsClaimedBuffer() async {
        let operationStartGate = UnlockGate(honorCancellation: false)
        let sleeper = UnlockManualSleeper()
        let coordinator = makeCoordinator(
            spy: UnlockDependencySpy(),
            sleeper: sleeper,
            operationStartGate: operationStartGate
        )
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(
            input: .passphrase(buffer),
            timeout: .seconds(30)
        )
        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReachStartGate = await operationStartGate.waitUntilEntered()
        let didSleep = await sleeper.waitUntilSleeping()
        XCTAssertTrue(didReachStartGate)
        XCTAssertTrue(didSleep)

        await sleeper.fire()

        let isCleared = await waitUntilCleared(buffer)
        XCTAssertTrue(isCleared)
        await operationStartGate.open()
        await assertTaskThrows(.expired, task: dispatch)
    }

    func testPassphraseFailureUsesGenericErrorAndClearsBuffer() async {
        let spy = UnlockDependencySpy(failure: .passphrase)
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)

        await assertDispatchThrows(.unlockFailed) {
            try await coordinator.dispatch(
                self.request(input: .passphrase(buffer))
            )
        }

        let isCleared = await buffer.isClearedForTesting
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testRecoveryFailureUsesSameGenericError() async {
        let spy = UnlockDependencySpy(failure: .recovery)
        let coordinator = makeCoordinator(spy: spy)

        await assertDispatchThrows(.unlockFailed) {
            try await coordinator.dispatch(
                self.request(
                    input: .recoveryKey(
                        AtlasVaultInMemorySecretBuffer(bytes: Self.fakeRecoveryKey)
                    )
                )
            )
        }
    }

    func testActivationFailureUsesGenericCoordinatorError() async {
        let spy = UnlockDependencySpy(failure: .activation)
        let coordinator = makeCoordinator(spy: spy)

        await assertDispatchThrows(.unlockFailed) {
            try await coordinator.dispatch(self.request(input: .localKey))
        }
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testDependencyLifecycleErrorsAreAlwaysGeneric() async {
        for lifecycleError in [
            AtlasVaultUnlockRequestError.cancelled,
            AtlasVaultUnlockRequestError.expired,
        ] {
            let dependencies = AtlasVaultUnlockRequestDependencies(
                derivePassphraseVaultKey: { _ in throw lifecycleError },
                deriveRecoveryVaultKey: { _ in throw lifecycleError },
                activate: { _ in throw lifecycleError }
            )

            let passphraseCoordinator = AtlasVaultUnlockRequestCoordinator(
                dependencies: dependencies
            )
            await assertDispatchThrows(.unlockFailed) {
                try await passphraseCoordinator.dispatch(
                    self.request(
                        input: .passphrase(
                            AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
                        )
                    )
                )
            }

            let recoveryCoordinator = AtlasVaultUnlockRequestCoordinator(
                dependencies: dependencies
            )
            await assertDispatchThrows(.unlockFailed) {
                try await recoveryCoordinator.dispatch(
                    self.request(
                        input: .recoveryKey(
                            AtlasVaultInMemorySecretBuffer(bytes: Self.fakeRecoveryKey)
                        )
                    )
                )
            }

            let activationCoordinator = AtlasVaultUnlockRequestCoordinator(
                dependencies: dependencies
            )
            await assertDispatchThrows(.unlockFailed) {
                try await activationCoordinator.dispatch(
                    self.request(input: .localKey)
                )
            }
        }
    }

    func testInvalidVaultIDFailsBeforeDependenciesAndClearsBuffer() async {
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(spy: spy)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = AtlasVaultUnlockRequest(
            vaultID: "../private-value",
            input: .passphrase(buffer)
        )

        await assertDispatchThrows(.invalidRequest) {
            try await coordinator.dispatch(request)
        }

        let isCleared = await waitUntilCleared(buffer)
        let snapshot = await spy.snapshot()
        XCTAssertTrue(isCleared)
        XCTAssertEqual(snapshot, UnlockDependencySnapshot())
    }

    func testInvalidDerivedKeyLengthFailsBeforeActivation() async {
        let spy = UnlockDependencySpy(derivedKey: Data(repeating: 0x11, count: 31))
        let coordinator = makeCoordinator(spy: spy)

        await assertDispatchThrows(.unlockFailed) {
            try await coordinator.dispatch(
                self.request(
                    input: .passphrase(
                        AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
                    )
                )
            )
        }
        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 0)
    }

    func testSuccessReleasesRequestSecretReference() async throws {
        let coordinator = makeCoordinator(spy: UnlockDependencySpy())
        weak var weakBuffer: AtlasVaultInMemorySecretBuffer?
        var request: AtlasVaultUnlockRequest?

        do {
            let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
            weakBuffer = buffer
            request = self.request(input: .passphrase(buffer))
            try await coordinator.dispatch(try XCTUnwrap(request))
        }

        XCTAssertNotNil(request)
        for _ in 0..<1_000 {
            if weakBuffer == nil {
                break
            }
            await Task.yield()
        }
        XCTAssertNil(weakBuffer)
    }

    func testCancelAfterCompletionIsSafeNoOp() async throws {
        let coordinator = makeCoordinator(spy: UnlockDependencySpy())
        let request = request(input: .localKey)

        try await coordinator.dispatch(request)

        let didCancel = await coordinator.cancel(request)
        XCTAssertFalse(didCancel)
    }

    func testCancellationCannotRelabelCompletedActivation() async throws {
        let successCommitGate = UnlockGate(honorCancellation: false)
        let spy = UnlockDependencySpy()
        let coordinator = makeCoordinator(
            spy: spy,
            successCommitGate: successCommitGate
        )
        let request = request(input: .localKey)

        let dispatch = Task {
            try await coordinator.dispatch(request)
        }
        let didReachCommit = await successCommitGate.waitUntilEntered()
        XCTAssertTrue(didReachCommit)

        let didCancel = await coordinator.cancel(request)
        XCTAssertFalse(didCancel)
        await successCommitGate.open()
        try await dispatch.value

        let snapshot = await spy.snapshot()
        XCTAssertEqual(snapshot.activationCalls, 1)
    }

    func testRequestBufferCoordinatorAndErrorsHaveRedactedDescriptions() async {
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Self.fakePassphrase)
        let request = request(input: .passphrase(buffer))
        let coordinator = makeCoordinator(spy: UnlockDependencySpy())
        let values: [Any] = [
            buffer,
            request,
            coordinator,
            AtlasVaultUnlockRequestError.invalidRequest,
            AtlasVaultUnlockRequestError.alreadyUsed,
            AtlasVaultUnlockRequestError.cancelled,
            AtlasVaultUnlockRequestError.expired,
            AtlasVaultUnlockRequestError.unlockFailed,
        ]

        for value in values {
            assertContainsNoPrivateSentinel(
                "\(String(describing: value)) \(String(reflecting: value))"
            )
        }
    }

    func testInputSourceDescriptionsDoNotInvokeSecretBufferDescriptions() {
        let secretBearingBuffer = UnlockSecretBearingDescriptionBuffer()
        let inputs: [AtlasVaultUnlockInputSource] = [
            .passphrase(secretBearingBuffer),
            .recoveryKey(secretBearingBuffer),
            .localKey,
        ]

        for input in inputs {
            let rendered = "\(String(describing: input)) \(String(reflecting: input))"
            XCTAssertEqual(
                rendered,
                "AtlasVaultUnlockInputSource(<redacted>) "
                    + "AtlasVaultUnlockInputSource(<redacted>)"
            )
            assertContainsNoPrivateSentinel(rendered)
        }
    }

    func testSecretBearingTypesAreNotPersistable() {
        XCTAssertFalse(AtlasVaultUnlockRequest.self is any Encodable.Type)
        XCTAssertFalse(AtlasVaultUnlockRequest.self is any Decodable.Type)
        XCTAssertFalse(AtlasVaultUnlockInputSource.self is any Encodable.Type)
        XCTAssertFalse(AtlasVaultUnlockInputSource.self is any Decodable.Type)
    }

    func testPresentationSnapshotContainsNoUnlockInput() {
        let snapshot = AtlasVaultPresentationAdapter().makeSnapshot(
            runtimeStatus: .locked
        )

        assertContainsNoPrivateSentinel(
            "\(String(describing: snapshot)) \(String(reflecting: snapshot))"
        )
        XCTAssertNil(snapshot.privateState)
    }

    func testPublicSnapshotIsUnchanged() async throws {
        let snapshot = try makePublicSnapshot()
        let before = try encodedPublicSnapshot(snapshot)
        let coordinator = makeCoordinator(spy: UnlockDependencySpy())

        try await coordinator.dispatch(request(input: .localKey))

        XCTAssertEqual(try encodedPublicSnapshot(snapshot), before)
    }

    func testSourceHasNoUIPlatformPersistenceOrEncodingCoupling() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)
        let forbidden = [
            "SwiftUI", "ObservableObject", "@Published", "@State", "@Environment",
            "UserDefaults", "FileManager", "Keychain", "SecItem", "LAContext",
            "LocalAuthentication", "URLSession", "Data.write", "createFile",
            "Codable", "AtlasLocalCache", "AtlasSearchViewModel",
            "AtlasPublicLocalSnapshot", ".atlasvault",
        ]

        for token in forbidden {
            XCTAssertFalse(source.contains(token), "Unexpected source token: \(token)")
        }
        XCTAssertFalse(source.contains("consumeSecret(buffer) { Data($0) }"))
    }

    private func request(
        input: AtlasVaultUnlockInputSource,
        timeout: Duration? = nil
    ) -> AtlasVaultUnlockRequest {
        AtlasVaultUnlockRequest(
            vaultID: Self.fakeVaultID,
            input: input,
            timeout: timeout
        )
    }

    private func suppliedTestKeyRequest(
        buffer: any AtlasVaultSecretBuffer
    ) -> AtlasVaultUnlockRequest {
        AtlasVaultUnlockRequest(
            vaultID: Self.fakeVaultID,
            suppliedTestVaultKey: buffer
        )
    }

    private func makeCoordinator(
        spy: UnlockDependencySpy,
        sleeper: UnlockManualSleeper? = nil,
        storageClaimGate: UnlockGate? = nil,
        beforeCancellationHandlerGate: UnlockGate? = nil,
        operationStartGate: UnlockGate? = nil,
        activationReturnGate: UnlockGate? = nil,
        successCommitGate: UnlockGate? = nil
    ) -> AtlasVaultUnlockRequestCoordinator {
        let dependencies = AtlasVaultUnlockRequestDependencies(
            derivePassphraseVaultKey: { bytes in
                try await spy.derivePassphrase(bytes)
            },
            deriveRecoveryVaultKey: { bytes in
                try await spy.deriveRecovery(bytes)
            },
            activate: { request in
                try await spy.activate(request)
            },
            sleep: { duration in
                if let sleeper {
                    try await sleeper.sleep(for: duration)
                } else {
                    try await Task.sleep(for: duration)
                }
            },
            afterStorageClaimTransition: {
                if let storageClaimGate {
                    try? await storageClaimGate.enter()
                }
            },
            beforeCancellationHandler: {
                if let beforeCancellationHandlerGate {
                    try? await beforeCancellationHandlerGate.enter()
                }
            },
            beforeOperationStart: {
                if let operationStartGate {
                    try? await operationStartGate.enter()
                }
            },
            afterActivationReturn: {
                if let activationReturnGate {
                    try? await activationReturnGate.enter()
                }
            },
            beforeSuccessCommit: {
                if let successCommitGate {
                    try? await successCommitGate.enter()
                }
            }
        )
        return AtlasVaultUnlockRequestCoordinator(dependencies: dependencies)
    }

    private func waitUntilCleared(
        _ buffer: AtlasVaultInMemorySecretBuffer
    ) async -> Bool {
        for _ in 0..<1_000 {
            if await buffer.isClearedForTesting {
                return true
            }
            await Task.yield()
        }
        return await buffer.isClearedForTesting
    }

    private func assertDispatchThrows(
        _ expected: AtlasVaultUnlockRequestError,
        operation: () async throws -> Void,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        do {
            try await operation()
            XCTFail("Expected \(expected)", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultUnlockRequestError,
                expected,
                file: file,
                line: line
            )
            assertContainsNoPrivateSentinel(
                "\(String(describing: error)) \(String(reflecting: error))",
                file: file,
                line: line
            )
        }
    }

    private func assertTaskThrows(
        _ expected: AtlasVaultUnlockRequestError,
        task: Task<Void, Error>,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        await assertDispatchThrows(expected, operation: {
            try await task.value
        }, file: file, line: line)
    }

    private func assertContainsNoPrivateSentinel(
        _ rendered: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        for sentinel in Self.privateSentinels {
            XCTAssertFalse(rendered.contains(sentinel), sentinel, file: file, line: line)
        }
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

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent(
                "../../Sources/AtlasUI/AtlasVaultUnlockRequestCoordinator.swift"
            ),
            sourceDirectory.appendingPathComponent(
                "../../../../apps/apple/Sources/AtlasUI/AtlasVaultUnlockRequestCoordinator.swift"
            ),
        ].map(\.standardizedFileURL)
        guard let sourceURL = candidates.first(where: {
            FileManager.default.fileExists(atPath: $0.path)
        }) else {
            throw NSError(
                domain: "AtlasVaultUnlockRequestCoordinatorTests",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Could not find source file"]
            )
        }
        return sourceURL
    }
}

private enum UnlockDependencyFailure: Error {
    case passphrase
    case recovery
    case activation
}

private actor UnlockBlockingClearSecretBuffer: AtlasVaultSecretBuffer {
    private var bytes: Data?
    private var shouldBlockClear = true
    private let clearGate: UnlockGate

    init(bytes: Data, clearGate: UnlockGate) {
        self.bytes = bytes
        self.clearGate = clearGate
    }

    func takeSecretBytes() async throws -> Data {
        guard let bytes else {
            throw AtlasVaultSecretBufferError.unavailable
        }
        self.bytes = nil
        return bytes
    }

    func clear() async {
        bytes = nil
        guard shouldBlockClear else { return }
        shouldBlockClear = false
        try? await clearGate.enter()
    }
}

private actor UnlockBlockingEveryClearSecretBuffer: AtlasVaultSecretBuffer {
    private var bytes: Data?
    private let clearGate: UnlockMultiWaiterGate

    init(bytes: Data, clearGate: UnlockMultiWaiterGate) {
        self.bytes = bytes
        self.clearGate = clearGate
    }

    func takeSecretBytes() async throws -> Data {
        guard let bytes else {
            throw AtlasVaultSecretBufferError.unavailable
        }
        self.bytes = nil
        return bytes
    }

    func clear() async {
        bytes = nil
        await clearGate.enter()
    }
}

private actor UnlockSecretBearingDescriptionBuffer:
    AtlasVaultSecretBuffer,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    func takeSecretBytes() async throws -> Data {
        Data()
    }

    func clear() async {}

    nonisolated var description: String {
        "FAKE_PASSPHRASE_DO_NOT_LEAK"
    }

    nonisolated var debugDescription: String {
        description
    }
}

private actor UnlockBooleanResultProbe {
    private var result: Bool?

    func record(_ result: Bool) {
        self.result = result
    }

    func waitUntilRecorded() async -> Bool {
        for _ in 0..<1_000 {
            if result != nil {
                return true
            }
            await Task.yield()
        }
        return result != nil
    }
}

private enum UnlockDispatchResult: Equatable, Sendable {
    case succeeded
    case failed(AtlasVaultUnlockRequestError)
}

private actor UnlockDispatchResultProbe {
    private var result: UnlockDispatchResult?

    func record(_ result: UnlockDispatchResult) {
        self.result = result
    }

    func waitUntilRecorded() async -> Bool {
        for _ in 0..<1_000 {
            if result != nil {
                return true
            }
            await Task.yield()
        }
        return result != nil
    }

    func snapshot() -> UnlockDispatchResult? {
        result
    }
}

private struct UnlockDependencySnapshot: Equatable, Sendable {
    var passphraseCalls = 0
    var recoveryCalls = 0
    var activationCalls = 0
    var passphraseMatched = false
    var recoveryMatched = false
    var activationKeyMatched = false
    var activationUsedLocalKey = false
    var vaultIDMatched = false
}

private actor UnlockDependencySpy {
    private let derivedKey: Data
    private let failure: UnlockDependencyFailure?
    private let passphraseGate: UnlockGate?
    private let activationGate: UnlockGate?
    private var value = UnlockDependencySnapshot()

    init(
        derivedKey: Data = AtlasVaultUnlockRequestCoordinatorTests.fakeVaultKey,
        failure: UnlockDependencyFailure? = nil,
        passphraseGate: UnlockGate? = nil,
        activationGate: UnlockGate? = nil
    ) {
        self.derivedKey = derivedKey
        self.failure = failure
        self.passphraseGate = passphraseGate
        self.activationGate = activationGate
    }

    func derivePassphrase(_ bytes: Data) async throws -> Data {
        value.passphraseCalls += 1
        value.passphraseMatched = bytes == AtlasVaultUnlockRequestCoordinatorTests.fakePassphrase
        if let passphraseGate {
            try await passphraseGate.enter()
        }
        if failure == .passphrase {
            throw UnlockDependencyFailure.passphrase
        }
        return derivedKey
    }

    func deriveRecovery(_ bytes: Data) async throws -> Data {
        value.recoveryCalls += 1
        value.recoveryMatched = bytes == AtlasVaultUnlockRequestCoordinatorTests.fakeRecoveryKey
        if failure == .recovery {
            throw UnlockDependencyFailure.recovery
        }
        return derivedKey
    }

    func activate(_ request: AtlasVaultRuntimeActivationRequest) async throws {
        value.activationCalls += 1
        value.vaultIDMatched = request.vaultID == AtlasVaultUnlockRequestCoordinatorTests.fakeVaultID
        value.activationUsedLocalKey = request.suppliedVaultKeyValue == nil
        value.activationKeyMatched = request.suppliedVaultKeyValue
            == AtlasVaultUnlockRequestCoordinatorTests.fakeVaultKey
        if let activationGate {
            try await activationGate.enter()
        }
        if failure == .activation {
            throw AtlasVaultRuntimeFacadeError.activationFailed(.authenticationFailed)
        }
    }

    func snapshot() -> UnlockDependencySnapshot {
        value
    }
}

private actor UnlockGate {
    private let honorCancellation: Bool
    private var entered = false
    private var isOpen = false
    private var cancellationObserved = false
    private var continuation: CheckedContinuation<Void, Error>?

    init(honorCancellation: Bool) {
        self.honorCancellation = honorCancellation
    }

    func enter() async throws {
        entered = true
        guard !isOpen else {
            return
        }
        precondition(continuation == nil, "UnlockGate supports one waiter")
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation {
                (continuation: CheckedContinuation<Void, Error>) in
                self.continuation = continuation
                if Task.isCancelled {
                    observeCancellation()
                }
            }
        } onCancel: {
            Task { await self.observeCancellation() }
        }
    }

    func waitUntilEntered() async -> Bool {
        for _ in 0..<1_000 {
            if entered {
                return true
            }
            await Task.yield()
        }
        return entered
    }

    func open() {
        isOpen = true
        continuation?.resume()
        continuation = nil
    }

    func waitUntilCancellationObserved() async -> Bool {
        for _ in 0..<1_000 {
            if cancellationObserved {
                return true
            }
            await Task.yield()
        }
        return cancellationObserved
    }

    private func observeCancellation() {
        cancellationObserved = true
        if honorCancellation {
            cancelWaiter()
        }
    }

    private func cancelWaiter() {
        continuation?.resume(throwing: CancellationError())
        continuation = nil
    }
}

private actor UnlockMultiWaiterGate {
    private var enteredCount = 0
    private var isOpen = false
    private var continuations: [CheckedContinuation<Void, Never>] = []

    func enter() async {
        enteredCount += 1
        guard !isOpen else { return }
        await withCheckedContinuation { continuation in
            if isOpen {
                continuation.resume()
            } else {
                continuations.append(continuation)
            }
        }
    }

    func waitUntilEntered() async -> Bool {
        for _ in 0..<1_000 {
            if enteredCount > 0 {
                return true
            }
            await Task.yield()
        }
        return enteredCount > 0
    }

    func open() {
        isOpen = true
        let waiting = continuations
        continuations.removeAll(keepingCapacity: false)
        for continuation in waiting {
            continuation.resume()
        }
    }
}

private actor UnlockManualSleeper {
    private let gate = UnlockGate(honorCancellation: true)
    private var durations: [Duration] = []

    func sleep(for duration: Duration) async throws {
        durations.append(duration)
        try await gate.enter()
    }

    func waitUntilSleeping() async -> Bool {
        for _ in 0..<1_000 {
            if !durations.isEmpty {
                return true
            }
            await Task.yield()
        }
        return !durations.isEmpty
    }

    func fire() async {
        await gate.open()
    }
}
