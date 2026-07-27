// Phase 2D-56 repository boundary.
import Foundation

public struct AtlasVaultProductionHostBuilder:
    AtlasVaultProductionHostBuilding,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public init() {}

    public func makeHost(
        dependencies: AtlasVaultProductionHostDependencies
    ) -> any AtlasVaultProductionHosting {
        AtlasVaultProductionHost(dependencies: dependencies)
    }

    public var description: String {
        "AtlasVaultProductionHostBuilder(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultProductionUnlockPresentationControllerBuilder:
    AtlasVaultUnlockPresentationControllerBuilding,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public init() {}

    public func makeController(
        selectedVaultID: AtlasSelectedVaultID,
        capabilities: AtlasVaultUnlockCapabilities,
        coordinator: any AtlasVaultUnlockRequestCoordinating
    ) -> any AtlasVaultUnlockPresentationControlling {
        AtlasVaultUnlockPresentationController(
            vaultID: selectedVaultID.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
    }

    public var description: String {
        "AtlasVaultProductionUnlockPresentationControllerBuilder(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public actor AtlasVaultProductionHost:
    AtlasVaultProductionHosting,
    AtlasVaultPrivateMutationHosting,
    AtlasVaultPrivateMutationContainmentHosting,
    AtlasSavedSearchPublicHandoffHosting,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum Lifetime: Equatable {
        case inactive
        case starting
        case started
        case reconciling
        case stopping
        case stopped
    }

    private enum PublicationResult {
        case acknowledged
        case stale
        case failed
    }

    private typealias PublicationPermitContinuation = CheckedContinuation<
        UUID?,
        Never
    >

    private struct StartOperation {
        let id: UUID
        let task: Task<
            Result<
                AtlasLockedShellUnlockFlowState,
                AtlasVaultProductionHostError
            >,
            Never
        >
    }

    private struct SearchOperation {
        let id: UUID
        let generation: UInt64
        let task: Task<
            Result<
                AtlasPublicJobSearchResult,
                AtlasPublicJobServiceError
            >,
            Never
        >
    }

    private struct SelectionOperation {
        let id: UUID
        let generation: AtlasVaultProductionHostGeneration
        let task: Task<
            Result<AtlasVaultIDSelection, AtlasVaultIDSelectionError>,
            Never
        >
    }

    private struct SubmitOperation {
        let id: UUID
        let generation: AtlasVaultProductionHostGeneration
        let task: Task<AtlasVaultUnlockPresentationState, Never>
    }

    private enum PrivateMutationExecutionResult {
        case saved(AtlasVaultSaveOutcome)
        case rejected(AtlasVaultRuntimeFacadeError)
        case failed
    }

    private struct PrivateMutationOperation {
        let id: UUID
        let generation: AtlasVaultProductionHostGeneration
        let task: Task<PrivateMutationExecutionResult, Never>
    }

    private struct SavedSearchHandoffOperation {
        let id: UUID
        let task: Task<
            AtlasSavedSearchPublicHandoffResult,
            Never
        >
    }

    private struct BarrierOperation {
        let id: UUID
        let terminal: Bool
        let skipsPrivateSessionDrain: Bool
        let task: Task<AtlasLockedShellUnlockFlowState, Never>
    }

    private struct StopOperation {
        let id: UUID
        let task: Task<AtlasLockedShellUnlockFlowState, Never>
    }

    private let dependencies: AtlasVaultProductionHostDependencies
    private var lifetime: Lifetime = .inactive
    private var shell = AtlasLockedPublicShellModel(
        vaultStatus: .locked,
        serviceStatus: .checking,
        cacheFreshness: .unavailable,
        searchQuery: "",
        publicJobs: [],
        isSearching: false,
        canRequestUnlock: false
    )
    private var unlockState = AtlasVaultUnlockPresentationState(
        capabilities: .currentProduction,
        selectedMethod: nil,
        status: .locked
    )
    private var isUnlockPanelPresented = false
    private var generation = AtlasVaultProductionHostGeneration()
    private var searchGeneration: UInt64 = 0
    private var unlockAdmissionOpen = false
    private var lifecycleIsActive = true
    private var protectedDataIsAvailable = true
    private var lifecycleAdmissionPermitted = true
    private var lifecycleEventRevision: UInt64 = 0
    private var safeLifecycleCheckRevision: UInt64?
    private var safeLifecycleCheckWaiters: [CheckedContinuation<Void, Never>] = []
    private var isTerminated = false
    private var explicitLockRequestedDuringStart = false
    private var restoreAttempted = false
    private var presentationWasStarted = false
    private var selectedVaultID: AtlasSelectedVaultID?
    private var unlockController:
        (any AtlasVaultUnlockPresentationControlling)?
    private var startOperation: StartOperation?
    private var searchOperation: SearchOperation?
    private var retainedSearchOperations: [UUID: SearchOperation] = [:]
    private var selectionOperation: SelectionOperation?
    private var selectionWaiters: [CheckedContinuation<
        AtlasLockedShellUnlockFlowState,
        Never
    >] = []
    private var publicationPermitID: UUID?
    private var publicationWaiters: [PublicationPermitContinuation] = []
    private var submitOperation: SubmitOperation?
    private var privateMutationOperation: PrivateMutationOperation?
    private var privateSessionIsActive = false
    private var savedSearchHandoffOperation:
        SavedSearchHandoffOperation?
    private var barrierOperation: BarrierOperation?
    private var stopOperation: StopOperation?

    public init(dependencies: AtlasVaultProductionHostDependencies) {
        self.dependencies = dependencies
    }

    public func start() async throws -> AtlasLockedShellUnlockFlowState {
        if let startOperation {
            return try await startOperation.task.value.get()
        }
        switch lifetime {
        case .started:
            return flowState()
        case .stopping, .stopped:
            throw AtlasVaultProductionHostError.stopped
        case .reconciling:
            throw AtlasVaultProductionHostError.presentationUnavailable
        case .inactive, .starting:
            break
        }

        lifetime = .starting
        let id = UUID()
        let task = Task { [self] in
            await performStart()
        }
        startOperation = StartOperation(id: id, task: task)
        let result = await task.value
        if startOperation?.id == id {
            startOperation = nil
        }
        return try result.get()
    }

    public func stop() async -> AtlasLockedShellUnlockFlowState {
        if let stopOperation {
            return await stopOperation.task.value
        }
        if lifetime == .stopped {
            return flowState()
        }
        if lifetime == .inactive, !presentationWasStarted {
            explicitLockRequestedDuringStart = false
            closeUnlockAdmission()
            lifetime = .stopped
            return flowState()
        }

        beginTerminalStop()
        abandonSelectionAndResumeCallers()
        let id = UUID()
        let task = Task { [self] in
            await performStop()
        }
        stopOperation = StopOperation(id: id, task: task)
        let state = await task.value
        if stopOperation?.id == id {
            stopOperation = nil
        }
        return state
    }

    public func currentFlowState() async
        -> AtlasLockedShellUnlockFlowState
    {
        flowState()
    }

    public func applyPrivateMutation(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async -> AtlasVaultPrivateMutationResult {
        guard privateMutationAdmissionPermitted(for: request) else {
            return privateSessionIsActive ? .failed : .locked
        }
        let operationGeneration = generation
        guard await dependencies.runtime.status() == .unlocked,
              generation == operationGeneration,
              privateMutationAdmissionPermitted(for: request) else {
            return privateSessionIsActive ? .failed : .locked
        }

        let id = UUID()
        let runtime = dependencies.runtime
        let task = Task<PrivateMutationExecutionResult, Never> {
            do {
                return .saved(try await runtime.apply(request))
            } catch let error as AtlasVaultRuntimeFacadeError {
                return .rejected(error)
            } catch {
                return .failed
            }
        }
        privateMutationOperation = PrivateMutationOperation(
            id: id,
            generation: operationGeneration,
            task: task
        )
        let result = await task.value
        guard privateMutationOperation?.id == id else {
            return privateSessionIsActive ? .cancelled : .locked
        }
        privateMutationOperation = nil
        guard generation == operationGeneration,
              privateMutationAdmissionPermitted(for: request) else {
            return privateSessionIsActive ? .cancelled : .locked
        }

        switch result {
        case let .saved(outcome):
            guard await dependencies.runtime.status() == .unlocked,
                  generation == operationGeneration,
                  privateMutationAdmissionPermitted(for: request) else {
                return await containFatalPrivateMutation()
            }
            switch outcome {
            case .committed:
                return .committed
            case .committedDurabilityUnconfirmed:
                return .committedDurabilityUnconfirmed
            }
        case let .rejected(error):
            switch error {
            case .cancelled:
                return privateSessionIsActive ? .cancelled : .locked
            case .saveFailed, .operationInProgress:
                return .failed
            case .locked,
                 .sessionMismatch,
                 .saveIntegrityUnknown,
                 .privateStateUnavailable,
                 .committedStateUnavailable,
                 .alreadyUnlocked,
                 .activationFailed:
                return await containFatalPrivateMutation()
            }
        case .failed:
            return await containFatalPrivateMutation()
        }
    }

    public func containCommittedPrivateMutationFailure() async {
        _ = await runPrivateFreeBarrier(
            terminal: false,
            skipsPrivateSessionDrain: true
        )
    }

    public func searchPublicJobs(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError)
        -> AtlasPublicJobSearchResult
    {
        guard request.origin == .manual else {
            throw .invalidRequest
        }
        return try await performPublicSearch(request)
    }

    public func performSavedSearchPublicHandoff(
        _ request: AtlasPublicJobSearchRequest
    ) async -> AtlasSavedSearchPublicHandoffResult {
        guard request.origin == .savedSearchHandoff,
              savedSearchHandoffOperation == nil,
              savedSearchHandoffAdmissionPermitted else {
            return unavailableSavedSearchHandoffResult
        }
        let admissionGeneration = generation
        guard await dependencies.runtime.status() == .unlocked,
              generation == admissionGeneration,
              savedSearchHandoffAdmissionPermitted else {
            return unavailableSavedSearchHandoffResult
        }

        let id = UUID()
        let task = Task { [self] in
            await executeSavedSearchPublicHandoff(
                request,
                operationID: id,
                admissionGeneration: admissionGeneration
            )
        }
        savedSearchHandoffOperation = SavedSearchHandoffOperation(
            id: id,
            task: task
        )
        let result = await task.value
        if savedSearchHandoffOperation?.id == id {
            savedSearchHandoffOperation = nil
        }
        return result
    }

    private func performPublicSearch(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError)
        -> AtlasPublicJobSearchResult
    {
        guard isPublicOperationAvailable else {
            throw .unavailable
        }
        searchGeneration &+= 1
        let operationGeneration = searchGeneration
        searchOperation?.task.cancel()

        replaceShell(
            serviceStatus: .checking,
            cacheFreshness: .unavailable,
            searchQuery: request.query,
            publicJobs: [],
            isSearching: true,
            searchOrigin: request.origin,
            hasAdditionalCriteria: request.hasAdditionalCriteria
        )

        let id = UUID()
        let service = dependencies.publicJobs
        let task = Task<
            Result<
                AtlasPublicJobSearchResult,
                AtlasPublicJobServiceError
            >,
            Never
        > {
            do {
                return .success(try await service.search(request))
            } catch let error as AtlasPublicJobServiceError {
                return .failure(error)
            } catch {
                return .failure(.unavailable)
            }
        }
        let operation = SearchOperation(
            id: id,
            generation: operationGeneration,
            task: task
        )
        retainedSearchOperations[id] = operation
        searchOperation = operation
        _ = await publishCurrentFlow(status: presentationStatus)

        let result = await task.value
        retainedSearchOperations[id] = nil
        guard searchOperation?.id == id,
              searchOperation?.generation == operationGeneration,
              searchGeneration == operationGeneration,
              isPublicOperationAvailable else {
            throw .unavailable
        }
        searchOperation = nil

        switch result {
        case let .success(value):
            replaceShell(
                serviceStatus: .available,
                cacheFreshness: .current,
                searchQuery: request.query,
                publicJobs: value.jobs,
                isSearching: false,
                searchOrigin: request.origin,
                hasAdditionalCriteria: request.hasAdditionalCriteria
            )
            let publication = await publishCurrentFlow(
                status: presentationStatus
            )
            if case .failed = publication {
                throw .unavailable
            }
            return value
        case let .failure(error):
            replaceShell(
                serviceStatus: .unavailable,
                cacheFreshness: .unavailable,
                searchQuery: request.query,
                publicJobs: [],
                isSearching: false,
                searchOrigin: request.origin,
                hasAdditionalCriteria: request.hasAdditionalCriteria
            )
            _ = await publishCurrentFlow(status: presentationStatus)
            throw error
        }
    }

    public func requestUnlockPanel() async
        -> AtlasLockedShellUnlockFlowState
    {
        if let selectionOperation {
            return await waitForSelectionCompletion(selectionOperation)
        }
        guard
            isUnlockOperationAvailable
                || isNoVaultSelectionRetryAvailable
        else {
            return flowState()
        }
        if let unlockController {
            closeUnlockAdmission()
            let operationGeneration = advanceGeneration()
            var current = await unlockController.currentState()
            guard generation == operationGeneration,
                  lifetime == .started else {
                return flowState()
            }
            if current.status == .cancelled {
                current = await unlockController.select(nil)
                guard generation == operationGeneration,
                      lifetime == .started else {
                    return flowState()
                }
            }
            isUnlockPanelPresented = true
            unlockState = current
            let publication = await publishCurrentFlowAndReopenAdmission(
                status: presentationStatus
            )
            if case .failed = publication {
                return await runPrivateFreeBarrier(terminal: false)
            }
            return flowState()
        }
        closeUnlockAdmission()
        let selectionGeneration = advanceGeneration()
        let id = UUID()
        let selector = dependencies.vaultIDSelector
        let task = Task<
            Result<AtlasVaultIDSelection, AtlasVaultIDSelectionError>,
            Never
        > {
            do {
                return .success(try await selector.selectVaultID())
            } catch let error as AtlasVaultIDSelectionError {
                return .failure(error)
            } catch {
                return .failure(.unavailable)
            }
        }
        let operation = SelectionOperation(
            id: id,
            generation: selectionGeneration,
            task: task
        )
        selectionOperation = operation
        Task { [weak self] in
            let result = await task.value
            await self?.completeSelection(id: id, result: result)
        }
        return await waitForSelectionCompletion(operation)
    }

    public func selectUnlockMethod(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasLockedShellUnlockFlowState {
        guard isUnlockOperationAvailable,
              let unlockController else {
            return flowState()
        }
        closeUnlockAdmission()
        let operationGeneration = advanceGeneration()
        let selected = await unlockController.select(method)
        guard generation == operationGeneration,
              lifetime == .started else {
            return flowState()
        }
        unlockState = selected
        let publication = await publishCurrentFlowAndReopenAdmission(
            status: presentationStatus
        )
        if case .failed = publication {
            return await runPrivateFreeBarrier(terminal: false)
        }
        return flowState()
    }

    public func submitUnlock(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration?
    ) async -> AtlasLockedShellUnlockFlowState {
        guard isUnlockOperationAvailable,
              let unlockController else {
            await clearSecret(in: submission)
            return flowState()
        }
        guard submitOperation == nil else {
            await clearSecret(in: submission)
            return flowState()
        }

        closeUnlockAdmission()
        let submitGeneration = advanceGeneration()
        let id = UUID()
        let task = Task {
            await unlockController.submit(
                submission,
                timeout: timeout
            )
        }
        submitOperation = SubmitOperation(
            id: id,
            generation: submitGeneration,
            task: task
        )
        unlockState = AtlasVaultUnlockPresentationState(
            capabilities: unlockState.capabilities,
            selectedMethod: submission.methodForHost,
            status: .activating
        )
        isUnlockPanelPresented = true
        let activatingPublication = await publishCurrentFlow(
            status: .activating,
            expectedGeneration: submitGeneration
        )
        if case .failed = activatingPublication {
            return await runPrivateFreeBarrier(terminal: false)
        }

        let result = await task.value
        guard submitOperation?.id == id,
              submitOperation?.generation == submitGeneration else {
            return flowState()
        }
        submitOperation = nil
        guard generation == submitGeneration,
              lifetime == .started else {
            return await runPrivateFreeBarrier(terminal: false)
        }
        if result.status != .unlocked {
            unlockState = result
        }
        return await finishSubmit(result)
    }

    public func cancelUnlock() async
        -> AtlasLockedShellUnlockFlowState
    {
        guard lifetime == .started,
              let unlockController else {
            return flowState()
        }
        if submitOperation == nil {
            closeUnlockAdmission()
            let operationGeneration = advanceGeneration()
            let cancelled = await unlockController.cancel()
            switch lifetime {
            case .stopping:
                return await runPrivateFreeBarrier(terminal: true)
            case .reconciling:
                return await runPrivateFreeBarrier(terminal: false)
            case .inactive, .starting, .stopped:
                return flowState()
            case .started:
                break
            }
            guard generation == operationGeneration else {
                return flowState()
            }
            if requiresPrivateFreeReconciliation(cancelled.status) {
                unlockState = reconciliationUnlockState()
                isUnlockPanelPresented = true
                return await runPrivateFreeBarrier(terminal: false)
            }
            unlockState = cancelled
            isUnlockPanelPresented = false
            let publication = await publishCurrentFlowAndReopenAdmission(
                status: .locked
            )
            if case .failed = publication {
                return await runPrivateFreeBarrier(terminal: false)
            }
            return flowState()
        }

        closeUnlockAdmission()
        _ = advanceGeneration()
        let active = submitOperation
        let cancelledState = await unlockController.cancel()
        let terminalState = await active?.task.value
        if submitOperation?.id == active?.id {
            submitOperation = nil
        }
        switch lifetime {
        case .stopping:
            return await runPrivateFreeBarrier(terminal: true)
        case .reconciling:
            return await runPrivateFreeBarrier(terminal: false)
        case .inactive, .starting, .stopped:
            return flowState()
        case .started:
            break
        }
        if requiresPrivateFreeReconciliation(cancelledState.status)
            || terminalState.map({
                requiresPrivateFreeReconciliation($0.status)
            }) == true
        {
            return await runPrivateFreeBarrier(terminal: false)
        }
        unlockState = terminalState ?? cancelledState
        isUnlockPanelPresented = false
        return await finishOrdinaryUnlockFailure()
    }

    public func unlockPanelDidDisappear() async
        -> AtlasLockedShellUnlockFlowState
    {
        guard lifetime == .started,
              let unlockController else {
            return flowState()
        }
        if submitOperation == nil {
            closeUnlockAdmission()
            let operationGeneration = advanceGeneration()
            let disappeared = await unlockController.didDisappear()
            switch lifetime {
            case .stopping:
                return await runPrivateFreeBarrier(terminal: true)
            case .reconciling:
                return await runPrivateFreeBarrier(terminal: false)
            case .inactive, .starting, .stopped:
                return flowState()
            case .started:
                break
            }
            guard generation == operationGeneration else {
                return flowState()
            }
            if requiresPrivateFreeReconciliation(disappeared.status) {
                unlockState = reconciliationUnlockState()
                isUnlockPanelPresented = true
                return await runPrivateFreeBarrier(terminal: false)
            }
            unlockState = disappeared
            isUnlockPanelPresented = false
            let publication = await publishCurrentFlowAndReopenAdmission(
                status: .locked
            )
            if case .failed = publication {
                return await runPrivateFreeBarrier(terminal: false)
            }
            return flowState()
        }

        closeUnlockAdmission()
        _ = advanceGeneration()
        let active = submitOperation
        let disappearedState = await unlockController.didDisappear()
        let terminalState = await active?.task.value
        if submitOperation?.id == active?.id {
            submitOperation = nil
        }
        switch lifetime {
        case .stopping:
            return await runPrivateFreeBarrier(terminal: true)
        case .reconciling:
            return await runPrivateFreeBarrier(terminal: false)
        case .inactive, .starting, .stopped:
            return flowState()
        case .started:
            break
        }
        if requiresPrivateFreeReconciliation(disappearedState.status)
            || terminalState.map({
                requiresPrivateFreeReconciliation($0.status)
            }) == true
        {
            return await runPrivateFreeBarrier(terminal: false)
        }
        unlockState = terminalState ?? disappearedState
        isUnlockPanelPresented = false
        return await finishOrdinaryUnlockFailure()
    }

    public func lock() async -> AtlasLockedShellUnlockFlowState {
        switch lifetime {
        case .stopped, .stopping:
            return flowState()
        case .inactive:
            return flowState()
        case .starting:
            explicitLockRequestedDuringStart = true
            closeUnlockAdmission()
            guard let startOperation else {
                explicitLockRequestedDuringStart = false
                return flowState()
            }
            return await awaitDeferredExplicitLock(startOperation)
        case .started, .reconciling:
            break
        }
        if let barrierOperation {
            return await barrierOperation.task.value
        }
        closeUnlockAdmission()
        _ = advanceGeneration()
        return await runPrivateFreeBarrier(terminal: false)
    }

    public func handleLifecycleEvent(
        _ event: AtlasVaultLifecycleEvent
    ) async -> AtlasLockedShellUnlockFlowState {
        guard lifetime != .stopped, lifetime != .stopping else {
            return flowState()
        }
        updateLifecycleState(for: event)
        lifecycleEventRevision &+= 1
        let eventRevision = lifecycleEventRevision
        let safeReopenGeneration: AtlasVaultProductionHostGeneration?
        if event.isSafeReopenEvent {
            safeLifecycleCheckRevision = eventRevision
            closeUnlockAdmission()
            safeReopenGeneration = generation
        } else {
            safeReopenGeneration = nil
        }
        if event.closesUnlockAdmission {
            invalidateSafeLifecycleCheck()
            lifecycleAdmissionPermitted = false
            closeUnlockAdmission()
            _ = advanceGeneration()
        }

        await dependencies.lifecycle.handle(event)

        switch event {
        case .didBecomeActive, .protectedDataBecameAvailable:
            guard let safeReopenGeneration else {
                return flowState()
            }
            return await finishSafeReopen(
                expectedGeneration: safeReopenGeneration,
                lifecycleRevision: eventRevision
            )
        case .willResignActive:
            guard lifetime != .inactive,
                  lifetime != .starting,
                  isPublicOperationAvailable else {
                return flowState()
            }
            if submitOperation != nil {
                return await cancelUnlock()
            }
            if privateSessionIsActive
                || unlockState.status == .unlocked
                || privateMutationOperation != nil
            {
                return await runPrivateFreeBarrier(terminal: false)
            }
            let publication = await publishCurrentFlow(
                status: presentationStatus
            )
            if case .failed = publication {
                return await runPrivateFreeBarrier(terminal: false)
            }
            return flowState()
        case .didEnterBackground, .protectedDataBecameUnavailable:
            guard lifetime != .inactive,
                  lifetime != .starting,
                  isPublicOperationAvailable else {
                return flowState()
            }
            return await runPrivateFreeBarrier(terminal: false)
        case .willTerminate:
            return await stop()
        }
    }

    public nonisolated var description: String {
        "AtlasVaultProductionHost(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    func isInactiveForTesting() -> Bool {
        lifetime == .inactive
    }

    func hasUnlockControllerForTesting() -> Bool {
        unlockController != nil
    }

    func hasSelectedVaultForTesting() -> Bool {
        selectedVaultID != nil
    }

    func hasActiveSubmitForTesting() -> Bool {
        submitOperation != nil
    }

    func retainedSearchOperationCountForTesting() -> Int {
        retainedSearchOperations.count
    }

    private var isPublicOperationAvailable: Bool {
        lifetime == .started || lifetime == .reconciling
    }

    private var isUnlockOperationAvailable: Bool {
        lifetime == .started
            && unlockAdmissionOpen
            && !isTerminated
    }

    private var isNoVaultSelectionRetryAvailable: Bool {
        lifetime == .started
            && lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && safeLifecycleCheckRevision == nil
            && !isTerminated
            && selectionOperation == nil
            && submitOperation == nil
            && barrierOperation == nil
            && stopOperation == nil
            && unlockController == nil
            && shell.vaultStatus == .noVault
    }

    private var startAdmissionPermitted: Bool {
        lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && safeLifecycleCheckRevision == nil
            && !isTerminated
    }

    private var presentationStatus: AtlasVaultPresentationStatus {
        switch unlockState.status {
        case .activating:
            .activating
        case .unlocked:
            .unlocked
        case .hostReconciliationRequired:
            .locking
        case .methodUnavailable:
            .keyUnavailable
        case .failed, .timedOut:
            .failed
        case .locked, .ready, .cancelled:
            shell.vaultStatus == .noVault ? .noVault : .locked
        }
    }

    private func performStart() async -> Result<
        AtlasLockedShellUnlockFlowState,
        AtlasVaultProductionHostError
    > {
        guard lifetime == .starting else {
            return .failure(.stopped)
        }
        guard await dependencies.presentation.start() else {
            return failStartForPresentation()
        }
        presentationWasStarted = true
        guard lifetime == .starting else {
            return .failure(.stopped)
        }

        if !restoreAttempted {
            restoreAttempted = true
            do {
                let snapshot = try await dependencies.publicSnapshotRestorer
                    .restore()
                guard lifetime == .starting else {
                    return .failure(.stopped)
                }
                mapRestoredSnapshot(snapshot)
            } catch {
                guard lifetime == .starting else {
                    return .failure(.stopped)
                }
                replaceShell(
                    serviceStatus: .unavailable,
                    cacheFreshness: .unavailable,
                    searchQuery: "",
                    publicJobs: [],
                    isSearching: false,
                    canRequestUnlock: false
                )
            }
        }

        unlockState = lockedUnlockState()
        isUnlockPanelPresented = false
        closeUnlockAdmission()
        let startGeneration = advanceGeneration()
        let publication = await publishAndReset(
            status: .locked,
            expectedGeneration: startGeneration
        )
        guard case .acknowledged = publication,
              lifetime == .starting,
              generation == startGeneration else {
            return failStartForPresentation()
        }

        let startedGeneration = advanceGeneration()
        while true {
            await waitForSafeLifecycleCheckCompletion()
            guard lifetime == .starting,
                  generation == startedGeneration else {
                return failStartForPresentation()
            }

            let mayOpen = startAdmissionPermitted
                && !explicitLockRequestedDuringStart
            let startedShell = shellReplacingCanRequestUnlock(mayOpen)
            let startedState = flowState(publicShell: startedShell)
            let finalPublication = await publishAndReset(
                status: .locked,
                expectedGeneration: startedGeneration,
                ownerState: startedState
            )
            guard lifetime == .starting,
                  generation == startedGeneration else {
                return failStartForPresentation()
            }
            switch finalPublication {
            case .acknowledged:
                break
            case .stale:
                continue
            case .failed:
                return failStartForPresentation()
            }
            let currentMayOpen = startAdmissionPermitted
                && !explicitLockRequestedDuringStart
            guard safeLifecycleCheckRevision == nil,
                  currentMayOpen == mayOpen else {
                continue
            }

            lifetime = .started
            shell = startedShell
            unlockAdmissionOpen = mayOpen
            return .success(flowState())
        }
    }

    private func performStop() async -> AtlasLockedShellUnlockFlowState {
        await awaitStartingOperationBeforeStop()
        guard presentationWasStarted else {
            lifetime = .stopped
            closeUnlockAdmission()
            return flowState()
        }

        selectionOperation?.task.cancel()
        await cancelAndDrainSearchOperations()
        replaceShell(isSearching: false)
        let state = await runPrivateFreeBarrier(terminal: true)
        lifetime = .stopped
        closeUnlockAdmission()
        return state
    }

    private func cancelAndDrainSearchOperations() async {
        searchGeneration &+= 1
        let operations = Array(retainedSearchOperations.values)
        searchOperation = nil
        for operation in operations {
            operation.task.cancel()
        }
        for operation in operations {
            _ = await operation.task.value
        }
        for operation in operations {
            retainedSearchOperations[operation.id] = nil
        }
    }

    private func awaitStartingOperationBeforeStop() async {
        guard let operation = startOperation else {
            return
        }
        _ = await operation.task.value
        if startOperation?.id == operation.id {
            startOperation = nil
        }
    }

    private func awaitDeferredExplicitLock(
        _ operation: StartOperation
    ) async -> AtlasLockedShellUnlockFlowState {
        let startResult = await operation.task.value
        if startOperation?.id == operation.id {
            startOperation = nil
        }

        guard case .success = startResult else {
            switch lifetime {
            case .stopping:
                if let stopOperation {
                    return await stopOperation.task.value
                }
                if let barrierOperation {
                    return await barrierOperation.task.value
                }
            case .stopped, .inactive, .starting, .started, .reconciling:
                break
            }
            return flowState()
        }

        switch lifetime {
        case .stopping:
            explicitLockRequestedDuringStart = false
            if let stopOperation {
                return await stopOperation.task.value
            }
            if let barrierOperation {
                return await barrierOperation.task.value
            }
            return flowState()
        case .stopped:
            explicitLockRequestedDuringStart = false
            return flowState()
        case .inactive:
            explicitLockRequestedDuringStart = false
            closeUnlockAdmission()
            return flowState()
        case .starting:
            explicitLockRequestedDuringStart = false
            closeUnlockAdmission()
            return flowState()
        case .reconciling:
            explicitLockRequestedDuringStart = false
            if let barrierOperation {
                return await barrierOperation.task.value
            }
            return flowState()
        case .started:
            break
        }

        guard explicitLockRequestedDuringStart else {
            if let barrierOperation {
                return await barrierOperation.task.value
            }
            return flowState()
        }

        explicitLockRequestedDuringStart = false
        closeUnlockAdmission()
        _ = advanceGeneration()
        return await runPrivateFreeBarrier(terminal: false)
    }

    private func finishSubmit(
        _ result: AtlasVaultUnlockPresentationState
    ) async -> AtlasLockedShellUnlockFlowState {
        switch result.status {
        case .unlocked:
            let successGeneration = generation
            guard let selectedVaultID else {
                return await runPrivateFreeBarrier(terminal: false)
            }
            let runtimeStatus = await dependencies.runtime.status()
            guard generation == successGeneration,
                  lifetime == .started,
                  runtimeStatus == .unlocked else {
                return await runPrivateFreeBarrier(terminal: false)
            }
            privateSessionIsActive = false
            let privateSessionReady = await dependencies
                .privateSessionBoundary
                .activatePrivateSession(
                    selectedVault: selectedVaultID.vaultID
                )
            let confirmedRuntimeStatus = await dependencies.runtime.status()
            guard privateSessionReady,
                  generation == successGeneration,
                  lifetime == .started,
                  confirmedRuntimeStatus == .unlocked,
                  privateSessionActivationPermitted(
                    selectedVaultID,
                    expectedGeneration: successGeneration
                  ) else {
                return await runPrivateFreeBarrier(terminal: false)
            }
            privateSessionIsActive = true
            unlockState = result
            replaceShell(canRequestUnlock: false)
            isUnlockPanelPresented = false
            let publication = await publishAndReset(
                status: .unlocked,
                expectedGeneration: successGeneration
            )
            guard case .acknowledged = publication else {
                return await runPrivateFreeBarrier(terminal: false)
            }
            return flowState()
        case .hostReconciliationRequired, .activating:
            return await runPrivateFreeBarrier(terminal: false)
        case .locked,
             .ready,
             .methodUnavailable,
             .failed,
             .cancelled,
             .timedOut:
            return await finishOrdinaryUnlockFailure()
        }
    }

    private func finishSelection(
        _ operation: SelectionOperation,
        result: Result<
            AtlasVaultIDSelection,
            AtlasVaultIDSelectionError
        >
    ) async -> AtlasLockedShellUnlockFlowState {
        guard selectionOperation?.id == operation.id else {
            return flowState()
        }
        guard generation == operation.generation,
              lifetime == .started,
              !isTerminated else {
            selectionOperation = nil
            let state = flowState()
            resumeSelectionWaiters(with: state)
            return state
        }

        switch result {
        case .success(.none):
            replaceShell(
                vaultStatus: .noVault,
                canRequestUnlock: false
            )
            unlockState = lockedUnlockState()
            isUnlockPanelPresented = false
            unlockAdmissionOpen = false
        case let .success(.selected(value)):
            let resolvedCapabilities:
                Result<AtlasVaultUnlockCapabilities, Error>
            do {
                resolvedCapabilities = .success(
                    try await dependencies.unlockCapabilitiesResolver
                        .capabilities(for: value)
                )
            } catch {
                resolvedCapabilities = .failure(error)
            }
            guard selectionOperation?.id == operation.id else {
                return flowState()
            }
            guard generation == operation.generation,
                  lifetime == .started,
                  !isTerminated else {
                abandonSelectionAndResumeCallers()
                return flowState()
            }
            guard case let .success(capabilities) =
                resolvedCapabilities
            else {
                replaceShell(
                    vaultStatus: .keyUnavailable,
                    canRequestUnlock: false
                )
                unlockState = lockedUnlockState()
                isUnlockPanelPresented = false
                break
            }
            guard
                !capabilities.availableMethods.isEmpty
            else {
                replaceShell(
                    vaultStatus: .keyUnavailable,
                    canRequestUnlock: false
                )
                unlockState = lockedUnlockState()
                isUnlockPanelPresented = false
                break
            }
            let controller = dependencies.unlockControllerBuilder
                .makeController(
                    selectedVaultID: value,
                    capabilities: capabilities,
                    coordinator: dependencies.unlockCoordinator
                )
            let current = await controller.currentState()
            guard selectionOperation?.id == operation.id,
                  generation == operation.generation,
                  lifetime == .started,
                  !isTerminated else {
                selectionOperation = nil
                let state = flowState()
                resumeSelectionWaiters(with: state)
                return state
            }
            selectedVaultID = value
            unlockController = controller
            unlockState = current
            isUnlockPanelPresented = true
            replaceShell(
                vaultStatus: .locked,
                canRequestUnlock: false
            )
        case .failure:
            replaceShell(
                vaultStatus: .keyUnavailable,
                canRequestUnlock: false
            )
            unlockState = lockedUnlockState()
            isUnlockPanelPresented = false
        }

        selectionOperation = nil
        let publication: PublicationResult
        switch result {
        case .success(.none):
            publication = await publishCurrentFlow(
                status: presentationStatus
            )
        case .success(.selected), .failure:
            publication = await publishCurrentFlowAndReopenAdmission(
                status: presentationStatus
            )
        }
        let state: AtlasLockedShellUnlockFlowState
        if case .failed = publication {
            state = await runPrivateFreeBarrier(terminal: false)
        } else {
            state = flowState()
        }
        resumeSelectionWaiters(with: state)
        return state
    }

    private func completeSelection(
        id: UUID,
        result: Result<
            AtlasVaultIDSelection,
            AtlasVaultIDSelectionError
        >
    ) async {
        guard let operation = selectionOperation,
              operation.id == id else {
            return
        }
        _ = await finishSelection(operation, result: result)
    }

    private func waitForSelectionCompletion(
        _ operation: SelectionOperation
    ) async -> AtlasLockedShellUnlockFlowState {
        guard selectionOperation?.id == operation.id else {
            return flowState()
        }
        return await withCheckedContinuation { continuation in
            guard selectionOperation?.id == operation.id else {
                continuation.resume(returning: flowState())
                return
            }
            selectionWaiters.append(continuation)
        }
    }

    private func resumeSelectionWaiters(
        with state: AtlasLockedShellUnlockFlowState
    ) {
        let waiters = selectionWaiters
        selectionWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: state)
        }
    }

    private func finishSafeReopen(
        expectedGeneration: AtlasVaultProductionHostGeneration,
        lifecycleRevision: UInt64
    ) async -> AtlasLockedShellUnlockFlowState {
        guard isCurrentLifecycleCheck(lifecycleRevision) else {
            return flowState()
        }
        let lifecycleStatus = await dependencies.lifecycle.status()
        guard isCurrentLifecycleCheck(lifecycleRevision) else {
            return flowState()
        }
        lifecycleAdmissionPermitted = persistentLifecycleEligibility(
            lifecycleStatus
        )
        guard lifetime == .started,
              generation == expectedGeneration,
              !isTerminated else {
            clearSafeLifecycleCheck(lifecycleRevision)
            return flowState()
        }
        var runtimeStatus = await dependencies.runtime.status()
        guard isCurrentSafeReopen(
            expectedGeneration,
            lifecycleRevision: lifecycleRevision
        ) else {
            clearSafeLifecycleCheck(lifecycleRevision)
            return flowState()
        }

        var mayReopen = maySafelyReopen(
            expectedGeneration: expectedGeneration,
            lifecycleRevision: lifecycleRevision,
            runtimeStatus: runtimeStatus
        )
        while true {
            let targetShell = shellReplacingCanRequestUnlock(mayReopen)
            let targetState = flowState(publicShell: targetShell)
            let publication = await publishAndReset(
                status: presentationStatus,
                expectedGeneration: expectedGeneration,
                ownerState: targetState
            )
            guard isCurrentSafeReopen(
                expectedGeneration,
                lifecycleRevision: lifecycleRevision
            ) else {
                clearSafeLifecycleCheck(lifecycleRevision)
                return flowState()
            }

            switch publication {
            case .acknowledged:
                var currentMayReopen = maySafelyReopen(
                    expectedGeneration: expectedGeneration,
                    lifecycleRevision: lifecycleRevision,
                    runtimeStatus: runtimeStatus
                )
                if !mayReopen, currentMayReopen {
                    runtimeStatus = await dependencies.runtime.status()
                    guard isCurrentSafeReopen(
                        expectedGeneration,
                        lifecycleRevision: lifecycleRevision
                    ) else {
                        clearSafeLifecycleCheck(lifecycleRevision)
                        return flowState()
                    }
                    currentMayReopen = maySafelyReopen(
                        expectedGeneration: expectedGeneration,
                        lifecycleRevision: lifecycleRevision,
                        runtimeStatus: runtimeStatus
                    )
                }
                guard currentMayReopen == mayReopen else {
                    mayReopen = currentMayReopen
                    continue
                }
                clearSafeLifecycleCheck(lifecycleRevision)
                replaceShell(canRequestUnlock: mayReopen)
                unlockAdmissionOpen = mayReopen
                return flowState()
            case .stale:
                clearSafeLifecycleCheck(lifecycleRevision)
                return flowState()
            case .failed:
                clearSafeLifecycleCheck(lifecycleRevision)
                return await runPrivateFreeBarrier(terminal: false)
            }
        }
    }

    private func isCurrentSafeReopen(
        _ expectedGeneration: AtlasVaultProductionHostGeneration,
        lifecycleRevision: UInt64
    ) -> Bool {
        isCurrentLifecycleCheck(lifecycleRevision)
            && generation == expectedGeneration
            && lifetime == .started
            && barrierOperation == nil
            && stopOperation == nil
            && !isTerminated
    }

    private func maySafelyReopen(
        expectedGeneration: AtlasVaultProductionHostGeneration,
        lifecycleRevision: UInt64,
        runtimeStatus: AtlasVaultRuntimeStatus
    ) -> Bool {
        isCurrentSafeReopen(
            expectedGeneration,
            lifecycleRevision: lifecycleRevision
        )
            && lifecycleAdmissionPermitted
            && runtimeStatus == .locked
            && selectionOperation == nil
            && submitOperation == nil
            && shell.vaultStatus != .noVault
            && unlockStatePermitsAdmission
    }

    private func isCurrentLifecycleCheck(_ revision: UInt64) -> Bool {
        lifecycleEventRevision == revision
            && safeLifecycleCheckRevision == revision
            && lifetime != .stopping
            && lifetime != .stopped
            && !isTerminated
    }

    private func persistentLifecycleEligibility(
        _ status: AtlasVaultLifecycleStatus
    ) -> Bool {
        lifecycleIsActive
            && protectedDataIsAvailable
            && !isTerminated
            && !status.hasPendingGraceLock
            && status.failure == nil
    }

    private func clearSafeLifecycleCheck(_ revision: UInt64) {
        if safeLifecycleCheckRevision == revision {
            safeLifecycleCheckRevision = nil
            resumeSafeLifecycleCheckWaiters()
        }
    }

    private func invalidateSafeLifecycleCheck() {
        safeLifecycleCheckRevision = nil
        resumeSafeLifecycleCheckWaiters()
    }

    private func waitForSafeLifecycleCheckCompletion() async {
        guard safeLifecycleCheckRevision != nil else {
            return
        }
        await withCheckedContinuation { continuation in
            guard safeLifecycleCheckRevision != nil else {
                continuation.resume()
                return
            }
            safeLifecycleCheckWaiters.append(continuation)
        }
    }

    private func resumeSafeLifecycleCheckWaiters() {
        let waiters = safeLifecycleCheckWaiters
        safeLifecycleCheckWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
    }

    private func requiresPrivateFreeReconciliation(
        _ status: AtlasVaultUnlockPresentationStatus
    ) -> Bool {
        status == .unlocked || status == .hostReconciliationRequired
    }

    private func finishOrdinaryUnlockFailure() async
        -> AtlasLockedShellUnlockFlowState
    {
        let status = await dependencies.runtime.status()
        switch status {
        case .locked:
            break
        case .failed:
            return await runPrivateFreeBarrier(terminal: false)
        case .activating, .locking, .unlocked, .saving:
            return await runPrivateFreeBarrier(terminal: false)
        }

        if unlockState.status == .cancelled {
            isUnlockPanelPresented = false
        }
        let publication = await publishCurrentFlowAndReopenAdmission(
            status: presentationStatus
        )
        if case .failed = publication {
            return await runPrivateFreeBarrier(terminal: false)
        }
        return flowState()
    }

    private func runPrivateFreeBarrier(
        terminal: Bool,
        skipsPrivateSessionDrain: Bool = false
    ) async -> AtlasLockedShellUnlockFlowState {
        let terminalBarrierRequested = terminal
            || lifetime == .stopping
            || lifetime == .stopped
            || stopOperation != nil
        if let barrierOperation {
            if terminalBarrierRequested && !barrierOperation.terminal {
                barrierOperation.task.cancel()
                self.barrierOperation = nil
                lifetime = .stopping
                closeUnlockAdmission()
                _ = advanceGeneration()
                return await runPrivateFreeBarrier(
                    terminal: true,
                    skipsPrivateSessionDrain: false
                )
            }
            if skipsPrivateSessionDrain
                && !barrierOperation.skipsPrivateSessionDrain
            {
                await dependencies.privateSessionBoundary
                    .hidePrivatePresentation()
                privateSessionIsActive = false
                return flowState()
            }
            return await barrierOperation.task.value
        }

        lifetime = terminalBarrierRequested ? .stopping : .reconciling
        closeUnlockAdmission()
        let id = UUID()
        let operationGeneration = generation
        let task = Task { [self] in
            await performPrivateFreeBarrier(
                operationID: id,
                operationGeneration: operationGeneration,
                terminal: terminalBarrierRequested,
                skipsPrivateSessionDrain: skipsPrivateSessionDrain
            )
        }
        barrierOperation = BarrierOperation(
            id: id,
            terminal: terminalBarrierRequested,
            skipsPrivateSessionDrain: skipsPrivateSessionDrain,
            task: task
        )
        let result = await task.value
        if barrierOperation?.id == id {
            barrierOperation = nil
        }
        return result
    }

    private func performPrivateFreeBarrier(
        operationID: UUID,
        operationGeneration: AtlasVaultProductionHostGeneration,
        terminal: Bool,
        skipsPrivateSessionDrain: Bool
    ) async -> AtlasLockedShellUnlockFlowState {
        guard barrierOperation?.id == operationID else {
            return flowState()
        }
        await dependencies.privateSessionBoundary
            .hidePrivatePresentation()
        guard barrierOperation?.id == operationID else {
            return flowState()
        }
        privateSessionIsActive = false
        if !skipsPrivateSessionDrain {
            let activePrivateMutation = privateMutationOperation
            activePrivateMutation?.task.cancel()
            if let activePrivateMutation {
                _ = await activePrivateMutation.task.value
                guard barrierOperation?.id == operationID else {
                    return flowState()
                }
                if privateMutationOperation?.id
                    == activePrivateMutation.id
                {
                    privateMutationOperation = nil
                }
            }
            await dependencies.privateSessionBoundary
                .stopAndDrainPrivateSession()
            guard barrierOperation?.id == operationID else {
                return flowState()
            }
        }
        if terminal {
            await dependencies.presentationOwner
                .supersedePresentationGeneration(operationGeneration)
            guard barrierOperation?.id == operationID,
                  generation == operationGeneration else {
                return flowState()
            }
            invalidatePublicationPermit()
        }
        abandonSelectionAndResumeCallers()
        let preservesNoVault = shell.vaultStatus == .noVault
            && selectedVaultID == nil
        let lockedPresentationStatus: AtlasVaultPresentationStatus =
            preservesNoVault ? .noVault : .locked
        closeUnlockAdmission()
        let reconciliationGeneration: AtlasVaultProductionHostGeneration
        if terminal {
            reconciliationGeneration = operationGeneration
        } else {
            reconciliationGeneration = advanceGeneration()
        }
        unlockState = AtlasVaultUnlockPresentationState(
            capabilities: unlockState.capabilities,
            selectedMethod: nil,
            status: .hostReconciliationRequired
        )
        isUnlockPanelPresented = true
        let reconciliationPublication = await publishAndReset(
            status: .locking,
            expectedGeneration: reconciliationGeneration
        )
        guard barrierOperation?.id == operationID else {
            return flowState()
        }
        var barrierSucceeded: Bool
        if case .acknowledged = reconciliationPublication {
            barrierSucceeded = true
        } else {
            barrierSucceeded = false
        }

        let active = submitOperation
        if active != nil, let unlockController {
            _ = await unlockController.cancel()
            guard barrierOperation?.id == operationID else {
                return flowState()
            }
        }
        if let active {
            _ = await active.task.value
            guard barrierOperation?.id == operationID else {
                return flowState()
            }
            if submitOperation?.id == active.id {
                submitOperation = nil
            }
        }

        let initialRuntimeStatus = await dependencies.runtime.status()
        guard barrierOperation?.id == operationID else {
            return flowState()
        }
        if terminal || initialRuntimeStatus != .locked {
            await dependencies.runtime.lock()
            guard barrierOperation?.id == operationID else {
                return flowState()
            }
        }
        if let unlockController {
            _ = await unlockController.hostDidLock()
            guard barrierOperation?.id == operationID else {
                return flowState()
            }
        }

        let lockedGeneration = advanceGeneration()
        replaceShell(
            vaultStatus: preservesNoVault ? .noVault : .locked,
            canRequestUnlock: false
        )
        let lockedPublication = await publishAndReset(
            status: lockedPresentationStatus,
            expectedGeneration: lockedGeneration,
            ownerState: flowState()
        )
        guard barrierOperation?.id == operationID else {
            return flowState()
        }
        if case .acknowledged = lockedPublication {
        } else {
            barrierSucceeded = false
        }

        let observable = await dependencies.presentation.currentSnapshot()
        guard barrierOperation?.id == operationID else {
            return flowState()
        }
        if observable.privateState != nil {
            barrierSucceeded = false
        }
        let finalRuntimeStatus = await dependencies.runtime.status()
        guard barrierOperation?.id == operationID else {
            return flowState()
        }
        if finalRuntimeStatus != .locked {
            barrierSucceeded = false
        }

        if !terminal, lifetime == .stopping {
            closeUnlockAdmission()
            return flowState()
        }

        if barrierSucceeded && !terminal {
            unlockState = lockedUnlockState()
            isUnlockPanelPresented = false
            let ordinaryGeneration = advanceGeneration()
            var requiresFreshRuntimeAdmissionProof = false
            while barrierSucceeded {
                let admissionLifecycleRevision = lifecycleEventRevision
                let admissionSafeCheckRevision = safeLifecycleCheckRevision
                let mayOpen = !requiresFreshRuntimeAdmissionProof
                    && barrierCompletionAdmissionPermitted
                let ordinaryShell = shellReplacingCanRequestUnlock(mayOpen)
                let ordinaryState: AtlasLockedShellUnlockFlowState
                if requiresFreshRuntimeAdmissionProof {
                    ordinaryState = AtlasLockedShellUnlockFlowState(
                        publicShell: ordinaryShell,
                        unlockPresentationState: reconciliationUnlockState(),
                        isUnlockPanelPresented: true
                    )
                } else {
                    ordinaryState = flowState(publicShell: ordinaryShell)
                }
                let ordinaryPresentationStatus: AtlasVaultPresentationStatus =
                    requiresFreshRuntimeAdmissionProof
                    ? .locking
                    : lockedPresentationStatus
                let ordinaryPublication = await publishAndReset(
                    status: ordinaryPresentationStatus,
                    expectedGeneration: ordinaryGeneration,
                    ownerState: ordinaryState
                )
                guard barrierOperation?.id == operationID else {
                    return flowState()
                }
                guard generation == ordinaryGeneration else {
                    barrierSucceeded = false
                    break
                }
                if case .stale = ordinaryPublication,
                   !publicationLifecycleFenceIsCurrent(
                       revision: admissionLifecycleRevision,
                       safeCheckRevision: admissionSafeCheckRevision
                   ) {
                    closeUnlockAdmission()
                    unlockState = reconciliationUnlockState()
                    isUnlockPanelPresented = true
                    requiresFreshRuntimeAdmissionProof = true
                    continue
                }
                guard case .acknowledged = ordinaryPublication else {
                    barrierSucceeded = false
                    break
                }
                guard publicationLifecycleFenceIsCurrent(
                    revision: admissionLifecycleRevision,
                    safeCheckRevision: admissionSafeCheckRevision
                ) else {
                    closeUnlockAdmission()
                    unlockState = reconciliationUnlockState()
                    isUnlockPanelPresented = true
                    requiresFreshRuntimeAdmissionProof = true
                    continue
                }
                if requiresFreshRuntimeAdmissionProof {
                    var currentRuntimeStatus = await dependencies.runtime
                        .status()
                    guard barrierOperation?.id == operationID else {
                        return flowState()
                    }
                    guard generation == ordinaryGeneration else {
                        barrierSucceeded = false
                        break
                    }
                    guard publicationLifecycleFenceIsCurrent(
                        revision: admissionLifecycleRevision,
                        safeCheckRevision: admissionSafeCheckRevision
                    ) else {
                        closeUnlockAdmission()
                        continue
                    }
                    if currentRuntimeStatus != .locked {
                        await dependencies.runtime.lock()
                        guard barrierOperation?.id == operationID else {
                            return flowState()
                        }
                        guard generation == ordinaryGeneration else {
                            barrierSucceeded = false
                            break
                        }
                        guard publicationLifecycleFenceIsCurrent(
                            revision: admissionLifecycleRevision,
                            safeCheckRevision: admissionSafeCheckRevision
                        ) else {
                            closeUnlockAdmission()
                            continue
                        }
                        currentRuntimeStatus = await dependencies.runtime
                            .status()
                        guard barrierOperation?.id == operationID else {
                            return flowState()
                        }
                        guard generation == ordinaryGeneration else {
                            barrierSucceeded = false
                            break
                        }
                        guard publicationLifecycleFenceIsCurrent(
                            revision: admissionLifecycleRevision,
                            safeCheckRevision: admissionSafeCheckRevision
                        ) else {
                            closeUnlockAdmission()
                            continue
                        }
                    }
                    guard currentRuntimeStatus == .locked else {
                        barrierSucceeded = false
                        break
                    }
                    unlockState = lockedUnlockState()
                    isUnlockPanelPresented = false
                    requiresFreshRuntimeAdmissionProof = false
                    continue
                }
                guard !mayOpen || barrierCompletionAdmissionPermitted else {
                    closeUnlockAdmission()
                    unlockState = reconciliationUnlockState()
                    isUnlockPanelPresented = true
                    requiresFreshRuntimeAdmissionProof = true
                    continue
                }
                selectedVaultID = nil
                unlockController = nil
                lifetime = .started
                unlockAdmissionOpen = mayOpen
                shell = ordinaryShell
                return flowState()
            }
        }

        if terminal {
            let finished = await dependencies.presentation.finish()
            guard barrierOperation?.id == operationID else {
                return flowState()
            }
            barrierSucceeded = barrierSucceeded && finished
            if barrierSucceeded {
                let terminalRuntimeStatus = await dependencies.runtime.status()
                guard barrierOperation?.id == operationID else {
                    return flowState()
                }
                barrierSucceeded = terminalRuntimeStatus == .locked
            }
            if barrierSucceeded {
                let terminalOwnerGeneration = advanceGeneration()
                let terminalState = AtlasLockedShellUnlockFlowState(
                    publicShell: shell,
                    unlockPresentationState: lockedUnlockState(),
                    isUnlockPanelPresented: false
                )
                let ownerAcknowledged = await dependencies.presentationOwner
                    .resetPresentation(
                        to: terminalState,
                        generation: terminalOwnerGeneration
                    )
                guard barrierOperation?.id == operationID,
                      generation == terminalOwnerGeneration else {
                    return flowState()
                }
                barrierSucceeded = ownerAcknowledged
            }
            closeUnlockAdmission()
            if barrierSucceeded {
                selectedVaultID = nil
                unlockController = nil
                unlockState = lockedUnlockState()
                isUnlockPanelPresented = false
            } else {
                unlockState = reconciliationUnlockState()
                isUnlockPanelPresented = true
            }
            lifetime = .stopped
            return flowState()
        }

        closeUnlockAdmission()
        unlockState = reconciliationUnlockState()
        isUnlockPanelPresented = true
        if lifetime != .stopping {
            lifetime = .reconciling
        }
        return flowState()
    }

    private func publishCurrentFlow(
        status: AtlasVaultPresentationStatus
    ) async -> PublicationResult {
        let publicationGeneration = generation
        return await publishAndReset(
            status: status,
            expectedGeneration: publicationGeneration
        )
    }

    private func publishCurrentFlow(
        status: AtlasVaultPresentationStatus,
        expectedGeneration: AtlasVaultProductionHostGeneration
    ) async -> PublicationResult {
        await publishAndReset(
            status: status,
            expectedGeneration: expectedGeneration
        )
    }

    private func publishCurrentFlowAndReopenAdmission(
        status: AtlasVaultPresentationStatus
    ) async -> PublicationResult {
        closeUnlockAdmission()
        guard lifetime == .started else {
            return .stale
        }
        let admissionLifecycleRevision = lifecycleEventRevision
        let admissionSafeCheckRevision = safeLifecycleCheckRevision
        let mayOpen = transientAdmissionPermitted
        let readyShell = shellReplacingCanRequestUnlock(mayOpen)
        let readyState = flowState(publicShell: readyShell)
        let readyGeneration = generation
        let publication = await publishAndReset(
            status: status,
            expectedGeneration: readyGeneration,
            ownerState: readyState
        )
        guard case .acknowledged = publication,
              generation == readyGeneration,
              lifetime == .started else {
            return publication
        }
        guard lifecycleEventRevision == admissionLifecycleRevision,
              safeLifecycleCheckRevision == admissionSafeCheckRevision else {
            closeUnlockAdmission()
            return .stale
        }
        guard !mayOpen || transientAdmissionPermitted else {
            closeUnlockAdmission()
            return .stale
        }
        shell = readyShell
        unlockAdmissionOpen = mayOpen
        return .acknowledged
    }

    private var transientAdmissionPermitted: Bool {
        lifetime == .started
            && lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && safeLifecycleCheckRevision == nil
            && !isTerminated
            && selectionOperation == nil
            && submitOperation == nil
            && barrierOperation == nil
            && stopOperation == nil
            && shell.vaultStatus != .noVault
            && unlockStatePermitsAdmission
    }

    private func privateMutationAdmissionPermitted(
        for request: AtlasVaultRuntimeMutationRequest
    ) -> Bool {
        guard let selectedVaultID else {
            return false
        }
        return lifetime == .started
            && privateSessionIsActive
            && unlockState.status == .unlocked
            && flowState().mode == .unlockedTransition
            && selectedVaultID.vaultID == request.expectedVaultID
            && lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && safeLifecycleCheckRevision == nil
            && !isTerminated
            && selectionOperation == nil
            && submitOperation == nil
            && privateMutationOperation == nil
            && barrierOperation == nil
            && stopOperation == nil
    }

    private func privateSessionActivationPermitted(
        _ selectedVaultID: AtlasSelectedVaultID,
        expectedGeneration: AtlasVaultProductionHostGeneration
    ) -> Bool {
        lifetime == .started
            && generation == expectedGeneration
            && self.selectedVaultID == selectedVaultID
            && lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && safeLifecycleCheckRevision == nil
            && !isTerminated
            && selectionOperation == nil
            && submitOperation == nil
            && privateMutationOperation == nil
            && barrierOperation == nil
            && stopOperation == nil
    }

    private var savedSearchHandoffAdmissionPermitted: Bool {
        savedSearchHandoffOperation == nil
            && savedSearchHandoffBaseAdmissionPermitted
    }

    private var savedSearchHandoffBaseAdmissionPermitted: Bool {
        lifetime == .started
            && privateSessionIsActive
            && unlockState.status == .unlocked
            && flowState().mode == .unlockedTransition
            && selectedVaultID != nil
            && lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && safeLifecycleCheckRevision == nil
            && !isTerminated
            && selectionOperation == nil
            && submitOperation == nil
            && privateMutationOperation == nil
            && searchOperation == nil
            && retainedSearchOperations.isEmpty
            && barrierOperation == nil
            && stopOperation == nil
    }

    private var unavailableSavedSearchHandoffResult:
        AtlasSavedSearchPublicHandoffResult
    {
        if lifetime == .stopping
            || lifetime == .stopped
            || stopOperation != nil
            || isTerminated
        {
            return .stopped
        }
        if Task.isCancelled || savedSearchHandoffOperation != nil {
            return .cancelled
        }
        return .lockFailed
    }

    private func executeSavedSearchPublicHandoff(
        _ request: AtlasPublicJobSearchRequest,
        operationID: UUID,
        admissionGeneration: AtlasVaultProductionHostGeneration
    ) async -> AtlasSavedSearchPublicHandoffResult {
        guard savedSearchHandoffOperation?.id == operationID,
              generation == admissionGeneration,
              savedSearchHandoffBaseAdmissionPermitted,
              !Task.isCancelled else {
            return unavailableSavedSearchHandoffResult
        }

        _ = await runPrivateFreeBarrier(
            terminal: false,
            skipsPrivateSessionDrain: false
        )
        guard savedSearchHandoffOperation?.id == operationID else {
            return unavailableSavedSearchHandoffResult
        }
        if lifetime == .stopping
            || lifetime == .stopped
            || stopOperation != nil
            || isTerminated
        {
            return .stopped
        }
        guard !Task.isCancelled else {
            return .cancelled
        }

        let lockedGeneration = generation
        guard savedSearchLockedPublicAdmissionPermitted(
            operationID: operationID
        ) else {
            return .lockFailed
        }
        let runtimeStatus = await dependencies.runtime.status()
        guard generation == lockedGeneration,
              runtimeStatus == .locked,
              savedSearchLockedPublicAdmissionPermitted(
                  operationID: operationID
              ) else {
            return unavailableSavedSearchHandoffResult
        }

        do {
            _ = try await performPublicSearch(request)
            guard savedSearchHandoffOperation?.id == operationID,
                  generation == lockedGeneration,
                  lifetime == .started else {
                return unavailableSavedSearchHandoffResult
            }
            return .completed
        } catch {
            if lifetime == .stopping
                || lifetime == .stopped
                || stopOperation != nil
                || isTerminated
            {
                return .stopped
            }
            if Task.isCancelled {
                return .cancelled
            }
            return .publicSearchFailed
        }
    }

    private func savedSearchLockedPublicAdmissionPermitted(
        operationID: UUID
    ) -> Bool {
        lifetime == .started
            && savedSearchHandoffOperation?.id == operationID
            && !privateSessionIsActive
            && flowState().mode == .lockedPublic
            && unlockState.status == .locked
            && !isUnlockPanelPresented
            && lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && safeLifecycleCheckRevision == nil
            && !isTerminated
            && selectionOperation == nil
            && submitOperation == nil
            && privateMutationOperation == nil
            && searchOperation == nil
            && retainedSearchOperations.isEmpty
            && barrierOperation == nil
            && stopOperation == nil
            && isPublicOperationAvailable
    }

    private func containFatalPrivateMutation() async
        -> AtlasVaultPrivateMutationResult
    {
        _ = await runPrivateFreeBarrier(
            terminal: false,
            skipsPrivateSessionDrain: true
        )
        return .locked
    }

    private var barrierCompletionAdmissionPermitted: Bool {
        lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && safeLifecycleCheckRevision == nil
            && !isTerminated
            && selectionOperation == nil
            && submitOperation == nil
            && stopOperation == nil
            && shell.vaultStatus != .noVault
            && unlockStatePermitsAdmission
    }

    private var unlockStatePermitsAdmission: Bool {
        switch unlockState.status {
        case .activating, .unlocked, .hostReconciliationRequired:
            false
        case .locked,
             .ready,
             .methodUnavailable,
             .failed,
             .cancelled,
             .timedOut:
            true
        }
    }

    private func publishAndReset(
        status: AtlasVaultPresentationStatus,
        expectedGeneration: AtlasVaultProductionHostGeneration,
        ownerState: AtlasLockedShellUnlockFlowState? = nil
    ) async -> PublicationResult {
        let state = ownerState ?? flowState()
        let requiresLifecycleFence = state.publicShell.canRequestUnlock
        let publicationLifecycleRevision = lifecycleEventRevision
        let publicationSafeCheckRevision = safeLifecycleCheckRevision
        guard let publicationPermit = await acquirePublicationPermit() else {
            return .stale
        }
        defer {
            releasePublicationPermit(publicationPermit)
        }
        guard generation == expectedGeneration else {
            return .stale
        }
        guard !requiresLifecycleFence
            || publicationLifecycleFenceIsCurrent(
                revision: publicationLifecycleRevision,
                safeCheckRevision: publicationSafeCheckRevision
            ) else {
            return .stale
        }
        let raw = AtlasVaultPresentationSnapshot(
            status: status,
            privateState: nil
        )
        guard let value = try? AtlasVaultPrivateFreePresentationSnapshot(
            validating: raw
        ) else {
            return .failed
        }
        guard await dependencies.presentation.publish(value) else {
            return generation == expectedGeneration ? .failed : .stale
        }
        guard generation == expectedGeneration else {
            return .stale
        }
        guard !requiresLifecycleFence
            || publicationLifecycleFenceIsCurrent(
                revision: publicationLifecycleRevision,
                safeCheckRevision: publicationSafeCheckRevision
            ) else {
            return .stale
        }
        let ownerAcknowledged = await dependencies.presentationOwner
            .resetPresentation(
                to: state,
                generation: expectedGeneration
            )
        guard generation == expectedGeneration else {
            return .stale
        }
        guard !requiresLifecycleFence
            || publicationLifecycleFenceIsCurrent(
                revision: publicationLifecycleRevision,
                safeCheckRevision: publicationSafeCheckRevision
            ) else {
            return .stale
        }
        return ownerAcknowledged ? .acknowledged : .failed
    }

    private func publicationLifecycleFenceIsCurrent(
        revision: UInt64,
        safeCheckRevision: UInt64?
    ) -> Bool {
        lifecycleEventRevision == revision
            && self.safeLifecycleCheckRevision == safeCheckRevision
    }

    private func mapRestoredSnapshot(
        _ snapshot: AtlasProductionPublicSnapshot?
    ) {
        guard let snapshot else {
            replaceShell(
                serviceStatus: .checking,
                cacheFreshness: .unavailable,
                searchQuery: "",
                publicJobs: [],
                isSearching: false,
                canRequestUnlock: false,
                searchOrigin: .manual,
                hasAdditionalCriteria: false
            )
            return
        }
        replaceShell(
            serviceStatus: shellServiceStatus(
                for: snapshot.health.availability
            ),
            cacheFreshness: .stale,
            searchQuery: "",
            publicJobs: snapshot.jobs,
            isSearching: false,
            canRequestUnlock: false,
            searchOrigin: .manual,
            hasAdditionalCriteria: false
        )
    }

    private func shellServiceStatus(
        for availability: AtlasPublicServiceAvailability
    ) -> AtlasLockedPublicServiceStatus {
        switch availability {
        case .checking:
            .checking
        case .available:
            .available
        case .unavailable:
            .unavailable
        }
    }

    private func flowState(
        publicShell: AtlasLockedPublicShellModel? = nil
    ) -> AtlasLockedShellUnlockFlowState {
        AtlasLockedShellUnlockFlowState(
            publicShell: publicShell ?? shell,
            unlockPresentationState: unlockState,
            isUnlockPanelPresented: isUnlockPanelPresented
        )
    }

    private func shellReplacingCanRequestUnlock(
        _ canRequestUnlock: Bool
    ) -> AtlasLockedPublicShellModel {
        AtlasLockedPublicShellModel(
            vaultStatus: shell.vaultStatus,
            serviceStatus: shell.serviceStatus,
            cacheFreshness: shell.cacheFreshness,
            searchQuery: shell.searchQuery,
            publicJobs: shell.publicJobs,
            isSearching: shell.isSearching,
            canRequestUnlock: canRequestUnlock,
            searchOrigin: shell.searchOrigin,
            hasAdditionalCriteria: shell.hasAdditionalCriteria
        )
    }

    private func lockedUnlockState()
        -> AtlasVaultUnlockPresentationState
    {
        AtlasVaultUnlockPresentationState(
            capabilities: .currentProduction,
            selectedMethod: nil,
            status: .locked
        )
    }

    private func reconciliationUnlockState()
        -> AtlasVaultUnlockPresentationState
    {
        AtlasVaultUnlockPresentationState(
            capabilities: unlockState.capabilities,
            selectedMethod: nil,
            status: .hostReconciliationRequired
        )
    }

    private func advanceGeneration()
        -> AtlasVaultProductionHostGeneration
    {
        let next = AtlasVaultProductionHostGeneration()
        generation = next
        return next
    }

    private func beginTerminalStop() {
        explicitLockRequestedDuringStart = false
        lifetime = .stopping
        invalidateSafeLifecycleCheck()
        closeUnlockAdmission()
        _ = advanceGeneration()
    }

    private func failStartForPresentation() -> Result<
        AtlasLockedShellUnlockFlowState,
        AtlasVaultProductionHostError
    > {
        closeUnlockAdmission()
        if lifetime == .stopping || lifetime == .stopped {
            return .failure(.stopped)
        }
        explicitLockRequestedDuringStart = false
        lifetime = .inactive
        return .failure(.presentationUnavailable)
    }

    private func abandonSelectionAndResumeCallers() {
        selectionOperation?.task.cancel()
        selectionOperation = nil
        let abandonedState = flowState()
        resumeSelectionWaiters(with: abandonedState)
    }

    private func acquirePublicationPermit() async -> UUID? {
        guard publicationPermitID != nil else {
            let identifier = UUID()
            publicationPermitID = identifier
            return identifier
        }
        return await withCheckedContinuation { continuation in
            publicationWaiters.append(continuation)
        }
    }

    private func releasePublicationPermit(_ identifier: UUID) {
        guard publicationPermitID == identifier else {
            return
        }
        guard !publicationWaiters.isEmpty else {
            publicationPermitID = nil
            return
        }
        let next = publicationWaiters.removeFirst()
        let nextIdentifier = UUID()
        publicationPermitID = nextIdentifier
        next.resume(returning: nextIdentifier)
    }

    private func invalidatePublicationPermit() {
        publicationPermitID = nil
        let waiters = publicationWaiters
        publicationWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: nil)
        }
    }

    private func closeUnlockAdmission() {
        unlockAdmissionOpen = false
        replaceShell(canRequestUnlock: false)
    }

    private func replaceShell(
        vaultStatus: AtlasLockedPublicVaultStatus? = nil,
        serviceStatus: AtlasLockedPublicServiceStatus? = nil,
        cacheFreshness: AtlasLockedPublicCacheFreshness? = nil,
        searchQuery: String? = nil,
        publicJobs: [AtlasLockedPublicJob]? = nil,
        isSearching: Bool? = nil,
        canRequestUnlock: Bool? = nil,
        searchOrigin: AtlasPublicJobSearchOrigin? = nil,
        hasAdditionalCriteria: Bool? = nil
    ) {
        shell = AtlasLockedPublicShellModel(
            vaultStatus: vaultStatus ?? shell.vaultStatus,
            serviceStatus: serviceStatus ?? shell.serviceStatus,
            cacheFreshness: cacheFreshness ?? shell.cacheFreshness,
            searchQuery: searchQuery ?? shell.searchQuery,
            publicJobs: publicJobs ?? shell.publicJobs,
            isSearching: isSearching ?? shell.isSearching,
            canRequestUnlock:
                canRequestUnlock ?? shell.canRequestUnlock,
            searchOrigin: searchOrigin ?? shell.searchOrigin,
            hasAdditionalCriteria:
                hasAdditionalCriteria ?? shell.hasAdditionalCriteria
        )
    }

    private func updateLifecycleState(
        for event: AtlasVaultLifecycleEvent
    ) {
        switch event {
        case .didBecomeActive:
            lifecycleIsActive = true
        case .willResignActive, .didEnterBackground:
            lifecycleIsActive = false
        case .willTerminate:
            lifecycleIsActive = false
            isTerminated = true
        case .protectedDataBecameUnavailable:
            protectedDataIsAvailable = false
        case .protectedDataBecameAvailable:
            protectedDataIsAvailable = true
        }
    }

    private func clearSecret(
        in submission: AtlasVaultUnlockSubmission
    ) async {
        switch submission {
        case .localKey:
            return
        case let .passphrase(buffer), let .recoveryKey(buffer):
            await buffer.clear()
        }
    }
}

private extension AtlasVaultUnlockSubmission {
    var methodForHost: AtlasVaultUnlockMethod {
        switch self {
        case .localKey:
            .localKey
        case .passphrase:
            .passphrase
        case .recoveryKey:
            .recoveryKey
        }
    }
}

private extension AtlasVaultLifecycleEvent {
    var isSafeReopenEvent: Bool {
        switch self {
        case .didBecomeActive, .protectedDataBecameAvailable:
            true
        case .willResignActive,
             .didEnterBackground,
             .willTerminate,
             .protectedDataBecameUnavailable:
            false
        }
    }

    var closesUnlockAdmission: Bool {
        switch self {
        case .willResignActive,
             .didEnterBackground,
             .willTerminate,
             .protectedDataBecameUnavailable:
            true
        case .didBecomeActive, .protectedDataBecameAvailable:
            false
        }
    }
}
