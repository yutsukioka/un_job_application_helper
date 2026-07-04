import SwiftUI

public enum AtlasReferenceCaptureMode: String, CaseIterable, Sendable {
    case search
    case filters
    case filterLocation = "filter_location"
    case filterContractSeniority = "filter_contract_seniority"
    case filterGradeCcog = "filter_grade_ccog"
    case filterOrganizationsWorkMode = "filter_organizations_work_mode"
    case filterCapabilityTags = "filter_capability_tags"
    case filterJapan = "filter_japan"
    case filterTokyo = "filter_tokyo"
    case filterEntryJunior = "filter_entry_junior"
    case filterGradeSelected = "filter_grade_selected"
    case detail
    case saved
    case updates
    case sources
    case settings
}

public struct AtlasReferenceCaptureView: View {
    private let mode: AtlasReferenceCaptureMode
    @StateObject private var viewModel: AtlasSearchViewModel
    @State private var selectedJob: JobSearchResult?
    @State private var isFilterSheetPresented = true

    @MainActor
    public init(mode: AtlasReferenceCaptureMode) {
        let snapshot = AtlasReferenceCaptureData.snapshot()
        try? AtlasLocalCache.saveSnapshot(snapshot)
        AtlasReferenceCaptureData.seedDetails()
        let model = AtlasSearchViewModel()
        model.filters = mode.referenceFilters
        self.mode = mode
        _viewModel = StateObject(wrappedValue: model)
        _selectedJob = State(initialValue: snapshot.searchResponse.results.first)
    }

    public var body: some View {
        content
            .task {
                await viewModel.loadIfNeeded()
                if mode.isFilterReference {
                    await viewModel.refreshFilterAvailability()
                }
            }
            .preferredColorScheme(mode.isFilterReference ? .dark : nil)
    }

    @ViewBuilder
    private var content: some View {
        switch mode {
        case .search:
            NavigationStack {
                AtlasSearchScreen(selection: $selectedJob, viewModel: viewModel)
            }
        case .filters:
            FilterSheetView(viewModel: viewModel, isPresented: $isFilterSheetPresented)
        case .filterLocation, .filterContractSeniority, .filterGradeCcog, .filterOrganizationsWorkMode,
             .filterCapabilityTags, .filterJapan, .filterTokyo, .filterEntryJunior, .filterGradeSelected:
            FilterSheetView(
                viewModel: viewModel,
                isPresented: $isFilterSheetPresented,
                initialScrollTarget: mode.filterScrollTarget
            )
        case .detail:
            NavigationStack {
                if let selectedJob {
                    JobDetailView(
                        job: selectedJob,
                        client: AtlasAPIClient(baseURL: URL(string: "http://127.0.0.1:1")!)
                    )
                }
            }
        case .saved:
            NavigationStack {
                SavedPanel(selection: $selectedJob, viewModel: viewModel)
            }
        case .updates:
            NavigationStack {
                UpdatesPanel(viewModel: viewModel)
            }
        case .sources:
            NavigationStack {
                SourcesPanel(viewModel: viewModel)
            }
        case .settings:
            AtlasSettingsPanel(viewModel: viewModel)
        }
    }
}

private extension AtlasReferenceCaptureMode {
    var isFilterReference: Bool {
        switch self {
        case .filters, .filterLocation, .filterContractSeniority, .filterGradeCcog,
             .filterOrganizationsWorkMode, .filterCapabilityTags, .filterJapan, .filterTokyo,
             .filterEntryJunior, .filterGradeSelected:
            return true
        default:
            return false
        }
    }

    var filterScrollTarget: FilterSheetScrollTarget? {
        switch self {
        case .filterLocation, .filterJapan, .filterTokyo:
            return .location
        case .filterContractSeniority:
            return .contract
        case .filterGradeCcog, .filterEntryJunior:
            return .grade
        case .filterOrganizationsWorkMode:
            return .organizations
        case .filterCapabilityTags:
            return .capabilityTags
        case .filterGradeSelected:
            return .seniority
        default:
            return nil
        }
    }

