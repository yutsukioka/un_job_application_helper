import Foundation

public protocol AtlasVaultUnlockPresentationControlling: Sendable {
    func currentState() async -> AtlasVaultUnlockPresentationState
    func select(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasVaultUnlockPresentationState
    func submit(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration?
    ) async -> AtlasVaultUnlockPresentationState
    func cancel() async -> AtlasVaultUnlockPresentationState
    func didDisappear() async -> AtlasVaultUnlockPresentationState
    func hostDidLock() async -> AtlasVaultUnlockPresentationState
}

public enum AtlasVaultUnlockPresentationStatus:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case locked
    case ready
    case methodUnavailable
    case activating
    case unlocked
    case failed
    case cancelled
    case timedOut
    case hostReconciliationRequired

    public var description: String {
        switch self {
        case .locked: "locked"
        case .ready: "ready"
        case .methodUnavailable: "methodUnavailable"
        case .activating: "activating"
        case .unlocked: "unlocked"
        case .failed: "failed"
        case .cancelled: "cancelled"
        case .timedOut: "timedOut"
        case .hostReconciliationRequired: "hostReconciliationRequired"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultUnlockPresentationState:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let capabilities: AtlasVaultUnlockCapabilities
    public let selectedMethod: AtlasVaultUnlockMethod?
    public let status: AtlasVaultUnlockPresentationStatus

    public init(
        capabilities: AtlasVaultUnlockCapabilities,
        selectedMethod: AtlasVaultUnlockMethod?,
        status: AtlasVaultUnlockPresentationStatus
    ) {
        self.capabilities = capabilities
        self.selectedMethod = selectedMethod
        self.status = status
    }

    public var description: String {
        "AtlasVaultUnlockPresentationState(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultUnlockSubmission:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case localKey
    case passphrase(any AtlasVaultSecretBuffer)
    case recoveryKey(any AtlasVaultSecretBuffer)

    public var description: String {
        "AtlasVaultUnlockSubmission(<redacted>)"
    }

    public var debugDescription: String {
        description
    }

    fileprivate var method: AtlasVaultUnlockMethod {
        switch self {
        case .localKey: .localKey
        case .passphrase: .passphrase
        case .recoveryKey: .recoveryKey
        }
    }

    fileprivate var requestInput: AtlasVaultUnlockInputSource {
        switch self {
        case .localKey:
            .localKey
        case let .passphrase(buffer):
            .passphrase(buffer)
        case let .recoveryKey(buffer):
            .recoveryKey(buffer)
        }
    }

    fileprivate func clearSecret() async {
        switch self {
        case .localKey:
            return
        case let .passphrase(buffer), let .recoveryKey(buffer):
            await buffer.clear()
        }
    }
}

public actor AtlasVaultUnlockPresentationController:
    AtlasVaultUnlockPresentationControlling,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private struct ActiveAttempt: Sendable {
        let authorization: UInt64
        let request: AtlasVaultUnlockRequest
    }

    private struct InvalidatedAttempt: Sendable {
        let authorization: UInt64
        let requiresHostReconciliationOnSuccess: Bool
    }

    private let vaultID: String
    private let capabilities: AtlasVaultUnlockCapabilities
    private let coordinator: any AtlasVaultUnlockRequestCoordinating
    private var selectedMethod: AtlasVaultUnlockMethod?
    private var status: AtlasVaultUnlockPresentationStatus = .locked
    private var authorization: UInt64 = 0
    private var activeAttempt: ActiveAttempt?
    private var invalidatedAttempt: InvalidatedAttempt?
    private var pendingCancellationAuthorization: UInt64?

    public init(
        vaultID: String,
        capabilities: AtlasVaultUnlockCapabilities,
        coordinator: any AtlasVaultUnlockRequestCoordinating
    ) {
        self.vaultID = vaultID
        self.capabilities = capabilities
        self.coordinator = coordinator
    }

    public func currentState() async -> AtlasVaultUnlockPresentationState {
        snapshot()
    }

    public func select(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasVaultUnlockPresentationState {
        guard
            pendingCancellationAuthorization == nil,
            invalidatedAttempt == nil
        else {
            return snapshot()
        }
        guard status != .unlocked, status != .hostReconciliationRequired else {
            return snapshot()
        }

        let isAvailable = method.map {
            capabilities.status(for: $0) == .available
        } ?? true
        let nextMethod = isAvailable ? method : nil
        let nextStatus: AtlasVaultUnlockPresentationStatus
        if method == nil {
            nextStatus = .locked
        } else if isAvailable {
            nextStatus = .ready
        } else {
            nextStatus = .methodUnavailable
        }

        guard activeAttempt != nil else {
            selectedMethod = nextMethod
            status = nextStatus
            return snapshot()
        }
        return await invalidateActiveAttempt(
            selectedMethod: nextMethod,
            status: nextStatus,
            requireHostReconciliationIfCommitted: true
        )
    }

    public func submit(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration? = nil
    ) async -> AtlasVaultUnlockPresentationState {
        let method = submission.method
        guard
            activeAttempt == nil,
            invalidatedAttempt == nil,
            pendingCancellationAuthorization == nil
        else {
            await submission.clearSecret()
            return snapshot()
        }
        guard status != .unlocked, status != .hostReconciliationRequired else {
            await submission.clearSecret()
            return snapshot()
        }
        guard capabilities.status(for: method) == .available else {
            await submission.clearSecret()
            selectedMethod = nil
            status = .methodUnavailable
            return snapshot()
        }
        guard selectedMethod == method else {
            await submission.clearSecret()
            status = .failed
            return snapshot()
        }

        authorization &+= 1
        let attemptAuthorization = authorization
        let request = AtlasVaultUnlockRequest(
            vaultID: vaultID,
            input: submission.requestInput,
            timeout: timeout
        )
        activeAttempt = ActiveAttempt(
            authorization: attemptAuthorization,
            request: request
        )
        status = .activating

        let result: Result<Void, AtlasVaultUnlockRequestError>
        do {
            try await coordinator.dispatch(request)
            result = .success(())
        } catch let error as AtlasVaultUnlockRequestError {
            result = .failure(error)
        } catch {
            result = .failure(.unlockFailed)
        }

        guard
            authorization == attemptAuthorization,
            activeAttempt?.authorization == attemptAuthorization
        else {
            publishInvalidatedCompletion(
                result,
                attemptAuthorization: attemptAuthorization
            )
            await submission.clearSecret()
            return snapshot()
        }
        activeAttempt = nil
        switch result {
        case .success:
            status = .unlocked
        case let .failure(error):
            status = Self.presentationStatus(for: error)
        }
        await submission.clearSecret()
        return snapshot()
    }

    public func cancel() async -> AtlasVaultUnlockPresentationState {
        guard status != .hostReconciliationRequired else {
            return snapshot()
        }
        guard pendingCancellationAuthorization == nil else {
            return snapshot()
        }
        guard invalidatedAttempt == nil else {
            return snapshot()
        }
        guard activeAttempt != nil else {
            authorization &+= 1
            status = status == .unlocked
                ? .hostReconciliationRequired
                : .cancelled
            return snapshot()
        }
        return await invalidateActiveAttempt(
            selectedMethod: selectedMethod,
            status: .cancelled,
            requireHostReconciliationIfCommitted: true
        )
    }

    public func didDisappear() async -> AtlasVaultUnlockPresentationState {
        guard status != .hostReconciliationRequired else {
            return snapshot()
        }
        guard pendingCancellationAuthorization == nil else {
            selectedMethod = nil
            status = .hostReconciliationRequired
            return snapshot()
        }
        guard invalidatedAttempt == nil else {
            selectedMethod = nil
            if status != .hostReconciliationRequired {
                status = .locked
            }
            return snapshot()
        }
        guard activeAttempt != nil else {
            authorization &+= 1
            selectedMethod = nil
            status = status == .unlocked
                ? .hostReconciliationRequired
                : .locked
            return snapshot()
        }
        return await invalidateActiveAttempt(
            selectedMethod: nil,
            status: .locked,
            requireHostReconciliationIfCommitted: true
        )
    }

    public func hostDidLock() async -> AtlasVaultUnlockPresentationState {
        authorization &+= 1
        invalidatedAttempt = nil
        selectedMethod = nil
        status = .locked

        guard pendingCancellationAuthorization == nil else {
            return snapshot()
        }
        guard let attempt = activeAttempt else {
            return snapshot()
        }
        activeAttempt = nil
        let cancellationAuthorization = authorization
        pendingCancellationAuthorization = cancellationAuthorization
        _ = await coordinator.cancel(attempt.request)
        if pendingCancellationAuthorization == cancellationAuthorization {
            pendingCancellationAuthorization = nil
        }
        return snapshot()
    }

    public nonisolated var description: String {
        "AtlasVaultUnlockPresentationController(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private func invalidateActiveAttempt(
        selectedMethod nextMethod: AtlasVaultUnlockMethod?,
        status nextStatus: AtlasVaultUnlockPresentationStatus,
        requireHostReconciliationIfCommitted: Bool
    ) async -> AtlasVaultUnlockPresentationState {
        guard let attempt = activeAttempt else {
            selectedMethod = nextMethod
            status = nextStatus
            return snapshot()
        }

        authorization &+= 1
        let cancellationAuthorization = authorization
        activeAttempt = nil
        invalidatedAttempt = InvalidatedAttempt(
            authorization: attempt.authorization,
            requiresHostReconciliationOnSuccess: requireHostReconciliationIfCommitted
        )
        pendingCancellationAuthorization = cancellationAuthorization
        selectedMethod = nextMethod
        status = nextStatus

        _ = await coordinator.cancel(attempt.request)
        guard pendingCancellationAuthorization == cancellationAuthorization else {
            return snapshot()
        }
        pendingCancellationAuthorization = nil
        guard authorization == cancellationAuthorization else {
            return snapshot()
        }
        return snapshot()
    }

    private func publishInvalidatedCompletion(
        _ result: Result<Void, AtlasVaultUnlockRequestError>,
        attemptAuthorization: UInt64
    ) {
        guard invalidatedAttempt?.authorization == attemptAuthorization else {
            return
        }
        let requiresHostReconciliation =
            invalidatedAttempt?.requiresHostReconciliationOnSuccess == true
        invalidatedAttempt = nil
        if case .success = result, requiresHostReconciliation {
            status = .hostReconciliationRequired
        }
    }

    private func snapshot() -> AtlasVaultUnlockPresentationState {
        AtlasVaultUnlockPresentationState(
            capabilities: capabilities,
            selectedMethod: selectedMethod,
            status: status
        )
    }

    private static func presentationStatus(
        for error: AtlasVaultUnlockRequestError
    ) -> AtlasVaultUnlockPresentationStatus {
        switch error {
        case .cancelled:
            .cancelled
        case .expired:
            .timedOut
        case .invalidRequest, .alreadyUsed, .unlockFailed:
            .failed
        }
    }
}
