// Phase 2D-56 repository boundary.
import Foundation

private actor AtlasVaultProductionPresentationUpdateSource:
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
    private var sequenceWaiters:
        [UInt64: [CheckedContinuation<Void, Never>]] = [:]
    private var observationStartWaiters:
        [CheckedContinuation<Void, Never>] = []
    private var activationWaiters:
        [CheckedContinuation<Bool, Never>] = []
    private var nextSequence: UInt64 = 1
    private var observationStartCount = 0
    private var acceptingUpdates = true
    private var activated = false
    private var deliverySuspended = false

    func updates() async -> AsyncStream<AtlasVaultPresentationUpdate> {
        guard await waitUntilActivated() else {
            return AsyncStream(unfolding: { nil })
        }
        observationStartCount += 1
        let startWaiters = observationStartWaiters
        observationStartWaiters.removeAll()
        for waiter in startWaiters {
            waiter.resume()
        }
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

    func activate() {
        guard acceptingUpdates, !activated else {
            return
        }
        activated = true
        let waiters = activationWaiters
        activationWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: true)
        }
    }

    func sendAndWait(
        _ snapshot: AtlasVaultPresentationSnapshot
    ) async -> Bool {
        guard acceptingUpdates, nextSequence != UInt64.max else {
            return false
        }
        let update = AtlasVaultPresentationUpdate(
            sequence: nextSequence,
            snapshot: snapshot
        )
        nextSequence += 1
        resumeSequenceWaiters(through: update.sequence)
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
        let activationWaiters = activationWaiters
        self.activationWaiters.removeAll()
        for waiter in activationWaiters {
            waiter.resume(returning: false)
        }
        resumeTerminalWaiters()
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
        nextSequence - 1
    }

    func lastAcknowledgedSequence() -> UInt64 {
        acknowledgedSequence
    }

    func startCount() -> Int {
        observationStartCount
    }

    func waitUntilObservationStarted() async {
        guard observationStartCount == 0, acceptingUpdates else {
            return
        }
        await withCheckedContinuation { continuation in
            observationStartWaiters.append(continuation)
        }
    }

    func waitUntilSequence(_ sequence: UInt64) async {
        guard acceptingUpdates, latestSequence() < sequence else {
            return
        }
        await withCheckedContinuation { continuation in
            sequenceWaiters[sequence, default: []].append(continuation)
        }
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

    private func waitUntilActivated() async -> Bool {
        if activated {
            return acceptingUpdates
        }
        guard acceptingUpdates else {
            return false
        }
        return await withCheckedContinuation { continuation in
            activationWaiters.append(continuation)
        }
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
        acknowledgedSequence = max(
            acknowledgedSequence,
            deliveredSequenceAwaitingAcknowledgement
        )
        let completed = acknowledgementWaiters.keys.filter {
            $0 <= acknowledgedSequence
        }
        for sequence in completed {
            let waiters = acknowledgementWaiters.removeValue(
                forKey: sequence
            ) ?? []
            for waiter in waiters {
                waiter.resume(returning: true)
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

    private func resumeSequenceWaiters(through sequence: UInt64) {
        let completed = sequenceWaiters.keys.filter { $0 <= sequence }
        for target in completed {
            let waiters = sequenceWaiters.removeValue(forKey: target) ?? []
            for waiter in waiters {
                waiter.resume()
            }
        }
    }

    private func resumeTerminalWaiters() {
        let observationWaiters = observationStartWaiters
        observationStartWaiters.removeAll()
        for waiter in observationWaiters {
            waiter.resume()
        }

        let terminalSequenceWaiters = sequenceWaiters.values.flatMap { $0 }
        sequenceWaiters.removeAll()
        for waiter in terminalSequenceWaiters {
            waiter.resume()
        }
    }
}

public actor AtlasVaultProductionPresentationPipeline:
    AtlasVaultProductionPresentationCoordinating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum State {
        case inactive
        case starting
        case active
        case finishing
        case finished
    }

    private let source: AtlasVaultProductionPresentationUpdateSource
    private let observable: AtlasVaultObservablePresentationAdapter
    private var state: State = .inactive
    private var anchorSubscription: AtlasVaultPresentationSubscription?
    private var anchorTask: Task<Void, Never>?
    private var startWaiters: [CheckedContinuation<Bool, Never>] = []
    private var finishWaiters: [CheckedContinuation<Bool, Never>] = []

    public init() {
        let source = AtlasVaultProductionPresentationUpdateSource()
        self.source = source
        self.observable = AtlasVaultObservablePresentationAdapter(
            source: source
        )
    }

    public func start() async -> Bool {
        switch state {
        case .active:
            return true
        case .starting:
            return await withCheckedContinuation { continuation in
                startWaiters.append(continuation)
            }
        case .finishing, .finished:
            return false
        case .inactive:
            break
        }

        state = .starting
        await source.activate()
        let subscription = await observable.subscribe()
        anchorSubscription = subscription
        anchorTask = Task {
            for await _ in subscription.snapshots {}
        }
        await source.waitUntilObservationStarted()
        state = .active
        resumeStartWaiters(with: true)
        return true
    }

    public func publish(
        _ value: AtlasVaultPrivateFreePresentationSnapshot
    ) async -> Bool {
        guard case .active = state else {
            return false
        }
        guard await source.sendAndWait(value.snapshot) else {
            return false
        }
        guard case .active = state else {
            return false
        }
        let current = await observable.currentSnapshot()
        return current == value.snapshot && current.privateState == nil
    }

    public func subscribe() async -> AtlasVaultPresentationSubscription {
        await observable.subscribe()
    }

    public func currentSnapshot() async -> AtlasVaultPresentationSnapshot {
        await observable.currentSnapshot()
    }

    public func finish() async -> Bool {
        switch state {
        case .finished:
            return true
        case .starting:
            guard await withCheckedContinuation({ continuation in
                startWaiters.append(continuation)
            }) else {
                return false
            }
            return await finish()
        case .finishing:
            return await withCheckedContinuation { continuation in
                finishWaiters.append(continuation)
            }
        case .inactive:
            state = .finished
            await source.finish()
            return false
        case .active:
            state = .finishing
        }

        let locked = AtlasVaultPresentationSnapshot(
            status: .locked,
            privateState: nil
        )
        await source.finish()
        await anchorTask?.value
        let current = await observable.currentSnapshot()
        let verified = current == locked && current.privateState == nil
        await anchorSubscription?.cancel()
        anchorTask = nil
        anchorSubscription = nil
        state = .finished
        resumeFinishWaiters(with: verified)
        return verified
    }

    public nonisolated var description: String {
        "AtlasVaultProductionPresentationPipeline(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    func observationStartCountForTesting() async -> Int {
        await source.startCount()
    }

    func suspendDeliveryForTesting() async {
        await source.suspendDelivery()
    }

    func resumeDeliveryForTesting() async {
        await source.resumeDelivery()
    }

    func latestSequenceForTesting() async -> UInt64 {
        await source.latestSequence()
    }

    func acknowledgedSequenceForTesting() async -> UInt64 {
        await source.lastAcknowledgedSequence()
    }

    func waitUntilSequenceForTesting(_ sequence: UInt64) async {
        await source.waitUntilSequence(sequence)
    }

    private func resumeStartWaiters(with result: Bool) {
        let waiters = startWaiters
        startWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: result)
        }
    }

    private func resumeFinishWaiters(with result: Bool) {
        let waiters = finishWaiters
        finishWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: result)
        }
    }
}
