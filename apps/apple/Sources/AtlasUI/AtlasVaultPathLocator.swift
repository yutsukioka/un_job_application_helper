import Foundation

public enum AtlasVaultPathLocatorError: Error, Equatable, Sendable {
    case invalidRootURL
    case invalidVaultID
}

public protocol AtlasVaultPathLocator: Sendable {
    func localStoreURL(vaultID: String) throws -> URL
}

public struct AtlasInjectedRootVaultPathLocator: AtlasVaultPathLocator {
    public static let atlasDirectoryName = "Atlas"
    public static let vaultsDirectoryName = "Vaults"
    public static let localStoreFileName = "atlasvault-local-store.json"
    public static let maxVaultIDLength = 96

    private let rootURL: URL

    public init(rootURL: URL) throws {
        guard rootURL.isFileURL else {
            throw AtlasVaultPathLocatorError.invalidRootURL
        }
        self.rootURL = rootURL.standardizedFileURL
    }

    public func localStoreURL(vaultID: String) throws -> URL {
        let vaultID = try Self.validatedVaultID(vaultID)
        return rootURL
            .appendingPathComponent(Self.atlasDirectoryName, isDirectory: true)
            .appendingPathComponent(Self.vaultsDirectoryName, isDirectory: true)
            .appendingPathComponent(vaultID, isDirectory: true)
            .appendingPathComponent(Self.localStoreFileName, isDirectory: false)
    }

    public static func validatedVaultID(_ vaultID: String) throws -> String {
        guard
            !vaultID.isEmpty,
            vaultID == vaultID.trimmingCharacters(in: .whitespacesAndNewlines),
            vaultID.count <= maxVaultIDLength,
            vaultID != ".",
            vaultID != ".."
        else {
            throw AtlasVaultPathLocatorError.invalidVaultID
        }

        let allowedCharacters = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "_-"))
        guard vaultID.unicodeScalars.allSatisfy({ allowedCharacters.contains($0) }) else {
            throw AtlasVaultPathLocatorError.invalidVaultID
        }

        let reservedSemanticIDs: Set<String> = [
            "application_note",
            "draft_metadata",
            "profile_snippet",
            "saved_job",
            "saved_search",
        ]
        guard !reservedSemanticIDs.contains(vaultID.lowercased()) else {
            throw AtlasVaultPathLocatorError.invalidVaultID
        }

        return vaultID
    }
}
