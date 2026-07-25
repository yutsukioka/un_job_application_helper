import Foundation

public enum AtlasVaultPlatformLifecycleEventDelivery: Sendable {
    case event(AtlasVaultLifecycleEvent)
    case readinessBoundary(UUID)
}

public struct AtlasVaultPlatformLifecycleEventSubscription: Sendable {
    public let bootstrapEvents: [AtlasVaultLifecycleEvent]
    public let events: AsyncStream<AtlasVaultPlatformLifecycleEventDelivery>
    private let readinessBoundaryRequest:
        @Sendable (UUID) async -> Void

    public init(
        bootstrapEvents: [AtlasVaultLifecycleEvent],
        events: AsyncStream<AtlasVaultPlatformLifecycleEventDelivery>,
        requestReadinessBoundary:
            @escaping @Sendable (UUID) async -> Void
    ) {
        self.bootstrapEvents = bootstrapEvents
        self.events = events
        readinessBoundaryRequest = requestReadinessBoundary
    }

    public func requestReadinessBoundary(_ identifier: UUID) async {
        await readinessBoundaryRequest(identifier)
    }
}

public protocol AtlasVaultPlatformLifecycleEventSourcing: Sendable {
    func subscription() async
        -> AtlasVaultPlatformLifecycleEventSubscription
}

public actor AtlasVaultProductionLifecycleForwarder:
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum State {
        case inactive
        case starting(UUID)
        case active(UUID)
        case terminal
    }

    private let source: any AtlasVaultPlatformLifecycleEventSourcing
    private let host: any AtlasVaultProductionHosting
    private var state: State = .inactive
    private var forwardingTask: Task<Void, Never>?
    private var startWaiters: [CheckedContinuation<Bool, Never>] = []
    private var terminalWaiters: [CheckedContinuation<Void, Never>] = []
    private var terminalLifecycleRequested = false

    public init(
        source: any AtlasVaultPlatformLifecycleEventSourcing,
        host: any AtlasVaultProductionHosting
    ) {
        self.source = source
        self.host = host
    }

    public func start() async -> Bool {
        guard !terminalLifecycleRequested else {
            return false
        }
        switch state {
        case .active:
            return true
        case .starting:
            return await withCheckedContinuation { continuation in
                startWaiters.append(continuation)
            }
        case .terminal:
            return false
        case .inactive:
            break
        }

        let identifier = UUID()
        state = .starting(identifier)
        let source = source
        let host = host
        return await withCheckedContinuation { continuation in
            startWaiters.append(continuation)
            let task = Task { [self] in
                let subscription = await source.subscription()
                guard !Task.isCancelled,
                      mayProcessBootstrap(identifier) else {
                    return
                }

                for event in subscription.bootstrapEvents {
                    guard await forwardBeforeReadiness(
                        event,
                        identifier: identifier,
                        host: host
                    ) else {
                        return
                    }
                }

                var iterator = subscription.events.makeAsyncIterator()
                let readinessBoundary = UUID()
                await subscription.requestReadinessBoundary(
                    readinessBoundary
                )
                guard !Task.isCancelled,
                      mayProcessBootstrap(identifier) else {
                    return
                }
                var reachedReadinessBoundary = false

                while let delivery = await iterator.next() {
                    guard !Task.isCancelled,
                          mayProcessBootstrap(identifier) else {
                        return
                    }
                    switch delivery {
                    case let .event(event):
                        guard await forwardBeforeReadiness(
                            event,
                            identifier: identifier,
                            host: host
                        ) else {
                            return
                        }
                    case let .readinessBoundary(candidate):
                        guard candidate == readinessBoundary else {
                            continue
                        }
                        reachedReadinessBoundary = true
                    }
                    if reachedReadinessBoundary {
                        break
                    }
                }

                guard reachedReadinessBoundary else {
                    forwardingDidFinish(identifier)
                    return
                }

                guard markBootstrapReady(identifier) else {
                    return
                }

                while let delivery = await iterator.next() {
                    guard !Task.isCancelled,
                          mayForward(identifier) else {
                        break
                    }
                    guard case let .event(event) = delivery else {
                        continue
                    }
                    if event == .willTerminate {
                        terminalLifecycleRequested = true
                    }
                    _ = await host.handleLifecycleEvent(event)
                    if event == .willTerminate {
                        break
                    }
                }
                forwardingDidFinish(identifier)
            }
            forwardingTask = task
        }
    }

    public func stop() async {
        if case .terminal = state, forwardingTask == nil {
            return
        }

        state = .terminal
        terminalLifecycleRequested = true
        resumeTerminalWaiters()
        let task = forwardingTask
        task?.cancel()
        await task?.value
        resumeStartWaiters(with: false)
        forwardingTask = nil
    }

    public nonisolated var description: String {
        "AtlasVaultProductionLifecycleForwarder(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    func isRunningForTesting() -> Bool {
        switch state {
        case .starting, .active:
            true
        case .inactive, .terminal:
            false
        }
    }

    func isTerminal() -> Bool {
        if case .terminal = state {
            return true
        }
        return false
    }

    func hasTerminalLifecycleIntent() -> Bool {
        terminalLifecycleRequested || isTerminal()
    }

    func hasRetainedForwardingTaskForTesting() -> Bool {
        forwardingTask != nil
    }

    func waitUntilTerminalForTesting() async {
        guard !isTerminal() else {
            return
        }
        await withCheckedContinuation { continuation in
            terminalWaiters.append(continuation)
        }
    }

    private func mayProcessBootstrap(_ identifier: UUID) -> Bool {
        guard case .starting(identifier) = state else {
            return false
        }
        return true
    }

    private func forwardBeforeReadiness(
        _ event: AtlasVaultLifecycleEvent,
        identifier: UUID,
        host: any AtlasVaultProductionHosting
    ) async -> Bool {
        guard !Task.isCancelled,
              mayProcessBootstrap(identifier) else {
            return false
        }
        if event == .willTerminate {
            terminalLifecycleRequested = true
        }
        _ = await host.handleLifecycleEvent(event)
        guard !Task.isCancelled,
              mayProcessBootstrap(identifier) else {
            return false
        }
        if event == .willTerminate {
            forwardingDidFinish(identifier)
            return false
        }
        return true
    }

    private func markBootstrapReady(_ identifier: UUID) -> Bool {
        guard case .starting(identifier) = state else {
            return false
        }
        state = .active(identifier)
        resumeStartWaiters(with: true)
        return true
    }

    private func mayForward(_ identifier: UUID) -> Bool {
        guard case .active(identifier) = state else {
            return false
        }
        return true
    }

    private func forwardingDidFinish(_ identifier: UUID) {
        switch state {
        case .starting(identifier), .active(identifier):
            state = .terminal
            resumeStartWaiters(with: false)
            resumeTerminalWaiters()
        case .inactive, .terminal, .starting, .active:
            break
        }
    }

    private func resumeStartWaiters(with result: Bool) {
        let waiters = startWaiters
        startWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: result)
        }
    }

    private func resumeTerminalWaiters() {
        let waiters = terminalWaiters
        terminalWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
    }
}

