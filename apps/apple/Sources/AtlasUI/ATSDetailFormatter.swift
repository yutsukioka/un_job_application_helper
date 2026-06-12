import Foundation

// MARK: - Output model

public struct DetailFact: Hashable, Sendable {
    public let label: String
    public let value: String

    public init(label: String, value: String) {
        self.label = label
        self.value = value
    }
}

public enum DetailBlock: Hashable, Sendable, Identifiable {
    case paragraph(String)
    case bullets([String])
    case facts([DetailFact])

    public var id: Int { hashValue }

    var characterCount: Int {
        switch self {
        case .paragraph(let text):
            return text.count
        case .bullets(let items):
            return items.reduce(0) { $0 + $1.count }
        case .facts(let facts):
            return facts.reduce(0) { $0 + $1.label.count + $1.value.count }
        }
    }
}

public enum DetailSectionKind: Int, CaseIterable, Sendable {
    case about
    case responsibilities
    case qualifications
    case experience
    case competencies
    case languages
    case compensation
    case application
    case other

    public var displayTitle: String {
        switch self {
        case .about: return "About the role"
        case .responsibilities: return "Responsibilities"
        case .qualifications: return "Qualifications & education"
        case .experience: return "Experience"
        case .competencies: return "Competencies"
        case .languages: return "Languages"
        case .compensation: return "Compensation & benefits"
        case .application: return "Application & notes"
        case .other: return "More details"
        }
    }

    public var systemImage: String {
        switch self {
        case .about: return "doc.text"
        case .responsibilities: return "list.bullet.rectangle"
        case .qualifications: return "graduationcap"
        case .experience: return "briefcase"
        case .competencies: return "rosette"
        case .languages: return "globe"
        case .compensation: return "banknote"
        case .application: return "paperplane"
        case .other: return "doc.plaintext"
        }
    }
}

public struct FormattedDetailSection: Identifiable, Sendable {
    public let id: String
    public let kind: DetailSectionKind
    public let title: String
    public let systemImage: String
    public let blocks: [DetailBlock]

    public var characterCount: Int {
        blocks.reduce(0) { $0 + $1.characterCount }
    }
}

public struct FormattedDetail: Sendable {
    public let sections: [FormattedDetailSection]
    /// True when navigation/footer chrome was removed or trimmed.
    public let hiddenBoilerplate: Bool

    public static let empty = FormattedDetail(sections: [], hiddenBoilerplate: false)
}

// MARK: - Formatter

/// Display-level normalizer that maps messy per-ATS detail sections into one
/// canonical, readable outline. This is presentation hygiene only: it never
/// invents content, and trimming is surfaced through `hiddenBoilerplate` so the
/// UI can point users at the original posting.
public enum ATSDetailFormatter {
    public static func format(
        sections: [AtlasDetailSection],
        fallbackDescription: String? = nil
    ) -> FormattedDetail {
        var working: [(title: String, body: String)] = sections
            .filter { $0.rows.isEmpty }
            .compactMap { section in
                guard let body = section.body?.cleanedWhitespace, !body.isEmpty else { return nil }
                return (section.title.cleanedWhitespace, body)
            }
        if working.isEmpty, let fallback = fallbackDescription?.cleanedWhitespace, !fallback.isEmpty {
            working = [("About the role", fallback)]
        }
        guard !working.isEmpty else { return .empty }

        // 1. Scrub site chrome (drop chrome-dominant sections, trim trailing chrome).
        var hidden = false
        var scrubbed: [(title: String, body: String)] = []
        for (title, body) in working {
            switch scrubChrome(from: body) {
            case .drop:
                hidden = true
            case .keep(let cleaned, let didTrim):
                hidden = hidden || didTrim
                scrubbed.append((title, cleaned))
            }
        }

        // 2. Heal broken heading splits: a body that starts mid-sentence is a
        //    fragment whose heading word was swallowed by the splitter. Re-join
        //    "Title body" and append to the previous section.
        var healed: [(title: String, body: String)] = []
        for (title, body) in scrubbed {
            if isOrphanFragment(body) {
                let rejoined = "\(title) \(body)"
                if healed.isEmpty {
                    healed.append(("Details", rejoined))
                } else {
                    healed[healed.count - 1].body += " \(rejoined)"
                }
            } else {
                healed.append((title, body))
            }
        }

        // 3. Canonicalize and merge duplicates; keep unknown sections separate.
        var byKind: [DetailSectionKind: [(title: String, body: String)]] = [:]
        var kindOrder: [DetailSectionKind] = []
        var others: [(title: String, body: String)] = []
        for (title, body) in healed {
            let kind = kind(forTitle: title)
            if kind == .other {
                others.append((title, body))
            } else {
                if byKind[kind] == nil { kindOrder.append(kind) }
                byKind[kind, default: []].append((title, body))
            }
        }

        // 4. Structure each section body into paragraphs, bullets, and facts.
        var result: [FormattedDetailSection] = []
        let sortedKinds = kindOrder.sorted { $0.rawValue < $1.rawValue }
        for kind in sortedKinds {
            let blocks = (byKind[kind] ?? []).flatMap { entry in
                blockify(entry.body, sectionTitle: entry.title)
            }
            guard !blocks.isEmpty else { continue }
            result.append(
                FormattedDetailSection(
                    id: "kind-\(kind.rawValue)",
                    kind: kind,
                    title: kind.displayTitle,
                    systemImage: kind.systemImage,
                    blocks: blocks
                )
            )
        }
        for (index, section) in others.enumerated() {
            let blocks = blockify(section.body, sectionTitle: section.title)
            guard !blocks.isEmpty else { continue }
            result.append(
                FormattedDetailSection(
                    id: "other-\(index)",
                    kind: .other,
                    title: section.title,
                    systemImage: DetailSectionKind.other.systemImage,
                    blocks: blocks
                )
            )
        }
        return FormattedDetail(sections: result, hiddenBoilerplate: hidden)
    }