    var referenceFilters: AtlasSearchFilters {
        var filters = AtlasSearchFilters()
        switch self {
        case .filterJapan:
            filters.countryISO3 = "Japan"
        case .filterTokyo:
            filters.city = "Tokyo"
        case .filterEntryJunior:
            filters.seniorityGroups = ["entry_junior"]
        case .filterGradeSelected:
            filters.gradeCodes = ["P2"]
        case .filterCapabilityTags:
            filters.capabilityTags = ["programming", "procurement"]
            filters.capabilityQuery = "data"
        default:
            break
        }
        return filters
    }
}

extension AtlasSearchResponse {
    init(
        total: Int,
        limit: Int,
        offset: Int,
        results: [JobSearchResult],
        facets: [String: [String: Int]],
        facetLabels: [String: [String: String]],
        unclassifiedCount: Int
    ) {
        self.total = total
        self.limit = limit
        self.offset = offset
        self.results = results
        self.facets = facets
        self.facetLabels = facetLabels
        self.unclassifiedCount = unclassifiedCount
    }
}

private enum AtlasReferenceCaptureData {
    private static let baseURL = URL(string: "http://10.253.1.43:8765")!
    private static let observedAt = "2026-07-03T04:58:56Z"

    static func snapshot() -> AtlasLocalSnapshot {
        let jobs = referenceJobs()
        let facets = facets(for: jobs)
        return AtlasLocalSnapshot(
            savedAt: Date(timeIntervalSince1970: 1_783_041_536),
            baseURL: baseURL,
            health: AtlasHealthSummary(
                status: "ok",
                dbPath: "output/all_jobs.sqlite3",
                schemaVersion: "atlas-reference-capture",
                openJobs: 2_420,
                enabledSources: 12,
                lastSyncAt: observedAt
            ),
            searchResponse: AtlasSearchResponse(
                total: jobs.count,
                limit: jobs.count,
                offset: 0,
                results: jobs,
                facets: facets,
                facetLabels: facetLabels(),
                unclassifiedCount: jobs.filter { $0.gradeCode == "UG" || $0.gradeCode == "Unknown" }.count
            ),
            savedSearches: savedSearches(),
            savedJobs: savedJobs(),
            sources: sources(),
            recentRuns: recentRuns()
        )
    }

    static func seedDetails() {
        for job in referenceJobs() where !AtlasLocalCache.hasDetail(jobKey: job.jobKey) {
            AtlasLocalCache.saveDetail(detail(for: job), jobKey: job.jobKey)
        }
    }

