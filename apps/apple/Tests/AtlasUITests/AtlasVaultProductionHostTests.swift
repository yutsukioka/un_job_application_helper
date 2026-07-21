// Phase 2D-56 repository boundary.
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultProductionHostTests: XCTestCase {
    private enum SafeReopenSuspensionPoint: CaseIterable {
        case lifecycleHandle
        case lifecycleStatus
        case runtimeStatus
    }

    private enum PostSuccessCallback: CaseIterable {
        case cancel
        case disappearance
    }

    private static let fakeVaultID =
        "00000000-0000-4000-8000-000000000256"
    private static let fakeQuery = "FAKE_PHASE_2D56_QUERY_DO_NOT_LOG"

    func testPhaseTypesErrorsGenerationsAndDescriptionsAreRedacted() async {
        _ = AtlasVaultProductionHost.self
        _ = AtlasVaultProductionHostBuilder.self
        _ = AtlasVaultProductionUnlockPresentationControllerBuilder.self
        _ = AtlasVaultProductionPresentationPipeline.self
        _ = AtlasVaultProductionPresentationCoordinating.self
        _ = AtlasVaultProductionPresentationOwnerResetting.self

        let first = AtlasVaultProductionHostGeneration()
        let second = AtlasVaultProductionHostGeneration()
        await expectNotEqual(first, second)

        let rendered = [
            first.description,
            first.debugDescription,
            AtlasVaultProductionHostError.stopped.description,
            AtlasVaultProductionHostError.presentationUnavailable.description,
            AtlasVaultProductionHostBuilder().description,
            AtlasVaultProductionUnlockPresentationControllerBuilder()
                .description,
        ].joined(separator: "\n")
        await expectTrue(rendered.contains("<redacted>"))
        await expectFalse(rendered.contains(Self.fakeVaultID))
        await expectFalse(rendered.contains(Self.fakeQuery))
        requireSendable(first)
    }

    func testPipelineConstructionExplicitStartAndIdempotence() async throws {
        let pipeline = AtlasVaultProductionPresentationPipeline()
        let locked = try privateFreeSnapshot(.locked)

        await expectEqual(
            await pipeline.observationStartCountForTesting(),
            0
        )
        await expectFalse(await pipeline.publish(locked))
        let prestartSubscription = await pipeline.subscribe()
        await expectEqual(
            await pipeline.observationStartCountForTesting(),
            0
        )
        await expectTrue(await pipeline.start())
        await expectTrue(await pipeline.start())
        await expectEqual(
            await pipeline.observationStartCountForTesting(),
            1
        )
        await prestartSubscription.cancel()
        await expectTrue(pipeline.description.contains("<redacted>"))
        let pipelineSource = try source(
            named: "AtlasVaultProductionPresentationPipeline.swift"
        )
        await expectTrue(
            pipelineSource.contains("await source.activate()")
        )
    }

    func testPipelineConcurrentStartWaitsForOneCompletedHandshake() async throws {
        let pipelineSource = try source(
            named: "AtlasVaultProductionPresentationPipeline.swift"
        )
        await expectTrue(pipelineSource.contains("case starting"))
        await expectTrue(pipelineSource.contains("startWaiters"))

        let pipeline = AtlasVaultProductionPresentationPipeline()
        async let firstStart = pipeline.start()
        async let secondStart = pipeline.start()
        let results = await (firstStart, secondStart)

        await expectTrue(results.0)
        await expectTrue(results.1)
        await expectEqual(
            await pipeline.observationStartCountForTesting(),
            1
        )
    }

    func testPipelinePublishesOrderedPrivateFreeSnapshotsAndCurrentValue() async throws {
        let pipeline = AtlasVaultProductionPresentationPipeline()
        await expectTrue(await pipeline.start())
        let subscription = await pipeline.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        await expectEqual((await iterator.next())?.status, .locked)

        await expectTrue(
            await pipeline.publish(
                try privateFreeSnapshot(.activating)
            )
        )
        await expectEqual((await iterator.next())?.status, .activating)
        await expectTrue(
            await pipeline.publish(
                try privateFreeSnapshot(.unlocked)
            )
        )
        await expectEqual((await iterator.next())?.status, .unlocked)

        let current = await pipeline.currentSnapshot()
        await expectEqual(current.status, .unlocked)
        await expectNil(current.privateState)
        await expectEqual(await pipeline.latestSequenceForTesting(), 2)
        await expectEqual(
            await pipeline.acknowledgedSequenceForTesting(),
            2
        )
        await subscription.cancel()
    }

    func testPipelineRejectsPrivatePayloadWithoutEmission() async throws {
        let pipeline = AtlasVaultProductionPresentationPipeline()
        await expectTrue(await pipeline.start())
        let unsafe = AtlasVaultPresentationSnapshot(
            status: .unlocked,
            privateState: AtlasVaultPrivatePresentationState(
                savedSearches: [],
                savedJobs: [],
                applicationNotes: [],
                profileSnippets: [],
                draftMetadata: []
            )
        )

        await expectThrowsError(
            try AtlasVaultPrivateFreePresentationSnapshot(
                validating: unsafe
            )
        ) { error in
            await expectEqual(
                error as? AtlasVaultProductionHostError,
                .presentationUnavailable
            )
        }
        await expectEqual(
            await pipeline.currentSnapshot(),
            AtlasVaultPresentationSnapshot(
                status: .locked,
                privateState: nil
            )
        )
        await expectEqual(await pipeline.latestSequenceForTesting(), 0)
    }

    func testPipelinePublishWaitsForAdapterAcknowledgement() async throws {
        let pipeline = AtlasVaultProductionPresentationPipeline()
        let completion = HostBoolRecorder()
        await expectTrue(await pipeline.start())
        await pipeline.suspendDeliveryForTesting()
        let activating = try privateFreeSnapshot(.activating)

        let task = Task {
            let result = await pipeline.publish(activating)
            await completion.record(result)
        }
        await pipeline.waitUntilSequenceForTesting(1)
        await expectNil(await completion.value())
        await expectEqual(
            await pipeline.acknowledgedSequenceForTesting(),
            0
        )

        await pipeline.resumeDeliveryForTesting()
        await task.value
        await expectEqual(await completion.value(), true)
        await expectEqual(
            await pipeline.acknowledgedSequenceForTesting(),
            1
        )
    }

    func testPipelineSubscribersCancellationAndNewestValueBuffering() async throws {
        let pipeline = AtlasVaultProductionPresentationPipeline()
        await expectTrue(await pipeline.start())
        let first = await pipeline.subscribe()
        let second = await pipeline.subscribe()
        var firstIterator = first.snapshots.makeAsyncIterator()
        var secondIterator = second.snapshots.makeAsyncIterator()
        _ = await firstIterator.next()
        _ = await secondIterator.next()

        await expectTrue(
            await pipeline.publish(
                try privateFreeSnapshot(.activating)
            )
        )
        await expectEqual((await firstIterator.next())?.status, .activating)
        await expectEqual((await secondIterator.next())?.status, .activating)

        await first.cancel()
        await expectNil(await firstIterator.next())
        await expectTrue(
            await pipeline.publish(
                try privateFreeSnapshot(.unlocked)
            )
        )
        await expectEqual((await secondIterator.next())?.status, .unlocked)

        let slow = await pipeline.subscribe()
        var slowIterator = slow.snapshots.makeAsyncIterator()
        await expectEqual((await slowIterator.next())?.status, .unlocked)
        await expectTrue(
            await pipeline.publish(
                try privateFreeSnapshot(.locking)
            )
        )
        await expectTrue(
            await pipeline.publish(
                try privateFreeSnapshot(.locked)
            )
        )
        await expectEqual((await slowIterator.next())?.status, .locked)
        await second.cancel()
        await slow.cancel()
    }

    func testPipelineFinishIsPrivateFreeTerminalAndCompletesSubscribers() async throws {
        let pipeline = AtlasVaultProductionPresentationPipeline()
        await expectTrue(await pipeline.start())
        let subscription = await pipeline.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        await expectTrue(
            await pipeline.publish(
                try privateFreeSnapshot(.unlocked)
            )
        )

        await expectTrue(await pipeline.finish())
        await expectEqual((await iterator.next())?.status, .locked)
        await expectNil(await iterator.next())
        await expectEqual(
            await pipeline.currentSnapshot(),
            AtlasVaultPresentationSnapshot(
                status: .locked,
                privateState: nil
            )
        )
        await expectFalse(
            await pipeline.publish(
                try privateFreeSnapshot(.activating)
            )
        )
        await expectFalse(await pipeline.start())
        await expectTrue(await pipeline.finish())
    }

    func testPipelineFinishReleasesUnfulfilledInternalWaiters() async throws {
        let pipelineSource = try source(
            named: "AtlasVaultProductionPresentationPipeline.swift"
        )
        guard pipelineSource.contains("resumeTerminalWaiters()") else {
            XCTFail("Terminal source finish must release every waiter")
            return
        }

        let pipeline = AtlasVaultProductionPresentationPipeline()
        await expectTrue(await pipeline.start())
        let waiter = Task {
            await pipeline.waitUntilSequenceForTesting(3)
        }

        await expectTrue(await pipeline.finish())
        await waiter.value
        await expectFalse(
            await pipeline.publish(
                try privateFreeSnapshot(.activating)
            )
        )
    }

    func testPipelineFinishInterruptsSuspendedPublishWithoutAcknowledgement() async throws {
        let pipelineSource = try source(
            named: "AtlasVaultProductionPresentationPipeline.swift"
        )
        let pipeline = AtlasVaultProductionPresentationPipeline()
        await expectTrue(await pipeline.start())
        await pipeline.suspendDeliveryForTesting()
        let activating = try privateFreeSnapshot(.activating)
        let publish = Task {
            await pipeline.publish(activating)
        }
        await pipeline.waitUntilSequenceForTesting(1)
        let finish = Task { await pipeline.finish() }

        guard !pipelineSource.contains(
            "let acknowledged = await source.sendAndWait(locked)"
        ) else {
            XCTFail("Terminal finish must not wait for another acknowledgement")
            await pipeline.resumeDeliveryForTesting()
            _ = await finish.value
            _ = await publish.value
            return
        }

        await expectTrue(await finish.value)
        await expectFalse(await publish.value)
        await expectEqual(
            await pipeline.currentSnapshot(),
            AtlasVaultPresentationSnapshot(
                status: .locked,
                privateState: nil
            )
        )
    }

    func testHostConstructionInvokesNoDependencyAndStartsInactive() async throws {
        let graph = try makeGraph()
        await expectTrue(await graph.host.isInactiveForTesting())
        await expectEqual(await graph.publicJobs.totalCalls(), 0)
        await expectEqual(await graph.snapshot.restoreCount(), 0)
        await expectEqual(await graph.selector.selectCount(), 0)
        await expectEqual(await graph.runtime.totalCalls(), 0)
        await expectEqual(await graph.lifecycle.totalCalls(), 0)
        await expectEqual(await graph.presentation.totalCalls(), 0)
        await expectEqual(await graph.owner.totalCalls(), 0)
        await expectEqual(graph.controllerBuilder.callCount, 0)
    }

    func testStartWithNilSnapshotIsExplicitPrivateFreeAndSideEffectBounded() async throws {
        let graph = try makeGraph()
        let state = try await graph.host.start()

        await expectEqual(state.mode, .lockedPublic)
        await expectEqual(state.publicShell.vaultStatus, .locked)
        await expectEqual(state.publicShell.serviceStatus, .checking)
        await expectEqual(state.publicShell.cacheFreshness, .unavailable)
        await expectEqual(state.publicShell.searchQuery, "")
        await expectEqual(state.publicShell.publicJobs, [])
        await expectFalse(state.publicShell.isSearching)
        await expectTrue(state.publicShell.canRequestUnlock)
        await expectEqual(await graph.snapshot.restoreCount(), 1)
        await expectEqual(await graph.presentation.startCount(), 1)
        await expectEqual(await graph.selector.selectCount(), 0)
        await expectEqual(await graph.runtime.totalCalls(), 0)
        await expectEqual(await graph.lifecycle.totalCalls(), 0)
        await expectEqual(graph.controllerBuilder.callCount, 0)
        await expectTrue(await graph.owner.allStatesArePrivateFree())
    }

    func testStartRestoresOnlyPublicSnapshotWithConservativeFreshness() async throws {
        let snapshot = try makeSnapshot(jobID: "FAKE_SNAPSHOT_JOB")
        let graph = try makeGraph(snapshot: .success(snapshot))
        let state = try await graph.host.start()

        await expectEqual(state.publicShell.publicJobs, snapshot.jobs)
        await expectEqual(state.publicShell.serviceStatus, .available)
        await expectEqual(state.publicShell.cacheFreshness, .stale)
        await expectEqual(state.publicShell.searchQuery, "")
        await expectEqual(await graph.snapshot.restoreCount(), 1)
        await expectEqual(await graph.runtime.totalCalls(), 0)
    }

    func testUnavailableSnapshotFailsOpenToEmptyPublicShell() async throws {
        let graph = try makeGraph(snapshot: .failure(.invalidSnapshot))
        let state = try await graph.host.start()

        await expectEqual(state.mode, .lockedPublic)
        await expectEqual(state.publicShell.publicJobs, [])
        await expectEqual(state.publicShell.serviceStatus, .unavailable)
        await expectEqual(state.publicShell.cacheFreshness, .unavailable)
        await expectTrue(await graph.owner.allStatesArePrivateFree())
    }

    func testConcurrentAndRepeatedStartRestoreOnlyOnce() async throws {
        let gate = HostSuspensionGate()
        let graph = try makeGraph(snapshotGate: gate)
        let first = Task { await captureStart(graph.host) }
        await gate.waitUntilEntered()
        let second = Task { await captureStart(graph.host) }
        await gate.release()

        await expectNoThrow(try await first.value.get())
        await expectNoThrow(try await second.value.get())
        _ = try await graph.host.start()
        await expectEqual(await graph.snapshot.restoreCount(), 1)
        await expectEqual(await graph.presentation.startCount(), 1)
    }

    func testStartKeepsAdmissionClosedUntilOwnerAcknowledges() async throws {
        let ownerGate = HostSuspensionGate()
        let graph = try makeGraph(ownerGate: ownerGate)

        let start = Task { await captureStart(graph.host) }
        await ownerGate.waitUntilEntered()
        let whileSuspended = await graph.host.requestUnlockPanel()

        await expectFalse(whileSuspended.publicShell.canRequestUnlock)
        await expectEqual(await graph.selector.selectCount(), 0)
        await ownerGate.release()
        await expectNoThrow(try await start.value.get())
        await expectTrue(
            (await graph.host.currentFlowState())
                .publicShell.canRequestUnlock
        )
    }

    func testStartAcknowledgementFailuresFailClosedBeforeSelection() async throws {
        let pipelineFailure = try makeGraph()
        await pipelineFailure.presentation.setPublishResults([false])
        await assertStartPresentationFailure(pipelineFailure.host)
        _ = await pipelineFailure.host.requestUnlockPanel()
        await expectEqual(await pipelineFailure.selector.selectCount(), 0)
        await expectFalse(
            (await pipelineFailure.host.currentFlowState())
                .publicShell.canRequestUnlock
        )

        let ownerFailure = try makeGraph()
        await ownerFailure.owner.setResults([false])
        await assertStartPresentationFailure(ownerFailure.host)
        _ = await ownerFailure.host.requestUnlockPanel()
        await expectEqual(await ownerFailure.selector.selectCount(), 0)
    }

    func testPublicSearchUpdatesShellWithoutPrivateDependencies() async throws {
        let result = try makeSearchResult(
            jobID: "FAKE_SEARCH_JOB",
            title: "Fake Search Role"
        )
        let graph = try makeGraph(searchPlans: [
            HostPublicJobsFake.Plan(result: .success(result))
        ])
        _ = try await graph.host.start()
        let request = try searchRequest(query: Self.fakeQuery)
        let returned = try await graph.host.searchPublicJobs(request)
        let state = await graph.host.currentFlowState()

        await expectEqual(returned, result)
        await expectEqual(state.publicShell.searchQuery, Self.fakeQuery)
        await expectEqual(state.publicShell.publicJobs, result.jobs)
        await expectEqual(state.publicShell.serviceStatus, .available)
        await expectEqual(state.publicShell.cacheFreshness, .current)
        await expectFalse(state.publicShell.isSearching)
        await expectEqual(await graph.selector.selectCount(), 0)
        await expectEqual(await graph.runtime.totalCalls(), 0)
        await expectFalse(graph.host.description.contains(Self.fakeQuery))
    }

    func testSearchBeforeStartAndAfterStopFailsWithoutCallingService() async throws {
        let graph = try makeGraph()
        let request = try searchRequest(query: "FAKE_BOUNDARY_QUERY")

        let beforeStart = await captureSearch(graph.host, request)
        await expectEqual(beforeStart, .failure(.unavailable))
        await expectEqual(await graph.publicJobs.totalCalls(), 0)

        _ = try await graph.host.start()
        _ = await graph.host.stop()
        let afterStop = await captureSearch(graph.host, request)
        await expectEqual(afterStop, .failure(.unavailable))
        await expectEqual(await graph.publicJobs.totalCalls(), 0)
    }

    func testSupersedingSearchCancelsAndRejectsLateResult() async throws {
        let firstGate = HostSuspensionGate()
        let secondGate = HostSuspensionGate()
        let firstResult = try makeSearchResult(
            jobID: "FAKE_STALE_JOB",
            title: "Fake Stale Role"
        )
        let secondResult = try makeSearchResult(
            jobID: "FAKE_CURRENT_JOB",
            title: "Fake Current Role"
        )
        let graph = try makeGraph(searchPlans: [
            HostPublicJobsFake.Plan(
                result: .success(firstResult),
                gate: firstGate
            ),
            HostPublicJobsFake.Plan(
                result: .success(secondResult),
                gate: secondGate
            ),
        ])
        _ = try await graph.host.start()
        let firstRequest = try searchRequest(query: "FAKE_FIRST")
        let secondRequest = try searchRequest(query: "FAKE_SECOND")

        let first = Task {
            await captureSearch(graph.host, firstRequest)
        }
        await firstGate.waitUntilEntered()
        let second = Task {
            await captureSearch(graph.host, secondRequest)
        }
        await secondGate.waitUntilEntered()
        await secondGate.release()
        await expectEqual(try await second.value.get(), secondResult)
        await firstGate.release()
        await expectThrowsError(try await first.value.get()) { error in
            await expectEqual(
                error as? AtlasPublicJobServiceError,
                .unavailable
            )
        }

        let state = await graph.host.currentFlowState()
        await expectEqual(state.publicShell.publicJobs, secondResult.jobs)
        await expectEqual(state.publicShell.searchQuery, "FAKE_SECOND")
        await expectEqual(await graph.publicJobs.cancelledCalls(), 1)
    }

    func testPanelAndExplicitLockDoNotCancelIndependentPublicSearch() async throws {
        let gate = HostSuspensionGate()
        let result = try makeSearchResult(
            jobID: "FAKE_INDEPENDENT_JOB",
            title: "Fake Independent Role"
        )
        let graph = try makeGraph(
            searchPlans: [
                HostPublicJobsFake.Plan(
                    result: .success(result),
                    gate: gate
                )
            ],
            selection: .success(
                .selected(try selectedVaultID())
            )
        )
        _ = try await graph.host.start()
        let independentRequest = try searchRequest(
            query: "FAKE_INDEPENDENT"
        )
        let search = Task {
            await captureSearch(graph.host, independentRequest)
        }
        await gate.waitUntilEntered()
        await expectEqual(
            (await graph.host.requestUnlockPanel()).mode,
            .unlockPanel
        )
        _ = await graph.host.lock()
        await expectEqual(await graph.publicJobs.cancelledCalls(), 0)
        await gate.release()
        await expectEqual(try await search.value.get(), result)
        await expectEqual(
            (await graph.host.currentFlowState()).publicShell.publicJobs,
            result.jobs
        )
    }

    func testSearchCompletionDoesNotInvalidateSuspendedVaultSelection() async throws {
        let searchGate = HostSuspensionGate()
        let selectionGate = HostSuspensionGate()
        let result = try makeSearchResult(
            jobID: "FAKE_SELECTION_SEARCH_JOB",
            title: "Fake Selection Search Role"
        )
        let graph = try makeGraph(
            searchPlans: [
                HostPublicJobsFake.Plan(
                    result: .success(result),
                    gate: searchGate
                )
            ],
            selection: .success(
                .selected(try selectedVaultID())
            ),
            selectionGate: selectionGate
        )
        _ = try await graph.host.start()
        let request = try searchRequest(
            query: "FAKE_DURING_SELECTION"
        )

        let search = Task {
            await captureSearch(graph.host, request)
        }
        await searchGate.waitUntilEntered()
        let panel = Task { await graph.host.requestUnlockPanel() }
        await selectionGate.waitUntilEntered()

        await searchGate.release()
        await expectEqual(try await search.value.get(), result)
        await selectionGate.release()

        let panelState = await panel.value
        await expectEqual(panelState.mode, .unlockPanel)
        await expectEqual(panelState.publicShell.publicJobs, result.jobs)
        await expectEqual(graph.controllerBuilder.callCount, 1)
        await expectTrue(await graph.host.hasUnlockControllerForTesting())
    }

    func testSearchCompletionDoesNotInvalidateSuspendedUnlockSubmit() async throws {
        let submitGate = HostSuspensionGate()
        let result = try makeSearchResult(
            jobID: "FAKE_SUBMIT_SEARCH_JOB",
            title: "Fake Submit Search Role"
        )
        let graph = try makeGraph(
            searchPlans: [
                HostPublicJobsFake.Plan(result: .success(result))
            ],
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked),
            submitGate: submitGate
        )
        try await prepareLocalKeyUnlock(graph)

        let submit = Task {
            await graph.host.submitUnlock(.localKey, timeout: nil)
        }
        await submitGate.waitUntilEntered()
        let searchResult = try await graph.host.searchPublicJobs(
            try searchRequest(query: "FAKE_DURING_SUBMIT")
        )
        await expectEqual(searchResult, result)
        await submitGate.release()

        let submitState = await submit.value
        await expectEqual(submitState.mode, .unlockedTransition)
        await expectEqual(submitState.publicShell.publicJobs, result.jobs)
        await expectEqual(await graph.runtime.lockCalls(), 0)
        await expectEqual(await graph.controller.submitCount(), 1)
    }

    func testStopCancelsSearchAndIgnoresLateCompletion() async throws {
        let gate = HostSuspensionGate()
        let result = try makeSearchResult(
            jobID: "FAKE_STOPPED_JOB",
            title: "Fake Stopped Role"
        )
        let graph = try makeGraph(searchPlans: [
            HostPublicJobsFake.Plan(
                result: .success(result),
                gate: gate
            )
        ])
        _ = try await graph.host.start()
        let stoppedRequest = try searchRequest(
            query: "FAKE_STOPPED_QUERY"
        )
        let search = Task {
            await captureSearch(graph.host, stoppedRequest)
        }
        await gate.waitUntilEntered()
        let stop = Task { await graph.host.stop() }
        await graph.publicJobs.waitUntilCancellation()
        await gate.release()
        let stopped = await stop.value

        await expectThrowsError(try await search.value.get())
        await expectFalse(stopped.publicShell.isSearching)
        await expectEqual(
            (await graph.host.currentFlowState()).publicShell.publicJobs,
            []
        )
        await expectEqual(await graph.publicJobs.cancelledCalls(), 1)
    }

    func testPanelSelectionNoneAndFailureRemainNonSensitive() async throws {
        let noVault = try makeGraph(selection: .success(.none))
        _ = try await noVault.host.start()
        let noVaultState = await noVault.host.requestUnlockPanel()
        await expectEqual(noVaultState.publicShell.vaultStatus, .noVault)
        await expectEqual(noVaultState.mode, .lockedPublic)
        await expectFalse(noVaultState.publicShell.canRequestUnlock)
        await expectEqual(noVault.controllerBuilder.callCount, 0)
        await expectEqual(await noVault.runtime.totalCalls(), 0)

        let unavailable = try makeGraph(
            selection: .failure(.invalidRegistry)
        )
        _ = try await unavailable.host.start()
        let unavailableState =
            await unavailable.host.requestUnlockPanel()
        await expectEqual(
            unavailableState.publicShell.vaultStatus,
            .keyUnavailable
        )
        await expectEqual(unavailableState.mode, .lockedPublic)
        await expectEqual(unavailable.controllerBuilder.callCount, 0)
        await expectFalse(
            unavailableState.description.contains(Self.fakeVaultID)
        )
    }

    func testNoVaultPublicationFailurePreservesNoVaultAndClosedAdmission() async throws {
        let graph = try makeGraph(selection: .success(.none))
        _ = try await graph.host.start()
        await graph.presentation.setPublishResults([false])

        let state = await graph.host.requestUnlockPanel()

        await expectEqual(state.publicShell.vaultStatus, .noVault)
        await expectFalse(state.publicShell.canRequestUnlock)
        await expectEqual(state.mode, .lockedPublic)
        await expectEqual(graph.controllerBuilder.callCount, 0)
        _ = await graph.host.requestUnlockPanel()
        await expectEqual(await graph.selector.selectCount(), 1)
    }

    func testConcurrentPanelRequestsSelectOnceAndReuseOneController() async throws {
        let gate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            selectionGate: gate
        )
        _ = try await graph.host.start()
        let first = Task { await graph.host.requestUnlockPanel() }
        await gate.waitUntilEntered()
        let second = Task { await graph.host.requestUnlockPanel() }
        await gate.release()

        await expectEqual((await first.value).mode, .unlockPanel)
        await expectEqual((await second.value).mode, .unlockPanel)
        _ = await graph.host.requestUnlockPanel()
        await expectEqual(await graph.selector.selectCount(), 1)
        await expectEqual(graph.controllerBuilder.callCount, 1)
        await expectEqual(
            graph.controllerBuilder.capturedCapabilities?.availableMethods,
            [.localKey]
        )
        await expectEqual(
            graph.controllerBuilder.capturedVaultID?.vaultID,
            Self.fakeVaultID
        )
        await expectTrue(await graph.host.hasUnlockControllerForTesting())
        await expectFalse(
            (await graph.host.currentFlowState()).description
                .contains(Self.fakeVaultID)
        )
        await expectEqual(await graph.runtime.activationCalls(), 0)
    }

    func testCancelAndDisappearanceWithoutSubmitRemainPrivateFree() async throws {
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            )
        )
        _ = try await graph.host.start()
        _ = await graph.host.requestUnlockPanel()

        let cancelled = await graph.host.cancelUnlock()
        await expectEqual(cancelled.mode, .lockedPublic)
        await expectTrue(cancelled.publicShell.canRequestUnlock)

        let reopened = await graph.host.requestUnlockPanel()
        await expectEqual(reopened.mode, .unlockPanel)
        await expectEqual(reopened.unlockPanelState?.status, .locked)
        let disappeared = await graph.host.unlockPanelDidDisappear()
        await expectEqual(disappeared.mode, .lockedPublic)
        await expectTrue(disappeared.publicShell.canRequestUnlock)
        await expectEqual(await graph.selector.selectCount(), 1)
        await expectEqual(graph.controllerBuilder.callCount, 1)
        await expectEqual(await graph.controller.cancelCount(), 1)
        await expectEqual(await graph.controller.disappearanceCount(), 1)
        await expectEqual(await graph.runtime.lockCalls(), 0)
        await expectTrue(await graph.owner.allStatesArePrivateFree())
    }

    func testStopDuringSuspendedSelectionReleasesEveryPanelCaller() async throws {
        let hostSource = try source(named: "AtlasVaultProductionHost.swift")
        let gate = HostSuspensionGate()
        let selectedID = try selectedVaultID()
        let graph = try makeGraph(
            selection: .success(.selected(selectedID)),
            selectionGate: gate
        )
        _ = try await graph.host.start()

        let first = Task { await graph.host.requestUnlockPanel() }
        await gate.waitUntilEntered()
        let second = Task { await graph.host.requestUnlockPanel() }
        let stopped = await graph.host.stop()

        await expectFalse(stopped.publicShell.canRequestUnlock)
        guard hostSource.contains("completeSelection") else {
            XCTFail("Teardown must release the initiating selection caller")
            await gate.release()
            _ = await first.value
            _ = await second.value
            return
        }

        await expectEqual((await first.value).mode, .lockedPublic)
        await expectEqual((await second.value).mode, .lockedPublic)
        await gate.release()
        await expectEqual(graph.controllerBuilder.callCount, 0)
    }

    func testAbandonedSelectionCallersReturnBeforeSlowBarrierCompletes() async throws {
        let hostSource = try source(named: "AtlasVaultProductionHost.swift")
        let selectionGate = HostSuspensionGate()
        let ownerGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(.selected(try selectedVaultID())),
            selectionGate: selectionGate,
            runtimeStatus: .unlocked
        )
        _ = try await graph.host.start()
        let first = Task { await graph.host.requestUnlockPanel() }
        await selectionGate.waitUntilEntered()
        let second = Task { await graph.host.requestUnlockPanel() }
        await graph.owner.setGate(ownerGate)
        let lock = Task { await graph.host.lock() }
        await ownerGate.waitUntilEntered()

        guard hostSource.contains("abandonSelectionAndResumeCallers") else {
            XCTFail("Selection callers must be released before barrier awaits")
            await ownerGate.release()
            _ = await lock.value
            await selectionGate.release()
            _ = await first.value
            _ = await second.value
            return
        }

        await expectEqual((await first.value).mode, .lockedPublic)
        await expectEqual((await second.value).mode, .lockedPublic)
        await expectFalse(
            (await graph.host.currentFlowState())
                .publicShell.canRequestUnlock
        )
        await ownerGate.release()
        _ = await lock.value
        await selectionGate.release()
        await expectEqual(graph.controllerBuilder.callCount, 0)
    }

    func testStopReleasesSelectionCallersBeforeWaitingForSearchTeardown() async throws {
        let hostSource = try source(named: "AtlasVaultProductionHost.swift")
        let searchGate = HostSuspensionGate()
        let selectionGate = HostSuspensionGate()
        let searchResult = try makeSearchResult(
            jobID: "FAKE_STOP_SELECTION_JOB",
            title: "Fake Stop Selection Role"
        )
        let graph = try makeGraph(
            searchPlans: [
                HostPublicJobsFake.Plan(
                    result: .success(searchResult),
                    gate: searchGate,
                    releasesGateOnCancellation: false
                )
            ],
            selection: .success(.selected(try selectedVaultID())),
            selectionGate: selectionGate
        )
        _ = try await graph.host.start()
        let request = try searchRequest(
            query: "FAKE_STOP_SELECTION_QUERY"
        )
        let search = Task {
            await captureSearch(graph.host, request)
        }
        await searchGate.waitUntilEntered()
        let panel = Task { await graph.host.requestUnlockPanel() }
        await selectionGate.waitUntilEntered()
        let stop = Task { await graph.host.stop() }
        await graph.publicJobs.waitUntilCancellation()

        guard hostSource.contains(
            "beginTerminalStop()\n        abandonSelectionAndResumeCallers()"
        ) else {
            XCTFail("Stop must release selection callers before search teardown")
            await searchGate.release()
            await selectionGate.release()
            _ = await stop.value
            _ = await search.value
            _ = await panel.value
            return
        }

        let abandoned = await panel.value
        await expectEqual(abandoned.mode, .lockedPublic)
        await expectFalse(abandoned.publicShell.canRequestUnlock)
        await searchGate.release()
        await expectEqual((await stop.value).mode, .lockedPublic)
        await expectThrowsError(try await search.value.get())
        await selectionGate.release()
        await expectEqual(graph.controllerBuilder.callCount, 0)
    }

    func testSuccessfulSubmitUsesHostOwnedTaskAndAuthoritativeRuntimeStatus() async throws {
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked)
        )
        try await prepareLocalKeyUnlock(graph)
        let result = await graph.host.submitUnlock(
            .localKey,
            timeout: nil
        )

        await expectEqual(result.mode, .unlockedTransition)
        await expectNil(result.unlockPanelState)
        await expectEqual(await graph.controller.submitCount(), 1)
        await expectEqual(await graph.runtime.activationCalls(), 0)
        await expectTrue(await graph.owner.allStatesArePrivateFree())
        await expectTrue(
            await graph.presentation.publishedStatuses()
                .contains(.unlocked)
        )
    }

    func testPostSuccessDisappearanceEntersPrivateFreeBarrier() async throws {
        let ownerGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(.selected(try selectedVaultID())),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked),
            disappearanceResult:
                unlockState(.hostReconciliationRequired)
        )
        try await prepareLocalKeyUnlock(graph)
        await expectEqual(
            (await graph.host.submitUnlock(.localKey, timeout: nil)).mode,
            .unlockedTransition
        )
        await graph.owner.setGate(ownerGate)

        let disappearance = Task {
            await graph.host.unlockPanelDidDisappear()
        }
        await ownerGate.waitUntilEntered()

        let reconciling = await graph.host.currentFlowState()
        await expectEqual(
            reconciling.unlockPanelState?.status,
            .hostReconciliationRequired
        )
        await expectFalse(reconciling.publicShell.canRequestUnlock)
        await expectEqual(
            await graph.presentation.publishedStatuses().last,
            .locking
        )
        _ = await graph.host.submitUnlock(.localKey, timeout: nil)
        await expectEqual(await graph.controller.submitCount(), 1)
        await expectEqual(await graph.selector.selectCount(), 1)
        await expectEqual(graph.controllerBuilder.callCount, 1)

        await ownerGate.release()
        let locked = await disappearance.value
        await expectEqual(locked.mode, .lockedPublic)
        await expectTrue(locked.publicShell.canRequestUnlock)
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await expectEqual(await graph.controller.hostLockCount(), 1)
        await expectFalse(await graph.host.hasSelectedVaultForTesting())
        await expectTrue(await graph.owner.allStatesArePrivateFree())
    }

    func testPostSuccessCancellationEntersPrivateFreeBarrier() async throws {
        let graph = try makeGraph(
            selection: .success(.selected(try selectedVaultID())),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked),
            cancelResult: unlockState(.hostReconciliationRequired)
        )
        try await prepareLocalKeyUnlock(graph)
        await expectEqual(
            (await graph.host.submitUnlock(.localKey, timeout: nil)).mode,
            .unlockedTransition
        )

        let locked = await graph.host.cancelUnlock()

        await expectEqual(locked.mode, .lockedPublic)
        await expectTrue(locked.publicShell.canRequestUnlock)
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await expectEqual(await graph.controller.hostLockCount(), 1)
        await expectFalse(await graph.host.hasSelectedVaultForTesting())
        await expectTrue(
            await graph.presentation.publishedStatuses().contains(.locking)
        )
    }

    func testDirectUnlockedCallbackResultAlsoRequiresBarrier() async throws {
        let graph = try makeGraph(
            selection: .success(.selected(try selectedVaultID())),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked),
            disappearanceResult: unlockState(.unlocked)
        )
        try await prepareLocalKeyUnlock(graph)
        _ = await graph.host.submitUnlock(.localKey, timeout: nil)

        let locked = await graph.host.unlockPanelDidDisappear()

        await expectEqual(locked.mode, .lockedPublic)
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await expectEqual(await graph.controller.hostLockCount(), 1)
        await expectFalse(await graph.host.hasSelectedVaultForTesting())
    }

    func testPostSuccessCallbackBarrierFailureRetriesThroughExplicitLock() async throws {
        for callback in PostSuccessCallback.allCases {
            let graph = try makeGraph(
                selection: .success(.selected(try selectedVaultID())),
                runtimeStatus: .unlocked,
                submitResult: unlockState(.unlocked),
                cancelResult: unlockState(.hostReconciliationRequired),
                disappearanceResult:
                    unlockState(.hostReconciliationRequired)
            )
            try await prepareLocalKeyUnlock(graph)
            _ = await graph.host.submitUnlock(.localKey, timeout: nil)
            await graph.runtime.setLockResult(.unlocked)

            let failed: AtlasLockedShellUnlockFlowState
            switch callback {
            case .cancel:
                failed = await graph.host.cancelUnlock()
            case .disappearance:
                failed = await graph.host.unlockPanelDidDisappear()
            }

            await expectEqual(
                failed.unlockPanelState?.status,
                .hostReconciliationRequired
            )
            await expectFalse(failed.publicShell.canRequestUnlock)
            await expectTrue(await graph.host.hasSelectedVaultForTesting())

            await graph.runtime.setStatus(.unlocked)
            await graph.runtime.setLockResult(.locked)
            let retried = await graph.host.lock()

            await expectEqual(retried.mode, .lockedPublic)
            await expectTrue(retried.publicShell.canRequestUnlock)
            await expectFalse(await graph.host.hasSelectedVaultForTesting())
            await expectEqual(await graph.runtime.lockCalls(), 2)
        }
    }

    func testDuplicateSubmissionDispatchesOnceAndClearsRejectedSecret() async throws {
        let gate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            submitResult: unlockState(.failed),
            submitGate: gate
        )
        try await prepareLocalKeyUnlock(graph)
        let first = Task {
            await graph.host.submitUnlock(.localKey, timeout: nil)
        }
        await gate.waitUntilEntered()
        let secret = HostSecretBuffer()
        _ = await graph.host.submitUnlock(
            .passphrase(secret),
            timeout: nil
        )

        await expectEqual(await secret.clearCount(), 1)
        await expectEqual(await graph.controller.submitCount(), 1)
        await gate.release()
        _ = await first.value
        await expectEqual(await graph.controller.submitCount(), 1)
    }

    func testCallerCancellationCannotOrphanHostOwnedSubmit() async throws {
        let gate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked),
            submitGate: gate
        )
        try await prepareLocalKeyUnlock(graph)
        let caller = Task {
            await graph.host.submitUnlock(.localKey, timeout: nil)
        }
        await gate.waitUntilEntered()
        caller.cancel()
        await expectTrue(await graph.host.hasActiveSubmitForTesting())
        await gate.release()
        await expectEqual((await caller.value).mode, .unlockedTransition)
        await expectFalse(await graph.host.hasActiveSubmitForTesting())
    }

    func testRuntimeMismatchAfterControllerSuccessReconcilesWithoutUnlockedTransition() async throws {
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .locked,
            submitResult: unlockState(.unlocked)
        )
        try await prepareLocalKeyUnlock(graph)
        let result = await graph.host.submitUnlock(
            .localKey,
            timeout: nil
        )

        await expectEqual(result.mode, .lockedPublic)
        await expectNotEqual(result.mode, .unlockedTransition)
        await expectEqual(await graph.controller.hostLockCount(), 1)
        await expectTrue(
            await graph.presentation.publishedStatuses()
                .contains(.locking)
        )
        await expectFalse(await graph.host.hasSelectedVaultForTesting())
    }

    func testCancelDuringSubmitRetainsTaskAndContainsLateSuccess() async throws {
        let gate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked),
            submitGate: gate,
            cancelResult: unlockState(.hostReconciliationRequired)
        )
        try await prepareLocalKeyUnlock(graph)
        let submit = Task {
            await graph.host.submitUnlock(.localKey, timeout: nil)
        }
        await gate.waitUntilEntered()
        let cancel = Task { await graph.host.cancelUnlock() }
        await expectTrue(await graph.host.hasActiveSubmitForTesting())
        await gate.release()

        let cancelled = await cancel.value
        let submitted = await submit.value
        await expectNotEqual(cancelled.mode, .unlockedTransition)
        await expectNotEqual(submitted.mode, .unlockedTransition)
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await expectFalse(await graph.host.hasActiveSubmitForTesting())
    }

    func testDisappearanceDuringSubmitContainsLateSuccess() async throws {
        let gate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked),
            submitGate: gate,
            disappearanceResult:
                unlockState(.hostReconciliationRequired)
        )
        try await prepareLocalKeyUnlock(graph)
        let submit = Task {
            await graph.host.submitUnlock(.localKey, timeout: nil)
        }
        await gate.waitUntilEntered()
        let disappearance = Task {
            await graph.host.unlockPanelDidDisappear()
        }
        await gate.release()

        await expectNotEqual(
            (await disappearance.value).mode,
            .unlockedTransition
        )
        await expectNotEqual((await submit.value).mode, .unlockedTransition)
        await expectEqual(await graph.runtime.lockCalls(), 1)
    }

    func testStopRemainsTerminalWhenCancelledSubmitCompletesLate() async throws {
        let submitGate = HostSuspensionGate()
        let firstCancelGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked,
            submitResult: unlockState(.unlocked),
            submitGate: submitGate,
            cancelGate: firstCancelGate,
            cancelResult: unlockState(.hostReconciliationRequired)
        )
        try await prepareLocalKeyUnlock(graph)
        let submit = Task {
            await graph.host.submitUnlock(.localKey, timeout: nil)
        }
        await submitGate.waitUntilEntered()
        let cancel = Task { await graph.host.cancelUnlock() }
        await firstCancelGate.waitUntilEntered()

        let stop = Task { await graph.host.stop() }
        await graph.controller.waitUntilCancelCount(2)
        await submitGate.release()
        let stopped = await stop.value
        await expectFalse(stopped.publicShell.canRequestUnlock)
        await assertStartStopped(graph.host)

        await firstCancelGate.release()
        _ = await cancel.value
        _ = await submit.value
        await assertStartStopped(graph.host)
        await expectFalse(
            (await graph.host.currentFlowState()).publicShell.canRequestUnlock
        )
        await expectEqual(await graph.presentation.finishCount(), 1)
    }

    func testPanelCallbackCannotInvalidateReconciliationBarrier() async throws {
        let ownerGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked
        )
        _ = try await graph.host.start()
        _ = await graph.host.requestUnlockPanel()
        await graph.owner.setGate(ownerGate)

        let lock = Task { await graph.host.lock() }
        await ownerGate.waitUntilEntered()
        let callback = await graph.host.unlockPanelDidDisappear()

        await expectEqual(
            callback.unlockPanelState?.status,
            .hostReconciliationRequired
        )
        await expectEqual(await graph.controller.disappearanceCount(), 0)
        await ownerGate.release()

        let locked = await lock.value
        await expectEqual(locked.mode, .lockedPublic)
        await expectTrue(locked.publicShell.canRequestUnlock)
        await expectFalse(await graph.host.hasSelectedVaultForTesting())
    }

    func testFailedBarrierRemainsReconciliationAndExplicitLockRetries() async throws {
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            )
        )
        _ = try await graph.host.start()
        _ = await graph.host.requestUnlockPanel()
        await graph.runtime.setStatus(.unlocked)
        await graph.runtime.setLockResult(.unlocked)

        let failed = await graph.host.lock()
        await expectEqual(failed.mode, .unlockPanel)
        await expectEqual(
            failed.unlockPanelState?.status,
            .hostReconciliationRequired
        )
        await expectFalse(failed.publicShell.canRequestUnlock)
        await expectTrue(await graph.host.hasSelectedVaultForTesting())

        await graph.runtime.setStatus(.unlocked)
        await graph.runtime.setLockResult(.locked)
        let retried = await graph.host.lock()
        await expectEqual(retried.mode, .lockedPublic)
        await expectTrue(retried.publicShell.canRequestUnlock)
        await expectFalse(await graph.host.hasSelectedVaultForTesting())
        await expectEqual(await graph.runtime.lockCalls(), 2)
    }

    func testFailedBarrierKeepsPresentationOwnerInReconciliation() async throws {
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            )
        )
        _ = try await graph.host.start()
        _ = await graph.host.requestUnlockPanel()
        await graph.runtime.setStatus(.unlocked)
        await graph.runtime.setLockResult(.unlocked)

        let failed = await graph.host.lock()

        await expectEqual(
            failed.unlockPanelState?.status,
            .hostReconciliationRequired
        )
        await expectEqual(await graph.owner.latestMode(), .unlockPanel)
        await expectEqual(
            await graph.owner.latestUnlockStatus(),
            .hostReconciliationRequired
        )
    }

    func testPipelineAndOwnerBarrierFailuresRemainClosedUntilRetry() async throws {
        let pipelineGraph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            )
        )
        _ = try await pipelineGraph.host.start()
        _ = await pipelineGraph.host.requestUnlockPanel()
        await pipelineGraph.runtime.setStatus(.unlocked)
        await pipelineGraph.presentation.setPublishResults([false])
        await expectEqual(
            (await pipelineGraph.host.lock()).mode,
            .unlockPanel
        )
        await expectFalse(
            (await pipelineGraph.host.currentFlowState())
                .publicShell.canRequestUnlock
        )
        await pipelineGraph.runtime.setStatus(.unlocked)
        await expectEqual(
            (await pipelineGraph.host.lock()).mode,
            .lockedPublic
        )

        let ownerGraph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            )
        )
        _ = try await ownerGraph.host.start()
        _ = await ownerGraph.host.requestUnlockPanel()
        await ownerGraph.runtime.setStatus(.unlocked)
        await ownerGraph.owner.setResults([false])
        await expectEqual((await ownerGraph.host.lock()).mode, .unlockPanel)
        await ownerGraph.runtime.setStatus(.unlocked)
        await expectEqual((await ownerGraph.host.lock()).mode, .lockedPublic)
    }

    func testConcurrentLockCoalescesAndPreservesPublicResults() async throws {
        let job = try makeSnapshot(jobID: "FAKE_LOCKED_PUBLIC_JOB")
        let gate = HostSuspensionGate()
        let graph = try makeGraph(
            snapshot: .success(job),
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked,
            runtimeLockGate: gate
        )
        _ = try await graph.host.start()
        _ = await graph.host.requestUnlockPanel()
        let first = Task { await graph.host.lock() }
        await gate.waitUntilEntered()
        let second = Task { await graph.host.lock() }
        await gate.release()

        await expectEqual((await first.value).mode, .lockedPublic)
        await expectEqual((await second.value).mode, .lockedPublic)
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await expectEqual(
            (await graph.host.currentFlowState()).publicShell.publicJobs,
            job.jobs
        )
    }

    func testLockClosesAdmissionBeforeAwaitingRuntimeStatus() async throws {
        let statusGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(.selected(try selectedVaultID())),
            runtimeStatus: .locked,
            runtimeStatusGate: statusGate
        )
        _ = try await graph.host.start()

        let lock = Task { await graph.host.lock() }
        await statusGate.waitUntilEntered()
        let panel = await graph.host.requestUnlockPanel()

        await expectFalse(panel.publicShell.canRequestUnlock)
        await expectEqual(await graph.selector.selectCount(), 0)
        await expectEqual(graph.controllerBuilder.callCount, 0)

        await statusGate.release()
        let locked = await lock.value
        await expectEqual(locked.mode, .lockedPublic)
        await expectTrue(locked.publicShell.canRequestUnlock)
        await expectEqual(await graph.runtime.lockCalls(), 0)
    }

    func testSafeReopenClosesAdmissionAcrossEverySafetyAwait() async throws {
        for event in [
            AtlasVaultLifecycleEvent.didBecomeActive,
            .protectedDataBecameAvailable,
        ] {
            for suspensionPoint in SafeReopenSuspensionPoint.allCases {
                try await assertSafeReopenAdmissionClosed(
                    event: event,
                    suspensionPoint: suspensionPoint
                )
            }
        }
    }

    func testSafeReopenAcknowledgesClosedAndInteractiveTargets() async throws {
        let ownerGate = HostSuspensionGate()
        let safe = try makeGraph(
            selection: .success(.selected(try selectedVaultID()))
        )
        _ = try await safe.host.start()
        await safe.owner.setGate(ownerGate)

        let reopening = Task {
            await safe.host.handleLifecycleEvent(.didBecomeActive)
        }
        await ownerGate.waitUntilEntered()

        await expectFalse(
            (await safe.host.currentFlowState())
                .publicShell.canRequestUnlock
        )
        _ = await safe.host.requestUnlockPanel()
        await expectEqual(await safe.selector.selectCount(), 0)
        await expectEqual(safe.controllerBuilder.callCount, 0)

        await ownerGate.release()
        let reopened = await reopening.value
        await expectTrue(reopened.publicShell.canRequestUnlock)
        await expectEqual(
            await safe.owner.latestCanRequestUnlock(),
            true
        )

        for runtimeStatus in [
            AtlasVaultRuntimeStatus.activating,
            .locking,
            .unlocked,
            .saving,
            .failed(.activation(.authenticationFailed)),
        ] {
            let unsafe = try makeGraph(runtimeStatus: runtimeStatus)
            _ = try await unsafe.host.start()

            let closed = await unsafe.host.handleLifecycleEvent(
                .protectedDataBecameAvailable
            )

            await expectFalse(closed.publicShell.canRequestUnlock)
            await expectEqual(
                await unsafe.owner.latestCanRequestUnlock(),
                false
            )
            await expectNil(
                await unsafe.presentation.currentSnapshot().privateState
            )
            await expectEqual(await unsafe.selector.selectCount(), 0)
            await expectEqual(unsafe.controllerBuilder.callCount, 0)
        }

        let pendingGrace = try makeGraph(lifecyclePendingGrace: true)
        _ = try await pendingGrace.host.start()
        let graceOwnerCalls = await pendingGrace.owner.totalCalls()
        let graceClosed = await pendingGrace.host.handleLifecycleEvent(
            .didBecomeActive
        )
        await expectFalse(graceClosed.publicShell.canRequestUnlock)
        await expectEqual(
            await pendingGrace.owner.totalCalls(),
            graceOwnerCalls + 1
        )
        await expectEqual(
            await pendingGrace.owner.latestCanRequestUnlock(),
            false
        )

        let protectedDataUnavailable = try makeGraph()
        _ = try await protectedDataUnavailable.host.start()
        _ = await protectedDataUnavailable.host.handleLifecycleEvent(
            .protectedDataBecameUnavailable
        )
        let protectedOwnerCalls = await protectedDataUnavailable.owner
            .totalCalls()
        let protectedClosed = await protectedDataUnavailable.host
            .handleLifecycleEvent(.didBecomeActive)
        await expectFalse(protectedClosed.publicShell.canRequestUnlock)
        await expectEqual(
            await protectedDataUnavailable.owner.totalCalls(),
            protectedOwnerCalls + 1
        )
        await expectEqual(
            await protectedDataUnavailable.owner.latestCanRequestUnlock(),
            false
        )

        let inactiveLifecycle = try makeGraph()
        _ = try await inactiveLifecycle.host.start()
        _ = await inactiveLifecycle.host.handleLifecycleEvent(
            .willResignActive
        )
        let inactiveOwnerCalls = await inactiveLifecycle.owner.totalCalls()
        let inactiveClosed = await inactiveLifecycle.host
            .handleLifecycleEvent(.protectedDataBecameAvailable)
        await expectFalse(inactiveClosed.publicShell.canRequestUnlock)
        await expectEqual(
            await inactiveLifecycle.owner.totalCalls(),
            inactiveOwnerCalls + 1
        )
        await expectEqual(
            await inactiveLifecycle.owner.latestCanRequestUnlock(),
            false
        )
    }

    func testUnsafeSafeReopenAcknowledgementFailureReconcilesAndRetries() async throws {
        let graph = try makeGraph(lifecyclePendingGrace: true)
        _ = try await graph.host.start()
        await graph.owner.setResults([false, false])

        let failed = await graph.host.handleLifecycleEvent(.didBecomeActive)

        await expectEqual(
            failed.unlockPanelState?.status,
            .hostReconciliationRequired
        )
        await expectFalse(failed.publicShell.canRequestUnlock)
        await expectTrue(
            await graph.presentation.publishedStatuses().contains(.locking)
        )
        _ = await graph.host.requestUnlockPanel()
        await expectEqual(await graph.selector.selectCount(), 0)

        let retried = await graph.host.lock()
        await expectEqual(retried.mode, .lockedPublic)
        await expectFalse(retried.publicShell.canRequestUnlock)
    }

    func testStaleSafeReopenCannotPublishAfterExplicitLockWins() async throws {
        let statusGate = HostSuspensionGate()
        let graph = try makeGraph(lifecycleStatusGate: statusGate)
        _ = try await graph.host.start()

        let reopening = Task {
            await graph.host.handleLifecycleEvent(.didBecomeActive)
        }
        await statusGate.waitUntilEntered()
        _ = await graph.host.lock()
        let ownerCallsAfterLock = await graph.owner.totalCalls()

        await statusGate.release()
        _ = await reopening.value

        await expectEqual(
            await graph.owner.totalCalls(),
            ownerCallsAfterLock
        )
    }

    func testSafeReopenDoesNotInvalidateSuspendedSelection() async throws {
        let selectionGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(.selected(try selectedVaultID())),
            selectionGate: selectionGate
        )
        _ = try await graph.host.start()
        let panel = Task { await graph.host.requestUnlockPanel() }
        await selectionGate.waitUntilEntered()

        let lifecycle = await graph.host.handleLifecycleEvent(
            .protectedDataBecameAvailable
        )

        await expectFalse(lifecycle.publicShell.canRequestUnlock)
        await expectEqual(graph.controllerBuilder.callCount, 0)
        await selectionGate.release()

        let selected = await panel.value
        await expectEqual(selected.mode, .unlockPanel)
        await expectTrue(selected.publicShell.canRequestUnlock)
        await expectEqual(await graph.selector.selectCount(), 1)
        await expectEqual(graph.controllerBuilder.callCount, 1)
    }

    func testSafeLifecycleEligibilityPersistsBeforeAndDuringStart() async throws {
        for event in [
            AtlasVaultLifecycleEvent.didBecomeActive,
            .protectedDataBecameAvailable,
        ] {
            let graph = try makeGraph()

            let beforeStart = await graph.host.handleLifecycleEvent(event)

            await expectFalse(beforeStart.publicShell.canRequestUnlock)
            await expectEqual(await graph.lifecycle.totalCalls(), 2)
            await expectEqual(await graph.runtime.totalCalls(), 0)
            await expectEqual(await graph.presentation.totalCalls(), 0)
            await expectEqual(await graph.owner.totalCalls(), 0)
            await expectTrue(
                (try await graph.host.start()).publicShell.canRequestUnlock
            )
        }

        let restoreGate = HostSuspensionGate()
        let restoring = try makeGraph(snapshotGate: restoreGate)
        let restoreStart = Task { await captureStart(restoring.host) }
        await restoreGate.waitUntilEntered()

        _ = await restoring.host.handleLifecycleEvent(.didBecomeActive)

        await expectEqual(await restoring.lifecycle.totalCalls(), 2)
        await expectEqual(await restoring.runtime.totalCalls(), 0)
        await restoreGate.release()
        await expectTrue(
            (try await restoreStart.value.get()).publicShell.canRequestUnlock
        )

        let ownerGate = HostSuspensionGate()
        let acknowledging = try makeGraph(ownerGate: ownerGate)
        let ownerStart = Task { await captureStart(acknowledging.host) }
        await ownerGate.waitUntilEntered()

        _ = await acknowledging.host.handleLifecycleEvent(
            .protectedDataBecameAvailable
        )

        await expectEqual(await acknowledging.lifecycle.totalCalls(), 2)
        await expectEqual(await acknowledging.runtime.totalCalls(), 0)
        await ownerGate.release()
        await expectTrue(
            (try await ownerStart.value.get()).publicShell.canRequestUnlock
        )
    }

    func testSafeLifecycleEligibilitySurvivesActiveSubmit() async throws {
        let submitGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(.selected(try selectedVaultID())),
            submitResult: unlockState(.failed),
            submitGate: submitGate
        )
        try await prepareLocalKeyUnlock(graph)
        let submit = Task {
            await graph.host.submitUnlock(.localKey, timeout: nil)
        }
        await submitGate.waitUntilEntered()

        let checking = await graph.host.handleLifecycleEvent(
            .protectedDataBecameAvailable
        )

        await expectFalse(checking.publicShell.canRequestUnlock)
        await expectTrue(await graph.host.hasActiveSubmitForTesting())
        await submitGate.release()
        let completed = await submit.value
        await expectTrue(completed.publicShell.canRequestUnlock)
        await expectEqual(await graph.controller.submitCount(), 1)
    }

    func testSafeLifecycleEligibilitySurvivesTemporaryRuntimeState() async throws {
        let graph = try makeGraph(runtimeStatus: .activating)
        await expectTrue(
            (try await graph.host.start()).publicShell.canRequestUnlock
        )

        let checking = await graph.host.handleLifecycleEvent(.didBecomeActive)

        await expectFalse(checking.publicShell.canRequestUnlock)
        await graph.runtime.setStatus(.locked)
        let locked = await graph.host.lock()
        await expectTrue(locked.publicShell.canRequestUnlock)
        await expectEqual(await graph.lifecycle.events(), [.didBecomeActive])
    }

    func testLatestSafeLifecycleEventOwnsEligibilityCommit() async throws {
        let firstHandleGate = HostSuspensionGate()
        let graph = try makeGraph(lifecycleHandleGate: firstHandleGate)
        _ = try await graph.host.start()
        let ownerCalls = await graph.owner.totalCalls()
        let first = Task {
            await graph.host.handleLifecycleEvent(.didBecomeActive)
        }
        await firstHandleGate.waitUntilEntered()

        let second = await graph.host.handleLifecycleEvent(
            .protectedDataBecameAvailable
        )

        await expectTrue(second.publicShell.canRequestUnlock)
        await firstHandleGate.release()
        _ = await first.value
        await expectEqual(await graph.owner.totalCalls(), ownerCalls + 1)
    }

    func testLifecycleCloseEventSupersedesSuspendedSafeEvent() async throws {
        let safeHandleGate = HostSuspensionGate()
        let graph = try makeGraph(lifecycleHandleGate: safeHandleGate)
        _ = try await graph.host.start()
        let safe = Task {
            await graph.host.handleLifecycleEvent(.didBecomeActive)
        }
        await safeHandleGate.waitUntilEntered()

        let closed = await graph.host.handleLifecycleEvent(.willResignActive)
        let ownerCallsAfterClose = await graph.owner.totalCalls()

        await expectFalse(closed.publicShell.canRequestUnlock)
        await safeHandleGate.release()
        _ = await safe.value
        await expectEqual(
            await graph.owner.totalCalls(),
            ownerCallsAfterClose
        )
        _ = await graph.host.requestUnlockPanel()
        await expectEqual(await graph.selector.selectCount(), 0)
        await expectEqual(
            await graph.lifecycle.events(),
            [.didBecomeActive, .willResignActive]
        )
    }

    func testSafeLifecycleCheckBlocksSelectionReopenUntilCurrentCheckFinishes() async throws {
        let selectionGate = HostSuspensionGate()
        let lifecycleStatusGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(.selected(try selectedVaultID())),
            selectionGate: selectionGate,
            lifecycleStatusGate: lifecycleStatusGate
        )
        _ = try await graph.host.start()
        let panel = Task { await graph.host.requestUnlockPanel() }
        await selectionGate.waitUntilEntered()
        let lifecycle = Task {
            await graph.host.handleLifecycleEvent(.didBecomeActive)
        }
        await lifecycleStatusGate.waitUntilEntered()

        await selectionGate.release()
        let selected = await panel.value
        await expectFalse(selected.publicShell.canRequestUnlock)

        await lifecycleStatusGate.release()
        let reopened = await lifecycle.value
        await expectTrue(reopened.publicShell.canRequestUnlock)
    }

    func testLifecycleEligibilityIsSeparateFromTransientAdmissionInSource() async throws {
        let hostSource = try source(named: "AtlasVaultProductionHost.swift")

        await expectTrue(hostSource.contains("lifecycleEventRevision"))
        await expectTrue(hostSource.contains("safeLifecycleCheckRevision"))
        await expectFalse(
            hostSource.contains("lifecycleAdmissionPermitted = mayReopen")
        )
    }

    func testLifecycleNeverUnlocksAndLockingEventsCompleteBarrier() async throws {
        let graph = try makeGraph()
        _ = try await graph.host.start()

        _ = await graph.host.handleLifecycleEvent(.didBecomeActive)
        _ = await graph.host.handleLifecycleEvent(
            .protectedDataBecameAvailable
        )
        await expectEqual(await graph.runtime.activationCalls(), 0)
        await expectEqual(await graph.runtime.lockCalls(), 0)

        let resigned = await graph.host.handleLifecycleEvent(
            .willResignActive
        )
        await expectFalse(resigned.publicShell.canRequestUnlock)
        let active = await graph.host.handleLifecycleEvent(.didBecomeActive)
        await expectTrue(active.publicShell.canRequestUnlock)

        await graph.runtime.setStatus(.unlocked)
        _ = await graph.host.handleLifecycleEvent(.didEnterBackground)
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await expectFalse(
            (await graph.host.currentFlowState())
                .publicShell.canRequestUnlock
        )

        let events = await graph.lifecycle.events()
        await expectEqual(
            events,
            [
                .didBecomeActive,
                .protectedDataBecameAvailable,
                .willResignActive,
                .didBecomeActive,
                .didEnterBackground,
            ]
        )
    }

    func testLifecycleCloseEventsBeforeAndDuringStartKeepAdmissionClosed() async throws {
        let inactive = try makeGraph()
        _ = await inactive.host.handleLifecycleEvent(
            .protectedDataBecameUnavailable
        )
        let inactiveStart = try await inactive.host.start()
        await expectFalse(inactiveStart.publicShell.canRequestUnlock)
        await expectEqual(
            await inactive.lifecycle.events(),
            [.protectedDataBecameUnavailable]
        )
        await expectEqual(await inactive.runtime.totalCalls(), 0)

        let restoreGate = HostSuspensionGate()
        let starting = try makeGraph(snapshotGate: restoreGate)
        let start = Task { await captureStart(starting.host) }
        await restoreGate.waitUntilEntered()
        _ = await starting.host.handleLifecycleEvent(.didEnterBackground)
        await restoreGate.release()

        let started = try await start.value.get()
        await expectFalse(started.publicShell.canRequestUnlock)
        await expectEqual(
            await starting.lifecycle.events(),
            [.didEnterBackground]
        )
        await expectEqual(await starting.runtime.totalCalls(), 0)

        let ownerGate = HostSuspensionGate()
        let acknowledging = try makeGraph(ownerGate: ownerGate)
        let firstStart = Task { await captureStart(acknowledging.host) }
        await ownerGate.waitUntilEntered()
        _ = await acknowledging.host.handleLifecycleEvent(
            .didEnterBackground
        )
        await ownerGate.release()
        await expectEqual(
            await firstStart.value,
            .failure(.presentationUnavailable)
        )

        let retried = try await acknowledging.host.start()
        await expectFalse(retried.publicShell.canRequestUnlock)
        await expectEqual(
            await acknowledging.lifecycle.events(),
            [.didEnterBackground]
        )
        await expectEqual(await acknowledging.runtime.totalCalls(), 0)

        let hostSource = try source(named: "AtlasVaultProductionHost.swift")
        await expectFalse(
            hostSource.contains("if case .willTerminate = event")
        )
    }

    func testProtectedDataLossLocksAndTerminationStopsTerminally() async throws {
        let graph = try makeGraph(runtimeStatus: .unlocked)
        _ = try await graph.host.start()
        _ = await graph.host.handleLifecycleEvent(
            .protectedDataBecameUnavailable
        )
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await expectFalse(
            (await graph.host.currentFlowState())
                .publicShell.canRequestUnlock
        )

        await graph.runtime.setStatus(.unlocked)
        let terminated = await graph.host.handleLifecycleEvent(
            .willTerminate
        )
        await expectEqual(terminated.mode, .lockedPublic)
        await expectEqual(await graph.presentation.finishCount(), 1)
        await expectEqual(
            await graph.lifecycle.events(),
            [.protectedDataBecameUnavailable, .willTerminate]
        )
        await assertStartStopped(graph.host)
    }

    func testStopBeforeStartIsSafeAndTerminal() async throws {
        let graph = try makeGraph()
        let state = await graph.host.stop()
        await expectEqual(state.mode, .lockedPublic)
        await expectEqual(await graph.runtime.totalCalls(), 0)
        await expectEqual(await graph.presentation.totalCalls(), 0)
        await assertStartStopped(graph.host)
    }

    func testStopCoordinatesWithSuspendedPresentationStartHandshake() async throws {
        let hostSource = try source(named: "AtlasVaultProductionHost.swift")
        let startGate = HostSuspensionGate()
        let graph = try makeGraph(presentationStartGate: startGate)
        let start = Task { await captureStart(graph.host) }
        await startGate.waitUntilEntered()
        let stop = Task { await graph.host.stop() }

        guard hostSource.contains("awaitStartingOperationBeforeStop") else {
            XCTFail("Stop must coordinate with an in-flight start handshake")
            await startGate.release()
            _ = await stop.value
            _ = await start.value
            return
        }

        await startGate.release()
        let stopped = await stop.value
        await expectEqual(stopped.mode, .lockedPublic)
        await expectEqual(await graph.presentation.finishCount(), 1)
        await expectEqual(await start.value, .failure(.stopped))
        await assertStartStopped(graph.host)
    }

    func testConcurrentStopCoalescesLocksAndFinishesPrivateFree() async throws {
        let gate = HostSuspensionGate()
        let graph = try makeGraph(
            runtimeStatus: .unlocked,
            runtimeLockGate: gate
        )
        _ = try await graph.host.start()
        let first = Task { await graph.host.stop() }
        await gate.waitUntilEntered()
        let second = Task { await graph.host.stop() }
        await gate.release()

        await expectEqual((await first.value).mode, .lockedPublic)
        await expectEqual((await second.value).mode, .lockedPublic)
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await expectEqual(await graph.presentation.finishCount(), 1)
        await expectTrue(await graph.owner.allStatesArePrivateFree())
        _ = await graph.host.stop()
        await expectEqual(await graph.presentation.finishCount(), 1)
        await assertStartStopped(graph.host)
    }

    func testStopAfterFailedStartFinishesAlreadyStartedPipeline() async throws {
        let graph = try makeGraph()
        await graph.owner.setResults([false])
        await assertStartPresentationFailure(graph.host)

        let stopped = await graph.host.stop()

        await expectEqual(stopped.mode, .lockedPublic)
        await expectEqual(await graph.presentation.finishCount(), 1)
        await expectEqual(await graph.runtime.lockCalls(), 1)
        await assertStartStopped(graph.host)
    }

    func testTerminalStopCannotReopenAdmissionFromInFlightLockBarrier() async throws {
        let lockGate = HostSuspensionGate()
        let graph = try makeGraph(
            selection: .success(
                .selected(try selectedVaultID())
            ),
            runtimeStatus: .unlocked,
            runtimeLockGate: lockGate
        )
        _ = try await graph.host.start()
        _ = await graph.host.requestUnlockPanel()

        let lock = Task { await graph.host.lock() }
        await lockGate.waitUntilEntered()
        let stop = Task { await graph.host.stop() }
        await lockGate.release()

        _ = await lock.value
        let stopped = await stop.value
        await expectFalse(stopped.publicShell.canRequestUnlock)
        await expectEqual(await graph.selector.selectCount(), 1)
        await assertStartStopped(graph.host)
    }

    func testTerminalStopSupersedesSuspendedNonterminalBarrier() async throws {
        let hostSource = try source(named: "AtlasVaultProductionHost.swift")
        let ownerGate = HostSuspensionGate()
        let graph = try makeGraph(runtimeStatus: .unlocked)
        _ = try await graph.host.start()
        await graph.owner.setGate(ownerGate)

        let lock = Task { await graph.host.lock() }
        await ownerGate.waitUntilEntered()
        let stop = Task { await graph.host.stop() }

        guard hostSource.contains("invalidatePublicationPermit()"),
              hostSource.contains("barrierOperation.task.cancel()"),
              hostSource.contains("operationID: id"),
              hostSource.contains("supersedePresentationGeneration") else {
            XCTFail("Terminal stop must supersede stale nonterminal barriers")
            await ownerGate.release()
            _ = await lock.value
            _ = await stop.value
            return
        }

        let stopped = await stop.value
        await expectEqual(stopped.mode, .lockedPublic)
        await expectFalse(stopped.publicShell.canRequestUnlock)
        await assertStartStopped(graph.host)

        await ownerGate.release()
        _ = await lock.value
        await assertStartStopped(graph.host)
        await expectEqual(await graph.presentation.finishCount(), 1)
        await expectEqual(await graph.owner.staleResetCount(), 1)
        await expectEqual(await graph.owner.latestMode(), .lockedPublic)
    }

    func testStopAcknowledgementFailureRemainsTerminalReconciliation() async throws {
        let graph = try makeGraph()
        _ = try await graph.host.start()
        await graph.presentation.setFinishResult(false)
        let stopped = await graph.host.stop()

        await expectEqual(stopped.mode, .unlockPanel)
        await expectEqual(
            stopped.unlockPanelState?.status,
            .hostReconciliationRequired
        )
        await expectEqual(await graph.owner.latestMode(), .unlockPanel)
        await expectEqual(
            await graph.owner.latestUnlockStatus(),
            .hostReconciliationRequired
        )
        await expectFalse(stopped.publicShell.canRequestUnlock)
        await assertStartStopped(graph.host)
    }

    func testTerminalRuntimeFailureKeepsPresentationOwnerInReconciliation()
        async throws
    {
        let graph = try makeGraph(runtimeStatus: .unlocked)
        _ = try await graph.host.start()
        await graph.runtime.setLockResult(.unlocked)

        let stopped = await graph.host.stop()

        await expectEqual(
            stopped.unlockPanelState?.status,
            .hostReconciliationRequired
        )
        await expectEqual(await graph.owner.latestMode(), .unlockPanel)
        await expectEqual(
            await graph.owner.latestUnlockStatus(),
            .hostReconciliationRequired
        )
        await assertStartStopped(graph.host)
    }

    func testProductionSourceGuardsAndExactAllowlist() async throws {
        let host = try source(named: "AtlasVaultProductionHost.swift")
        await expectTrue(host.contains("beginTerminalStop()"))
        await expectTrue(host.contains("terminalBarrierRequested"))
        for forbidden in [
            "privateState(",
            "AtlasVaultPrivateStateSnapshot",
            "AtlasVaultHydratedState",
            "AtlasVaultPrivatePresentationState",
            "apply(",
            "AtlasVaultRuntimeMutationRequest",
            "AtlasVaultSaveOutcome",
            "savedSearch",
            "savedJob",
            "applicationNote",
            "profileSnippet",
            "draftMetadata",
            "generatedDocument",
            "SwiftUI",
            "@main",
            "AtlasIOSHostApp",
            "AtlasRootView",
            "SearchViewModel",
            "AtlasAPIClient",
            "AtlasLocalCache",
            "Keychain",
            "SecItem",
            "FileManager",
            "Data.write",
            "URLSession",
            "UserDefaults",
            "NavigationStack",
            "NavigationLink",
            "LocalAuthentication",
            "LAContext",
            "suppliedTestVaultKey",
            "Task.detached",
            "nonisolated(unsafe)",
            "@unchecked Sendable",
            "NSLock",
            "DispatchSemaphore",
            "sleep(",
        ] {
            await expectFalse(host.contains(forbidden), forbidden)
        }

        let pipeline = try source(
            named: "AtlasVaultProductionPresentationPipeline.swift"
        )
        for forbidden in [
            "SwiftUI",
            "@main",
            "AtlasRootView",
            "AtlasAPIClient",
            "Keychain",
            "SecItem",
            "FileManager",
            "Data.write",
            "URLSession",
            "UserDefaults",
            "LocalAuthentication",
            "LAContext",
            "Task.detached",
            "nonisolated(unsafe)",
            "@unchecked Sendable",
            "NSLock",
            "DispatchSemaphore",
            "sleep(",
        ] {
            await expectFalse(pipeline.contains(forbidden), forbidden)
        }
        for line in pipeline.split(separator: "\n")
            where line.contains("privateState:") {
            await expectTrue(line.contains("nil"), String(line))
        }

        let expected = Set([
            "phase2d56_runtime_neutral_production_host.md",
            "AtlasVaultProductionHostContracts.swift",
            "AtlasVaultProductionPresentationPipeline.swift",
            "AtlasVaultProductionHost.swift",
            "AtlasVaultProductionHostTests.swift",
            "AtlasVaultProductionHostFactoryTests.swift",
        ])
        let actual = Set([
            architectureURL().lastPathComponent,
            sourceURL(named: "AtlasVaultProductionHostContracts.swift")
                .lastPathComponent,
            sourceURL(
                named: "AtlasVaultProductionPresentationPipeline.swift"
            ).lastPathComponent,
            sourceURL(named: "AtlasVaultProductionHost.swift")
                .lastPathComponent,
            URL(fileURLWithPath: #filePath).lastPathComponent,
            testURL(named: "AtlasVaultProductionHostFactoryTests.swift")
                .lastPathComponent,
        ])
        await expectEqual(actual, expected)
    }

    func testNoAppEntryWiringOrReviewArtifactsExist() async throws {
        let appEntry = repositoryRootURL()
            .appendingPathComponent("apps/apple/AtlasIOSHost")
            .appendingPathComponent("AtlasIOSHostApp.swift")
        if FileManager.default.fileExists(atPath: appEntry.path) {
            let source = try String(contentsOf: appEntry, encoding: .utf8)
            await expectFalse(source.contains("AtlasVaultProductionHost("))
            await expectFalse(
                source.contains("AtlasVaultProductionHostBuilder(")
            )
            await expectFalse(
                source.contains("AtlasVaultProductionPresentationPipeline(")
            )
        }

        for url in try reviewArtifactURLs(at: repositoryRootURL()) {
            await expectNotEqual(url.pathExtension, "atlasvault")
            await expectNotEqual(url.lastPathComponent, ".venv-review")
        }
    }

    private func privateFreeSnapshot(
        _ status: AtlasVaultPresentationStatus
    ) throws -> AtlasVaultPrivateFreePresentationSnapshot {
        try AtlasVaultPrivateFreePresentationSnapshot(
            validating: AtlasVaultPresentationSnapshot(
                status: status,
                privateState: nil
            )
        )
    }

    private func assertSafeReopenAdmissionClosed(
        event: AtlasVaultLifecycleEvent,
        suspensionPoint: SafeReopenSuspensionPoint
    ) async throws {
        let gate = HostSuspensionGate()
        let selection = HostVaultSelectorFake.Outcome.success(
            .selected(try selectedVaultID())
        )
        let graph: HostGraph
        switch suspensionPoint {
        case .lifecycleHandle:
            graph = try makeGraph(
                selection: selection,
                lifecycleHandleGate: gate
            )
        case .lifecycleStatus:
            graph = try makeGraph(
                selection: selection,
                lifecycleStatusGate: gate
            )
        case .runtimeStatus:
            graph = try makeGraph(
                selection: selection,
                runtimeStatusGate: gate
            )
        }
        _ = try await graph.host.start()

        let reopening = Task {
            await graph.host.handleLifecycleEvent(event)
        }
        await gate.waitUntilEntered()

        let suspended = await graph.host.currentFlowState()
        await expectFalse(
            suspended.publicShell.canRequestUnlock,
            "\(event) at \(suspensionPoint)"
        )
        let panel = await graph.host.requestUnlockPanel()
        await expectFalse(
            panel.publicShell.canRequestUnlock,
            "\(event) at \(suspensionPoint)"
        )
        await expectEqual(await graph.selector.selectCount(), 0)
        await expectEqual(graph.controllerBuilder.callCount, 0)
        await expectEqual(await graph.runtime.activationCalls(), 0)

        await gate.release()
        _ = await reopening.value
    }

    private func makeGraph(
        snapshot: HostSnapshotRestorerFake.Outcome = .success(nil),
        snapshotGate: HostSuspensionGate? = nil,
        searchPlans: [HostPublicJobsFake.Plan] = [],
        selection: HostVaultSelectorFake.Outcome = .success(.none),
        selectionGate: HostSuspensionGate? = nil,
        lifecycleHandleGate: HostSuspensionGate? = nil,
        lifecycleStatusGate: HostSuspensionGate? = nil,
        lifecyclePendingGrace: Bool = false,
        runtimeStatus: AtlasVaultRuntimeStatus = .locked,
        runtimeStatusGate: HostSuspensionGate? = nil,
        runtimeLockGate: HostSuspensionGate? = nil,
        submitResult: AtlasVaultUnlockPresentationState =
            unlockState(.failed),
        submitGate: HostSuspensionGate? = nil,
        cancelGate: HostSuspensionGate? = nil,
        presentationStartGate: HostSuspensionGate? = nil,
        ownerGate: HostSuspensionGate? = nil,
        cancelResult: AtlasVaultUnlockPresentationState =
            unlockState(.cancelled),
        disappearanceResult: AtlasVaultUnlockPresentationState =
            unlockState(.locked)
    ) throws -> HostGraph {
        let publicJobs = HostPublicJobsFake(plans: searchPlans)
        let restorer = HostSnapshotRestorerFake(
            outcome: snapshot,
            gate: snapshotGate
        )
        let selector = HostVaultSelectorFake(
            outcome: selection,
            gate: selectionGate
        )
        let runtime = HostRuntimeFake(
            status: runtimeStatus,
            statusGate: runtimeStatusGate,
            lockGate: runtimeLockGate
        )
        let lifecycle = HostLifecycleFake(
            handleGate: lifecycleHandleGate,
            statusGate: lifecycleStatusGate,
            pendingGrace: lifecyclePendingGrace
        )
        let presentation = HostPresentationFake(
            startGate: presentationStartGate
        )
        let owner = HostPresentationOwnerFake(gate: ownerGate)
        let coordinator = HostUnlockCoordinatorFake()
        let controller = HostUnlockControllerFake(
            submitResult: submitResult,
            submitGate: submitGate,
            cancelGate: cancelGate,
            cancelResult: cancelResult,
            disappearanceResult: disappearanceResult
        )
        let controllerBuilder = HostUnlockControllerBuilderFake(
            controller: controller
        )
        let dependencies = AtlasVaultProductionHostDependencies(
            publicJobs: publicJobs,
            publicSnapshotRestorer: restorer,
            vaultIDSelector: selector,
            runtime: runtime,
            lifecycle: lifecycle,
            presentation: presentation,
            presentationOwner: owner,
            unlockCoordinator: coordinator,
            unlockControllerBuilder: controllerBuilder
        )
        let host = AtlasVaultProductionHost(dependencies: dependencies)
        return HostGraph(
            host: host,
            publicJobs: publicJobs,
            snapshot: restorer,
            selector: selector,
            runtime: runtime,
            lifecycle: lifecycle,
            presentation: presentation,
            owner: owner,
            coordinator: coordinator,
            controller: controller,
            controllerBuilder: controllerBuilder
        )
    }

    private func makeSnapshot(
        jobID: String
    ) throws -> AtlasProductionPublicSnapshot {
        AtlasProductionPublicSnapshot(
            savedAt: Date(timeIntervalSince1970: 1_800_000_000),
            health: try AtlasPublicServiceHealth(
                availability: .available,
                openJobCount: 1,
                enabledSourceCount: 1,
                lastSyncAt: Date(timeIntervalSince1970: 1_799_999_000)
            ),
            jobs: [
                AtlasLockedPublicJob(
                    id: jobID,
                    title: "Fake Public Role",
                    organization: "Fake Public Organization",
                    location: "Fake Public Location",
                    closingDateText: "2099-12-31"
                )
            ],
            sources: [],
            updates: []
        )
    }

    private func makeSearchResult(
        jobID: String,
        title: String
    ) throws -> AtlasPublicJobSearchResult {
        try AtlasPublicJobSearchResult(
            jobs: [
                AtlasLockedPublicJob(
                    id: jobID,
                    title: title,
                    organization: "Fake Search Organization",
                    location: "Fake Search Location",
                    closingDateText: "2099-12-31"
                )
            ],
            total: 1,
            limit: 25,
            offset: 0
        )
    }

    private func searchRequest(
        query: String
    ) throws -> AtlasPublicJobSearchRequest {
        try AtlasPublicJobSearchRequest(
            query: query,
            limit: 25,
            offset: 0
        )
    }

    private func selectedVaultID() throws -> AtlasSelectedVaultID {
        try AtlasSelectedVaultID(validating: Self.fakeVaultID)
    }

    private func prepareLocalKeyUnlock(
        _ graph: HostGraph
    ) async throws {
        _ = try await graph.host.start()
        await expectEqual(
            (await graph.host.requestUnlockPanel()).mode,
            .unlockPanel
        )
        let selected = await graph.host.selectUnlockMethod(.localKey)
        await expectEqual(selected.unlockPanelState?.selectedMethod, .localKey)
    }

    private func assertStartPresentationFailure(
        _ host: AtlasVaultProductionHost
    ) async {
        do {
            _ = try await host.start()
            XCTFail("Expected presentationUnavailable")
        } catch {
            await expectEqual(
                error as? AtlasVaultProductionHostError,
                .presentationUnavailable
            )
        }
    }

    private func assertStartStopped(
        _ host: AtlasVaultProductionHost
    ) async {
        do {
            _ = try await host.start()
            XCTFail("Expected stopped")
        } catch {
            await expectEqual(
                error as? AtlasVaultProductionHostError,
                .stopped
            )
        }
    }

    private func source(named filename: String) throws -> String {
        try String(
            contentsOf: sourceURL(named: filename),
            encoding: .utf8
        )
    }

    private func sourceURL(named filename: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(filename)
    }

    private func testURL(named filename: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent(filename)
    }

    private func architectureURL() -> URL {
        repositoryRootURL()
            .appendingPathComponent("docs/architecture")
            .appendingPathComponent(
                "phase2d56_runtime_neutral_production_host.md"
            )
    }

    private func repositoryRootURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}

