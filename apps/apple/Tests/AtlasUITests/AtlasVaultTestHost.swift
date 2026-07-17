import Foundation
@testable import AtlasUI

enum AtlasVaultTestHostError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case notStarted
    case privateOperationsUnavailable

    var description: String {
        switch self {
        case .notStarted: "notStarted"
        case .privateOperationsUnavailable: "privateOperationsUnavailable"
        }
    }

    var debugDescription: String {
        description
    }
}

enum AtlasVaultTestEndpointCall: Equatable, Sendable {
    case publicSearch
    case savedSearchCompatibility
    case trackerCompatibility
    case privateSidebarRefresh
}

actor AtlasVaultTestEndpointCallRecorder {
    private var calls: [AtlasVaultTestEndpointCall] = []

    func record(_ call: AtlasVaultTestEndpointCall) {
        calls.append(call)
    }

    func snapshot() -> [AtlasVaultTestEndpointCall] {
        calls
    }

    func count(_ call: AtlasVaultTestEndpointCall) -> Int {
        calls.filter { $0 == call }.count
    }
}

protocol AtlasVaultTestPublicStateStoring: Sendable {
    func loadPublicStateBytes() async -> Data
    func replacePublicStateBytes(_ bytes: Data) async
}

actor AtlasVaultTestPublicStateStore: AtlasVaultTestPublicStateStoring {
    private var bytes: Data
    private var loadCount = 0
    private var replacementCount = 0

    init(bytes: Data) {
        self.bytes = bytes
    }

    func loadPublicStateBytes() -> Data {
        loadCount += 1
        return bytes
    }

    func replacePublicStateBytes(_ bytes: Data) {
        replacementCount += 1
        self.bytes = bytes
    }

    func snapshotForTesting() -> Data {
        bytes
    }

    func callCountsForTesting() -> (loads: Int, replacements: Int) {
        (loadCount, replacementCount)
    }
}

protocol AtlasVaultTestPrivateCompatibilityAccessing: Sendable {
    func loadSavedSearchCompatibility() async
    func loadTrackerCompatibility() async
    func refreshPrivateSidebar() async
}

actor AtlasVaultTestPrivateCompatibilityEndpointSpy:
    AtlasVaultTestPrivateCompatibilityAccessing
{
    private let recorder: AtlasVaultTestEndpointCallRecorder

    init(recorder: AtlasVaultTestEndpointCallRecorder) {
        self.recorder = recorder
    }

    func loadSavedSearchCompatibility() async {
        await recorder.record(.savedSearchCompatibility)
    }

    func loadTrackerCompatibility() async {
        await recorder.record(.trackerCompatibility)
    }

    func refreshPrivateSidebar() async {
        await recorder.record(.privateSidebarRefresh)
    }
}

struct AtlasVaultTestPublicJob: Equatable, Sendable {
    let identifier: String
    let title: String
}

protocol AtlasVaultTestPublicJobSearching: Sendable {
    func search(query: String) async throws -> [AtlasVaultTestPublicJob]
}

actor AtlasVaultFakePublicJobSearchService: AtlasVaultTestPublicJobSearching {
    private let recorder: AtlasVaultTestEndpointCallRecorder
    private let publicStateStore: any AtlasVaultTestPublicStateStoring
    private let results: [AtlasVaultTestPublicJob]
    private var callCount = 0
    private var nextSearchGate: AtlasVaultTestSuspensionGate?

    init(
        recorder: AtlasVaultTestEndpointCallRecorder,
        publicStateStore: any AtlasVaultTestPublicStateStoring,
        results: [AtlasVaultTestPublicJob]
    ) {
        self.recorder = recorder
        self.publicStateStore = publicStateStore
        self.results = results
    }

    func search(query: String) async throws -> [AtlasVaultTestPublicJob] {
        callCount += 1
        await recorder.record(.publicSearch)
        _ = await publicStateStore.loadPublicStateBytes()
        let gate = nextSearchGate
        nextSearchGate = nil
        if let gate {
            try await gate.wait()
        }
        try Task.checkCancellation()
        return results
    }

    func calls() -> Int {
        callCount
    }

    func setNextSearchGate(_ gate: AtlasVaultTestSuspensionGate?) {
        nextSearchGate = gate
    }
}

final class AtlasVaultTestFakeKeyStore:
    AtlasVaultKeyStore,
    @unchecked Sendable
{
    private let lock = NSLock()
    private var key: Data?
    private var loadCountValue = 0
    private var saveCountValue = 0
    private var deleteCountValue = 0

    init(key: Data?) {
        self.key = key
    }

    func loadVaultKey(for vaultID: String) throws -> Data? {
        lock.lock()
        defer { lock.unlock() }
        loadCountValue += 1
        return key
    }

    func saveVaultKey(_ key: Data, for vaultID: String) throws {
        lock.lock()
        defer { lock.unlock() }
        saveCountValue += 1
        self.key = key
    }

    func deleteVaultKey(for vaultID: String) throws {
        lock.lock()
        defer { lock.unlock() }
        deleteCountValue += 1
        key = nil
    }

    var callCounts: (load: Int, save: Int, delete: Int) {
        lock.lock()
        defer { lock.unlock() }
        return (loadCountValue, saveCountValue, deleteCountValue)
    }
}

