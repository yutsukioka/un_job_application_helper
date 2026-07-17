import Foundation

public struct AtlasVaultPresentationUpdate:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let sequence: UInt64
    public let snapshot: AtlasVaultPresentationSnapshot

    public init(
        sequence: UInt64,
        snapshot: AtlasVaultPresentationSnapshot
    ) {
        self.sequence = sequence
        self.snapshot = snapshot
    }

    public var description: String {
        "AtlasVaultPresentationUpdate(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultPresentationUpdateSourcing: Sendable {
    func updates() async -> AsyncStream<AtlasVaultPresentationUpdate>
}

public struct AtlasVaultPresentationSubscription:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let snapshots: AsyncStream<AtlasVaultPresentationSnapshot>
    private let cancelAction: @Sendable () async -> Void

    init(
        snapshots: AsyncStream<AtlasVaultPresentationSnapshot>,
        cancelAction: @escaping @Sendable () async -> Void
    ) {
        self.snapshots = snapshots
        self.cancelAction = cancelAction
    }

    public func cancel() async {
        await cancelAction()
    }

    public var description: String {
        "AtlasVaultPresentationSubscription(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultPresentationObserving: Sendable {
    func subscribe() async -> AtlasVaultPresentationSubscription
    func currentSnapshot() async -> AtlasVaultPresentationSnapshot
}

public actor AtlasVaultObservablePresentationAdapter:
    AtlasVaultPresentationObserving,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private typealias Continuation =
        AsyncStream<AtlasVaultPresentationSnapshot>.Continuation

    private let source: any AtlasVaultPresentationUpdateSourcing
    private var latestSnapshot = AtlasVaultPresentationSnapshot(
        status: .locked,
        privateState: nil
    )
    private var lastSequence: UInt64?
    private var subscribers: [UUID: Continuation] = [:]
    private var observationID: UUID?
    private var observationTask: Task<Void, Never>?

    public init(source: any AtlasVaultPresentationUpdateSourcing) {
        self.source = source
    }

    deinit {
        observationTask?.cancel()
        for continuation in subscribers.values {
            continuation.finish()
        }
    }

    public func currentSnapshot() -> AtlasVaultPresentationSnapshot {
        latestSnapshot
    }

    public func subscribe() -> AtlasVaultPresentationSubscription {
        let subscriberID = UUID()
        let pair = AsyncStream.makeStream(
            of: AtlasVaultPresentationSnapshot.self,
            bufferingPolicy: .bufferingNewest(1)
        )
        pair.continuation.onTermination = { [weak self] _ in
            Task {
                await self?.removeSubscriber(subscriberID)
            }
        }
        subscribers[subscriberID] = pair.continuation
        pair.continuation.yield(latestSnapshot)
        startObservationIfNeeded()

        return AtlasVaultPresentationSubscription(
            snapshots: pair.stream,
            cancelAction: { [weak self] in
                await self?.removeSubscriber(subscriberID)
            }
        )
    }

    public nonisolated var description: String {
        "AtlasVaultObservablePresentationAdapter(state: <redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private func startObservationIfNeeded() {
        guard observationTask == nil else {
            return
        }
        let identifier = UUID()
        let source = source
        observationID = identifier
        observationTask = Task { [weak self] in
            let updates = await source.updates()
            for await update in updates {
                guard !Task.isCancelled else {
                    break
                }
                await self?.receive(
                    update,
                    observationID: identifier
                )
            }
            await self?.finishObservation(identifier)
        }
    }

    private func receive(
        _ update: AtlasVaultPresentationUpdate,
        observationID identifier: UUID
    ) {
        guard observationID == identifier,
              lastSequence.map({ update.sequence > $0 }) ?? true else {
            return
        }
        lastSequence = update.sequence
        let sanitized = sanitize(update.snapshot)
        guard sanitized != latestSnapshot else {
            return
        }
        latestSnapshot = sanitized
        for continuation in subscribers.values {
            continuation.yield(sanitized)
        }
    }

    private func sanitize(
        _ snapshot: AtlasVaultPresentationSnapshot
    ) -> AtlasVaultPresentationSnapshot {
        guard statusMayExposePrivateState(snapshot.status) else {
            return AtlasVaultPresentationSnapshot(
                status: snapshot.status,
                privateState: nil
            )
        }
        return snapshot
    }

    private func statusMayExposePrivateState(
        _ status: AtlasVaultPresentationStatus
    ) -> Bool {
        switch status {
        case .unlocked,
             .saveInProgress,
             .saveFailed,
             .saveDurabilityUnconfirmed:
            true
        case .locked,
             .noVault,
             .activating,
             .locking,
             .keyUnavailable,
             .corruptStore,
             .unsupportedVersion,
             .cancelled,
             .failed:
            false
        }
    }

    private func removeSubscriber(_ identifier: UUID) {
        guard let continuation = subscribers.removeValue(
            forKey: identifier
        ) else {
            return
        }
        continuation.finish()
    }

    private func finishObservation(_ identifier: UUID) {
        guard observationID == identifier else {
            return
        }
        observationID = nil
        observationTask = nil
        let lockedSnapshot = AtlasVaultPresentationSnapshot(
            status: .locked,
            privateState: nil
        )
        if latestSnapshot != lockedSnapshot {
            latestSnapshot = lockedSnapshot
            for continuation in subscribers.values {
                continuation.yield(lockedSnapshot)
            }
        }
        let activeSubscribers = subscribers.values
        subscribers.removeAll()
        for continuation in activeSubscribers {
            continuation.finish()
        }
    }
}
