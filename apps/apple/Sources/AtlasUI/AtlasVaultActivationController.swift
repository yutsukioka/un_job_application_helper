import Foundation

public enum AtlasVaultActivationFailure:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case invalidVaultID
    case keyUnavailable
    case keyStoreFailure
    case invalidVaultKey
    case vaultUnavailable
    case storeMissing
    case authenticationFailed
    case corruptStore
    case unsupportedVersion
    case activationInProgress
    case alreadyUnlocked
    case cancelled

    public var description: String {
        switch self {
        case .invalidVaultID: "invalidVaultID"
        case .keyUnavailable: "keyUnavailable"
        case .keyStoreFailure: "keyStoreFailure"
        case .invalidVaultKey: "invalidVaultKey"
        case .vaultUnavailable: "vaultUnavailable"
        case .storeMissing: "storeMissing"
        case .authenticationFailed: "authenticationFailed"
        case .corruptStore: "corruptStore"
        case .unsupportedVersion: "unsupportedVersion"
        case .activationInProgress: "activationInProgress"
        case .alreadyUnlocked: "alreadyUnlocked"
        case .cancelled: "cancelled"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultActivationState:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case locked
    case activating
    case locking
    case unlocked
    case failed(AtlasVaultActivationFailure)

    public var description: String {
        switch self {
        case .locked: "locked"
        case .activating: "activating"
        case .locking: "locking"
        case .unlocked: "unlocked"
        case let .failed(failure): "failed(\(failure))"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultActivationScope:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let boundVaultID: String
    private let encryptedStoreLoader:
        @Sendable (AtlasVaultUnlockedSession) throws -> AtlasVaultLocalStoreEnvelope?
    private let recordHydrator:
        @Sendable (
            [AtlasVaultEncryptedRecordEnvelope],
            AtlasVaultUnlockedSession
        ) throws -> AtlasVaultHydratedState

    public init(
        vaultID: String,
        loadEncryptedStore: @escaping @Sendable (
            AtlasVaultUnlockedSession
        ) throws -> AtlasVaultLocalStoreEnvelope?,
        hydrateRecords: @escaping @Sendable (
            [AtlasVaultEncryptedRecordEnvelope],
            AtlasVaultUnlockedSession
        ) throws -> AtlasVaultHydratedState
    ) throws {
        self.boundVaultID = try AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID)
        self.encryptedStoreLoader = loadEncryptedStore
        self.recordHydrator = hydrateRecords
    }

    func isBound(to vaultID: String) -> Bool {
        boundVaultID == vaultID
    }

    func loadEncryptedStore(
        for session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultLocalStoreEnvelope? {
        guard session.vaultID == boundVaultID else {
            throw AtlasVaultPersistenceError.invalidSession
        }
        return try encryptedStoreLoader(session)
    }

    func hydrate(
        records: [AtlasVaultEncryptedRecordEnvelope],
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultHydratedState {
        guard session.vaultID == boundVaultID else {
            throw AtlasVaultHydrationError.invalidSession
        }
        return try recordHydrator(records, session)
    }

    public var description: String {
        "AtlasVaultActivationScope(vault: <redacted>, dependencies: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultActivationEnvironment:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let storedKeyLoader: @Sendable (String) async throws -> Data?
    private let rootResolver: @Sendable () async throws -> URL
    private let scopeFactory: @Sendable (URL, String) async throws -> AtlasVaultActivationScope

    public init(
        loadStoredVaultKey: @escaping @Sendable (String) async throws -> Data?,
        resolveRootDirectory: @escaping @Sendable () async throws -> URL,
        makeScope: @escaping @Sendable (
            URL,
            String
        ) async throws -> AtlasVaultActivationScope
    ) {
        self.storedKeyLoader = loadStoredVaultKey
        self.rootResolver = resolveRootDirectory
        self.scopeFactory = makeScope
    }

    func loadStoredVaultKey(for vaultID: String) async throws -> Data? {
        try await storedKeyLoader(vaultID)
    }

    func resolveRootDirectory() async throws -> URL {
        try await rootResolver()
    }

    func makeScope(rootURL: URL, vaultID: String) async throws -> AtlasVaultActivationScope {
        try await scopeFactory(rootURL, vaultID)
    }

    public var description: String {
        "AtlasVaultActivationEnvironment(dependencies: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public extension AtlasVaultActivationEnvironment {
    static func runtimeServices<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding
    >(
        _ services: AtlasVaultRuntimeServices<DirectoryPreparer, LocalStoreIO>
    ) -> AtlasVaultActivationEnvironment {
        AtlasVaultActivationEnvironment(
            loadStoredVaultKey: { vaultID in
                try services.keyStore.loadVaultKey(for: vaultID)
            },
            resolveRootDirectory: {
                try services.rootDirectoryProvider.rootDirectory()
            },
            makeScope: { rootURL, vaultID in
                let perVaultServices = try services.perVaultFactory.makeServices(
                    rootURL: rootURL,
                    vaultID: vaultID
                )
                return try AtlasVaultActivationScope(
                    vaultID: perVaultServices.vaultID,
                    loadEncryptedStore: { session in
                        try perVaultServices.persistenceCoordinator.loadEncryptedStore(
                            for: session
                        )
                    },
                    hydrateRecords: { records, session in
                        try perVaultServices.recordHydrator.hydrate(
                            records: records,
                            session: session
                        )
                    }
                )
            }
        )
    }
}

public actor AtlasVaultActivationController:
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public private(set) var state: AtlasVaultActivationState

    private let environment: AtlasVaultActivationEnvironment
    private let privateStateStore: any AtlasVaultPrivateStateStoring
    private let keyReleaseObserver: @Sendable () -> Void
    private var nextAttemptID: UInt64 = 1
    private var activeAttemptID: UInt64?
    private var activeAttemptGeneration: AtlasVaultPrivateStateGeneration?
    private var provisionalKeyOwner: AtlasVaultActivationKeyOwner?
    private var activeSession: AtlasVaultActivatedSession?

    public init(
        environment: AtlasVaultActivationEnvironment,
        keyReleaseObserver: @escaping @Sendable () -> Void = {}
    ) {
        self.environment = environment
        self.privateStateStore = AtlasVaultPrivateStateStore()
        self.keyReleaseObserver = keyReleaseObserver
        self.state = .locked
    }

    init(
        environment: AtlasVaultActivationEnvironment,
        privateStateStore: any AtlasVaultPrivateStateStoring,
        keyReleaseObserver: @escaping @Sendable () -> Void = {}
    ) {
        self.environment = environment
        self.privateStateStore = privateStateStore
        self.keyReleaseObserver = keyReleaseObserver
        self.state = .locked
    }

    deinit {
        provisionalKeyOwner?.wipe()
        activeSession?.keyOwner.wipe()
        let privateStateStore = privateStateStore
        Task {
            await privateStateStore.clearAll()
        }
    }

    public func activate(
        vaultID: String,
        suppliedVaultKey: Data? = nil
    ) async throws {
        switch state {
        case .activating, .locking:
            throw AtlasVaultActivationFailure.activationInProgress
        case .unlocked:
            throw AtlasVaultActivationFailure.alreadyUnlocked
        case .locked, .failed:
            break
        }

        let attemptID = nextAttemptID
        let generation = AtlasVaultPrivateStateGeneration()
        nextAttemptID &+= 1
        activeAttemptID = attemptID
        activeAttemptGeneration = generation
        state = .activating

        do {
            let validatedVaultID: String
            do {
                validatedVaultID = try AtlasInjectedRootVaultPathLocator.validatedVaultID(
                    vaultID
                )
            } catch {
                throw AtlasVaultActivationFailure.invalidVaultID
            }

            try await checkpoint(attemptID)
            let vaultKey: Data
            do {
                vaultKey = try await selectedVaultKey(
                    vaultID: validatedVaultID,
                    suppliedVaultKey: suppliedVaultKey
                )
            } catch {
                try requireActiveAttempt(attemptID)
                throw error
            }
            try await checkpoint(attemptID)
            let keyOwner: AtlasVaultActivationKeyOwner
            do {
                keyOwner = try AtlasVaultActivationKeyOwner(
                    vaultID: validatedVaultID,
                    vaultKey: vaultKey,
                    onRelease: keyReleaseObserver
                )
            } catch {
                throw AtlasVaultActivationFailure.invalidVaultKey
            }
            provisionalKeyOwner = keyOwner

            try await checkpoint(attemptID)
            let rootURL: URL
            do {
                rootURL = try await environment.resolveRootDirectory()
            } catch {
                try requireActiveAttempt(attemptID)
                if let failure = error as? AtlasVaultActivationFailure {
                    throw failure
                }
                if error is CancellationError {
                    throw CancellationError()
                }
                throw AtlasVaultActivationFailure.vaultUnavailable
            }

            try await checkpoint(attemptID)
            let scope: AtlasVaultActivationScope
            do {
                scope = try await environment.makeScope(
                    rootURL: rootURL,
                    vaultID: validatedVaultID
                )
            } catch {
                try requireActiveAttempt(attemptID)
                if let failure = error as? AtlasVaultActivationFailure {
                    throw failure
                }
                if error is CancellationError {
                    throw CancellationError()
                }
                throw AtlasVaultActivationFailure.vaultUnavailable
            }
            guard scope.isBound(to: validatedVaultID) else {
                throw AtlasVaultActivationFailure.vaultUnavailable
            }

            try await checkpoint(attemptID)
            let storeResult: Result<AtlasVaultLocalStoreEnvelope?, Error> = Result {
                try keyOwner.withUnlockedSession { session in
                    try scope.loadEncryptedStore(for: session)
                }
            }
            try await checkpoint(attemptID)
            let store: AtlasVaultLocalStoreEnvelope?
            do {
                store = try storeResult.get()
            } catch {
                throw Self.activationFailure(forPersistenceError: error)
            }
            guard let store else {
                throw AtlasVaultActivationFailure.storeMissing
            }

            try await checkpoint(attemptID)
            let hydrationResult: Result<AtlasVaultHydratedState, Error> = Result {
                try keyOwner.withUnlockedSession { session in
                    try scope.hydrate(records: store.records, session: session)
                }
            }
            try await checkpoint(attemptID)
            let hydratedState: AtlasVaultHydratedState
            do {
                hydratedState = try hydrationResult.get()
            } catch {
                throw Self.activationFailure(forHydrationError: error)
            }

            try await checkpoint(attemptID)
            do {
                try await privateStateStore.stage(
                    hydratedState,
                    generation: generation
                )
            } catch {
                try requireActiveAttempt(attemptID)
                throw error
            }

            try await checkpoint(attemptID)
            do {
                try await privateStateStore.commit(generation: generation)
            } catch {
                try requireActiveAttempt(attemptID)
                throw error
            }

            do {
                try requireActiveAttempt(attemptID)
                guard activeAttemptGeneration == generation else {
                    throw AtlasVaultActivationFailure.cancelled
                }
            } catch {
                await privateStateStore.clear(generation: generation)
                throw error
            }

            activeSession = AtlasVaultActivatedSession(
                keyOwner: keyOwner,
                scope: scope,
                privateStateGeneration: generation
            )
            provisionalKeyOwner = nil
            activeAttemptID = nil
            activeAttemptGeneration = nil
            state = .unlocked
        } catch let failure as AtlasVaultActivationFailure {
            let finalFailure = await finishFailedAttempt(
                attemptID,
                generation: generation,
                failure: failure
            )
            throw finalFailure
        } catch is CancellationError {
            let finalFailure = await finishFailedAttempt(
                attemptID,
                generation: generation,
                failure: .cancelled
            )
            throw finalFailure
        } catch {
            let finalFailure = await finishFailedAttempt(
                attemptID,
                generation: generation,
                failure: .vaultUnavailable
            )
            throw finalFailure
        }
    }

    @discardableResult
    public func cancelActivation() async -> Bool {
        guard activeAttemptID != nil,
              let generation = activeAttemptGeneration else {
            return false
        }
        activeAttemptID = nil
        activeAttemptGeneration = nil
        provisionalKeyOwner?.wipe()
        provisionalKeyOwner = nil
        await privateStateStore.clear(generation: generation)
        state = .locked
        return true
    }

    public func lock() async {
        state = .locking
        activeAttemptID = nil
        activeAttemptGeneration = nil
        provisionalKeyOwner?.wipe()
        provisionalKeyOwner = nil
        activeSession?.keyOwner.wipe()
        activeSession = nil
        await privateStateStore.clearAll()
        state = .locked
    }

    var hasInstalledPrivateStateForTesting: Bool {
        activeSession != nil
    }

    func privateStateSnapshot() async throws -> AtlasVaultHydratedState {
        guard state == .unlocked,
              let generation = activeSession?.privateStateGeneration else {
            throw AtlasVaultPrivateStateStoreError.unavailable
        }
        return try await privateStateStore.snapshot(generation: generation)
    }

    public nonisolated var description: String {
        "AtlasVaultActivationController(state: <redacted>, private: <redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private func selectedVaultKey(
        vaultID: String,
        suppliedVaultKey: Data?
    ) async throws -> Data {
        if let suppliedVaultKey {
            guard suppliedVaultKey.count == AtlasVaultRecordCrypto.vaultKeyByteCount else {
                throw AtlasVaultActivationFailure.invalidVaultKey
            }
            return suppliedVaultKey
        }

        do {
            guard let storedKey = try await environment.loadStoredVaultKey(for: vaultID) else {
                throw AtlasVaultActivationFailure.keyUnavailable
            }
            guard storedKey.count == AtlasVaultRecordCrypto.vaultKeyByteCount else {
                throw AtlasVaultActivationFailure.invalidVaultKey
            }
            return storedKey
        } catch let failure as AtlasVaultActivationFailure {
            throw failure
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as AtlasKeychainVaultKeyStoreError {
            if error == .invalidVaultKeyLength {
                throw AtlasVaultActivationFailure.invalidVaultKey
            }
            throw AtlasVaultActivationFailure.keyStoreFailure
        } catch let error as AtlasVaultUnlockError {
            if error == .invalidVaultKeyLength {
                throw AtlasVaultActivationFailure.invalidVaultKey
            }
            throw AtlasVaultActivationFailure.keyStoreFailure
        } catch {
            throw AtlasVaultActivationFailure.keyStoreFailure
        }
    }

    private func checkpoint(_ attemptID: UInt64) async throws {
        await Task.yield()
        try requireActiveAttempt(attemptID)
    }

    private func requireActiveAttempt(_ attemptID: UInt64) throws {
        try Task.checkCancellation()
        guard activeAttemptID == attemptID else {
            throw AtlasVaultActivationFailure.cancelled
        }
    }

    private func finishFailedAttempt(
        _ attemptID: UInt64,
        generation: AtlasVaultPrivateStateGeneration,
        failure: AtlasVaultActivationFailure
    ) async -> AtlasVaultActivationFailure {
        guard activeAttemptID == attemptID else {
            await privateStateStore.clear(generation: generation)
            return .cancelled
        }
        provisionalKeyOwner?.wipe()
        provisionalKeyOwner = nil
        await privateStateStore.clear(generation: generation)
        guard activeAttemptID == attemptID,
              activeAttemptGeneration == generation else {
            return .cancelled
        }
        activeAttemptID = nil
        activeAttemptGeneration = nil
        state = failure == .cancelled ? .locked : .failed(failure)
        return failure
    }

    private static func activationFailure(
        forPersistenceError error: Error
    ) -> AtlasVaultActivationFailure {
        if let failure = error as? AtlasVaultActivationFailure {
            return failure
        }
        guard let persistenceError = error as? AtlasVaultPersistenceError else {
            return .vaultUnavailable
        }
        switch persistenceError {
        case .corruptStore:
            return .corruptStore
        case .unsupportedStoreVersion:
            return .unsupportedVersion
        case .invalidSession:
            return .invalidVaultKey
        case .directoryPreparationFailed, .readFailed, .writeFailed, .fileExists, .cryptoFailed:
            return .vaultUnavailable
        }
    }

    private static func activationFailure(
        forHydrationError error: Error
    ) -> AtlasVaultActivationFailure {
        if let failure = error as? AtlasVaultActivationFailure {
            return failure
        }
        guard let hydrationError = error as? AtlasVaultHydrationError else {
            return .corruptStore
        }
        switch hydrationError {
        case .authenticationFailed:
            return .authenticationFailed
        case .malformedPayload, .corruptRecord:
            return .corruptStore
        case .unsupportedPayloadSchema, .unknownRecordType, .unsupportedRecordVersion:
            return .unsupportedVersion
        case .invalidSession:
            return .invalidVaultKey
        }
    }
}

private final class AtlasVaultActivationKeyOwner: @unchecked Sendable {
    private var session: AtlasVaultSession?
    private let onRelease: @Sendable () -> Void
    private var released = false

    init(
        vaultID: String,
        vaultKey: Data,
        onRelease: @escaping @Sendable () -> Void
    ) throws {
        self.session = try AtlasVaultSession(vaultID: vaultID, vaultKey: vaultKey)
        self.onRelease = onRelease
    }

    func withUnlockedSession<Result>(
        _ operation: (AtlasVaultUnlockedSession) throws -> Result
    ) throws -> Result {
        guard let session else {
            throw AtlasVaultPersistenceError.invalidSession
        }
        return try session.withVaultKey { vaultKey in
            let view = try AtlasVaultUnlockedSession(
                vaultID: session.vaultID,
                vaultKey: vaultKey
            )
            return try operation(view)
        }
    }

    func wipe() {
        guard !released else {
            return
        }
        session?.wipeVaultKey()
        session = nil
        released = true
        onRelease()
    }

    deinit {
        wipe()
    }
}

private struct AtlasVaultActivatedSession: Sendable {
    let keyOwner: AtlasVaultActivationKeyOwner
    let scope: AtlasVaultActivationScope
    let privateStateGeneration: AtlasVaultPrivateStateGeneration
}
