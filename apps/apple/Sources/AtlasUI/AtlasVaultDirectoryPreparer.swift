import Foundation

public enum AtlasVaultDirectoryError: Error, Equatable, Sendable {
    case invalidURL
    case rootNotDirectory
    case pathEscapesRoot
    case parentExistsAsFile
    case createDirectoryFailed
    case unsupportedSymlink
}

public protocol AtlasVaultDirectoryPreparer: Sendable {
    func prepareParentDirectory(for storeURL: URL, under rootDirectory: URL) throws
}

public struct AtlasFileManagerVaultDirectoryPreparer: AtlasVaultDirectoryPreparer {
    public init() {}

    public func prepareParentDirectory(for storeURL: URL, under rootDirectory: URL) throws {
        guard storeURL.isFileURL, rootDirectory.isFileURL else {
            throw AtlasVaultDirectoryError.invalidURL
        }

        let rootURL = rootDirectory.standardizedFileURL
        let parentURL = storeURL.deletingLastPathComponent().standardizedFileURL
        let fileManager = FileManager.default

        try Self.validateExistingRoot(rootURL, fileManager: fileManager)
        guard Self.isContained(parentURL, in: rootURL) else {
            throw AtlasVaultDirectoryError.pathEscapesRoot
        }
        try Self.validateExistingParentComponents(from: rootURL, to: parentURL, fileManager: fileManager)

        do {
            try fileManager.createDirectory(at: parentURL, withIntermediateDirectories: true)
        } catch {
            if Self.existingPathIsFile(parentURL, fileManager: fileManager) {
                throw AtlasVaultDirectoryError.parentExistsAsFile
            }
            throw AtlasVaultDirectoryError.createDirectoryFailed
        }
    }

    private static func validateExistingRoot(_ rootURL: URL, fileManager: FileManager) throws {
        var isDirectory = ObjCBool(false)
        guard fileManager.fileExists(atPath: rootURL.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw AtlasVaultDirectoryError.rootNotDirectory
        }
        if isSymbolicLink(rootURL, fileManager: fileManager) {
            throw AtlasVaultDirectoryError.unsupportedSymlink
        }
    }

    private static func validateExistingParentComponents(
        from rootURL: URL,
        to parentURL: URL,
        fileManager: FileManager
    ) throws {
        let rootComponents = rootURL.pathComponents
        let parentComponents = parentURL.pathComponents
        let relativeComponents = parentComponents.dropFirst(rootComponents.count)
        var currentURL = rootURL

        for component in relativeComponents {
            currentURL.appendPathComponent(component, isDirectory: true)
            var isDirectory = ObjCBool(false)
            guard fileManager.fileExists(atPath: currentURL.path, isDirectory: &isDirectory) else {
                break
            }
            if isSymbolicLink(currentURL, fileManager: fileManager) {
                throw AtlasVaultDirectoryError.unsupportedSymlink
            }
            guard isDirectory.boolValue else {
                throw AtlasVaultDirectoryError.parentExistsAsFile
            }
        }
    }

    private static func isContained(_ candidateURL: URL, in rootURL: URL) -> Bool {
        let rootComponents = rootURL.standardizedFileURL.pathComponents
        let candidateComponents = candidateURL.standardizedFileURL.pathComponents
        guard candidateComponents.count >= rootComponents.count else {
            return false
        }
        return zip(rootComponents, candidateComponents).allSatisfy { $0.0 == $0.1 }
    }

    private static func existingPathIsFile(_ url: URL, fileManager: FileManager) -> Bool {
        var isDirectory = ObjCBool(false)
        return fileManager.fileExists(atPath: url.path, isDirectory: &isDirectory) && !isDirectory.boolValue
    }

    private static func isSymbolicLink(_ url: URL, fileManager: FileManager) -> Bool {
        (try? fileManager.destinationOfSymbolicLink(atPath: url.path)) != nil
    }
}
