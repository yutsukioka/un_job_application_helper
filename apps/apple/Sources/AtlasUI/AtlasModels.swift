import Foundation

public struct JobSearchResult: Identifiable, Hashable, Codable, Sendable {
    public let jobKey: String
    public let title: String
    public let organization: String
    public let sourceID: String
    public let dutyStation: String
    public let gradeCode: String
    public let nationalInternational: String?
    public let contractCategory: String?
    public let contractGroup: String?
    public let seniorityGroup: String?
    public let contractLabel: String
    public let workModality: String
    public let ccogFamilyCode: String?
    public let ccogFamilyLabel: String?
    public let ccogPrimaryCode: String?
    public let ccogPrimaryLabel: String?
    public let capabilityTags: [String]
    public let closingDate: Date?
    public let needsReview: Bool
    public let locationConfidence: Double?
    public let gradeConfidence: Double?
    public let score: Double?
    public let scoreReasons: [String]
    public let matchSummary: String
    public let description: String
    public let status: String
    public let postedDate: Date?
    public let applyURL: URL?
    public let sourceURL: URL?

    public var id: String { jobKey }

    public init(
        jobKey: String,
        title: String,
        organization: String,
        sourceID: String,
        dutyStation: String,
        gradeCode: String,
        nationalInternational: String? = nil,
        contractCategory: String? = nil,
        contractGroup: String? = nil,
        seniorityGroup: String? = nil,
        contractLabel: String,
        workModality: String,
        ccogFamilyCode: String? = nil,
        ccogFamilyLabel: String? = nil,
        ccogPrimaryCode: String? = nil,
        ccogPrimaryLabel: String? = nil,
        capabilityTags: [String] = [],
        closingDate: Date?,
        needsReview: Bool,
        locationConfidence: Double?,
        gradeConfidence: Double?,
        score: Double?,
        scoreReasons: [String],
        matchSummary: String,
        description: String,
        status: String = "open",
        postedDate: Date? = nil,
        applyURL: URL? = nil,
        sourceURL: URL? = nil
    ) {
        self.jobKey = jobKey
        self.title = title
        self.organization = organization
        self.sourceID = sourceID
        self.dutyStation = dutyStation
        self.gradeCode = gradeCode
        self.nationalInternational = nationalInternational
        self.contractCategory = contractCategory
        self.contractGroup = contractGroup
        self.seniorityGroup = seniorityGroup
        self.contractLabel = contractLabel
        self.workModality = workModality
        self.ccogFamilyCode = ccogFamilyCode
        self.ccogFamilyLabel = ccogFamilyLabel
        self.ccogPrimaryCode = ccogPrimaryCode
        self.ccogPrimaryLabel = ccogPrimaryLabel
        self.capabilityTags = capabilityTags
        self.closingDate = closingDate
        self.needsReview = needsReview
        self.locationConfidence = locationConfidence
        self.gradeConfidence = gradeConfidence
        self.score = score
        self.scoreReasons = scoreReasons
        self.matchSummary = matchSummary
        self.description = description
        self.status = status
        self.postedDate = postedDate
        self.applyURL = applyURL
        self.sourceURL = sourceURL
    }