private struct HostGraph {
    let host: AtlasVaultProductionHost
    let publicJobs: HostPublicJobsFake
    let snapshot: HostSnapshotRestorerFake
    let selector: HostVaultSelectorFake
    let runtime: HostRuntimeFake
    let lifecycle: HostLifecycleFake
    let presentation: HostPresentationFake
    let owner: HostPresentationOwnerFake
    let coordinator: HostUnlockCoordinatorFake
    let controller: HostUnlockControllerFake
    let controllerBuilder: HostUnlockControllerBuilderFake
}

private actor HostSuspensionGate {
    private var entered = false
    private var released = false
    private var entryWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseWaiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        entered = true
        let waiters = entryWaiters
        entryWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
        guard !released else {
            return
        }
        await withCheckedContinuation { continuation in
            releaseWaiters.append(continuation)
        }
    }

    func waitUntilEntered() async {
        guard !entered else {
            return
        }
        await withCheckedContinuation { continuation in
            entryWaiters.append(continuation)
        }
    }

    func release() {
        guard !released else {
            return
        }
        released = true
        let waiters = releaseWaiters
        releaseWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
    }
}

private actor HostBoolRecorder {
    private var recordedValue: Bool?

    func record(_ value: Bool) {
        recordedValue = value
    }

    func value() -> Bool? {
        recordedValue
    }
}

