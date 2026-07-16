import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultLifecycleCoordinatorTests: XCTestCase {
    private static let privateSentinel = "FAKE_PRIVATE_LIFECYCLE_SENTINEL_DO_NOT_LEAK"
    private static let keySentinel = "FAKE_LIFECYCLE_KEY_SENTINEL_DO_NOT_LEAK"

    func testConstructionInvokesNoRuntimeOrTimeDependency() async {
        let harness = LifecycleHarness(status: .locked)

        _ = harness.coordinator(policy: .immediate)

        let runtimeEvents = await harness.runtime.events()
        let timeEvents = await harness.time.events()
        XCTAssertEqual(runtimeEvents, [])
        XCTAssertEqual(timeEvents, [])
    }

    func testPublicFacadeInitializerIsSideEffectFree() async {
        let facadeHarness = LifecycleFacadeHarness()
        let facade = AtlasVaultRuntimeFacade(environment: facadeHarness.environment())
        let time = LifecycleManualTime(honorCancellation: true)

        _ = AtlasVaultLifecycleCoordinator(
            runtimeFacade: facade,
            lockPolicy: .immediate,
            clock: time,
            sleeper: time
        )

        let facadeEvents = await facadeHarness.events()
        let timeEvents = await time.events()
        XCTAssertEqual(facadeEvents, [])
        XCTAssertEqual(timeEvents, [])
    }

    func testActiveEventDoesNotActivateOrCallRuntime() async {
        let harness = LifecycleHarness(status: .locked)
        let coordinator = harness.coordinator(policy: .immediate)

        await coordinator.handle(.didBecomeActive)

        let runtimeEvents = await harness.runtime.events()
        let status = await coordinator.status()
        XCTAssertEqual(runtimeEvents, [])
        XCTAssertEqual(status.lastEvent, .didBecomeActive)
    }

    func testImmediateBackgroundLocksImmediately() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(policy: .immediate)

        await coordinator.handle(.didEnterBackground)

        let events = await harness.runtime.events()
        let status = await harness.runtime.currentStatus()
        XCTAssertEqual(events, ["lock"])
        XCTAssertEqual(status, .locked)
    }

    func testGraceBackgroundLocksOnlyAfterManualClockAdvance() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: true)
        )

        await coordinator.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        let initialLockCount = await harness.runtime.lockCount()
        XCTAssertTrue(didSchedule)
        XCTAssertEqual(initialLockCount, 0)

        await harness.time.advance(by: .seconds(29))
        await drainTasks()
        let earlyLockCount = await harness.runtime.lockCount()
        XCTAssertEqual(earlyLockCount, 0)

        await harness.time.advance(by: .seconds(1))
        let didLock = await harness.runtime.waitUntilLockCount(1)
        let status = await harness.runtime.currentStatus()
        XCTAssertTrue(didLock)
        XCTAssertEqual(status, .locked)
    }

    func testForegroundCancelsGracePeriod() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: true)
        )
        await coordinator.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)

        await coordinator.handle(.didBecomeActive)
        await harness.time.advance(by: .seconds(30))
        await drainTasks()

        let lockCount = await harness.runtime.lockCount()
        let status = await coordinator.status()
        XCTAssertEqual(lockCount, 0)
        XCTAssertFalse(status.hasPendingGraceLock)
    }

    func testForegroundLocksWhenSuspendedGraceDeadlineAlreadyExpired() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: true)
        )
        await coordinator.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)

        await harness.time.elapseWithoutResumingSleepers(by: .seconds(30))
        await coordinator.handle(.didBecomeActive)

        let lockCount = await harness.runtime.lockCount()
        let status = await coordinator.status()
        XCTAssertEqual(lockCount, 1)
        XCTAssertFalse(status.hasPendingGraceLock)
    }

    func testGracePolicyCanRetainTimerOnForeground() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: false)
        )
        await coordinator.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)

        await coordinator.handle(.didBecomeActive)
        await harness.time.advance(by: .seconds(30))
        await drainTasks()

        let lockCount = await harness.runtime.lockCount()
        let status = await coordinator.status()
        XCTAssertEqual(lockCount, 1)
        XCTAssertFalse(status.hasPendingGraceLock)
    }

    func testRetainedGracePolicySurvivesForegroundWhileStatusIsSuspended() async {
        let statusGate = LifecycleGate()
        let harness = LifecycleHarness(status: .unlocked, statusGate: statusGate)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: false)
        )
        let background = Task { await coordinator.handle(.didEnterBackground) }
        let didEnterStatus = await statusGate.waitUntilEntered()
        XCTAssertTrue(didEnterStatus)

        await coordinator.handle(.didBecomeActive)
        await statusGate.open()
        await background.value

        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)
        await harness.time.advance(by: .seconds(30))
        let didLock = await harness.runtime.waitUntilLockCount(1)
        XCTAssertTrue(didLock)
    }

    func testCancellableGracePolicyDropsSchedulingAfterForegroundRace() async {
        let statusGate = LifecycleGate()
        let harness = LifecycleHarness(status: .unlocked, statusGate: statusGate)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: true)
        )
        let background = Task { await coordinator.handle(.didEnterBackground) }
        let didEnterStatus = await statusGate.waitUntilEntered()
        XCTAssertTrue(didEnterStatus)

        await coordinator.handle(.didBecomeActive)
        await statusGate.open()
        await background.value
        await drainTasks()

        let sleeperCount = await harness.time.sleeperCount()
        let lockCount = await harness.runtime.lockCount()
        XCTAssertEqual(sleeperCount, 0)
        XCTAssertEqual(lockCount, 0)
    }

    func testProtectedDataUnavailableOverridesGracePeriod() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: true)
        )
        await coordinator.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)

        await coordinator.handle(.protectedDataBecameUnavailable)

        let lockCount = await harness.runtime.lockCount()
        let status = await coordinator.status()
        XCTAssertEqual(lockCount, 1)
        XCTAssertFalse(status.hasPendingGraceLock)
    }

    func testTerminationLocksAndIgnoresLaterEvents() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(policy: .immediate)

        await coordinator.handle(.willTerminate)
        await coordinator.handle(.didBecomeActive)
        await coordinator.handle(.didEnterBackground)

        let events = await harness.runtime.events()
        let status = await coordinator.status()
        XCTAssertEqual(events, ["lock"])
        XCTAssertEqual(status.lastEvent, .willTerminate)
    }

    func testRepeatedBackgroundEventIsIdempotent() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(policy: .immediate)

        await coordinator.handle(.didEnterBackground)
        await coordinator.handle(.didEnterBackground)

        let lockCount = await harness.runtime.lockCount()
        XCTAssertEqual(lockCount, 1)
    }

    func testRepeatedLockRequestsRemainSafe() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(policy: .immediate)

        await coordinator.handle(.didEnterBackground)
        await coordinator.handle(.protectedDataBecameUnavailable)
        await coordinator.handle(.willTerminate)

        let lockCount = await harness.runtime.lockCount()
        let status = await harness.runtime.currentStatus()
        XCTAssertEqual(lockCount, 3)
        XCTAssertEqual(status, .locked)
    }

    func testInactiveCancelsOnlyPendingActivation() async {
        let harness = LifecycleHarness(status: .activating)
        let coordinator = harness.coordinator(policy: .immediate)

        await coordinator.handle(.willResignActive)

        let events = await harness.runtime.events()
        let status = await harness.runtime.currentStatus()
        XCTAssertEqual(events, ["cancelActivation"])
        XCTAssertEqual(status, .locked)
    }

    func testInactiveLeavesUnlockedRuntimeAloneByDefault() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(policy: .immediate)

        await coordinator.handle(.willResignActive)

        let events = await harness.runtime.events()
        let lockCount = await harness.runtime.lockCount()
        let status = await harness.runtime.currentStatus()
        XCTAssertEqual(events, ["cancelActivation"])
        XCTAssertEqual(lockCount, 0)
        XCTAssertEqual(status, .unlocked)
    }

    func testStrictInactivePolicyLocksUnlockedRuntime() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(policy: .immediate, lockOnInactive: true)

        await coordinator.handle(.willResignActive)

        let events = await harness.runtime.events()
        let status = await harness.runtime.currentStatus()
        XCTAssertEqual(events, ["cancelActivation", "lock"])
        XCTAssertEqual(status, .locked)
    }

    func testGraceBackgroundCancelsActivationWithoutSchedulingTimer() async {
        let harness = LifecycleHarness(status: .activating)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: true)
        )

        await coordinator.handle(.didEnterBackground)

        let events = await harness.runtime.events()
        let sleeperCount = await harness.time.sleeperCount()
        XCTAssertEqual(events, ["cancelActivation"])
        XCTAssertEqual(sleeperCount, 0)
    }

    func testGraceBackgroundFailsClosedWhenActivationRemainsTransient() async {
        let harness = LifecycleHarness(
            status: .activating,
            cancellationResults: [false, false]
        )
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(30), cancelOnActive: true)
        )

        await coordinator.handle(.didEnterBackground)

        let events = await harness.runtime.events()
        let status = await harness.runtime.currentStatus()
        let sleeperCount = await harness.time.sleeperCount()
        XCTAssertEqual(events, ["cancelActivation", "status", "cancelActivation", "lock"])
        XCTAssertEqual(status, .locked)
        XCTAssertEqual(sleeperCount, 0)
    }

    func testSavingStateReceivesBoundedGraceThenLocks() async {
        let harness = LifecycleHarness(status: .saving)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(5), cancelOnActive: true)
        )

        await coordinator.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)
        await harness.time.advance(by: .seconds(5))

        let didLock = await harness.runtime.waitUntilLockCount(1)
        let status = await harness.runtime.currentStatus()
        XCTAssertTrue(didLock)
        XCTAssertEqual(status, .locked)
    }

    func testStaleCancelledTimerCannotLockLaterSession() async {
        let harness = LifecycleHarness(status: .unlocked, honorTimerCancellation: false)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(10), cancelOnActive: true)
        )
        await coordinator.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)

        await coordinator.handle(.didBecomeActive)
        await harness.runtime.setStatus(.unlocked)
        await harness.time.advance(by: .seconds(10))
        await drainTasks()

        let lockCount = await harness.runtime.lockCount()
        let status = await harness.runtime.currentStatus()
        XCTAssertEqual(lockCount, 0)
        XCTAssertEqual(status, .unlocked)
    }

    func testPendingGraceTaskRetainsCoordinatorUntilScheduledLockRuns() async {
        let harness = LifecycleHarness(status: .unlocked)
        var coordinator: AtlasVaultLifecycleCoordinator? = harness.coordinator(
            policy: .afterGracePeriod(.seconds(10), cancelOnActive: true)
        )
        weak let weakCoordinator = coordinator
        await coordinator?.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)

        coordinator = nil
        XCTAssertNotNil(weakCoordinator)
        await harness.time.advance(by: .seconds(10))

        let didLock = await harness.runtime.waitUntilLockCount(1)
        XCTAssertTrue(didLock)
    }

    func testEventOrderingLetsSecurityEventWinOverForeground() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(10), cancelOnActive: true)
        )
        await coordinator.handle(.didEnterBackground)
        let didSchedule = await harness.time.waitUntilSleeperCount(1)
        XCTAssertTrue(didSchedule)

        await coordinator.handle(.didBecomeActive)
        await coordinator.handle(.protectedDataBecameUnavailable)

        let lockCount = await harness.runtime.lockCount()
        let status = await coordinator.status()
        XCTAssertEqual(lockCount, 1)
        XCTAssertEqual(status.lastEvent, .protectedDataBecameUnavailable)
    }

    func testNonPositiveGraceFailsClosedWithoutSleeping() async {
        let harness = LifecycleHarness(status: .unlocked)
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.zero, cancelOnActive: true)
        )

        await coordinator.handle(.didEnterBackground)

        let lockCount = await harness.runtime.lockCount()
        let events = await harness.runtime.events()
        let sleeperCount = await harness.time.sleeperCount()
        XCTAssertEqual(lockCount, 1)
        XCTAssertEqual(events, ["lock"])
        XCTAssertEqual(sleeperCount, 0)
    }

    func testManualSleeperCancelsWhenTaskIsAlreadyCancelled() async {
        let startGate = LifecycleGate()
        let time = LifecycleManualTime(honorCancellation: true)
        let sleeper = Task {
            await startGate.enter()
            try await time.sleep(until: .seconds(10))
        }
        let didReachGate = await startGate.waitUntilEntered()
        XCTAssertTrue(didReachGate)
        sleeper.cancel()
        await startGate.open()

        do {
            try await sleeper.value
            XCTFail("Expected cancelled sleeper")
        } catch is CancellationError {
            // Expected.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
        let sleeperCount = await time.sleeperCount()
        XCTAssertEqual(sleeperCount, 0)
    }

    func testDescriptionsAndFailureAreNonSensitive() async {
        let harness = LifecycleHarness(status: .unlocked)
        await harness.time.failNextSleep()
        let coordinator = harness.coordinator(
            policy: .afterGracePeriod(.seconds(5), cancelOnActive: true)
        )

        await coordinator.handle(.didEnterBackground)
        let didLock = await harness.runtime.waitUntilLockCount(1)
        XCTAssertTrue(didLock)

        let status = await coordinator.status()
        XCTAssertEqual(
            String(describing: coordinator),
            "AtlasVaultLifecycleCoordinator(state: <redacted>)"
        )
        XCTAssertEqual(
            String(reflecting: coordinator),
            "AtlasVaultLifecycleCoordinator(state: <redacted>)"
        )
        XCTAssertEqual(
            String(describing: AtlasVaultLifecycleEvent.didEnterBackground),
            "didEnterBackground"
        )
        XCTAssertEqual(
            String(reflecting: AtlasVaultLifecycleLockPolicy.immediate),
            "immediate"
        )
        XCTAssertEqual(
            String(describing: status),
            "AtlasVaultLifecycleStatus(event: <redacted>, timer: <redacted>, failure: <redacted>)"
        )
        XCTAssertEqual(
            String(reflecting: AtlasVaultLifecycleFailure.graceTimerUnavailable),
            "graceTimerUnavailable"
        )
        let values = [String(describing: coordinator), String(describing: status)]
            .joined(separator: "|")
        XCTAssertFalse(values.contains(Self.privateSentinel))
        XCTAssertFalse(values.contains(Self.keySentinel))
        XCTAssertEqual(status.failure, .graceTimerUnavailable)
    }

    func testFacadeConditionalCancellationLeavesUnlockedSessionAlone() async throws {
        let harness = LifecycleFacadeHarness()
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        try await facade.activate(AtlasVaultRuntimeActivationRequest(vaultID: "vault-random-001"))

        let didCancel = await facade.cancelActivationIfInProgress()

        let status = await facade.status()
        let events = await harness.events()
        XCTAssertFalse(didCancel)
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(events, ["activate"])
    }

    func testFacadeConditionalCancellationAtomicallyCancelsActivation() async {
        let harness = LifecycleFacadeHarness(blockActivation: true)
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        let request = AtlasVaultRuntimeActivationRequest(vaultID: "vault-random-002")
        let activation = Task { try await facade.activate(request) }
        let didStart = await harness.waitUntilActivationStarted()
        XCTAssertTrue(didStart)

        let didCancel = await facade.cancelActivationIfInProgress()

        XCTAssertTrue(didCancel)
        do {
            try await activation.value
            XCTFail("Expected cancelled activation")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .cancelled)
        }
        let status = await facade.status()
        let events = await harness.events()
        XCTAssertEqual(status, .locked)
        XCTAssertEqual(events, ["activate", "cancelActivation", "lock"])
    }

    func testFacadeConditionalCancellationDoesNotLockWhenCancellationLosesRace() async throws {
        let harness = LifecycleFacadeHarness(
            blockActivation: true,
            cancellationCompletesActivation: true
        )
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        let request = AtlasVaultRuntimeActivationRequest(vaultID: "vault-random-003")
        let activation = Task { try await facade.activate(request) }
        let didStart = await harness.waitUntilActivationStarted()
        XCTAssertTrue(didStart)

        let didCancel = await facade.cancelActivationIfInProgress()

        XCTAssertFalse(didCancel)
        try await activation.value
        let status = await facade.status()
        let events = await harness.events()
        XCTAssertEqual(status, .unlocked)
        XCTAssertEqual(events, ["activate", "cancelActivation"])
    }

    func testFacadeConditionalCancellationRejectsStaleSuccessForNewActivation() async {
        let cancellationGate = LifecycleGate()
        let harness = LifecycleFacadeHarness(
            blockActivation: true,
            firstCancellationGate: cancellationGate
        )
        let facade = AtlasVaultRuntimeFacade(environment: harness.environment())
        let firstRequest = AtlasVaultRuntimeActivationRequest(vaultID: "vault-random-004")
        let firstActivation = Task { try await facade.activate(firstRequest) }
        let didStartFirst = await harness.waitUntilActivationCount(1)
        XCTAssertTrue(didStartFirst)

        let staleCancellation = Task {
            await facade.cancelActivationIfInProgress()
        }
        let didHoldCancellation = await cancellationGate.waitUntilEntered()
        XCTAssertTrue(didHoldCancellation)
        do {
            try await firstActivation.value
            XCTFail("Expected first activation cancellation")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .cancelled)
        }

        let secondRequest = AtlasVaultRuntimeActivationRequest(vaultID: "vault-random-005")
        let secondActivation = Task { try await facade.activate(secondRequest) }
        let didStartSecond = await harness.waitUntilActivationCount(2)
        XCTAssertTrue(didStartSecond)
        await cancellationGate.open()

        let didCancelCurrentActivation = await staleCancellation.value
        let statusBeforeCleanup = await facade.status()
        XCTAssertFalse(didCancelCurrentActivation)
        XCTAssertEqual(statusBeforeCleanup, .activating)

        await facade.lock()
        do {
            try await secondActivation.value
            XCTFail("Expected cleanup cancellation")
        } catch {
            XCTAssertEqual(error as? AtlasVaultRuntimeFacadeError, .cancelled)
        }
    }

    func testCoordinatorSourceHasNoRuntimeOrPlatformCoupling() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)
        let forbidden = [
            "SwiftUI", "UIKit", "AppKit", "NotificationCenter", "scenePhase",
            "ObservableObject", "@Published", "@State", "@Environment",
            "SearchViewModel", "AtlasLocalCache", "AtlasPublicLocalSnapshot",
            "URLSession", "UserDefaults", "SecItem", "LAContext",
            "LocalAuthentication", "FileManager", "Data.write", "vaultKey",
            "AtlasVaultHydratedState", "decrypt", ".atlasvault",
        ]
        for token in forbidden {
            XCTAssertFalse(source.contains(token), "Unexpected source token: \(token)")
        }
    }

    private func drainTasks() async {
        for _ in 0..<20 {
            await Task.yield()
        }
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/AtlasVaultLifecycleCoordinator.swift"),
            sourceDirectory.appendingPathComponent("../../../../apps/apple/Sources/AtlasUI/AtlasVaultLifecycleCoordinator.swift"),
        ].map(\.standardizedFileURL)
        guard let sourceURL = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) else {
            throw NSError(
                domain: "AtlasVaultLifecycleCoordinatorTests",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Could not find lifecycle coordinator source"]
            )
        }
        return sourceURL
    }
}

