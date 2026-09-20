import Darwin
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
    public let atomicStoreWriter: any AtlasVaultAtomicStoreWriting

    public init(
        rootDirectory: URL,
        pathLocator: PathLocator,
        directoryPreparer: DirectoryPreparer,
        localStoreIO: LocalStoreIO,
        atomicStoreWriter: any AtlasVaultAtomicStoreWriting = AtlasVaultAtomicStoreWriter()
    ) {
        self.rootDirectory = rootDirectory
        self.pathLocator = pathLocator
        self.directoryPreparer = directoryPreparer
        self.localStoreIO = localStoreIO
        self.atomicStoreWriter = atomicStoreWriter
    }
}

public extension AtlasVaultPersistenceEnvironment where LocalStoreIO == AtlasVaultLocalStoreFileIO {
    init(
        rootDirectory: URL,
        pathLocator: PathLocator,
        directoryPreparer: DirectoryPreparer,
        atomicStoreWriter: any AtlasVaultAtomicStoreWriting = AtlasVaultAtomicStoreWriter()
    ) {
        self.init(
            rootDirectory: rootDirectory,
            pathLocator: pathLocator,
            directoryPreparer: directoryPreparer,
            localStoreIO: AtlasVaultLocalStoreFileIO(),
            atomicStoreWriter: atomicStoreWriter
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

public protocol AtlasVaultAtomicPersistenceCoordinating: AtlasVaultPersistenceCoordinating {
    func saveEncryptedStoreAtomically(
        _ store: AtlasVaultLocalStoreEnvelope,
        for session: AtlasVaultUnlockedSession,
        overwrite: Bool
    ) throws -> AtlasVaultAtomicWriteResult
}

public struct AtlasVaultPersistenceCoordinator<
    PathLocator: AtlasVaultPathLocator,
    DirectoryPreparer: AtlasVaultDirectoryPreparer,
    LocalStoreIO: AtlasVaultLocalStoreProviding
>: AtlasVaultAtomicPersistenceCoordinating {
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

    @discardableResult
    public func saveEncryptedStoreAtomically(
        _ store: AtlasVaultLocalStoreEnvelope,
        for session: AtlasVaultUnlockedSession,
        overwrite: Bool = false
    ) throws -> AtlasVaultAtomicWriteResult {
        let storeURL = try localStoreURL(for: session)
        let atomicDestination = try atomicDestination(for: storeURL)
        do {
            try environment.directoryPreparer.prepareParentDirectory(
                for: atomicDestination.storeURL,
                under: atomicDestination.rootURL
            )
        } catch {
            throw AtlasVaultPersistenceError.directoryPreparationFailed
        }
        return try environment.atomicStoreWriter.write(
            store,
            to: atomicDestination.storeURL,
            overwrite: overwrite
        )
    }

    public func saveEncryptedRecords(
        _ records: [AtlasVaultEncryptedRecordEnvelope],
        for session: AtlasVaultUnlockedSession,
        overwrite: Bool = false
    ) throws {
        try saveEncryptedRecords(
            records,
            for: session,
            overwrite: overwrite,
            merger: AtlasVaultLocalStoreMerger()
        )
    }

    public func saveEncryptedRecords<Merger: AtlasVaultLocalStoreMerging>(
        _ records: [AtlasVaultEncryptedRecordEnvelope],
        for session: AtlasVaultUnlockedSession,
        overwrite: Bool = false,
        merger: Merger
    ) throws {
        guard let currentStore = try loadEncryptedStore(for: session) else {
            throw AtlasVaultPersistenceError.readFailed
        }
        let mergedStore = try merger.merge(records: records, into: currentStore)
        try saveEncryptedStore(mergedStore, for: session, overwrite: overwrite)
    }

    @discardableResult
    public func saveEncryptedRecordsAtomically(
        _ records: [AtlasVaultEncryptedRecordEnvelope],
        for session: AtlasVaultUnlockedSession,
        overwrite: Bool = false
    ) throws -> AtlasVaultAtomicWriteResult {
        try saveEncryptedRecordsAtomically(
            records,
            for: session,
            overwrite: overwrite,
            merger: AtlasVaultLocalStoreMerger()
        )
    }

    @discardableResult
    public func saveEncryptedRecordsAtomically<Merger: AtlasVaultLocalStoreMerging>(
        _ records: [AtlasVaultEncryptedRecordEnvelope],
        for session: AtlasVaultUnlockedSession,
        overwrite: Bool = false,
        merger: Merger
    ) throws -> AtlasVaultAtomicWriteResult {
        guard let currentStore = try loadEncryptedStore(for: session) else {
            throw AtlasVaultPersistenceError.readFailed
        }
        let mergedStore = try merger.merge(records: records, into: currentStore)
        return try saveEncryptedStoreAtomically(
            mergedStore,
            for: session,
            overwrite: overwrite
        )
    }

    private func localStoreURL(for session: AtlasVaultUnlockedSession) throws -> URL {
        do {
            return try environment.pathLocator.localStoreURL(vaultID: session.vaultID)
        } catch {
            throw AtlasVaultPersistenceError.invalidSession
        }
    }

    private func atomicDestination(
        for storeURL: URL
    ) throws -> (rootURL: URL, storeURL: URL) {
        let inputRootURL = environment.rootDirectory.standardized
        let inputStoreURL = storeURL.standardized
        guard AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(environment.rootDirectory),
              AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(storeURL),
              AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(inputRootURL),
              AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(inputStoreURL)
        else {
            throw AtlasVaultPersistenceError.directoryPreparationFailed
        }

        let rootComponents = inputRootURL.pathComponents
        let storeComponents = inputStoreURL.pathComponents
        guard storeComponents.count > rootComponents.count,
              zip(rootComponents, storeComponents).allSatisfy({ $0.0 == $0.1 })
        else {
            throw AtlasVaultPersistenceError.directoryPreparationFailed
        }

        let relativeComponents = storeComponents.dropFirst(rootComponents.count)
        let canonicalRootURL = try canonicalRootURL(inputRootURL)
        var destinationURL = canonicalRootURL
        for (index, component) in relativeComponents.enumerated() {
            guard AtlasVaultFileURLPolicy.isSafePathComponent(component) else {
                throw AtlasVaultPersistenceError.directoryPreparationFailed
            }
            destinationURL.appendPathComponent(
                component,
                isDirectory: index < relativeComponents.count - 1
            )
        }
        let standardizedDestinationURL = destinationURL.standardized
        guard AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(standardizedDestinationURL) else {
            throw AtlasVaultPersistenceError.directoryPreparationFailed
        }
        return (canonicalRootURL, standardizedDestinationURL)
    }

    private func canonicalRootURL(_ rootURL: URL) throws -> URL {
        var resolvedPath = [CChar](repeating: 0, count: Int(PATH_MAX))
        let resolved = rootURL.withUnsafeFileSystemRepresentation { path in
            guard let path else {
                return false
            }
            return resolvedPath.withUnsafeMutableBufferPointer { buffer in
                realpath(path, buffer.baseAddress) != nil
            }
        }
        guard resolved else {
            throw AtlasVaultPersistenceError.directoryPreparationFailed
        }
        let path = String(
            decoding: resolvedPath.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) },
            as: UTF8.self
        )
        let canonicalRootURL = URL(fileURLWithPath: path, isDirectory: true)
        guard AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(canonicalRootURL) else {
            throw AtlasVaultPersistenceError.directoryPreparationFailed
        }
        return canonicalRootURL
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