    private static func referenceJobs() -> [JobSearchResult] {
        let now = Date(timeIntervalSince1970: 1_783_041_536)
        let templates: [JobTemplate] = [
            JobTemplate(
                title: "Emergency Specialist (Cash Based Assistance Beneficiary Data), P-3",
                organization: "UNICEF",
                sourceID: "unicef_pageup",
                dutyStation: "Nairobi, Kenya",
                gradeCode: "P-3",
                scope: "international",
                contractGroup: "staff",
                seniorityGroup: "senior",
                contractLabel: "Fixed term",
                workModality: "Onsite",
                ccogCode: "SOC",
                ccogLabel: "Relief specialists",
                tags: ["cash assistance", "emergency", "programme management", "data systems"],
                offsetDays: 2
            ),
            JobTemplate(
                title: "Associate Programme Management Officer, P-2",
                organization: "United Nations",
                sourceID: "un_inspira",
                dutyStation: "Geneva, Switzerland",
                gradeCode: "P-2",
                scope: "international",
                contractGroup: "staff",
                seniorityGroup: "mid",
                contractLabel: "Staff",
                workModality: "Onsite",
                ccogCode: "PMD",
                ccogLabel: "Programme management",
                tags: ["programme management", "evaluation", "reporting"],
                offsetDays: 5
            ),
            JobTemplate(
                title: "Information Systems Officer, P-3",
                organization: "United Nations",
                sourceID: "un_inspira",
                dutyStation: "New York, United States",
                gradeCode: "P-3",
                scope: "international",
                contractGroup: "staff",
                seniorityGroup: "senior",
                contractLabel: "Staff",
                workModality: "Hybrid",
                ccogCode: "INF",
                ccogLabel: "Information systems",
                tags: ["programming", "ERP systems", "data platforms"],
                offsetDays: 7
            ),
            JobTemplate(
                title: "Online Volunteer - Data Visualization Dashboard Support",
                organization: "UN Volunteers",
                sourceID: "unv_uvp",
                dutyStation: "Home-based",
                gradeCode: "UNV",
                scope: "unspecified",
                contractGroup: "volunteer",
                seniorityGroup: "volunteer",
                contractLabel: "UN Volunteer",
                workModality: "Online Remote",
                ccogCode: "ADM",
                ccogLabel: "Administrative",
                tags: ["data visualization", "social media", "writing editing"],
                offsetDays: 18
            ),
            JobTemplate(
                title: "Procurement Analyst, NPSA-9",
                organization: "UNDP",
                sourceID: "undp",
                dutyStation: "Tokyo, Japan",
                gradeCode: "NPSA-9",
                scope: "national",
                contractGroup: "consultant_contractor",
                seniorityGroup: "mid",
                contractLabel: "National personnel service agreement",
                workModality: "Onsite",
                ccogCode: "ADM",
                ccogLabel: "General administration",
                tags: ["procurement", "budgeting", "contract management"],
                offsetDays: 9
            ),
            JobTemplate(
                title: "Human Rights Officer, NO-B",
                organization: "OHCHR",
                sourceID: "ohchr",
                dutyStation: "Bangkok, Thailand",
                gradeCode: "NO-B",
                scope: "national",
                contractGroup: "staff",
                seniorityGroup: "mid",
                contractLabel: "Fixed term",
                workModality: "Onsite",
                ccogCode: "SOC",
                ccogLabel: "Social scientists",
                tags: ["human rights", "capacity building", "monitoring"],
                offsetDays: 11
            ),
            JobTemplate(
                title: "Budget and Finance Assistant, G-6",
                organization: "WFP",
                sourceID: "wfp",
                dutyStation: "Rome, Italy",
                gradeCode: "G-6",
                scope: "national",
                contractGroup: "staff",
                seniorityGroup: "entry_junior",
                contractLabel: "General service",
                workModality: "Onsite",
                ccogCode: "ADM",
                ccogLabel: "Administrative",
                tags: ["budgeting", "ERP systems", "finance"],
                offsetDays: 13
            ),
            JobTemplate(
                title: "Logistics and Transport Consultant",
                organization: "IOM",
                sourceID: "iom",
                dutyStation: "Multiple locations",
                gradeCode: "CON",
                scope: "unspecified",
                contractGroup: "consultant_contractor",
                seniorityGroup: "ungraded_nonstandard",
                contractLabel: "Consultant contractor",
                workModality: "Multiple Locations",
                ccogCode: "LOG",
                ccogLabel: "Transport / Freight",
                tags: ["transport freight", "procurement", "operations"],
                offsetDays: 16
            ),
            JobTemplate(
                title: "Audit Specialist, D-1",
                organization: "FAO",
                sourceID: "fao",
                dutyStation: "Rome, Italy",
                gradeCode: "D-1",
                scope: "international",
                contractGroup: "staff",
                seniorityGroup: "director_executive",
                contractLabel: "Staff",
                workModality: "Hybrid",
                ccogCode: "ADM",
                ccogLabel: "General administration",
                tags: ["auditing", "risk management", "governance"],
                offsetDays: 20
            ),
            JobTemplate(
                title: "Intern - Environment and Climate Programme",
                organization: "UNEP",
                sourceID: "unep",
                dutyStation: "Nairobi, Kenya",
                gradeCode: "I-1",
                scope: "international",
                contractGroup: "internship",
                seniorityGroup: "internship_trainee",
                contractLabel: "Internship",
                workModality: "Onsite",
                ccogCode: "SCI",
                ccogLabel: "Environment",
                tags: ["environment", "research", "writing editing"],
                offsetDays: 24
            ),
        ]

        return (0..<2_274).map { index in
            let template = templates[index % templates.count]
            let closingDate = Calendar(identifier: .gregorian).date(
                byAdding: .day,
                value: template.offsetDays + (index / templates.count) % 9,
                to: now
            )
            return JobSearchResult(
                jobKey: "\(template.sourceID):reference-\(index)",
                title: template.title,
                organization: template.organization,
                sourceID: template.sourceID,
                dutyStation: template.dutyStation,
                gradeCode: template.gradeCode,
                nationalInternational: template.scope,
                contractCategory: template.contractGroup,
                contractGroup: template.contractGroup,
                seniorityGroup: template.seniorityGroup,
                contractLabel: template.contractLabel,
                workModality: template.workModality,
                ccogFamilyCode: template.ccogCode,
                ccogFamilyLabel: template.ccogLabel,
                ccogPrimaryCode: template.ccogCode,
                ccogPrimaryLabel: template.ccogLabel,
                capabilityTags: template.tags,
                closingDate: closingDate,
                needsReview: index % 19 == 0,
                locationConfidence: index % 17 == 0 ? 0.52 : 0.94,
                gradeConfidence: index % 23 == 0 ? nil : 0.92,
                score: Double(80 - (index % 30)) / 100.0,
                scoreReasons: [
                    "CCOG family match: \(template.ccogLabel.lowercased())",
                    "Capability tag match: \(template.tags.first ?? "programme management")",
                ],
                matchSummary: "Matched title, organization, location, and capability filters.",
                description: "Coordinate \(template.tags.prefix(2).joined(separator: " and ")) workstreams, reporting, stakeholder follow-up, and evidence products for an international organization.",
                status: "open",
                postedDate: Calendar(identifier: .gregorian).date(byAdding: .day, value: -((index % 12) + 1), to: now),
                applyURL: URL(string: "https://example.org/apply/\(template.sourceID)/\(index)"),
                sourceURL: URL(string: "https://example.org/source/\(template.sourceID)/\(index)")
            )
        }
    }

