import Darwin
import Foundation

public enum AtlasVaultAtomicCommitState: Equatable, Sendable {
    case committed
    case committedDurabilityUnconfirmed
}

public struct AtlasVaultAtomicWriteResult: Equatable, Sendable {
    public let commitState: AtlasVaultAtomicCommitState

    public init(commitState: AtlasVaultAtomicCommitState) {
        self.commitState = commitState
    }
}

public enum AtlasVaultAtomicWriteStage: Equatable, Sendable {
    case temporaryProtection
    case byteWrite
    case validation
    case temporarySynchronization
    case replacement
}

public enum AtlasVaultAtomicWriteError: Error, Equatable, Sendable {
    case invalidDestination
    case parentDirectoryUnavailable
    case unsafePath
    case invalidTemporaryFileName
    case destinationExists
    case temporaryCreationFailed
    case temporaryProtectionFailed
    case writeFailed
    case validationFailed
    case synchronizationFailed
    case replacementFailed
    case cleanupFailed(after: AtlasVaultAtomicWriteStage)
}

public protocol AtlasVaultAtomicStoreWriting: Sendable {
    func write(
        _ store: AtlasVaultLocalStoreEnvelope,
        to destinationURL: URL,
        overwrite: Bool
    ) throws -> AtlasVaultAtomicWriteResult
}

public enum AtlasVaultAtomicFileSystemError: Error, Equatable, Sendable {
    case parentUnavailable
    case unsafePath
    case destinationExists
    case temporaryCreationFailed
    case protectionFailed
    case writeFailed
    case readFailed
    case synchronizationFailed
    case replacementFailed
    case cleanupFailed
}

public protocol AtlasVaultAtomicFileSystemClient: Sendable {
    func validatePreparedParent(for destinationURL: URL) throws
    func createTemporaryFile(at url: URL) throws
    func protectTemporaryFile(at url: URL) throws
    func write(_ data: Data, to url: URL) throws
    func read(from url: URL) throws -> Data
    func synchronizeFile(at url: URL) throws
    func commitTemporaryFile(
        at temporaryURL: URL,
        to destinationURL: URL,
        overwrite: Bool
    ) throws
    func synchronizeDirectory(at url: URL) throws
    func removeItemIfExists(at url: URL) throws
}

public struct AtlasVaultAtomicStoreWriter: AtlasVaultAtomicStoreWriting {
    private let fileSystem: any AtlasVaultAtomicFileSystemClient
    private let temporaryNameGenerator: @Sendable () -> String

    public init(
        fileSystem: any AtlasVaultAtomicFileSystemClient = AtlasFoundationAtomicFileSystemClient()
    ) {
        self.init(
            fileSystem: fileSystem,
            temporaryNameGenerator: { UUID().uuidString.lowercased() }
        )
    }

    init(
        fileSystem: any AtlasVaultAtomicFileSystemClient,
        temporaryNameGenerator: @escaping @Sendable () -> String
    ) {
        self.fileSystem = fileSystem
        self.temporaryNameGenerator = temporaryNameGenerator
    }

