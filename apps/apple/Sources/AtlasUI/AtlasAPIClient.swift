import Foundation

public struct AtlasHealthSummary: Codable, Equatable, Sendable {
    public let status: String
    public let dbPath: String?
    public let schemaVersion: String?
    public let openJobs: Int?
    public let enabledSources: Int?
    public let lastSyncAt: String?

    enum CodingKeys: String, CodingKey {
        case status
        case dbPath = "db_path"
        case schemaVersion = "schema_version"
        case openJobs = "open_jobs"
        case enabledSources = "enabled_sources"
        case lastSyncAt = "last_sync_at"
    }
}

public struct AtlasSearchRequest: Codable, Equatable, Sendable {
    public var text: String?
    public var status: [String]
    public var organizations: [String]
    public var sourceIDs: [String]
    public var cities: [String]
    public var countriesISO3: [String]
    public var nationalInternational: [String]
    public var gradeCodes: [String]
    public var ccogFamilies: [String]
    public var capabilityTags: [String]
    public var contractGroups: [String]
    public var seniorityGroups: [String]
    public var workModalities: [String]
    public var volunteerKinds: [String]
    public var unvCategories: [String]
    public var unvVolunteerTypes: [String]
    public var closingDateTo: String?
    public var includeLowConfidence: Bool
    public var includeFacets: Bool
    public var limit: Int
    public var offset: Int
    public var sort: String

    public init(
        text: String? = nil,
        status: [String] = ["open"],
        organizations: [String] = [],
        sourceIDs: [String] = [],
        cities: [String] = [],
        countriesISO3: [String] = [],
        nationalInternational: [String] = [],
        gradeCodes: [String] = [],
        ccogFamilies: [String] = [],
        capabilityTags: [String] = [],
        contractGroups: [String] = [],
        seniorityGroups: [String] = [],
        workModalities: [String] = [],
        volunteerKinds: [String] = [],
        unvCategories: [String] = [],
        unvVolunteerTypes: [String] = [],
        closingDateTo: String? = nil,
        includeLowConfidence: Bool = false,
        includeFacets: Bool = true,
        limit: Int = 50,
        offset: Int = 0,
        sort: String = "closing_date_asc"
    ) {
        self.text = text
        self.status = status
        self.organizations = organizations
        self.sourceIDs = sourceIDs
        self.cities = cities
        self.countriesISO3 = countriesISO3
        self.nationalInternational = nationalInternational
        self.gradeCodes = gradeCodes
        self.ccogFamilies = ccogFamilies
        self.capabilityTags = capabilityTags
        self.contractGroups = contractGroups
        self.seniorityGroups = seniorityGroups
        self.workModalities = workModalities
        self.volunteerKinds = volunteerKinds
        self.unvCategories = unvCategories
        self.unvVolunteerTypes = unvVolunteerTypes
        self.closingDateTo = closingDateTo
        self.includeLowConfidence = includeLowConfidence
        self.includeFacets = includeFacets
        self.limit = limit
        self.offset = offset
        self.sort = sort
    }