    private static func facets(for jobs: [JobSearchResult]) -> [String: [String: Int]] {
        var output: [String: [String: Int]] = [:]
        for job in jobs {
            increment(&output, "organizations", job.organization)
            increment(&output, "source_ids", job.sourceID)
            increment(&output, "grades", job.gradeCode.replacingOccurrences(of: "-", with: "").uppercased())
            increment(&output, "national_international", job.nationalInternational)
            increment(&output, "contract_groups", job.contractGroup)
            increment(&output, "seniority_groups", job.seniorityGroup)
            increment(&output, "work_modalities", normalizedFacet(job.workModality))
            increment(&output, "ccog_families", job.ccogFamilyCode)
            for tag in job.capabilityTags {
                increment(&output, "capability_tags", tag)
            }
            if job.sourceID == "unv_uvp" {
                increment(&output, "volunteer_kinds", "un_volunteer")
                increment(&output, "unv_categories", "online")
                increment(&output, "unv_volunteer_types", "online")
            }
        }
        return output
    }

    private static func facetLabels() -> [String: [String: String]] {
        [
            "organizations": [
                "UNICEF": "UNICEF",
                "United Nations": "UN",
                "UN Volunteers": "UNV",
                "UNDP": "UNDP",
                "OHCHR": "OHCHR",
                "WFP": "WFP",
                "IOM": "IOM",
                "FAO": "FAO",
                "UNEP": "UNEP",
            ],
            "source_ids": [
                "unicef_pageup": "UNICEF",
                "un_inspira": "UN",
                "unv_uvp": "UNV",
                "undp": "UNDP",
                "ohchr": "OHCHR",
                "wfp": "WFP",
                "iom": "IOM",
                "fao": "FAO",
                "unep": "UNEP",
            ],
            "ccog_families": [
                "ADM": "Administrative",
                "PMD": "Programme management",
                "SOC": "Social scientists",
                "INF": "Information systems",
                "LOG": "Transport / Freight",
                "SCI": "Environment",
            ],
            "seniority_groups": [
                "entry_junior": "Entry Junior",
                "mid": "MID",
                "senior": "Senior",
                "director_executive": "Director Executive",
                "volunteer": "UN Volunteer",
                "internship_trainee": "Internship / Trainee",
                "ungraded_nonstandard": "Ungraded / Nonstandard",
            ],
            "contract_groups": [
                "staff": "Staff",
                "consultant_contractor": "Consultant Contractor",
                "internship": "Internship",
                "volunteer": "Volunteer",
            ],
            "work_modalities": [
                "onsite": "Onsite",
                "home_based": "HOME Based",
                "online_remote": "Online Remote",
                "hybrid": "Hybrid",
                "multiple_locations": "Multiple Locations",
            ],
        ]
    }

