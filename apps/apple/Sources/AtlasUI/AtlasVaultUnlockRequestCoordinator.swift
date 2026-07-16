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
    private var bytes: [UInt8]?

    public init(bytes: Data) {
        self.bytes = Array(bytes)
    }

    deinit {
        guard var retainedBytes = bytes else { return }
        bytes = nil
        Self.wipe(&retainedBytes)
    }

    public func takeSecretBytes() async throws -> Data {
        guard var retainedBytes = bytes else {
            throw AtlasVaultSecretBufferError.unavailable
        }
        bytes = nil
        let result = retainedBytes.withUnsafeBytes { rawBuffer -> Data in
            guard let baseAddress = rawBuffer.baseAddress else {
                return Data()
            }
            return Data(bytes: baseAddress, count: rawBuffer.count)
        }
        Self.wipe(&retainedBytes)
        return result
    }

    public func clear() async {
        guard var retainedBytes = bytes else { return }
        self.bytes = nil
        Self.wipe(&retainedBytes)
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

    private static func wipe(_ value: inout [UInt8]) {
        guard !value.isEmpty else { return }
        value.withUnsafeMutableBufferPointer { buffer in
            for index in buffer.indices {
                buffer[index] = 0
            }
        }
        value.removeAll(keepingCapacity: false)
    }
}

public enum AtlasVaultUnlockInputSource: Sendable {
    case passphrase(any AtlasVaultSecretBuffer)
    case recoveryKey(any AtlasVaultSecretBuffer)
    case localKey
}

private enum AtlasVaultUnlockRequestInput: Sendable {
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
            input: AtlasVaultUnlockRequestInput(input),
            timeout: timeout
        )
    }

    init(
        vaultID: String,
        suppliedTestVaultKey: any AtlasVaultSecretBuffer,
        timeout: Duration? = nil
    ) {
        self.storage = AtlasVaultUnlockRequestStorage(
            id: UUID(),
            vaultID: vaultID,
            input: .suppliedTestVaultKey(suppliedTestVaultKey),
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
    fileprivate let beforeCancellationHandler: @Sendable () async -> Void
    fileprivate let beforeOperationStart: @Sendable () async -> Void
    fileprivate let afterActivationReturn: @Sendable () async -> Void
    fileprivate let beforeSuccessCommit: @Sendable () async -> Void

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
        self.init(
            derivePassphraseVaultKey: derivePassphraseVaultKey,
            deriveRecoveryVaultKey: deriveRecoveryVaultKey,
            activate: activate,
            sleep: sleep,
            beforeCancellationHandler: {},
            beforeOperationStart: {},
            afterActivationReturn: {},
            beforeSuccessCommit: {}
        )
    }

    init(
        derivePassphraseVaultKey: @escaping @Sendable (Data) async throws -> Data,
        deriveRecoveryVaultKey: @escaping @Sendable (Data) async throws -> Data,
        activate: @escaping @Sendable (
            AtlasVaultRuntimeActivationRequest
        ) async throws -> Void,
        sleep: @escaping @Sendable (Duration) async throws -> Void,
        beforeCancellationHandler: @escaping @Sendable () async -> Void,
        beforeOperationStart: @escaping @Sendable () async -> Void,
        afterActivationReturn: @escaping @Sendable () async -> Void,
        beforeSuccessCommit: @escaping @Sendable () async -> Void
    ) {
        self.derivePassphraseVaultKey = derivePassphraseVaultKey
        self.deriveRecoveryVaultKey = deriveRecoveryVaultKey
        self.activate = activate
        self.sleep = sleep
        self.beforeCancellationHandler = beforeCancellationHandler
        self.beforeOperationStart = beforeOperationStart
        self.afterActivationReturn = afterActivationReturn
        self.beforeSuccessCommit = beforeSuccessCommit
    }
}