final class AtlasVaultTestRootProvider:
    AtlasVaultRootDirectoryProviding,
    @unchecked Sendable
{
    private let lock = NSLock()
    private let rootURL: URL
    private var callCountValue = 0

    init(rootURL: URL) {
        self.rootURL = rootURL
    }

    func rootDirectory() throws -> URL {
        lock.lock()
        defer { lock.unlock() }
        callCountValue += 1
        return rootURL
    }

    var callCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return callCountValue
    }
}

struct AtlasVaultTestHostEnvironment: Sendable {
    let temporaryRootURL: URL
    let keyStore: any AtlasVaultKeyStore
    let publicStateStore: any AtlasVaultTestPublicStateStoring
    let privateCompatibilityEndpoints:
        any AtlasVaultTestPrivateCompatibilityAccessing
}

protocol AtlasVaultTestHostRuntime:
    AtlasVaultRuntimeFacading,
    AtlasVaultPrivateStateReading,
    AtlasVaultLifecycleRuntimeControlling
{}

extension AtlasVaultRuntimeFacade: AtlasVaultTestHostRuntime {}

actor AtlasVaultTestPresentationUpdateSource:
    AtlasVaultPresentationUpdateSourcing
{
    private typealias NextContinuation = CheckedContinuation<
        AtlasVaultPresentationUpdate?,
        Never
    >
    private typealias AcknowledgementContinuation = CheckedContinuation<
        Bool,
        Never
    >

    private var observationID: UUID?
    private var pendingNext: NextContinuation?
    private var bufferedUpdate: AtlasVaultPresentationUpdate?
    private var deliveredSequenceAwaitingAcknowledgement: UInt64?
    private var acknowledgedSequence: UInt64 = 0
    private var acknowledgementWaiters:
        [UInt64: [AcknowledgementContinuation]] = [:]
    private var latestSentSnapshot = AtlasVaultPresentationSnapshot(
        status: .locked,
        privateState: nil
    )
    private var nextSequence: UInt64 = 1
    private var observationStartCount = 0
    private var acceptingUpdates = true
    private var deliverySuspended = false

    func updates() -> AsyncStream<AtlasVaultPresentationUpdate> {
        observationStartCount += 1
        if observationID != nil {
            invalidateObservation()
        }
        guard acceptingUpdates else {
            return AsyncStream(unfolding: { nil })
        }
        let identifier = UUID()
        observationID = identifier
        return AsyncStream(
            unfolding: { [weak self] in
                guard let self else {
                    return nil
                }
                return await self.nextUpdate(observationID: identifier)
            },
            onCancel: { [weak self] in
                Task {
                    await self?.cancelObservation(identifier)
                }
            }
        )
    }

    func sendAndWait(
        _ snapshot: AtlasVaultPresentationSnapshot
    ) async -> Bool {
        guard acceptingUpdates else {
            return false
        }
        latestSentSnapshot = snapshot
        let update = AtlasVaultPresentationUpdate(
            sequence: nextSequence,
            snapshot: snapshot
        )
        nextSequence &+= 1
        enqueue(update)
        if update.sequence <= acknowledgedSequence {
            return true
        }
        return await withCheckedContinuation { continuation in
            acknowledgementWaiters[update.sequence, default: []]
                .append(continuation)
        }
    }

    func finish() {
        acceptingUpdates = false
        deliverySuspended = false
        bufferedUpdate = nil
        invalidateObservation()
    }

    func suspendDelivery() {
        deliverySuspended = true
    }

    func resumeDelivery() {
        deliverySuspended = false
        deliverBufferedUpdateIfPossible()
    }

    func latestSequence() -> UInt64 {
        nextSequence &- 1
    }

    func lastAcknowledgedSequence() -> UInt64 {
        acknowledgedSequence
    }

    func startCount() -> Int {
        observationStartCount
    }

    func latestSnapshot() -> AtlasVaultPresentationSnapshot {
        latestSentSnapshot
    }

    private func enqueue(_ update: AtlasVaultPresentationUpdate) {
        guard !deliverySuspended, let pendingNext else {
            bufferedUpdate = update
            return
        }
        self.pendingNext = nil
        deliveredSequenceAwaitingAcknowledgement = update.sequence
        pendingNext.resume(returning: update)
    }

    private func nextUpdate(
        observationID identifier: UUID
    ) async -> AtlasVaultPresentationUpdate? {
        guard observationID == identifier, acceptingUpdates else {
            return nil
        }
        acknowledgeDeliveredSequence()
        if !deliverySuspended, let bufferedUpdate {
            self.bufferedUpdate = nil
            deliveredSequenceAwaitingAcknowledgement = bufferedUpdate.sequence
            return bufferedUpdate
        }
        return await withCheckedContinuation { continuation in
            guard observationID == identifier,
                  acceptingUpdates,
                  pendingNext == nil else {
                continuation.resume(returning: nil)
                return
            }
            pendingNext = continuation
        }
    }

    private func deliverBufferedUpdateIfPossible() {
        guard let pendingNext, let bufferedUpdate else {
            return
        }
        self.pendingNext = nil
        self.bufferedUpdate = nil
        deliveredSequenceAwaitingAcknowledgement = bufferedUpdate.sequence
        pendingNext.resume(returning: bufferedUpdate)
    }

    private func acknowledgeDeliveredSequence() {
        guard let deliveredSequenceAwaitingAcknowledgement else {
            return
        }
        self.deliveredSequenceAwaitingAcknowledgement = nil
        if deliveredSequenceAwaitingAcknowledgement > acknowledgedSequence {
            acknowledgedSequence = deliveredSequenceAwaitingAcknowledgement
        }
        let completedSequences = acknowledgementWaiters.keys.filter {
            $0 <= acknowledgedSequence
        }
        for sequence in completedSequences {
            let continuations = acknowledgementWaiters.removeValue(
                forKey: sequence
            ) ?? []
            for continuation in continuations {
                continuation.resume(returning: true)
            }
        }
    }

    private func cancelObservation(_ identifier: UUID) {
        guard observationID == identifier else {
            return
        }
        invalidateObservation()
    }

    private func invalidateObservation() {
        observationID = nil
        deliveredSequenceAwaitingAcknowledgement = nil
        let next = pendingNext
        pendingNext = nil
        next?.resume(returning: nil)
        let waiters = acknowledgementWaiters.values.flatMap { $0 }
        acknowledgementWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: false)
        }
    }
}

