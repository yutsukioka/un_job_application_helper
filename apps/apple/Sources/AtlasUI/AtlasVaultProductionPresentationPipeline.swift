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
    private var nextSequence: UInt64 = 1
    private var observationStartCount = 0
    private var acceptingUpdates = true
    private var deliverySuspended = false

    func updates() -> AsyncStream<AtlasVaultPresentationUpdate> {
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
        guard observationStartCount == 0 else {
            return
        }
        await withCheckedContinuation { continuation in
            observationStartWaiters.append(continuation)
        }
    }

    func waitUntilSequence(_ sequence: UInt64) async {
        guard latestSequence() < sequence else {
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
}

public actor AtlasVaultProductionPresentationPipeline:
    AtlasVaultProductionPresentationCoordinating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum State {
        case inactive
        case active
        case finishing
        case finished
    }

    private let source: AtlasVaultProductionPresentationUpdateSource
    private let observable: AtlasVaultObservablePresentationAdapter
    private var state: State = .inactive
    private var anchorSubscription: AtlasVaultPresentationSubscription?
    private var anchorTask: Task<Void, Never>?
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
        case .finishing, .finished:
            return false
        case .inactive:
            break
        }

        state = .active
        let subscription = await observable.subscribe()
        anchorSubscription = subscription
        anchorTask = Task {
            for await _ in subscription.snapshots {}
        }
        await source.waitUntilObservationStarted()
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
        case .finishing:
            return await withCheckedContinuation { continuation in
                finishWaiters.append(continuation)
            }
        case .inactive:
            state = .finished
            return false
        case .active:
            state = .finishing
        }

        let locked = AtlasVaultPresentationSnapshot(
            status: .locked,
            privateState: nil
        )
        let acknowledged = await source.sendAndWait(locked)
        let current = await observable.currentSnapshot()
        let verified = acknowledged
            && current == locked
            && current.privateState == nil
        await source.finish()
        await anchorTask?.value
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

    private func resumeFinishWaiters(with result: Bool) {
        let waiters = finishWaiters
        finishWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: result)
        }
    }
}
