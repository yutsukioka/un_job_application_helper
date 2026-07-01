import Foundation

public struct AtlasLocalSnapshot: Codable, Sendable {
    public let savedAt: Date
    public let baseURL: URL
    public let health: AtlasHealthSummary
    public let searchResponse: AtlasSearchResponse
    public let savedSearches: [AtlasSavedSearch]
    public let savedJobs: [AtlasApplicationRecord]
    public let sources: [AtlasSourceSummary]
    public let recentRuns: [AtlasSourceRun]

    public var jobCount: Int {
        searchResponse.results.count
    }
}

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
        let data = try encoder.encode(snapshot)
        try data.write(to: snapshotURL(), options: [.atomic])
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
            let data = try encoder.encode(detail)
            try data.write(to: detailURL(jobKey: jobKey), options: [.atomic])
        } catch {
            // Detail caching is opportunistic; the UI should still work when a
            // single cached detail write fails.
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
        try detailDirectory().appendingPathComponent("\(safeFileName(jobKey)).json")
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

    private static func safeFileName(_ value: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        let fallback = UnicodeScalar("_").value
        let scalars = value.unicodeScalars.map { allowed.contains($0) ? $0.value : fallback }
            .compactMap(UnicodeScalar.init)
        return String(String.UnicodeScalarView(scalars)).prefix(180).description
    }
}
