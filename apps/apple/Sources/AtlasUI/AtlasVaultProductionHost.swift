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

    private struct BarrierOperation {
        let id: UUID
        let terminal: Bool
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
    private var isTerminated = false
    private var restoreAttempted = false
    private var presentationWasStarted = false
    private var selectedVaultID: AtlasSelectedVaultID?
    private var unlockController:
        (any AtlasVaultUnlockPresentationControlling)?
    private var startOperation: StartOperation?
    private var searchOperation: SearchOperation?
    private var selectionOperation: SelectionOperation?
    private var selectionWaiters: [CheckedContinuation<
        AtlasLockedShellUnlockFlowState,
        Never
    >] = []
    private var publicationInProgress = false
    private var publicationWaiters: [CheckedContinuation<Void, Never>] = []
    private var submitOperation: SubmitOperation?
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
            closeUnlockAdmission()
            lifetime = .stopped
            return flowState()
        }

        lifetime = .stopping
        closeUnlockAdmission()
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

    public func searchPublicJobs(
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
        searchOperation = SearchOperation(
            id: id,
            generation: operationGeneration,
            task: task
        )
        replaceShell(
            serviceStatus: .checking,
            searchQuery: request.query,
            isSearching: true
        )
        _ = await publishCurrentFlow(status: presentationStatus)

        let result = await task.value
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
                publicJobs: value.jobs,
                isSearching: false
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
                isSearching: false
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
        guard isUnlockOperationAvailable else {
            return flowState()
        }
        if let unlockController {
            closeUnlockAdmission()
            let operationGeneration = advanceGeneration()
            let current = await unlockController.currentState()
            guard generation == operationGeneration,
                  lifetime == .started else {
                return flowState()
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
        selectionOperation = SelectionOperation(
            id: id,
            generation: selectionGeneration,
            task: task
        )
        let result = await task.value
        guard let operation = selectionOperation,
              operation.id == id else {
            return flowState()
        }
        return await finishSelection(operation, result: result)
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
            capabilities: .currentProduction,
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
        unlockState = result
        return await finishSubmit(result)
    }

    public func cancelUnlock() async
        -> AtlasLockedShellUnlockFlowState
    {
        guard isPublicOperationAvailable,
              let unlockController else {
            return flowState()
        }
        if submitOperation == nil {
            closeUnlockAdmission()
            let operationGeneration = advanceGeneration()
            let cancelled = await unlockController.cancel()
            guard generation == operationGeneration,
                  lifetime == .started else {
                return flowState()
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
        if cancelledState.status == .hostReconciliationRequired
            || terminalState?.status == .hostReconciliationRequired
            || terminalState?.status == .unlocked
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
        guard isPublicOperationAvailable,
              let unlockController else {
            return flowState()
        }
        if submitOperation == nil {
            closeUnlockAdmission()
            let operationGeneration = advanceGeneration()
            let disappeared = await unlockController.didDisappear()
            guard generation == operationGeneration,
                  lifetime == .started else {
                return flowState()
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
        if disappearedState.status == .hostReconciliationRequired
            || terminalState?.status == .hostReconciliationRequired
            || terminalState?.status == .unlocked
        {
            return await runPrivateFreeBarrier(terminal: false)
        }
        unlockState = terminalState ?? disappearedState
        isUnlockPanelPresented = false
        return await finishOrdinaryUnlockFailure()
    }

    public func lock() async -> AtlasLockedShellUnlockFlowState {
        guard lifetime != .stopped, lifetime != .stopping else {
            return flowState()
        }
        guard lifetime != .inactive else {
            return flowState()
        }
        if lifetime == .started,
           submitOperation == nil,
           selectedVaultID == nil,
           unlockState.status == .locked,
           !isUnlockPanelPresented {
            if await dependencies.runtime.status() == .locked {
                return flowState()
            }
        }
        return await runPrivateFreeBarrier(terminal: false)
    }

    public func handleLifecycleEvent(
        _ event: AtlasVaultLifecycleEvent
    ) async -> AtlasLockedShellUnlockFlowState {
        guard lifetime != .stopped, lifetime != .stopping else {
            return flowState()
        }
        updateLifecycleState(for: event)
        if event.closesUnlockAdmission {
            lifecycleAdmissionPermitted = false
            closeUnlockAdmission()
            _ = advanceGeneration()
        }

        await dependencies.lifecycle.handle(event)

        switch event {
        case .didBecomeActive, .protectedDataBecameAvailable:
            guard lifetime != .inactive,
                  lifetime != .starting,
                  isPublicOperationAvailable else {
                return flowState()
            }
            let lifecycleStatus = await dependencies.lifecycle.status()
            let runtimeStatus = await dependencies.runtime.status()
            let mayReopen = lifetime == .started
                && lifecycleIsActive
                && protectedDataIsAvailable
                && runtimeStatus == .locked
                && !lifecycleStatus.hasPendingGraceLock
            lifecycleAdmissionPermitted = mayReopen
            guard mayReopen else {
                closeUnlockAdmission()
                return flowState()
            }
            let publication = await publishCurrentFlowAndReopenAdmission(
                status: presentationStatus
            )
            if case .failed = publication {
                return await runPrivateFreeBarrier(terminal: false)
            }
            return flowState()
        case .willResignActive:
            guard lifetime != .inactive,
                  lifetime != .starting,
                  isPublicOperationAvailable else {
                return flowState()
            }
            if submitOperation != nil {
                return await cancelUnlock()
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

    private var isPublicOperationAvailable: Bool {
        lifetime == .started || lifetime == .reconciling
    }

    private var isUnlockOperationAvailable: Bool {
        lifetime == .started
            && unlockAdmissionOpen
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
            lifetime = .inactive
            closeUnlockAdmission()
            return .failure(.presentationUnavailable)
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
            lifetime = .inactive
            closeUnlockAdmission()
            return .failure(.presentationUnavailable)
        }

        let mayOpen = lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && !isTerminated
        let startedShell = shellReplacingCanRequestUnlock(mayOpen)
        let startedState = flowState(publicShell: startedShell)
        let startedGeneration = advanceGeneration()
        let finalPublication = await publishAndReset(
            status: .locked,
            expectedGeneration: startedGeneration,
            ownerState: startedState
        )
        guard case .acknowledged = finalPublication,
              lifetime == .starting,
              generation == startedGeneration else {
            lifetime = .inactive
            closeUnlockAdmission()
            return .failure(.presentationUnavailable)
        }
        lifetime = .started
        shell = startedShell
        unlockAdmissionOpen = mayOpen
        return .success(flowState())
    }

    private func performStop() async -> AtlasLockedShellUnlockFlowState {
        searchGeneration &+= 1
        let search = searchOperation
        searchOperation = nil
        search?.task.cancel()
        selectionOperation?.task.cancel()
        if let search {
            _ = await search.task.value
        }
        replaceShell(isSearching: false)
        let state = await runPrivateFreeBarrier(terminal: true)
        lifetime = .stopped
        closeUnlockAdmission()
        return state
    }

    private func finishSubmit(
        _ result: AtlasVaultUnlockPresentationState
    ) async -> AtlasLockedShellUnlockFlowState {
        switch result.status {
        case .unlocked:
            let successGeneration = generation
            let runtimeStatus = await dependencies.runtime.status()
            guard generation == successGeneration,
                  lifetime == .started,
                  runtimeStatus == .unlocked else {
                return await runPrivateFreeBarrier(terminal: false)
            }
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
            let controller = dependencies.unlockControllerBuilder
                .makeController(
                    selectedVaultID: value,
                    capabilities: .currentProduction,
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
        case .failure:
            replaceShell(
                vaultStatus: .keyUnavailable,
                canRequestUnlock: false
            )
            unlockState = lockedUnlockState()
            isUnlockPanelPresented = false
        }

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
        selectionOperation = nil
        resumeSelectionWaiters(with: state)
        return state
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

    private func finishOrdinaryUnlockFailure() async
        -> AtlasLockedShellUnlockFlowState
    {
        let status = await dependencies.runtime.status()
        switch status {
        case .locked:
            break
        case .failed:
            await dependencies.runtime.lock()
            guard await dependencies.runtime.status() == .locked else {
                return await runPrivateFreeBarrier(terminal: false)
            }
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
        terminal: Bool
    ) async -> AtlasLockedShellUnlockFlowState {
        if let barrierOperation {
            let result = await barrierOperation.task.value
            if terminal && !barrierOperation.terminal {
                if self.barrierOperation?.id == barrierOperation.id {
                    self.barrierOperation = nil
                }
                return await runPrivateFreeBarrier(terminal: true)
            }
            return result
        }

        lifetime = terminal ? .stopping : .reconciling
        closeUnlockAdmission()
        let id = UUID()
        let task = Task { [self] in
            await performPrivateFreeBarrier(terminal: terminal)
        }
        barrierOperation = BarrierOperation(
            id: id,
            terminal: terminal,
            task: task
        )
        let result = await task.value
        if barrierOperation?.id == id {
            barrierOperation = nil
        }
        return result
    }

    private func performPrivateFreeBarrier(
        terminal: Bool
    ) async -> AtlasLockedShellUnlockFlowState {
        selectionOperation?.task.cancel()
        selectionOperation = nil
        defer {
            resumeSelectionWaiters(with: flowState())
        }
        let preservesNoVault = shell.vaultStatus == .noVault
            && selectedVaultID == nil
        let lockedPresentationStatus: AtlasVaultPresentationStatus =
            preservesNoVault ? .noVault : .locked
        closeUnlockAdmission()
        _ = advanceGeneration()
        unlockState = AtlasVaultUnlockPresentationState(
            capabilities: .currentProduction,
            selectedMethod: nil,
            status: .hostReconciliationRequired
        )
        isUnlockPanelPresented = true
        let reconciliationGeneration = generation
        let reconciliationPublication = await publishAndReset(
            status: .locking,
            expectedGeneration: reconciliationGeneration
        )
        var barrierSucceeded: Bool
        if case .acknowledged = reconciliationPublication {
            barrierSucceeded = true
        } else {
            barrierSucceeded = false
        }

        let active = submitOperation
        if active != nil, let unlockController {
            _ = await unlockController.cancel()
        }
        if let active {
            _ = await active.task.value
            if submitOperation?.id == active.id {
                submitOperation = nil
            }
        }

        let initialRuntimeStatus = await dependencies.runtime.status()
        if terminal || initialRuntimeStatus != .locked {
            await dependencies.runtime.lock()
        }
        if let unlockController {
            _ = await unlockController.hostDidLock()
        }

        let lockedGeneration = advanceGeneration()
        unlockState = lockedUnlockState()
        isUnlockPanelPresented = false
        replaceShell(
            vaultStatus: preservesNoVault ? .noVault : .locked,
            canRequestUnlock: false
        )
        let lockedPublication = await publishAndReset(
            status: lockedPresentationStatus,
            expectedGeneration: lockedGeneration
        )
        if case .acknowledged = lockedPublication {
        } else {
            barrierSucceeded = false
        }

        let observable = await dependencies.presentation.currentSnapshot()
        if observable.privateState != nil {
            barrierSucceeded = false
        }
        if await dependencies.runtime.status() != .locked {
            barrierSucceeded = false
        }

        if !terminal, lifetime == .stopping {
            closeUnlockAdmission()
            return flowState()
        }

        if barrierSucceeded && !terminal {
            let mayOpen = lifecycleIsActive
                && protectedDataIsAvailable
                && lifecycleAdmissionPermitted
                && !isTerminated
                && shell.vaultStatus != .noVault
            let ordinaryShell = shellReplacingCanRequestUnlock(mayOpen)
            let ordinaryState = flowState(publicShell: ordinaryShell)
            let ordinaryGeneration = advanceGeneration()
            let ordinaryPublication = await publishAndReset(
                status: lockedPresentationStatus,
                expectedGeneration: ordinaryGeneration,
                ownerState: ordinaryState
            )
            if case .acknowledged = ordinaryPublication {
                selectedVaultID = nil
                unlockController = nil
                lifetime = .started
                unlockAdmissionOpen = mayOpen
                shell = ordinaryShell
                return flowState()
            }
            barrierSucceeded = false
        }

        if terminal {
            let finished = await dependencies.presentation.finish()
            barrierSucceeded = barrierSucceeded && finished
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
        let mayOpen = lifecycleIsActive
            && protectedDataIsAvailable
            && lifecycleAdmissionPermitted
            && !isTerminated
            && shell.vaultStatus != .noVault
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
        shell = readyShell
        unlockAdmissionOpen = mayOpen
        return .acknowledged
    }

    private func publishAndReset(
        status: AtlasVaultPresentationStatus,
        expectedGeneration: AtlasVaultProductionHostGeneration,
        ownerState: AtlasLockedShellUnlockFlowState? = nil
    ) async -> PublicationResult {
        await acquirePublicationPermit()
        defer {
            releasePublicationPermit()
        }
        guard generation == expectedGeneration else {
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
        let state = ownerState ?? flowState()
        guard await dependencies.presentationOwner.resetPresentation(
            to: state,
            generation: expectedGeneration
        ) else {
            return generation == expectedGeneration ? .failed : .stale
        }
        return generation == expectedGeneration
            ? .acknowledged
            : .stale
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
                canRequestUnlock: false
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
            canRequestUnlock: false
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
            canRequestUnlock: canRequestUnlock
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
            capabilities: .currentProduction,
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

    private func acquirePublicationPermit() async {
        guard publicationInProgress else {
            publicationInProgress = true
            return
        }
        await withCheckedContinuation { continuation in
            publicationWaiters.append(continuation)
        }
    }

    private func releasePublicationPermit() {
        guard !publicationWaiters.isEmpty else {
            publicationInProgress = false
            return
        }
        let next = publicationWaiters.removeFirst()
        next.resume()
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
        canRequestUnlock: Bool? = nil
    ) {
        shell = AtlasLockedPublicShellModel(
            vaultStatus: vaultStatus ?? shell.vaultStatus,
            serviceStatus: serviceStatus ?? shell.serviceStatus,
            cacheFreshness: cacheFreshness ?? shell.cacheFreshness,
            searchQuery: searchQuery ?? shell.searchQuery,
            publicJobs: publicJobs ?? shell.publicJobs,
            isSearching: isSearching ?? shell.isSearching,
            canRequestUnlock:
                canRequestUnlock ?? shell.canRequestUnlock
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