    enum CodingKeys: String, CodingKey {
        case text
        case status
        case organizations
        case sourceIDs = "source_ids"
        case cities
        case countriesISO3 = "countries_iso3"
        case nationalInternational = "national_international"
        case gradeCodes = "grade_codes"
        case ccogFamilies = "ccog_families"
        case capabilityTags = "capability_tags"
        case contractGroups = "contract_groups"
        case seniorityGroups = "seniority_groups"
        case workModalities = "work_modalities"
        case volunteerKinds = "volunteer_kinds"
        case unvCategories = "unv_categories"
        case unvVolunteerTypes = "unv_volunteer_types"
        case closingDateTo = "closing_date_to"
        case includeLowConfidence = "include_low_confidence"
        case includeFacets = "include_facets"
        case limit
        case offset
        case sort
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            text: try container.decodeIfPresent(String.self, forKey: .text),
            status: try container.decodeIfPresent([String].self, forKey: .status) ?? ["open"],
            organizations: try container.decodeIfPresent([String].self, forKey: .organizations) ?? [],
            sourceIDs: try container.decodeIfPresent([String].self, forKey: .sourceIDs) ?? [],
            cities: try container.decodeIfPresent([String].self, forKey: .cities) ?? [],
            countriesISO3: try container.decodeIfPresent([String].self, forKey: .countriesISO3) ?? [],
            nationalInternational: try container.decodeIfPresent([String].self, forKey: .nationalInternational) ?? [],
            gradeCodes: try container.decodeIfPresent([String].self, forKey: .gradeCodes) ?? [],
            ccogFamilies: try container.decodeIfPresent([String].self, forKey: .ccogFamilies) ?? [],
            capabilityTags: try container.decodeIfPresent([String].self, forKey: .capabilityTags) ?? [],
            contractGroups: try container.decodeIfPresent([String].self, forKey: .contractGroups) ?? [],
            seniorityGroups: try container.decodeIfPresent([String].self, forKey: .seniorityGroups) ?? [],
            workModalities: try container.decodeIfPresent([String].self, forKey: .workModalities) ?? [],
            volunteerKinds: try container.decodeIfPresent([String].self, forKey: .volunteerKinds) ?? [],
            unvCategories: try container.decodeIfPresent([String].self, forKey: .unvCategories) ?? [],
            unvVolunteerTypes: try container.decodeIfPresent([String].self, forKey: .unvVolunteerTypes) ?? [],
            closingDateTo: try container.decodeIfPresent(String.self, forKey: .closingDateTo),
            includeLowConfidence: try container.decodeIfPresent(Bool.self, forKey: .includeLowConfidence) ?? false,
            includeFacets: try container.decodeIfPresent(Bool.self, forKey: .includeFacets) ?? true,
            limit: try container.decodeIfPresent(Int.self, forKey: .limit) ?? 50,
            offset: try container.decodeIfPresent(Int.self, forKey: .offset) ?? 0,
            sort: try container.decodeIfPresent(String.self, forKey: .sort) ?? "closing_date_asc"
        )
    }
}

public struct AtlasSearchResponse: Codable, Sendable {
    public let total: Int
    public let limit: Int
    public let offset: Int
    public let results: [JobSearchResult]
    public let facets: [String: [String: Int]]
    public let facetLabels: [String: [String: String]]
    public let unclassifiedCount: Int

    enum CodingKeys: String, CodingKey {
        case total
        case limit
        case offset
        case results
        case facets
        case facetLabels = "facet_labels"
        case unclassifiedCount = "unclassified_count"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        total = try container.decode(Int.self, forKey: .total)
        limit = try container.decode(Int.self, forKey: .limit)
        offset = try container.decode(Int.self, forKey: .offset)
        if let rawResults = try? container.decode([AtlasJobResultDTO].self, forKey: .results) {
            results = rawResults.map(\.jobSearchResult)
        } else {
            results = try container.decodeIfPresent([JobSearchResult].self, forKey: .results) ?? []
        }
        facets = try container.decodeIfPresent([String: [String: Int]].self, forKey: .facets) ?? [:]
        facetLabels = try container.decodeIfPresent([String: [String: String]].self, forKey: .facetLabels) ?? [:]
        unclassifiedCount = try container.decodeIfPresent(Int.self, forKey: .unclassifiedCount) ?? 0
    }
}

public struct AtlasSavedSearch: Identifiable, Codable, Equatable, Sendable {
    public let name: String
    public let description: String?
    public let request: AtlasSearchRequest
    public let createdAt: String?
    public let updatedAt: String?

    public var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case description
        case request
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct AtlasSavedSearchPayload: Encodable, Sendable {
    public let name: String
    public let request: AtlasSearchRequest
    public let summary: String
}

private struct AtlasDeleteResponse: Decodable, Sendable {
    let deleted: Bool
}

public struct AtlasApplicationRecord: Identifiable, Codable, Equatable, Sendable {
    public let id: String
    public let jobKey: String
    public let status: String
    public let notes: String?
    public let appliedAt: String?
    public let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case jobKey = "job_key"
        case status
        case notes
        case appliedAt = "applied_at"
        case updatedAt = "updated_at"
    }
}

public struct AtlasSourceSummary: Identifiable, Codable, Equatable, Sendable {
    public let sourceID: String
    public let organization: String
    public let totalJobs: Int
    public let openJobs: Int
    public let lastSeenAt: String?
    public let healthStatus: String?
    public let observedAt: String?
    public let detailAttempted: Int?
    public let detailFailed: Int?
    public let missingTransitionAllowed: Bool?

    public var id: String { sourceID }