private struct LifecycleHarness {
    let runtime: LifecycleRuntimeSpy
    let time: LifecycleManualTime

    init(
        status: AtlasVaultRuntimeStatus,
        honorTimerCancellation: Bool = true,
        statusGate: LifecycleGate? = nil,
        cancellationResults: [Bool] = []
    ) {
        runtime = LifecycleRuntimeSpy(
            status: status,
            statusGate: statusGate,
            cancellationResults: cancellationResults
        )
        time = LifecycleManualTime(honorCancellation: honorTimerCancellation)
    }

    func coordinator(
        policy: AtlasVaultLifecycleLockPolicy,
        lockOnInactive: Bool = false
    ) -> AtlasVaultLifecycleCoordinator {
        AtlasVaultLifecycleCoordinator(
            runtime: runtime,
            lockPolicy: policy,
            clock: time,
            sleeper: time,
            lockOnInactive: lockOnInactive
        )
    }
}

private actor LifecycleRuntimeSpy: AtlasVaultLifecycleRuntimeControlling {
    private var statusValue: AtlasVaultRuntimeStatus
    private var recordedEvents: [String] = []
    private let statusGate: LifecycleGate?
    private var cancellationResults: [Bool]

    init(
        status: AtlasVaultRuntimeStatus,
        statusGate: LifecycleGate?,
        cancellationResults: [Bool]
    ) {
        statusValue = status
        self.statusGate = statusGate
        self.cancellationResults = cancellationResults
    }

    func status() async -> AtlasVaultRuntimeStatus {
        recordedEvents.append("status")
        if let statusGate {
            await statusGate.enter()
        }
        return statusValue
    }

    func lock() async {
        recordedEvents.append("lock")
        statusValue = .locked
    }

    func cancelActivationIfInProgress() async -> Bool {
        recordedEvents.append("cancelActivation")
        if !cancellationResults.isEmpty {
            let result = cancellationResults.removeFirst()
            if result {
                statusValue = .locked
            }
            return result
        }
        guard statusValue == .activating else {
            return false
        }
        statusValue = .locked
        return true
    }

    func events() -> [String] {
        recordedEvents
    }

    func currentStatus() -> AtlasVaultRuntimeStatus {
        statusValue
    }

    func setStatus(_ status: AtlasVaultRuntimeStatus) {
        statusValue = status
    }

    func lockCount() -> Int {
        recordedEvents.filter { $0 == "lock" }.count
    }

    func waitUntilLockCount(_ expected: Int) async -> Bool {
        for _ in 0..<1_000 {
            if lockCount() >= expected {
                return true
            }
            await Task.yield()
        }
        return lockCount() >= expected
    }
}