actor AtlasVaultTestHost:
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private struct ActiveUnlock {
        let epoch: UInt64
        let request: AtlasVaultUnlockRequest
    }

    private let runtime: any AtlasVaultTestHostRuntime
    private let lifecycle: any AtlasVaultLifecycleCoordinating
    private let unlockCoordinator: any AtlasVaultUnlockRequestCoordinating
    private let publicSearch: any AtlasVaultTestPublicJobSearching
    private let environment: AtlasVaultTestHostEnvironment
    private let projectionAdapter = AtlasVaultPresentationAdapter()
    private var updateSource: AtlasVaultTestPresentationUpdateSource
    private var observablePresentation: AtlasVaultObservablePresentationAdapter

    private var started = false
    private var isStopping = false
    private var hostEpoch: UInt64 = 0
    private var hostObservation: AtlasVaultPresentationSubscription?
    private var stopWaiters: [CheckedContinuation<Void, Never>] = []
    private var publicSearchTasks:
        [UUID: Task<[AtlasVaultTestPublicJob], Error>] = [:]
    private var privatePresentationAllowed = false
    private var privateSessionAuthorizedByHost = false
    private var lifecycleIsActive = true
    private var protectedDataIsAvailable = true
    private var isTerminated = false
    private var unlockAdmissionAllowed = true
    private var unlockEpoch: UInt64 = 0
    private var activeUnlock: ActiveUnlock?
    private var activeMutationID: UUID?
    private var generation: AtlasVaultPresentationGeneration?
    private var privateState: AtlasVaultHydratedState?

    init(
        runtime: any AtlasVaultTestHostRuntime,
        lifecycle: any AtlasVaultLifecycleCoordinating,
        unlockCoordinator: any AtlasVaultUnlockRequestCoordinating,
        publicSearch: any AtlasVaultTestPublicJobSearching,
        environment: AtlasVaultTestHostEnvironment
    ) {
        let updateSource = AtlasVaultTestPresentationUpdateSource()
        self.runtime = runtime
        self.lifecycle = lifecycle
        self.unlockCoordinator = unlockCoordinator
        self.publicSearch = publicSearch
        self.environment = environment
        self.updateSource = updateSource
        self.observablePresentation = AtlasVaultObservablePresentationAdapter(
            source: updateSource
        )
    }

    func presentationObserver() -> any AtlasVaultPresentationObserving {
        observablePresentation
    }

    func start() async {
        guard !started, !isStopping else {
            return
        }
        started = true
        hostEpoch &+= 1
        let epoch = hostEpoch
        let observer = observablePresentation
        let subscription = await observer.subscribe()
        guard started,
              !isStopping,
              hostEpoch == epoch else {
            await subscription.cancel()
            return
        }
        hostObservation = subscription
        await synchronizePresentation()
    }

    func searchPublicJobs(
        query: String
    ) async throws -> [AtlasVaultTestPublicJob] {
        guard started, !isStopping else {
            throw AtlasVaultTestHostError.notStarted
        }
        try Task.checkCancellation()
        let identifier = UUID()
        let service = publicSearch
        let task = Task {
            try await service.search(query: query)
        }
        publicSearchTasks[identifier] = task
        defer {
            publicSearchTasks.removeValue(forKey: identifier)
        }
        return try await withTaskCancellationHandler {
            let jobs = try await task.value
            try Task.checkCancellation()
            guard started, !isStopping else {
                throw CancellationError()
            }
            return jobs
        } onCancel: {
            task.cancel()
        }
    }

    func unlock(_ request: AtlasVaultUnlockRequest) async throws {
        guard started, !isStopping else {
            throw AtlasVaultTestHostError.notStarted
        }
        let lifecycleStatus = await lifecycle.status()
        refreshUnlockAdmission(from: lifecycleStatus)
        let runtimeStatus = await runtime.status()
        guard unlockAdmissionAllowed,
              activeUnlock == nil,
              !lifecycleStatus.hasPendingGraceLock,
              runtimeStatus == .locked || runtimeStatus.isAtlasVaultTestFailure
        else {
            throw AtlasVaultTestHostError.privateOperationsUnavailable
        }

        unlockEpoch &+= 1
        let epoch = unlockEpoch
        activeUnlock = ActiveUnlock(epoch: epoch, request: request)
        closePrivatePresentation()
        await publishControlStatus(.activating)
        do {
            guard activeUnlock?.epoch == epoch,
                  unlockAdmissionAllowed else {
                throw AtlasVaultUnlockRequestError.cancelled
            }
            try await unlockCoordinator.dispatch(request)
            guard activeUnlock?.epoch == epoch,
                  unlockAdmissionAllowed else {
                throw AtlasVaultUnlockRequestError.cancelled
            }
            privateSessionAuthorizedByHost = true
            privatePresentationAllowed = true
            generation = AtlasVaultPresentationGeneration()
            guard await synchronizePresentation() else {
                throw AtlasVaultTestHostError.privateOperationsUnavailable
            }
            activeUnlock = nil
        } catch {
            guard activeUnlock?.epoch == epoch else {
                throw error
            }
            closePrivatePresentation()
            if error as? AtlasVaultUnlockRequestError == .cancelled
                || error as? AtlasVaultRuntimeFacadeError == .cancelled {
                await runtime.lock()
                guard activeUnlock?.epoch == epoch else {
                    throw error
                }
                await synchronizePresentation(commandState: .cancelled)
            } else {
                await synchronizePresentation()
            }
            if activeUnlock?.epoch == epoch {
                activeUnlock = nil
            }
            throw error
        }
    }

    func apply(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async throws -> AtlasVaultSaveOutcome {
        guard started, !isStopping else {
            throw AtlasVaultTestHostError.notStarted
        }
        guard privatePresentationAllowed,
              let admissionGeneration = generation,
              activeMutationID == nil else {
            throw AtlasVaultTestHostError.privateOperationsUnavailable
        }
        let mutationID = UUID()
        activeMutationID = mutationID
        defer {
            if activeMutationID == mutationID {
                activeMutationID = nil
            }
        }
        let runtimeStatus = await runtime.status()
        guard runtimeStatus == .unlocked,
              privatePresentationAllowed,
              generation == admissionGeneration else {
            throw AtlasVaultTestHostError.privateOperationsUnavailable
        }

        await publish(
            runtimeStatus: .saving,
            privateState: privateState,
            generation: generation,
            commandState: .none
        )
        guard privatePresentationAllowed,
              generation == admissionGeneration else {
            throw AtlasVaultTestHostError.privateOperationsUnavailable
        }

        do {
            let outcome = try await runtime.apply(request)
            guard privatePresentationAllowed,
                  generation == admissionGeneration else {
                throw AtlasVaultTestHostError.privateOperationsUnavailable
            }
            let commandState: AtlasVaultPresentationCommandState
            switch outcome {
            case .committed:
                commandState = .none
            case .committedDurabilityUnconfirmed:
                commandState = .saveDurabilityUnconfirmed
            }
            guard await refreshPrivatePresentation(
                commandState: commandState,
                expectedGeneration: admissionGeneration
            ) else {
                throw AtlasVaultTestHostError.privateOperationsUnavailable
            }
            return outcome
        } catch {
            guard privatePresentationAllowed,
                  generation == admissionGeneration else {
                throw error
            }
            let status = await runtime.status()
            guard privatePresentationAllowed,
                  generation == admissionGeneration else {
                throw error
            }
            switch status {
            case .locked, .locking, .activating, .failed:
                closePrivatePresentation()
            case .unlocked, .saving:
                break
            }
            let commandState: AtlasVaultPresentationCommandState
            switch error as? AtlasVaultRuntimeFacadeError {
            case .saveFailed:
                commandState = .saveFailed
            case .cancelled:
                commandState = status == .unlocked ? .cancelled : .none
            default:
                commandState = .none
            }
            let projectedState = commandState == .cancelled
                ? nil
                : privateState
            await publish(
                runtimeStatus: status,
                privateState: projectedState,
                generation: generation,
                commandState: commandState
            )
            throw error
        }
    }

    func lock() async {
        guard started, !isStopping else {
            return
        }
        _ = await cancelActiveUnlock()
        closePrivatePresentation()
        await publishControlStatus(.locking)
        await runtime.lock()
        await synchronizePresentation()
    }

    func handleLifecycle(_ event: AtlasVaultLifecycleEvent) async {
        guard started, !isStopping else {
            return
        }
        updateLifecycleState(for: event)
        if event.closesAtlasVaultTestHostPrivatePresentation {
            let activationMayHaveCommitted = await cancelActiveUnlock()
            closePrivatePresentation(clearSessionAuthorization: false)
            await publishControlStatus(.locking)
            if activationMayHaveCommitted {
                await runtime.lock()
            }
        }

        await lifecycle.handle(event)
        let lifecycleStatus = await lifecycle.status()
        let runtimeStatus = await runtime.status()
        refreshUnlockAdmission(from: lifecycleStatus)

        if event == .didBecomeActive,
           privateSessionAuthorizedByHost,
           unlockAdmissionAllowed,
           !lifecycleStatus.hasPendingGraceLock,
           runtimeStatus == .unlocked {
            privatePresentationAllowed = true
            generation = AtlasVaultPresentationGeneration()
            await synchronizePresentation()
            return
        }

        if lifecycleStatus.hasPendingGraceLock,
           runtimeStatus == .unlocked || runtimeStatus == .saving {
            await publishControlStatus(.locking)
            return
        }
        await synchronizePresentation()
    }

    func stop() async {
        if isStopping {
            await waitForStopCompletion()
            return
        }
        guard started else {
            return
        }

        isStopping = true
        started = false
        hostEpoch &+= 1
        unlockAdmissionAllowed = false

        let searches = Array(publicSearchTasks.values)
        publicSearchTasks.removeAll()
        for search in searches {
            search.cancel()
        }

        _ = await cancelActiveUnlock()
        closePrivatePresentation()
        await publishControlStatus(.locking)
        await runtime.lock()
        for search in searches {
            _ = await search.result
        }
        await synchronizePresentation()

        let source = updateSource
        let observation = hostObservation
        hostObservation = nil
        await source.finish()
        await observation?.cancel()
        resetPresentationPipeline()
        isStopping = false
        resumeStopWaiters()
    }

    @discardableResult
    func synchronizePresentation(
        commandState: AtlasVaultPresentationCommandState = .none
    ) async -> Bool {
        let synchronizationGeneration = generation
        let runtimeStatus = await runtime.status()
        guard generation == synchronizationGeneration else {
            return false
        }
        switch runtimeStatus {
        case .locked, .locking, .activating, .failed:
            closePrivatePresentation()
        case .unlocked:
            guard privatePresentationAllowed,
                  privateSessionAuthorizedByHost else {
                await publishControlStatus(.locking)
                return false
            }
            guard let projectionGeneration = generation else {
                await publishControlStatus(.locking)
                return false
            }
            guard let currentPrivateState =
                await readPrivateStateForPresentation(
                    expectedGeneration: projectionGeneration
                )
            else {
                return false
            }
            privateState = currentPrivateState
        case .saving:
            guard privatePresentationAllowed,
                  privateSessionAuthorizedByHost else {
                await publishControlStatus(.locking)
                return false
            }
        }

        let projectedState = commandState == .cancelled ? nil : privateState
        await publish(
            runtimeStatus: runtimeStatus,
            privateState: projectedState,
            generation: generation,
            commandState: commandState
        )
        return true
    }

    func presentationSourceStartCount() async -> Int {
        await updateSource.startCount()
    }

    func latestPublishedSnapshot() async -> AtlasVaultPresentationSnapshot {
        await updateSource.latestSnapshot()
    }

    func suspendPresentationDeliveryForTesting() async {
        await updateSource.suspendDelivery()
    }

    func resumePresentationDeliveryForTesting() async {
        await updateSource.resumeDelivery()
    }

    func latestPresentationSequenceForTesting() async -> UInt64 {
        await updateSource.latestSequence()
    }

    func acknowledgedPresentationSequenceForTesting() async -> UInt64 {
        await updateSource.lastAcknowledgedSequence()
    }

    func temporaryRootIsFileURL() -> Bool {
        environment.temporaryRootURL.isFileURL
    }

    func activePublicSearchCountForTesting() -> Int {
        publicSearchTasks.count
    }

    nonisolated var description: String {
        "AtlasVaultTestHost(state: <redacted>, dependencies: <redacted>)"
    }

    nonisolated var debugDescription: String {
        description
    }

    private func closePrivatePresentation(
        clearSessionAuthorization: Bool = true
    ) {
        privatePresentationAllowed = false
        activeMutationID = nil
        generation = nil
        privateState = nil
        if clearSessionAuthorization {
            privateSessionAuthorizedByHost = false
        }
    }

    private func cancelActiveUnlock() async -> Bool {
        guard let activeUnlock else {
            return false
        }
        unlockEpoch &+= 1
        self.activeUnlock = nil
        let cancellationWon =
            await unlockCoordinator.cancel(activeUnlock.request)
        return !cancellationWon
    }

    private func updateLifecycleState(for event: AtlasVaultLifecycleEvent) {
        switch event {
        case .didBecomeActive:
            lifecycleIsActive = true
        case .willResignActive, .didEnterBackground:
            lifecycleIsActive = false
            unlockAdmissionAllowed = false
        case .willTerminate:
            lifecycleIsActive = false
            isTerminated = true
            unlockAdmissionAllowed = false
        case .protectedDataBecameUnavailable:
            protectedDataIsAvailable = false
            unlockAdmissionAllowed = false
        case .protectedDataBecameAvailable:
            protectedDataIsAvailable = true
        }
    }

    private func refreshUnlockAdmission(
        from lifecycleStatus: AtlasVaultLifecycleStatus
    ) {
        unlockAdmissionAllowed =
            !isTerminated
            && lifecycleIsActive
            && protectedDataIsAvailable
            && !lifecycleStatus.hasPendingGraceLock
    }

    private func publishControlStatus(
        _ status: AtlasVaultRuntimeStatus
    ) async {
        await publish(
            runtimeStatus: status,
            privateState: nil,
            generation: nil,
            commandState: .none
        )
    }

    private func refreshPrivatePresentation(
        commandState: AtlasVaultPresentationCommandState,
        expectedGeneration: AtlasVaultPresentationGeneration
    ) async -> Bool {
        guard privatePresentationAllowed,
              generation == expectedGeneration else {
            return false
        }
        let runtimeStatus = await runtime.status()
        guard runtimeStatus == .unlocked,
              privatePresentationAllowed,
              privateSessionAuthorizedByHost,
              generation == expectedGeneration else {
            return false
        }
        guard let currentPrivateState =
            await readPrivateStateForPresentation(
                expectedGeneration: expectedGeneration
            )
        else {
            return false
        }
        privateState = currentPrivateState
        await publish(
            runtimeStatus: runtimeStatus,
            privateState: currentPrivateState,
            generation: expectedGeneration,
            commandState: commandState
        )
        return privatePresentationAllowed
            && privateSessionAuthorizedByHost
            && generation == expectedGeneration
    }

    private func readPrivateStateForPresentation(
        expectedGeneration: AtlasVaultPresentationGeneration
    ) async -> AtlasVaultHydratedState? {
        do {
            let currentPrivateState = try await runtime.privateState().state
            guard privatePresentationAllowed,
                  privateSessionAuthorizedByHost,
                  generation == expectedGeneration else {
                return nil
            }
            return currentPrivateState
        } catch {
            guard privatePresentationAllowed,
                  privateSessionAuthorizedByHost,
                  generation == expectedGeneration else {
                return nil
            }
            await containPrivateStateReadFailure(
                expectedGeneration: expectedGeneration
            )
            return nil
        }
    }

    private func containPrivateStateReadFailure(
        expectedGeneration: AtlasVaultPresentationGeneration
    ) async {
        guard privatePresentationAllowed,
              privateSessionAuthorizedByHost,
              generation == expectedGeneration else {
            return
        }
        closePrivatePresentation()
        await publishControlStatus(.locking)
        await runtime.lock()
        await synchronizePresentation()
    }

    private func publish(
        runtimeStatus: AtlasVaultRuntimeStatus,
        privateState: AtlasVaultHydratedState?,
        generation: AtlasVaultPresentationGeneration?,
        commandState: AtlasVaultPresentationCommandState
    ) async {
        let snapshot = projectionAdapter.makeSnapshot(
            runtimeStatus: runtimeStatus,
            privateState: privateState,
            generation: generation,
            commandState: commandState
        )
        let source = updateSource
        _ = await source.sendAndWait(snapshot)
    }

    private func waitForStopCompletion() async {
        await withCheckedContinuation { continuation in
            stopWaiters.append(continuation)
        }
    }

    private func resumeStopWaiters() {
        let waiters = stopWaiters
        stopWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
    }

    private func resetPresentationPipeline() {
        let source = AtlasVaultTestPresentationUpdateSource()
        updateSource = source
        observablePresentation = AtlasVaultObservablePresentationAdapter(
            source: source
        )
    }
}