private actor HostPublicJobsFake: AtlasPublicJobSearching {
    struct Plan: Sendable {
        let result: Result<
            AtlasPublicJobSearchResult,
            AtlasPublicJobServiceError
        >
        let gate: HostSuspensionGate?
        let releasesGateOnCancellation: Bool

        init(
            result: Result<
                AtlasPublicJobSearchResult,
                AtlasPublicJobServiceError
            >,
            gate: HostSuspensionGate? = nil,
            releasesGateOnCancellation: Bool = true
        ) {
            self.result = result
            self.gate = gate
            self.releasesGateOnCancellation = releasesGateOnCancellation
        }
    }

    private var plans: [Plan]
    private var searchCalls = 0
    private var cancellationCalls = 0
    private var cancellationWaiters:
        [CheckedContinuation<Void, Never>] = []

    init(plans: [Plan]) {
        self.plans = plans
    }

    func health() async throws(AtlasPublicJobServiceError)
        -> AtlasPublicServiceHealth
    {
        do {
            return try AtlasPublicServiceHealth(
                availability: .available,
                openJobCount: 0,
                enabledSourceCount: 0,
                lastSyncAt: nil
            )
        } catch {
            throw .invalidResponse
        }
    }

    func search(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError)
        -> AtlasPublicJobSearchResult
    {
        searchCalls += 1
        guard !plans.isEmpty else {
            throw .unavailable
        }
        let plan = plans.removeFirst()
        if let gate = plan.gate {
            await withTaskCancellationHandler {
                await gate.wait()
            } onCancel: {
                Task {
                    await self.recordCancellation(
                        gate,
                        releasesGate: plan.releasesGateOnCancellation
                    )
                }
            }
        }
        if Task.isCancelled {
            throw .unavailable
        }
        return try plan.result.get()
    }

    func sources() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicSourceStatus]
    {
        []
    }

    func updates() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicUpdateStatus]
    {
        []
    }

    func detail(
        for reference: AtlasPublicJobReference
    ) async throws(AtlasPublicJobServiceError)
        -> AtlasPublicJobDetailResult
    {
        throw .invalidRequest
    }

    func totalCalls() -> Int {
        searchCalls
    }

    func cancelledCalls() -> Int {
        cancellationCalls
    }

    func waitUntilCancellation() async {
        guard cancellationCalls == 0 else {
            return
        }
        await withCheckedContinuation { continuation in
            cancellationWaiters.append(continuation)
        }
    }

    private func recordCancellation(
        _ gate: HostSuspensionGate,
        releasesGate: Bool
    ) async {
        cancellationCalls += 1
        let waiters = cancellationWaiters
        cancellationWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
        if releasesGate {
            await gate.release()
        }
    }
}

