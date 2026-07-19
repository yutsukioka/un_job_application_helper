import Foundation

protocol AtlasPublicJobAPIClient: Sendable {
    func health() async throws -> AtlasHealthSummary
    func search(_ request: AtlasSearchRequest) async throws
        -> AtlasSearchResponse
    func jobDetail(_ jobKey: String) async throws -> AtlasJobDetail
    func sources() async throws -> AtlasSourcesResponse
    func updates() async throws -> AtlasUpdatesResponse
}

extension AtlasAPIClient: AtlasPublicJobAPIClient {}

public actor AtlasAPIClientPublicJobAdapter:
    AtlasPublicJobSearching,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private static let defaultProvenanceCapacity = 200

    private let client: any AtlasPublicJobAPIClient
    private let provenanceCapacity: Int
    private var issuedJobs: [String: AtlasLockedPublicJob] = [:]
    private var issuanceOrder: [String] = []

    public init(client: AtlasAPIClient) {
        self.client = client
        provenanceCapacity = Self.defaultProvenanceCapacity
    }

    init(
        client: any AtlasPublicJobAPIClient,
        provenanceCapacity: Int = defaultProvenanceCapacity
    ) {
        self.client = client
        self.provenanceCapacity = max(1, provenanceCapacity)
    }

    public nonisolated var description: String {
        "AtlasAPIClientPublicJobAdapter(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    public func health() async throws(AtlasPublicJobServiceError)
        -> AtlasPublicServiceHealth
    {
        let response: AtlasHealthSummary
        do {
            response = try await client.health()
        } catch {
            throw Self.mapClientError(error)
        }
        return try AtlasProductionPublicProjection.health(response)
    }

    public func search(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult {
        let apiRequest = AtlasSearchRequest(
            text: request.query,
            status: ["open"],
            includeLowConfidence: false,
            includeFacets: false,
            limit: request.limit,
            offset: request.offset,
            sort: "closing_date_asc"
        )
        let response: AtlasSearchResponse
        do {
            response = try await client.search(apiRequest)
        } catch {
            throw Self.mapClientError(error)
        }

        let result = try AtlasProductionPublicProjection.searchResult(
            response,
            expectedLimit: request.limit,
            expectedOffset: request.offset
        )
        authorize(result.jobs)
        return result
    }

    public func sources() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicSourceStatus]
    {
        let response: AtlasSourcesResponse
        do {
            response = try await client.sources()
        } catch {
            throw Self.mapClientError(error)
        }
        return try response.sources.map(AtlasProductionPublicProjection.source)
    }

    public func updates() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicUpdateStatus]
    {
        let response: AtlasUpdatesResponse
        do {
            response = try await client.updates()
        } catch {
            throw Self.mapClientError(error)
        }
        return try response.recentSourceRuns.map(
            AtlasProductionPublicProjection.update
        )
    }

    public func detail(
        for reference: AtlasPublicJobReference
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobDetailResult {
        guard let issuedJob = issuedJobs[reference.publicJobID] else {
            throw .invalidRequest
        }

        let response: AtlasJobDetail
        do {
            response = try await client.jobDetail(reference.publicJobID)
        } catch {
            throw Self.mapClientError(error)
        }
        guard response.jobKey == reference.publicJobID else {
            throw .invalidResponse
        }
        let text = try AtlasProductionPublicProjection.detailText(response)
        do {
            return try AtlasPublicJobDetailResult(
                reference: reference,
                job: issuedJob,
                detailText: text
            )
        } catch {
            throw .invalidResponse
        }
    }

    func authorizedReferenceCountForTesting() -> Int {
        issuedJobs.count
    }

    private func authorize(_ jobs: [AtlasLockedPublicJob]) {
        for job in jobs {
            if issuedJobs[job.id] != nil {
                issuanceOrder.removeAll { $0 == job.id }
            }
            issuedJobs[job.id] = job
            issuanceOrder.append(job.id)
            while issuanceOrder.count > provenanceCapacity {
                let evicted = issuanceOrder.removeFirst()
                issuedJobs.removeValue(forKey: evicted)
            }
        }
    }

    private static func mapClientError(
        _ error: any Error
    ) -> AtlasPublicJobServiceError {
        if error is DecodingError {
            return .invalidResponse
        }
        guard let apiError = error as? AtlasAPIError else {
            return .unavailable
        }
        switch apiError {
        case .invalidResponse:
            return .invalidResponse
        case .httpStatus, .transport:
            return .unavailable
        }
    }
}

enum AtlasProductionPublicProjection {
    private static let excludedPublicDetailSectionTitles: Set<String> = {
        let canonicalTitles = [
            "Job Record",
            "Locations",
            "Source Features",
            "Raw Source Data",
        ]
        return Set(
            canonicalTitles.map {
                $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            }
        )
    }()

    static func health(
        _ value: AtlasHealthSummary
    ) throws(AtlasPublicJobServiceError) -> AtlasPublicServiceHealth {
        let availability = try availability(
            value.status,
            missingIsUnavailable: true
        )
        let lastSyncAt = try optionalDate(value.lastSyncAt)
        do {
            return try AtlasPublicServiceHealth(
                availability: availability,
                openJobCount: value.openJobs,
                enabledSourceCount: value.enabledSources,
                lastSyncAt: lastSyncAt
            )
        } catch {
            throw .invalidResponse
        }
    }

