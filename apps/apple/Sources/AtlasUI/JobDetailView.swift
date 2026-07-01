import SwiftUI

public struct JobDetailView: View {
    private let job: JobSearchResult
    private let client: AtlasAPIClient
    @State private var detail: AtlasJobDetail?
    @State private var detailError: String?
    @State private var isLoadingDetail = false
    @State private var saveMessage: String?
    @State private var saveError: String?
    @State private var isSaving = false
    @State private var formattedDetail: FormattedDetail?
    @State private var showRawRecord = false

    public init(job: JobSearchResult, client: AtlasAPIClient = AtlasAPIClient()) {
        self.job = job
        self.client = client
    }

    public var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    header
                    statusBanner
                    DeadlineDetailPanel(job: job, detail: detail)
                    WhyMatchedPanel(job: job)
                    detailSection("Classification") {
                        VStack(spacing: 0) {
                            classificationRow("Grade", job.gradeCode)
                            Divider()
                            classificationRow("Contract", job.contractLabel)
                            Divider()
                            classificationRow("Work mode", job.workModality)
                            Divider()
                            classificationRow("CCOG family", ccogFamilyText)
                            Divider()
                            classificationRow("CCOG primary", ccogPrimaryText)
                            Divider()
                            classificationRow("Capability tags", capabilityTagsText)
                            Divider()
                            classificationRow("Source", job.sourceID)
                        }
                    }
                    if isLoadingDetail && detail == nil {
                        ProgressView("Loading full details")
                    }
                    if let detailError {
                        detailSection("Detail Loading") {
                            Label(detailError, systemImage: "exclamationmark.triangle")
                                .font(.caption)
                                .foregroundStyle(AtlasTheme.warning)
                        }
                    }
                    detailContent(proxy: proxy)
                    rawRecordDisclosure
                    detailSection("Source and History") {
                        VStack(alignment: .leading, spacing: 8) {
                            if let sourceURL = detail?.sourceURL ?? job.sourceURL {
                                Link(destination: sourceURL) {
                                    Label("Open source vacancy", systemImage: "arrow.up.right.square")
                                }
                            }
                            Label(job.jobKey, systemImage: "number")
                            Label("Source: \(job.sourceID)", systemImage: "server.rack")
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                }
                .padding(20)
                .frame(maxWidth: 920, alignment: .leading)
            }
        }
        .navigationTitle(displayedTitle)
        .task(id: job.jobKey) {
            await loadDetail()
        }
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }

    @ViewBuilder
    private func detailContent(proxy: ScrollViewProxy) -> some View {
        let formatted = resolvedFormatted
        if formatted.sections.isEmpty {
            detailSection("Description") {
                DetailBodyText(detailDescription)
            }
        } else {
            if formatted.sections.count >= 3 {
                DetailSectionNav(sections: formatted.sections) { id in
                    withAnimation(.snappy(duration: 0.3)) {
                        proxy.scrollTo(id, anchor: .top)
                    }
                }
            }
            ForEach(formatted.sections) { section in
                FormattedSectionView(section: section)
                    .id(section.id)
            }
            if formatted.hiddenBoilerplate {
                Label(
                    "Some site navigation and footer text was hidden for readability. Open Source for the original posting.",
                    systemImage: "sparkles"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var rawRecordDisclosure: some View {
        if !metadataSections.isEmpty {
            DisclosureGroup(isExpanded: $showRawRecord) {
                VStack(alignment: .leading, spacing: 18) {
                    ForEach(metadataSections) { section in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(section.title)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.secondary)
                            DetailSectionContent(section: section)
                        }
                    }
                }
                .padding(.top, 10)
            } label: {
                Label("Raw record", systemImage: "tablecells")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            .tint(.secondary)
        }
    }

    private var resolvedFormatted: FormattedDetail {
        formattedDetail ?? ATSDetailFormatter.format(
            sections: [],
            fallbackDescription: detailDescription
        )
    }

    private var detailDescription: String {
        detail?.description?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
            ?? job.description
    }

    private var displayedTitle: String {
        detail?.title?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
            ?? job.title
    }

    private var metadataSections: [AtlasDetailSection] {
        (detail?.displaySections ?? []).filter { metadataSectionTitles.contains($0.title) }
    }

    private var metadataSectionTitles: Set<String> {
        ["Job Record", "Locations", "Source Features", "Raw Source Data"]
    }

    private var isClosedOrExpired: Bool {
        let status = detail?.status ?? job.status
        if ["closed", "missing"].contains(status.lowercased()) {
            return true
        }
        guard let closingDate = detailClosingDate ?? job.closingDate else { return false }
        return closingDate < .now
    }

    private var detailClosingDate: Date? {
        parseDetailDate(detail?.closingDate)
    }

    private var ccogFamilyText: String {
        [job.ccogFamilyCode, job.ccogFamilyLabel]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty }
            .joined(separator: " · ")
            .nilIfEmpty ?? "CCOG family unknown"
    }

    private var ccogPrimaryText: String {
        [job.ccogPrimaryCode, job.ccogPrimaryLabel]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty }
            .joined(separator: " · ")
            .nilIfEmpty ?? "CCOG primary unknown"
    }

    private var capabilityTagsText: String {
        job.capabilityTags.isEmpty
            ? "No capability tags"
            : job.capabilityTags.map(displayAtlasFilterValue).joined(separator: ", ")
    }

    @MainActor
    private func loadDetail() async {
        let cached = AtlasLocalCache.loadDetail(jobKey: job.jobKey)
        if let cached {
            applyDetail(cached)
        }

        isLoadingDetail = cached == nil
        detailError = nil
        defer { isLoadingDetail = false }
        do {
            let loaded = try await client.jobDetail(job.jobKey)
            applyDetail(loaded)
            AtlasLocalCache.saveDetail(loaded, jobKey: job.jobKey)
        } catch {
            let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            if cached == nil {
                detailError = message
                formattedDetail = ATSDetailFormatter.format(
                    sections: [],
                    fallbackDescription: job.description
                )
            } else {
                detailError = "Showing cached details. Server refresh failed: \(message)"
            }
        }
    }

    @MainActor
    private func applyDetail(_ loaded: AtlasJobDetail) {
        detail = loaded
        let prose = loaded.displaySections.filter { !metadataSectionTitles.contains($0.title) }
        formattedDetail = ATSDetailFormatter.format(
            sections: prose,
            fallbackDescription: loaded.description ?? job.description
        )
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            ViewThatFits(in: .horizontal) {
                // Wide: identity left, deadline right.
                HStack(alignment: .center, spacing: 12) {
                    identityBlock
                    Spacer(minLength: 12)
                    DeadlinePill(text: displayedDeadlineText, urgency: displayedDeadlineUrgency)
                }
                // Narrow: stack identity above deadline.
                VStack(alignment: .leading, spacing: 8) {
                    identityBlock
                    DeadlinePill(text: displayedDeadlineText, urgency: displayedDeadlineUrgency)
                }
            }
            Text(displayedTitle)
                .font(.title2.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            actionButtons
        }
    }

    private var identityBlock: some View {
        HStack(alignment: .center, spacing: 12) {
            SourceMonogram(initials: job.sourceInitials, sourceID: job.sourceID)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.organizationDisplay)
                    .font(.headline)
                    .lineLimit(1)
                Text(job.dutyStation)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private var actionButtons: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) {
                applyButton
                sourceButton
                saveButton
            }
            HStack(spacing: 10) {
                applyButton
                sourceButton
            }
        }
    }

    private var applyButton: some View {
        Group {
            if let applyURL = detail?.applyURL ?? job.applyURL, !isClosedOrExpired {
                Link(destination: applyURL) {
                    Label("Apply", systemImage: "paperplane")
                }
                .buttonStyle(.borderedProminent)
                .tint(AtlasTheme.accent)
            } else {
                Button {
                } label: {
                    Label(isClosedOrExpired ? "Closed" : "Apply", systemImage: "paperplane")
                }
                .buttonStyle(.borderedProminent)
                .tint(AtlasTheme.accent)
                .disabled(true)
            }
        }
        .keyboardShortcut(.defaultAction)
    }

    private var sourceButton: some View {
        Group {
            if let sourceURL = detail?.sourceURL ?? job.sourceURL {
                Link(destination: sourceURL) {
                    Label("Source", systemImage: "arrow.up.right.square")
                }
                .buttonStyle(.bordered)
            } else {
                Button {
                } label: {
                    Label("Source", systemImage: "arrow.up.right.square")
                }
                .buttonStyle(.bordered)
                .disabled(true)
            }
        }
    }

    private var saveButton: some View {
        Button {
            Task { await saveJob() }
        } label: {
            if isSaving {
                Label("Saving", systemImage: "bookmark")
            } else {
                Label(saveMessage == nil ? "Save" : "Saved", systemImage: saveMessage == nil ? "bookmark" : "bookmark.fill")
            }
        }
        .buttonStyle(.bordered)
        .disabled(isSaving)
    }

    @ViewBuilder
    private var statusBanner: some View {
        VStack(alignment: .leading, spacing: 8) {
            if isClosedOrExpired {
                Label("This vacancy appears closed or past deadline. Details are shown from the historical local database.", systemImage: "clock.badge.exclamationmark")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(AtlasTheme.warning)
            }
            if let saveMessage {
                Label(saveMessage, systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundStyle(AtlasTheme.success)
            }
            if let saveError {
                Label(saveError, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(AtlasTheme.warning)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @MainActor
    private func saveJob() async {
        isSaving = true
        saveError = nil
        defer { isSaving = false }
        do {
            _ = try await client.saveJob(job.jobKey)
            saveMessage = "Saved to application tracker"
        } catch {
            saveError = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    private func detailSection<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                .kerning(0.4)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func classificationRow(_ title: String, _ value: String) -> some View {
        LabeledContent(title) {
            Text(value)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(.primary)
        }
        .font(.subheadline)
        .padding(.vertical, 8)
    }

    private var displayedDeadlineDate: Date? {
        detailClosingDate ?? job.closingDate
    }

    private var displayedDeadlineText: String {
        guard let closingDate = displayedDeadlineDate else { return "No deadline" }
        let hours = closingDate.timeIntervalSince(.now) / 3600
        if hours < 0 { return "Deadline passed" }
        if hours <= 48 { return "Closes in \(max(1, Int(ceil(hours))))h" }
        if hours <= 24 * 14 { return "Closes in \(max(1, Int(ceil(hours / 24))))d" }
        return "Closes \(closingDate.formatted(.dateTime.month(.abbreviated).day()))"
    }

    private var displayedDeadlineUrgency: DeadlineUrgency {
        guard let closingDate = displayedDeadlineDate else { return .unknown }
        let hours = closingDate.timeIntervalSince(.now) / 3600
        if hours < 0 { return .passed }
        if hours <= 48 { return .critical }
        if hours <= 24 * 7 { return .soon }
        return .neutral
    }
}

struct DetailSectionContent: View {
    let section: AtlasDetailSection

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let body = section.body, !body.isEmpty {
                DetailBodyText(body)
            }
            if !section.rows.isEmpty {
                VStack(spacing: 0) {
                    ForEach(section.rows) { row in
                        LabeledContent(row.label) {
                            Text(row.value)
                                .font(.subheadline)
                                .textSelection(.enabled)
                                .multilineTextAlignment(.trailing)
                        }
                        .font(.subheadline)
                        .padding(.vertical, 6)
                        Divider()
                    }
                }
            }
        }
    }
}

struct DetailBodyText: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.body)
            .lineSpacing(5)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct DeadlineDetailPanel: View {
    let job: JobSearchResult
    let detail: AtlasJobDetail?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "calendar.badge.clock")
                    .foregroundStyle(AtlasTheme.accent)
                Text("Deadline")
                    .font(.headline)
                Spacer()
                DeadlinePill(text: job.deadlineText, urgency: job.deadlineUrgency)
            }
            VStack(alignment: .leading, spacing: 6) {
                if let local = localDeadlineText {
                    Label(local, systemImage: "clock")
                } else {
                    Label("No parsed deadline", systemImage: "clock")
                }
                if let sourceLine {
                    Label(sourceLine, systemImage: "building.columns")
                }
                if let sourceText = detail?.deadlineInfo?.sourceText, !sourceText.isEmpty {
                    Text(sourceText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Color.secondary.opacity(0.06))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(Color.secondary.opacity(0.14), lineWidth: 1)
        )
    }

    private var parsedDeadline: Date? {
        parseDetailDate(detail?.closingDate ?? detail?.deadlineInfo?.storedUTC) ?? job.closingDate
    }

    private var localDeadlineText: String? {
        guard let parsedDeadline else { return nil }
        return "Your local time: \(Self.localFormatter.string(from: parsedDeadline))"
    }

    private var sourceLine: String? {
        if let sourceLocal = detail?.deadlineInfo?.sourceLocal, !sourceLocal.isEmpty {
            if let timezone = detail?.deadlineInfo?.sourceTimezone, !timezone.isEmpty {
                return "Source announcement time: \(sourceLocal) (\(timezone))"
            }
            return "Source announcement time: \(sourceLocal)"
        }
        if let stored = detail?.deadlineInfo?.storedUTC, !stored.isEmpty {
            return "Stored normalized time: \(stored)"
        }
        return nil
    }

    private static let localFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = .current
        formatter.timeZone = .current
        formatter.dateFormat = "MMM d, yyyy h:mm a zzz"
        return formatter
    }()
}