private actor HostSnapshotRestorerFake: AtlasPublicSnapshotRestoring {
    enum Outcome: Sendable {
        case success(AtlasProductionPublicSnapshot?)
        case failure(AtlasPublicSnapshotRestoreError)
    }

    private let outcome: Outcome
    private let gate: HostSuspensionGate?
    private var calls = 0

    init(outcome: Outcome, gate: HostSuspensionGate?) {
        self.outcome = outcome
        self.gate = gate
    }

    func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
    {
        calls += 1
        if let gate {
            await gate.wait()
        }
        switch outcome {
        case let .success(snapshot):
            return snapshot
        case let .failure(error):
            throw error
        }
    }

    func restoreCount() -> Int {
        calls
    }
}

private actor HostVaultSelectorFake: AtlasVaultIDSelecting {
    enum Outcome: Sendable {
        case success(AtlasVaultIDSelection)
        case failure(AtlasVaultIDSelectionError)
    }

    private let outcome: Outcome
    private let gate: HostSuspensionGate?
    private var calls = 0

    init(outcome: Outcome, gate: HostSuspensionGate?) {
        self.outcome = outcome
        self.gate = gate
    }

    func selectVaultID() async throws(AtlasVaultIDSelectionError)
        -> AtlasVaultIDSelection
    {
        calls += 1
        if let gate {
            await gate.wait()
        }
        switch outcome {
        case let .success(selection):
            return selection
        case let .failure(error):
            throw error
        }
    }

    func selectCount() -> Int {
        calls
    }
}