    // MARK: Chrome scrubbing

    private enum ScrubResult {
        case drop
        case keep(String, trimmed: Bool)
    }

    private static let chromeMarkers: [String] = [
        "skip to main content",
        "toggle navigation",
        "candidate login",
        "back to search results",
        "apply now",
        "powered by pageup",
        "send me jobs like these",
        "we will email you new jobs",
        "privacy agreement",
        "recaptcha",
        "visit us on linkedin",
        "visit us on facebook",
        "sharethis",
        "report fraud",
        "beware of fraudulent",
        "main navigation",
        "explore our current job opportunities",
        "search using keywords",
        "whatsapp facebook linkedin",
        "cookie policy",
        "current vacancies explore",
    ]

    private static func scrubChrome(from body: String) -> ScrubResult {
        var earliest: String.Index?
        for marker in chromeMarkers {
            if let range = body.range(of: marker, options: [.caseInsensitive]) {
                if earliest == nil || range.lowerBound < earliest! {
                    earliest = range.lowerBound
                }
            }
        }
        guard let cut = earliest else { return .keep(body, trimmed: false) }
        let prefix = String(body[..<cut]).cleanedWhitespace
        if prefix.count >= 200 {
            return .keep(prefix, trimmed: true)
        }
        return .drop
    }

    // MARK: Orphan fragments

    private static func isOrphanFragment(_ body: String) -> Bool {
        guard let first = body.unicodeScalars.first else { return true }
        return CharacterSet.lowercaseLetters.contains(first)
    }

    // MARK: Canonical mapping

    private static func kind(forTitle title: String) -> DetailSectionKind {
        let t = title.lowercased()
        if t.contains("work experience") || t == "experience" { return .experience }
        if t.contains("responsibilit") || t.contains("duties") || t.contains("key functions") || t.contains("tasks") { return .responsibilities }
        if t.contains("education") || t.contains("qualification") || t.contains("requirement") { return .qualifications }
        if t.contains("competenc") || t.contains("core values") || t.contains("skills") { return .competencies }
        if t.contains("language") { return .languages }
        if t.contains("benefit") || t.contains("remuneration") || t.contains("compensation") || t.contains("salary") || t.contains("entitlement") { return .compensation }
        if t.contains("how to apply") || t.contains("application") || t.contains("assessment") || t.contains("additional information") || t.contains("special notice") || t.contains("consideration") || t.contains("closing date") { return .application }
        if t.contains("summary") || t.contains("setting") || t.contains("background") || t.contains("purpose") || t.contains("objective") || t.contains("about")
            || t.contains("contract type") || t.contains("position level") || t.contains("location") || t.contains("department") || t.contains("organization") {
            return .about
        }
        return .other
    }

    // MARK: Block structuring

    private static let factLabels: [String] = [
        "Contract type", "Contractual Agreement", "Duty Station", "Duty station",
        "Position level", "Position Level", "Level", "Locations", "Location",
        "Categories", "Job no", "Job Posting", "Schedule", "Organization",
        "Functional Area", "Grade", "Advertised", "Deadline", "Closing Date",
        "Closing date", "Department",
    ]

    private static func blockify(_ text: String, sectionTitle: String) -> [DetailBlock] {
        var blocks: [DetailBlock] = []
        let (facts, remainder) = extractFacts(from: text, sectionTitle: sectionTitle)
        if !facts.isEmpty {
            blocks.append(.facts(facts))
        }
        guard let remainder, !remainder.isEmpty else { return blocks }

        let bulletParts = remainder.components(separatedBy: "• ")
        if bulletParts.count >= 3 {
            let intro = bulletParts[0].cleanedWhitespace
            if !intro.isEmpty {
                blocks.append(contentsOf: paragraphBlocks(intro))
            }
            let items = bulletParts.dropFirst()
                .map { $0.cleanedWhitespace }
                .filter { $0.count >= 3 }
            if !items.isEmpty {
                blocks.append(.bullets(items))
            }
            return blocks
        }

        let numbered = splitNumberedList(remainder)
        if numbered.count >= 3 {
            blocks.append(.bullets(numbered))
            return blocks
        }

        blocks.append(contentsOf: paragraphBlocks(remainder))
        return blocks
    }

