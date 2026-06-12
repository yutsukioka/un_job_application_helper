import Combine
import Foundation

public enum AtlasServerState: Equatable {
    case preview
    case checking
    case online(AtlasHealthSummary)
    case cached(Date)
    case offline(String)

    var isOffline: Bool {
        if case .offline = self { return true }
        return false
    }
}

@MainActor
public final class AtlasSearchViewModel: ObservableObject {
    @Published public var query: String = ""
    @Published public private(set) var results: [JobSearchResult]
    @Published public private(set) var total: Int
    @Published public private(set) var facets: [String: [String: Int]] = [:]
    @Published public private(set) var facetLabels: [String: [String: String]] = [:]
    @Published public private(set) var filterAvailabilityFacets: [String: [String: Int]] = [:]
    @Published public private(set) var filterAvailabilityLabels: [String: [String: String]] = [:]
    @Published public private(set) var unclassifiedCount: Int = 0
    @Published public private(set) var savedSearches: [AtlasSavedSearch] = []
    @Published public private(set) var savedJobs: [AtlasApplicationRecord] = []
    @Published public private(set) var sources: [AtlasSourceSummary] = []
    @Published public private(set) var recentRuns: [AtlasSourceRun] = []
    @Published public private(set) var serverState: AtlasServerState
    @Published public private(set) var isLoading = false
    @Published public private(set) var lastUpdated: Date?
    @Published public private(set) var errorMessage: String?
    @Published public private(set) var userMessage: String?
    @Published public private(set) var cacheSavedAt: Date?
    @Published public private(set) var cachedJobCount: Int = 0
    @Published public var refreshIntervalHours: Double
    @Published public var filters = AtlasSearchFilters()
    @Published public var sortOrder: SortOrder = .closingSoon

    private var client: AtlasAPIClient
    private let usesPreviewData: Bool
    private var cachedSnapshot: AtlasLocalSnapshot?
    private var cachedAllJobs: [JobSearchResult] = []
    private var hasLoaded = false
    private var scheduledSearchTask: Task<Void, Never>?

    public init(
        client: AtlasAPIClient = AtlasAPIClient(),
        usesPreviewData: Bool = false
    ) {
        self.client = client
        self.usesPreviewData = usesPreviewData
        self.results = usesPreviewData ? JobSearchResult.samples : []
        self.total = usesPreviewData ? JobSearchResult.samples.count : 0
        self.serverState = usesPreviewData ? .preview : .checking
        self.refreshIntervalHours = AtlasLocalCache.refreshIntervalHours
        if !usesPreviewData {
            _ = loadCachedSnapshot()
        }
    }

    public static func preview() -> AtlasSearchViewModel {
        AtlasSearchViewModel(usesPreviewData: true)
    }

    public var apiBaseURL: URL {
        client.baseURL
    }

    public var statusSubtitle: String {
        if isLoading {
            return "Refreshing from local server"
        }
        switch serverState {
        case .preview:
            return "Preview data"
        case .checking:
            return "Checking local server"
        case .online(let health):
            let openJobs = health.openJobs.map { "\($0.formatted()) open jobs" }
            let sources = health.enabledSources.map { "\($0) sources" }
            return [openJobs, sources, health.lastSyncAt.map { "sync \($0)" }]
                .compactMap { $0 }
                .joined(separator: " · ")
        case .cached(let savedAt):
            return "Local save · updated \(AtlasLocalCache.formattedSavedAt(savedAt))"
        case .offline:
            if let cacheSavedAt {
                return "Offline · local save from \(AtlasLocalCache.formattedSavedAt(cacheSavedAt))"
            }
            return usesPreviewData ? "Offline preview data" : "Offline"
        }
    }

    public var activeFilterChips: [AtlasActiveFilterChip] {
        filters.activeChips
    }

    public var activeFilterCount: Int {
        activeFilterChips.count
    }

    public func displayTitle(for chip: AtlasActiveFilterChip) -> String {
        switch chip.id {
        case "organizations":
            return labeledSelectionSummary(
                prefix: "Org",
                values: filters.organizations,
                labels: facetLabels["organizations"] ?? [:]
            )
        case "source.ids":
            let sourceLabels = Dictionary(uniqueKeysWithValues: sources.map {
                ($0.sourceID, displayAtlasFilterValue($0.sourceID))
            })
            return labeledSelectionSummary(prefix: "Source", values: filters.sourceIDs, labels: sourceLabels)
        case "ccog.families":
            return labeledSelectionSummary(
                prefix: "CCOG",
                values: filters.ccogFamilies,
                labels: facetLabels["ccog_families"] ?? [:]
            )
        case "volunteer.kinds":
            return labeledSelectionSummary(
                prefix: "Volunteer",
                values: filters.volunteerKinds,
                labels: facetLabels["volunteer_kinds"] ?? [:]
            )
        case "unv.categories":
            return labeledSelectionSummary(
                prefix: "UNV",
                values: filters.unvCategories,
                labels: facetLabels["unv_categories"] ?? [:]
            )
        default:
            return chip.title
        }
    }

