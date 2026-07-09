import Foundation

public struct AtlasVaultUnlockedSession: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public let vaultID: String
    private let vaultKey: Data

    public init(vaultID: String, vaultKey: Data) throws {
        guard vaultKey.count == AtlasVaultRecordCrypto.vaultKeyByteCount else {
            throw AtlasVaultPersistenceError.invalidSession
        }
        self.vaultID = vaultID
        self.vaultKey = vaultKey
    }

    public var vaultKeyByteCount: Int {
        vaultKey.count
    }

    public var description: String {
        "AtlasVaultUnlockedSession(vaultID: \(vaultID), vaultKey: <redacted \(vaultKey.count) bytes>)"
    }

    public var debugDescription: String {
        description
    }

    public func withVaultKey<Result>(_ operation: (Data) throws -> Result) rethrows -> Result {
        try operation(vaultKey)
    }
}

public enum AtlasVaultPersistenceError: Error, Equatable, Sendable {
    case invalidSession
    case corruptStore
    case unsupportedStoreVersion
    case directoryPreparationFailed
    case readFailed
    case writeFailed
    case fileExists
    case cryptoFailed
}

public protocol AtlasVaultLocalStoreProviding: Sendable {
    func read(from url: URL) throws -> AtlasVaultLocalStoreEnvelope
    func write(_ store: AtlasVaultLocalStoreEnvelope, to url: URL, overwrite: Bool) throws
}

public struct AtlasVaultLocalStoreFileIO: AtlasVaultLocalStoreProviding {
    public init() {}

    public func read(from url: URL) throws -> AtlasVaultLocalStoreEnvelope {
        try AtlasVaultLocalStoreIO.read(from: url)
    }

    public func write(_ store: AtlasVaultLocalStoreEnvelope, to url: URL, overwrite: Bool) throws {
        try AtlasVaultLocalStoreIO.write(store, to: url, overwrite: overwrite)
    }
}

public struct AtlasVaultPersistenceEnvironment<
    PathLocator: AtlasVaultPathLocator,
    DirectoryPreparer: AtlasVaultDirectoryPreparer,
    LocalStoreIO: AtlasVaultLocalStoreProviding
>: Sendable {
    public let rootDirectory: URL
    public let pathLocator: PathLocator
    public let directoryPreparer: DirectoryPreparer
    public let localStoreIO: LocalStoreIO

    public init(
        rootDirectory: URL,
        pathLocator: PathLocator,
        directoryPreparer: DirectoryPreparer,
        localStoreIO: LocalStoreIO
    ) {
        self.rootDirectory = rootDirectory
        self.pathLocator = pathLocator
        self.directoryPreparer = directoryPreparer
        self.localStoreIO = localStoreIO
    }
}

public extension AtlasVaultPersistenceEnvironment where LocalStoreIO == AtlasVaultLocalStoreFileIO {
    init(
        rootDirectory: URL,
        pathLocator: PathLocator,
        directoryPreparer: DirectoryPreparer
    ) {
        self.init(
            rootDirectory: rootDirectory,
            pathLocator: pathLocator,
            directoryPreparer: directoryPreparer,
            localStoreIO: AtlasVaultLocalStoreFileIO()
        )
    }
}

public protocol AtlasVaultPersistenceCoordinating: Sendable {
    func loadEncryptedStore(for session: AtlasVaultUnlockedSession) throws -> AtlasVaultLocalStoreEnvelope?
    func saveEncryptedStore(
        _ store: AtlasVaultLocalStoreEnvelope,
        for session: AtlasVaultUnlockedSession,
        overwrite: Bool
    ) throws
}

public struct AtlasVaultPersistenceCoordinator<
    PathLocator: AtlasVaultPathLocator,
    DirectoryPreparer: AtlasVaultDirectoryPreparer,
    LocalStoreIO: AtlasVaultLocalStoreProviding
>: AtlasVaultPersistenceCoordinating {
    private let environment: AtlasVaultPersistenceEnvironment<PathLocator, DirectoryPreparer, LocalStoreIO>

    public init(environment: AtlasVaultPersistenceEnvironment<PathLocator, DirectoryPreparer, LocalStoreIO>) {
        self.environment = environment
    }

    public func loadEncryptedStore(for session: AtlasVaultUnlockedSession) throws -> AtlasVaultLocalStoreEnvelope? {
        let storeURL = try localStoreURL(for: session)
        guard fileExists(at: storeURL) else {
            return nil
        }
        do {
            try environment.directoryPreparer.prepareParentDirectory(
                for: storeURL,
                under: environment.rootDirectory
            )
            try rejectSymbolicLink(at: storeURL)
        } catch {
            throw AtlasVaultPersistenceError.readFailed
        }
        do {
            return try environment.localStoreIO.read(from: storeURL)
        } catch {
            throw Self.persistenceError(for: error, operation: .read)
        }
    }

    public func saveEncryptedStore(
        _ store: AtlasVaultLocalStoreEnvelope,
        for session: AtlasVaultUnlockedSession,
        overwrite: Bool = false
    ) throws {
        let storeURL = try localStoreURL(for: session)
        do {
            try environment.directoryPreparer.prepareParentDirectory(
                for: storeURL,
                under: environment.rootDirectory
            )
        } catch {
            throw AtlasVaultPersistenceError.directoryPreparationFailed
        }
        do {
            try environment.localStoreIO.write(store, to: storeURL, overwrite: overwrite)
        } catch {
            throw Self.persistenceError(for: error, operation: .write)
        }
    }

    private func localStoreURL(for session: AtlasVaultUnlockedSession) throws -> URL {
        do {
            return try environment.pathLocator.localStoreURL(vaultID: session.vaultID)
        } catch {
            throw AtlasVaultPersistenceError.invalidSession
        }
    }

    private func fileExists(at url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.path)
    }

    private func rejectSymbolicLink(at url: URL) throws {
        if (try? FileManager.default.destinationOfSymbolicLink(atPath: url.path)) != nil {
            throw AtlasVaultPersistenceError.readFailed
        }
    }

    private enum StoreOperation {
        case read
        case write
    }

    private static func persistenceError(for error: Error, operation: StoreOperation) -> AtlasVaultPersistenceError {
        guard let storeError = error as? AtlasVaultStoreError else {
            return operation == .read ? .readFailed : .writeFailed
        }
        switch storeError {
        case .unsupportedStoreVersion:
            return .unsupportedStoreVersion
        case .invalidStoreFormat, .invalidEnvelope, .invalidRecord, .invalidJSON:
            return .corruptStore
        case .fileExists:
            return .fileExists
        case .readFailed:
            return .readFailed
        case .writeFailed:
            return .writeFailed
        case .invalidFileURL:
            return operation == .read ? .readFailed : .writeFailed
        }
    }
}
