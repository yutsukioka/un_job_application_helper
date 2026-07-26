import Foundation

public enum AtlasVaultRecoveryUnlockFailure:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case unavailable
    case unlockFailed
    case cancelled

    public var description: String {
        switch self {
        case .unavailable:
            "unavailable"
        case .unlockFailed:
            "unlockFailed"
        case .cancelled:
            "cancelled"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultRecoveryUnlockEnvironment: Sendable {
    let loadStore:
        @Sendable (String) async throws
            -> AtlasVaultLocalStoreEnvelope?

    public init(
        loadStore:
            @escaping @Sendable (String) async throws
                -> AtlasVaultLocalStoreEnvelope?
    ) {
        self.loadStore = loadStore
    }
}

public struct AtlasVaultRecoveryUnlockProvider:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let environment: AtlasVaultRecoveryUnlockEnvironment

    public init(environment: AtlasVaultRecoveryUnlockEnvironment) {
        self.environment = environment
    }

    public func deriveVaultKey(
        vaultID: String,
        recoverySecret: Data
    ) async throws -> Data {
        var secret = recoverySecret
        defer {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&secret)
        }
        do {
            try Task.checkCancellation()
            let validatedVaultID =
                try AtlasInjectedRootVaultPathLocator.validatedVaultID(
                    vaultID
                )
            guard
                validatedVaultID == vaultID,
                let recoveryText = String(
                    data: secret,
                    encoding: .utf8
                ),
                Data(recoveryText.utf8) == secret
            else {
                throw AtlasVaultRecoveryUnlockFailure.unlockFailed
            }
            let store = try await environment.loadStore(
                validatedVaultID
            )
            try Task.checkCancellation()
            guard let store else {
                throw AtlasVaultRecoveryUnlockFailure.unavailable
            }
            let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: store.vaultMetadata
            )
            guard
                metadata.vaultID == validatedVaultID,
                let recoveryWrap = Self.onlyRecoveryWrap(in: metadata)
            else {
                throw AtlasVaultRecoveryUnlockFailure.unlockFailed
            }
            var recoveryKey = try AtlasVaultRecoveryKeyCodec.parse(
                recoveryText
            )
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&recoveryKey)
            }
            try Task.checkCancellation()
            var unwrapped = try AtlasVaultRecoveryWrapCrypto.unwrap(
                recoveryWrap,
                recoveryKey: recoveryKey,
                vaultID: validatedVaultID
            )
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&unwrapped)
            }
            guard
                unwrapped.count
                    == AtlasVaultRecordCrypto.vaultKeyByteCount
            else {
                throw AtlasVaultRecoveryUnlockFailure.unlockFailed
            }
            try Task.checkCancellation()
            return Data(unwrapped)
        } catch is CancellationError {
            throw AtlasVaultRecoveryUnlockFailure.cancelled
        } catch let failure as AtlasVaultRecoveryUnlockFailure {
            throw failure
        } catch {
            throw AtlasVaultRecoveryUnlockFailure.unlockFailed
        }
    }

    public var description: String {
        "AtlasVaultRecoveryUnlockProvider(<redacted>)"
    }

    public var debugDescription: String {
        description
    }

    static func onlyRecoveryWrap(
        in metadata: AtlasVaultVersionedWrappedKeyMetadata
    ) -> AtlasVaultRecoveryWrappedKeyEnvelope? {
        let wraps = metadata.keyWraps.compactMap(\.recoveryKeyEnvelope)
        guard wraps.count == 1 else {
            return nil
        }
        return wraps[0]
    }

    static func production<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding
    >(
        runtimeServices: AtlasVaultRuntimeServices<
            DirectoryPreparer,
            LocalStoreIO
        >
    ) -> AtlasVaultRecoveryUnlockProvider {
        AtlasVaultRecoveryUnlockProvider(
            environment: AtlasVaultRecoveryUnlockEnvironment(
                loadStore: { vaultID in
                    try Self.loadProductionStore(
                        runtimeServices: runtimeServices,
                        vaultID: vaultID
                    )
                }
            )
        )
    }

    private static func loadProductionStore<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding
    >(
        runtimeServices: AtlasVaultRuntimeServices<
            DirectoryPreparer,
            LocalStoreIO
        >,
        vaultID: String
    ) throws -> AtlasVaultLocalStoreEnvelope? {
        let root = try runtimeServices.rootDirectoryProvider
            .rootDirectory()
        let services = try runtimeServices.perVaultFactory.makeServices(
            rootURL: root,
            vaultID: vaultID
        )
        let url = try services.pathLocator.localStoreURL(
            vaultID: vaultID
        )
        guard FileManager.default.fileExists(atPath: url.path) else {
            return nil
        }
        let values = try url.resourceValues(
            forKeys: [.isRegularFileKey, .isSymbolicLinkKey]
        )
        guard
            values.isRegularFile == true,
            values.isSymbolicLink != true
        else {
            throw AtlasVaultRecoveryUnlockFailure.unavailable
        }
        return try services.localStoreIO.read(from: url)
    }
}

