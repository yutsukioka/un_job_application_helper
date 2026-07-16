import Foundation

public protocol AtlasVaultRuntimeFacading: Sendable {
    func status() async -> AtlasVaultRuntimeStatus
    func activate(_ request: AtlasVaultRuntimeActivationRequest) async throws
    func lock() async
    func apply(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async throws -> AtlasVaultSaveOutcome
}

protocol AtlasVaultPrivateStateReading: Sendable {
    func privateState() async throws -> AtlasVaultPrivateStateSnapshot
}

public enum AtlasVaultRuntimeFailure:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case activation(AtlasVaultActivationFailure)

    public var description: String {
        switch self {
        case let .activation(failure): "activation(\(failure))"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultRuntimeStatus:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case locked
    case activating
    case locking
    case unlocked
    case saving
    case failed(AtlasVaultRuntimeFailure)

    public var description: String {
        switch self {
        case .locked: "locked"
        case .activating: "activating"
        case .locking: "locking"
        case .unlocked: "unlocked"
        case .saving: "saving"
        case let .failed(failure): "failed(\(failure))"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultSaveOutcome:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case committed
    case committedDurabilityUnconfirmed

    init(_ result: AtlasVaultAtomicWriteResult) {
        switch result.commitState {
        case .committed:
            self = .committed
        case .committedDurabilityUnconfirmed:
            self = .committedDurabilityUnconfirmed
        }
    }

    public var description: String {
        switch self {
        case .committed: "committed"
        case .committedDurabilityUnconfirmed: "committedDurabilityUnconfirmed"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultRuntimeFacadeError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case locked
    case alreadyUnlocked
    case operationInProgress
    case activationFailed(AtlasVaultActivationFailure)
    case sessionMismatch
    case saveFailed
    case privateStateUnavailable
    case cancelled
    case committedStateUnavailable(AtlasVaultSaveOutcome)

    public var description: String {
        switch self {
        case .locked: "locked"
        case .alreadyUnlocked: "alreadyUnlocked"
        case .operationInProgress: "operationInProgress"
        case let .activationFailed(failure): "activationFailed(\(failure))"
        case .sessionMismatch: "sessionMismatch"
        case .saveFailed: "saveFailed"
        case .privateStateUnavailable: "privateStateUnavailable"
        case .cancelled: "cancelled"
        case .committedStateUnavailable: "committedStateUnavailable"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultRuntimeActivationRequest:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    let vaultID: String
    private let suppliedVaultKey: Data?

    public init(
        vaultID: String,
        suppliedVaultKey: Data? = nil
    ) {
        self.vaultID = vaultID
        self.suppliedVaultKey = suppliedVaultKey
    }

    var suppliedVaultKeyValue: Data? {
        suppliedVaultKey
    }

    public var description: String {
        "AtlasVaultRuntimeActivationRequest(vault: <redacted>, key: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultRuntimeMutationRequest:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    let expectedVaultID: String
    let mutations: AtlasVaultMutationSet

    public init(
        expectedVaultID: String,
        mutations: AtlasVaultMutationSet
    ) {
        self.expectedVaultID = expectedVaultID
        self.mutations = mutations
    }

    public var description: String {
        "AtlasVaultRuntimeMutationRequest(vault: <redacted>, mutations: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

struct AtlasVaultPrivateStateSnapshot:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    let state: AtlasVaultHydratedState

    var description: String {
        "AtlasVaultPrivateStateSnapshot(state: <redacted>)"
    }

    var debugDescription: String {
        description
    }
}

struct AtlasVaultRuntimeFacadeEnvironment: Sendable {
    let activate: @Sendable (String, Data?) async throws -> Void
    let cancelActivation: @Sendable () async -> Bool
    let lock: @Sendable () async -> Void
    let privateState: @Sendable () async throws -> AtlasVaultHydratedState
    let save: @Sendable (
        AtlasVaultMutationSet,
        String
    ) async throws -> AtlasVaultAtomicWriteResult

    init(
        activate: @escaping @Sendable (String, Data?) async throws -> Void,
        cancelActivation: @escaping @Sendable () async -> Bool,
        lock: @escaping @Sendable () async -> Void,
        privateState: @escaping @Sendable () async throws -> AtlasVaultHydratedState,
        save: @escaping @Sendable (
            AtlasVaultMutationSet,
            String
        ) async throws -> AtlasVaultAtomicWriteResult
    ) {
        self.activate = activate
        self.cancelActivation = cancelActivation
        self.lock = lock
        self.privateState = privateState
        self.save = save
    }

    init(activationController: AtlasVaultActivationController) {
        self.init(
            activate: { vaultID, vaultKey in
                try await activationController.activate(
                    vaultID: vaultID,
                    suppliedVaultKey: vaultKey
                )
            },
            cancelActivation: {
                await activationController.cancelActivation()
            },
            lock: {
                await activationController.lock()
            },
            privateState: {
                try await activationController.privateStateSnapshot()
            },
            save: { mutations, expectedVaultID in
                try await activationController.saveRuntimeMutations(
                    mutations,
                    expectedVaultID: expectedVaultID
                )
            }
        )
    }
}

public actor AtlasVaultRuntimeFacade:
    AtlasVaultRuntimeFacading,
    AtlasVaultPrivateStateReading,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum OperationKind: Equatable {
        case activation
        case save
        case lock
    }

    private struct ActiveOperation: Equatable {
        let token: UInt64
        let kind: OperationKind
    }

    private let environment: AtlasVaultRuntimeFacadeEnvironment
    private var runtimeStatus: AtlasVaultRuntimeStatus = .locked
    private var operationEpoch: UInt64 = 0
    private var activeOperation: ActiveOperation?

    public init(activationController: AtlasVaultActivationController) {
        self.environment = AtlasVaultRuntimeFacadeEnvironment(
            activationController: activationController
        )
    }

    init(environment: AtlasVaultRuntimeFacadeEnvironment) {
        self.environment = environment
    }

    public static func production() -> AtlasVaultRuntimeFacade {
        runtimeServices(AtlasVaultRuntimeFactory.production())
    }

    public static func runtimeServices<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding
    >(
        _ services: AtlasVaultRuntimeServices<DirectoryPreparer, LocalStoreIO>
    ) -> AtlasVaultRuntimeFacade {
        let activationController = AtlasVaultActivationController(
            environment: AtlasVaultActivationEnvironment.runtimeServices(services)
        )
        return AtlasVaultRuntimeFacade(activationController: activationController)
    }

    public func status() async -> AtlasVaultRuntimeStatus {
        runtimeStatus
    }

    public func activate(
        _ request: AtlasVaultRuntimeActivationRequest
    ) async throws {
        switch runtimeStatus {
        case .locked, .failed:
            guard activeOperation == nil else {
                throw AtlasVaultRuntimeFacadeError.operationInProgress
            }
        case .unlocked:
            throw AtlasVaultRuntimeFacadeError.alreadyUnlocked
        case .activating, .locking, .saving:
            throw AtlasVaultRuntimeFacadeError.operationInProgress
        }
        guard !Task.isCancelled else {
            throw AtlasVaultRuntimeFacadeError.cancelled
        }

        let operation = begin(.activation, status: .activating)
        do {
            try await environment.activate(
                request.vaultID,
                request.suppliedVaultKeyValue
            )
        } catch {
            throw await finishActivationFailure(error, operation: operation)
        }

        guard !Task.isCancelled else {
            await cancelAndLock(operation: operation)
            throw AtlasVaultRuntimeFacadeError.cancelled
        }
        guard isCurrent(operation) else {
            throw AtlasVaultRuntimeFacadeError.cancelled
        }
        activeOperation = nil
        runtimeStatus = .unlocked
    }

    public func lock() async {
        let wasActivating = runtimeStatus == .activating
        let operation = begin(
            .lock,
            status: runtimeStatus == .locked ? .locked : .locking
        )
        if wasActivating {
            _ = await environment.cancelActivation()
        }
        await environment.lock()
        guard isCurrent(operation) else {
            return
        }
        activeOperation = nil
        runtimeStatus = .locked
    }

    func privateState() async throws -> AtlasVaultPrivateStateSnapshot {
        guard runtimeStatus == .unlocked,
              activeOperation == nil else {
            throw AtlasVaultRuntimeFacadeError.privateStateUnavailable
        }
        guard !Task.isCancelled else {
            throw AtlasVaultRuntimeFacadeError.cancelled
        }
        let epoch = operationEpoch
        let state: AtlasVaultHydratedState
        do {
            state = try await environment.privateState()
        } catch is CancellationError {
            throw AtlasVaultRuntimeFacadeError.cancelled
        } catch {
            guard operationEpoch == epoch,
                  runtimeStatus == .unlocked,
                  activeOperation == nil else {
                throw AtlasVaultRuntimeFacadeError.cancelled
            }
            throw AtlasVaultRuntimeFacadeError.privateStateUnavailable
        }
        guard operationEpoch == epoch,
              runtimeStatus == .unlocked,
              activeOperation == nil else {
            throw AtlasVaultRuntimeFacadeError.cancelled
        }
        return AtlasVaultPrivateStateSnapshot(state: state)
    }

    public func apply(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async throws -> AtlasVaultSaveOutcome {
        guard runtimeStatus == .unlocked else {
            switch runtimeStatus {
            case .locked, .locking, .failed:
                throw AtlasVaultRuntimeFacadeError.locked
            case .activating, .saving:
                throw AtlasVaultRuntimeFacadeError.operationInProgress
            case .unlocked:
                preconditionFailure("unreachable")
            }
        }
        guard activeOperation == nil else {
            throw AtlasVaultRuntimeFacadeError.operationInProgress
        }
        guard !Task.isCancelled else {
            throw AtlasVaultRuntimeFacadeError.cancelled
        }

        let operation = begin(.save, status: .saving)
        let result: AtlasVaultAtomicWriteResult
        do {
            result = try await environment.save(
                request.mutations,
                request.expectedVaultID
            )
        } catch let error as AtlasVaultActivatedOperationError {
            throw await finishSaveFailure(error, operation: operation)
        } catch is CancellationError {
            await cancelAndLock(operation: operation)
            throw AtlasVaultRuntimeFacadeError.cancelled
        } catch {
            guard isCurrent(operation) else {
                throw AtlasVaultRuntimeFacadeError.cancelled
            }
            activeOperation = nil
            runtimeStatus = .unlocked
            throw AtlasVaultRuntimeFacadeError.saveFailed
        }
        let outcome = AtlasVaultSaveOutcome(result)
        guard isCurrent(operation) else {
            return outcome
        }
        activeOperation = nil
        runtimeStatus = .unlocked
        return outcome
    }

    public nonisolated var description: String {
        "AtlasVaultRuntimeFacade(status: <redacted>, private: <redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private func begin(
        _ kind: OperationKind,
        status: AtlasVaultRuntimeStatus
    ) -> ActiveOperation {
        operationEpoch &+= 1
        let operation = ActiveOperation(token: operationEpoch, kind: kind)
        activeOperation = operation
        runtimeStatus = status
        return operation
    }

    private func isCurrent(_ operation: ActiveOperation) -> Bool {
        activeOperation == operation && operationEpoch == operation.token
    }

    private func finishActivationFailure(
        _ error: Error,
        operation: ActiveOperation
    ) async -> AtlasVaultRuntimeFacadeError {
        guard isCurrent(operation) else {
            return .cancelled
        }
        let failure = (error as? AtlasVaultActivationFailure) ?? .vaultUnavailable
        if failure == .cancelled || error is CancellationError {
            await cancelAndLock(operation: operation)
            return .cancelled
        }
        activeOperation = nil
        runtimeStatus = .failed(.activation(failure))
        return .activationFailed(failure)
    }

    private func finishSaveFailure(
        _ error: AtlasVaultActivatedOperationError,
        operation: ActiveOperation
    ) async -> AtlasVaultRuntimeFacadeError {
        guard isCurrent(operation) else {
            return .cancelled
        }
        switch error {
        case .locked:
            activeOperation = nil
            runtimeStatus = .locked
            return .locked
        case .sessionMismatch:
            activeOperation = nil
            runtimeStatus = .unlocked
            return .sessionMismatch
        case .saveUnavailable, .saveFailed:
            activeOperation = nil
            runtimeStatus = .unlocked
            return .saveFailed
        case .cancelled:
            await cancelAndLock(operation: operation)
            return .cancelled
        case let .committedStateUnavailable(result):
            await environment.lock()
            guard isCurrent(operation) else {
                return .cancelled
            }
            activeOperation = nil
            runtimeStatus = .locked
            return .committedStateUnavailable(AtlasVaultSaveOutcome(result))
        }
    }

    private func cancelAndLock(operation: ActiveOperation) async {
        guard isCurrent(operation) else {
            return
        }
        operationEpoch &+= 1
        let lockOperation = ActiveOperation(token: operationEpoch, kind: .lock)
        activeOperation = lockOperation
        runtimeStatus = .locking
        _ = await environment.cancelActivation()
        await environment.lock()
        guard isCurrent(lockOperation) else {
            return
        }
        activeOperation = nil
        runtimeStatus = .locked
    }
}
