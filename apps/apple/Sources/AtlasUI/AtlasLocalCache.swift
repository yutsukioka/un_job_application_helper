import Foundation

public struct AtlasPublicLocalSnapshot: Codable, Sendable {
    public let savedAt: Date
    public let baseURL: URL
    public let health: AtlasHealthSummary
    public let searchResponse: AtlasSearchResponse
    public let sources: [AtlasSourceSummary]
    public let recentRuns: [AtlasSourceRun]

    public var jobCount: Int {
        searchResponse.results.count
    }
}

public typealias AtlasLocalSnapshot = AtlasPublicLocalSnapshot

public enum AtlasLocalCache {
    public static let refreshIntervalHoursKey = "atlas.cache.refreshIntervalHours"
    public static let defaultRefreshIntervalHours: Double = 24

    public static var refreshIntervalHours: Double {
        get {
            let stored = UserDefaults.standard.double(forKey: refreshIntervalHoursKey)
            return stored > 0 ? stored : defaultRefreshIntervalHours
        }
        set {
            UserDefaults.standard.set(max(0.25, newValue), forKey: refreshIntervalHoursKey)
        }
    }

    public static func loadSnapshot() -> AtlasLocalSnapshot? {
        do {
            let data = try Data(contentsOf: snapshotURL())
            return try decoder.decode(AtlasLocalSnapshot.self, from: data)
        } catch {
            return nil
        }
    }

    public static func saveSnapshot(_ snapshot: AtlasLocalSnapshot) throws {
        try ensureCacheDirectory()
        let data = try encodedSnapshotData(snapshot)
        try data.write(to: snapshotURL(), options: [.atomic])
    }

    static func saveSnapshot(_ snapshot: AtlasLocalSnapshot, to url: URL) throws {
        let data = try encodedSnapshotData(snapshot)
        try data.write(to: url, options: [.atomic])
    }

    static func encodedSnapshotData(_ snapshot: AtlasLocalSnapshot) throws -> Data {
        try encoder.encode(snapshot)
    }