    enum CodingKeys: String, CodingKey {
        case sourceID = "source_id"
        case organization
        case totalJobs = "total_jobs"
        case openJobs = "open_jobs"
        case lastSeenAt = "last_seen_at"
        case healthStatus = "health_status"
        case observedAt = "observed_at"
        case detailAttempted = "detail_attempted"
        case detailFailed = "detail_failed"
        case missingTransitionAllowed = "missing_transition_allowed"
    }
}

public struct AtlasSourcesResponse: Codable, Sendable {
    public let sources: [AtlasSourceSummary]
}

public struct AtlasUpdatesResponse: Codable, Sendable {
    public let recentSourceRuns: [AtlasSourceRun]

    enum CodingKeys: String, CodingKey {
        case recentSourceRuns = "recent_source_runs"
    }
}

public struct AtlasSourceRun: Identifiable, Codable, Equatable, Sendable {
    public let sourceID: String
    public let fetched: Int
    public let inserted: Int
    public let updated: Int
    public let missing: Int
    public let closed: Int
    public let observedAt: String?

    public var id: String { "\(sourceID)-\(observedAt ?? "")" }

    enum CodingKeys: String, CodingKey {
        case sourceID = "source_id"
        case fetched
        case inserted
        case updated
        case missing
        case closed
        case observedAt = "observed_at"
    }
}

public struct AtlasJobDetail: Codable, Sendable {
    public let jobKey: String
    public let title: String?
    public let description: String?
    public let status: String?
    public let closingDate: String?
    public let closesAtLocal: String?
    public let closesTimezone: String?
    public let applyURL: URL?
    public let sourceURL: URL?
    public let deadlineInfo: AtlasDeadlineInfo?
    public let displaySections: [AtlasDetailSection]

    enum CodingKeys: String, CodingKey {
        case jobKey = "job_key"
        case title
        case description
        case status
        case closingDate = "closes_at"
        case closesAtLocal = "closes_at_local"
        case closesTimezone = "closes_tz"
        case applyURL = "apply_url"
        case sourceURL = "source_url"
        case deadlineInfo = "deadline_info"
        case displaySections = "display_sections"
    }
}

public struct AtlasDeadlineInfo: Codable, Sendable {
    public let storedUTC: String?
    public let sourceLocal: String?
    public let sourceTimezone: String?
    public let sourceText: String?

    enum CodingKeys: String, CodingKey {
        case storedUTC = "stored_utc"
        case sourceLocal = "source_local"
        case sourceTimezone = "source_timezone"
        case sourceText = "source_text"
    }
}

public struct AtlasDetailSection: Identifiable, Codable, Sendable {
    public let title: String
    public let body: String?
    public let rows: [AtlasDetailRow]

    public var id: String { title }

    public init(title: String, body: String? = nil, rows: [AtlasDetailRow] = []) {
        self.title = title
        self.body = body
        self.rows = rows
    }

    enum CodingKeys: String, CodingKey {
        case title
        case body
        case rows
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        title = try container.decode(String.self, forKey: .title)
        body = try container.decodeIfPresent(String.self, forKey: .body)
        rows = try container.decodeIfPresent([AtlasDetailRow].self, forKey: .rows) ?? []
    }
}

public struct AtlasDetailRow: Identifiable, Codable, Sendable {
    public let label: String
    public let value: String

    public var id: String { label }

    public init(label: String, value: String) {
        self.label = label
        self.value = value
    }
}

public enum AtlasAPIError: LocalizedError, Equatable {
    case invalidResponse
    case httpStatus(Int, String)
    case transport(String)

    public var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "The server returned a response without an HTTP status."
        case .httpStatus(let code, let body):
            if body.isEmpty {
                return "The server returned HTTP \(code)."
            }
            return "The server returned HTTP \(code): \(body)"
        case .transport(let message):
            return message
        }
    }
}

public struct AtlasAPIClient: Sendable {
    public static let baseURLDefaultsKey = "atlas.api.baseURL"

    public let baseURL: URL

    public init(baseURL: URL = AtlasAPIClient.defaultBaseURL()) {
        self.baseURL = baseURL
    }

    public static func defaultBaseURL() -> URL {
        if let stored = UserDefaults.standard.string(forKey: baseURLDefaultsKey),
           let url = URL(string: stored),
           url.scheme?.hasPrefix("http") == true {
            return url
        }

        #if os(iOS) && !targetEnvironment(simulator)
        return URL(string: "http://192.168.50.208:8765")!
        #else
        return URL(string: "http://127.0.0.1:8765")!
        #endif
    }

