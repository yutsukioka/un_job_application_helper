import Foundation

public enum AtlasVaultRootDirectoryError: Error, Equatable, Sendable {
    case applicationSupportUnavailable
    case invalidFileURL
    case malformedURL
}

public protocol AtlasVaultRootDirectoryProviding: Sendable {
    func rootDirectory() throws -> URL
}

public protocol AtlasApplicationSupportDirectoryLocating: Sendable {
    func applicationSupportDirectory() throws -> URL
}

public struct AtlasFoundationApplicationSupportDirectoryLocator: AtlasApplicationSupportDirectoryLocating {
    public init() {}

    public func applicationSupportDirectory() throws -> URL {
        let candidates = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )
        guard candidates.count == 1, let directory = candidates.first else {
            throw AtlasVaultRootDirectoryError.applicationSupportUnavailable
        }
        return directory
    }
}

public struct AtlasApplicationSupportVaultRootProvider: AtlasVaultRootDirectoryProviding {
    private let directoryLocator: any AtlasApplicationSupportDirectoryLocating

    public init(directoryLocator: any AtlasApplicationSupportDirectoryLocating) {
        self.directoryLocator = directoryLocator
    }

    public func rootDirectory() throws -> URL {
        let candidate: URL
        do {
            candidate = try directoryLocator.applicationSupportDirectory()
        } catch {
            throw AtlasVaultRootDirectoryError.applicationSupportUnavailable
        }

        guard candidate.isFileURL else {
            throw AtlasVaultRootDirectoryError.invalidFileURL
        }
        guard AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(candidate) else {
            throw AtlasVaultRootDirectoryError.malformedURL
        }

        let standardized = candidate.standardizedFileURL
        guard standardized.path != "/",
              AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(standardized)
        else {
            throw AtlasVaultRootDirectoryError.malformedURL
        }
        return standardized
    }
}