    enum CodingKeys: String, CodingKey {
        case jobKey
        case title
        case organization
        case sourceID
        case dutyStation
        case gradeCode
        case nationalInternational
        case contractCategory
        case contractGroup
        case seniorityGroup
        case contractLabel
        case workModality
        case ccogFamilyCode
        case ccogFamilyLabel
        case ccogPrimaryCode
        case ccogPrimaryLabel
        case capabilityTags
        case closingDate
        case needsReview
        case locationConfidence
        case gradeConfidence
        case score
        case scoreReasons
        case matchSummary
        case description
        case status
        case postedDate
        case applyURL
        case sourceURL
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            jobKey: try container.decode(String.self, forKey: .jobKey),
            title: try container.decode(String.self, forKey: .title),
            organization: try container.decode(String.self, forKey: .organization),
            sourceID: try container.decode(String.self, forKey: .sourceID),
            dutyStation: try container.decode(String.self, forKey: .dutyStation),
            gradeCode: try container.decode(String.self, forKey: .gradeCode),
            nationalInternational: try container.decodeIfPresent(String.self, forKey: .nationalInternational),
            contractCategory: try container.decodeIfPresent(String.self, forKey: .contractCategory),
            contractGroup: try container.decodeIfPresent(String.self, forKey: .contractGroup),
            seniorityGroup: try container.decodeIfPresent(String.self, forKey: .seniorityGroup),
            contractLabel: try container.decode(String.self, forKey: .contractLabel),
            workModality: try container.decode(String.self, forKey: .workModality),
            ccogFamilyCode: try container.decodeIfPresent(String.self, forKey: .ccogFamilyCode),
            ccogFamilyLabel: try container.decodeIfPresent(String.self, forKey: .ccogFamilyLabel),
            ccogPrimaryCode: try container.decodeIfPresent(String.self, forKey: .ccogPrimaryCode),
            ccogPrimaryLabel: try container.decodeIfPresent(String.self, forKey: .ccogPrimaryLabel),
            capabilityTags: try container.decodeIfPresent([String].self, forKey: .capabilityTags) ?? [],
            closingDate: try container.decodeIfPresent(Date.self, forKey: .closingDate),
            needsReview: try container.decode(Bool.self, forKey: .needsReview),
            locationConfidence: try container.decodeIfPresent(Double.self, forKey: .locationConfidence),
            gradeConfidence: try container.decodeIfPresent(Double.self, forKey: .gradeConfidence),
            score: try container.decodeIfPresent(Double.self, forKey: .score),
            scoreReasons: try container.decodeIfPresent([String].self, forKey: .scoreReasons) ?? [],
            matchSummary: try container.decodeIfPresent(String.self, forKey: .matchSummary) ?? "",
            description: try container.decodeIfPresent(String.self, forKey: .description) ?? "",
            status: try container.decodeIfPresent(String.self, forKey: .status) ?? "open",
            postedDate: try container.decodeIfPresent(Date.self, forKey: .postedDate),
            applyURL: try container.decodeIfPresent(URL.self, forKey: .applyURL),
            sourceURL: try container.decodeIfPresent(URL.self, forKey: .sourceURL)
        )
    }

    public var sourceInitials: String {
        let preferred = organizationDisplay
            .split(separator: " ")
            .filter { !$0.isEmpty }
            .prefix(3)
            .compactMap(\.first)
        let initials = String(preferred).uppercased()
        if initials.count >= 2 { return initials }
        return String(organizationDisplay.prefix(3)).uppercased()
    }

    /// Candidate-facing organization name: strips ATS family tokens that leak
    /// into source-derived names ("Unicef Pageup" -> "UNICEF") and renders
    /// short single-word names as acronyms.
    public var organizationDisplay: String {
        let atsTokens: Set<String> = [
            "pageup", "successfactors", "taleo", "workday", "inspira",
            "avature", "csod", "recruitee", "smartrecruiters", "oracle",
            "hcm", "peoplesoft", "talentsoft", "uvp", "api", "static",
            "html", "custom", "legacy", "rmk", "drupal", "split",
        ]
        let words = organization
            .split(separator: " ")
            .map(String.init)
            .filter { !atsTokens.contains($0.lowercased()) }
        guard !words.isEmpty else { return organization }
        let cleaned = words.joined(separator: " ")
        if words.count == 1, cleaned.count <= 6 {
            return cleaned.uppercased()
        }
        return cleaned
    }

    public var deadlineUrgency: DeadlineUrgency {
        guard let closingDate else { return .unknown }
        let hours = closingDate.timeIntervalSince(.now) / 3600
        if hours < 0 { return .passed }
        if hours <= 48 { return .critical }
        if hours <= 24 * 7 { return .soon }
        return .neutral
    }

    public var deadlineText: String {
        guard let closingDate else { return "No deadline" }
        let hours = closingDate.timeIntervalSince(.now) / 3600
        if hours < 0 { return "Deadline passed" }
        if hours <= 48 { return "Closes in \(max(1, Int(ceil(hours))))h" }
        if hours <= 24 * 14 { return "Closes in \(max(1, Int(ceil(hours / 24))))d" }
        return "Closes \(closingDate.formatted(.dateTime.month(.abbreviated).day()))"
    }
}