    public func write(
        _ store: AtlasVaultLocalStoreEnvelope,
        to destinationURL: URL,
        overwrite: Bool = false
    ) throws -> AtlasVaultAtomicWriteResult {
        let destinationURL = try validatedDestinationURL(destinationURL)

        let encodedStore: Data
        do {
            encodedStore = try AtlasVaultLocalStoreIO.encode(store)
        } catch {
            throw AtlasVaultAtomicWriteError.validationFailed
        }

        do {
            try fileSystem.validatePreparedParent(for: destinationURL)
        } catch let error as AtlasVaultAtomicFileSystemError where error == .unsafePath {
            throw AtlasVaultAtomicWriteError.unsafePath
        } catch {
            throw AtlasVaultAtomicWriteError.parentDirectoryUnavailable
        }

        let temporaryURL = try makeTemporaryURL(nextTo: destinationURL)
        do {
            try fileSystem.createTemporaryFile(at: temporaryURL)
        } catch {
            throw AtlasVaultAtomicWriteError.temporaryCreationFailed
        }

        do {
            try fileSystem.protectTemporaryFile(at: temporaryURL)
        } catch {
            throw cleanupError(
                primary: .temporaryProtectionFailed,
                stage: .temporaryProtection,
                temporaryURL: temporaryURL
            )
        }

        do {
            try fileSystem.write(encodedStore, to: temporaryURL)
        } catch {
            throw cleanupError(
                primary: .writeFailed,
                stage: .byteWrite,
                temporaryURL: temporaryURL
            )
        }

        do {
            let stagedData = try fileSystem.read(from: temporaryURL)
            guard stagedData == encodedStore,
                  try AtlasVaultLocalStoreIO.decode(stagedData) == store
            else {
                throw AtlasVaultAtomicWriteError.validationFailed
            }
        } catch {
            throw cleanupError(
                primary: .validationFailed,
                stage: .validation,
                temporaryURL: temporaryURL
            )
        }

        do {
            try fileSystem.synchronizeFile(at: temporaryURL)
        } catch {
            throw cleanupError(
                primary: .synchronizationFailed,
                stage: .temporarySynchronization,
                temporaryURL: temporaryURL
            )
        }

        do {
            try fileSystem.commitTemporaryFile(
                at: temporaryURL,
                to: destinationURL,
                overwrite: overwrite
            )
        } catch let error as AtlasVaultAtomicFileSystemError where error == .destinationExists {
            throw cleanupError(
                primary: .destinationExists,
                stage: .replacement,
                temporaryURL: temporaryURL
            )
        } catch let error as AtlasVaultAtomicFileSystemError where error == .unsafePath {
            throw cleanupError(
                primary: .unsafePath,
                stage: .replacement,
                temporaryURL: temporaryURL
            )
        } catch {
            throw cleanupError(
                primary: .replacementFailed,
                stage: .replacement,
                temporaryURL: temporaryURL
            )
        }

        do {
            try fileSystem.synchronizeDirectory(at: destinationURL.deletingLastPathComponent())
            return AtlasVaultAtomicWriteResult(commitState: .committed)
        } catch {
            return AtlasVaultAtomicWriteResult(commitState: .committedDurabilityUnconfirmed)
        }
    }

    private func validatedDestinationURL(_ url: URL) throws -> URL {
        guard url.isFileURL, !url.hasDirectoryPath else {
            throw AtlasVaultAtomicWriteError.invalidDestination
        }
        let standardizedURL = url.standardized
        guard !standardizedURL.lastPathComponent.isEmpty,
              standardizedURL.lastPathComponent != ".",
              standardizedURL.lastPathComponent != ".."
        else {
            throw AtlasVaultAtomicWriteError.invalidDestination
        }
        return standardizedURL
    }

    private func makeTemporaryURL(nextTo destinationURL: URL) throws -> URL {
        let token = temporaryNameGenerator()
        guard Self.isValidTemporaryToken(token) else {
            throw AtlasVaultAtomicWriteError.invalidTemporaryFileName
        }
        let parentURL = destinationURL.deletingLastPathComponent().standardized
        let temporaryURL = parentURL
            .appendingPathComponent(".\(token).tmp", isDirectory: false)
            .standardized
        guard temporaryURL != destinationURL,
              temporaryURL.deletingLastPathComponent() == parentURL
        else {
            throw AtlasVaultAtomicWriteError.invalidTemporaryFileName
        }
        return temporaryURL
    }

    private func cleanupError(
        primary: AtlasVaultAtomicWriteError,
        stage: AtlasVaultAtomicWriteStage,
        temporaryURL: URL
    ) -> AtlasVaultAtomicWriteError {
        do {
            try fileSystem.removeItemIfExists(at: temporaryURL)
            return primary
        } catch {
            return .cleanupFailed(after: stage)
        }
    }