    /// Pull leading `Label: short value` runs out of a body. Stops at the first
    /// label whose value is long (real prose), leaving the rest untouched.
    private static func extractFacts(
        from text: String,
        sectionTitle: String
    ) -> (facts: [DetailFact], remainder: String?) {
        let escaped = factLabels.map { NSRegularExpression.escapedPattern(for: $0) }
        let pattern = "\\b(?:\(escaped.joined(separator: "|")))\\s*:"
        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            return ([], text)
        }
        let nsText = text as NSString
        let matches = regex.matches(in: text, range: NSRange(location: 0, length: nsText.length))
        guard matches.count >= 2 else { return ([], text) }

        var facts: [DetailFact] = []
        var consumedUpTo = 0

        // The section title itself often names the first value
        // ("Contract type" -> "Temporary Appointment Duty Station: ...").
        let prefix = nsText.substring(to: matches[0].range.location).cleanedWhitespace
        let titleIsLabel = factLabels.contains { sectionTitle.compare($0, options: .caseInsensitive) == .orderedSame }
        var leadingProse: String?
        if titleIsLabel, !prefix.isEmpty, prefix.count <= 60 {
            facts.append(DetailFact(label: sectionTitle, value: prefix))
        } else if !prefix.isEmpty {
            leadingProse = prefix
        }

        for (index, match) in matches.enumerated() {
            let labelText = nsText.substring(with: match.range)
                .trimmingCharacters(in: CharacterSet(charactersIn: ": \u{00A0}"))
            let valueStart = match.range.location + match.range.length
            let valueEnd = index + 1 < matches.count ? matches[index + 1].range.location : nsText.length
            let value = nsText.substring(with: NSRange(location: valueStart, length: valueEnd - valueStart)).cleanedWhitespace
            if value.count > 60 || value.isEmpty {
                // Long value: this is prose, not a fact. Stop the streak here.
                consumedUpTo = match.range.location
                break
            }
            facts.append(DetailFact(label: labelText, value: value))
            consumedUpTo = valueEnd
        }

        guard facts.count >= 2 else { return ([], text) }
        var remainderParts: [String] = []
        if let leadingProse { remainderParts.append(leadingProse) }
        if consumedUpTo < nsText.length {
            remainderParts.append(nsText.substring(from: consumedUpTo).cleanedWhitespace)
        }
        let remainder = remainderParts.joined(separator: " ").cleanedWhitespace
        return (facts, remainder.isEmpty ? nil : remainder)
    }

    private static func splitNumberedList(_ text: String) -> [String] {
        let pattern = "(?<=\\s)\\d{1,2}[.)]\\s+(?=[A-Z])"
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let nsText = text as NSString
        let matches = regex.matches(in: text, range: NSRange(location: 0, length: nsText.length))
        guard matches.count >= 3 else { return [] }
        var items: [String] = []
        var start = matches[0].range.location
        // Only treat as a list when the intro before the first number is short.
        guard start < 240 else { return [] }
        let intro = nsText.substring(to: start).cleanedWhitespace
        if !intro.isEmpty { items.append(intro) }
        for (index, match) in matches.enumerated() {
            start = match.range.location
            let end = index + 1 < matches.count ? matches[index + 1].range.location : nsText.length
            let item = nsText.substring(with: NSRange(location: start, length: end - start)).cleanedWhitespace
            if item.count >= 3 { items.append(item) }
        }
        return items
    }

    private static func paragraphBlocks(_ text: String) -> [DetailBlock] {
        let rough = text
            .components(separatedBy: "\n\n")
            .flatMap { $0.components(separatedBy: "\n") }
            .map { $0.cleanedWhitespace }
            .filter { $0.count >= 3 }
        var paragraphs: [String] = []
        for chunk in rough {
            paragraphs.append(contentsOf: splitLongParagraph(chunk))
        }
        return paragraphs.map { .paragraph($0) }
    }

    /// Break very long single-line walls of text at sentence boundaries into
    /// readable paragraphs of roughly 400-700 characters.
    private static func splitLongParagraph(_ text: String) -> [String] {
        guard text.count > 900 else { return [text] }
        let sentences = text.components(separatedBy: ". ")
        var chunks: [String] = []
        var current = ""
        for (index, sentence) in sentences.enumerated() {
            let piece = index + 1 < sentences.count ? sentence + ". " : sentence
            current += piece
            if current.count >= 550 {
                chunks.append(current.cleanedWhitespace)
                current = ""
            }
        }
        if !current.cleanedWhitespace.isEmpty {
            chunks.append(current.cleanedWhitespace)
        }
        return chunks
    }
}

// MARK: - Helpers

private extension String {
    /// Collapses runs of whitespace and trims the result.
    var cleanedWhitespace: String {
        let collapsed = replacingOccurrences(
            of: "[\\t\\u{00A0}]+",
            with: " ",
            options: .regularExpression
        )
        .replacingOccurrences(of: " {2,}", with: " ", options: .regularExpression)
        return collapsed.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
