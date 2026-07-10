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
        guard AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(storeURL),
              AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(rootDirectory)
        else {
            throw AtlasVaultDirectoryError.invalidURL
        }

        let inputRootURL = rootDirectory.standardized
        let inputParentURL = storeURL.deletingLastPathComponent().standardized
        let rootURL = inputRootURL.resolvingSymlinksInPath().standardizedFileURL
        let fileManager = FileManager.default

        guard let relativeParentComponents = Self.relativePathComponents(from: inputRootURL, to: inputParentURL) else {
            throw AtlasVaultDirectoryError.pathEscapesRoot
        }
        let parentURL = Self.appending(relativeParentComponents, to: rootURL)

        try Self.validateExistingRoot(rootURL, fileManager: fileManager)
        guard Self.isContained(parentURL, in: rootURL) else {
            throw AtlasVaultDirectoryError.pathEscapesRoot
        }
        try Self.validateExistingParentComponents(from: inputRootURL, to: inputParentURL, fileManager: fileManager)

        do {
            try fileManager.createDirectory(at: parentURL, withIntermediateDirectories: true)
        } catch {
            if Self.existingPathIsFile(parentURL, fileManager: fileManager) {
                throw AtlasVaultDirectoryError.parentExistsAsFile
            }
            throw AtlasVaultDirectoryError.createDirectoryFailed
        }
    }

    private static func relativePathComponents(from rootURL: URL, to candidateURL: URL) -> ArraySlice<String>? {
        let rootComponents = rootURL.pathComponents
        let candidateComponents = candidateURL.pathComponents
        guard candidateComponents.count >= rootComponents.count else {
            return nil
        }
        guard zip(rootComponents, candidateComponents).allSatisfy({ $0.0 == $0.1 }) else {
            return nil
        }
        return candidateComponents.dropFirst(rootComponents.count)
    }

    private static func appending(_ components: ArraySlice<String>, to rootURL: URL) -> URL {
        var url = rootURL
        for component in components {
            url.appendPathComponent(component, isDirectory: true)
        }
        return url.standardizedFileURL
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
        guard parentComponents.count >= rootComponents.count else {
            throw AtlasVaultDirectoryError.pathEscapesRoot
        }
        guard zip(rootComponents, parentComponents).allSatisfy({ $0.0 == $0.1 }) else {
            throw AtlasVaultDirectoryError.pathEscapesRoot
        }
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