    static func searchResult(
        _ response: AtlasSearchResponse,
        expectedLimit: Int? = nil,
        expectedOffset: Int? = nil
    ) throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult {
        if let expectedLimit, response.limit != expectedLimit {
            throw .invalidResponse
        }
        if let expectedOffset, response.offset != expectedOffset {
            throw .invalidResponse
        }

        let jobs = try projectedJobs(response.results)
        do {
            return try AtlasPublicJobSearchResult(
                jobs: jobs,
                total: response.total,
                limit: response.limit,
                offset: response.offset
            )
        } catch {
            throw .invalidResponse
        }
    }

    static func snapshotJobs(
        _ response: AtlasSearchResponse
    ) throws(AtlasPublicJobServiceError) -> [AtlasLockedPublicJob] {
        guard
            response.total >= 0,
            response.limit > 0,
            response.offset >= 0,
            response.offset <= response.total,
            response.results.count <= response.limit,
            response.results.count <= response.total - response.offset
        else {
            throw .invalidResponse
        }
        return try projectedJobs(response.results)
    }

    private static func projectedJobs(
        _ values: [JobSearchResult]
    ) throws(AtlasPublicJobServiceError) -> [AtlasLockedPublicJob] {
        var seen = Set<String>()
        var jobs: [AtlasLockedPublicJob] = []
        jobs.reserveCapacity(values.count)
        for value in values {
            let projected = try job(value)
            guard seen.insert(projected.id).inserted else {
                throw .invalidResponse
            }
            jobs.append(projected)
        }
        return jobs
    }

    static func job(
        _ value: JobSearchResult
    ) throws(AtlasPublicJobServiceError) -> AtlasLockedPublicJob {
        let id = value.jobKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let title = value.title.trimmingCharacters(in: .whitespacesAndNewlines)
        let organization = value.organizationDisplay.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        let location = value.dutyStation.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        guard
            !id.isEmpty,
            id == value.jobKey,
            !title.isEmpty,
            title != "Untitled vacancy",
            !organization.isEmpty,
            organization != "Unknown organization",
            !location.isEmpty,
            location != "Location not classified",
            value.status == "open"
        else {
            throw .invalidResponse
        }
        return AtlasLockedPublicJob(
            id: id,
            title: title,
            organization: organization,
            location: location,
            closingDateText: value.closingDate.map(stableDateText)
        )
    }

    static func source(
        _ value: AtlasSourceSummary
    ) throws(AtlasPublicJobServiceError) -> AtlasPublicSourceStatus {
        let sourceID = value.sourceID.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        let displayName = value.organization.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        guard
            sourceID == value.sourceID,
            displayName == value.organization,
            value.totalJobs >= 0,
            value.openJobs >= 0
        else {
            throw .invalidResponse
        }
        let sourceAvailability = try availability(
            value.healthStatus,
            missingIsUnavailable: true
        )
        do {
            return try AtlasPublicSourceStatus(
                sourceID: sourceID,
                displayName: displayName,
                availability: sourceAvailability,
                openJobCount: value.openJobs
            )
        } catch {
            throw .invalidResponse
        }
    }

    static func update(
        _ value: AtlasSourceRun
    ) throws(AtlasPublicJobServiceError) -> AtlasPublicUpdateStatus {
        let sourceID = value.sourceID.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        guard
            sourceID == value.sourceID,
            !sourceID.isEmpty,
            value.fetched >= 0,
            value.inserted >= 0,
            value.updated >= 0,
            value.missing >= 0,
            value.closed >= 0
        else {
            throw .invalidResponse
        }
        let (changed, overflow) = value.inserted.addingReportingOverflow(
            value.updated
        )
        guard !overflow else {
            throw .invalidResponse
        }
        let observedAt = try optionalDate(value.observedAt)
        do {
            return try AtlasPublicUpdateStatus(
                sourceID: sourceID,
                observedAt: observedAt,
                fetchedJobCount: value.fetched,
                changedJobCount: changed,
                closedJobCount: value.closed
            )
        } catch {
            throw .invalidResponse
        }
    }

    static func detailText(
        _ detail: AtlasJobDetail
    ) throws(AtlasPublicJobServiceError) -> String {
        var components: [String] = []
        if let description = trimmed(detail.description) {
            components.append(description)
        }
        for section in detail.displaySections {
            let normalizedTitle = section.title
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased()
            if excludedPublicDetailSectionTitles.contains(normalizedTitle) {
                continue
            }
            if let body = trimmed(section.body) {
                components.append(body)
            }
            for row in section.rows {
                if let value = trimmed(row.value) {
                    components.append(value)
                }
            }
        }
        guard !components.isEmpty else {
            throw .invalidResponse
        }
        return components.joined(separator: "\n\n")
    }

    private static func availability(
        _ rawValue: String?,
        missingIsUnavailable: Bool
    ) throws(AtlasPublicJobServiceError) -> AtlasPublicServiceAvailability {
        guard let normalized = trimmed(rawValue)?.lowercased() else {
            if missingIsUnavailable {
                return .unavailable
            }
            throw .invalidResponse
        }
        switch normalized {
        case "ok", "ok_empty", "healthy", "available":
            return .available
        case
            "missing_db",
            "warning",
            "degraded",
            "issue",
            "unavailable",
            "down",
            "error",
            "disabled":
            return .unavailable
        default:
            throw .invalidResponse
        }
    }

    private static func optionalDate(
        _ rawValue: String?
    ) throws(AtlasPublicJobServiceError) -> Date? {
        guard let rawValue = trimmed(rawValue) else {
            return nil
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: rawValue) {
            return date
        }
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: rawValue) else {
            throw .invalidResponse
        }
        return date
    }

    private static func stableDateText(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }

    private static func trimmed(_ value: String?) -> String? {
        guard let value else {
            return nil
        }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
