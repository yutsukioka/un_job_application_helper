import Foundation

protocol AtlasVaultPrivateStateStoring: Sendable {
    func stage(
        _ state: AtlasVaultHydratedState,
        generation: AtlasVaultPrivateStateGeneration
    ) async throws
    func commit(generation: AtlasVaultPrivateStateGeneration) async throws
    func snapshot(
        generation: AtlasVaultPrivateStateGeneration
    ) async throws -> AtlasVaultHydratedState
    func clear(generation: AtlasVaultPrivateStateGeneration) async
    func clearAll() async
}

struct AtlasVaultPrivateStateGeneration:
    Hashable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let token = UUID()

    var description: String {
        "AtlasVaultPrivateStateGeneration(<redacted>)"
    }

    var debugDescription: String {
        description
    }
}

enum AtlasVaultPrivateStateStoreError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case invalidGeneration
    case noStagedState
    case staleGeneration
    case unavailable

    var description: String {
        switch self {
        case .invalidGeneration: "invalidGeneration"
        case .noStagedState: "noStagedState"
        case .staleGeneration: "staleGeneration"
        case .unavailable: "unavailable"
        }
    }

    var debugDescription: String {
        description
    }
}

actor AtlasVaultPrivateStateStore:
    AtlasVaultPrivateStateStoring,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum Storage {
        case empty
        case staged(AtlasVaultPrivateStateGeneration, AtlasVaultHydratedState)
        case active(AtlasVaultPrivateStateGeneration, AtlasVaultHydratedState)
    }

    private var storage: Storage = .empty

    func stage(
        _ state: AtlasVaultHydratedState,
        generation: AtlasVaultPrivateStateGeneration
    ) throws {
        switch storage {
        case .empty:
            storage = .staged(generation, state)
        case let .staged(existingGeneration, _),
             let .active(existingGeneration, _):
            throw existingGeneration == generation
                ? AtlasVaultPrivateStateStoreError.invalidGeneration
                : AtlasVaultPrivateStateStoreError.staleGeneration
        }
    }

    func commit(generation: AtlasVaultPrivateStateGeneration) throws {
        switch storage {
        case .empty:
            throw AtlasVaultPrivateStateStoreError.noStagedState
        case let .staged(existingGeneration, state):
            guard existingGeneration == generation else {
                throw AtlasVaultPrivateStateStoreError.staleGeneration
            }
            storage = .active(generation, state)
        case let .active(existingGeneration, _):
            guard existingGeneration == generation else {
                throw AtlasVaultPrivateStateStoreError.staleGeneration
            }
        }
    }

    func snapshot(
        generation: AtlasVaultPrivateStateGeneration
    ) throws -> AtlasVaultHydratedState {
        switch storage {
        case .empty:
            throw AtlasVaultPrivateStateStoreError.unavailable
        case let .staged(existingGeneration, _):
            guard existingGeneration == generation else {
                throw AtlasVaultPrivateStateStoreError.staleGeneration
            }
            throw AtlasVaultPrivateStateStoreError.unavailable
        case let .active(existingGeneration, state):
            guard existingGeneration == generation else {
                throw AtlasVaultPrivateStateStoreError.staleGeneration
            }
            return state
        }
    }

    func clear(generation: AtlasVaultPrivateStateGeneration) {
        switch storage {
        case .empty:
            return
        case let .staged(existingGeneration, _),
             let .active(existingGeneration, _):
            guard existingGeneration == generation else {
                return
            }
            storage = .empty
        }
    }

    func clearAll() {
        storage = .empty
    }

    var isEmpty: Bool {
        if case .empty = storage {
            return true
        }
        return false
    }

    nonisolated var description: String {
        "AtlasVaultPrivateStateStore(state: <redacted>)"
    }

    nonisolated var debugDescription: String {
        description
    }
}
