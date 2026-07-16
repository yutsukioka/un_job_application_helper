import Foundation

public protocol AtlasVaultSecretBuffer: AnyObject, Sendable {
    func takeSecretBytes() async throws -> Data
    func clear() async
}

public enum AtlasVaultSecretBufferError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case unavailable

    public var description: String {
        "unavailable"
    }

    public var debugDescription: String {
        description
    }
}

public actor AtlasVaultInMemorySecretBuffer:
    AtlasVaultSecretBuffer,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private var bytes: Data?

    public init(bytes: Data) {
        self.bytes = bytes
    }

    deinit {
        guard var bytes else { return }
        Self.wipe(&bytes)
    }

    public func takeSecretBytes() async throws -> Data {
        guard let bytes else {
            throw AtlasVaultSecretBufferError.unavailable
        }
        self.bytes = nil
        return bytes
    }

    public func clear() async {
        guard var bytes else { return }
        Self.wipe(&bytes)
        self.bytes = nil
    }

    var isClearedForTesting: Bool {
        bytes == nil
    }

    public nonisolated var description: String {
        "AtlasVaultInMemorySecretBuffer(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private static func wipe(_ value: inout Data) {
        guard !value.isEmpty else { return }
        value.resetBytes(in: value.startIndex..<value.endIndex)
        value.removeAll(keepingCapacity: false)
    }
}

public enum AtlasVaultUnlockInputSource: Sendable {
    case passphrase(any AtlasVaultSecretBuffer)
    case recoveryKey(any AtlasVaultSecretBuffer)
    case localKey
    case suppliedTestVaultKey(any AtlasVaultSecretBuffer)
}

