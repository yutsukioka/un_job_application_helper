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
    private let eventObserver:
        @Sendable (AtlasVaultLifecycleEvent) async -> Void
    private var state: State = .inactive
    private var forwardingTask: Task<Void, Never>?
    private var startWaiters: [CheckedContinuation<Bool, Never>] = []
    private var terminalWaiters: [CheckedContinuation<Void, Never>] = []
    private var terminalLifecycleRequested = false

    public init(
        source: any AtlasVaultPlatformLifecycleEventSourcing,
        host: any AtlasVaultProductionHosting,
        eventObserver:
            @escaping @Sendable (AtlasVaultLifecycleEvent) async -> Void =
                { _ in }
    ) {
        self.source = source
        self.host = host
        self.eventObserver = eventObserver
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
                    async let observed: Void = eventObserver(event)
                    async let forwarded = host.handleLifecycleEvent(event)
                    _ = await observed
                    _ = await forwarded
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
        async let observed: Void = eventObserver(event)
        async let forwarded = host.handleLifecycleEvent(event)
        _ = await observed
        _ = await forwarded
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
    case privateFeatureUnavailable
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
        case .privateFeatureUnavailable:
            "privateFeatureUnavailable"
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
    private let recoveryExportContext: AtlasVaultRecoveryExportContext?
    private let recoveryImportContext: AtlasVaultRecoveryImportContext?
    private let savedSearchContext: AtlasVaultSavedSearchContext?
    private let pairingContext: AtlasVaultTrustedPairingContext?
    private let savedSearchHandoffCoordinator:
        (any AtlasVaultSavedSearchPublicHandoffCoordinating)?
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
            creationContext: nil,
            recoveryExportContext: nil,
            recoveryImportContext: nil,
            savedSearchContext: nil,
            pairingContext: nil,
            savedSearchHandoffCoordinator: nil
        )
    }

    convenience init(
        host: any AtlasVaultProductionHosting,
        presentationOwner: AtlasVaultProductionPresentationOwner,
        lifecycleForwarder: AtlasVaultProductionLifecycleForwarder,
        publicSearchLimit: Int,
        unlockTimeout: Duration,
        creationContext: AtlasLocalVaultCreationContext?
    ) {
        self.init(
            host: host,
            presentationOwner: presentationOwner,
            lifecycleForwarder: lifecycleForwarder,
            publicSearchLimit: publicSearchLimit,
            unlockTimeout: unlockTimeout,
            creationContext: creationContext,
            recoveryExportContext: nil,
            recoveryImportContext: nil,
            savedSearchContext: nil,
            pairingContext: nil,
            savedSearchHandoffCoordinator: nil
        )
    }

    init(
        host: any AtlasVaultProductionHosting,
        presentationOwner: AtlasVaultProductionPresentationOwner,
        lifecycleForwarder: AtlasVaultProductionLifecycleForwarder,
        publicSearchLimit: Int,
        unlockTimeout: Duration,
        creationContext: AtlasLocalVaultCreationContext?,
        recoveryExportContext: AtlasVaultRecoveryExportContext?,
        recoveryImportContext: AtlasVaultRecoveryImportContext? = nil,
        savedSearchContext: AtlasVaultSavedSearchContext? = nil,
        pairingContext: AtlasVaultTrustedPairingContext? = nil,
        savedSearchHandoffCoordinator:
            (any AtlasVaultSavedSearchPublicHandoffCoordinating)? = nil
    ) {
        self.host = host
        self.presentationOwner = presentationOwner
        self.lifecycleForwarder = lifecycleForwarder
        self.creationContext = creationContext
        self.recoveryExportContext = recoveryExportContext
        self.recoveryImportContext = recoveryImportContext
        self.savedSearchContext = savedSearchContext
        self.pairingContext = pairingContext
        self.savedSearchHandoffCoordinator =
            savedSearchHandoffCoordinator

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

        if let pairingOwner = pairingContext?.owner,
           let productionHost = host as? AtlasVaultProductionHost,
           !(await productionHost.attachTrustedPairingAuthority(pairingOwner))
        {
            throw AtlasVaultProductionCompositionError
                .privateFeatureUnavailable
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
        let recoveryOwner = recoveryExportContext?.owner
        let recoveryImportOwner = recoveryImportContext?.owner
        let savedSearchOwner = savedSearchContext?.owner
        let pairingOwner = pairingContext?.owner
        let savedSearchHandoffCoordinator =
            savedSearchHandoffCoordinator
        savedSearchOwner?.hidePrivatePresentation()
        let task = Task {
            async let hostState = host.stop()
            async let lifecycleStop: Void = lifecycleForwarder.stop()
            async let savedSearchHandoffStop: Void =
                Self.stopSavedSearchHandoffCoordinator(
                    savedSearchHandoffCoordinator
                )
            if let creationOwner {
                await creationOwner.stop()
            }
            if let recoveryOwner {
                await recoveryOwner.stop()
            }
            if let recoveryImportOwner {
                await recoveryImportOwner.stop()
            }
            if let pairingOwner {
                await pairingOwner.stopAndDrain()
            }
            let state = await hostState
            if let savedSearchOwner {
                await savedSearchOwner.stopAndDrainPrivateSession()
            }
            _ = await savedSearchHandoffStop
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
        return AtlasVaultProductionRootView(
            owner: presentationOwner,
            publicShellActions: publicShellActions,
            unlockActions: unlockActions,
            creationContext: creationContext,
            recoveryExportContext: recoveryExportContext,
            recoveryImportContext: recoveryImportContext,
            savedSearchContext: savedSearchContext,
            pairingContext: pairingContext
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

    var recoveryExportContextForTesting:
        AtlasVaultRecoveryExportContext?
    {
        recoveryExportContext
    }

    var recoveryImportContextForTesting:
        AtlasVaultRecoveryImportContext?
    {
        recoveryImportContext
    }

    var savedSearchContextForTesting:
        AtlasVaultSavedSearchContext?
    {
        savedSearchContext
    }

    var pairingContextForTesting: AtlasVaultTrustedPairingContext? {
        pairingContext
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

    private nonisolated static func stopSavedSearchHandoffCoordinator(
        _ savedSearchHandoffCoordinator:
            (any AtlasVaultSavedSearchPublicHandoffCoordinating)?
    ) async {
        guard let savedSearchHandoffCoordinator else {
            return
        }
        await savedSearchHandoffCoordinator.stop()
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
        savedSearchTimestamp:
            (@Sendable () -> String)? = nil,
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
        let savedSearchTimestampProvider =
            savedSearchTimestamp ?? {
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime]
                return formatter.string(from: Date())
            }
        let vaultSelector = AtlasKeychainVaultSelectionRegistry(
            client: keychainClient
        )
        let creationJournalStore =
            AtlasKeychainLocalVaultCreationJournalStore(
                client: keychainClient
            )
        let recoveryJournalStore =
            AtlasKeychainVaultRecoveryExportJournalStore(
                client: keychainClient
            )
        let recoveryImportJournalStore =
            AtlasKeychainVaultRecoveryImportJournalStore(
                client: keychainClient
            )
        let recoveryImportAvailability =
            AtlasVaultRecoveryImportAvailability(
                initialPendingImport: true
            )
        let pendingTransactionAuthority =
            AtlasVaultPendingTransactionAuthority()
        let pairingTransactionStore =
            AtlasKeychainPairingTransactionStore(
                client: keychainClient
            )
        let hostVaultSelector =
            AtlasPendingVaultTransactionSelectionGate(
                selector: vaultSelector,
                hasPendingCreation: {
                    try creationJournalStore.loadJournal() != nil
                        || pairingTransactionStore.load() != nil
                },
                hasPendingImport: {
                    try recoveryImportJournalStore.loadJournal() != nil
                },
                pendingImportDidChange: { pending in
                    await recoveryImportAvailability.setPendingImport(
                        pending
                    )
                }
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
        let recoveryUnlockProvider =
            AtlasVaultRecoveryUnlockProvider.production(
                runtimeServices: runtimeServices
            )
        let unlockCapabilitiesResolver =
            AtlasVaultProductionUnlockCapabilitiesResolver.production(
                runtimeServices: runtimeServices
            )
        let unlockDependencies = AtlasVaultUnlockRequestDependencies(
            derivePassphraseVaultKey: { _ in
                throw AtlasVaultProductionCompositionError
                    .unlockMethodUnavailable
            },
            deriveVaultAwareRecoveryVaultKey: { vaultID, secret in
                try await recoveryUnlockProvider.deriveVaultKey(
                    vaultID: vaultID,
                    recoverySecret: secret
                )
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
        let privateSessionBridge =
            AtlasVaultPrivateSessionBoundaryBridge()
        let hostDependencies = AtlasVaultProductionHostDependencies(
            publicJobs: publicJobs,
            publicSnapshotRestorer: publicSnapshotRestorer,
            vaultIDSelector: hostVaultSelector,
            runtime: runtime,
            lifecycle: lifecycle,
            presentation: presentation,
            presentationOwner: presentationOwner,
            unlockCoordinator: unlockCoordinator,
            unlockControllerBuilder: unlockControllerBuilder,
            unlockCapabilitiesResolver: unlockCapabilitiesResolver,
            privateSessionBoundary: privateSessionBridge
        )
        let hostFactory = AtlasVaultProductionHostFactory(
            dependencies: hostDependencies,
            builder: AtlasVaultProductionHostBuilder()
        )
        let host = hostFactory.makeHost()
        let authorizeSensitivePairingMutation: @Sendable () async -> Bool = {
            do { try Task.checkCancellation() } catch { return false }
            let lifecycleStatus = await lifecycle.status()
            guard
                !lifecycleStatus.hasPendingGraceLock,
                lifecycleStatus.failure == nil
            else { return false }
            switch lifecycleStatus.lastEvent {
            case .didBecomeActive,
                 .protectedDataBecameAvailable:
                return true
            case .none,
                 .willResignActive,
                 .didEnterBackground,
                 .willTerminate,
                 .protectedDataBecameUnavailable:
                return false
            }
        }
        guard
            let privateMutationHost =
                host as? any AtlasVaultPrivateMutationHosting,
            let privateMutationContainmentHost =
                host as? any AtlasVaultPrivateMutationContainmentHosting,
            host is any AtlasSavedSearchPublicHandoffHosting,
            let savedSearchHandoffReservationHost =
                host as?
                    any AtlasSavedSearchPublicHandoffReservationHosting
        else {
            throw AtlasVaultProductionCompositionError
                .privateFeatureUnavailable
        }
        let savedSearchCoordinator = AtlasVaultSavedSearchCoordinator(
            environment: AtlasVaultSavedSearchEnvironment(
                readPrivateState: {
                    try await runtime.privateState().state
                },
                applyPrivateMutation: { request in
                    await privateMutationHost.applyPrivateMutation(request)
                },
                containCommittedPrivateMutationFailure: {
                    await privateMutationContainmentHost
                        .containCommittedPrivateMutationFailure()
                },
                timestamp: savedSearchTimestampProvider
            )
        )
        let savedSearchOwner =
            AtlasVaultSavedSearchPresentationOwner(
                coordinator: savedSearchCoordinator
            )
        let savedSearchHandoffCoordinator =
            AtlasVaultSavedSearchPublicHandoffCoordinator(
                host: savedSearchHandoffReservationHost
            )
        guard privateSessionBridge.attach(savedSearchOwner) else {
            throw AtlasVaultProductionCompositionError
                .privateFeatureUnavailable
        }
        let savedSearchActions = AtlasVaultSavedSearchActions(
            create: { [weak savedSearchOwner] draft in
                await savedSearchOwner?.create(draft)
            },
            update: {
                [weak savedSearchOwner] identifier, draft in
                await savedSearchOwner?.update(
                    presentationID: identifier,
                    draft: draft
                )
            },
            delete: { [weak savedSearchOwner] identifier in
                await savedSearchOwner?.delete(
                    presentationID: identifier
                )
            },
            execute: { [weak savedSearchOwner] identifier in
                guard let savedSearchOwner,
                      let claim = await savedSearchOwner
                          .beginPublicSearchHandoff() else {
                    return
                }
                guard let reservation =
                    await savedSearchHandoffCoordinator
                        .reserveHostAdmission() else {
                    await savedSearchOwner
                        .failPublicSearchHandoff(claim)
                    return
                }
                let request: AtlasPublicJobSearchRequest
                do {
                    request = try await savedSearchCoordinator
                        .publicSearchRequest(
                            presentationID: identifier,
                            maximumLimit: configuration.publicSearchLimit
                        )
                } catch {
                    await savedSearchHandoffCoordinator
                        .cancelHostAdmission(reservation)
                    await savedSearchOwner.failPublicSearchHandoff(claim)
                    return
                }
                guard await savedSearchOwner
                    .completePublicSearchHandoff(claim) else {
                    await savedSearchHandoffCoordinator
                        .cancelHostAdmission(reservation)
                    return
                }
                _ = await savedSearchHandoffCoordinator.perform(
                    request,
                    reservation: reservation
                )
            },
            lock: { [weak savedSearchOwner] in
                await savedSearchOwner?.beginLocking()
                _ = await host.lock()
            }
        )
        let savedSearchContext = AtlasVaultSavedSearchContext(
            owner: savedSearchOwner,
            actions: savedSearchActions
        )
        let vaultKeyCreator = AtlasKeychainVaultKeyStore(
            client: keychainClient
        )
        let recoveryImportCoordinator =
            AtlasVaultRecoveryImportCoordinator.production(
                runtimeServices: runtimeServices,
                selector: vaultSelector,
                keyCreator: vaultKeyCreator,
                selectionCreator: vaultSelector,
                journalStore: recoveryImportJournalStore,
                hasPendingCreation: {
                    try creationJournalStore.loadJournal() != nil
                        || pairingTransactionStore.load() != nil
                },
                transactionAuthority: pendingTransactionAuthority,
                fileReader: AtlasVaultRecoveryImportFileReader(),
                atomicFileSystem: atomicFileSystemClient,
                authorize: {
                    let flow = await host.currentFlowState()
                    guard
                        flow.mode == .lockedPublic,
                        await runtime.status() == .locked
                    else {
                        return false
                    }
                    let lifecycleStatus = await lifecycle.status()
                    guard
                        !lifecycleStatus.hasPendingGraceLock,
                        lifecycleStatus.failure == nil
                    else {
                        return false
                    }
                    switch lifecycleStatus.lastEvent {
                    case .didBecomeActive,
                         .protectedDataBecameAvailable:
                        return true
                    case .none,
                         .willResignActive,
                         .didEnterBackground,
                         .willTerminate,
                         .protectedDataBecameUnavailable:
                        return false
                    }
                },
                pendingImportDidChange: { pending in
                    await recoveryImportAvailability.setPendingImport(
                        pending
                    )
                }
            )
        let recoveryImportOwner =
            AtlasVaultRecoveryImportPresentationOwner(
                coordinator: recoveryImportCoordinator,
                continueToUnlock: {
                    _ = await host.requestUnlockPanel()
                }
            )
        let recoveryImportActions = AtlasVaultRecoveryImportActions(
            present: { [weak recoveryImportOwner] in
                await recoveryImportOwner?.present()
            },
            dismiss: { [weak recoveryImportOwner] in
                recoveryImportOwner?.dismiss()
            },
            prepareImport: { [weak recoveryImportOwner] url in
                await recoveryImportOwner?.prepareImport(from: url)
            },
            restore: { [weak recoveryImportOwner] secret, confirmed in
                await recoveryImportOwner?.restore(
                    secret: secret,
                    confirmed: confirmed
                )
            },
            resume: { [weak recoveryImportOwner] url, secret in
                await recoveryImportOwner?.resume(
                    from: url,
                    secret: secret
                )
            },
            finish: { [weak recoveryImportOwner] url, secret in
                await recoveryImportOwner?.finishCommittedImport(
                    from: url,
                    secret: secret
                )
            },
            reset: { [weak recoveryImportOwner] confirmed in
                await recoveryImportOwner?.resetPendingImport(
                    confirmed: confirmed
                )
            },
            pause: { [weak recoveryImportOwner] in
                await recoveryImportOwner?.pause()
            },
            claimPresentation: { [weak recoveryImportOwner] claim in
                recoveryImportOwner?.claimPresentation(claim) ?? false
            },
            releasePresentation: { [weak recoveryImportOwner] claim in
                recoveryImportOwner?.releasePresentation(claim) ?? false
            },
            ownsPresentation: { [weak recoveryImportOwner] claim in
                recoveryImportOwner?.ownsPresentation(claim) ?? false
            }
        )
        let recoveryImportContext = AtlasVaultRecoveryImportContext(
            owner: recoveryImportOwner,
            actions: recoveryImportActions,
            availability: recoveryImportAvailability
        )
        let creationCoordinator =
            AtlasLocalVaultCreationCoordinator.production(
                runtimeServices: runtimeServices,
                selectionRegistry: vaultSelector,
                journalStore: creationJournalStore
            )
        let guardedCreationCoordinator =
            AtlasPendingRecoveryImportCreationGate(
                creator: creationCoordinator,
                hasPendingImport: {
                    try recoveryImportJournalStore.loadJournal() != nil
                        || pairingTransactionStore.load() != nil
                },
                transactionAuthority: pendingTransactionAuthority
            )
        let creationOwner =
            AtlasLocalVaultCreationPresentationOwner(
                creator: guardedCreationCoordinator,
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
            },
            claimPresentation: { [weak creationOwner] claim in
                creationOwner?.claimPresentation(claim) ?? false
            },
            releasePresentation: { [weak creationOwner] claim in
                creationOwner?.releasePresentation(claim) ?? false
            },
            ownsPresentation: { [weak creationOwner] claim in
                creationOwner?.ownsPresentation(claim) ?? false
            }
        )
        let creationContext = AtlasLocalVaultCreationContext(
            owner: creationOwner,
            actions: creationActions
        )
        let recoveryCoordinator =
            AtlasVaultRecoveryExportCoordinator.production(
                runtimeServices: runtimeServices,
                selector: vaultSelector,
                journalStore: recoveryJournalStore,
                authorize: {
                    let flow = await host.currentFlowState()
                    guard
                        flow.mode == .unlockedTransition,
                        await runtime.status() == .unlocked
                    else {
                        return false
                    }
                    let lifecycleStatus = await lifecycle.status()
                    guard
                        !lifecycleStatus.hasPendingGraceLock,
                        lifecycleStatus.failure == nil
                    else {
                        return false
                    }
                    switch lifecycleStatus.lastEvent {
                    case .didBecomeActive,
                         .protectedDataBecameAvailable:
                        return true
                    case .none,
                         .willResignActive,
                         .didEnterBackground,
                         .willTerminate,
                         .protectedDataBecameUnavailable:
                        return false
                    }
                }
            )
        let recoveryOwner =
            AtlasVaultRecoveryExportPresentationOwner(
                coordinator: recoveryCoordinator
            )
        let recoveryActions = AtlasVaultRecoveryExportActions(
            present: { [weak recoveryOwner] in
                recoveryOwner?.present()
            },
            dismiss: { [weak recoveryOwner] in
                recoveryOwner?.dismiss()
            },
            generate: { [weak recoveryOwner] in
                await recoveryOwner?.generate()
            },
            confirm: { [weak recoveryOwner] secret in
                await recoveryOwner?.confirm(secret: secret)
            },
            resume: { [weak recoveryOwner] secret in
                await recoveryOwner?.resume(secret: secret)
            },
            exportDidFinish: { [weak recoveryOwner] success in
                await recoveryOwner?.exportDidFinish(success: success)
            },
            resetPendingSetup: {
                [weak recoveryOwner] confirmed in
                await recoveryOwner?.resetPendingSetup(
                    confirmed: confirmed
                )
            },
            pause: { [weak recoveryOwner] in
                await recoveryOwner?.pause()
            },
            claimPresentation: { [weak recoveryOwner] claim in
                recoveryOwner?.claimPresentation(claim) ?? false
            },
            releasePresentation: { [weak recoveryOwner] claim in
                recoveryOwner?.releasePresentation(claim) ?? false
            },
            ownsPresentation: { [weak recoveryOwner] claim in
                recoveryOwner?.ownsPresentation(claim) ?? false
            }
        )
        let recoveryExportContext = AtlasVaultRecoveryExportContext(
            owner: recoveryOwner,
            actions: recoveryActions
        )
        let deviceIdentityStore = AtlasKeychainDeviceIdentityStore(
            client: keychainClient
        )
        let trustedDeviceRegistryStore =
            AtlasKeychainTrustedDeviceRegistryStore(
                client: keychainClient
            )
        let pairingReplayStore = AtlasKeychainPairingReplayStore(
            client: keychainClient
        )
        let pairingArtifactStageStore:
            @Sendable () throws -> AtlasVaultPairingArtifactStageStore = {
                let root = try rootProvider.rootDirectory()
                    .appendingPathComponent(
                        AtlasInjectedRootVaultPathLocator.atlasDirectoryName,
                        isDirectory: true
                    )
                    .appendingPathComponent("Pairing", isDirectory: true)
                    .appendingPathComponent("v1", isDirectory: true)
                    .appendingPathComponent("Staging", isDirectory: true)
                return try AtlasVaultPairingArtifactStageStore(root: root)
            }
        let pairingCoordinator = AtlasVaultTrustedPairingCoordinator(
            environment: AtlasVaultTrustedPairingEnvironment(
                loadIdentity: {
                    guard var data = try deviceIdentityStore
                        .loadPrimaryIdentity() else {
                        return nil
                    }
                    defer { data.resetBytes(in: 0..<data.count) }
                    return try AtlasVaultDeviceIdentitySecret
                        .decodeStrict(data)
                        .loadIdentity()
                },
                createIdentity: {
                    let identity = try AtlasVaultDeviceIdentity.generate()
                    var data = try identity.secretBundle().canonicalData()
                    defer { data.resetBytes(in: 0..<data.count) }
                    try deviceIdentityStore.createPrimaryIdentity(data)
                    return identity
                },
                loadTransaction: {
                    try pairingTransactionStore.load()
                },
                createTransaction: { transaction in
                    try pairingTransactionStore.create(transaction)
                },
                replaceTransaction: { transaction, expectedSHA256 in
                    try pairingTransactionStore.replace(
                        transaction,
                        expectedSHA256: expectedSHA256
                    )
                },
                deleteTransaction: { expectedSHA256 in
                    try pairingTransactionStore.delete(
                        expectedSHA256: expectedSHA256
                    )
                },
                loadArtifact: { kind in
                    try pairingArtifactStageStore().read(kind: kind)
                },
                createArtifact: { artifact in
                    try pairingArtifactStageStore().create(artifact)
                },
                deleteArtifact: { kind, expectedSHA256 in
                    try pairingArtifactStageStore().delete(
                        kind: kind,
                        expectedSHA256: expectedSHA256
                    )
                },
                loadRegistry: {
                    try trustedDeviceRegistryStore.load()
                },
                createRegistry: { registry in
                    try trustedDeviceRegistryStore.create(registry)
                },
                replaceRegistry: { registry, expectedSHA256 in
                    try trustedDeviceRegistryStore.replace(
                        registry,
                        expectedSHA256: expectedSHA256
                    )
                },
                loadReplay: {
                    try pairingReplayStore.load()
                },
                createReplay: { replay in
                    try pairingReplayStore.create(replay)
                },
                replaceReplay: { replay, expectedSHA256 in
                    try pairingReplayStore.replace(
                        replay,
                        expectedSHA256: expectedSHA256
                    )
                },
                activeVault: {
                    guard
                        case let .selected(selected) =
                            try await vaultSelector.selectVaultID(),
                        await runtime.status() == .unlocked,
                        var key = try runtimeServices.keyStore.loadVaultKey(
                            for: selected.vaultID
                        )
                    else {
                        return nil
                    }
                    defer { key.resetBytes(in: 0..<key.count) }
                    let root = try runtimeServices.rootDirectoryProvider
                        .rootDirectory()
                    let services = try runtimeServices.perVaultFactory
                        .makeServices(
                            rootURL: root,
                            vaultID: selected.vaultID
                        )
                    let session = try AtlasVaultUnlockedSession(
                        vaultID: selected.vaultID,
                        vaultKey: key
                    )
                    guard let store = try services.persistenceCoordinator
                        .loadEncryptedStore(for: session) else {
                        return nil
                    }
                    return try AtlasVaultPairingActiveVault(
                        vaultID: selected.vaultID,
                        store: store,
                        keyMaterial: key
                    )
                },
                cleanInstall: {
                    do {
                        switch try await vaultSelector.selectVaultID() {
                        case .selected:
                            return .existingVault
                        case .none:
                            break
                        }
                        guard await runtime.status() == .locked else {
                            return .unavailable
                        }
                        guard
                            try creationJournalStore.loadJournal() == nil,
                            try recoveryImportJournalStore.loadJournal() == nil
                        else {
                            return .recoveryRequired
                        }
                        async let savedSearches = apiClient.savedSearches()
                        async let trackerRecords = apiClient.trackerRecords()
                        guard
                            try await savedSearches.isEmpty,
                            try await trackerRecords.isEmpty
                        else {
                            return .migrationRequired
                        }
                        return .clean
                    } catch {
                        return .unavailable
                    }
                },
                loadStore: { vaultID, key in
                    let root = try runtimeServices.rootDirectoryProvider
                        .rootDirectory()
                    let services = try runtimeServices.perVaultFactory
                        .makeServices(rootURL: root, vaultID: vaultID)
                    let session = try AtlasVaultUnlockedSession(
                        vaultID: vaultID,
                        vaultKey: key
                    )
                    return try services.persistenceCoordinator
                        .loadEncryptedStore(for: session)
                },
                createStore: { store, vaultID, key in
                    let root = try runtimeServices.rootDirectoryProvider
                        .rootDirectory()
                    let services = try runtimeServices.perVaultFactory
                        .makeServices(rootURL: root, vaultID: vaultID)
                    let session = try AtlasVaultUnlockedSession(
                        vaultID: vaultID,
                        vaultKey: key
                    )
                    _ = try services.persistenceCoordinator
                        .saveEncryptedStoreAtomically(
                            store,
                            for: session,
                            overwrite: false
                        )
                },
                deleteStore: { vaultID in
                    let root = try runtimeServices.rootDirectoryProvider
                        .rootDirectory()
                    let services = try runtimeServices.perVaultFactory
                        .makeServices(rootURL: root, vaultID: vaultID)
                    let url = try services.pathLocator.localStoreURL(
                        vaultID: vaultID
                    )
                    try atomicFileSystemClient.removeItemIfExists(at: url)
                    try atomicFileSystemClient.synchronizeDirectory(
                        at: url.deletingLastPathComponent()
                    )
                },
                loadStoredKey: { vaultID in
                    try runtimeServices.keyStore.loadVaultKey(for: vaultID)
                },
                createStoredKey: { key, vaultID in
                    try vaultKeyCreator.createVaultKey(key, for: vaultID)
                },
                deleteStoredKey: { vaultID in
                    try runtimeServices.keyStore.deleteVaultKey(for: vaultID)
                },
                selectedVault: {
                    switch try await vaultSelector.selectVaultID() {
                    case let .selected(selected): selected.vaultID
                    case .none: nil
                    }
                },
                createSelection: { vaultID in
                    try await vaultSelector.createSelection(
                        AtlasSelectedVaultID(validating: vaultID)
                    )
                },
                activate: { vaultID, key in
                    try await runtime.activate(
                        AtlasVaultRuntimeActivationRequest(
                            vaultID: vaultID,
                            suppliedVaultKey: key
                        )
                    )
                    return await runtime.status() == .unlocked
                },
                validateProjection: { store, vaultID, key, active in
                    let root = try runtimeServices.rootDirectoryProvider
                        .rootDirectory()
                    let services = try runtimeServices.perVaultFactory
                        .makeServices(rootURL: root, vaultID: vaultID)
                    let session = try AtlasVaultUnlockedSession(
                        vaultID: vaultID,
                        vaultKey: key
                    )
                    let projection = try services.recordHydrator.hydrate(
                        records: store.records,
                        session: session
                    )
                    guard active else { return true }
                    return try await runtime.privateState().state
                        == projection
                },
                transactionAdmission: { operation in
                    try await pendingTransactionAuthority.perform(operation)
                },
                authorizeSensitiveMutation: authorizeSensitivePairingMutation
            )
        )
        let pairingOwner = AtlasVaultTrustedPairingPresentationOwner(
            coordinator: pairingCoordinator
        )
        let pairingContext = AtlasVaultTrustedPairingContext(
            owner: pairingOwner
        )
        let lifecycleForwarder = AtlasVaultProductionLifecycleForwarder(
            source: lifecycleEvents,
            host: host,
            eventObserver: { event in
                switch event {
                case .willTerminate:
                    await pairingOwner.stopAndDrain()
                    await savedSearchHandoffCoordinator.stop()
                    await savedSearchOwner
                        .stopAndDrainPrivateSession()
                    await recoveryOwner.stop()
                    await recoveryImportOwner.stop()
                case .willResignActive,
                     .didEnterBackground,
                     .protectedDataBecameUnavailable:
                    await pairingOwner.clearSensitiveInput()
                    await savedSearchOwner
                        .hidePrivatePresentation()
                    await recoveryOwner.dismissForUnsafeLifecycle()
                    await recoveryImportOwner
                        .dismissForUnsafeLifecycle()
                case .didBecomeActive,
                     .protectedDataBecameAvailable:
                    break
                }
            }
        )
        return AtlasVaultProductionCompositionHarness(
            host: host,
            presentationOwner: presentationOwner,
            lifecycleForwarder: lifecycleForwarder,
            publicSearchLimit: configuration.publicSearchLimit,
            unlockTimeout: configuration.unlockTimeout,
            creationContext: creationContext,
            recoveryExportContext: recoveryExportContext,
            recoveryImportContext: recoveryImportContext,
            savedSearchContext: savedSearchContext,
            pairingContext: pairingContext,
            savedSearchHandoffCoordinator:
                savedSearchHandoffCoordinator
        )
    }
}
