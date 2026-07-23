import Foundation
import Synchronization

#if canImport(UIKit)
import UIKit
#endif

struct AtlasIOSLifecycleSystemObservation: Sendable {
    let bootstrap: AtlasIOSLifecycleBootstrap
    let signals: AsyncStream<AtlasIOSLifecycleSignal>
}

@MainActor
protocol AtlasIOSLifecycleSystemObserving: AnyObject, Sendable {
    func beginObservation() -> AtlasIOSLifecycleSystemObservation
    func stopObservation()
}

private final class AtlasIOSLifecycleProducerCancellationRelay: Sendable {
    private let task = Mutex<Task<Void, Never>?>(nil)

    func install(_ task: Task<Void, Never>) {
        self.task.withLock { retainedTask in
            retainedTask = task
        }
    }

    func cancel() {
        task.withLock { retainedTask in
            retainedTask?.cancel()
        }
    }

    func clear() {
        task.withLock { retainedTask in
            retainedTask = nil
        }
    }
}

public actor AtlasIOSProcessLifecycleEventSource:
    AtlasVaultPlatformLifecycleEventSourcing,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum State {
        case inactive
        case starting
        case active(UUID)
        case terminal
    }

    private let observer: any AtlasIOSLifecycleSystemObserving
    private var state: State = .inactive
    private var producerTask: Task<Void, Never>?
    private var terminalWaiters: [CheckedContinuation<Void, Never>] = []

    init(observer: any AtlasIOSLifecycleSystemObserving) {
        self.observer = observer
    }

    #if canImport(UIKit)
    public init() {
        observer = AtlasUIKitLifecycleSystemObserver()
    }
    #endif

    public func events() async -> AsyncStream<AtlasVaultLifecycleEvent> {
        guard case .inactive = state else {
            return Self.finishedStream()
        }
        state = .starting

        let observation = await observer.beginObservation()
        var aggregator = AtlasIOSLifecycleAggregator()
        let output = AsyncStream<AtlasVaultLifecycleEvent>.makeStream(
            bufferingPolicy: .unbounded
        )
        for event in aggregator.bootstrap(observation.bootstrap) {
            output.continuation.yield(event)
        }

        let identifier = UUID()
        let cancellationRelay =
            AtlasIOSLifecycleProducerCancellationRelay()
        output.continuation.onTermination = { @Sendable termination in
            if case .cancelled = termination {
                cancellationRelay.cancel()
            }
        }

        let task = Task { [self] in
            await runProducer(
                identifier: identifier,
                signals: observation.signals,
                output: output.continuation,
                aggregator: aggregator,
                cancellationRelay: cancellationRelay
            )
        }
        cancellationRelay.install(task)
        producerTask = task
        state = .active(identifier)
        return output.stream
    }

    public nonisolated var description: String {
        "AtlasIOSProcessLifecycleEventSource(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    func hasRetainedProducerTaskForTesting() -> Bool {
        producerTask != nil
    }

    func isTerminalForTesting() -> Bool {
        if case .terminal = state {
            return true
        }
        return false
    }

    func waitUntilTerminalForTesting() async {
        guard !isTerminalForTesting() else {
            return
        }
        await withCheckedContinuation { continuation in
            terminalWaiters.append(continuation)
        }
    }

    private func runProducer(
        identifier: UUID,
        signals: AsyncStream<AtlasIOSLifecycleSignal>,
        output: AsyncStream<AtlasVaultLifecycleEvent>.Continuation,
        aggregator initialAggregator: AtlasIOSLifecycleAggregator,
        cancellationRelay: AtlasIOSLifecycleProducerCancellationRelay
    ) async {
        var aggregator = initialAggregator
        var reachedTermination = false

        for await signal in signals {
            guard !Task.isCancelled else {
                break
            }
            let events = aggregator.consume(signal)
            for event in events {
                output.yield(event)
                if event == .willTerminate {
                    reachedTermination = true
                }
            }
            if reachedTermination {
                break
            }
        }

        await observer.stopObservation()
        output.finish()
        cancellationRelay.clear()
        producerDidFinish(identifier)
    }

    private func producerDidFinish(_ identifier: UUID) {
        guard case .active(identifier) = state else {
            return
        }
        producerTask = nil
        state = .terminal
        let waiters = terminalWaiters
        terminalWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
    }

    private static func finishedStream()
        -> AsyncStream<AtlasVaultLifecycleEvent>
    {
        AsyncStream { continuation in
            continuation.finish()
        }
    }
}