public struct WhyMatchedPanel: View {
    private let job: JobSearchResult

    public init(job: JobSearchResult) {
        self.job = job
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "sparkle.magnifyingglass")
                    .foregroundStyle(AtlasTheme.accent)
                Text("Why this matched")
                    .font(.headline)
                Spacer()
                if let score = job.score {
                    Label("\(Int((score * 100).rounded()))", systemImage: "target")
                        .font(.caption.monospacedDigit().weight(.bold))
                        .foregroundStyle(AtlasTheme.strategyOrange)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Capsule().fill(AtlasTheme.strategyOrange.opacity(0.12)))
                        .accessibilityLabel("Strategy fit \(Int((score * 100).rounded())) out of 100")
                }
            }

            EvidenceLine(
                title: "Location",
                value: "Matched \(job.dutyStation)",
                confidence: job.locationConfidence
            )
            EvidenceLine(
                title: "Grade",
                value: "Matched \(job.gradeCode)",
                confidence: job.gradeConfidence
            )
            Text(job.matchSummary)
                .font(.caption)
                .foregroundStyle(.secondary)

            if !job.scoreReasons.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(job.scoreReasons, id: \.self) { reason in
                        Label {
                            Text(reason)
                                .foregroundStyle(.secondary)
                        } icon: {
                            Image(systemName: "checkmark.seal")
                                .foregroundStyle(AtlasTheme.strategyOrange)
                        }
                        .font(.caption)
                    }
                }
            }
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(AtlasTheme.accent.opacity(0.06))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(AtlasTheme.accent.opacity(0.16), lineWidth: 1)
        )
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}

private func parseDetailDate(_ value: String?) -> Date? {
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