    private static func isValidTemporaryToken(_ token: String) -> Bool {
        guard !token.isEmpty, token.count <= 96, token != ".", token != ".." else {
            return false
        }
        return token.unicodeScalars.allSatisfy { scalar in
            switch scalar.value {
            case 48...57, 65...90, 97...122, UnicodeScalar("-").value:
                return true
            default:
                return false
            }
        }
    }
}

public struct AtlasFoundationAtomicFileSystemClient: AtlasVaultAtomicFileSystemClient {
    public init() {}

    public func validatePreparedParent(for destinationURL: URL) throws {
        guard destinationURL.isFileURL else {
            throw AtlasVaultAtomicFileSystemError.unsafePath
        }
        try withParentDirectoryDescriptor(for: destinationURL) { parentDescriptor, fileName in
            switch try pathType(named: fileName, relativeTo: parentDescriptor) {
            case .missing, .regularFile:
                return
            case .symbolicLink, .directory, .other:
                throw AtlasVaultAtomicFileSystemError.unsafePath
            }
        }
    }

    public func createTemporaryFile(at url: URL) throws {
        let descriptor = try withParentDirectoryDescriptor(for: url) { parentDescriptor, fileName in
            fileDescriptor(
                named: fileName,
                relativeTo: parentDescriptor,
                flags: O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                mode: mode_t(S_IRUSR | S_IWUSR)
            )
        }
        guard descriptor >= 0 else {
            throw AtlasVaultAtomicFileSystemError.temporaryCreationFailed
        }
        guard Darwin.close(descriptor) == 0 else {
            try? removeItemIfExists(at: url)
            throw AtlasVaultAtomicFileSystemError.temporaryCreationFailed
        }
    }

    public func protectTemporaryFile(at url: URL) throws {
        let descriptor = try withParentDirectoryDescriptor(for: url) { parentDescriptor, fileName in
            fileDescriptor(
                named: fileName,
                relativeTo: parentDescriptor,
                flags: O_WRONLY | O_NOFOLLOW | O_CLOEXEC
            )
        }
        guard descriptor >= 0 else {
            throw AtlasVaultAtomicFileSystemError.protectionFailed
        }
        let protected = fchmod(descriptor, mode_t(S_IRUSR | S_IWUSR)) == 0
        let closed = Darwin.close(descriptor) == 0
        guard protected, closed else {
            throw AtlasVaultAtomicFileSystemError.protectionFailed
        }
    }

    public func write(_ data: Data, to url: URL) throws {
        let descriptor = try withParentDirectoryDescriptor(for: url) { parentDescriptor, fileName in
            fileDescriptor(
                named: fileName,
                relativeTo: parentDescriptor,
                flags: O_WRONLY | O_NOFOLLOW | O_CLOEXEC
            )
        }
        guard descriptor >= 0 else {
            throw AtlasVaultAtomicFileSystemError.writeFailed
        }

        var writeSucceeded = true
        data.withUnsafeBytes { bytes in
            guard let baseAddress = bytes.baseAddress else {
                return
            }
            var offset = 0
            while offset < bytes.count {
                let written = Darwin.write(
                    descriptor,
                    baseAddress.advanced(by: offset),
                    bytes.count - offset
                )
                if written < 0, errno == EINTR {
                    continue
                }
                guard written > 0 else {
                    writeSucceeded = false
                    return
                }
                offset += written
            }
        }

        let closed = Darwin.close(descriptor) == 0
        guard writeSucceeded, closed else {
            throw AtlasVaultAtomicFileSystemError.writeFailed
        }
    }