public struct AtlasVaultUnlockRequest:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    fileprivate let storage: AtlasVaultUnlockRequestStorage

    public init(
        vaultID: String,
        input: AtlasVaultUnlockInputSource,
        timeout: Duration? = nil
    ) {
        self.storage = AtlasVaultUnlockRequestStorage(
            id: UUID(),
            vaultID: vaultID,
            input: input,
            timeout: timeout
        )
    }

    public var description: String {
        "AtlasVaultUnlockRequest(input: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultUnlockRequestError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case invalidRequest
    case alreadyUsed
    case cancelled
    case expired
    case unlockFailed

    public var description: String {
        switch self {
        case .invalidRequest: "invalidRequest"
        case .alreadyUsed: "alreadyUsed"
        case .cancelled: "cancelled"
        case .expired: "expired"
        case .unlockFailed: "unlockFailed"
        }
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultUnlockRequestCoordinating: Sendable {
    func dispatch(_ request: AtlasVaultUnlockRequest) async throws
    func cancel(_ request: AtlasVaultUnlockRequest) async -> Bool
}

public struct AtlasVaultUnlockRequestDependencies: Sendable {
    fileprivate let derivePassphraseVaultKey: @Sendable (Data) async throws -> Data
    fileprivate let deriveRecoveryVaultKey: @Sendable (Data) async throws -> Data
    fileprivate let activate: @Sendable (AtlasVaultRuntimeActivationRequest) async throws -> Void
    fileprivate let sleep: @Sendable (Duration) async throws -> Void

    public init(
        derivePassphraseVaultKey: @escaping @Sendable (Data) async throws -> Data,
        deriveRecoveryVaultKey: @escaping @Sendable (Data) async throws -> Data,
        activate: @escaping @Sendable (
            AtlasVaultRuntimeActivationRequest
        ) async throws -> Void,
        sleep: @escaping @Sendable (Duration) async throws -> Void = { duration in
            try await Task.sleep(for: duration)
        }
    ) {
        self.derivePassphraseVaultKey = derivePassphraseVaultKey
        self.deriveRecoveryVaultKey = deriveRecoveryVaultKey
        self.activate = activate
        self.sleep = sleep
    }
}

public actor AtlasVaultUnlockRequestCoordinator:
    AtlasVaultUnlockRequestCoordinating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private struct ActiveDispatch {
        let storage: AtlasVaultUnlockRequestStorage
        let operation: Task<Void, Error>
        var timeout: Task<Void, Never>?
    }

    private let dependencies: AtlasVaultUnlockRequestDependencies
    private var activeDispatches: [UUID: ActiveDispatch] = [:]

    public init(dependencies: AtlasVaultUnlockRequestDependencies) {
        self.dependencies = dependencies
    }

    deinit {
        for active in activeDispatches.values {
            active.operation.cancel()
            active.timeout?.cancel()
        }
    }

    public func dispatch(_ request: AtlasVaultUnlockRequest) async throws {
        let claim: AtlasVaultUnlockRequestClaim
        do {
            claim = try await request.storage.claim()
        } catch let error as AtlasVaultUnlockRequestError {
            throw error
        } catch {
            throw AtlasVaultUnlockRequestError.invalidRequest
        }

        let storage = request.storage
        let dependencies = dependencies
        let operation = Task {
            try await Self.perform(
                claim: claim,
                storage: storage,
                dependencies: dependencies
            )
        }
        activeDispatches[claim.id] = ActiveDispatch(
            storage: storage,
            operation: operation,
            timeout: nil
        )
        scheduleTimeout(for: claim)

        do {
            try await withTaskCancellationHandler {
                try await operation.value
            } onCancel: {
                operation.cancel()
                Task {
                    _ = await storage.cancel()
                }
            }
            clearActiveDispatch(claim.id)
            if let error = await storage.finishSuccess() {
                throw error
            }
        } catch {
            clearActiveDispatch(claim.id)
            if error is CancellationError || Task.isCancelled {
                _ = await storage.cancel()
            }
            throw await storage.finishFailure(default: Self.publicError(for: error))
        }
    }

    public func cancel(_ request: AtlasVaultUnlockRequest) async -> Bool {
        let id = await request.storage.idValue()
        let didCancel = await request.storage.cancel()
        guard didCancel else {
            return false
        }
        if let active = activeDispatches[id] {
            active.operation.cancel()
            active.timeout?.cancel()
        }
        return true
    }

    public nonisolated var description: String {
        "AtlasVaultUnlockRequestCoordinator(state: <redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private func scheduleTimeout(for claim: AtlasVaultUnlockRequestClaim) {
        guard let duration = claim.timeout else {
            return
        }
        let sleep = dependencies.sleep
        let timeout = Task { [weak self] in
            guard duration > .zero else {
                await self?.expire(claim.id)
                return
            }
            do {
                try await sleep(duration)
            } catch {
                return
            }
            guard !Task.isCancelled else {
                return
            }
            await self?.expire(claim.id)
        }
        activeDispatches[claim.id]?.timeout = timeout
    }

    private func expire(_ id: UUID) async {
        guard let active = activeDispatches[id],
              await active.storage.expire() else {
            return
        }
        active.operation.cancel()
    }

    private func clearActiveDispatch(_ id: UUID) {
        activeDispatches[id]?.timeout?.cancel()
        activeDispatches.removeValue(forKey: id)
    }

    private static func perform(
        claim: AtlasVaultUnlockRequestClaim,
        storage: AtlasVaultUnlockRequestStorage,
        dependencies: AtlasVaultUnlockRequestDependencies
    ) async throws {
        do {
            try await storage.requireDispatching()
            let vaultID: String
            do {
                vaultID = try AtlasInjectedRootVaultPathLocator.validatedVaultID(
                    claim.vaultID
                )
            } catch {
                throw AtlasVaultUnlockRequestInternalError.invalidRequest
            }

            switch claim.input {
            case let .passphrase(buffer):
                var key = try await consumeSecret(
                    buffer,
                    transform: dependencies.derivePassphraseVaultKey
                )
                defer { wipe(&key) }
                try await activate(
                    vaultID: vaultID,
                    key: key,
                    storage: storage,
                    dependencies: dependencies
                )
            case let .recoveryKey(buffer):
                var key = try await consumeSecret(
                    buffer,
                    transform: dependencies.deriveRecoveryVaultKey
                )
                defer { wipe(&key) }
                try await activate(
                    vaultID: vaultID,
                    key: key,
                    storage: storage,
                    dependencies: dependencies
                )
            case .localKey:
                try await storage.requireDispatching()
                try await dependencies.activate(
                    AtlasVaultRuntimeActivationRequest(vaultID: vaultID)
                )
                try await storage.requireDispatching()
            case let .suppliedTestVaultKey(buffer):
                var key = try await consumeSecret(buffer) { Data($0) }
                defer { wipe(&key) }
                try await activate(
                    vaultID: vaultID,
                    key: key,
                    storage: storage,
                    dependencies: dependencies
                )
            }
            await claim.input.clearSecret()
        } catch {
            await claim.input.clearSecret()
            throw error
        }
    }

    private static func activate(
        vaultID: String,
        key: Data,
        storage: AtlasVaultUnlockRequestStorage,
        dependencies: AtlasVaultUnlockRequestDependencies
    ) async throws {
        guard key.count == AtlasVaultRecordCrypto.vaultKeyByteCount else {
            throw AtlasVaultUnlockRequestInternalError.invalidKey
        }
        try await storage.requireDispatching()
        try await dependencies.activate(
            AtlasVaultRuntimeActivationRequest(
                vaultID: vaultID,
                suppliedVaultKey: key
            )
        )
        try await storage.requireDispatching()
    }

    private static func consumeSecret(
        _ buffer: any AtlasVaultSecretBuffer,
        transform: @Sendable (Data) async throws -> Data
    ) async throws -> Data {
        var secret: Data
        do {
            secret = try await buffer.takeSecretBytes()
        } catch {
            await buffer.clear()
            throw error
        }

        do {
            let result = try await transform(secret)
            wipe(&secret)
            await buffer.clear()
            return result
        } catch {
            wipe(&secret)
            await buffer.clear()
            throw error
        }
    }

    private static func publicError(for error: Error) -> AtlasVaultUnlockRequestError {
        if let error = error as? AtlasVaultUnlockRequestError {
            return error
        }
        if let error = error as? AtlasVaultUnlockRequestInternalError,
           error == .invalidRequest {
            return .invalidRequest
        }
        if error is CancellationError {
            return .cancelled
        }
        return .unlockFailed
    }

    private static func wipe(_ value: inout Data) {
        guard !value.isEmpty else { return }
        value.resetBytes(in: value.startIndex..<value.endIndex)
        value.removeAll(keepingCapacity: false)
    }
}

private struct AtlasVaultUnlockRequestClaim: Sendable {
    let id: UUID
    let vaultID: String
    let input: AtlasVaultUnlockInputSource
    let timeout: Duration?
}

private enum AtlasVaultUnlockRequestInternalError: Error, Equatable, Sendable {
    case invalidRequest
    case invalidKey
}

private enum AtlasVaultUnlockRequestState: Sendable {
    case pending
    case dispatching
    case completed
    case cancelled
    case expired
}

private actor AtlasVaultUnlockRequestStorage {
    private let id: UUID
    private let vaultID: String
    private let timeout: Duration?
    private var input: AtlasVaultUnlockInputSource?
    private var state: AtlasVaultUnlockRequestState = .pending

    init(
        id: UUID,
        vaultID: String,
        input: AtlasVaultUnlockInputSource,
        timeout: Duration?
    ) {
        self.id = id
        self.vaultID = vaultID
        self.input = input
        self.timeout = timeout
    }

    func idValue() -> UUID {
        id
    }

    func claim() async throws -> AtlasVaultUnlockRequestClaim {
        switch state {
        case .pending:
            guard let input else {
                state = .completed
                throw AtlasVaultUnlockRequestError.invalidRequest
            }
            state = .dispatching
            self.input = nil
            return AtlasVaultUnlockRequestClaim(
                id: id,
                vaultID: vaultID,
                input: input,
                timeout: timeout
            )
        case .dispatching, .completed:
            throw AtlasVaultUnlockRequestError.alreadyUsed
        case .cancelled:
            throw AtlasVaultUnlockRequestError.cancelled
        case .expired:
            throw AtlasVaultUnlockRequestError.expired
        }
    }

    func requireDispatching() throws {
        switch state {
        case .dispatching:
            return
        case .cancelled:
            throw AtlasVaultUnlockRequestError.cancelled
        case .expired:
            throw AtlasVaultUnlockRequestError.expired
        case .pending, .completed:
            throw AtlasVaultUnlockRequestError.alreadyUsed
        }
    }

    func finishSuccess() -> AtlasVaultUnlockRequestError? {
        switch state {
        case .dispatching:
            state = .completed
            return nil
        case .cancelled:
            return .cancelled
        case .expired:
            return .expired
        case .pending, .completed:
            return .alreadyUsed
        }
    }

    func finishFailure(
        default defaultError: AtlasVaultUnlockRequestError
    ) -> AtlasVaultUnlockRequestError {
        switch state {
        case .dispatching:
            state = .completed
            return defaultError
        case .cancelled:
            return .cancelled
        case .expired:
            return .expired
        case .pending, .completed:
            return defaultError
        }
    }

    func cancel() async -> Bool {
        switch state {
        case .pending:
            state = .cancelled
            let input = input
            self.input = nil
            await input?.clearSecret()
            return true
        case .dispatching:
            state = .cancelled
            return true
        case .completed, .cancelled, .expired:
            return false
        }
    }

    func expire() async -> Bool {
        switch state {
        case .pending:
            state = .expired
            let input = input
            self.input = nil
            await input?.clearSecret()
            return true
        case .dispatching:
            state = .expired
            return true
        case .completed, .cancelled, .expired:
            return false
        }
    }
}

private extension AtlasVaultUnlockInputSource {
    func clearSecret() async {
        switch self {
        case let .passphrase(buffer),
             let .recoveryKey(buffer),
             let .suppliedTestVaultKey(buffer):
            await buffer.clear()
        case .localKey:
            return
        }
    }
}
