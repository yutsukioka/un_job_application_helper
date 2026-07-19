import Foundation

enum AtlasPublicSnapshotFileStatus: Equatable, Sendable {
    case missing
    case regularFile
    case symbolicLink
    case nonRegular
}

enum AtlasPublicSnapshotFileReadError: Error, Equatable, Sendable {
    case unavailable
}

protocol AtlasPublicSnapshotFileReading: Sendable {
    func status(
        at url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> AtlasPublicSnapshotFileStatus
    func resolvedURL(
        for url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> URL
    func read(
        from url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> Data
}

struct AtlasFoundationPublicSnapshotFileReader:
    AtlasPublicSnapshotFileReading
{
    init() {}

    func status(
        at url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> AtlasPublicSnapshotFileStatus {
        do {
            let attributes = try FileManager.default.attributesOfItem(
                atPath: url.path
            )
            switch attributes[.type] as? FileAttributeType {
            case .typeRegular:
                return .regularFile
            case .typeSymbolicLink:
                return .symbolicLink
            default:
                return .nonRegular
            }
        } catch let error as CocoaError
        where error.code == .fileNoSuchFile {
            return .missing
        } catch {
            throw .unavailable
        }
    }

    func resolvedURL(
        for url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> URL {
        url.resolvingSymlinksInPath().standardizedFileURL
    }

    func read(
        from url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> Data {
        do {
            return try Data(contentsOf: url, options: [.mappedIfSafe])
        } catch {
            throw .unavailable
        }
    }
}

public struct AtlasApplicationSupportPublicSnapshotRestorer:
    AtlasPublicSnapshotRestoring,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private static let atlasDirectoryName = "Atlas"
    private static let snapshotFileName = "atlas-local-snapshot.json"
    private static let allowedTopLevelKeys: Set<String> = [
        "savedAt",
        "baseURL",
        "health",
        "searchResponse",
        "sources",
        "recentRuns",
    ]

    private let rootProvider: any AtlasVaultRootDirectoryProviding
    private let fileReader: any AtlasPublicSnapshotFileReading

    public init(rootProvider: any AtlasVaultRootDirectoryProviding) {
        self.rootProvider = rootProvider
        fileReader = AtlasFoundationPublicSnapshotFileReader()
    }

    init(
        rootProvider: any AtlasVaultRootDirectoryProviding,
        fileReader: any AtlasPublicSnapshotFileReading
    ) {
        self.rootProvider = rootProvider
        self.fileReader = fileReader
    }

    public var description: String {
        "AtlasApplicationSupportPublicSnapshotRestorer(<redacted>)"
    }

    public var debugDescription: String {
        description
    }

    public func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
    {
        let root: URL
        do {
            root = try rootProvider.rootDirectory()
        } catch {
            throw .unavailable
        }
        guard Self.isSafeRoot(root) else {
            throw .invalidSnapshot
        }

        let standardizedRoot = root.standardizedFileURL
        let candidate = standardizedRoot
            .appendingPathComponent(Self.atlasDirectoryName, isDirectory: true)
            .appendingPathComponent(Self.snapshotFileName, isDirectory: false)
            .standardizedFileURL
        guard Self.isStrictDescendant(candidate, of: standardizedRoot) else {
            throw .invalidSnapshot
        }

        let status: AtlasPublicSnapshotFileStatus
        do {
            status = try fileReader.status(at: candidate)
        } catch {
            throw .unavailable
        }
        switch status {
        case .missing:
            return nil
        case .nonRegular:
            throw .invalidSnapshot
        case .regularFile, .symbolicLink:
            break
        }

        let resolvedRoot: URL
        let resolvedCandidate: URL
        do {
            resolvedRoot = try fileReader.resolvedURL(for: standardizedRoot)
            resolvedCandidate = try fileReader.resolvedURL(for: candidate)
        } catch {
            throw .unavailable
        }
        guard
            Self.isSafeRoot(resolvedRoot),
            AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(
                resolvedCandidate
            ),
            Self.isStrictDescendant(resolvedCandidate, of: resolvedRoot)
        else {
            throw .invalidSnapshot
        }
        let resolvedStatus: AtlasPublicSnapshotFileStatus
        do {
            resolvedStatus = try fileReader.status(at: resolvedCandidate)
        } catch {
            throw .unavailable
        }
        guard resolvedStatus == .regularFile else {
            throw .invalidSnapshot
        }

        let data: Data
        do {
            data = try fileReader.read(from: resolvedCandidate)
        } catch {
            throw .unavailable
        }

        guard Self.hasOnlyAllowedTopLevelKeys(data) else {
            throw .invalidSnapshot
        }
        let snapshot: AtlasPublicLocalSnapshot
        do {
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            snapshot = try decoder.decode(
                AtlasPublicLocalSnapshot.self,
                from: data
            )
        } catch {
            throw .invalidSnapshot
        }

        do {
            let health = try AtlasProductionPublicProjection.health(
                snapshot.health
            )
            let search = try AtlasProductionPublicProjection.searchResult(
                snapshot.searchResponse
            )
            let sources = try snapshot.sources.map(
                AtlasProductionPublicProjection.source
            )
            let updates = try snapshot.recentRuns.map(
                AtlasProductionPublicProjection.update
            )
            return AtlasProductionPublicSnapshot(
                savedAt: snapshot.savedAt,
                health: health,
                jobs: search.jobs,
                sources: sources,
                updates: updates
            )
        } catch {
            throw .invalidSnapshot
        }
    }

    private static func hasOnlyAllowedTopLevelKeys(_ data: Data) -> Bool {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dictionary = object as? [String: Any]
        else {
            return false
        }
        return Set(dictionary.keys) == allowedTopLevelKeys
    }

    private static func isSafeRoot(_ url: URL) -> Bool {
        let standardized = url.standardizedFileURL
        return AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(standardized)
            && standardized.path != "/"
    }

    private static func isStrictDescendant(
        _ candidate: URL,
        of root: URL
    ) -> Bool {
        let rootComponents = root.standardizedFileURL.pathComponents
        let candidateComponents = candidate.standardizedFileURL.pathComponents
        guard candidateComponents.count > rootComponents.count else {
            return false
        }
        return candidateComponents.prefix(rootComponents.count)
            .elementsEqual(rootComponents)
    }
}
