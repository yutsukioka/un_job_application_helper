import Foundation

struct AtlasIOSSceneIdentifier:
    Hashable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let rawValue: String

    init(_ rawValue: String) {
        self.rawValue = rawValue
    }

    var description: String {
        "AtlasIOSSceneIdentifier(<redacted>)"
    }

    var debugDescription: String {
        description
    }
}

enum AtlasIOSSceneLifecycleState: Equatable, Sendable {
    case foregroundActive
    case foregroundInactive
    case background
    case unattached
}

enum AtlasIOSApplicationLifecycleState: Equatable, Sendable {
    case active
    case inactive
    case background
}

struct AtlasIOSLifecycleBootstrap: Equatable, Sendable {
    let scenes: [AtlasIOSSceneIdentifier: AtlasIOSSceneLifecycleState]
    let applicationState: AtlasIOSApplicationLifecycleState
    let protectedDataAvailable: Bool
}

enum AtlasIOSLifecycleSignal:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case sceneConnected(
        AtlasIOSSceneIdentifier,
        state: AtlasIOSSceneLifecycleState
    )
    case sceneDisconnected(AtlasIOSSceneIdentifier)
    case sceneWillEnterForeground(AtlasIOSSceneIdentifier)
    case sceneDidBecomeActive(AtlasIOSSceneIdentifier)
    case sceneWillResignActive(AtlasIOSSceneIdentifier)
    case sceneDidEnterBackground(AtlasIOSSceneIdentifier)
    case applicationDidBecomeActive
    case applicationWillResignActive
    case applicationDidEnterBackground
    case protectedDataBecameAvailable
    case protectedDataBecameUnavailable
    case willTerminate

    var description: String {
        "AtlasIOSLifecycleSignal(<redacted>)"
    }

    var debugDescription: String {
        description
    }
}

struct AtlasIOSLifecycleAggregator:
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum AggregatePhase: Equatable {
        case active
        case inactive
        case background
    }

    private var scenes: [
        AtlasIOSSceneIdentifier: AtlasIOSSceneLifecycleState
    ] = [:]
    private var applicationState: AtlasIOSApplicationLifecycleState =
        .background
    private var protectedDataAvailable: Bool?
    private var aggregatePhase: AggregatePhase?
    private var hasBootstrapped = false
    private var isTerminal = false

    var description: String {
        "AtlasIOSLifecycleAggregator(<redacted>)"
    }

    var debugDescription: String {
        description
    }

    mutating func bootstrap(
        _ bootstrap: AtlasIOSLifecycleBootstrap
    ) -> [AtlasVaultLifecycleEvent] {
        guard !hasBootstrapped, !isTerminal else {
            return []
        }

        hasBootstrapped = true
        scenes = bootstrap.scenes
        applicationState = bootstrap.applicationState
        protectedDataAvailable = bootstrap.protectedDataAvailable
        let initialPhase = currentAggregatePhase()
        aggregatePhase = initialPhase

        return [
            bootstrap.protectedDataAvailable
                ? .protectedDataBecameAvailable
                : .protectedDataBecameUnavailable,
            lifecycleEvent(forInitialPhase: initialPhase),
        ]
    }

    mutating func consume(
        _ signal: AtlasIOSLifecycleSignal
    ) -> [AtlasVaultLifecycleEvent] {
        guard !isTerminal else {
            return []
        }

        switch signal {
        case let .sceneConnected(identifier, state):
            return updateAggregate { aggregator in
                aggregator.scenes[identifier] = state
            }
        case let .sceneDisconnected(identifier):
            return updateAggregate { aggregator in
                aggregator.scenes.removeValue(forKey: identifier)
            }
        case let .sceneWillEnterForeground(identifier):
            return updateAggregate { aggregator in
                guard aggregator.scenes[identifier] != nil else {
                    return
                }
                aggregator.scenes[identifier] = .foregroundInactive
            }
        case let .sceneDidBecomeActive(identifier):
            return updateAggregate { aggregator in
                guard aggregator.scenes[identifier] != nil else {
                    return
                }
                aggregator.scenes[identifier] = .foregroundActive
            }
        case let .sceneWillResignActive(identifier):
            return updateAggregate { aggregator in
                guard aggregator.scenes[identifier] != nil else {
                    return
                }
                aggregator.scenes[identifier] = .foregroundInactive
            }
        case let .sceneDidEnterBackground(identifier):
            return updateAggregate { aggregator in
                guard aggregator.scenes[identifier] != nil else {
                    return
                }
                aggregator.scenes[identifier] = .background
            }
        case .applicationDidBecomeActive:
            return updateAggregate { aggregator in
                aggregator.applicationState = .active
            }
        case .applicationWillResignActive:
            return updateAggregate { aggregator in
                aggregator.applicationState = .inactive
            }
        case .applicationDidEnterBackground:
            return updateAggregate { aggregator in
                aggregator.applicationState = .background
            }
        case .protectedDataBecameAvailable:
            return updateProtectedData(available: true)
        case .protectedDataBecameUnavailable:
            return updateProtectedData(available: false)
        case .willTerminate:
            isTerminal = true
            scenes.removeAll()
            return [.willTerminate]
        }
    }

    private mutating func updateAggregate(
        _ update: (inout AtlasIOSLifecycleAggregator) -> Void
    ) -> [AtlasVaultLifecycleEvent] {
        let previous = aggregatePhase ?? currentAggregatePhase()
        update(&self)
        let current = currentAggregatePhase()
        aggregatePhase = current
        guard current != previous,
              let event = lifecycleEvent(from: previous, to: current) else {
            return []
        }
        return [event]
    }

    private mutating func updateProtectedData(
        available: Bool
    ) -> [AtlasVaultLifecycleEvent] {
        guard protectedDataAvailable != available else {
            return []
        }
        protectedDataAvailable = available
        return [
            available
                ? .protectedDataBecameAvailable
                : .protectedDataBecameUnavailable,
        ]
    }

    private func currentAggregatePhase() -> AggregatePhase {
        if scenes.isEmpty {
            switch applicationState {
            case .active:
                return .active
            case .inactive:
                return .inactive
            case .background:
                return .background
            }
        }

        if scenes.values.contains(.foregroundActive) {
            return .active
        }
        if scenes.values.contains(.foregroundInactive) {
            return .inactive
        }
        return .background
    }

    private func lifecycleEvent(
        forInitialPhase phase: AggregatePhase
    ) -> AtlasVaultLifecycleEvent {
        switch phase {
        case .active:
            .didBecomeActive
        case .inactive:
            .willResignActive
        case .background:
            .didEnterBackground
        }
    }

    private func lifecycleEvent(
        from previous: AggregatePhase,
        to current: AggregatePhase
    ) -> AtlasVaultLifecycleEvent? {
        switch (previous, current) {
        case (.inactive, .active), (.background, .active):
            .didBecomeActive
        case (.active, .inactive):
            .willResignActive
        case (.active, .background), (.inactive, .background):
            .didEnterBackground
        case (.background, .inactive):
            nil
        case (.inactive, .inactive), (.active, .active),
             (.background, .background):
            nil
        }
    }
}