    public static func normalizedBaseURL(from rawValue: String) -> URL? {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        let prefixed = value.contains("://") ? value : "http://\(value)"
        guard var components = URLComponents(string: prefixed),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              components.host != nil else {
            return nil
        }
        components.scheme = scheme
        components.path = ""
        components.query = nil
        components.fragment = nil
        return components.url
    }

    public func health() async throws -> AtlasHealthSummary {
        try await get("api/health")
    }

    public func search(_ request: AtlasSearchRequest) async throws -> AtlasSearchResponse {
        try await post("api/search", body: request)
    }

    public func jobDetail(_ jobKey: String) async throws -> AtlasJobDetail {
        try await get("api/job-detail", queryItems: [URLQueryItem(name: "job_key", value: jobKey)])
    }

    public func savedSearches() async throws -> [AtlasSavedSearch] {
        try await get("api/saved-searches")
    }

    public func saveSearch(name: String, request: AtlasSearchRequest, summary: String) async throws -> AtlasSavedSearch {
        try await post(
            "api/saved-searches",
            body: AtlasSavedSearchPayload(name: name, request: request, summary: summary)
        )
    }

    public func deleteSavedSearch(name: String) async throws -> Bool {
        let response: AtlasDeleteResponse = try await delete("api/saved-searches/\(name)")
        return response.deleted
    }

    public func saveJob(_ jobKey: String) async throws -> AtlasApplicationRecord {
        try await postEmpty("api/tracker/jobs/\(jobKey)")
    }

    public func trackerRecords() async throws -> [AtlasApplicationRecord] {
        try await get("api/tracker")
    }

    public func deleteTrackerRecord(id: String) async throws -> Bool {
        let response: AtlasDeleteResponse = try await delete("api/tracker/\(id)")
        return response.deleted
    }

    public func updates() async throws -> AtlasUpdatesResponse {
        try await get("api/updates")
    }

    public func sources() async throws -> AtlasSourcesResponse {
        try await get("api/sources")
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var request = URLRequest(url: endpoint(path))
        request.httpMethod = "GET"
        return try await decode(request)
    }