private actor HostRuntimeFake: AtlasVaultRuntimeFacading {
    private var runtimeStatus: AtlasVaultRuntimeStatus
    private var lockResult: AtlasVaultRuntimeStatus = .locked
    private var nextStatusGate: HostSuspensionGate?
    private var nextLockGate: HostSuspensionGate?
    private var statusCallCount = 0
    private var activationCallCount = 0
    private var lockCallCount = 0
    private var mutationCallCount = 0

    init(
        status: AtlasVaultRuntimeStatus,
        statusGate: HostSuspensionGate?,
        lockGate: HostSuspensionGate?
    ) {
        runtimeStatus = status
        nextStatusGate = statusGate
        nextLockGate = lockGate
    }

    func status() async -> AtlasVaultRuntimeStatus {
        statusCallCount += 1
        let gate = nextStatusGate
        nextStatusGate = nil
        if let gate {
            await gate.wait()
        }
        return runtimeStatus
    }

    func activate(
        _ request: AtlasVaultRuntimeActivationRequest
    ) async throws {
        activationCallCount += 1
    }

    func lock() async {
        lockCallCount += 1
        let gate = nextLockGate
        nextLockGate = nil
        if let gate {
            await gate.wait()
        }
        runtimeStatus = lockResult
    }

    func apply(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async throws -> AtlasVaultSaveOutcome {
        mutationCallCount += 1
        return .committed
    }

    func setStatus(_ status: AtlasVaultRuntimeStatus) {
        runtimeStatus = status
    }

    func setLockResult(_ status: AtlasVaultRuntimeStatus) {
        lockResult = status
    }

    func totalCalls() -> Int {
        statusCallCount
            + activationCallCount
            + lockCallCount
            + mutationCallCount
    }

    func activationCalls() -> Int {
        activationCallCount
    }

    func lockCalls() -> Int {
        lockCallCount
    }
}

private actor HostLifecycleFake: AtlasVaultLifecycleCoordinating {
    private var handledEvents: [AtlasVaultLifecycleEvent] = []
    private var statusCallCount = 0
    private var nextHandleGate: HostSuspensionGate?
    private var nextStatusGate: HostSuspensionGate?
    private var pendingGrace: Bool

    init(
        handleGate: HostSuspensionGate?,
        statusGate: HostSuspensionGate?,
        pendingGrace: Bool
    ) {
        nextHandleGate = handleGate
        nextStatusGate = statusGate
        self.pendingGrace = pendingGrace
    }

    func handle(_ event: AtlasVaultLifecycleEvent) async {
        handledEvents.append(event)
        let gate = nextHandleGate
        nextHandleGate = nil
        if let gate {
            await gate.wait()
        }
    }

    func status() async -> AtlasVaultLifecycleStatus {
        statusCallCount += 1
        let gate = nextStatusGate
        nextStatusGate = nil
        if let gate {
            await gate.wait()
        }
        return AtlasVaultLifecycleStatus(
            lastEvent: handledEvents.last,
            hasPendingGraceLock: pendingGrace,
            failure: nil
        )
    }

    func totalCalls() -> Int {
        handledEvents.count + statusCallCount
    }

    func events() -> [AtlasVaultLifecycleEvent] {
        handledEvents
    }
}

private struct HostNeverPresentationSource:
    AtlasVaultPresentationUpdateSourcing
{
    func updates() async -> AsyncStream<AtlasVaultPresentationUpdate> {
        AsyncStream { continuation in
            continuation.finish()
        }
    }
}

private actor HostPresentationFake:
    AtlasVaultProductionPresentationCoordinating
{
    private let backing = AtlasVaultObservablePresentationAdapter(
        source: HostNeverPresentationSource()
    )
    private var startCalls = 0
    private var publishCalls = 0
    private var finishCalls = 0
    private var subscribeCalls = 0
    private var snapshotCalls = 0
    private var publishResults: [Bool] = []
    private var finishResult = true
    private var current = AtlasVaultPresentationSnapshot(
        status: .locked,
        privateState: nil
    )
    private var statuses: [AtlasVaultPresentationStatus] = []
    private let startGate: HostSuspensionGate?

    init(startGate: HostSuspensionGate?) {
        self.startGate = startGate
    }

    func start() async -> Bool {
        startCalls += 1
        if let startGate {
            await startGate.wait()
        }
        return true
    }

    func publish(
        _ value: AtlasVaultPrivateFreePresentationSnapshot
    ) async -> Bool {
        publishCalls += 1
        statuses.append(value.snapshot.status)
        let result = publishResults.isEmpty
            ? true
            : publishResults.removeFirst()
        if result {
            current = value.snapshot
        }
        return result
    }

    func finish() async -> Bool {
        finishCalls += 1
        if finishResult {
            current = AtlasVaultPresentationSnapshot(
                status: .locked,
                privateState: nil
            )
        }
        return finishResult
    }

    func subscribe() async -> AtlasVaultPresentationSubscription {
        subscribeCalls += 1
        return await backing.subscribe()
    }

    func currentSnapshot() async -> AtlasVaultPresentationSnapshot {
        snapshotCalls += 1
        return current
    }

    func setPublishResults(_ results: [Bool]) {
        publishResults = results
    }

    func setFinishResult(_ result: Bool) {
        finishResult = result
    }

    func startCount() -> Int {
        startCalls
    }

    func finishCount() -> Int {
        finishCalls
    }

    func publishedStatuses() -> [AtlasVaultPresentationStatus] {
        statuses
    }

    func totalCalls() -> Int {
        startCalls
            + publishCalls
            + finishCalls
            + subscribeCalls
            + snapshotCalls
    }
}

private actor HostPresentationOwnerRecorder {
    private var states: [AtlasLockedShellUnlockFlowState] = []
    private var generations: [AtlasVaultProductionHostGeneration] = []
    private var results: [Bool] = []
    private var nextGate: HostSuspensionGate?
    private var currentGeneration: AtlasVaultProductionHostGeneration?
    private var staleResets = 0

    init(gate: HostSuspensionGate?) {
        nextGate = gate
    }

    func beginReset(
        generation: AtlasVaultProductionHostGeneration
    ) -> (result: Bool, gate: HostSuspensionGate?) {
        currentGeneration = generation
        let gate = nextGate
        nextGate = nil
        return (
            results.isEmpty ? true : results.removeFirst(),
            gate
        )
    }

    func finishReset(
        state: AtlasLockedShellUnlockFlowState,
        generation: AtlasVaultProductionHostGeneration,
        result: Bool
    ) -> Bool {
        guard currentGeneration == generation else {
            staleResets += 1
            return false
        }
        states.append(state)
        generations.append(generation)
        return result
    }

    func supersede(
        generation: AtlasVaultProductionHostGeneration
    ) {
        currentGeneration = generation
    }

    func setResults(_ values: [Bool]) {
        results = values
    }

    func setGate(_ gate: HostSuspensionGate?) {
        nextGate = gate
    }

    func totalCalls() -> Int {
        states.count
    }

    func allStatesArePrivateFree() -> Bool {
        states.allSatisfy { state in
            state.unlockPanelState?.description
                .contains(Self.privateMarker) != true
        }
    }

    func staleResetCount() -> Int {
        staleResets
    }

    func latestMode() -> AtlasLockedShellUnlockFlowMode? {
        states.last?.mode
    }

    func latestUnlockStatus() -> AtlasVaultUnlockPresentationStatus? {
        states.last?.unlockPanelState?.status
    }

    func latestCanRequestUnlock() -> Bool? {
        states.last?.publicShell.canRequestUnlock
    }

    private static let privateMarker =
        "FAKE_PRIVATE_STATE_MUST_NOT_APPEAR"
}

private final class HostPresentationOwnerFake:
    AtlasVaultProductionPresentationOwnerResetting,
    @unchecked Sendable
{
    private let recorder: HostPresentationOwnerRecorder

    init(gate: HostSuspensionGate? = nil) {
        recorder = HostPresentationOwnerRecorder(gate: gate)
    }

    @MainActor
    func resetPresentation(
        to state: AtlasLockedShellUnlockFlowState,
        generation: AtlasVaultProductionHostGeneration
    ) async -> Bool {
        let plan = await recorder.beginReset(
            generation: generation
        )
        if let gate = plan.gate {
            await gate.wait()
        }
        return await recorder.finishReset(
            state: state,
            generation: generation,
            result: plan.result
        )
    }

    @MainActor
    func supersedePresentationGeneration(
        _ generation: AtlasVaultProductionHostGeneration
    ) async {
        await recorder.supersede(generation: generation)
    }

    func setResults(_ results: [Bool]) async {
        await recorder.setResults(results)
    }

    func setGate(_ gate: HostSuspensionGate?) async {
        await recorder.setGate(gate)
    }

    func totalCalls() async -> Int {
        await recorder.totalCalls()
    }

    func allStatesArePrivateFree() async -> Bool {
        await recorder.allStatesArePrivateFree()
    }

    func staleResetCount() async -> Int {
        await recorder.staleResetCount()
    }

    func latestMode() async -> AtlasLockedShellUnlockFlowMode? {
        await recorder.latestMode()
    }

    func latestUnlockStatus() async -> AtlasVaultUnlockPresentationStatus? {
        await recorder.latestUnlockStatus()
    }

    func latestCanRequestUnlock() async -> Bool? {
        await recorder.latestCanRequestUnlock()
    }
}

private actor HostUnlockCoordinatorFake:
    AtlasVaultUnlockRequestCoordinating
{
    private var dispatchCalls = 0
    private var cancelCalls = 0

    func dispatch(_ request: AtlasVaultUnlockRequest) async throws {
        dispatchCalls += 1
    }

    func cancel(_ request: AtlasVaultUnlockRequest) async -> Bool {
        cancelCalls += 1
        return true
    }
}

private actor HostUnlockControllerFake:
    AtlasVaultUnlockPresentationControlling
{
    private var state = unlockState(.locked)
    private let submitResult: AtlasVaultUnlockPresentationState
    private let submitGate: HostSuspensionGate?
    private let cancelGate: HostSuspensionGate?
    private let cancelResult: AtlasVaultUnlockPresentationState
    private let disappearanceResult: AtlasVaultUnlockPresentationState
    private var submitCalls = 0
    private var cancelCalls = 0
    private var disappearanceCalls = 0
    private var hostLockCalls = 0
    private var cancelCountWaiters: [(
        target: Int,
        continuation: CheckedContinuation<Void, Never>
    )] = []

    init(
        submitResult: AtlasVaultUnlockPresentationState,
        submitGate: HostSuspensionGate?,
        cancelGate: HostSuspensionGate?,
        cancelResult: AtlasVaultUnlockPresentationState,
        disappearanceResult: AtlasVaultUnlockPresentationState
    ) {
        self.submitResult = submitResult
        self.submitGate = submitGate
        self.cancelGate = cancelGate
        self.cancelResult = cancelResult
        self.disappearanceResult = disappearanceResult
    }

    func currentState() async -> AtlasVaultUnlockPresentationState {
        state
    }

    func select(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasVaultUnlockPresentationState {
        state = AtlasVaultUnlockPresentationState(
            capabilities: .currentProduction,
            selectedMethod: method,
            status: method == nil ? .locked : .ready
        )
        return state
    }

    func submit(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration?
    ) async -> AtlasVaultUnlockPresentationState {
        submitCalls += 1
        state = AtlasVaultUnlockPresentationState(
            capabilities: .currentProduction,
            selectedMethod: .localKey,
            status: .activating
        )
        if let submitGate {
            await submitGate.wait()
        }
        state = submitResult
        return state
    }

    func cancel() async -> AtlasVaultUnlockPresentationState {
        cancelCalls += 1
        resumeCancelCountWaiters()
        if cancelCalls == 1, let cancelGate {
            await cancelGate.wait()
        }
        state = cancelResult
        return state
    }

    func didDisappear() async -> AtlasVaultUnlockPresentationState {
        disappearanceCalls += 1
        state = disappearanceResult
        return state
    }

    func hostDidLock() async -> AtlasVaultUnlockPresentationState {
        hostLockCalls += 1
        state = unlockState(.locked)
        return state
    }

    func submitCount() -> Int {
        submitCalls
    }

    func cancelCount() -> Int {
        cancelCalls
    }

    func waitUntilCancelCount(_ target: Int) async {
        guard cancelCalls < target else {
            return
        }
        await withCheckedContinuation { continuation in
            cancelCountWaiters.append((target, continuation))
        }
    }

    func disappearanceCount() -> Int {
        disappearanceCalls
    }

    func hostLockCount() -> Int {
        hostLockCalls
    }

    private func resumeCancelCountWaiters() {
        var remaining: [(
            target: Int,
            continuation: CheckedContinuation<Void, Never>
        )] = []
        for waiter in cancelCountWaiters {
            if waiter.target <= cancelCalls {
                waiter.continuation.resume()
            } else {
                remaining.append(waiter)
            }
        }
        cancelCountWaiters = remaining
    }
}

private final class HostUnlockControllerBuilderFake:
    AtlasVaultUnlockPresentationControllerBuilding,
    @unchecked Sendable
{
    private(set) var callCount = 0
    private(set) var capturedVaultID: AtlasSelectedVaultID?
    private(set) var capturedCapabilities: AtlasVaultUnlockCapabilities?
    private let controller: HostUnlockControllerFake

    init(controller: HostUnlockControllerFake) {
        self.controller = controller
    }

    func makeController(
        selectedVaultID: AtlasSelectedVaultID,
        capabilities: AtlasVaultUnlockCapabilities,
        coordinator: any AtlasVaultUnlockRequestCoordinating
    ) -> any AtlasVaultUnlockPresentationControlling {
        callCount += 1
        capturedVaultID = selectedVaultID
        capturedCapabilities = capabilities
        return controller
    }
}

private actor HostSecretBuffer: AtlasVaultSecretBuffer {
    private var clears = 0

    func takeSecretBytes() async throws -> Data {
        Data()
    }

    func clear() async {
        clears += 1
    }

    func clearCount() -> Int {
        clears
    }
}

private func unlockState(
    _ status: AtlasVaultUnlockPresentationStatus
) -> AtlasVaultUnlockPresentationState {
    AtlasVaultUnlockPresentationState(
        capabilities: .currentProduction,
        selectedMethod: status == .locked ? nil : .localKey,
        status: status
    )
}

private func captureStart(
    _ host: AtlasVaultProductionHost
) async -> Result<
    AtlasLockedShellUnlockFlowState,
    AtlasVaultProductionHostError
> {
    do {
        return .success(try await host.start())
    } catch let error as AtlasVaultProductionHostError {
        return .failure(error)
    } catch {
        return .failure(.presentationUnavailable)
    }
}

private func captureSearch(
    _ host: AtlasVaultProductionHost,
    _ request: AtlasPublicJobSearchRequest
) async -> Result<
    AtlasPublicJobSearchResult,
    AtlasPublicJobServiceError
> {
    do {
        return .success(try await host.searchPublicJobs(request))
    } catch {
        return .failure(error)
    }
}

private func reviewArtifactURLs(at root: URL) throws -> [URL] {
    let ignored = Set([
        ".git",
        "private",
        "node_modules",
        ".build",
        "DerivedData",
        ".codex",
    ])
    guard let enumerator = FileManager.default.enumerator(
        at: root,
        includingPropertiesForKeys: [.isDirectoryKey],
        options: []
    ) else {
        throw CocoaError(.fileReadUnknown)
    }

    var urls: [URL] = []
    for case let url as URL in enumerator {
        let values = try url.resourceValues(forKeys: [.isDirectoryKey])
        if values.isDirectory == true,
           ignored.contains(url.lastPathComponent) {
            enumerator.skipDescendants()
            continue
        }
        urls.append(url)
    }
    return urls
}

private func expectEqual<Value: Equatable>(
    _ actual: @autoclosure () async throws -> Value,
    _ expected: @autoclosure () async throws -> Value,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        let actualValue = try await actual()
        let expectedValue = try await expected()
        XCTAssertEqual(actualValue, expectedValue, message(), file: file, line: line)
    } catch {
        XCTFail("Unexpected error: \(error)", file: file, line: line)
    }
}

private func expectNotEqual<Value: Equatable>(
    _ actual: @autoclosure () async throws -> Value,
    _ expected: @autoclosure () async throws -> Value,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        let actualValue = try await actual()
        let expectedValue = try await expected()
        XCTAssertNotEqual(actualValue, expectedValue, message(), file: file, line: line)
    } catch {
        XCTFail("Unexpected error: \(error)", file: file, line: line)
    }
}