    public func loadIfNeeded() async {
        guard !hasLoaded else { return }
        hasLoaded = true
        if let snapshot = cachedSnapshot ?? AtlasLocalCache.loadSnapshot() {
            applyCachedSnapshot(snapshot)
            if !AtlasLocalCache.isStale(snapshot) {
                return
            }
        }
        await refresh()
    }

    public func refresh() async {
        guard !usesPreviewData else {
            applyPreviewResults()
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let health = try await client.health()
            let snapshot = try await fetchSnapshot(health: health)
            applyCachedSnapshot(snapshot)
            try AtlasLocalCache.saveSnapshot(snapshot)
            userMessage = "Local save refreshed"
        } catch {
            applyOfflineFallback(error)
        }
    }

    public func search() async {
        await search(setsLoading: true)
    }

    public func scheduleSearch() {
        scheduledSearchTask?.cancel()
        scheduledSearchTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            await self?.search()
        }
    }

    public func removeActiveFilter(_ chip: AtlasActiveFilterChip) {
        filters.removeChip(id: chip.id)
        scheduleSearch()
    }

    public func toggleQuickFilter(_ filter: String) {
        switch filter {
        case "Closing soon":
            filters.closingSoon.toggle()
        case "Remote":
            filters.setRemoteOnly(!filters.isRemoteOnly)
        case "Best fit":
            sortOrder = sortOrder == .bestFit ? .closingSoon : .bestFit
        default:
            break
        }
        scheduleSearch()
    }

    public func isQuickFilterActive(_ filter: String) -> Bool {
        switch filter {
        case "Closing soon":
            return filters.closingSoon
        case "Remote":
            return filters.isRemoteOnly
        case "Best fit":
            return sortOrder == .bestFit
        default:
            return false
        }
    }

    public func resetFilters() {
        filters.reset()
        sortOrder = .closingSoon
        scheduleSearch()
    }

    public func updateRefreshInterval(hours: Double) {
        refreshIntervalHours = hours
        AtlasLocalCache.refreshIntervalHours = hours
    }

    public func facetOptions(_ key: String, limit: Int = 8) -> [AtlasFacetOption] {
        facetOptions(from: facets[key] ?? [:], labels: facetLabels[key] ?? [:], limit: limit)
    }

    public func availabilityFacetOptions(
        _ key: String,
        limit: Int = 8,
        selected: Set<String> = []
    ) -> [AtlasFacetOption] {
        let values = filterAvailabilityFacets[key] ?? facets[key] ?? [:]
        let labels = mergedLabels(for: key)
        var options = facetOptions(from: values, labels: labels, limit: limit)
        let existingIDs = Set(options.map(\.id))
        for value in selected.sorted() where !existingIDs.contains(value) {
            options.append(
                AtlasFacetOption(
                    id: value,
                    title: labels[value] ?? displayAtlasFilterValue(value),
                    count: values[value] ?? 0
                )
            )
        }
        return options
    }

    public func isFilterOptionEnabled(key: String, value: String, isSelected: Bool) -> Bool {
        _ = isSelected
        guard let values = filterAvailabilityFacets[key] else {
            return true
        }
        return (values[value] ?? 0) > 0
    }

    public func refreshFilterAvailability() async {
        guard !usesPreviewData else {
            filterAvailabilityFacets = facets
            filterAvailabilityLabels = facetLabels
            return
        }
        guard cachedAllJobs.isEmpty else {
            refreshLocalFilterAvailability()
            return
        }
        let scopes = FilterAvailabilityScope.allCases
        let requests = scopes.map { ($0, searchRequest(clearing: $0)) }
        let client = self.client
        var loadedFacets: [String: [String: Int]] = [:]
        var loadedLabels: [String: [String: String]] = [:]

        await withTaskGroup(of: (FilterAvailabilityScope, AtlasSearchResponse?).self) { group in
            for (scope, request) in requests {
                group.addTask {
                    do {
                        return (scope, try await client.search(request))
                    } catch {
                        return (scope, nil)
                    }
                }
            }
            for await (scope, response) in group {
                guard let response else { continue }
                for key in scope.facetKeys {
                    if let values = response.facets[key] {
                        loadedFacets[key] = values
                    }
                    if let labels = response.facetLabels[key] {
                        loadedLabels[key] = labels
                    }
                }
            }
        }

        filterAvailabilityFacets = loadedFacets
        filterAvailabilityLabels = loadedLabels
    }

    private func facetOptions(
        from values: [String: Int],
        labels: [String: String],
        limit: Int
    ) -> [AtlasFacetOption] {
        return values
            .sorted { lhs, rhs in
                if lhs.value == rhs.value {
                    return lhs.key < rhs.key
                }
                return lhs.value > rhs.value
            }
            .prefix(limit)
            .map {
                AtlasFacetOption(
                    id: $0.key,
                    title: labels[$0.key] ?? displayAtlasFilterValue($0.key),
                    count: $0.value
                )
            }
    }

    private func mergedLabels(for key: String) -> [String: String] {
        var labels = facetLabels[key] ?? [:]
        for (value, label) in filterAvailabilityLabels[key] ?? [:] {
            labels[value] = label
        }
        return labels
    }

    public func refreshSidebarData(forceServer: Bool = false) async {
        guard !usesPreviewData else {
            applyPreviewSidebarData()
            return
        }
        if !forceServer, let snapshot = cachedSnapshot {
            applySidebarData(snapshot)
            return
        }
        do {
            async let saved = client.savedSearches()
            async let tracker = client.trackerRecords()
            async let sourceResponse = client.sources()
            async let updateResponse = client.updates()
            let loadedSavedSearches = try await saved
            let loadedSavedJobs = try await tracker
            let loadedSources = try await sourceResponse
            let loadedUpdates = try await updateResponse
            savedSearches = loadedSavedSearches
            savedJobs = loadedSavedJobs.sorted { lhs, rhs in
                (lhs.updatedAt ?? "") > (rhs.updatedAt ?? "")
            }
            sources = loadedSources.sources
            recentRuns = loadedUpdates.recentSourceRuns
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    public func saveCurrentSearch() async {
        guard !usesPreviewData else { return }
        let name = defaultSavedSearchName()
        do {
            _ = try await client.saveSearch(
                name: name,
                request: searchRequest(),
                summary: activeSearchSummary()
            )
            userMessage = "Saved \(name)"
            await refreshSidebarData(forceServer: true)
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    public func runSavedSearch(_ savedSearch: AtlasSavedSearch) async {
        apply(savedSearch.request)
        await search()
    }

    public func deleteSavedSearch(_ savedSearch: AtlasSavedSearch) async {
        guard !usesPreviewData else { return }
        do {
            if try await client.deleteSavedSearch(name: savedSearch.name) {
                savedSearches.removeAll { $0.name == savedSearch.name }
                userMessage = "Removed \(savedSearch.name)"
                await refreshSidebarData(forceServer: true)
            }
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    public func deleteSavedJob(_ savedJob: AtlasApplicationRecord) async {
        guard !usesPreviewData else { return }
        do {
            if try await client.deleteTrackerRecord(id: savedJob.id) {
                savedJobs.removeAll { $0.id == savedJob.id }
                userMessage = "Removed saved job"
            }
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    public func filterBySource(_ source: AtlasSourceSummary) async {
        filters.sourceIDs = [source.sourceID]
        query = ""
        await search()
    }

    public func clearUserMessage() {
        userMessage = nil
    }

    public func healthSummary(forBaseURL rawValue: String) async -> String {
        guard let url = AtlasAPIClient.normalizedBaseURL(from: rawValue) else {
            return "Enter a valid http:// or https:// API URL."
        }
        do {
            let health = try await AtlasAPIClient(baseURL: url).health()
            let openJobs = health.openJobs.map { "\($0.formatted()) open jobs" } ?? "open job count unavailable"
            let sources = health.enabledSources.map { "\($0) sources" } ?? "source count unavailable"
            return "Connected to \(url.absoluteString): \(openJobs), \(sources)."
        } catch {
            return (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    public func updateAPIBaseURL(_ rawValue: String) async -> String? {
        guard let url = AtlasAPIClient.normalizedBaseURL(from: rawValue) else {
            return "Enter a valid http:// or https:// API URL."
        }
        UserDefaults.standard.set(url.absoluteString, forKey: AtlasAPIClient.baseURLDefaultsKey)
        client = AtlasAPIClient(baseURL: url)
        hasLoaded = false
        await refresh()
        return nil
    }

    private func search(setsLoading: Bool) async {
        guard !usesPreviewData else {
            applyPreviewResults()
            return
        }
        if !cachedAllJobs.isEmpty {
            applyLocalSearch()
            return
        }
        if setsLoading {
            isLoading = true
        }
        defer {
            if setsLoading {
                isLoading = false
            }
        }

        do {
            let response = try await client.search(searchRequest())
            results = sorted(response.results)
            total = response.total
            facets = response.facets
            facetLabels = response.facetLabels
            unclassifiedCount = response.unclassifiedCount
            lastUpdated = .now
            errorMessage = nil
            await refreshFilterAvailability()
        } catch {
            applyOfflineFallback(error)
        }
    }

    private func applyOfflineFallback(_ error: Error) {
        let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        serverState = .offline(message)
        errorMessage = message
        guard usesPreviewData else {
            if let snapshot = cachedSnapshot ?? AtlasLocalCache.loadSnapshot() {
                applyCachedSnapshot(snapshot)
                serverState = .offline(message)
                return
            }
            results = []
            total = 0
            facets = [:]
            facetLabels = [:]
            filterAvailabilityFacets = [:]
            filterAvailabilityLabels = [:]
            unclassifiedCount = 0
            savedSearches = []
            savedJobs = []
            sources = []
            recentRuns = []
            return
        }
        applyPreviewResults()
    }

    private func loadCachedSnapshot() -> Bool {
        guard let snapshot = AtlasLocalCache.loadSnapshot() else { return false }
        applyCachedSnapshot(snapshot)
        return true
    }

    private func applyCachedSnapshot(_ snapshot: AtlasLocalSnapshot) {
        cachedSnapshot = snapshot
        cachedAllJobs = snapshot.searchResponse.results
        cacheSavedAt = snapshot.savedAt
        cachedJobCount = snapshot.jobCount
        serverState = .cached(snapshot.savedAt)
        applySidebarData(snapshot)
        applyLocalSearch()
    }

    private func applySidebarData(_ snapshot: AtlasLocalSnapshot) {
        savedSearches = snapshot.savedSearches
        savedJobs = snapshot.savedJobs.sorted { lhs, rhs in
            (lhs.updatedAt ?? "") > (rhs.updatedAt ?? "")
        }
        sources = snapshot.sources
        recentRuns = snapshot.recentRuns
    }

    private func fetchSnapshot(health: AtlasHealthSummary) async throws -> AtlasLocalSnapshot {
        let openJobs = health.openJobs ?? 10_000
        let limit = max(openJobs + 250, 10_000)
        async let searchResponse = client.search(cacheSearchRequest(limit: limit))
        async let loadedSavedSearches = client.savedSearches()
        async let loadedSavedJobs = client.trackerRecords()
        async let loadedSources = client.sources()
        async let loadedUpdates = client.updates()

        let search = try await searchResponse
        let saved = try await loadedSavedSearches
        let tracker = try await loadedSavedJobs
        let sourceResponse = try await loadedSources
        let updateResponse = try await loadedUpdates

        return AtlasLocalSnapshot(
            savedAt: .now,
            baseURL: client.baseURL,
            health: health,
            searchResponse: search,
            savedSearches: saved,
            savedJobs: tracker,
            sources: sourceResponse.sources,
            recentRuns: updateResponse.recentSourceRuns
        )
    }

    private func cacheSearchRequest(limit: Int) -> AtlasSearchRequest {
        var cacheFilters = AtlasSearchFilters()
        cacheFilters.openOnly = true
        return searchRequest(filters: cacheFilters, query: "", sortOrder: .closingSoon, limit: limit)
    }

    private func applyLocalSearch() {
        let rows = sorted(filteredLocalRows(filters: filters, query: query, rows: cachedAllJobs))
        total = rows.count
        results = Array(rows.prefix(200))
        facets = localFacets(for: rows)
        facetLabels = localFacetLabels(base: cachedSnapshot?.searchResponse.facetLabels ?? [:])
        unclassifiedCount = rows.filter { isUnknownGrade($0.gradeCode) }.count
        lastUpdated = cacheSavedAt
        errorMessage = nil
        refreshLocalFilterAvailability()
    }

    private func refreshLocalFilterAvailability() {
        var loadedFacets: [String: [String: Int]] = [:]
        let labels = localFacetLabels(base: cachedSnapshot?.searchResponse.facetLabels ?? [:])

        for scope in FilterAvailabilityScope.allCases {
            let scopedRows = filteredLocalRows(
                filters: clearedFilters(for: scope),
                query: query,
                rows: cachedAllJobs
            )
            let scopedFacets = localFacets(for: scopedRows)
            for key in scope.facetKeys {
                loadedFacets[key] = scopedFacets[key] ?? [:]
            }
        }

        filterAvailabilityFacets = loadedFacets
        filterAvailabilityLabels = labels
    }

    private func filteredLocalRows(
        filters: AtlasSearchFilters,
        query: String,
        rows: [JobSearchResult]
    ) -> [JobSearchResult] {
        let textTerms = query
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
            .split(separator: " ")
            .map(String.init)
        let city = filters.trimmedCity.lowercased()
        let country = filters.trimmedCountryISO3.lowercased()
        let scopeValues = Set(filters.scope.apiValues.map(normalizedToken))
        let gradeValues = Set(filters.sortedGradeCodes.map(normalizedGrade))
        let workModes = Set(filters.workModalities.map(normalizedToken))
        let contractGroups = Set(filters.contractGroups.map(normalizedToken))
        let seniorityGroups = Set(filters.seniorityGroups.map(normalizedToken))
        let volunteerKinds = Set(filters.volunteerKinds.map(normalizedToken))
        let capabilityTerms = filters.capabilityTerms.map { $0.lowercased() }

        return rows.filter { job in
            if filters.openOnly, job.status.lowercased() != "open" { return false }
            if filters.closingSoon, !isClosingSoon(job) { return false }
            if !city.isEmpty, !job.dutyStation.lowercased().contains(city) { return false }
            if !country.isEmpty, !job.dutyStation.lowercased().contains(country) { return false }
            if !filters.sourceIDs.isEmpty, !filters.sourceIDs.contains(job.sourceID) { return false }
            if !filters.organizations.isEmpty,
               !filters.organizations.contains(job.organization),
               !filters.organizations.contains(job.organizationDisplay) {
                return false
            }
            if !scopeValues.isEmpty, !scopeValues.contains(localScopeValue(job)) { return false }
            if !gradeValues.isEmpty, !gradeValues.contains(normalizedGrade(job.gradeCode)) { return false }
            if !filters.ccogFamilies.isEmpty, !filters.ccogFamilies.contains(job.ccogFamilyCode ?? "") { return false }
            if !workModes.isEmpty, !workModes.contains(normalizedToken(job.workModality)) { return false }
            if !contractGroups.isEmpty, !localContractTokens(job).contains(where: contractGroups.contains) { return false }
            if !seniorityGroups.isEmpty, !seniorityGroups.contains(normalizedToken(job.seniorityGroup ?? "")) { return false }
            if !filters.volunteerKinds.isEmpty,
               !localVolunteerTokens(job).contains(where: volunteerKinds.contains) {
                return false
            }
            if !capabilityTerms.isEmpty, !capabilityTerms.contains(where: { localSearchText(job).contains($0) }) { return false }
            if !textTerms.isEmpty, !textTerms.allSatisfy({ localSearchText(job).contains($0) }) { return false }
            return true
        }
    }

    private func localFacets(for rows: [JobSearchResult]) -> [String: [String: Int]] {
        var output: [String: [String: Int]] = [:]
        for job in rows {
            increment(&output, key: "organizations", value: job.organization)
            increment(&output, key: "source_ids", value: job.sourceID)
            increment(&output, key: "grades", value: normalizedGrade(job.gradeCode), unless: isUnknownGrade(job.gradeCode))
            increment(&output, key: "ccog_families", value: job.ccogFamilyCode)
            increment(&output, key: "contract_groups", value: job.contractGroup)
            increment(&output, key: "seniority_groups", value: job.seniorityGroup)
            increment(&output, key: "work_modalities", value: normalizedToken(job.workModality))
            for tag in job.capabilityTags {
                increment(&output, key: "capability_tags", value: tag)
            }
            for value in localVolunteerTokens(job) {
                increment(&output, key: "volunteer_kinds", value: value)
            }
        }
        return output
    }

    private func localFacetLabels(base: [String: [String: String]]) -> [String: [String: String]] {
        var labels = base
        var organizationLabels = labels["organizations"] ?? [:]
        for job in cachedAllJobs {
            organizationLabels[job.organization] = organizationLabels[job.organization] ?? job.organizationDisplay
        }
        labels["organizations"] = organizationLabels

        var sourceLabels = labels["source_ids"] ?? [:]
        for source in sources {
            sourceLabels[source.sourceID] = sourceLabels[source.sourceID] ?? source.organization
        }
        labels["source_ids"] = sourceLabels

        var gradeLabels = labels["grades"] ?? [:]
        for job in cachedAllJobs {
            let grade = normalizedGrade(job.gradeCode)
            if !grade.isEmpty {
                gradeLabels[grade] = gradeLabels[grade] ?? job.gradeCode
            }
        }
        labels["grades"] = gradeLabels
        return labels
    }

    private func increment(
        _ output: inout [String: [String: Int]],
        key: String,
        value: String?,
        unless skip: Bool = false
    ) {
        guard !skip,
              let value = value?.trimmingCharacters(in: .whitespacesAndNewlines),
              !value.isEmpty else { return }
        output[key, default: [:]][value, default: 0] += 1
    }

    private func localSearchText(_ job: JobSearchResult) -> String {
        [
            job.title,
            job.organization,
            job.sourceID,
            job.dutyStation,
            job.gradeCode,
            job.contractLabel,
            job.contractCategory,
            job.contractGroup,
            job.seniorityGroup,
            job.workModality,
            job.ccogFamilyCode,
            job.ccogFamilyLabel,
            job.ccogPrimaryCode,
            job.ccogPrimaryLabel,
            job.capabilityTags.joined(separator: " "),
            job.description,
        ]
        .compactMap { $0 }
        .joined(separator: " ")
        .lowercased()
    }

    private func localScopeValue(_ job: JobSearchResult) -> String {
        if let value = job.nationalInternational, !value.isEmpty {
            return normalizedToken(value)
        }
        let grade = normalizedGrade(job.gradeCode)
        if grade.hasPrefix("P") || grade.hasPrefix("D") || grade.hasPrefix("IPSA") {
            return "international"
        }
        if grade.hasPrefix("NO") || grade.hasPrefix("NPSA") || grade.hasPrefix("G") {
            return "national"
        }
        return "unknown"
    }

    private func localContractTokens(_ job: JobSearchResult) -> Set<String> {
        Set([job.contractGroup, job.contractCategory, job.contractLabel].compactMap { $0 }.map(normalizedToken))
    }

    private func localVolunteerTokens(_ job: JobSearchResult) -> Set<String> {
        let text = localSearchText(job)
        if text.contains("un volunteer") || job.sourceID == "unv_uvp" {
            return ["un_volunteer"]
        }
        if text.contains("volunteer") {
            return ["volunteer"]
        }
        return []
    }

    private func isClosingSoon(_ job: JobSearchResult) -> Bool {
        guard let closingDate = job.closingDate else { return false }
        let now = Date()
        let soon = Calendar(identifier: .gregorian).date(byAdding: .day, value: 7, to: now) ?? now
        return closingDate >= now && closingDate <= soon
    }

    private func isUnknownGrade(_ value: String) -> Bool {
        let normalized = normalizedGrade(value)
        return normalized.isEmpty || normalized.contains("UNKNOWN")
    }

    private func normalizedGrade(_ value: String) -> String {
        value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "-", with: "")
            .replacingOccurrences(of: " ", with: "")
            .uppercased()
    }

    private func normalizedToken(_ value: String) -> String {
        value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "-", with: "_")
            .replacingOccurrences(of: " ", with: "_")
            .lowercased()
    }

    private func applyPreviewResults() {
        var rows = JobSearchResult.samples
        let searchText = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !searchText.isEmpty {
            rows = rows.filter {
                $0.title.lowercased().contains(searchText)
                    || $0.organization.lowercased().contains(searchText)
                    || $0.dutyStation.lowercased().contains(searchText)
                    || $0.description.lowercased().contains(searchText)
            }
        }
        if filters.closingSoon {
            rows = rows.filter { $0.deadlineUrgency == .soon || $0.deadlineUrgency == .critical }
        }
        if filters.isRemoteOnly {
            rows = rows.filter { $0.workModality.localizedCaseInsensitiveContains("remote") || $0.workModality.localizedCaseInsensitiveContains("home") }
        }
        let city = filters.trimmedCity.lowercased()
        if !city.isEmpty {
            rows = rows.filter { $0.dutyStation.lowercased().contains(city) }
        }
        let country = filters.trimmedCountryISO3.lowercased()
        if !country.isEmpty {
            rows = rows.filter { $0.dutyStation.lowercased().contains(country) || $0.dutyStation.lowercased().contains("kenya") }
        }
        if !filters.gradeCodes.isEmpty {
            rows = rows.filter { filters.gradeCodes.contains($0.gradeCode.replacingOccurrences(of: "-", with: "").uppercased()) }
        }
        if !filters.organizations.isEmpty {
            rows = rows.filter { filters.organizations.contains($0.organization) }
        }
        rows = sorted(rows)
        results = rows
        total = rows.count
        facets = [:]
        facetLabels = [:]
        filterAvailabilityFacets = [:]
        filterAvailabilityLabels = [:]
        unclassifiedCount = 0
        applyPreviewSidebarData()
        lastUpdated = .now
    }

    private func searchRequest() -> AtlasSearchRequest {
        searchRequest(filters: filters, query: query, sortOrder: sortOrder, limit: 50)
    }

    private func searchRequest(clearing scope: FilterAvailabilityScope) -> AtlasSearchRequest {
        searchRequest(filters: clearedFilters(for: scope), query: query, sortOrder: sortOrder, limit: 0)
    }

    private func clearedFilters(for scope: FilterAvailabilityScope) -> AtlasSearchFilters {
        var nextFilters = filters
        switch scope {
        case .contract:
            nextFilters.contractGroups.removeAll()
            nextFilters.volunteerKinds.removeAll()
            nextFilters.unvCategories.removeAll()
            nextFilters.unvVolunteerTypes.removeAll()
        case .seniority:
            nextFilters.seniorityGroups.removeAll()
        case .grade:
            nextFilters.gradeCodes.removeAll()
        case .ccog:
            nextFilters.ccogFamilies.removeAll()
        case .organization:
            nextFilters.organizations.removeAll()
        case .workMode:
            nextFilters.workModalities.removeAll()
        case .capability:
            nextFilters.capabilityTags.removeAll()
            nextFilters.capabilityQuery = ""
        case .unv:
            nextFilters.unvCategories.removeAll()
            nextFilters.unvVolunteerTypes.removeAll()
        }
        return nextFilters
    }

    private func searchRequest(
        filters: AtlasSearchFilters,
        query: String,
        sortOrder: SortOrder,
        limit: Int
    ) -> AtlasSearchRequest {
        let trimmedQuery = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let city = filters.trimmedCity
        let country = filters.trimmedCountryISO3.uppercased()
        return AtlasSearchRequest(
            text: trimmedQuery.isEmpty ? nil : trimmedQuery,
            status: filters.openOnly ? ["open"] : [],
            organizations: filters.organizations.sorted(),
            sourceIDs: filters.sourceIDs.sorted(),
            cities: city.isEmpty ? [] : [city],
            countriesISO3: country.isEmpty ? [] : [country],
            nationalInternational: filters.scope.apiValues,
            gradeCodes: filters.sortedGradeCodes,
            ccogFamilies: filters.ccogFamilies.sorted(),
            capabilityTags: filters.capabilityTerms,
            contractGroups: filters.contractGroups.sorted(),
            seniorityGroups: backendSeniorityGroups(filters),
            workModalities: filters.workModalities.sorted(),
            volunteerKinds: backendVolunteerKinds(filters),
            unvCategories: filters.unvCategories.sorted(),
            unvVolunteerTypes: filters.unvVolunteerTypes.sorted(),
            closingDateTo: filters.closingSoon ? dateOnlyString(daysFromNow: 7) : nil,
            includeLowConfidence: filters.includeLowConfidence,
            includeFacets: true,
            limit: limit,
            offset: 0,
            sort: sortOrder.apiValue
        )
    }

    private func apply(_ request: AtlasSearchRequest) {
        query = request.text ?? ""
        filters.openOnly = request.status.contains("open")
        filters.city = request.cities.first ?? ""
        filters.countryISO3 = request.countriesISO3.first ?? ""
        filters.scope = scopeFilter(for: request.nationalInternational)
        filters.includeLowConfidence = request.includeLowConfidence
        filters.closingSoon = request.closingDateTo != nil
        filters.gradeCodes = Set(request.gradeCodes)
        filters.workModalities = Set(request.workModalities)
        filters.sourceIDs = Set(request.sourceIDs)
        filters.organizations = Set(request.organizations)
        filters.ccogFamilies = Set(request.ccogFamilies)
        filters.contractGroups = Set(request.contractGroups)
        filters.seniorityGroups = Set(request.seniorityGroups)
        filters.volunteerKinds = Set(request.volunteerKinds)
        filters.unvCategories = Set(request.unvCategories)
        filters.unvVolunteerTypes = Set(request.unvVolunteerTypes)
        filters.capabilityTags = []
        filters.capabilityQuery = request.capabilityTags.joined(separator: ", ")
        sortOrder = SortOrder(apiValue: request.sort) ?? .closingSoon
    }

    private func activeSearchSummary() -> String {
        var parts = activeFilterChips.map(\.title)
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            parts.insert("Text: \(trimmed)", at: 0)
        }
        return parts.isEmpty ? "Open vacancies" : parts.joined(separator: " · ")
    }

    private func defaultSavedSearchName() -> String {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            return slug(trimmed)
        }
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmm"
        return "search-\(formatter.string(from: .now))"
    }

    private func slug(_ value: String) -> String {
        let allowed = CharacterSet.alphanumerics
        let chunks = value.lowercased().unicodeScalars.split { !allowed.contains($0) }
        let base = chunks.map { String(String.UnicodeScalarView($0)) }.joined(separator: "-")
        return String(base.prefix(48)).trimmingCharacters(in: CharacterSet(charactersIn: "-")).nilIfEmpty
            ?? "saved-search"
    }

    private func applyPreviewSidebarData() {
        savedSearches = []
        savedJobs = []
        sources = []
        recentRuns = []
    }

    private func labeledSelectionSummary(
        prefix: String,
        values: Set<String>,
        labels: [String: String]
    ) -> String {
        let sorted = values.sorted()
        guard let first = sorted.first else { return prefix }
        let firstDisplay = labels[first] ?? displayAtlasFilterValue(first)
        if sorted.count == 1 {
            return "\(prefix): \(firstDisplay)"
        }
        return "\(prefix): \(firstDisplay) +\(sorted.count - 1)"
    }

    private func scopeFilter(for values: [String]) -> AtlasScopeFilter {
        let normalized = Set(values.map { $0.lowercased() })
        if normalized.contains("international") {
            return .international
        }
        if normalized.contains("national") || normalized.contains("local") {
            return .national
        }
        if normalized.contains("unknown") || normalized.contains("unspecified") {
            return .unspecified
        }
        return .any
    }

    private func sorted(_ rows: [JobSearchResult]) -> [JobSearchResult] {
        switch sortOrder {
        case .bestFit:
            return rows.sorted { ($0.score ?? -1) > ($1.score ?? -1) }
        case .deadlineLatest:
            return rows.sorted { lhs, rhs in
                switch (lhs.closingDate, rhs.closingDate) {
                case let (left?, right?):
                    return left > right
                case (nil, _?):
                    return false
                case (_?, nil):
                    return true
                case (nil, nil):
                    return lhs.title < rhs.title
                }
            }
        case .newestPosted:
            return rows.sorted { lhs, rhs in
                switch (lhs.postedDate, rhs.postedDate) {
                case let (left?, right?):
                    return left > right
                case (nil, _?):
                    return false
                case (_?, nil):
                    return true
                case (nil, nil):
                    return lhs.title < rhs.title
                }
            }
        case .closingSoon:
            return rows.sorted { lhs, rhs in
                switch (lhs.closingDate, rhs.closingDate) {
                case let (left?, right?):
                    return left < right
                case (nil, _?):
                    return false
                case (_?, nil):
                    return true
                case (nil, nil):
                    return lhs.title < rhs.title
                }
            }
        }
    }

    private func backendSeniorityGroups() -> [String] {
        backendSeniorityGroups(filters)
    }

    private func backendSeniorityGroups(_ filters: AtlasSearchFilters) -> [String] {
        filters.seniorityGroups
            .filter { $0 != "generic_volunteer" }
            .sorted()
    }

    private func backendVolunteerKinds() -> [String] {
        backendVolunteerKinds(filters)
    }

    private func backendVolunteerKinds(_ filters: AtlasSearchFilters) -> [String] {
        var kinds = filters.volunteerKinds
        if filters.seniorityGroups.contains("volunteer") {
            kinds.insert(AtlasVolunteerKind.unVolunteer.rawValue)
        }
        if filters.seniorityGroups.contains("generic_volunteer") {
            kinds.insert(AtlasVolunteerKind.volunteer.rawValue)
        }
        return kinds.sorted()
    }

    private func dateOnlyString(daysFromNow: Int) -> String {
        let date = Calendar(identifier: .gregorian).date(byAdding: .day, value: daysFromNow, to: Date()) ?? Date()
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }
}

private enum FilterAvailabilityScope: CaseIterable, Sendable {
    case contract
    case seniority
    case grade
    case ccog
    case organization
    case workMode
    case capability
    case unv

    var facetKeys: [String] {
        switch self {
        case .contract:
            return ["contract_groups", "volunteer_kinds"]
        case .seniority:
            return ["seniority_groups"]
        case .grade:
            return ["grades"]
        case .ccog:
            return ["ccog_families"]
        case .organization:
            return ["organizations"]
        case .workMode:
            return ["work_modalities"]
        case .capability:
            return ["capability_tags"]
        case .unv:
            return ["unv_categories", "unv_volunteer_types"]
        }
    }
}

private extension SortOrder {
    init?(apiValue: String) {
        switch apiValue {
        case "closing_date_asc":
            self = .closingSoon
        case "posted_date_desc":
            self = .newestPosted
        case "closing_date_desc":
            self = .deadlineLatest
        default:
            return nil
        }
    }

    var apiValue: String {
        switch self {
        case .closingSoon, .bestFit:
            return "closing_date_asc"
        case .newestPosted:
            return "posted_date_desc"
        case .deadlineLatest:
            return "closing_date_desc"
        }
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}
