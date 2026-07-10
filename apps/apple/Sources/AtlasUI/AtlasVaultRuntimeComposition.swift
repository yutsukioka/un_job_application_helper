import Foundation

public enum AtlasVaultRuntimeFactory {
    public static func production() -> AtlasVaultRuntimeServices<
        AtlasFileManagerVaultDirectoryPreparer,
        AtlasVaultLocalStoreFileIO
    > {
        production(
            directoryLocator: AtlasFoundationApplicationSupportDirectoryLocator(),
            keychainClient: SecItemAtlasKeychainClient(),
            atomicFileSystemClient: AtlasFoundationAtomicFileSystemClient()
        )
    }

    public static func production<
        DirectoryLocator: AtlasApplicationSupportDirectoryLocating,
        KeychainClient: AtlasKeychainClient,
        AtomicFileSystemClient: AtlasVaultAtomicFileSystemClient
    >(
        directoryLocator: DirectoryLocator,
        keychainClient: KeychainClient,
        atomicFileSystemClient: AtomicFileSystemClient
    ) -> AtlasVaultRuntimeServices<
        AtlasFileManagerVaultDirectoryPreparer,
        AtlasVaultLocalStoreFileIO
    > {
        makeServices(
            rootDirectoryProvider: AtlasApplicationSupportVaultRootProvider(
                directoryLocator: directoryLocator
            ),
            keyStore: AtlasKeychainVaultKeyStore(client: keychainClient),
            directoryPreparer: AtlasFileManagerVaultDirectoryPreparer(),
            localStoreIO: AtlasVaultLocalStoreFileIO(),
            atomicStoreWriter: AtlasVaultAtomicStoreWriter(
                fileSystem: atomicFileSystemClient
            ),
            localStoreMerger: AtlasVaultLocalStoreMerger(),
            recordSaver: AtlasVaultRecordSaver(),
            recordHydrator: AtlasVaultRecordHydrator()
        )
    }

    public static func makeServices<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding
    >(
        rootDirectoryProvider: any AtlasVaultRootDirectoryProviding,
        keyStore: any AtlasVaultKeyStore,
        directoryPreparer: DirectoryPreparer,
        localStoreIO: LocalStoreIO,
        atomicStoreWriter: any AtlasVaultAtomicStoreWriting,
        localStoreMerger: any AtlasVaultLocalStoreMerging,
        recordSaver: any AtlasVaultRecordSaving,
        recordHydrator: any AtlasVaultRecordHydrating
    ) -> AtlasVaultRuntimeServices<DirectoryPreparer, LocalStoreIO> {
        AtlasVaultRuntimeServices(
            rootDirectoryProvider: rootDirectoryProvider,
            keyStore: keyStore,
            perVaultFactory: AtlasVaultPerVaultServiceFactory(
                directoryPreparer: directoryPreparer,
                localStoreIO: localStoreIO,
                atomicStoreWriter: atomicStoreWriter,
                localStoreMerger: localStoreMerger,
                recordSaver: recordSaver,
                recordHydrator: recordHydrator
            )
        )
    }
}

public struct AtlasVaultRuntimeServices<
    DirectoryPreparer: AtlasVaultDirectoryPreparer,
    LocalStoreIO: AtlasVaultLocalStoreProviding