    private func get<T: Decodable>(_ path: String, queryItems: [URLQueryItem]) async throws -> T {
        var components = URLComponents(url: endpoint(path), resolvingAgainstBaseURL: false)
        components?.queryItems = queryItems
        guard let url = components?.url else {
            throw AtlasAPIError.invalidResponse
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        return try await decode(request)
    }

    private func post<Body: Encodable, T: Decodable>(_ path: String, body: Body) async throws -> T {
        var request = URLRequest(url: endpoint(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        return try await decode(request)
    }

    private func postEmpty<T: Decodable>(_ path: String) async throws -> T {
        var request = URLRequest(url: endpoint(path))
        request.httpMethod = "POST"
        return try await decode(request)
    }

    private func delete<T: Decodable>(_ path: String) async throws -> T {
        var request = URLRequest(url: endpoint(path))
        request.httpMethod = "DELETE"
        return try await decode(request)
    }

    private func decode<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw AtlasAPIError.transport(transportErrorMessage(error, url: request.url))
        }
        guard let httpResponse = response as? HTTPURLResponse else {
            throw AtlasAPIError.invalidResponse
        }
        guard 200..<300 ~= httpResponse.statusCode else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw AtlasAPIError.httpStatus(httpResponse.statusCode, body)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func endpoint(_ path: String) -> URL {
        baseURL.appendingPathComponent(path)
    }
}

private func transportErrorMessage(_ error: Error, url: URL?) -> String {
    let nsError = error as NSError
    let host = url?.host()
    if nsError.domain == NSURLErrorDomain,
       nsError.code == NSURLErrorNotConnectedToInternet,
       isLocalNetworkHost(host) {
        return "iOS is blocking local network access to \(host ?? "the Mac API"). Enable Local Network for AtlasIOSHost in iPhone Settings, then reopen the app."
    }
    return (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
}

private func isLocalNetworkHost(_ host: String?) -> Bool {
    guard let host = host?.lowercased(), !host.isEmpty else { return false }
    if host == "localhost" || host.hasSuffix(".local") || host.hasPrefix("127.") {
        return true
    }
    if host.hasPrefix("10.") || host.hasPrefix("192.168.") || host.hasPrefix("169.254.") {
        return true
    }
    let parts = host.split(separator: ".").compactMap { Int($0) }
    return parts.count == 4 && parts[0] == 172 && (16...31).contains(parts[1])
}

private struct AtlasJobResultDTO: Decodable, Sendable {
    let jobKey: String
    let title: String?
    let organization: String?
    let sourceID: String?
    let dutyStation: String?
    let gradeCode: String?
    let gradeFamily: String?
    let nationalInternational: String?
    let contractCategory: String?
    let contractGroup: String?
    let seniorityGroup: String?
    let ccogFamilyCode: String?
    let ccogFamilyLabel: String?
    let ccogPrimaryCode: String?
    let ccogPrimaryLabel: String?
    let capabilityTags: [String]?
    let workModality: String?
    let closingDate: String?
    let postedDate: String?
    let status: String?
    let applyURL: String?
    let sourceURL: String?
    let needsReview: Bool?
    let score: Double?
    let scoreReasons: [String]?
    let matchEvidence: AtlasMatchEvidenceDTO?

    enum CodingKeys: String, CodingKey {
        case jobKey = "job_key"
        case title
        case organization
        case sourceID = "source_id"
        case dutyStation = "duty_station"
        case gradeCode = "grade_code"
        case gradeFamily = "grade_family"
        case nationalInternational = "national_international"
        case contractCategory = "contract_category"
        case contractGroup = "contract_group"
        case seniorityGroup = "seniority_group"
        case ccogFamilyCode = "ccog_family_code"
        case ccogFamilyLabel = "ccog_family_label"
        case ccogPrimaryCode = "ccog_primary_code"
        case ccogPrimaryLabel = "ccog_primary_label"
        case capabilityTags = "capability_tags"
        case workModality = "work_modality"
        case closingDate = "closing_date"
        case postedDate = "posted_date"
        case status
        case applyURL = "apply_url"
        case sourceURL = "source_url"
        case needsReview = "needs_review"
        case score
        case scoreReasons = "score_reasons"
        case matchEvidence = "match_evidence"
    }

    var jobSearchResult: JobSearchResult {
        let organizationLabel = displayLabel(organization, fallback: "Unknown organization")
        let source = sourceID ?? "unknown"
        let station = displayLabel(dutyStation, fallback: "Location not classified")
        let grade = displayGrade(gradeCode ?? gradeFamily)
        let contract = displayLabel(contractGroup ?? contractCategory ?? seniorityGroup, fallback: "Contract unknown")
        let modality = displayLabel(workModality, fallback: "Modality unknown")
        let locationEvidence = matchEvidence?.location
        let gradeEvidence = matchEvidence?.grade
        let scoreValue = normalizedScore(score)
        let summary = matchSummary(
            location: locationEvidence,
            grade: gradeEvidence,
            scope: matchEvidence?.scope,
            dutyStation: station
        )

        return JobSearchResult(
            jobKey: jobKey,
            title: title?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
                ?? "Untitled vacancy",
            organization: organizationLabel,
            sourceID: source,
            dutyStation: station,
            gradeCode: grade,
            nationalInternational: nationalInternational,
            contractCategory: contractCategory,
            contractGroup: contractGroup,
            seniorityGroup: seniorityGroup,
            contractLabel: contract,
            workModality: modality,
            ccogFamilyCode: ccogFamilyCode,
            ccogFamilyLabel: ccogFamilyLabel,
            ccogPrimaryCode: ccogPrimaryCode,
            ccogPrimaryLabel: ccogPrimaryLabel,
            capabilityTags: capabilityTags ?? [],
            closingDate: parseAPIDate(closingDate),
            needsReview: needsReview ?? false,
            locationConfidence: locationEvidence?.confidence,
            gradeConfidence: gradeEvidence?.confidence,
            score: scoreValue,
            scoreReasons: scoreReasons ?? [],
            matchSummary: summary,
            description: descriptionFallback(
                title: title,
                organization: organizationLabel,
                dutyStation: station,
                contract: contract,
                modality: modality
            ),
            status: status ?? "unknown",
            postedDate: parseAPIDate(postedDate),
            applyURL: url(applyURL),
            sourceURL: url(sourceURL)
        )
    }

    private func matchSummary(
        location: AtlasLocationEvidenceDTO?,
        grade: AtlasGradeEvidenceDTO?,
        scope: AtlasScopeEvidenceDTO?,
        dutyStation: String
    ) -> String {
        var parts: [String] = []
        if let location {
            let source = displayLabel(location.sourceField, fallback: "location evidence")
            let confidence = percent(location.confidence)
            parts.append("Location matched \(dutyStation) from \(source) at \(confidence).")
        }
        if let grade {
            let source = displayLabel(grade.sourceField, fallback: "grade evidence")
            let confidence = percent(grade.confidence)
            parts.append("Grade \(displayGrade(grade.matchedGrade)) matched from \(source) at \(confidence).")
        }
        if let scope, let matched = scope.matched, !matched.isEmpty {
            parts.append("Scope matched \(displayLabel(matched, fallback: matched)) via \(displayLabel(scope.reason, fallback: "classification")).")
        }
        return parts.isEmpty ? "Matched the current search filters." : parts.joined(separator: " ")
    }

    private func descriptionFallback(
        title: String?,
        organization: String,
        dutyStation: String,
        contract: String,
        modality: String
    ) -> String {
        let cleanTitle = title?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
            ?? "This vacancy"
        return "\(cleanTitle) at \(organization). Server detail loading will provide the full job description; this search row keeps the \(contract), \(modality), and \(dutyStation) evidence available for triage."
    }
}

private struct AtlasMatchEvidenceDTO: Decodable, Sendable {
    let location: AtlasLocationEvidenceDTO?
    let grade: AtlasGradeEvidenceDTO?
    let scope: AtlasScopeEvidenceDTO?
}

private struct AtlasLocationEvidenceDTO: Decodable, Sendable {
    let matchedCity: String?
    let matchedCountryISO3: String?
    let sourceField: String?
    let locationType: String?
    let confidence: Double?

    enum CodingKeys: String, CodingKey {
        case matchedCity = "matched_city"
        case matchedCountryISO3 = "matched_country_iso3"
        case sourceField = "source_field"
        case locationType = "location_type"
        case confidence
    }
}

private struct AtlasGradeEvidenceDTO: Decodable, Sendable {
    let matchedGrade: String?
    let sourceField: String?
    let confidence: Double?

    enum CodingKeys: String, CodingKey {
        case matchedGrade = "matched_grade"
        case sourceField = "source_field"
        case confidence
    }
}

private struct AtlasScopeEvidenceDTO: Decodable, Sendable {
    let matched: String?
    let reason: String?
}

private func parseAPIDate(_ value: String?) -> Date? {
    guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
        return nil
    }
    let isoFormatter = ISO8601DateFormatter()
    isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = isoFormatter.date(from: value) {
        return date
    }
    isoFormatter.formatOptions = [.withInternetDateTime]
    if let date = isoFormatter.date(from: value) {
        return date
    }
    let dateOnlyFormatter = DateFormatter()
    dateOnlyFormatter.calendar = Calendar(identifier: .gregorian)
    dateOnlyFormatter.locale = Locale(identifier: "en_US_POSIX")
    dateOnlyFormatter.dateFormat = "yyyy-MM-dd"
    return dateOnlyFormatter.date(from: value)
}

private func displayLabel(_ value: String?, fallback: String) -> String {
    guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
        return fallback
    }
    return value
        .replacingOccurrences(of: "_", with: " ")
        .split(separator: " ")
        .map { word in
            if word.count <= 4 && word.allSatisfy(\.isLetter) {
                return word.uppercased()
            }
            return word.prefix(1).uppercased() + word.dropFirst()
        }
        .joined(separator: " ")
}

private func displayGrade(_ value: String?) -> String {
    guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
        return "Grade unknown"
    }
    let compact = value.replacingOccurrences(of: "-", with: "").uppercased()
    if compact.count >= 2,
       let firstDigit = compact.firstIndex(where: \.isNumber),
       firstDigit > compact.startIndex {
        let prefix = compact[..<firstDigit]
        let suffix = compact[firstDigit...]
        return "\(prefix)-\(suffix)"
    }
    return value.uppercased()
}

private func normalizedScore(_ value: Double?) -> Double? {
    guard let value else { return nil }
    if value > 1 {
        return min(max(value / 100, 0), 1)
    }
    return min(max(value, 0), 1)
}

private func percent(_ value: Double?) -> String {
    guard let value else { return "unknown confidence" }
    return "\(Int((value * 100).rounded())) percent confidence"
}

private func url(_ value: String?) -> URL? {
    guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
        return nil
    }
    return URL(string: value)
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}
