import Foundation

protocol AtlasVaultLifecycleRuntimeControlling: Sendable {
    func status() async -> AtlasVaultRuntimeStatus
    func lock() async
    func cancelActivationIfInProgress() async -> Bool
}

extension AtlasVaultRuntimeFacade: AtlasVaultLifecycleRuntimeControlling {}

public protocol AtlasVaultLifecycleClock: Sendable {
    /// A monotonic offset whose origin is shared with the injected sleeper.
    func now() async -> Duration
}

public protocol AtlasVaultLifecycleSleeper: Sendable {
    func sleep(until deadline: Duration) async throws
}

public enum AtlasVaultLifecycleEvent:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case didBecomeActive
    case willResignActive
    case didEnterBackground
    case willTerminate
    case protectedDataBecameUnavailable
    case protectedDataBecameAvailable

    public var description: String {
        switch self {
        case .didBecomeActive: "didBecomeActive"
        case .willResignActive: "willResignActive"
        case .didEnterBackground: "didEnterBackground"
        case .willTerminate: "willTerminate"
        case .protectedDataBecameUnavailable: "protectedDataBecameUnavailable"
        case .protectedDataBecameAvailable: "protectedDataBecameAvailable"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultLifecycleLockPolicy:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case immediate
    case afterGracePeriod(Duration, cancelOnActive: Bool)

    public var description: String {
        switch self {
        case .immediate:
            "immediate"
        case let .afterGracePeriod(_, cancelOnActive):
            cancelOnActive
                ? "afterGracePeriod(cancelOnActive)"
                : "afterGracePeriod"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultLifecycleFailure:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case graceTimerUnavailable

    public var description: String {
        "graceTimerUnavailable"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultLifecycleStatus:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let lastEvent: AtlasVaultLifecycleEvent?
    public let hasPendingGraceLock: Bool
    public let failure: AtlasVaultLifecycleFailure?

    public var description: String {
        "AtlasVaultLifecycleStatus(event: <redacted>, timer: <redacted>, failure: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultLifecycleCoordinating: Sendable {
    func handle(_ event: AtlasVaultLifecycleEvent) async
    func status() async -> AtlasVaultLifecycleStatus
}

public actor AtlasVaultLifecycleCoordinator:
    AtlasVaultLifecycleCoordinating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let runtime: any AtlasVaultLifecycleRuntimeControlling
    private let lockPolicy: AtlasVaultLifecycleLockPolicy
    private let clock: any AtlasVaultLifecycleClock
    private let sleeper: any AtlasVaultLifecycleSleeper
    private let lockOnInactive: Bool

    private var lastEvent: AtlasVaultLifecycleEvent?
    private var lastFailure: AtlasVaultLifecycleFailure?
    private var lifecycleGeneration: UInt64 = 0
    private var pendingGraceGeneration: UInt64?
    private var pendingGraceTask: Task<Void, Never>?
    private var isTerminated = false

    public init(
        runtimeFacade: AtlasVaultRuntimeFacade,
        lockPolicy: AtlasVaultLifecycleLockPolicy,
        clock: any AtlasVaultLifecycleClock,
        sleeper: any AtlasVaultLifecycleSleeper,
        lockOnInactive: Bool = false
    ) {
        self.runtime = runtimeFacade
        self.lockPolicy = lockPolicy
        self.clock = clock
        self.sleeper = sleeper
        self.lockOnInactive = lockOnInactive
    }

    init(
        runtime: any AtlasVaultLifecycleRuntimeControlling,
        lockPolicy: AtlasVaultLifecycleLockPolicy,
        clock: any AtlasVaultLifecycleClock,
        sleeper: any AtlasVaultLifecycleSleeper,
        lockOnInactive: Bool = false
    ) {
        self.runtime = runtime
        self.lockPolicy = lockPolicy
        self.clock = clock
        self.sleeper = sleeper
        self.lockOnInactive = lockOnInactive
    }

    public func handle(_ event: AtlasVaultLifecycleEvent) async {
        guard !isTerminated,
              lastEvent != event else {
            return
        }
        lastEvent = event
        lastFailure = nil

        switch event {
        case .didBecomeActive:
            if lockPolicy.cancelsGraceLockOnActive {
                invalidatePendingGraceLock()
            }
        case .willResignActive:
            _ = await runtime.cancelActivationIfInProgress()
            if lockOnInactive {
                invalidatePendingGraceLock()
                await runtime.lock()
            }
        case .didEnterBackground:
            await handleBackground()
        case .willTerminate:
            isTerminated = true
            invalidatePendingGraceLock()
            await runtime.lock()
        case .protectedDataBecameUnavailable:
            invalidatePendingGraceLock()
            await runtime.lock()
        case .protectedDataBecameAvailable:
            break
        }
    }

    public func status() async -> AtlasVaultLifecycleStatus {
        AtlasVaultLifecycleStatus(
            lastEvent: lastEvent,
            hasPendingGraceLock: pendingGraceGeneration != nil,
            failure: lastFailure
        )
    }

    public nonisolated var description: String {
        "AtlasVaultLifecycleCoordinator(state: <redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private func handleBackground() async {
        switch lockPolicy {
        case .immediate:
            invalidatePendingGraceLock()
            await runtime.lock()
        case let .afterGracePeriod(duration, _):
            guard duration > .zero else {
                invalidatePendingGraceLock()
                await runtime.lock()
                return
            }
            let schedulingGeneration = lifecycleGeneration
            let cancelledActivation = await runtime.cancelActivationIfInProgress()
            guard lifecycleGeneration == schedulingGeneration,
                  !isTerminated else {
                return
            }
            if cancelledActivation {
                invalidatePendingGraceLock()
                return
            }

            let runtimeStatus = await runtime.status()
            guard lifecycleGeneration == schedulingGeneration,
                  !isTerminated else {
                return
            }
            switch runtimeStatus {
            case .unlocked, .saving:
                await scheduleGraceLock(after: duration)
            case .activating:
                let cancelledActivation = await runtime.cancelActivationIfInProgress()
                guard lifecycleGeneration == schedulingGeneration,
                      !isTerminated else {
                    return
                }
                invalidatePendingGraceLock()
                if !cancelledActivation {
                    await runtime.lock()
                }
            case .locked, .locking, .failed:
                invalidatePendingGraceLock()
            }
        }
    }

    private func scheduleGraceLock(after duration: Duration) async {
        guard duration > .zero else {
            invalidatePendingGraceLock()
            await runtime.lock()
            return
        }

        invalidatePendingGraceLock()
        lifecycleGeneration &+= 1
        let generation = lifecycleGeneration
        pendingGraceGeneration = generation
        let now = await clock.now()
        guard pendingGraceGeneration == generation else {
            return
        }

        let deadline = now + duration
        let sleeper = sleeper
        pendingGraceTask = Task { [self] in
            do {
                try await sleeper.sleep(until: deadline)
            } catch is CancellationError {
                return
            } catch {
                await graceTimerFailed(generation: generation)
                return
            }
            guard !Task.isCancelled else {
                return
            }
            await graceTimerElapsed(generation: generation)
        }
    }

    private func graceTimerElapsed(generation: UInt64) async {
        guard pendingGraceGeneration == generation else {
            return
        }
        pendingGraceGeneration = nil
        pendingGraceTask = nil
        await runtime.lock()
    }

    private func graceTimerFailed(generation: UInt64) async {
        guard pendingGraceGeneration == generation else {
            return
        }
        pendingGraceGeneration = nil
        pendingGraceTask = nil
        lastFailure = .graceTimerUnavailable
        await runtime.lock()
    }

    private func invalidatePendingGraceLock() {
        lifecycleGeneration &+= 1
        pendingGraceGeneration = nil
        pendingGraceTask?.cancel()
        pendingGraceTask = nil
    }
}

private extension AtlasVaultLifecycleLockPolicy {
    var cancelsGraceLockOnActive: Bool {
        switch self {
        case .immediate:
            false
        case let .afterGracePeriod(_, cancelOnActive):
            cancelOnActive
        }
    }
}