private actor LifecycleGate {
    private var entered = false
    private var isOpen = false
    private var continuation: CheckedContinuation<Void, Never>?

    func enter() async {
        entered = true
        guard !isOpen else {
            return
        }
        precondition(continuation == nil, "LifecycleGate supports one waiter")
        await withCheckedContinuation { continuation in
            self.continuation = continuation
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
}

private actor LifecycleManualTime: AtlasVaultLifecycleClock, AtlasVaultLifecycleSleeper {
    private struct Waiter {
        let deadline: Duration
        let continuation: CheckedContinuation<Void, Error>
    }

    private var current: Duration = .zero
    private var waiters: [UUID: Waiter] = [:]
    private var recordedEvents: [String] = []
    private var shouldFailNextSleep = false
    private let honorCancellation: Bool

    init(honorCancellation: Bool) {
        self.honorCancellation = honorCancellation
    }

    func now() async -> Duration {
        recordedEvents.append("now")
        return current
    }

    func sleep(until deadline: Duration) async throws {
        recordedEvents.append("sleep")
        if shouldFailNextSleep {
            shouldFailNextSleep = false
            throw LifecycleManualTimeError.unavailable
        }
        guard deadline > current else {
            return
        }

        let id = UUID()
        let honorCancellation = honorCancellation
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation {
                (continuation: CheckedContinuation<Void, Error>) in
                waiters[id] = Waiter(deadline: deadline, continuation: continuation)
                if honorCancellation, Task.isCancelled {
                    cancelWaiter(id)
                }
            }
        } onCancel: {
            guard honorCancellation else {
                return
            }
            Task { await self.cancelWaiter(id) }
        }
    }

    func advance(by duration: Duration) {
        current += duration
        let ready = waiters.filter { $0.value.deadline <= current }
        for (id, waiter) in ready {
            waiters.removeValue(forKey: id)
            waiter.continuation.resume()
        }
    }

    func elapseWithoutResumingSleepers(by duration: Duration) {
        current += duration
    }

    func failNextSleep() {
        shouldFailNextSleep = true
    }

    func events() -> [String] {
        recordedEvents
    }

    func sleeperCount() -> Int {
        waiters.count
    }

    func waitUntilSleeperCount(_ expected: Int) async -> Bool {
        for _ in 0..<1_000 {
            if waiters.count == expected {
                return true
            }
            await Task.yield()
        }
        return waiters.count == expected
    }

    private func cancelWaiter(_ id: UUID) {
        guard let waiter = waiters.removeValue(forKey: id) else {
            return
        }
        waiter.continuation.resume(throwing: CancellationError())
    }
}

private enum LifecycleManualTimeError: Error {
    case unavailable
}

private actor LifecycleFacadeHarness {
    private var recordedEvents: [String] = []
    private var activationContinuation: CheckedContinuation<Void, Error>?
    private let blockActivation: Bool
    private let cancellationCompletesActivation: Bool
    private let firstCancellationGate: LifecycleGate?
    private var cancellationCallCount = 0

    init(
        blockActivation: Bool = false,
        cancellationCompletesActivation: Bool = false,
        firstCancellationGate: LifecycleGate? = nil
    ) {
        self.blockActivation = blockActivation
        self.cancellationCompletesActivation = cancellationCompletesActivation
        self.firstCancellationGate = firstCancellationGate
    }

    nonisolated func environment() -> AtlasVaultRuntimeFacadeEnvironment {
        AtlasVaultRuntimeFacadeEnvironment(
            activate: { [self] _, _ in try await activate() },
            cancelActivation: { [self] in await cancelActivation() },
            lock: { [self] in await lock() },
            privateState: { AtlasVaultHydratedState() },
            save: { _, _ in AtlasVaultAtomicWriteResult(commitState: .committed) }
        )
    }

    func events() -> [String] {
        recordedEvents
    }

    func waitUntilActivationStarted() async -> Bool {
        await waitUntilActivationCount(1)
    }

    func waitUntilActivationCount(_ expected: Int) async -> Bool {
        for _ in 0..<1_000 {
            if recordedEvents.filter({ $0 == "activate" }).count >= expected {
                return true
            }
            await Task.yield()
        }
        return recordedEvents.filter { $0 == "activate" }.count >= expected
    }

    private func activate() async throws {
        recordedEvents.append("activate")
        guard blockActivation else {
            return
        }
        try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<Void, Error>) in
            precondition(activationContinuation == nil)
            activationContinuation = continuation
        }
    }

    private func cancelActivation() async -> Bool {
        recordedEvents.append("cancelActivation")
        cancellationCallCount += 1
        if cancellationCallCount == 1,
           let firstCancellationGate,
           let continuation = activationContinuation {
            activationContinuation = nil
            continuation.resume(throwing: CancellationError())
            await firstCancellationGate.enter()
            return true
        }
        if cancellationCallCount == 2,
           firstCancellationGate != nil {
            return false
        }
        guard let continuation = activationContinuation else {
            return false
        }
        activationContinuation = nil
        if cancellationCompletesActivation {
            continuation.resume()
            return false
        }
        continuation.resume(throwing: CancellationError())
        return true
    }

    private func lock() {
        recordedEvents.append("lock")
    }
}