enum AtlasVaultScriptedActivationBehavior: Sendable {
    case succeed(AtlasVaultHydratedState)
    case fail(AtlasVaultActivationFailure)
}

enum AtlasVaultScriptedSaveBehavior: Sendable {
    case committed(AtlasVaultHydratedState)
    case committedDurabilityUnconfirmed(AtlasVaultHydratedState)
    case recoverableFailure(AtlasVaultScriptedRecoverableSaveFailure)
    case fatalFailure
    case committedStateUnavailable
    case cancelled
}

enum AtlasVaultScriptedRecoverableSaveFailure: Sendable {
    case atomicWrite
    case staleRevision
}

actor AtlasVaultScriptedTestRuntime: AtlasVaultTestHostRuntime {
    private var runtimeStatus: AtlasVaultRuntimeStatus = .locked
    private var installedState: AtlasVaultHydratedState?
    private var activeVaultID: String?
    private var activationBehavior: AtlasVaultScriptedActivationBehavior
    private var saveBehavior: AtlasVaultScriptedSaveBehavior =
        .recoverableFailure(.atomicWrite)
    private var activationGate: AtlasVaultTestSuspensionGate?
    private var saveGate: AtlasVaultTestSuspensionGate?
    private var nextStaleSaveCompletionGate: AtlasVaultTestSuspensionGate?
    private var nextStatusGate: AtlasVaultTestSuspensionGate?
    private var nextPrivateStateGate: AtlasVaultTestSuspensionGate?
    private var failNextPrivateStateRead = false
    private var operationEpoch: UInt64 = 0
    private var recordedEvents: [String] = []

    init(activationState: AtlasVaultHydratedState) {
        self.activationBehavior = .succeed(activationState)
    }

    func status() async -> AtlasVaultRuntimeStatus {
        recordedEvents.append("status")
        let gate = nextStatusGate
        nextStatusGate = nil
        if let gate {
            try? await gate.wait()
        }
        return runtimeStatus
    }

    func activate(_ request: AtlasVaultRuntimeActivationRequest) async throws {
        guard runtimeStatus == .locked || runtimeStatus.isAtlasVaultTestFailure else {
            throw AtlasVaultRuntimeFacadeError.operationInProgress
        }
        operationEpoch &+= 1
        let epoch = operationEpoch
        runtimeStatus = .activating
        recordedEvents.append("activate")
        let gate = activationGate

        do {
            if let gate {
                try await gate.wait()
            }
            try Task.checkCancellation()
        } catch {
            if operationEpoch == epoch {
                installedState = nil
                activeVaultID = nil
                runtimeStatus = .locked
            }
            throw AtlasVaultRuntimeFacadeError.cancelled
        }

        guard operationEpoch == epoch,
              runtimeStatus == .activating else {
            throw AtlasVaultRuntimeFacadeError.cancelled
        }
        switch activationBehavior {
        case let .succeed(state):
            installedState = state
            activeVaultID = request.vaultID
            runtimeStatus = .unlocked
        case let .fail(failure):
            installedState = nil
            activeVaultID = nil
            runtimeStatus = .failed(.activation(failure))
            throw AtlasVaultRuntimeFacadeError.activationFailed(failure)
        }
    }

    func lock() async {
        operationEpoch &+= 1
        runtimeStatus = .locking
        recordedEvents.append("lock")
        await activationGate?.open()
        await saveGate?.open()
        installedState = nil
        activeVaultID = nil
        runtimeStatus = .locked
    }

    func cancelActivationIfInProgress() async -> Bool {
        recordedEvents.append("cancelActivation")
        guard runtimeStatus == .activating else {
            return false
        }
        operationEpoch &+= 1
        installedState = nil
        activeVaultID = nil
        runtimeStatus = .locked
        await activationGate?.open()
        return true
    }

    func privateState() async throws -> AtlasVaultPrivateStateSnapshot {
        recordedEvents.append("privateState")
        let gate = nextPrivateStateGate
        nextPrivateStateGate = nil
        if let gate {
            try? await gate.wait()
        }
        if failNextPrivateStateRead {
            failNextPrivateStateRead = false
            throw AtlasVaultRuntimeFacadeError.privateStateUnavailable
        }
        guard runtimeStatus == .unlocked,
              let installedState else {
            throw AtlasVaultRuntimeFacadeError.privateStateUnavailable
        }
        return AtlasVaultPrivateStateSnapshot(state: installedState)
    }

    func apply(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async throws -> AtlasVaultSaveOutcome {
        guard runtimeStatus == .unlocked else {
            throw AtlasVaultRuntimeFacadeError.locked
        }
        recordedEvents.append("apply")
        guard request.expectedVaultID == activeVaultID else {
            throw AtlasVaultRuntimeFacadeError.sessionMismatch
        }
        operationEpoch &+= 1
        let epoch = operationEpoch
        runtimeStatus = .saving
        let gate = saveGate
        let staleCompletionGate = nextStaleSaveCompletionGate
        nextStaleSaveCompletionGate = nil

        do {
            if let gate {
                try await gate.wait()
            }
            try Task.checkCancellation()
        } catch {
            if operationEpoch == epoch {
                runtimeStatus = .unlocked
            }
            throw AtlasVaultRuntimeFacadeError.cancelled
        }
        guard operationEpoch == epoch,
              runtimeStatus == .saving else {
            if let staleCompletionGate {
                try? await staleCompletionGate.wait()
            }
            throw AtlasVaultRuntimeFacadeError.cancelled
        }

        switch saveBehavior {
        case let .committed(state):
            installedState = state
            runtimeStatus = .unlocked
            return .committed
        case let .committedDurabilityUnconfirmed(state):
            installedState = state
            runtimeStatus = .unlocked
            return .committedDurabilityUnconfirmed
        case .recoverableFailure:
            runtimeStatus = .unlocked
            throw AtlasVaultRuntimeFacadeError.saveFailed
        case .fatalFailure:
            installedState = nil
            activeVaultID = nil
            runtimeStatus = .locked
            throw AtlasVaultRuntimeFacadeError.saveIntegrityUnknown
        case .committedStateUnavailable:
            installedState = nil
            activeVaultID = nil
            runtimeStatus = .locked
            throw AtlasVaultRuntimeFacadeError.committedStateUnavailable(
                .committed
            )
        case .cancelled:
            runtimeStatus = .unlocked
            throw AtlasVaultRuntimeFacadeError.cancelled
        }
    }

    func setActivationBehavior(
        _ behavior: AtlasVaultScriptedActivationBehavior
    ) {
        activationBehavior = behavior
    }

    func setSaveBehavior(_ behavior: AtlasVaultScriptedSaveBehavior) {
        saveBehavior = behavior
    }

    func setActivationGate(_ gate: AtlasVaultTestSuspensionGate?) {
        activationGate = gate
    }

    func setSaveGate(_ gate: AtlasVaultTestSuspensionGate?) {
        saveGate = gate
    }

    func setNextStaleSaveCompletionGate(
        _ gate: AtlasVaultTestSuspensionGate?
    ) {
        nextStaleSaveCompletionGate = gate
    }

    func setNextStatusGate(_ gate: AtlasVaultTestSuspensionGate?) {
        nextStatusGate = gate
    }

    func setNextPrivateStateGate(_ gate: AtlasVaultTestSuspensionGate?) {
        nextPrivateStateGate = gate
    }

    func setFailNextPrivateStateRead(_ shouldFail: Bool) {
        failNextPrivateStateRead = shouldFail
    }

    func events() -> [String] {
        recordedEvents
    }

    func applyCallCount() -> Int {
        recordedEvents.filter { $0 == "apply" }.count
    }
}

actor AtlasVaultTestSuspensionGate {
    private var entered = false
    private var isOpen = false
    private var isCancelled = false
    private var continuation: CheckedContinuation<Void, Error>?

    func wait() async throws {
        entered = true
        guard !isOpen else {
            return
        }
        if isCancelled {
            throw CancellationError()
        }

        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                if isOpen {
                    continuation.resume()
                } else if isCancelled || Task.isCancelled {
                    continuation.resume(throwing: CancellationError())
                } else {
                    precondition(
                        self.continuation == nil,
                        "AtlasVaultTestSuspensionGate supports one waiter"
                    )
                    self.continuation = continuation
                }
            }
        } onCancel: {
            Task {
                await self.cancel()
            }
        }
    }

    func waitUntilEntered() async -> Bool {
        for _ in 0..<2_000 {
            if entered {
                return true
            }
            await Task.yield()
        }
        return entered
    }

    func open() {
        guard !isOpen, !isCancelled else {
            return
        }
        isOpen = true
        continuation?.resume()
        continuation = nil
    }

    func cancel() {
        guard !isOpen, !isCancelled else {
            return
        }
        isCancelled = true
        continuation?.resume(throwing: CancellationError())
        continuation = nil
    }
}

