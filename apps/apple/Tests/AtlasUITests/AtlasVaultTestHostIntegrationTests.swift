import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultTestHostIntegrationTests: XCTestCase {
    private static let vaultID = "vault_test_host_46"
    private static let vaultKey = Data(repeating: 0x46, count: 32)
    private static let privateSentinel =
        "FAKE_PRIVATE_TEST_HOST_SEARCH_DO_NOT_LEAK"
    private static let updatedPrivateSentinel =
        "FAKE_UPDATED_TEST_HOST_SEARCH_DO_NOT_LEAK"
    private static let initialPrivateQuerySentinel =
        "FAKE_PRIVATE_INITIAL_QUERY_DO_NOT_LEAK"
    private static let updatedPrivateQuerySentinel =
        "FAKE_PRIVATE_UPDATED_QUERY_DO_NOT_LEAK"

    func testConstructionAndStartRemainLockedAndSideEffectFree() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let entriesBefore = try relativePaths(at: harness.rootURL)

        let constructionEvents = await harness.runtime.events()
        let publicSearchCalls = await harness.publicSearch.calls()
        let publicStateCalls =
            await harness.publicStateStore.callCountsForTesting()
        let sourceStarts = await harness.host.presentationSourceStartCount()
        XCTAssertEqual(constructionEvents, [])
        XCTAssertEqual(publicSearchCalls, 0)
        XCTAssertEqual(publicStateCalls.loads, 0)
        XCTAssertEqual(publicStateCalls.replacements, 0)
        XCTAssertEqual(sourceStarts, 0)
        XCTAssertEqual(harness.keyStore.callCounts.load, 0)
        XCTAssertEqual(try relativePaths(at: harness.rootURL), entriesBefore)

        await harness.host.start()

        let startedSnapshot = await harness.observer.currentSnapshot()
        let startEvents = await harness.runtime.events()
        XCTAssertEqual(startedSnapshot.status, .locked)
        XCTAssertNil(startedSnapshot.privateState)
        XCTAssertFalse(startEvents.contains("activate"))
        XCTAssertEqual(harness.keyStore.callCounts.load, 0)
        let temporaryRootIsFileURL =
            await harness.host.temporaryRootIsFileURL()
        XCTAssertTrue(temporaryRootIsFileURL)
        XCTAssertEqual(try relativePaths(at: harness.rootURL), entriesBefore)
    }

    func testPublicStateAndPrivateEndpointTripwiresAreInstrumented() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }

        _ = try await harness.publicSearch.search(query: "FAKE_PUBLIC_QUERY")
        await harness.privateCompatibilityEndpoints
            .loadSavedSearchCompatibility()
        await harness.privateCompatibilityEndpoints
            .loadTrackerCompatibility()
        await harness.privateCompatibilityEndpoints.refreshPrivateSidebar()

        let endpointCalls = await harness.recorder.snapshot()
        let publicStateCalls =
            await harness.publicStateStore.callCountsForTesting()
        XCTAssertEqual(
            endpointCalls,
            [
                .publicSearch,
                .savedSearchCompatibility,
                .trackerCompatibility,
                .privateSidebarRefresh,
            ]
        )
        XCTAssertEqual(publicStateCalls.loads, 1)
        XCTAssertEqual(publicStateCalls.replacements, 0)

        let replacement = Data("FAKE_PUBLIC_REPLACEMENT".utf8)
        await harness.publicStateStore.replacePublicStateBytes(replacement)
        let replacedBytes =
            await harness.publicStateStore.snapshotForTesting()
        let replacedCalls =
            await harness.publicStateStore.callCountsForTesting()
        XCTAssertEqual(replacedBytes, replacement)
        XCTAssertEqual(replacedCalls.replacements, 1)
    }

    func testLockedPublicSearchUsesOnlyPublicEndpointCategory() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        let publicBefore =
            await harness.publicStateStore.snapshotForTesting()
        await harness.host.start()

        let jobs = try await harness.host.searchPublicJobs(
            query: "FAKE_PUBLIC_QUERY"
        )
        let endpointCalls = await harness.recorder.snapshot()
        let snapshot = await waitForSnapshot(
            harness.observer,
            status: .locked
        )

        XCTAssertEqual(
            jobs,
            [AtlasVaultTestPublicJob(
                identifier: "fake-public-job",
                title: "Fake public job"
            )]
        )
        XCTAssertEqual(endpointCalls, [.publicSearch])
        XCTAssertNil(snapshot.privateState)
        let publicAfter =
            await harness.publicStateStore.snapshotForTesting()
        let publicStateCalls =
            await harness.publicStateStore.callCountsForTesting()
        XCTAssertEqual(publicAfter, publicBefore)
        XCTAssertEqual(publicStateCalls.loads, 1)
        XCTAssertEqual(publicStateCalls.replacements, 0)
        await assertNoPrivateCompatibilityCalls(harness.recorder)
        await subscription.cancel()
    }

    func testLockedHostNeverReadsOrPublishesPrivateState() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()

        let snapshot = await waitForSnapshot(
            harness.observer,
            status: .locked
        )
        let events = await harness.runtime.events()

        XCTAssertNil(snapshot.privateState)
        XCTAssertFalse(events.contains("privateState"))
        XCTAssertFalse(events.contains("activate"))
        await assertNoPrivateCompatibilityCalls(harness.recorder)
        await subscription.cancel()
    }

    func testActiveEventCannotAuthorizeExternallyUnlockedRuntime() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.runtime.activate(
            AtlasVaultRuntimeActivationRequest(
                vaultID: Self.vaultID,
                suppliedVaultKey: Self.vaultKey
            )
        )

        await harness.host.handleLifecycle(.didBecomeActive)

        let privateFree = await waitForSnapshot(
            harness.observer,
            status: .locking
        )
        let events = await harness.runtime.events()
        XCTAssertNil(privateFree.privateState)
        XCTAssertFalse(events.contains("privateState"))
        do {
            _ = try await harness.host.apply(mutationRequest())
            XCTFail("Expected externally unlocked runtime to remain unauthorized")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultTestHostError,
                .privateOperationsUnavailable
            )
        }
        await harness.host.lock()
        await subscription.cancel()
    }

    func testExplicitActivationProjectsPrivateStateAndLockClearsIt() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        var snapshots = subscription.snapshots.makeAsyncIterator()
        _ = await snapshots.next()
        await harness.host.start()

        try await harness.host.unlock(unlockRequest())
        let unlocked = await waitForSnapshot(
            harness.observer,
            status: .unlocked
        )
        XCTAssertEqual(
            unlocked.privateState?.savedSearches.first?.name,
            Self.privateSentinel
        )
        XCTAssertEqual(unlocked.privateState?.savedJobs.count, 1)

        await harness.host.lock()
        let locked = await harness.observer.currentSnapshot()
        let deliveredLocked = await snapshots.next()
        XCTAssertEqual(locked.status, .locked)
        XCTAssertNil(locked.privateState)
        XCTAssertEqual(deliveredLocked?.status, .locked)
        XCTAssertNil(deliveredLocked?.privateState)
        let runtimeStatus = await harness.runtime.status()
        XCTAssertEqual(runtimeStatus, .locked)
        await subscription.cancel()
    }

    func testStopIsIdempotentPrivateFreeAndRestartDoesNotReplay() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        var snapshots = subscription.snapshots.makeAsyncIterator()
        _ = await snapshots.next()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)

        await harness.host.stop()

        let stopped = await harness.observer.currentSnapshot()
        let deliveredStopped = await snapshots.next()
        let firstLockCount = await harness.runtime.events()
            .filter { $0 == "lock" }
            .count
        XCTAssertEqual(stopped.status, .locked)
        XCTAssertNil(stopped.privateState)
        XCTAssertEqual(deliveredStopped?.status, .locked)
        XCTAssertNil(deliveredStopped?.privateState)
        let stoppedRuntimeStatus = await harness.runtime.status()
        XCTAssertEqual(stoppedRuntimeStatus, .locked)

        await harness.host.stop()

        let secondLockCount = await harness.runtime.events()
            .filter { $0 == "lock" }
            .count
        XCTAssertEqual(secondLockCount, firstLockCount)

        let restartedObserver = await harness.host.presentationObserver()
        await harness.host.start()
        let restartedSubscription = await restartedObserver.subscribe()
        var restartedSnapshots =
            restartedSubscription.snapshots.makeAsyncIterator()
        let restarted = await restartedSnapshots.next()
        XCTAssertEqual(restarted?.status, .locked)
        XCTAssertNil(restarted?.privateState)
        let activationCount = await harness.runtime.events()
            .filter { $0 == "activate" }
            .count
        XCTAssertEqual(activationCount, 1)

        await restartedSubscription.cancel()
        await subscription.cancel()
    }

    func testStopCancelsInFlightUnlockAndReturnsLocked() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        let activationGate = AtlasVaultTestSuspensionGate()
        await harness.runtime.setActivationGate(activationGate)
        await harness.host.start()

        let unlock = Task {
            try await harness.host.unlock(self.unlockRequest())
        }
        let activationEntered = await activationGate.waitUntilEntered()
        XCTAssertTrue(activationEntered)

        await harness.host.stop()

        do {
            try await unlock.value
            XCTFail("Expected stop to cancel the active unlock")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultUnlockRequestError,
                .cancelled
            )
        }
        let stopped = await harness.observer.currentSnapshot()
        XCTAssertEqual(stopped.status, .locked)
        XCTAssertNil(stopped.privateState)
        let stoppedRuntimeStatus = await harness.runtime.status()
        XCTAssertEqual(stoppedRuntimeStatus, .locked)
        await subscription.cancel()
    }

    func testStopInvalidatesInFlightSaveAndReturnsLocked() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        let saveGate = AtlasVaultTestSuspensionGate()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)
        await harness.runtime.setSaveBehavior(
            .committed(privateState(marker: "UPDATED"))
        )
        await harness.runtime.setSaveGate(saveGate)

        let save = Task {
            try await harness.host.apply(self.mutationRequest())
        }
        let saveEntered = await saveGate.waitUntilEntered()
        XCTAssertTrue(saveEntered)

        await harness.host.stop()

        do {
            _ = try await save.value
            XCTFail("Expected stop to invalidate the active save")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .cancelled
            )
        }
        let stopped = await harness.observer.currentSnapshot()
        XCTAssertEqual(stopped.status, .locked)
        XCTAssertNil(stopped.privateState)
        let stoppedRuntimeStatus = await harness.runtime.status()
        XCTAssertEqual(stoppedRuntimeStatus, .locked)
        await subscription.cancel()
    }

    func testStopCancelsInFlightPublicSearch() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let searchGate = AtlasVaultTestSuspensionGate()
        await harness.publicSearch.setNextSearchGate(searchGate)
        await harness.host.start()

        let search = Task {
            try await harness.host.searchPublicJobs(
                query: "FAKE_PUBLIC_STOP_QUERY"
            )
        }
        let searchEntered = await searchGate.waitUntilEntered()
        XCTAssertTrue(searchEntered)

        await harness.host.stop()

        do {
            _ = try await search.value
            XCTFail("Expected stop to cancel the active public search")
        } catch {
            XCTAssertTrue(error is CancellationError)
        }
        let stoppedRuntimeStatus = await harness.runtime.status()
        XCTAssertEqual(stoppedRuntimeStatus, .locked)
        let stopped = await harness.observer.currentSnapshot()
        XCTAssertEqual(stopped.status, .locked)
        XCTAssertNil(stopped.privateState)
    }

    func testBackgroundAndProtectedDataLossClearPrivateState() async throws {
        for event in [
            AtlasVaultLifecycleEvent.didEnterBackground,
            .protectedDataBecameUnavailable,
        ] {
            let harness = try await makeScriptedHarness()
            defer { try? FileManager.default.removeItem(at: harness.rootURL) }
            let subscription = await harness.observer.subscribe()
            await harness.host.start()
            try await harness.host.unlock(unlockRequest())
            _ = await waitForSnapshot(harness.observer, status: .unlocked)

            await harness.host.handleLifecycle(event)

            let locked = await harness.observer.currentSnapshot()
            let runtimeStatus = await harness.runtime.status()
            XCTAssertEqual(locked.status, .locked)
            XCTAssertNil(locked.privateState)
            XCTAssertEqual(runtimeStatus, .locked)
            await subscription.cancel()
        }
    }

    func testGracePeriodImmediatelyClearsPresentationAndRejectsMutation() async throws {
        let harness = try await makeScriptedHarness(
            lockPolicy: .afterGracePeriod(
                .seconds(60),
                cancelOnActive: true
            )
        )
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)

        await harness.host.handleLifecycle(.didEnterBackground)

        let privateFree = await harness.observer.currentSnapshot()
        let runtimeDuringGrace = await harness.runtime.status()
        XCTAssertEqual(privateFree.status, .locking)
        XCTAssertNil(privateFree.privateState)
        XCTAssertEqual(runtimeDuringGrace, .unlocked)

        do {
            _ = try await harness.host.apply(mutationRequest())
            XCTFail("Expected private mutation admission to remain closed")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultTestHostError,
                .privateOperationsUnavailable
            )
        }
        let applyCalls = await harness.runtime.applyCallCount()
        XCTAssertEqual(applyCalls, 0)

        await harness.host.handleLifecycle(.didBecomeActive)
        let activeAgain = await waitForSnapshot(
            harness.observer,
            status: .unlocked
        )
        XCTAssertNotNil(activeAgain.privateState)
        await subscription.cancel()
    }

    func testRetainedGraceCompletionAllowsFreshExplicitUnlock() async throws {
        let harness = try await makeScriptedHarness(
            lockPolicy: .afterGracePeriod(
                .seconds(60),
                cancelOnActive: false
            )
        )
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)

        await harness.host.handleLifecycle(.didEnterBackground)
        _ = await waitForSnapshot(harness.observer, status: .locking)
        await harness.host.handleLifecycle(.didBecomeActive)
        await harness.time.advance(by: .seconds(60))

        let graceLockCompleted = await waitForGraceLockCompletion(
            lifecycle: harness.lifecycle,
            runtime: harness.runtime
        )
        XCTAssertTrue(graceLockCompleted)

        try await harness.host.unlock(unlockRequest())

        let unlocked = await waitForSnapshot(
            harness.observer,
            status: .unlocked
        )
        XCTAssertNotNil(unlocked.privateState)
        await subscription.cancel()
    }

    func testGraceClosureWinsWhileMutationAdmissionStatusIsSuspended() async throws {
        let harness = try await makeScriptedHarness(
            lockPolicy: .afterGracePeriod(
                .seconds(60),
                cancelOnActive: true
            )
        )
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        let statusGate = AtlasVaultTestSuspensionGate()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)
        await harness.runtime.setNextStatusGate(statusGate)

        let mutation = Task {
            try await harness.host.apply(self.mutationRequest())
        }
        let statusReadEntered = await statusGate.waitUntilEntered()
        XCTAssertTrue(statusReadEntered)

        await harness.host.handleLifecycle(.didEnterBackground)
        let privateFree = await waitForSnapshot(
            harness.observer,
            status: .locking
        )
        XCTAssertNil(privateFree.privateState)

        await statusGate.open()
        do {
            _ = try await mutation.value
            XCTFail("Expected lifecycle gate closure to reject the mutation")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultTestHostError,
                .privateOperationsUnavailable
            )
        }
        let applyCalls = await harness.runtime.applyCallCount()
        XCTAssertEqual(applyCalls, 0)
        await subscription.cancel()
    }

    func testStaleActiveCompletionCannotReopenAfterLaterInactiveEvent() async throws {
        let harness = try await makeScriptedHarness(
            lockPolicy: .afterGracePeriod(
                .seconds(60),
                cancelOnActive: true
            )
        )
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)
        await harness.host.handleLifecycle(.didEnterBackground)
        _ = await waitForSnapshot(harness.observer, status: .locking)

        let activeClockGate = AtlasVaultTestSuspensionGate()
        await harness.time.setNextNowGate(activeClockGate)
        let activeTransition = Task {
            await harness.host.handleLifecycle(.didBecomeActive)
        }
        let activeClockReadEntered =
            await activeClockGate.waitUntilEntered()
        XCTAssertTrue(activeClockReadEntered)

        await harness.host.handleLifecycle(.willResignActive)
        let latestInactive = await waitForSnapshot(
            harness.observer,
            status: .locking
        )
        XCTAssertNil(latestInactive.privateState)

        await activeClockGate.open()
        await activeTransition.value

        let finalSnapshot = await harness.observer.currentSnapshot()
        XCTAssertEqual(finalSnapshot.status, .locking)
        XCTAssertNil(finalSnapshot.privateState)
        do {
            _ = try await harness.host.apply(mutationRequest())
            XCTFail("Expected the later inactive event to keep admission closed")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultTestHostError,
                .privateOperationsUnavailable
            )
        }
        let applyCalls = await harness.runtime.applyCallCount()
        XCTAssertEqual(applyCalls, 0)
        await subscription.cancel()
    }

    func testStalePrivateStateReadCannotPublishLockingAfterCompletedLock() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        let privateStateGate = AtlasVaultTestSuspensionGate()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)
        await harness.runtime.setNextPrivateStateGate(privateStateGate)

        let staleSynchronization = Task {
            await harness.host.synchronizePresentation()
        }
        let privateStateReadEntered =
            await privateStateGate.waitUntilEntered()
        XCTAssertTrue(privateStateReadEntered)

        await harness.host.lock()
        let locked = await waitForSnapshot(
            harness.observer,
            status: .locked
        )
        XCTAssertNil(locked.privateState)

        await privateStateGate.open()
        _ = await staleSynchronization.value

        let latestPublished = await harness.host.latestPublishedSnapshot()
        XCTAssertEqual(latestPublished.status, .locked)
        XCTAssertNil(latestPublished.privateState)
        await subscription.cancel()
    }

    func testUnlockPrivateStateReadFailureLocksAndClearsPresentation() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        await harness.runtime.setFailNextPrivateStateRead(true)

        do {
            try await harness.host.unlock(unlockRequest())
            XCTFail("Expected private-state projection failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultTestHostError,
                .privateOperationsUnavailable
            )
        }

        let locked = await waitForSnapshot(
            harness.observer,
            status: .locked
        )
        let runtimeStatus = await harness.runtime.status()
        XCTAssertNil(locked.privateState)
        XCTAssertEqual(runtimeStatus, .locked)
        do {
            _ = try await harness.host.apply(mutationRequest())
            XCTFail("Expected private operations to remain unavailable")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultTestHostError,
                .privateOperationsUnavailable
            )
        }
        await subscription.cancel()
    }

    func testSaveRefreshPrivateStateReadFailureLocksAndFailsClosed() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)
        await harness.runtime.setSaveBehavior(
            .committed(privateState(marker: "UPDATED"))
        )
        await harness.runtime.setFailNextPrivateStateRead(true)

        do {
            _ = try await harness.host.apply(mutationRequest())
            XCTFail("Expected private-state refresh failure")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultTestHostError,
                .privateOperationsUnavailable
            )
        }

        let locked = await waitForSnapshot(
            harness.observer,
            status: .locked
        )
        let runtimeStatus = await harness.runtime.status()
        XCTAssertNil(locked.privateState)
        XCTAssertEqual(runtimeStatus, .locked)
        await subscription.cancel()
    }

    func testRecoverableSaveFailurePreservesUnlockedProjection() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        let publicBefore =
            await harness.publicStateStore.snapshotForTesting()

        for failure in [
            AtlasVaultScriptedRecoverableSaveFailure.atomicWrite,
            .staleRevision,
        ] {
            await harness.runtime.setSaveBehavior(
                .recoverableFailure(failure)
            )
            do {
                _ = try await harness.host.apply(mutationRequest())
                XCTFail("Expected recoverable save failure")
            } catch {
                XCTAssertEqual(
                    error as? AtlasVaultRuntimeFacadeError,
                    .saveFailed
                )
            }

            let snapshot = await waitForSnapshot(
                harness.observer,
                status: .saveFailed
            )
            let runtimeStatus = await harness.runtime.status()
            XCTAssertEqual(runtimeStatus, .unlocked)
            XCTAssertEqual(
                snapshot.privateState?.savedSearches.first?.name,
                Self.privateSentinel
            )
            let publicAfter =
                await harness.publicStateStore.snapshotForTesting()
            let publicStateCalls =
                await harness.publicStateStore.callCountsForTesting()
            XCTAssertEqual(publicAfter, publicBefore)
            XCTAssertEqual(publicStateCalls.replacements, 0)
        }
        await subscription.cancel()
    }

    func testScriptedRuntimeRejectsMutationForDifferentVaultID() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        _ = await waitForSnapshot(harness.observer, status: .unlocked)

        do {
            _ = try await harness.host.apply(
                mutationRequest(expectedVaultID: "vault_other_test_host_46")
            )
            XCTFail("Expected scripted session mismatch")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .sessionMismatch
            )
        }

        let snapshot = await harness.observer.currentSnapshot()
        XCTAssertEqual(snapshot.status, .unlocked)
        XCTAssertNotNil(snapshot.privateState)
        let runtimeStatus = await harness.runtime.status()
        let applyCalls = await harness.runtime.applyCallCount()
        XCTAssertEqual(runtimeStatus, .unlocked)
        XCTAssertEqual(applyCalls, 1)
        await subscription.cancel()
    }

    func testDurabilityWarningRefreshesCommittedPrivateProjection() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        await harness.runtime.setSaveBehavior(
            .committedDurabilityUnconfirmed(
                privateState(marker: "UPDATED")
            )
        )

        let outcome = try await harness.host.apply(mutationRequest())

        let snapshot = await waitForSnapshot(
            harness.observer,
            status: .saveDurabilityUnconfirmed
        )
        XCTAssertEqual(outcome, .committedDurabilityUnconfirmed)
        XCTAssertEqual(
            snapshot.privateState?.savedSearches.first?.name,
            Self.updatedPrivateSentinel
        )
        await subscription.cancel()
    }

    func testFatalSaveLocksClearsAndAllowsFreshReactivation() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        await harness.runtime.setSaveBehavior(.fatalFailure)

        do {
            _ = try await harness.host.apply(mutationRequest())
            XCTFail("Expected fatal save containment")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .saveIntegrityUnknown
            )
        }

        let locked = await waitForSnapshot(
            harness.observer,
            status: .locked
        )
        XCTAssertNil(locked.privateState)

        await harness.runtime.setActivationBehavior(
            .succeed(privateState(marker: "UPDATED"))
        )
        try await harness.host.unlock(unlockRequest())
        let reactivated = await waitForSnapshot(
            harness.observer,
            status: .unlocked
        )
        XCTAssertEqual(
            reactivated.privateState?.savedSearches.first?.name,
            Self.updatedPrivateSentinel
        )
        await subscription.cancel()
    }

    func testCommittedStateUnavailableIsFatalAndPrivateFree() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        await harness.runtime.setSaveBehavior(.committedStateUnavailable)

        do {
            _ = try await harness.host.apply(mutationRequest())
            XCTFail("Expected committed-state containment")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .committedStateUnavailable(.committed)
            )
        }

        let locked = await waitForSnapshot(
            harness.observer,
            status: .locked
        )
        XCTAssertNil(locked.privateState)
        await subscription.cancel()
    }

    func testActivationCancellationClearsPendingStateAndLateResult() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        let gate = AtlasVaultTestSuspensionGate()
        await harness.runtime.setActivationGate(gate)
        await harness.host.start()

        let request = unlockRequest()
        let activation = Task {
            try await harness.host.unlock(request)
        }
        let entered = await gate.waitUntilEntered()
        XCTAssertTrue(entered)
        activation.cancel()

        do {
            try await activation.value
            XCTFail("Expected activation cancellation")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultUnlockRequestError,
                .cancelled
            )
        }

        let snapshot = await waitForSnapshot(
            harness.observer,
            status: .cancelled
        )
        let runtimeStatus = await harness.runtime.status()
        XCTAssertNil(snapshot.privateState)
        XCTAssertEqual(runtimeStatus, .locked)
        await subscription.cancel()
    }

    func testLifecycleLocksWhenCancellationLosesToCommittedActivation() async throws {
        let committedActivationGate = AtlasVaultTestSuspensionGate()
        let harness = try await makeScriptedHarness(
            unlockCoordinatorBuilder: { runtime, vaultKey in
                AtlasVaultCommittedActivationUnlockCoordinator(
                    runtime: runtime,
                    vaultID: Self.vaultID,
                    vaultKey: vaultKey,
                    committedActivationGate: committedActivationGate
                )
            }
        )
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()

        let firstUnlock = Task {
            try await harness.host.unlock(self.unlockRequest())
        }
        let activationCommitted =
            await committedActivationGate.waitUntilEntered()
        XCTAssertTrue(activationCommitted)
        let committedRuntimeStatus = await harness.runtime.status()
        XCTAssertEqual(committedRuntimeStatus, .unlocked)

        await harness.host.handleLifecycle(.willResignActive)

        let locked = await waitForSnapshot(
            harness.observer,
            status: .locked
        )
        XCTAssertNil(locked.privateState)
        let lockedRuntimeStatus = await harness.runtime.status()
        XCTAssertEqual(lockedRuntimeStatus, .locked)

        await committedActivationGate.open()
        do {
            try await firstUnlock.value
            XCTFail("Expected stale unlock completion to be rejected")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultUnlockRequestError,
                .cancelled
            )
        }

        await harness.host.handleLifecycle(.didBecomeActive)
        try await harness.host.unlock(unlockRequest())
        let replacement = await waitForSnapshot(
            harness.observer,
            status: .unlocked
        )
        XCTAssertNotNil(replacement.privateState)
        let replacementRuntimeStatus = await harness.runtime.status()
        XCTAssertEqual(replacementRuntimeStatus, .unlocked)
        await subscription.cancel()
    }

    func testStaleUnlockFailureCannotTearDownReplacementSession() async throws {
        let staleFailureGate = AtlasVaultTestSuspensionGate()
        let harness = try await makeScriptedHarness(
            unlockCoordinatorBuilder: { runtime, vaultKey in
                AtlasVaultStaleFailureUnlockCoordinator(
                    runtime: runtime,
                    vaultID: Self.vaultID,
                    vaultKey: vaultKey,
                    staleFailureGate: staleFailureGate
                )
            }
        )
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()

        let staleUnlock = Task {
            try await harness.host.unlock(self.unlockRequest())
        }
        let staleDispatchEntered = await staleFailureGate.waitUntilEntered()
        XCTAssertTrue(staleDispatchEntered)

        await harness.host.handleLifecycle(.didEnterBackground)
        await harness.host.handleLifecycle(.didBecomeActive)
        await harness.runtime.setActivationBehavior(
            .succeed(privateState(marker: "UPDATED"))
        )
        try await harness.host.unlock(unlockRequest())
        let replacement = await waitForSnapshot(
            harness.observer,
            status: .unlocked
        )
        XCTAssertEqual(
            replacement.privateState?.savedSearches.first?.name,
            Self.updatedPrivateSentinel
        )

        await staleFailureGate.open()
        do {
            try await staleUnlock.value
            XCTFail("Expected the stale unlock to report cancellation")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultUnlockRequestError,
                .cancelled
            )
        }

        let finalStatus = await harness.runtime.status()
        let finalSnapshot = await harness.observer.currentSnapshot()
        XCTAssertEqual(finalStatus, .unlocked)
        XCTAssertEqual(finalSnapshot.status, .unlocked)
        XCTAssertEqual(
            finalSnapshot.privateState?.savedSearches.first?.name,
            Self.updatedPrivateSentinel
        )
        await subscription.cancel()
    }

    func testUnlockTimeoutFailsBeforeFacadeActivation() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()

        do {
            try await harness.host.unlock(
                unlockRequest(timeout: .zero)
            )
            XCTFail("Expected unlock request timeout")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultUnlockRequestError,
                .expired
            )
        }

        let events = await harness.runtime.events()
        let snapshot = await waitForPrivateFreeSnapshot(harness.observer)
        XCTAssertFalse(events.contains("activate"))
        XCTAssertNil(snapshot.privateState)
        await subscription.cancel()
    }

    func testProtectedDataGateRejectsUnlockUntilAvailabilityReturns() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        await harness.host.handleLifecycle(.protectedDataBecameUnavailable)
        let eventsBeforeRejectedUnlock = await harness.runtime.events()
            .filter { $0 == "activate" }
            .count

        do {
            try await harness.host.unlock(unlockRequest())
            XCTFail("Expected protected-data unlock admission rejection")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultTestHostError,
                .privateOperationsUnavailable
            )
        }

        let eventsAfterRejectedUnlock = await harness.runtime.events()
            .filter { $0 == "activate" }
            .count
        XCTAssertEqual(
            eventsAfterRejectedUnlock,
            eventsBeforeRejectedUnlock
        )

        await harness.host.handleLifecycle(.protectedDataBecameAvailable)
        try await harness.host.unlock(unlockRequest())
        let unlocked = await waitForSnapshot(
            harness.observer,
            status: .unlocked
        )
        XCTAssertNotNil(unlocked.privateState)
        await subscription.cancel()
    }

    func testDelayedSaveCompletionCannotRepublishAfterBackgroundLock() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        let gate = AtlasVaultTestSuspensionGate()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        await harness.runtime.setSaveBehavior(
            .committed(privateState(marker: "UPDATED"))
        )
        await harness.runtime.setSaveGate(gate)

        let save = Task {
            try await harness.host.apply(self.mutationRequest())
        }
        let entered = await gate.waitUntilEntered()
        XCTAssertTrue(entered)

        await harness.host.handleLifecycle(.didEnterBackground)
        let statusAfterLifecycle = await harness.runtime.status()
        XCTAssertEqual(statusAfterLifecycle, .locked)

        do {
            _ = try await save.value
            XCTFail("Expected the delayed save to lose admission")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .cancelled
            )
        }
        let statusAfterLateCompletion = await harness.runtime.status()
        XCTAssertEqual(statusAfterLateCompletion, .locked)
        let locked = await waitForSnapshot(
            harness.observer,
            status: .locked
        )
        XCTAssertNil(locked.privateState)
        await subscription.cancel()
    }

    func testStaleSaveFailureCannotOverwriteReplacementGeneration() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let subscription = await harness.observer.subscribe()
        let saveGate = AtlasVaultTestSuspensionGate()
        let staleCompletionGate = AtlasVaultTestSuspensionGate()
        await harness.host.start()
        try await harness.host.unlock(unlockRequest())
        await harness.runtime.setSaveBehavior(
            .committed(privateState(marker: "INITIAL"))
        )
        await harness.runtime.setSaveGate(saveGate)
        await harness.runtime.setNextStaleSaveCompletionGate(
            staleCompletionGate
        )

        let staleSave = Task {
            try await harness.host.apply(self.mutationRequest())
        }
        let saveEntered = await saveGate.waitUntilEntered()
        XCTAssertTrue(saveEntered)

        await harness.host.handleLifecycle(.didEnterBackground)
        let staleCompletionEntered =
            await staleCompletionGate.waitUntilEntered()
        XCTAssertTrue(staleCompletionEntered)

        await harness.host.handleLifecycle(.didBecomeActive)
        await harness.runtime.setActivationBehavior(
            .succeed(privateState(marker: "UPDATED"))
        )
        try await harness.host.unlock(unlockRequest())
        let replacement = await waitForSnapshot(
            harness.observer,
            status: .unlocked
        )
        XCTAssertEqual(
            replacement.privateState?.savedSearches.first?.name,
            Self.updatedPrivateSentinel
        )

        await staleCompletionGate.open()
        do {
            _ = try await staleSave.value
            XCTFail("Expected the stale save to report cancellation")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultRuntimeFacadeError,
                .cancelled
            )
        }

        let finalSnapshot = await harness.observer.currentSnapshot()
        let finalStatus = await harness.runtime.status()
        XCTAssertEqual(finalStatus, .unlocked)
        XCTAssertEqual(finalSnapshot.status, .unlocked)
        XCTAssertEqual(
            finalSnapshot.privateState?.savedSearches.first?.name,
            Self.updatedPrivateSentinel
        )
        await subscription.cancel()
    }

    func testActivationFailureMatrixHasNoPartialPrivateProjection() async throws {
        let failures: [
            (AtlasVaultActivationFailure, AtlasVaultPresentationStatus)
        ] = [
            (.keyUnavailable, .keyUnavailable),
            (.authenticationFailed, .corruptStore),
            (.corruptStore, .corruptStore),
            (.unsupportedVersion, .unsupportedVersion),
            (.vaultUnavailable, .failed),
        ]
        for (failure, expectedStatus) in failures {
            let harness = try await makeScriptedHarness()
            defer { try? FileManager.default.removeItem(at: harness.rootURL) }
            let subscription = await harness.observer.subscribe()
            let publicBefore =
                await harness.publicStateStore.snapshotForTesting()
            await harness.runtime.setActivationBehavior(.fail(failure))
            await harness.host.start()

            do {
                try await harness.host.unlock(unlockRequest())
                XCTFail("Expected scripted activation failure")
            } catch {
                XCTAssertEqual(
                    error as? AtlasVaultUnlockRequestError,
                    .unlockFailed
                )
            }

            let snapshot = await waitForSnapshot(
                harness.observer,
                status: expectedStatus
            )
            XCTAssertNil(snapshot.privateState)
            let publicAfter =
                await harness.publicStateStore.snapshotForTesting()
            let publicStateCalls =
                await harness.publicStateStore.callCountsForTesting()
            XCTAssertEqual(publicAfter, publicBefore)
            XCTAssertEqual(publicStateCalls.replacements, 0)
            await subscription.cancel()
        }
    }

    func testRealFacadeLoadsSavesAndRehydratesEncryptedStateUnderTempRoot() async throws {
        let rootURL = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: rootURL) }
        let session = try AtlasVaultUnlockedSession(
            vaultID: Self.vaultID,
            vaultKey: Self.vaultKey
        )
        let initialRecord = try XCTUnwrap(
            AtlasVaultRecordSaver().save(
                mutations: AtlasVaultMutationSet(
                    creates: [AtlasVaultCreateMutation(
                        payload: .savedSearch(
                            savedSearchEnvelope(
                                name: Self.privateSentinel,
                                text: Self.initialPrivateQuerySentinel
                            )
                        ),
                        keyID: "fake-key-id"
                    )]
                ),
                session: session
            ).first
        )
        let store = AtlasVaultLocalStoreEnvelope(
            storeID: "fake-test-host-store",
            createdAt: "2026-01-01T00:00:00Z",
            updatedAt: "2026-01-01T00:00:00Z",
            vaultMetadata: [:],
            records: [initialRecord]
        )
        let storeURL = try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)
            .localStoreURL(vaultID: Self.vaultID)
        try FileManager.default.createDirectory(
            at: storeURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try AtlasVaultLocalStoreIO.write(store, to: storeURL)

        let rootProvider = AtlasVaultTestRootProvider(rootURL: rootURL)
        let keyStore = AtlasVaultTestFakeKeyStore(key: Self.vaultKey)
        let services: AtlasVaultRuntimeServices<
            AtlasFileManagerVaultDirectoryPreparer,
            AtlasVaultLocalStoreFileIO
        > = AtlasVaultRuntimeFactory.makeServices(
            rootDirectoryProvider: rootProvider,
            keyStore: keyStore,
            directoryPreparer: AtlasFileManagerVaultDirectoryPreparer(),
            localStoreIO: AtlasVaultLocalStoreFileIO(),
            atomicStoreWriter: AtlasVaultAtomicStoreWriter(
                fileSystem: AtlasFoundationAtomicFileSystemClient()
            ),
            localStoreMerger: AtlasVaultLocalStoreMerger(),
            recordSaver: AtlasVaultRecordSaver(),
            recordHydrator: AtlasVaultRecordHydrator()
        )
        let runtime = AtlasVaultRuntimeFacade.runtimeServices(services)
        let time = AtlasVaultTestManualTime()
        let vaultKey = Self.vaultKey
        let unlockCoordinator = AtlasVaultUnlockRequestCoordinator(
            dependencies: AtlasVaultUnlockRequestDependencies(
                derivePassphraseVaultKey: { _ in vaultKey },
                deriveRecoveryVaultKey: { _ in vaultKey },
                activate: { request in
                    try await runtime.activate(request)
                }
            )
        )
        let lifecycle = AtlasVaultLifecycleCoordinator(
            runtimeFacade: runtime,
            lockPolicy: .immediate,
            clock: time,
            sleeper: time
        )
        let recorder = AtlasVaultTestEndpointCallRecorder()
        let publicStateStore = try makePublicStateStore()
        let privateCompatibilityEndpoints =
            AtlasVaultTestPrivateCompatibilityEndpointSpy(recorder: recorder)
        let publicSearch = AtlasVaultFakePublicJobSearchService(
            recorder: recorder,
            publicStateStore: publicStateStore,
            results: [AtlasVaultTestPublicJob(
                identifier: "fake-public-job",
                title: "Fake public job"
            )]
        )
        let host = AtlasVaultTestHost(
            runtime: runtime,
            lifecycle: lifecycle,
            unlockCoordinator: unlockCoordinator,
            publicSearch: publicSearch,
            environment: AtlasVaultTestHostEnvironment(
                temporaryRootURL: rootURL,
                keyStore: keyStore,
                publicStateStore: publicStateStore,
                privateCompatibilityEndpoints: privateCompatibilityEndpoints
            )
        )
        let observer = await host.presentationObserver()
        let subscription = await observer.subscribe()
        let publicBefore = await publicStateStore.snapshotForTesting()

        await host.start()
        try await host.unlock(
            AtlasVaultUnlockRequest(
                vaultID: Self.vaultID,
                input: .localKey
            )
        )
        let initial = await waitForSnapshot(observer, status: .unlocked)
        XCTAssertEqual(
            initial.privateState?.savedSearches.first?.name,
            Self.privateSentinel
        )

        let outcome = try await host.apply(
            AtlasVaultRuntimeMutationRequest(
                expectedVaultID: Self.vaultID,
                mutations: AtlasVaultMutationSet(
                    creates: [AtlasVaultCreateMutation(
                        payload: .savedSearch(
                            savedSearchEnvelope(
                                name: Self.updatedPrivateSentinel,
                                text: Self.updatedPrivateQuerySentinel
                            )
                        ),
                        keyID: "fake-key-id"
                    )]
                )
            )
        )
        XCTAssertEqual(outcome, .committed)
        let afterSave = await waitForSnapshot(
            observer,
            status: .unlocked,
            matching: { snapshot in
                snapshot.privateState?.savedSearches.count == 2
            }
        )
        XCTAssertEqual(afterSave.privateState?.savedSearches.count, 2)
        let savedPresentationID = try XCTUnwrap(
            afterSave.privateState?.savedSearches.first?.id
        )

        let serializedStore = String(
            decoding: try Data(contentsOf: storeURL),
            as: UTF8.self
        )
        XCTAssertFalse(serializedStore.contains(Self.privateSentinel))
        XCTAssertFalse(serializedStore.contains(Self.updatedPrivateSentinel))
        XCTAssertFalse(
            serializedStore.contains(Self.initialPrivateQuerySentinel)
        )
        XCTAssertFalse(
            serializedStore.contains(Self.updatedPrivateQuerySentinel)
        )
        XCTAssertFalse(serializedStore.contains("saved_search"))

        await host.lock()
        try await host.unlock(
            AtlasVaultUnlockRequest(
                vaultID: Self.vaultID,
                input: .localKey
            )
        )
        let rehydrated = await waitForSnapshot(
            observer,
            status: .unlocked,
            matching: { snapshot in
                snapshot.privateState?.savedSearches.count == 2
                    && snapshot.privateState?.savedSearches.first?.id
                        != savedPresentationID
            }
        )
        XCTAssertEqual(rehydrated.privateState?.savedSearches.count, 2)
        XCTAssertEqual(keyStore.callCounts.load, 2)
        XCTAssertEqual(keyStore.callCounts.save, 0)
        XCTAssertEqual(keyStore.callCounts.delete, 0)
        XCTAssertEqual(rootProvider.callCount, 2)
        let publicAfter = await publicStateStore.snapshotForTesting()
        let publicStateCalls = await publicStateStore.callCountsForTesting()
        XCTAssertEqual(publicAfter, publicBefore)
        XCTAssertEqual(publicStateCalls.replacements, 0)
        XCTAssertFalse(
            try relativePaths(at: rootURL).contains {
                $0.hasSuffix(".atlasvault")
            }
        )
        await assertNoPrivateCompatibilityCalls(recorder)
        await subscription.cancel()
    }

    func testHostDiagnosticsAndErrorsAreNonSensitive() async throws {
        let harness = try await makeScriptedHarness()
        defer { try? FileManager.default.removeItem(at: harness.rootURL) }
        let values = [
            String(describing: harness.host),
            String(reflecting: harness.host),
            String(describing: AtlasVaultTestHostError.notStarted),
            String(reflecting: AtlasVaultTestHostError.privateOperationsUnavailable),
        ].joined(separator: "|")

        XCTAssertFalse(values.contains(Self.privateSentinel))
        XCTAssertFalse(values.contains(Self.updatedPrivateSentinel))
        XCTAssertFalse(values.contains(Self.vaultKey.base64EncodedString()))
        XCTAssertFalse(values.contains(harness.rootURL.path))
    }

    func testTestHostSourceHasNoUIAppCacheNetworkOrPlatformAuthCoupling() throws {
        let source = try String(
            contentsOf: testHostSourceURL(),
            encoding: .utf8
        )

        for forbidden in [
            "SwiftUI",
            "UIKit",
            "AppKit",
            "NotificationCenter",
            "@main",
            "ObservableObject",
            "@Published",
            "@State",
            "@Environment",
            "UserDefaults",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "URLSession",
            "AtlasAPIClient",
            "SearchViewModel",
            "AtlasLocalCache",
            "AtlasPublicLocalSnapshot",
            "applicationSupportDirectory",
            "AtlasApplicationSupport",
            "refreshSidebarData",
            "ATLAS_REFERENCE_CAPTURE",
            "AtlasReferenceCaptureView",
        ] {
            XCTAssertFalse(
                source.contains(forbidden),
                "Unexpected test-host source reference: \(forbidden)"
            )
        }
    }

    private struct ScriptedHarness {
        let rootURL: URL
        let recorder: AtlasVaultTestEndpointCallRecorder
        let publicStateStore: AtlasVaultTestPublicStateStore
        let privateCompatibilityEndpoints:
            AtlasVaultTestPrivateCompatibilityEndpointSpy
        let publicSearch: AtlasVaultFakePublicJobSearchService
        let keyStore: AtlasVaultTestFakeKeyStore
        let runtime: AtlasVaultScriptedTestRuntime
        let time: AtlasVaultTestManualTime
        let lifecycle: AtlasVaultLifecycleCoordinator
        let host: AtlasVaultTestHost
        let observer: any AtlasVaultPresentationObserving
    }

    private func makeScriptedHarness(
        lockPolicy: AtlasVaultLifecycleLockPolicy = .immediate,
        unlockCoordinatorBuilder: ((
            AtlasVaultScriptedTestRuntime,
            Data
        ) -> any AtlasVaultUnlockRequestCoordinating)? = nil
    ) async throws -> ScriptedHarness {
        let rootURL = try temporaryRoot()
        let recorder = AtlasVaultTestEndpointCallRecorder()
        let publicStateStore = try makePublicStateStore()
        let privateCompatibilityEndpoints =
            AtlasVaultTestPrivateCompatibilityEndpointSpy(recorder: recorder)
        let publicSearch = AtlasVaultFakePublicJobSearchService(
            recorder: recorder,
            publicStateStore: publicStateStore,
            results: [AtlasVaultTestPublicJob(
                identifier: "fake-public-job",
                title: "Fake public job"
            )]
        )
        let keyStore = AtlasVaultTestFakeKeyStore(key: Self.vaultKey)
        let runtime = AtlasVaultScriptedTestRuntime(
            activationState: privateState(marker: "INITIAL")
        )
        let time = AtlasVaultTestManualTime()
        let vaultKey = Self.vaultKey
        let unlockCoordinator: any AtlasVaultUnlockRequestCoordinating
        if let unlockCoordinatorBuilder {
            unlockCoordinator = unlockCoordinatorBuilder(runtime, vaultKey)
        } else {
            unlockCoordinator = AtlasVaultUnlockRequestCoordinator(
                dependencies: AtlasVaultUnlockRequestDependencies(
                    derivePassphraseVaultKey: { _ in vaultKey },
                    deriveRecoveryVaultKey: { _ in vaultKey },
                    activate: { request in
                        try await runtime.activate(request)
                    }
                )
            )
        }
        let lifecycle = AtlasVaultLifecycleCoordinator(
            runtime: runtime,
            lockPolicy: lockPolicy,
            clock: time,
            sleeper: time
        )
        let host = AtlasVaultTestHost(
            runtime: runtime,
            lifecycle: lifecycle,
            unlockCoordinator: unlockCoordinator,
            publicSearch: publicSearch,
            environment: AtlasVaultTestHostEnvironment(
                temporaryRootURL: rootURL,
                keyStore: keyStore,
                publicStateStore: publicStateStore,
                privateCompatibilityEndpoints: privateCompatibilityEndpoints
            )
        )
        let observer = await host.presentationObserver()
        return ScriptedHarness(
            rootURL: rootURL,
            recorder: recorder,
            publicStateStore: publicStateStore,
            privateCompatibilityEndpoints: privateCompatibilityEndpoints,
            publicSearch: publicSearch,
            keyStore: keyStore,
            runtime: runtime,
            time: time,
            lifecycle: lifecycle,
            host: host,
            observer: observer
        )
    }

    private func unlockRequest(
        timeout: Duration? = nil
    ) -> AtlasVaultUnlockRequest {
        AtlasVaultUnlockRequest(
            vaultID: Self.vaultID,
            input: .localKey,
            timeout: timeout
        )
    }

    private func mutationRequest(
        expectedVaultID: String =
            AtlasVaultTestHostIntegrationTests.vaultID
    ) -> AtlasVaultRuntimeMutationRequest {
        AtlasVaultRuntimeMutationRequest(
            expectedVaultID: expectedVaultID,
            mutations: AtlasVaultMutationSet(
                deletes: [AtlasVaultDeleteMutation(
                    recordID: "fake-record-id",
                    currentRevision: "fake-revision",
                    keyID: "fake-key-id"
                )]
            )
        )
    }

    private func privateState(marker: String) -> AtlasVaultHydratedState {
        let isUpdated = marker == "UPDATED"
        let searchName = isUpdated
            ? Self.updatedPrivateSentinel
            : Self.privateSentinel
        let metadata = AtlasHydratedRecordMetadata(
            id: "fake-record-\(marker.lowercased())",
            revision: "fake-revision-\(marker.lowercased())",
            parentRevision: nil,
            deleted: false,
            keyID: "fake-key-id"
        )
        return AtlasVaultHydratedState(
            savedSearches: [AtlasHydratedSavedSearch(
                metadata: metadata,
                payload: AtlasSavedSearchVaultPayload(
                    name: searchName,
                    summary: "Fake private summary",
                    request: AtlasSearchRequest(
                        text: "FAKE_PRIVATE_QUERY_\(marker)_DO_NOT_LEAK"
                    )
                ),
                clientCreatedAt: "2026-01-01T00:00:00Z",
                clientUpdatedAt: "2026-01-01T00:00:00Z"
            )],
            savedJobs: [AtlasHydratedSavedJob(
                metadata: AtlasHydratedRecordMetadata(
                    id: "fake-job-record-\(marker.lowercased())",
                    revision: "fake-job-revision-\(marker.lowercased())",
                    parentRevision: nil,
                    deleted: false,
                    keyID: "fake-key-id"
                ),
                payload: AtlasSavedJobVaultPayload(
                    jobKey: "FAKE_PRIVATE_JOB_KEY_\(marker)_DO_NOT_LEAK",
                    status: "saved"
                ),
                clientCreatedAt: "2026-01-01T00:00:00Z",
                clientUpdatedAt: "2026-01-01T00:00:00Z"
            )]
        )
    }

    private func savedSearchEnvelope(
        name: String,
        text: String
    ) -> AtlasSavedSearchVaultRecordPayload {
        .savedSearch(
            AtlasSavedSearchVaultPayload(
                name: name,
                summary: "Fake encrypted search",
                request: AtlasSearchRequest(text: text)
            ),
            clientCreatedAt: "2026-01-01T00:00:00Z",
            clientUpdatedAt: "2026-01-01T00:00:00Z"
        )
    }

    private func waitForSnapshot(
        _ observer: any AtlasVaultPresentationObserving,
        status: AtlasVaultPresentationStatus,
        matching predicate: (AtlasVaultPresentationSnapshot) -> Bool = { _ in
            true
        }
    ) async -> AtlasVaultPresentationSnapshot {
        var latest = await observer.currentSnapshot()
        for _ in 0..<2_000 {
            latest = await observer.currentSnapshot()
            if latest.status == status,
               predicate(latest) {
                return latest
            }
            await Task.yield()
        }
        XCTFail(
            "Timed out waiting for presentation status/state \(status); "
                + "latest was \(latest.status)"
        )
        return latest
    }

    private func waitForPrivateFreeSnapshot(
        _ observer: any AtlasVaultPresentationObserving
    ) async -> AtlasVaultPresentationSnapshot {
        var latest = await observer.currentSnapshot()
        for _ in 0..<2_000 {
            latest = await observer.currentSnapshot()
            if latest.privateState == nil,
               latest.status != .unlocked,
               latest.status != .saveInProgress,
               latest.status != .saveFailed,
               latest.status != .saveDurabilityUnconfirmed {
                return latest
            }
            await Task.yield()
        }
        XCTFail("Timed out waiting for private-free presentation")
        return latest
    }

    private func waitForGraceLockCompletion(
        lifecycle: AtlasVaultLifecycleCoordinator,
        runtime: AtlasVaultScriptedTestRuntime
    ) async -> Bool {
        for _ in 0..<2_000 {
            let lifecycleStatus = await lifecycle.status()
            let runtimeStatus = await runtime.status()
            if !lifecycleStatus.hasPendingGraceLock,
               runtimeStatus == .locked {
                return true
            }
            await Task.yield()
        }
        return false
    }

    private func assertNoPrivateCompatibilityCalls(
        _ recorder: AtlasVaultTestEndpointCallRecorder
    ) async {
        let savedSearchCalls = await recorder.count(.savedSearchCompatibility)
        let trackerCalls = await recorder.count(.trackerCompatibility)
        let sidebarCalls = await recorder.count(.privateSidebarRefresh)
        XCTAssertEqual(savedSearchCalls, 0)
        XCTAssertEqual(trackerCalls, 0)
        XCTAssertEqual(sidebarCalls, 0)
    }

    private func temporaryRoot() throws -> URL {
        let rootURL = try AtlasVaultTestFileSystemSupport
            .canonicalTemporaryRoot()
            .appendingPathComponent(
                "atlasvault-test-host-\(UUID().uuidString)",
                isDirectory: true
            )
            .standardizedFileURL
        try FileManager.default.createDirectory(
            at: rootURL,
            withIntermediateDirectories: true
        )
        return rootURL
    }

    private func relativePaths(at rootURL: URL) throws -> [String] {
        guard let enumerator = FileManager.default.enumerator(
            at: rootURL,
            includingPropertiesForKeys: nil
        ) else {
            return []
        }
        return enumerator.compactMap { element in
            guard let url = element as? URL else {
                return nil
            }
            return String(
                url.path.dropFirst(rootURL.path.count + 1)
            )
        }.sorted()
    }

    private func makePublicStateStore() throws -> AtlasVaultTestPublicStateStore {
        let snapshot = try makePublicSnapshot()
        return try AtlasVaultTestPublicStateStore(
            bytes: encodedPublicSnapshot(snapshot)
        )
    }

    private func makePublicSnapshot() throws -> AtlasPublicLocalSnapshot {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(
            AtlasPublicLocalSnapshot.self,
            from: Data("""
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
            """.utf8)
        )
    }

    private func encodedPublicSnapshot(
        _ snapshot: AtlasPublicLocalSnapshot
    ) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(snapshot)
    }

    private func testHostSourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("AtlasVaultTestHost.swift"),
            sourceDirectory.appendingPathComponent(
                "../../../../apps/apple/Tests/AtlasUITests/AtlasVaultTestHost.swift"
            ),
        ].map(\.standardizedFileURL)
        guard let sourceURL = candidates.first(where: {
            FileManager.default.fileExists(atPath: $0.path)
        }) else {
            throw NSError(
                domain: "AtlasVaultTestHostIntegrationTests",
                code: 1,
                userInfo: [
                    NSLocalizedDescriptionKey:
                        "Could not find AtlasVaultTestHost.swift",
                ]
            )
        }
        return sourceURL
    }
}