    private static func savedSearches() -> [AtlasSavedSearch] {
        [
            AtlasSavedSearch(
                name: "Tokyo programme roles",
                description: "Open programme and operations jobs with Japan location signals.",
                request: AtlasSearchRequest(text: "programme", countriesISO3: ["JPN"], limit: 200),
                createdAt: observedAt,
                updatedAt: observedAt
            ),
            AtlasSavedSearch(
                name: "Remote data roles",
                description: "Online remote data, dashboard, and analysis assignments.",
                request: AtlasSearchRequest(text: "data", workModalities: ["online_remote"], limit: 200),
                createdAt: observedAt,
                updatedAt: observedAt
            ),
        ]
    }

    private static func savedJobs() -> [AtlasApplicationRecord] {
        [
            AtlasApplicationRecord(
                id: "saved-unicef-reference-0",
                jobKey: "unicef_pageup:reference-0",
                status: "saved",
                notes: "Review emergency data scope.",
                appliedAt: nil,
                updatedAt: observedAt
            ),
            AtlasApplicationRecord(
                id: "saved-undp-reference-4",
                jobKey: "undp:reference-4",
                status: "saved",
                notes: "Strong procurement match.",
                appliedAt: nil,
                updatedAt: observedAt
            ),
        ]
    }

    private static func sources() -> [AtlasSourceSummary] {
        [
            AtlasSourceSummary(
                sourceID: "unicef_pageup",
                organization: "UNICEF",
                totalJobs: 312,
                openJobs: 286,
                lastSeenAt: observedAt,
                healthStatus: "ok",
                observedAt: observedAt,
                detailAttempted: 286,
                detailFailed: 3,
                missingTransitionAllowed: false
            ),
            AtlasSourceSummary(
                sourceID: "un_inspira",
                organization: "United Nations",
                totalJobs: 1_102,
                openJobs: 1_048,
                lastSeenAt: observedAt,
                healthStatus: "ok",
                observedAt: observedAt,
                detailAttempted: 1_048,
                detailFailed: 11,
                missingTransitionAllowed: false
            ),
            AtlasSourceSummary(
                sourceID: "unv_uvp",
                organization: "UN Volunteers",
                totalJobs: 184,
                openJobs: 171,
                lastSeenAt: observedAt,
                healthStatus: "degraded",
                observedAt: observedAt,
                detailAttempted: 171,
                detailFailed: 9,
                missingTransitionAllowed: true
            ),
            AtlasSourceSummary(
                sourceID: "undp",
                organization: "UNDP",
                totalJobs: 410,
                openJobs: 392,
                lastSeenAt: observedAt,
                healthStatus: "ok",
                observedAt: observedAt,
                detailAttempted: 392,
                detailFailed: 4,
                missingTransitionAllowed: false
            ),
        ]
    }

