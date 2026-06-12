import SwiftUI

public struct FilterChip: View {
    public enum Style {
        /// A committed filter: accent-filled, removable.
        case active
        /// A quick-filter suggestion: ghost/bordered, toggles on tap.
        case suggestion
    }

    private let title: String
    private let systemImage: String?
    private let style: Style
    private let onTap: (() -> Void)?
    private let onRemove: (() -> Void)?

    public init(
        _ title: String,
        systemImage: String? = nil,
        style: Style = .active,
        onTap: (() -> Void)? = nil,
        onRemove: (() -> Void)? = nil
    ) {
        self.title = title
        self.systemImage = systemImage
        self.style = style
        self.onTap = onTap
        self.onRemove = onRemove
    }

    public var body: some View {
        Group {
            if let onTap {
                Button(action: onTap) { label }
                    .buttonStyle(.plain)
            } else {
                label
            }
        }
    }

    private var label: some View {
        HStack(spacing: 6) {
            if let systemImage {
                Image(systemName: systemImage)
                    .imageScale(.small)
            }
            Text(title)
                .lineLimit(1)
            if let onRemove {
                Button(action: onRemove) {
                    Image(systemName: "xmark")
                        .imageScale(.small)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Remove \(title)")
            }
        }
        .font(.caption.weight(style == .active ? .medium : .regular))
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .foregroundStyle(style == .active ? AtlasTheme.accent : .secondary)
        .background(
            Capsule()
                .fill(style == .active ? AtlasTheme.accent.opacity(0.14) : Color.clear)
        )
        .overlay(
            Capsule()
                .strokeBorder(
                    style == .active
                        ? AtlasTheme.accent.opacity(0.30)
                        : Color.secondary.opacity(0.30),
                    lineWidth: 1
                )
        )
        .contentShape(Capsule())
    }
}

public struct ConfidenceDot: View {
    private let confidence: Double?
    private let label: String
    private let evidence: String?
    @State private var showEvidence = false

    public init(_ confidence: Double?, label: String = "Confidence", evidence: String? = nil) {
        self.confidence = confidence
        self.label = label
        self.evidence = evidence
    }

    public var body: some View {
        Button {
            showEvidence.toggle()
        } label: {
            HStack(spacing: 2) {
                ForEach(0..<3, id: \.self) { index in
                    Circle()
                        .fill(index < filledSegments ? tint : Color.secondary.opacity(0.22))
                        .frame(width: 5, height: 5)
                }
            }
            .frame(minWidth: 24, minHeight: 24)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .popover(isPresented: $showEvidence, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 4) {
                Text("\(label): \(accessibilityValue)")
                    .font(.caption.weight(.semibold))
                if let evidence {
                    Text(evidence)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(12)
            .frame(maxWidth: 260, alignment: .leading)
            .presentationCompactAdaptation(.popover)
        }
        .accessibilityLabel("\(label) \(accessibilityValue)")
    }

    private var filledSegments: Int {
        guard let confidence else { return 0 }
        if confidence >= 0.85 { return 3 }
        if confidence >= 0.70 { return 2 }
        if confidence > 0 { return 1 }
        return 0
    }

    private var tint: Color {
        switch filledSegments {
        case 3:
            return AtlasTheme.success
        case 2:
            return AtlasTheme.warning
        default:
            return AtlasTheme.danger
        }
    }

    private var accessibilityValue: String {
        guard let confidence else { return "missing" }
        return "\(Int(confidence * 100)) percent"
    }
}

public struct DeadlinePill: View {
    private let text: String
    private let urgency: DeadlineUrgency

    public init(text: String, urgency: DeadlineUrgency) {
        self.text = text
        self.urgency = urgency
    }

    public var body: some View {
        Label {
            Text(text)
                .strikethrough(urgency == .passed)
        } icon: {
            Image(systemName: urgency.systemImage)
        }
        .font(.caption.monospacedDigit())
        .lineLimit(1)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .foregroundStyle(urgency.tint)
        .background(
            Capsule().fill(
                urgency == .soon || urgency == .critical
                    ? urgency.tint.opacity(0.12)
                    : Color.secondary.opacity(0.08)
            )
        )
        .accessibilityLabel(text)
    }
}

public struct ScoreRing: View {
    private let score: Double

    public init(score: Double) {
        self.score = min(max(score, 0), 1)
    }

    public var body: some View {
        ZStack {
            Circle()
                .stroke(AtlasTheme.strategyOrange.opacity(0.20), lineWidth: 4)
            Circle()
                .trim(from: 0, to: score)
                .stroke(
                    AtlasTheme.strategyOrange,
                    style: StrokeStyle(lineWidth: 4, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
            Text("\(Int((score * 100).rounded()))")
                .font(.caption2.monospacedDigit().weight(.semibold))
                .foregroundStyle(AtlasTheme.strategyOrange)
        }
        .frame(width: 30, height: 30)
        .accessibilityLabel("Strategy fit score \(Int((score * 100).rounded())) out of 100")
    }
}

/// Flat numeric score badge used in compact density rows.
public struct ScoreBadge: View {
    private let score: Double

    public init(score: Double) {
        self.score = min(max(score, 0), 1)
    }

    public var body: some View {
        Text("\(Int((score * 100).rounded()))")
            .font(.caption2.monospacedDigit().weight(.bold))
            .foregroundStyle(AtlasTheme.strategyOrange)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(AtlasTheme.strategyOrange.opacity(0.12)))
            .accessibilityLabel("Strategy fit score \(Int((score * 100).rounded())) out of 100")
    }
}

public struct SourceMonogram: View {
    private let initials: String
    private let sourceID: String

    public init(initials: String, sourceID: String) {
        self.initials = initials
        self.sourceID = sourceID
    }

    public var body: some View {
        Text(initials)
            .font(.caption2.weight(.bold))
            .foregroundStyle(.white)
            .frame(width: 34, height: 34)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(AtlasTheme.sourceColor(for: sourceID))
            )
            .accessibilityLabel("Source \(initials)")
    }
}

public struct MetadataCapsule: View {
    private let text: String
    private let systemImage: String?

    public init(_ text: String, systemImage: String? = nil) {
        self.text = text
        self.systemImage = systemImage
    }

    public var body: some View {
        HStack(spacing: 4) {
            if let systemImage {
                Image(systemName: systemImage)
                    .imageScale(.small)
            }
            Text(text)
                .lineLimit(1)
        }
        .font(.caption.monospacedDigit())
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .foregroundStyle(.secondary)
        .background(Capsule().fill(Color.secondary.opacity(0.10)))
    }
}

struct EvidenceLine: View {
    let title: String
    let value: String
    let confidence: Double?

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            ConfidenceDot(confidence, label: title, evidence: value)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption.weight(.semibold))
                Text(value)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
    }
}