#if canImport(UIKit)
@MainActor
private final class AtlasUIKitLifecycleSystemObserver:
    AtlasIOSLifecycleSystemObserving
{
    private var notificationCenter: NotificationCenter?
    private var tokens: [AtlasUIKitLifecycleNotificationToken] = []
    private var signalContinuation:
        AsyncStream<AtlasIOSLifecycleSignal>.Continuation?
    private var isObserving = false

    nonisolated init() {}

    func beginObservation() -> AtlasIOSLifecycleSystemObservation {
        guard !isObserving else {
            return AtlasIOSLifecycleSystemObservation(
                bootstrap: Self.conservativeFinishedBootstrap(),
                signals: Self.finishedSignalStream()
            )
        }

        isObserving = true
        let signalPair = AsyncStream<AtlasIOSLifecycleSignal>.makeStream(
            bufferingPolicy: .unbounded
        )
        signalContinuation = signalPair.continuation
        notificationCenter = .default
        registerObservers()
        let bootstrap = captureBootstrap()
        return AtlasIOSLifecycleSystemObservation(
            bootstrap: bootstrap,
            signals: signalPair.stream
        )
    }

    func stopObservation() {
        guard isObserving else {
            return
        }
        isObserving = false
        if let notificationCenter {
            for token in tokens {
                notificationCenter.removeObserver(token)
            }
        }
        tokens.removeAll()
        signalContinuation?.finish()
        signalContinuation = nil
        notificationCenter = nil
    }

    private func registerObservers() {
        observe(UIScene.willConnectNotification) { [weak self] notification in
            guard let scene = notification.object as? UIScene else {
                return
            }
            self?.emit(
                .sceneConnected(
                    Self.identifier(for: scene),
                    state: Self.state(for: scene.activationState)
                )
            )
        }
        observe(UIScene.didDisconnectNotification) { [weak self] notification in
            guard let scene = notification.object as? UIScene else {
                return
            }
            self?.emit(.sceneDisconnected(Self.identifier(for: scene)))
        }
        observe(UIScene.willEnterForegroundNotification) {
            [weak self] notification in
            guard let scene = notification.object as? UIScene else {
                return
            }
            self?.emit(.sceneWillEnterForeground(Self.identifier(for: scene)))
        }
        observe(UIScene.didActivateNotification) { [weak self] notification in
            guard let scene = notification.object as? UIScene else {
                return
            }
            self?.emit(.sceneDidBecomeActive(Self.identifier(for: scene)))
        }
        observe(UIScene.willDeactivateNotification) {
            [weak self] notification in
            guard let scene = notification.object as? UIScene else {
                return
            }
            self?.emit(.sceneWillResignActive(Self.identifier(for: scene)))
        }
        observe(UIScene.didEnterBackgroundNotification) {
            [weak self] notification in
            guard let scene = notification.object as? UIScene else {
                return
            }
            self?.emit(.sceneDidEnterBackground(Self.identifier(for: scene)))
        }
        observe(UIApplication.didBecomeActiveNotification) {
            [weak self] _ in
            self?.emit(.applicationDidBecomeActive)
        }
        observe(UIApplication.willResignActiveNotification) {
            [weak self] _ in
            self?.emit(.applicationWillResignActive)
        }
        observe(UIApplication.didEnterBackgroundNotification) {
            [weak self] _ in
            self?.emit(.applicationDidEnterBackground)
        }
        observe(UIApplication.protectedDataDidBecomeAvailableNotification) {
            [weak self] _ in
            self?.emit(.protectedDataBecameAvailable)
        }
        observe(UIApplication.protectedDataWillBecomeUnavailableNotification) {
            [weak self] _ in
            self?.emit(.protectedDataBecameUnavailable)
        }
        observe(UIApplication.willTerminateNotification) { [weak self] _ in
            self?.emit(.willTerminate)
        }
    }

    private func captureBootstrap() -> AtlasIOSLifecycleBootstrap {
        let application = UIApplication.shared
        let scenes = Dictionary(
            uniqueKeysWithValues: application.connectedScenes.map { scene in
                (
                    Self.identifier(for: scene),
                    Self.state(for: scene.activationState)
                )
            }
        )
        return AtlasIOSLifecycleBootstrap(
            scenes: scenes,
            applicationState: Self.state(for: application.applicationState),
            protectedDataAvailable: application.isProtectedDataAvailable
        )
    }

    private func observe(
        _ name: Notification.Name,
        handler: @escaping @MainActor (Notification) -> Void
    ) {
        guard let notificationCenter else {
            return
        }
        let token = AtlasUIKitLifecycleNotificationToken(handler: handler)
        notificationCenter.addObserver(
            token,
            selector: #selector(
                AtlasUIKitLifecycleNotificationToken.receive(_:)
            ),
            name: name,
            object: nil,
        )
        tokens.append(token)
    }

    private func emit(_ signal: AtlasIOSLifecycleSignal) {
        signalContinuation?.yield(signal)
    }

    private static func identifier(
        for scene: UIScene
    ) -> AtlasIOSSceneIdentifier {
        AtlasIOSSceneIdentifier(scene.session.persistentIdentifier)
    }

    private static func state(
        for activationState: UIScene.ActivationState
    ) -> AtlasIOSSceneLifecycleState {
        switch activationState {
        case .foregroundActive:
            .foregroundActive
        case .foregroundInactive:
            .foregroundInactive
        case .background:
            .background
        case .unattached:
            .unattached
        @unknown default:
            .unattached
        }
    }

    private static func state(
        for applicationState: UIApplication.State
    ) -> AtlasIOSApplicationLifecycleState {
        switch applicationState {
        case .active:
            .active
        case .inactive:
            .inactive
        case .background:
            .background
        @unknown default:
            .background
        }
    }

    private static func finishedSignalStream()
        -> AsyncStream<AtlasIOSLifecycleSignal>
    {
        AsyncStream { continuation in
            continuation.finish()
        }
    }

    private static func conservativeFinishedBootstrap()
        -> AtlasIOSLifecycleBootstrap
    {
        AtlasIOSLifecycleBootstrap(
            scenes: [:],
            applicationState: .background,
            protectedDataAvailable: false
        )
    }
}

@MainActor
private final class AtlasUIKitLifecycleNotificationToken: NSObject {
    private let handler: @MainActor (Notification) -> Void

    init(handler: @escaping @MainActor (Notification) -> Void) {
        self.handler = handler
    }

    @objc
    func receive(_ notification: Notification) {
        MainActor.preconditionIsolated()
        handler(notification)
    }
}
#endif