    public static func prepareDetailStagingDirectory() throws -> URL {
        try ensureCacheDirectory()
        let directory = try detailStagingDirectory()
        let fileManager = FileManager.default
        if fileManager.fileExists(atPath: directory.path) {
            try fileManager.removeItem(at: directory)
        }
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    public static func discardDetailStagingDirectory() {
        guard let directory = try? detailStagingDirectory() else { return }
        try? FileManager.default.removeItem(at: directory)
    }

    public static func commitSnapshot(
        _ snapshot: AtlasLocalSnapshot,
        replacingDetailsWith stagedDetailDirectory: URL?
    ) throws {
        guard let stagedDetailDirectory else {
            try saveSnapshot(snapshot)
            return
        }

        try ensureCacheDirectory()
        let fileManager = FileManager.default
        let currentDetails = try detailDirectory()
        let backupDetails = try detailBackupDirectory()

        if fileManager.fileExists(atPath: backupDetails.path) {
            try fileManager.removeItem(at: backupDetails)
        }
        if fileManager.fileExists(atPath: currentDetails.path) {
            try fileManager.moveItem(at: currentDetails, to: backupDetails)
        }

        do {
            try fileManager.moveItem(at: stagedDetailDirectory, to: currentDetails)
            try saveSnapshot(snapshot)
        } catch {
            if fileManager.fileExists(atPath: currentDetails.path) {
                try? fileManager.removeItem(at: currentDetails)
            }
            if fileManager.fileExists(atPath: backupDetails.path) {
                try? fileManager.moveItem(at: backupDetails, to: currentDetails)
            }
            throw error
        }
        if fileManager.fileExists(atPath: backupDetails.path) {
            try? fileManager.removeItem(at: backupDetails)
        }
    }

    public static func isStale(_ snapshot: AtlasLocalSnapshot, now: Date = .now) -> Bool {
        now.timeIntervalSince(snapshot.savedAt) >= refreshIntervalHours * 3600
    }

    public static func loadDetail(jobKey: String) -> AtlasJobDetail? {
        do {
            let data = try Data(contentsOf: detailURL(jobKey: jobKey))
            return try decoder.decode(AtlasJobDetail.self, from: data)
        } catch {
            return nil
        }
    }

    public static func hasDetail(jobKey: String) -> Bool {
        (try? detailURL(jobKey: jobKey).checkResourceIsReachable()) ?? false
    }

    public static func cachedDetailCount(jobKeys: [String]) -> Int {
        var seen = Set<String>()
        var count = 0
        for jobKey in jobKeys where seen.insert(jobKey).inserted {
            if hasDetail(jobKey: jobKey) {
                count += 1
            }
        }
        return count
    }

    public static func missingDetailJobKeys(jobKeys: [String]) -> [String] {
        var seen = Set<String>()
        return jobKeys.filter { jobKey in
            seen.insert(jobKey).inserted && !hasDetail(jobKey: jobKey)
        }
    }

    public static func saveDetail(_ detail: AtlasJobDetail, jobKey: String) {
        do {
            try ensureCacheDirectory()
            try ensureDetailDirectory()
            try saveDetail(detail, jobKey: jobKey, to: try detailDirectory())
        } catch {
            // Detail caching is opportunistic; the UI should still work when a
            // single cached detail write fails.
        }
    }

    public static func saveDetail(_ detail: AtlasJobDetail, jobKey: String, to directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let data = try encoder.encode(detail)
        try data.write(to: detailURL(jobKey: jobKey, in: directory), options: [.atomic])
    }

    public static func copyExistingDetail(jobKey: String, to directory: URL) -> Bool {
        do {
            let source = try detailURL(jobKey: jobKey)
            guard FileManager.default.fileExists(atPath: source.path) else { return false }
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let destination = try detailURL(jobKey: jobKey, in: directory)
            if FileManager.default.fileExists(atPath: destination.path) {
                try FileManager.default.removeItem(at: destination)
            }
            try FileManager.default.copyItem(at: source, to: destination)
            return true
        } catch {
            return false
        }
    }

    public static func formattedSavedAt(_ date: Date?) -> String {
        guard let date else { return "Never" }
        return date.formatted(.dateTime.year().month(.abbreviated).day().hour().minute())
    }

    private static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }

    private static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    private static func snapshotURL() throws -> URL {
        try cacheDirectory().appendingPathComponent("atlas-local-snapshot.json")
    }

    private static func detailURL(jobKey: String) throws -> URL {
        try detailURL(jobKey: jobKey, in: detailDirectory())
    }

    private static func detailURL(jobKey: String, in directory: URL) throws -> URL {
        directory.appendingPathComponent("\(safeFileName(jobKey)).json")
    }

    private static func ensureCacheDirectory() throws {
        try FileManager.default.createDirectory(
            at: cacheDirectory(),
            withIntermediateDirectories: true
        )
    }

    private static func ensureDetailDirectory() throws {
        try FileManager.default.createDirectory(
            at: detailDirectory(),
            withIntermediateDirectories: true
        )
    }

    private static func cacheDirectory() throws -> URL {
        let base = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        return base.appendingPathComponent("Atlas", isDirectory: true)
    }

    private static func detailDirectory() throws -> URL {
        try cacheDirectory().appendingPathComponent("JobDetails", isDirectory: true)
    }

    private static func detailStagingDirectory() throws -> URL {
        try cacheDirectory().appendingPathComponent("JobDetails.staging", isDirectory: true)
    }

    private static func detailBackupDirectory() throws -> URL {
        try cacheDirectory().appendingPathComponent("JobDetails.previous", isDirectory: true)
    }

    private static func safeFileName(_ value: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        let fallback = UnicodeScalar("_").value
        let scalars = value.unicodeScalars.map { allowed.contains($0) ? $0.value : fallback }
            .compactMap(UnicodeScalar.init)
        return String(String.UnicodeScalarView(scalars)).prefix(180).description
    }
}