    private static func recentRuns() -> [AtlasSourceRun] {
        [
            AtlasSourceRun(sourceID: "unicef_pageup", fetched: 286, inserted: 14, updated: 22, missing: 3, closed: 7, observedAt: observedAt),
            AtlasSourceRun(sourceID: "un_inspira", fetched: 1_048, inserted: 29, updated: 41, missing: 11, closed: 18, observedAt: observedAt),
            AtlasSourceRun(sourceID: "undp", fetched: 392, inserted: 8, updated: 16, missing: 4, closed: 6, observedAt: observedAt),
            AtlasSourceRun(sourceID: "unv_uvp", fetched: 171, inserted: 3, updated: 9, missing: 9, closed: 12, observedAt: observedAt),
        ]
    }

    private static func detail(for job: JobSearchResult) -> AtlasJobDetail {
        AtlasJobDetail(
            jobKey: job.jobKey,
            title: job.title,
            description: """
            This reference vacancy detail demonstrates the iOS product layout used for Android parity review. The posting includes programme delivery, stakeholder coordination, evidence generation, reporting, and operational follow-up responsibilities.

            Responsibilities include coordinating workplans, maintaining implementation evidence, producing concise written outputs, and supporting cross-functional partners.

            Qualifications include relevant professional experience, strong written communication, data fluency, and familiarity with international organization operating contexts.
            """,
            status: job.status,
            closingDate: job.closingDate?.ISO8601Format(),
            closesAtLocal: job.closingDate?.formatted(date: .abbreviated, time: .shortened),
            closesTimezone: "UTC",
            applyURL: job.applyURL,
            sourceURL: job.sourceURL,
            deadlineInfo: AtlasDeadlineInfo(
                storedUTC: job.closingDate?.ISO8601Format(),
                sourceLocal: job.closingDate?.formatted(date: .abbreviated, time: .shortened),
                sourceTimezone: "UTC",
                sourceText: job.deadlineText
            ),
            displaySections: [
                AtlasDetailSection(
                    title: "Responsibilities",
                    body: "Coordinate delivery plans, manage implementation evidence, prepare reporting products, and support stakeholder follow-up across field and headquarters teams."
                ),
                AtlasDetailSection(
                    title: "Qualifications",
                    body: "Relevant professional experience, excellent drafting skills, analytical judgment, and familiarity with UN or international organization programme operations."
                ),
                AtlasDetailSection(
                    title: "Job Record",
                    rows: [
                        AtlasDetailRow(label: "Organization", value: job.organizationDisplay),
                        AtlasDetailRow(label: "Duty station", value: job.dutyStation),
                        AtlasDetailRow(label: "Grade", value: job.gradeCode),
                        AtlasDetailRow(label: "Contract", value: job.contractLabel),
                        AtlasDetailRow(label: "Work mode", value: job.workModality),
                    ]
                ),
                AtlasDetailSection(
                    title: "Raw Source Data",
                    body: "Reference capture fixture. Diagnostic fields are intentionally hidden from the search result list and available here for detail/debug review."
                ),
            ]
        )
    }

    private static func increment(_ output: inout [String: [String: Int]], _ key: String, _ value: String?) {
        guard let value, !value.isEmpty else { return }
        output[key, default: [:]][value, default: 0] += 1
    }

    private static func normalizedFacet(_ value: String) -> String {
        value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
            .replacingOccurrences(of: " ", with: "_")
            .replacingOccurrences(of: "-", with: "_")
    }

    private struct JobTemplate {
        let title: String
        let organization: String
        let sourceID: String
        let dutyStation: String
        let gradeCode: String
        let scope: String
        let contractGroup: String
        let seniorityGroup: String
        let contractLabel: String
        let workModality: String
        let ccogCode: String
        let ccogLabel: String
        let tags: [String]
        let offsetDays: Int
    }
}
