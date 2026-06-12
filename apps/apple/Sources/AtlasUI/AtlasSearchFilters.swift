import Foundation

public struct AtlasActiveFilterChip: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
}

public struct AtlasFacetOption: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let count: Int
}

public enum AtlasScopeFilter: String, CaseIterable, Identifiable, Equatable, Sendable {
    case any
    case international
    case national
    case unspecified

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .any:
            return "Any"
        case .international:
            return "International"
        case .national:
            return "National"
        case .unspecified:
            return "Unspecified"
        }
    }

    public var apiValues: [String] {
        switch self {
        case .any:
            return []
        case .international:
            return ["international"]
        case .national:
            return ["national", "local"]
        case .unspecified:
            return ["unknown"]
        }
    }
}

public enum AtlasVolunteerKind: String, CaseIterable, Identifiable, Equatable, Sendable {
    case unVolunteer = "un_volunteer"
    case volunteer

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .unVolunteer:
            return "UN Volunteer"
        case .volunteer:
            return "Volunteer"
        }
    }
}

public struct AtlasUNVCategoryInfo: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let detail: String
}

public struct AtlasSearchFilters: Equatable, Sendable {
    public static let remoteWorkModalities: Set<String> = ["home_based", "online_remote"]

    public var openOnly = true
    public var city = ""
    public var countryISO3 = ""
    public var scope: AtlasScopeFilter = .any
    public var includeLowConfidence = false
    public var closingSoon = false
    public var gradeCodes: Set<String> = []
    public var workModalities: Set<String> = []
    public var sourceIDs: Set<String> = []
    public var organizations: Set<String> = []
    public var ccogFamilies: Set<String> = []
    public var contractGroups: Set<String> = []
    public var seniorityGroups: Set<String> = []
    public var volunteerKinds: Set<String> = []
    public var unvCategories: Set<String> = []
    public var unvVolunteerTypes: Set<String> = []
    public var capabilityTags: Set<String> = []
    public var capabilityQuery = ""

    public init() {}

    public var activeChips: [AtlasActiveFilterChip] {
        var chips: [AtlasActiveFilterChip] = []
        if openOnly {
            chips.append(.init(id: "status.open", title: "Open only"))
        }
        if closingSoon {
            chips.append(.init(id: "deadline.soon", title: "Closing soon"))
        }
        if !trimmedCity.isEmpty {
            chips.append(.init(id: "location.city", title: trimmedCity))
        }
        if !trimmedCountryISO3.isEmpty {
            chips.append(.init(id: "location.country", title: trimmedCountryISO3.uppercased()))
        }
        if scope != .any {
            chips.append(.init(id: "scope", title: "Scope: \(scope.title)"))
        }
        if !gradeCodes.isEmpty {
            chips.append(.init(id: "grade.codes", title: gradeSummary))
        }
        if !workModalities.isEmpty {
            chips.append(.init(id: "work.modalities", title: workModalitySummary))
        }
        if !organizations.isEmpty {
            chips.append(.init(id: "organizations", title: selectionSummary(prefix: "Org", values: organizations)))
        }
        if !sourceIDs.isEmpty {
            chips.append(.init(id: "source.ids", title: selectionSummary(prefix: "Source", values: sourceIDs)))
        }
        if !contractGroups.isEmpty {
            chips.append(.init(id: "contract.groups", title: selectionSummary(prefix: "Contract", values: contractGroups)))
        }
        if !volunteerKinds.isEmpty {
            chips.append(.init(id: "volunteer.kinds", title: selectionSummary(prefix: "Volunteer", values: volunteerKinds)))
        }
        if !unvCategories.isEmpty {
            chips.append(.init(id: "unv.categories", title: selectionSummary(prefix: "UNV", values: unvCategories)))
        }
        if !seniorityGroups.isEmpty {
            chips.append(.init(id: "seniority.groups", title: selectionSummary(prefix: "Seniority", values: seniorityGroups)))
        }
        if !ccogFamilies.isEmpty {
            chips.append(.init(id: "ccog.families", title: selectionSummary(prefix: "CCOG", values: ccogFamilies)))
        }
        if !trimmedCapabilityQuery.isEmpty {
            chips.append(.init(id: "capabilities", title: "Skill: \(trimmedCapabilityQuery)"))
        } else if !capabilityTags.isEmpty {
            chips.append(.init(id: "capabilities", title: selectionSummary(prefix: "Skill", values: capabilityTags)))
        }
        if includeLowConfidence {
            chips.append(.init(id: "confidence.low", title: "Include uncertain"))
        }
        return chips
    }