private actor AtlasVaultStaleFailureUnlockCoordinator:
    AtlasVaultUnlockRequestCoordinating
{
    private let runtime: AtlasVaultScriptedTestRuntime
    private let vaultID: String
    private let vaultKey: Data
    private let staleFailureGate: AtlasVaultTestSuspensionGate
    private var dispatchCount = 0

    init(
        runtime: AtlasVaultScriptedTestRuntime,
        vaultID: String,
        vaultKey: Data,
        staleFailureGate: AtlasVaultTestSuspensionGate
    ) {
        self.runtime = runtime
        self.vaultID = vaultID
        self.vaultKey = vaultKey
        self.staleFailureGate = staleFailureGate
    }

    func dispatch(_ request: AtlasVaultUnlockRequest) async throws {
        dispatchCount += 1
        if dispatchCount == 1 {
            try? await staleFailureGate.wait()
            throw AtlasVaultUnlockRequestError.cancelled
        }
        try await runtime.activate(
            AtlasVaultRuntimeActivationRequest(
                vaultID: vaultID,
                suppliedVaultKey: vaultKey
            )
        )
    }

    func cancel(_ request: AtlasVaultUnlockRequest) -> Bool {
        true
    }
}

private actor AtlasVaultCommittedActivationUnlockCoordinator:
    AtlasVaultUnlockRequestCoordinating
{
    private let runtime: AtlasVaultScriptedTestRuntime
    private let vaultID: String
    private let vaultKey: Data
    private let committedActivationGate: AtlasVaultTestSuspensionGate
    private var dispatchCount = 0

    init(
        runtime: AtlasVaultScriptedTestRuntime,
        vaultID: String,
        vaultKey: Data,
        committedActivationGate: AtlasVaultTestSuspensionGate
    ) {
        self.runtime = runtime
        self.vaultID = vaultID
        self.vaultKey = vaultKey
        self.committedActivationGate = committedActivationGate
    }

    func dispatch(_ request: AtlasVaultUnlockRequest) async throws {
        dispatchCount += 1
        try await runtime.activate(
            AtlasVaultRuntimeActivationRequest(
                vaultID: vaultID,
                suppliedVaultKey: vaultKey
            )
        )
        if dispatchCount == 1 {
            try await committedActivationGate.wait()
        }
    }

    func cancel(_ request: AtlasVaultUnlockRequest) -> Bool {
        false
    }
}
