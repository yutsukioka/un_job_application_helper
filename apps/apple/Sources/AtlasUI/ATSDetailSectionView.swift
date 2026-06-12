import SwiftUI

// MARK: - Section view

/// Renders one canonical detail section: icon header, structured blocks,
/// and progressive disclosure for long content.
struct FormattedSectionView: View {
    let section: FormattedDetailSection
    @State private var isExpanded = false

    private static let collapseThreshold = 1100
    private static let previewBudget = 650

    private var isCollapsible: Bool {
        section.characterCount > Self.collapseThreshold
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: section.systemImage)
                    .foregroundStyle(AtlasTheme.accent)
                    .frame(width: 20)
                Text(section.title)
                    .font(.headline)
                Spacer()
            }

            ForEach(visibleBlocks) { block in
                DetailBlockView(block: block)
            }

            if isCollapsible {
                Button {
                    withAnimation(.snappy(duration: 0.25)) {
                        isExpanded.toggle()
                    }
                } label: {
                    Label(
                        isExpanded ? "Show less" : "Show more",
                        systemImage: isExpanded ? "chevron.up" : "chevron.down"
                    )
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(AtlasTheme.accent)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(isExpanded ? "Collapse" : "Expand") \(section.title)")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var visibleBlocks: [DetailBlock] {
        guard isCollapsible, !isExpanded else { return section.blocks }
        var budget = Self.previewBudget
        var preview: [DetailBlock] = []
        for block in section.blocks {
            if budget <= 0 { break }
            if block.characterCount <= budget {
                preview.append(block)
                budget -= block.characterCount
            } else {
                preview.append(truncate(block, to: budget))
                budget = 0
            }
        }
        return preview
    }

    private func truncate(_ block: DetailBlock, to budget: Int) -> DetailBlock {
        switch block {
        case .paragraph(let text):
            return .paragraph(text.wordBoundaryPrefix(budget) + "…")
        case .bullets(let items):
            var kept: [String] = []
            var remaining = budget
            for item in items {
                guard remaining > 80 else { break }
                if item.count <= remaining {
                    kept.append(item)
                    remaining -= item.count
                } else {
                    kept.append(item.wordBoundaryPrefix(remaining) + "…")
                    remaining = 0
                }
            }
            return .bullets(kept.isEmpty ? [items[0].wordBoundaryPrefix(budget) + "…"] : kept)
        case .facts:
            return block
        }
    }
}

// MARK: - Block view

struct DetailBlockView: View {
    let block: DetailBlock

    var body: some View {
        switch block {
        case .paragraph(let text):
            DetailBodyText(text)
        case .bullets(let items):
            VStack(alignment: .leading, spacing: 7) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline, spacing: 9) {
                        Circle()
                            .fill(AtlasTheme.accent.opacity(0.55))
                            .frame(width: 5, height: 5)
                            .alignmentGuide(.firstTextBaseline) { dimensions in
                                dimensions[VerticalAlignment.center] + 2
                            }
                        Text(item)
                            .font(.callout)
                            .lineSpacing(3)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        case .facts(let facts):
            VStack(spacing: 0) {
                ForEach(Array(facts.enumerated()), id: \.offset) { index, fact in
                    LabeledContent(fact.label) {
                        Text(fact.value)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.primary)
                            .multilineTextAlignment(.trailing)
                            .textSelection(.enabled)
                    }
                    .font(.subheadline)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    if index < facts.count - 1 {
                        Divider().padding(.leading, 12)
                    }
                }
            }
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color.secondary.opacity(0.06))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(Color.secondary.opacity(0.12), lineWidth: 1)
            )
        }
    }
}

// MARK: - Section navigation

/// Horizontal jump chips shown above the content when several sections exist.
struct DetailSectionNav: View {
    let sections: [FormattedDetailSection]
    let onSelect: (String) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(sections) { section in
                    Button {
                        onSelect(section.id)
                    } label: {
                        Label(section.title, systemImage: section.systemImage)
                            .font(.caption.weight(.medium))
                            .lineLimit(1)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .foregroundStyle(AtlasTheme.accent)
                            .background(Capsule().fill(AtlasTheme.accent.opacity(0.10)))
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Jump to \(section.title)")
                }
            }
        }
        .mask {
            HStack(spacing: 0) {
                Rectangle()
                LinearGradient(colors: [.black, .clear], startPoint: .leading, endPoint: .trailing)
                    .frame(width: 24)
            }
        }
    }
}

// MARK: - Preview fixtures

#Preview("Messy PageUp example, cleaned") {
    let fixture: [AtlasDetailSection] = [
        AtlasDetailSection(
            title: "Summary",
            body: "Vacancies | UNICEF Careers Skip to main content Global Links Visit UNICEF Global Careers Toggle navigation Careers Explore UNICEF About UNICEF Where we work Candidate login Careers Main navigation Vacancies Current vacancies Explore our current job opportunities Filter results Search using keywords Search for jobs"
        ),
        AtlasDetailSection(
            title: "Contract type",
            body: "Temporary Appointment Duty Station: San Jose Level: G-5 Location: Costa Rica Categories: Administration UNICEF trabaja en más de 190 países y territorios para salvar la vida de niños y niñas, defender sus derechos y ayudarles a desarrollar su máximo potencial. Como Asistente de Administración y Finanzas en UNICEF Costa Rica, desempeñarás un papel clave en el fortalecimiento de la plataforma administrativa y financiera que permite a la organización cumplir con su mandato en favor de la niñez. Tu trabajo garantizará la eficiencia, precisión y oportunidad en los procesos administrativos."
        ),
        AtlasDetailSection(
            title: "Responsibilities",
            body: "Key functions and accountabilities:\n• Support to financial planning, budget monitoring, and processing of payments in VISION/SAP.\n• Provide travel assistance to staff members for travel arrangements and entitlements.\n• Maintain office premises services, supplies, and inventory records.\n• Support procurement processes including preparation of purchase orders."
        ),
        AtlasDetailSection(
            title: "Assessment",
            body: "of other candidates."
        ),
        AtlasDetailSection(
            title: "Additional Information",
            body: "about working for UNICEF can be found here including remuneration, contract duration of 364 days, and onboarding expectations for national staff in San Jose with hybrid arrangements subject to office policy and supervisor approval during the assignment period overall. Advertised: 08 Jun 2026 Central America Standard Time Deadline: 22 Jun 2026 Central America Standard Time Back to search results Apply now Whatsapp Facebook LinkedIn Email App Send me jobs like these"
        ),
    ]
    let formatted = ATSDetailFormatter.format(sections: fixture)
    return ScrollView {
        VStack(alignment: .leading, spacing: 28) {
            DetailSectionNav(sections: formatted.sections) { _ in }
            ForEach(formatted.sections) { section in
                FormattedSectionView(section: section)
            }
            if formatted.hiddenBoilerplate {
                Label(
                    "Some site navigation text was hidden for readability. Open Source for the original posting.",
                    systemImage: "sparkles"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .padding(20)
    }
    .frame(width: 560, height: 760)
}

// MARK: - Helpers

private extension String {
    func wordBoundaryPrefix(_ limit: Int) -> String {
        guard count > limit else { return self }
        let cut = index(startIndex, offsetBy: limit)
        let slice = self[..<cut]
        if let lastSpace = slice.lastIndex(of: " ") {
            return String(slice[..<lastSpace])
        }
        return String(slice)
    }
}