    public var trimmedCity: String {
        city.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public var trimmedCountryISO3: String {
        countryISO3.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public var trimmedCapabilityQuery: String {
        capabilityQuery.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public var capabilityTerms: [String] {
        let typed = trimmedCapabilityQuery
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        return Array(Set(typed).union(capabilityTags)).sorted()
    }

    public var isRemoteOnly: Bool {
        workModalities == Self.remoteWorkModalities
    }

    public var gradeSummary: String {
        let sorted = sortedGradeCodes
        if sorted == ["P2", "P3", "P4"] {
            return "P-2 to P-4"
        }
        return sorted.map(displayGrade).joined(separator: ", ")
    }

    public var workModalitySummary: String {
        if isRemoteOnly {
            return "Remote"
        }
        return selectionSummary(prefix: "Work", values: workModalities)
    }

    public var sortedGradeCodes: [String] {
        gradeCodes.sorted { lhs, rhs in
            gradeSortKey(lhs) < gradeSortKey(rhs)
        }
    }

    public mutating func removeChip(id: String) {
        switch id {
        case "status.open":
            openOnly = false
        case "deadline.soon":
            closingSoon = false
        case "location.city":
            city = ""
        case "location.country":
            countryISO3 = ""
        case "scope":
            scope = .any
        case "grade.codes":
            gradeCodes.removeAll()
        case "work.modalities":
            workModalities.removeAll()
        case "organizations":
            organizations.removeAll()
        case "source.ids":
            sourceIDs.removeAll()
        case "contract.groups":
            contractGroups.removeAll()
        case "volunteer.kinds":
            volunteerKinds.removeAll()
            unvCategories.removeAll()
            unvVolunteerTypes.removeAll()
        case "unv.categories":
            unvCategories.removeAll()
        case "seniority.groups":
            seniorityGroups.removeAll()
        case "ccog.families":
            ccogFamilies.removeAll()
        case "capabilities":
            capabilityTags.removeAll()
            capabilityQuery = ""
        case "confidence.low":
            includeLowConfidence = false
        default:
            break
        }
    }

    public mutating func setRemoteOnly(_ isOn: Bool) {
        workModalities = isOn ? Self.remoteWorkModalities : []
    }

    public mutating func reset() {
        self = AtlasSearchFilters()
    }
}

public func displayAtlasFilterValue(_ value: String) -> String {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return "Unknown" }
    let upper = trimmed.uppercased()
    if upper.count <= 5, upper.allSatisfy({ $0.isLetter || $0.isNumber }) {
        return upper
    }
    return trimmed
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

public let atlasSeniorityOrder: [String] = [
    "entry_junior",
    "mid",
    "senior",
    "director_executive",
    "volunteer",
    "internship_trainee",
    "generic_volunteer",
    "ungraded_nonstaff_or_pathway",
    "unknown",
]

public let atlasSeniorityLabels: [String: String] = [
    "entry_junior": "Entry Junior",
    "mid": "MID",
    "senior": "Senior",
    "director_executive": "Director Executive",
    "volunteer": "UN Volunteer",
    "internship_trainee": "Internship Trainee",
    "generic_volunteer": "Volunteer",
    "ungraded_nonstaff_or_pathway": "Ungraded Nonstaff Or Pathway",
    "unknown": "Unknown",
]

public let atlasUNVCategoryInfo: [AtlasUNVCategoryInfo] = [
    .init(id: "un_community_volunteer", title: "UN Community Volunteer", detail: "Age 18-80; Experience 0+ months; Serving period 3-48 months"),
    .init(id: "un_university_volunteer", title: "UN University Volunteer", detail: "Age 18-80; Experience 1+ month; Serving period 3-48 months"),
    .init(id: "un_youth_volunteer", title: "UN Youth Volunteer", detail: "Age 18-80; Experience 1+ month; Serving period 3-48 months"),
    .init(id: "un_volunteer_specialist", title: "UN Volunteer Specialist", detail: "Age 18-80; Experience 3+ years; Serving period 3-48 months"),
    .init(id: "un_volunteer_expert", title: "UN Volunteer Expert", detail: "Age 18-80; Experience 7+ years; Serving period 3-48 months"),
]

private func selectionSummary(prefix: String, values: Set<String>) -> String {
    let sorted = values.sorted()
    guard let first = sorted.first else { return prefix }
    let firstDisplay = displayAtlasFilterValue(first)
    if sorted.count == 1 {
        return "\(prefix): \(firstDisplay)"
    }
    return "\(prefix): \(firstDisplay) +\(sorted.count - 1)"
}

private func displayGrade(_ value: String) -> String {
    let compact = value.replacingOccurrences(of: "-", with: "").uppercased()
    guard compact.count >= 2,
          let digitIndex = compact.firstIndex(where: \.isNumber),
          digitIndex > compact.startIndex else {
        return compact
    }
    return "\(compact[..<digitIndex])-\(compact[digitIndex...])"
}

private func gradeSortKey(_ value: String) -> String {
    let compact = value.replacingOccurrences(of: "-", with: "").uppercased()
    let letters = compact.prefix { !$0.isNumber }
    let numbers = compact.drop { !$0.isNumber }
    let padded = String(format: "%03d", Int(numbers) ?? 0)
    return "\(letters)\(padded)"
}