public enum AtlasVaultProductionCompositionError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case invalidAPIBaseURL
    case invalidSearchLimit
    case invalidUnlockTimeout
    case productionCapabilityUnavailable
    case unlockMethodUnavailable
    case startUnavailable
    case stopped

    public var description: String {
        switch self {
        case .invalidAPIBaseURL:
            "invalidAPIBaseURL"
        case .invalidSearchLimit:
            "invalidSearchLimit"
        case .invalidUnlockTimeout:
            "invalidUnlockTimeout"
        case .productionCapabilityUnavailable:
            "productionCapabilityUnavailable"
        case .unlockMethodUnavailable:
            "unlockMethodUnavailable"
        case .startUnavailable:
            "startUnavailable"
        case .stopped:
            "stopped"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultProductionCompositionConfiguration:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let apiBaseURL: URL
    public let publicSearchLimit: Int
    public let unlockTimeout: Duration
    public let lifecycleLockPolicy: AtlasVaultLifecycleLockPolicy
    public let lockOnInactive: Bool

    public init(
        apiBaseURL: URL,
        publicSearchLimit: Int = 50,
        unlockTimeout: Duration = .seconds(30),
        lifecycleLockPolicy: AtlasVaultLifecycleLockPolicy,
        lockOnInactive: Bool
    ) throws(AtlasVaultProductionCompositionError) {
        guard let normalizedURL = Self.validatedOrigin(apiBaseURL) else {
            throw .invalidAPIBaseURL
        }
        guard (1...AtlasPublicJobSearchRequest.maximumLimit)
            .contains(publicSearchLimit) else {
            throw .invalidSearchLimit
        }
        guard unlockTimeout > .zero else {
            throw .invalidUnlockTimeout
        }

        self.apiBaseURL = normalizedURL
        self.publicSearchLimit = publicSearchLimit
        self.unlockTimeout = unlockTimeout
        self.lifecycleLockPolicy = lifecycleLockPolicy
        self.lockOnInactive = lockOnInactive
    }

    public var description: String {
        "AtlasVaultProductionCompositionConfiguration(<redacted>)"
    }

    public var debugDescription: String {
        description
    }

    private static func validatedOrigin(_ candidate: URL) -> URL? {
        guard var components = URLComponents(
            url: candidate,
            resolvingAgainstBaseURL: false
        ),
        let scheme = components.scheme?.lowercased(),
        scheme == "http" || scheme == "https",
        let host = components.host,
        !host.isEmpty,
        components.user == nil,
        components.password == nil,
        components.query == nil,
        components.fragment == nil,
        components.path.isEmpty || components.path == "/"
        else {
            return nil
        }

        components.scheme = scheme
        components.path = ""
        return components.url
    }
}

public struct AtlasContinuousVaultLifecycleTimebase:
    AtlasVaultLifecycleClock,
    AtlasVaultLifecycleSleeper,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let clock: ContinuousClock
    private let origin: ContinuousClock.Instant

    public init() {
        let clock = ContinuousClock()
        self.clock = clock
        origin = clock.now
    }

    public func now() async -> Duration {
        origin.duration(to: clock.now)
    }

    public func sleep(until deadline: Duration) async throws {
        let elapsed = origin.duration(to: clock.now)
        guard deadline > elapsed else {
            return
        }
        try await clock.sleep(for: deadline - elapsed)
    }

    public var description: String {
        "AtlasContinuousVaultLifecycleTimebase(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

@MainActor
public final class AtlasVaultProductionCompositionHarness:
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum Lifetime {
        case inactive
        case starting
        case started
        case stopping
        case stopped
    }

    private enum StartOutcome {
        case started(AtlasLockedShellUnlockFlowState)
        case failed(AtlasLockedShellUnlockFlowState)
        case terminal(AtlasLockedShellUnlockFlowState)

        func get() throws -> AtlasLockedShellUnlockFlowState {
            switch self {
            case let .started(state):
                state
            case .failed:
                throw AtlasVaultProductionCompositionError.startUnavailable
            case .terminal:
                throw AtlasVaultProductionCompositionError.stopped
            }
        }
    }

    private struct StartOperation {
        let identifier: UUID
        let task: Task<StartOutcome, Never>
    }

    private struct StopOperation {
        let identifier: UUID
        let task: Task<AtlasLockedShellUnlockFlowState, Never>
    }

    public let presentationOwner: AtlasVaultProductionPresentationOwner
    public let publicShellActions: AtlasLockedPublicShellActions
    public let unlockActions: AtlasExplicitUnlockViewActions

    private let host: any AtlasVaultProductionHosting
    private let lifecycleForwarder: AtlasVaultProductionLifecycleForwarder
    private let creationContext: AtlasLocalVaultCreationContext?
    private var lifetime: Lifetime = .inactive
    private var startOperation: StartOperation?
    private var stopOperation: StopOperation?
    private var terminalState: AtlasLockedShellUnlockFlowState?
    private var terminalStopRequested = false
    private var stoppingWaiters: [CheckedContinuation<Void, Never>] = []

    convenience init(
        host: any AtlasVaultProductionHosting,
        presentationOwner: AtlasVaultProductionPresentationOwner,
        lifecycleForwarder: AtlasVaultProductionLifecycleForwarder,
        publicSearchLimit: Int,
        unlockTimeout: Duration
    ) {
        self.init(
            host: host,
            presentationOwner: presentationOwner,
            lifecycleForwarder: lifecycleForwarder,
            publicSearchLimit: publicSearchLimit,
            unlockTimeout: unlockTimeout,
            creationContext: nil
        )
    }

    init(
        host: any AtlasVaultProductionHosting,
        presentationOwner: AtlasVaultProductionPresentationOwner,
        lifecycleForwarder: AtlasVaultProductionLifecycleForwarder,
        publicSearchLimit: Int,
        unlockTimeout: Duration,
        creationContext: AtlasLocalVaultCreationContext?
    ) {
        self.host = host
        self.presentationOwner = presentationOwner
        self.lifecycleForwarder = lifecycleForwarder
        self.creationContext = creationContext

        publicShellActions = AtlasLockedPublicShellActions(
            search: { query in
                guard let request = try? AtlasPublicJobSearchRequest(
                    query: query,
                    limit: publicSearchLimit,
                    offset: 0
                ) else {
                    return
                }
                _ = try? await host.searchPublicJobs(request)
            },
            requestUnlock: {
                _ = await host.requestUnlockPanel()
            }
        )
        unlockActions = AtlasExplicitUnlockViewActions(
            select: { method in
                _ = await host.selectUnlockMethod(method)
            },
            submit: { submission in
                let state = await host.submitUnlock(
                    submission,
                    timeout: unlockTimeout
                )
                if state.mode == .unlockedTransition {
                    return .unlocked
                }
                return state.unlockPanelState?.status ?? .failed
            },
            cancel: {
                _ = await host.cancelUnlock()
            },
            didDisappear: {
                _ = await host.unlockPanelDidDisappear()
            }
        )
    }

    public func start() async throws -> AtlasLockedShellUnlockFlowState {
        switch lifetime {
        case .stopping, .stopped:
            throw AtlasVaultProductionCompositionError.stopped
        case .started:
            let terminalIntentBeforeState = await lifecycleForwarder
                .hasTerminalLifecycleIntent()
            guard !terminalStopRequested,
                  case .started = lifetime,
                  !terminalIntentBeforeState else {
                _ = await stop()
                throw AtlasVaultProductionCompositionError.stopped
            }
            let state = await host.currentFlowState()
            let terminalIntentAfterState = await lifecycleForwarder
                .hasTerminalLifecycleIntent()
            guard !terminalStopRequested,
                  case .started = lifetime,
                  !terminalIntentAfterState else {
                _ = await stop()
                throw AtlasVaultProductionCompositionError.stopped
            }
            return state
        case .starting:
            guard let startOperation else {
                lifetime = .stopped
                throw AtlasVaultProductionCompositionError.startUnavailable
            }
            let result = await startOperation.task.value
            try await stopIfLifecycleTerminatedDuringStart(result)
            return try startResultAfterAwait(result)
        case .inactive:
            break
        }

        lifetime = .starting
        let identifier = UUID()
        let host = host
        let lifecycleForwarder = lifecycleForwarder
        let task = Task<StartOutcome, Never> {
            guard await lifecycleForwarder.start() else {
                async let hostState = host.stop()
                async let lifecycleStop: Void = lifecycleForwarder.stop()
                let state = await hostState
                _ = await lifecycleStop
                return .terminal(state)
            }
            do {
                return .started(try await host.start())
            } catch {
                let lifecycleTerminated = await lifecycleForwarder
                    .hasTerminalLifecycleIntent()
                async let hostState = host.stop()
                async let lifecycleStop: Void = lifecycleForwarder.stop()
                let state = await hostState
                _ = await lifecycleStop
                return lifecycleTerminated
                    ? .terminal(state)
                    : .failed(state)
            }
        }
        let operation = StartOperation(identifier: identifier, task: task)
        startOperation = operation
        let result = await task.value
        try await stopIfLifecycleTerminatedDuringStart(result)
        if startOperation?.identifier == identifier {
            startOperation = nil
            if lifetime == .starting {
                switch result {
                case .started:
                    lifetime = .started
                case let .failed(state), let .terminal(state):
                    terminalState = state
                    lifetime = .stopped
                }
            }
        }
        return try startResultAfterOperation(result)
    }

    public func stop() async -> AtlasLockedShellUnlockFlowState {
        terminalStopRequested = true
        if let stopOperation {
            return await stopOperation.task.value
        }
        if lifetime == .stopped, let terminalState {
            return terminalState
        }

        lifetime = .stopping
        resumeStoppingWaiters()
        let identifier = UUID()
        let host = host
        let lifecycleForwarder = lifecycleForwarder
        let creationOwner = creationContext?.owner
        let task = Task {
            async let hostState = host.stop()
            async let lifecycleStop: Void = lifecycleForwarder.stop()
            if let creationOwner {
                await creationOwner.stop()
            }
            let state = await hostState
            _ = await lifecycleStop
            return state
        }
        stopOperation = StopOperation(identifier: identifier, task: task)
        let state = await task.value
        if stopOperation?.identifier == identifier {
            stopOperation = nil
            terminalState = state
            lifetime = .stopped
        }
        return state
    }

    public func makeRootView() -> AtlasVaultProductionRootView {
        if let creationContext {
            return AtlasVaultProductionRootView(
                owner: presentationOwner,
                publicShellActions: publicShellActions,
                unlockActions: unlockActions,
                creationContext: creationContext
            )
        }
        return AtlasVaultProductionRootView(
            owner: presentationOwner,
            publicShellActions: publicShellActions,
            unlockActions: unlockActions
        )
    }

    public nonisolated var description: String {
        "AtlasVaultProductionCompositionHarness(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    func lifecycleIsRunningForTesting() async -> Bool {
        await lifecycleForwarder.isRunningForTesting()
    }

    var creationContextForTesting: AtlasLocalVaultCreationContext? {
        creationContext
    }

    func waitUntilStoppingForTesting() async {
        switch lifetime {
        case .stopping, .stopped:
            return
        case .inactive, .starting, .started:
            break
        }
        await withCheckedContinuation { continuation in
            stoppingWaiters.append(continuation)
        }
    }

    func waitUntilLifecycleTerminationForTesting() async {
        await lifecycleForwarder.waitUntilTerminalForTesting()
    }

    private func resumeStoppingWaiters() {
        let waiters = stoppingWaiters
        stoppingWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
    }

    private func stopIfLifecycleTerminatedDuringStart(
        _ result: StartOutcome
    ) async throws {
        guard !terminalStopRequested else {
            return
        }
        switch result {
        case .failed:
            return
        case let .terminal(state):
            terminalStopRequested = true
            startOperation = nil
            terminalState = state
            lifetime = .stopped
            throw AtlasVaultProductionCompositionError.stopped
        case .started:
            guard await lifecycleForwarder
                .hasTerminalLifecycleIntent() else {
                return
            }
            startOperation = nil
            _ = await stop()
            throw AtlasVaultProductionCompositionError.stopped
        }
    }

    private func startResultAfterAwait(
        _ result: StartOutcome
    ) throws -> AtlasLockedShellUnlockFlowState {
        guard !terminalStopRequested else {
            throw AtlasVaultProductionCompositionError.stopped
        }
        if case .started = result {
            switch lifetime {
            case .starting, .started:
                break
            case .inactive, .stopping, .stopped:
                throw AtlasVaultProductionCompositionError.stopped
            }
        }
        return try result.get()
    }

    private func startResultAfterOperation(
        _ result: StartOutcome
    ) throws -> AtlasLockedShellUnlockFlowState {
        guard !terminalStopRequested else {
            throw AtlasVaultProductionCompositionError.stopped
        }
        if case .started = result {
            guard case .started = lifetime else {
                throw AtlasVaultProductionCompositionError.stopped
            }
        }
        return try result.get()
    }
}

@MainActor
public enum AtlasVaultProductionCompositionFactory {
    public static func makeUnwiredProductionLike(
        configuration: AtlasVaultProductionCompositionConfiguration,
        lifecycleEvents: any AtlasVaultPlatformLifecycleEventSourcing
    ) throws -> AtlasVaultProductionCompositionHarness {
        let timebase = AtlasContinuousVaultLifecycleTimebase()
        return try makeUnwiredProductionLike(
            configuration: configuration,
            lifecycleEvents: lifecycleEvents,
            directoryLocator:
                AtlasFoundationApplicationSupportDirectoryLocator(),
            keychainClient: SecItemAtlasKeychainClient(),
            atomicFileSystemClient: AtlasFoundationAtomicFileSystemClient(),
            lifecycleClock: timebase,
            lifecycleSleeper: timebase
        )
    }

    static func makeUnwiredProductionLike<
        DirectoryLocator: AtlasApplicationSupportDirectoryLocating,
        KeychainClient: AtlasKeychainClient,
        AtomicFileSystemClient: AtlasVaultAtomicFileSystemClient,
        LifecycleClock: AtlasVaultLifecycleClock,
        LifecycleSleeper: AtlasVaultLifecycleSleeper
    >(
        configuration: AtlasVaultProductionCompositionConfiguration,
        lifecycleEvents: any AtlasVaultPlatformLifecycleEventSourcing,
        directoryLocator: DirectoryLocator,
        keychainClient: KeychainClient,
        atomicFileSystemClient: AtomicFileSystemClient,
        lifecycleClock: LifecycleClock,
        lifecycleSleeper: LifecycleSleeper,
        publicJobs injectedPublicJobs:
            (any AtlasPublicJobSearching)? = nil,
        publicSnapshotRestorer injectedPublicSnapshotRestorer:
            (any AtlasPublicSnapshotRestoring)? = nil,
        unlockRequestSleep:
            (@Sendable (Duration) async throws -> Void)? = nil
    ) throws -> AtlasVaultProductionCompositionHarness {
        let productionCapabilities =
            AtlasVaultUnlockCapabilities.currentProduction
        guard productionCapabilities.availableMethods == [.localKey] else {
            throw AtlasVaultProductionCompositionError
                .productionCapabilityUnavailable
        }

        let apiClient = AtlasAPIClient(baseURL: configuration.apiBaseURL)
        let publicJobs: any AtlasPublicJobSearching =
            injectedPublicJobs
                ?? AtlasAPIClientPublicJobAdapter(client: apiClient)
        let rootProvider = AtlasApplicationSupportVaultRootProvider(
            directoryLocator: directoryLocator
        )
        let publicSnapshotRestorer: any AtlasPublicSnapshotRestoring =
            injectedPublicSnapshotRestorer
                ?? AtlasApplicationSupportPublicSnapshotRestorer(
                    rootProvider: rootProvider
                )
        let vaultSelector = AtlasKeychainVaultSelectionRegistry(
            client: keychainClient
        )
        let runtimeServices = AtlasVaultRuntimeFactory.production(
            directoryLocator: directoryLocator,
            keychainClient: keychainClient,
            atomicFileSystemClient: atomicFileSystemClient
        )
        let runtime = AtlasVaultRuntimeFacade.runtimeServices(runtimeServices)
        let lifecycle = AtlasVaultLifecycleCoordinator(
            runtimeFacade: runtime,
            lockPolicy: configuration.lifecycleLockPolicy,
            clock: lifecycleClock,
            sleeper: lifecycleSleeper,
            lockOnInactive: configuration.lockOnInactive
        )
        let presentation = AtlasVaultProductionPresentationPipeline()
        let presentationOwner = AtlasVaultProductionPresentationOwner()
        let unlockDependencies = AtlasVaultUnlockRequestDependencies(
            derivePassphraseVaultKey: { _ in
                throw AtlasVaultProductionCompositionError
                    .unlockMethodUnavailable
            },
            deriveRecoveryVaultKey: { _ in
                throw AtlasVaultProductionCompositionError
                    .unlockMethodUnavailable
            },
            activate: { request in
                try await runtime.activate(request)
            },
            sleep: unlockRequestSleep ?? { duration in
                try await Task.sleep(for: duration)
            }
        )
        let unlockCoordinator = AtlasVaultUnlockRequestCoordinator(
            dependencies: unlockDependencies
        )
        let unlockControllerBuilder =
            AtlasVaultProductionUnlockPresentationControllerBuilder()
        let hostDependencies = AtlasVaultProductionHostDependencies(
            publicJobs: publicJobs,
            publicSnapshotRestorer: publicSnapshotRestorer,
            vaultIDSelector: vaultSelector,
            runtime: runtime,
            lifecycle: lifecycle,
            presentation: presentation,
            presentationOwner: presentationOwner,
            unlockCoordinator: unlockCoordinator,
            unlockControllerBuilder: unlockControllerBuilder
        )
        let hostFactory = AtlasVaultProductionHostFactory(
            dependencies: hostDependencies,
            builder: AtlasVaultProductionHostBuilder()
        )
        let host = hostFactory.makeHost()
        let creationCoordinator =
            AtlasLocalVaultCreationCoordinator.production(
                runtimeServices: runtimeServices,
                selectionRegistry: vaultSelector,
                journalStore:
                    AtlasKeychainLocalVaultCreationJournalStore(
                        client: keychainClient
                    )
            )
        let creationOwner =
            AtlasLocalVaultCreationPresentationOwner(
                creator: creationCoordinator,
                continueToUnlock: {
                    await host.requestUnlockPanel()
                }
            )
        let creationActions = AtlasLocalVaultCreationActions(
            present: { [weak creationOwner] in
                creationOwner?.present()
            },
            dismiss: { [weak creationOwner] in
                creationOwner?.dismiss()
            },
            createOrResume: { [weak creationOwner] in
                creationOwner?.beginCreateOrResume()
            },
            pause: { [weak creationOwner] in
                await creationOwner?.pause()
            }
        )
        let creationContext = AtlasLocalVaultCreationContext(
            owner: creationOwner,
            actions: creationActions
        )
        let lifecycleForwarder = AtlasVaultProductionLifecycleForwarder(
            source: lifecycleEvents,
            host: host
        )
        return AtlasVaultProductionCompositionHarness(
            host: host,
            presentationOwner: presentationOwner,
            lifecycleForwarder: lifecycleForwarder,
            publicSearchLimit: configuration.publicSearchLimit,
            unlockTimeout: configuration.unlockTimeout,
            creationContext: creationContext
        )
    }
}