>: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public let rootDirectoryProvider: any AtlasVaultRootDirectoryProviding
    public let keyStore: any AtlasVaultKeyStore
    public let perVaultFactory: AtlasVaultPerVaultServiceFactory<DirectoryPreparer, LocalStoreIO>

    init(
        rootDirectoryProvider: any AtlasVaultRootDirectoryProviding,
        keyStore: any AtlasVaultKeyStore,
        perVaultFactory: AtlasVaultPerVaultServiceFactory<DirectoryPreparer, LocalStoreIO>
    ) {
        self.rootDirectoryProvider = rootDirectoryProvider
        self.keyStore = keyStore
        self.perVaultFactory = perVaultFactory
    }

    public var description: String {
        "AtlasVaultRuntimeServices(state: locked, dependencies: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultBoundPathLocator:
    AtlasVaultPathLocator,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let boundVaultID: String
    private let baseLocator: AtlasInjectedRootVaultPathLocator

    init(
        vaultID: String,
        baseLocator: AtlasInjectedRootVaultPathLocator
    ) {
        self.boundVaultID = vaultID
        self.baseLocator = baseLocator
    }

    public func localStoreURL(vaultID: String) throws -> URL {
        guard vaultID == boundVaultID else {
            throw AtlasVaultPathLocatorError.invalidVaultID
        }
        return try baseLocator.localStoreURL(vaultID: boundVaultID)
    }

    public var description: String {
        "AtlasVaultBoundPathLocator(vault: <redacted>, root: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultPerVaultServiceFactory<
    DirectoryPreparer: AtlasVaultDirectoryPreparer,
    LocalStoreIO: AtlasVaultLocalStoreProviding
>: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public let directoryPreparer: DirectoryPreparer
    public let localStoreIO: LocalStoreIO
    public let atomicStoreWriter: any AtlasVaultAtomicStoreWriting
    public let localStoreMerger: any AtlasVaultLocalStoreMerging
    public let recordSaver: any AtlasVaultRecordSaving
    public let recordHydrator: any AtlasVaultRecordHydrating

    init(
        directoryPreparer: DirectoryPreparer,
        localStoreIO: LocalStoreIO,
        atomicStoreWriter: any AtlasVaultAtomicStoreWriting,
        localStoreMerger: any AtlasVaultLocalStoreMerging,
        recordSaver: any AtlasVaultRecordSaving,
        recordHydrator: any AtlasVaultRecordHydrating
    ) {
        self.directoryPreparer = directoryPreparer
        self.localStoreIO = localStoreIO
        self.atomicStoreWriter = atomicStoreWriter
        self.localStoreMerger = localStoreMerger
        self.recordSaver = recordSaver
        self.recordHydrator = recordHydrator
    }

    public func makeServices(
        rootURL: URL,
        vaultID: String
    ) throws -> AtlasVaultPerVaultServices<DirectoryPreparer, LocalStoreIO> {
        let vaultID = try AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID)
        guard AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(rootURL) else {
            throw AtlasVaultPathLocatorError.invalidRootURL
        }
        let standardizedRootURL = rootURL.standardizedFileURL
        guard standardizedRootURL.path != "/",
              AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(standardizedRootURL)
        else {
            throw AtlasVaultPathLocatorError.invalidRootURL
        }
        let baseLocator = try AtlasInjectedRootVaultPathLocator(
            rootURL: standardizedRootURL
        )
        let pathLocator = AtlasVaultBoundPathLocator(
            vaultID: vaultID,
            baseLocator: baseLocator
        )
        let environment = AtlasVaultPersistenceEnvironment(
            rootDirectory: standardizedRootURL,
            pathLocator: pathLocator,
            directoryPreparer: directoryPreparer,
            localStoreIO: localStoreIO,
            atomicStoreWriter: atomicStoreWriter
        )

        return AtlasVaultPerVaultServices(
            vaultID: vaultID,
            pathLocator: pathLocator,
            persistenceCoordinator: AtlasVaultPersistenceCoordinator(
                environment: environment
            ),
            directoryPreparer: directoryPreparer,
            localStoreIO: localStoreIO,
            atomicStoreWriter: atomicStoreWriter,
            localStoreMerger: localStoreMerger,
            recordSaver: recordSaver,
            recordHydrator: recordHydrator
        )
    }

    public var description: String {
        "AtlasVaultPerVaultServiceFactory(dependencies: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultPerVaultServices<
    DirectoryPreparer: AtlasVaultDirectoryPreparer,
    LocalStoreIO: AtlasVaultLocalStoreProviding
>: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public let vaultID: String
    public let pathLocator: AtlasVaultBoundPathLocator
    public let persistenceCoordinator: AtlasVaultPersistenceCoordinator<
        AtlasVaultBoundPathLocator,
        DirectoryPreparer,
        LocalStoreIO
    >
    public let directoryPreparer: DirectoryPreparer
    public let localStoreIO: LocalStoreIO
    public let atomicStoreWriter: any AtlasVaultAtomicStoreWriting
    public let localStoreMerger: any AtlasVaultLocalStoreMerging
    public let recordSaver: any AtlasVaultRecordSaving
    public let recordHydrator: any AtlasVaultRecordHydrating

    init(
        vaultID: String,
        pathLocator: AtlasVaultBoundPathLocator,
        persistenceCoordinator: AtlasVaultPersistenceCoordinator<
            AtlasVaultBoundPathLocator,
            DirectoryPreparer,
            LocalStoreIO
        >,
        directoryPreparer: DirectoryPreparer,
        localStoreIO: LocalStoreIO,
        atomicStoreWriter: any AtlasVaultAtomicStoreWriting,
        localStoreMerger: any AtlasVaultLocalStoreMerging,
        recordSaver: any AtlasVaultRecordSaving,
        recordHydrator: any AtlasVaultRecordHydrating
    ) {
        self.vaultID = vaultID
        self.pathLocator = pathLocator
        self.persistenceCoordinator = persistenceCoordinator
        self.directoryPreparer = directoryPreparer
        self.localStoreIO = localStoreIO
        self.atomicStoreWriter = atomicStoreWriter
        self.localStoreMerger = localStoreMerger
        self.recordSaver = recordSaver
        self.recordHydrator = recordHydrator
    }

    public var description: String {
        "AtlasVaultPerVaultServices(vault: <redacted>, dependencies: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}