public actor AtlasVaultUnlockRequestCoordinator:
    AtlasVaultUnlockRequestCoordinating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private struct ActiveDispatch {
        let storage: AtlasVaultUnlockRequestStorage
        let input: AtlasVaultUnlockRequestInput
        let operation: Task<Void, Error>
        let terminalGate: AtlasVaultUnlockTerminalGate
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
            let input = active.input
            Task {
                await input.clearSecret()
            }
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
        if let timeout = claim.timeout, timeout <= .zero {
            let didExpire = await storage.expire()
            await claim.input.clearSecret()
            guard didExpire else {
                throw await storage.finishFailure(default: .expired)
            }
            throw AtlasVaultUnlockRequestError.expired
        }

        let dependencies = dependencies
        let terminalGate = AtlasVaultUnlockTerminalGate()
        let operationStartGate = AtlasVaultUnlockOperationStartGate()
        let operation = Task {
            await operationStartGate.wait()
            try Task.checkCancellation()
            await dependencies.beforeOperationStart()
            try await Self.perform(
                claim: claim,
                storage: storage,
                terminalGate: terminalGate,
                dependencies: dependencies
            )
        }
        activeDispatches[claim.id] = ActiveDispatch(
            storage: storage,
            input: claim.input,
            operation: operation,
            terminalGate: terminalGate,
            timeout: nil
        )
        scheduleTimeout(for: claim)
        await dependencies.beforeCancellationHandler()

        do {
            try await withTaskCancellationHandler {
                if Task.isCancelled,
                   terminalGate.reserveTermination(.cancelled) {
                    operation.cancel()
                    Task {
                        await claim.input.clearSecret()
                        _ = await storage.cancelActive()
                    }
                }
                await operationStartGate.release()
                try await operation.value
            } onCancel: {
                if terminalGate.reserveTermination(.cancelled) {
                    operation.cancel()
                    Task {
                        await claim.input.clearSecret()
                        _ = await storage.cancelActive()
                    }
                }
            }
            await dependencies.beforeSuccessCommit()
            if let error = await storage.finishActivationSuccess() {
                throw error
            }
            clearActiveDispatch(claim.id)
        } catch {
            clearActiveDispatch(claim.id)
            await claim.input.clearSecret()
            if error is CancellationError || Task.isCancelled {
                _ = await storage.cancelActive()
            }
            throw await storage.finishFailure(default: Self.publicError(for: error))
        }
    }

    public func cancel(_ request: AtlasVaultUnlockRequest) async -> Bool {
        let id = await request.storage.idValue()
        if let active = activeDispatches[id] {
            guard active.terminalGate.reserveTermination(.cancelled) else {
                return false
            }

            let didCancel = await request.storage.cancelActive()
            guard didCancel else {
                return false
            }
            await active.input.clearSecret()
            active.operation.cancel()
            active.timeout?.cancel()
            return true
        }

        let didCancel = await request.storage.cancelPending()
        return didCancel
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
              active.terminalGate.reserveTermination(.expired) else {
            return
        }
        guard await active.storage.expire() else { return }
        await active.input.clearSecret()
        active.operation.cancel()
    }

    private func clearActiveDispatch(_ id: UUID) {
        activeDispatches[id]?.timeout?.cancel()
        activeDispatches.removeValue(forKey: id)
    }

    private static func perform(
        claim: AtlasVaultUnlockRequestClaim,
        storage: AtlasVaultUnlockRequestStorage,
        terminalGate: AtlasVaultUnlockTerminalGate,
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
                    terminalGate: terminalGate,
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
                    terminalGate: terminalGate,
                    dependencies: dependencies
                )
            case .localKey:
                try await storage.requireDispatching()
                try terminalGate.reserveActivation()
                try await invokeActivation(
                    AtlasVaultRuntimeActivationRequest(vaultID: vaultID),
                    dependencies: dependencies
                )
                await dependencies.afterActivationReturn()
                try terminalGate.completeActivation()
            case let .suppliedTestVaultKey(buffer):
                var key = try await takeSecret(from: buffer)
                defer { wipe(&key) }
                try await activate(
                    vaultID: vaultID,
                    key: key,
                    storage: storage,
                    terminalGate: terminalGate,
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
        terminalGate: AtlasVaultUnlockTerminalGate,
        dependencies: AtlasVaultUnlockRequestDependencies
    ) async throws {
        guard key.count == AtlasVaultRecordCrypto.vaultKeyByteCount else {
            throw AtlasVaultUnlockRequestInternalError.invalidKey
        }
        try await storage.requireDispatching()
        try terminalGate.reserveActivation()
        try await invokeActivation(
            AtlasVaultRuntimeActivationRequest(
                vaultID: vaultID,
                suppliedVaultKey: key
            ),
            dependencies: dependencies
        )
        await dependencies.afterActivationReturn()
        try terminalGate.completeActivation()
    }

    private static func consumeSecret(
        _ buffer: any AtlasVaultSecretBuffer,
        transform: @Sendable (Data) async throws -> Data
    ) async throws -> Data {
        var secret = try await takeSecret(from: buffer)

        do {
            let result = try await transform(secret)
            wipe(&secret)
            await buffer.clear()
            return result
        } catch {
            wipe(&secret)
            await buffer.clear()
            if error is CancellationError, Task.isCancelled {
                throw CancellationError()
            }
            throw AtlasVaultUnlockRequestInternalError.dependencyFailure
        }
    }

    private static func takeSecret(
        from buffer: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        do {
            return try await buffer.takeSecretBytes()
        } catch {
            await buffer.clear()
            if error is CancellationError, Task.isCancelled {
                throw CancellationError()
            }
            throw AtlasVaultUnlockRequestInternalError.dependencyFailure
        }
    }

    private static func invokeActivation(
        _ request: AtlasVaultRuntimeActivationRequest,
        dependencies: AtlasVaultUnlockRequestDependencies
    ) async throws {
        do {
            try await dependencies.activate(request)
        } catch {
            if error is CancellationError, Task.isCancelled {
                throw CancellationError()
            }
            throw AtlasVaultUnlockRequestInternalError.dependencyFailure
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

private final class AtlasVaultUnlockTerminalGate: @unchecked Sendable {
    private enum State {
        case open
        case cancelled
        case expired
        case activationReserved
        case activationCompleted
    }

    private let lock = NSLock()
    private var state: State = .open

    func reserveTermination(_ error: AtlasVaultUnlockRequestError) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        switch (state, error) {
        case (.open, .cancelled):
            state = .cancelled
        case (.open, .expired), (.activationReserved, .expired):
            state = .expired
        default:
            return false
        }
        return true
    }

    func reserveActivation() throws {
        lock.lock()
        defer { lock.unlock() }
        switch state {
        case .open:
            state = .activationReserved
        case .cancelled:
            throw AtlasVaultUnlockRequestError.cancelled
        case .expired:
            throw AtlasVaultUnlockRequestError.expired
        case .activationReserved, .activationCompleted:
            throw AtlasVaultUnlockRequestError.alreadyUsed
        }
    }

    func completeActivation() throws {
        lock.lock()
        defer { lock.unlock() }
        switch state {
        case .activationReserved, .expired:
            state = .activationCompleted
        case .cancelled:
            throw AtlasVaultUnlockRequestError.cancelled
        case .open, .activationCompleted:
            throw AtlasVaultUnlockRequestError.alreadyUsed
        }
    }
}

private actor AtlasVaultUnlockOperationStartGate {
    private var isReleased = false
    private var waiter: CheckedContinuation<Void, Never>?

    func wait() async {
        guard !isReleased else { return }
        await withCheckedContinuation { continuation in
            if isReleased {
                continuation.resume()
            } else {
                precondition(waiter == nil, "Operation start gate supports one waiter")
                waiter = continuation
            }
        }
    }

    func release() {
        guard !isReleased else { return }
        isReleased = true
        waiter?.resume()
        waiter = nil
    }
}

private struct AtlasVaultUnlockRequestClaim: Sendable {
    let id: UUID
    let vaultID: String
    let input: AtlasVaultUnlockRequestInput
    let timeout: Duration?
}

private enum AtlasVaultUnlockRequestInternalError: Error, Equatable, Sendable {
    case invalidRequest
    case invalidKey
    case dependencyFailure
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
    private var input: AtlasVaultUnlockRequestInput?
    private var state: AtlasVaultUnlockRequestState = .pending

    init(
        id: UUID,
        vaultID: String,
        input: AtlasVaultUnlockRequestInput,
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

    func finishActivationSuccess() -> AtlasVaultUnlockRequestError? {
        switch state {
        case .dispatching, .expired:
            state = .completed
            return nil
        case .cancelled:
            return .cancelled
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

    func cancelPending() async -> Bool {
        switch state {
        case .pending:
            state = .cancelled
            let input = input
            self.input = nil
            await input?.clearSecret()
            return true
        case .dispatching, .completed, .cancelled, .expired:
            return false
        }
    }

    func cancelActive() async -> Bool {
        switch state {
        case .dispatching:
            state = .cancelled
            return true
        case .pending, .completed, .cancelled, .expired:
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

private extension AtlasVaultUnlockRequestInput {
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

private extension AtlasVaultUnlockRequestInput {
    init(_ source: AtlasVaultUnlockInputSource) {
        switch source {
        case let .passphrase(buffer):
            self = .passphrase(buffer)
        case let .recoveryKey(buffer):
            self = .recoveryKey(buffer)
        case .localKey:
            self = .localKey
        }
    }
}