actor AtlasVaultTestManualTime:
    AtlasVaultLifecycleClock,
    AtlasVaultLifecycleSleeper
{
    private struct Waiter {
        let deadline: Duration
        let continuation: CheckedContinuation<Void, Error>
    }

    private var current: Duration = .zero
    private var waiters: [UUID: Waiter] = [:]
    private var nextNowGate: AtlasVaultTestSuspensionGate?

    func now() async -> Duration {
        let gate = nextNowGate
        nextNowGate = nil
        if let gate {
            try? await gate.wait()
        }
        return current
    }

    func sleep(until deadline: Duration) async throws {
        guard deadline > current else {
            return
        }
        let identifier = UUID()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                waiters[identifier] = Waiter(
                    deadline: deadline,
                    continuation: continuation
                )
                if Task.isCancelled {
                    cancel(identifier)
                }
            }
        } onCancel: {
            Task {
                await self.cancel(identifier)
            }
        }
    }

    func advance(by duration: Duration) {
        current += duration
        let ready = waiters.filter { $0.value.deadline <= current }
        for (identifier, waiter) in ready {
            waiters.removeValue(forKey: identifier)
            waiter.continuation.resume()
        }
    }

    func setNextNowGate(_ gate: AtlasVaultTestSuspensionGate?) {
        nextNowGate = gate
    }

    private func cancel(_ identifier: UUID) {
        guard let waiter = waiters.removeValue(forKey: identifier) else {
            return
        }
        waiter.continuation.resume(throwing: CancellationError())
    }
}

private extension AtlasVaultLifecycleEvent {
    var closesAtlasVaultTestHostPrivatePresentation: Bool {
        switch self {
        case .willResignActive,
             .didEnterBackground,
             .willTerminate,
             .protectedDataBecameUnavailable:
            true
        case .didBecomeActive,
             .protectedDataBecameAvailable:
            false
        }
    }
}

private extension AtlasVaultRuntimeStatus {
    var isAtlasVaultTestFailure: Bool {
        if case .failed = self {
            return true
        }
        return false
    }
}