public extension JobSearchResult {
    static let samples: [JobSearchResult] = [
        JobSearchResult(
            jobKey: "unicef_pageup:593420",
            title: "Emergency Specialist (Cash Based Assistance Beneficiary Data), P-3",
            organization: "UNICEF",
            sourceID: "unicef_pageup",
            dutyStation: "Nairobi, Kenya",
            gradeCode: "P-3",
            contractLabel: "Fixed term",
            workModality: "Onsite",
            closingDate: Calendar.current.date(byAdding: .hour, value: 31, to: .now),
            needsReview: false,
            locationConfidence: 0.80,
            gradeConfidence: 0.88,
            score: 0.86,
            scoreReasons: [
                "Term in title: cash based assistance",
                "CCOG family match: relief specialists",
                "Location matched Nairobi from title",
            ],
            matchSummary: "Location matched Nairobi from title; grade P-3 inferred from title.",
            description: "Support cash-based assistance beneficiary data systems, emergency response coordination, and programme evidence workflows for UNICEF's Global Programme Division."
        ),
        JobSearchResult(
            jobKey: "un_inspira:273426",
            title: "Associate Programme Management Officer, P-2",
            organization: "United Nations",
            sourceID: "un_inspira",
            dutyStation: "Nairobi, Kenya",
            gradeCode: "P-2",
            contractLabel: "Fixed term",
            workModality: "Onsite",
            closingDate: Calendar.current.date(byAdding: .day, value: 3, to: .now),
            needsReview: false,
            locationConfidence: 0.98,
            gradeConfidence: 0.96,
            score: 0.72,
            scoreReasons: [
                "Term in title: programme management",
                "Location matched raw duty station",
            ],
            matchSummary: "Duty station matched Nairobi with high confidence; grade P-2 came from source grade.",
            description: "Assist with programme planning, performance monitoring, coordination, reporting, and stakeholder follow-up in a UN Secretariat office."
        ),
        JobSearchResult(
            jobKey: "un_inspira:275041",
            title: "Information Systems Officer, P-3",
            organization: "United Nations",
            sourceID: "un_inspira",
            dutyStation: "Nairobi, Kenya",
            gradeCode: "P-3",
            contractLabel: "Staff",
            workModality: "Onsite",
            closingDate: Calendar.current.date(byAdding: .day, value: 6, to: .now),
            needsReview: true,
            locationConfidence: 0.98,
            gradeConfidence: 0.96,
            score: nil,
            scoreReasons: [],
            matchSummary: "Matched city and international grade scope; classification marked for review.",
            description: "Plan, implement, and maintain information systems services, data platforms, and technical support operations."
        ),
        JobSearchResult(
            jobKey: "unv_uvp:remote-001",
            title: "Online Volunteer - Data Visualization Dashboard Support",
            organization: "UN Volunteers",
            sourceID: "unv_uvp",
            dutyStation: "Home-based",
            gradeCode: "UNV",
            contractLabel: "Volunteer",
            workModality: "Online remote",
            closingDate: Calendar.current.date(byAdding: .day, value: 18, to: .now),
            needsReview: false,
            locationConfidence: 0.74,
            gradeConfidence: nil,
            score: 0.64,
            scoreReasons: [
                "Capability tag match: data visualization",
                "Work modality matched online remote",
            ],
            matchSummary: "Remote modality matched; grade not applicable for volunteer assignment.",
            description: "Create dashboards and visualization templates for programme reporting and knowledge products."
        ),
    ]
}