private func expectTrue(
    _ expression: @autoclosure () async throws -> Bool,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        let value = try await expression()
        XCTAssertTrue(value, message(), file: file, line: line)
    } catch {
        XCTFail("Unexpected error: \(error)", file: file, line: line)
    }
}

private func expectFalse(
    _ expression: @autoclosure () async throws -> Bool,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        let value = try await expression()
        XCTAssertFalse(value, message(), file: file, line: line)
    } catch {
        XCTFail("Unexpected error: \(error)", file: file, line: line)
    }
}

private func expectNil<Value>(
    _ expression: @autoclosure () async throws -> Value?,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        let value = try await expression()
        XCTAssertNil(value, message(), file: file, line: line)
    } catch {
        XCTFail("Unexpected error: \(error)", file: file, line: line)
    }
}

private func expectNotNil<Value>(
    _ expression: @autoclosure () async throws -> Value?,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        let value = try await expression()
        XCTAssertNotNil(value, message(), file: file, line: line)
    } catch {
        XCTFail("Unexpected error: \(error)", file: file, line: line)
    }
}

private func expectNoThrow<Value>(
    _ expression: @autoclosure () async throws -> Value,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        _ = try await expression()
    } catch {
        XCTFail("Unexpected error: \(error)", file: file, line: line)
    }
}

private func expectThrowsError<Value>(
    _ expression: @autoclosure () async throws -> Value,
    file: StaticString = #filePath,
    line: UInt = #line,
    _ handler: ((any Error) async -> Void)? = nil
) async {
    do {
        _ = try await expression()
        XCTFail("Expected an error", file: file, line: line)
    } catch {
        await handler?(error)
    }
}

private func requireSendable<Value: Sendable>(_ value: Value) {}