public struct AtlasVaultUnlockCapabilitiesResolverEnvironment:
    Sendable
{
    let loadLocalKey: @Sendable (String) async throws -> Data?
    let loadStore:
        @Sendable (String) async throws
            -> AtlasVaultLocalStoreEnvelope?

    public init(
        loadLocalKey:
            @escaping @Sendable (String) async throws -> Data?,
        loadStore:
            @escaping @Sendable (String) async throws
                -> AtlasVaultLocalStoreEnvelope?
    ) {
        self.loadLocalKey = loadLocalKey
        self.loadStore = loadStore
    }
}

public struct AtlasVaultProductionUnlockCapabilitiesResolver:
    AtlasVaultUnlockCapabilitiesResolving,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let environment:
        AtlasVaultUnlockCapabilitiesResolverEnvironment

    public init(
        environment: AtlasVaultUnlockCapabilitiesResolverEnvironment
    ) {
        self.environment = environment
    }

    public func capabilities(
        for selectedVaultID: AtlasSelectedVaultID
    ) async throws -> AtlasVaultUnlockCapabilities {
        try Task.checkCancellation()
        let localKeyAvailable: Bool
        do {
            let loadedKey = try await environment.loadLocalKey(
                selectedVaultID.vaultID
            )
            if var loadedKey {
                defer {
                    AtlasVaultRecoveryKeyCodec.bestEffortWipe(&loadedKey)
                }
                localKeyAvailable =
                    loadedKey.count
                        == AtlasVaultRecordCrypto.vaultKeyByteCount
            } else {
                localKeyAvailable = false
            }
        } catch {
            localKeyAvailable = false
        }

        try Task.checkCancellation()
        let recoveryKeyAvailable: Bool
        do {
            if let store = try await environment.loadStore(
                selectedVaultID.vaultID
            ) {
                let metadata =
                    try AtlasVaultVersionedWrappedKeyMetadata(
                        localStoreMetadata: store.vaultMetadata
                    )
                guard metadata.vaultID == selectedVaultID.vaultID else {
                    throw AtlasVaultRecoveryUnlockFailure.unavailable
                }
                recoveryKeyAvailable =
                    AtlasVaultRecoveryUnlockProvider.onlyRecoveryWrap(
                        in: metadata
                    ) != nil
            } else {
                recoveryKeyAvailable = false
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw AtlasVaultRecoveryUnlockFailure.unavailable
        }
        return AtlasVaultUnlockCapabilities(
            localKeyAvailable: localKeyAvailable,
            passphraseAvailable: false,
            recoveryKeyAvailable: recoveryKeyAvailable
        )
    }

    public var description: String {
        "AtlasVaultProductionUnlockCapabilitiesResolver(<redacted>)"
    }

    public var debugDescription: String {
        description
    }

    static func production<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding
    >(
        runtimeServices: AtlasVaultRuntimeServices<
            DirectoryPreparer,
            LocalStoreIO
        >
    ) -> AtlasVaultProductionUnlockCapabilitiesResolver {
        AtlasVaultProductionUnlockCapabilitiesResolver(
            environment: AtlasVaultUnlockCapabilitiesResolverEnvironment(
                loadLocalKey: { vaultID in
                    try runtimeServices.keyStore.loadVaultKey(
                        for: vaultID
                    )
                },
                loadStore: { vaultID in
                    try AtlasVaultRecoveryUnlockProvider
                        .loadProductionStoreForResolver(
                            runtimeServices: runtimeServices,
                            vaultID: vaultID
                        )
                }
            )
        )
    }
}

private extension AtlasVaultRecoveryUnlockProvider {
    static func loadProductionStoreForResolver<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding
    >(
        runtimeServices: AtlasVaultRuntimeServices<
            DirectoryPreparer,
            LocalStoreIO
        >,
        vaultID: String
    ) throws -> AtlasVaultLocalStoreEnvelope? {
        try loadProductionStore(
            runtimeServices: runtimeServices,
            vaultID: vaultID
        )
    }
}