    public func read(from url: URL) throws -> Data {
        let descriptor = try withParentDirectoryDescriptor(for: url) { parentDescriptor, fileName in
            fileDescriptor(
                named: fileName,
                relativeTo: parentDescriptor,
                flags: O_RDONLY | O_NOFOLLOW | O_CLOEXEC
            )
        }
        guard descriptor >= 0 else {
            throw AtlasVaultAtomicFileSystemError.readFailed
        }

        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 8_192)
        var readSucceeded = true
        while true {
            let count = buffer.withUnsafeMutableBytes { bytes in
                Darwin.read(descriptor, bytes.baseAddress, bytes.count)
            }
            if count < 0, errno == EINTR {
                continue
            }
            if count < 0 {
                readSucceeded = false
                break
            }
            if count == 0 {
                break
            }
            data.append(contentsOf: buffer.prefix(Int(count)))
        }

        let closed = Darwin.close(descriptor) == 0
        guard readSucceeded, closed else {
            throw AtlasVaultAtomicFileSystemError.readFailed
        }
        return data
    }

    public func synchronizeFile(at url: URL) throws {
        let descriptor = try withParentDirectoryDescriptor(for: url) { parentDescriptor, fileName in
            fileDescriptor(
                named: fileName,
                relativeTo: parentDescriptor,
                flags: O_RDONLY | O_NOFOLLOW | O_CLOEXEC
            )
        }
        guard descriptor >= 0 else {
            throw AtlasVaultAtomicFileSystemError.synchronizationFailed
        }
        let synchronized = fsync(descriptor) == 0
        let closed = Darwin.close(descriptor) == 0
        guard synchronized, closed else {
            throw AtlasVaultAtomicFileSystemError.synchronizationFailed
        }
    }

    public func commitTemporaryFile(
        at temporaryURL: URL,
        to destinationURL: URL,
        overwrite: Bool
    ) throws {
        let temporaryParent = temporaryURL.deletingLastPathComponent().standardized
        let destinationParent = destinationURL.deletingLastPathComponent().standardized
        guard temporaryParent == destinationParent else {
            throw AtlasVaultAtomicFileSystemError.unsafePath
        }

        try withParentDirectoryDescriptor(for: destinationURL) { parentDescriptor, destinationName in
            let temporaryName = temporaryURL.lastPathComponent
            guard try pathType(named: temporaryName, relativeTo: parentDescriptor) == .regularFile else {
                throw AtlasVaultAtomicFileSystemError.unsafePath
            }
            switch try pathType(named: destinationName, relativeTo: parentDescriptor) {
            case .missing, .regularFile:
                break
            case .symbolicLink, .directory, .other:
                throw AtlasVaultAtomicFileSystemError.unsafePath
            }

            let result: Int32 = temporaryName.withCString { sourcePath in
                destinationName.withCString { destinationPath in
                    if overwrite {
                        return Darwin.renameat(
                            parentDescriptor,
                            sourcePath,
                            parentDescriptor,
                            destinationPath
                        )
                    }
                    return renameatx_np(
                        parentDescriptor,
                        sourcePath,
                        parentDescriptor,
                        destinationPath,
                        UInt32(RENAME_EXCL)
                    )
                }
            }

            guard result == 0 else {
                if overwrite {
                    throw AtlasVaultAtomicFileSystemError.replacementFailed
                }
                if errno == EEXIST {
                    throw AtlasVaultAtomicFileSystemError.destinationExists
                }
                throw AtlasVaultAtomicFileSystemError.replacementFailed
            }
        }
    }

    public func synchronizeDirectory(at url: URL) throws {
        let descriptor: Int32
        do {
            descriptor = try openDirectoryDescriptor(at: url)
        } catch {
            throw AtlasVaultAtomicFileSystemError.synchronizationFailed
        }
        let synchronized = fsync(descriptor) == 0
        let closed = Darwin.close(descriptor) == 0
        guard synchronized, closed else {
            throw AtlasVaultAtomicFileSystemError.synchronizationFailed
        }
    }

    public func removeItemIfExists(at url: URL) throws {
        try withParentDirectoryDescriptor(for: url) { parentDescriptor, fileName in
            if try pathType(named: fileName, relativeTo: parentDescriptor) == .missing {
                return
            }
            let result = fileName.withCString { path in
                Darwin.unlinkat(parentDescriptor, path, 0)
            }
            if result != 0, errno != ENOENT {
                throw AtlasVaultAtomicFileSystemError.cleanupFailed
            }
        }
    }

    private enum PathType {
        case missing
        case regularFile
        case directory
        case symbolicLink
        case other
    }

    private func pathType(named fileName: String, relativeTo directoryDescriptor: Int32) throws -> PathType {
        var information = stat()
        let result = fileName.withCString { path in
            fstatat(directoryDescriptor, path, &information, AT_SYMLINK_NOFOLLOW)
        }
        if result != 0 {
            if errno == ENOENT {
                return .missing
            }
            throw AtlasVaultAtomicFileSystemError.unsafePath
        }

        switch information.st_mode & S_IFMT {
        case S_IFREG:
            return .regularFile
        case S_IFDIR:
            return .directory
        case S_IFLNK:
            return .symbolicLink
        default:
            return .other
        }
    }

    private func withParentDirectoryDescriptor<Result>(
        for url: URL,
        _ operation: (Int32, String) throws -> Result
    ) throws -> Result {
        let standardizedURL = url.standardized
        guard standardizedURL.isFileURL,
              standardizedURL.path.hasPrefix("/"),
              !standardizedURL.lastPathComponent.isEmpty
        else {
            throw AtlasVaultAtomicFileSystemError.unsafePath
        }
        let parentDescriptor = try openDirectoryDescriptor(
            at: standardizedURL.deletingLastPathComponent()
        )
        defer { _ = Darwin.close(parentDescriptor) }
        return try operation(parentDescriptor, standardizedURL.lastPathComponent)
    }

    private func openDirectoryDescriptor(at url: URL) throws -> Int32 {
        let standardizedURL = url.standardized
        guard standardizedURL.isFileURL, standardizedURL.path.hasPrefix("/") else {
            throw AtlasVaultAtomicFileSystemError.unsafePath
        }

        var descriptor = Darwin.open(
            "/",
            O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
        )
        guard descriptor >= 0 else {
            throw AtlasVaultAtomicFileSystemError.unsafePath
        }

        for component in standardizedURL.pathComponents.dropFirst() {
            guard !component.isEmpty, component != ".", component != ".." else {
                _ = Darwin.close(descriptor)
                throw AtlasVaultAtomicFileSystemError.unsafePath
            }

            let nextDescriptor = component.withCString { path in
                Darwin.openat(
                    descriptor,
                    path,
                    O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
                )
            }
            guard nextDescriptor >= 0 else {
                let traversalError = directoryTraversalError(
                    component: component,
                    relativeTo: descriptor
                )
                _ = Darwin.close(descriptor)
                throw traversalError
            }
            _ = Darwin.close(descriptor)
            descriptor = nextDescriptor
        }
        return descriptor
    }

    private func directoryTraversalError(
        component: String,
        relativeTo directoryDescriptor: Int32
    ) -> AtlasVaultAtomicFileSystemError {
        var information = stat()
        let result = component.withCString { path in
            fstatat(directoryDescriptor, path, &information, AT_SYMLINK_NOFOLLOW)
        }
        if result == 0, information.st_mode & S_IFMT == S_IFLNK {
            return .unsafePath
        }
        if result == 0 || errno == ENOENT || errno == ENOTDIR {
            return .parentUnavailable
        }
        return .unsafePath
    }

    private func fileDescriptor(
        named fileName: String,
        relativeTo directoryDescriptor: Int32,
        flags: Int32,
        mode: mode_t? = nil
    ) -> Int32 {
        fileName.withCString { path in
            if let mode {
                return Darwin.openat(directoryDescriptor, path, flags, mode)
            }
            return Darwin.openat(directoryDescriptor, path, flags)
        }
    }
}
