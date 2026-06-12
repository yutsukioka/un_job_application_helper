import SwiftUI

public struct JobResultRow: View {
    private let job: JobSearchResult
    private let density: ResultDensity

    public init(
        job: JobSearchResult,
        density: ResultDensity = .comfortable
    ) {
        self.job = job
        self.density = density
    }

    public var body: some View {
        HStack(alignment: .top, spacing: 12) {
            SourceMonogram(initials: job.sourceInitials, sourceID: job.sourceID)

            VStack(alignment: .leading, spacing: density == .compact ? 3 : 5) {
                titleLine
                organizationLine
                metadataLine
                if density == .comfortable {
                    Text(job.matchSummary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }

            Spacer(minLength: 8)

            if let score = job.score {
                if density == .compact {
                    ScoreBadge(score: score)
                } else {
                    ScoreRing(score: score)
                }
            }
        }
        .padding(.vertical, density == .compact ? 6 : 10)
        .contentShape(Rectangle())
    }

    private var titleLine: some View {
        // Concatenated Text so the needs-review badge flows inline after the
        // last word instead of floating beside a wrapped title.
        Group {
            if job.needsReview {
                Text(job.title)
                    + Text(" ")
                    + Text(Image(systemName: "questionmark.diamond.fill"))
                        .foregroundStyle(AtlasTheme.warning)
                        .font(.subheadline)
            } else {
                Text(job.title)
            }
        }
        .font(.headline)
        .lineLimit(density == .compact ? 1 : 2)
        .accessibilityLabel(job.needsReview ? "\(job.title), needs review" : job.title)
    }

    private var organizationLine: some View {
        HStack(spacing: 5) {
            Text(job.organizationDisplay)
                .lineLimit(1)
            Text("·")
                .foregroundStyle(.tertiary)
            Text(job.dutyStation)
                .lineLimit(1)
            ConfidenceDot(
                job.locationConfidence,
                label: "Location confidence",
                evidence: "Matched \(job.dutyStation)"
            )
        }
        .font(.subheadline)
        .foregroundStyle(.secondary)
    }

    private var metadataLine: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) {
                deadlinePill
                metadataText
            }
            VStack(alignment: .leading, spacing: 4) {
                deadlinePill
                metadataText
            }
        }
    }

    /// fixedSize keeps the pill text whole so ViewThatFits wraps to the
    /// vertical layout instead of truncating "Closes in 31h" to "Closes i...".
    private var deadlinePill: some View {
        DeadlinePill(text: job.deadlineText, urgency: job.deadlineUrgency)
            .fixedSize()
    }

    private var metadataText: some View {
        HStack(spacing: 4) {
            Text(job.gradeCode)
                .fontWeight(.semibold)
            Text("·")
                .foregroundStyle(.tertiary)
            Text(job.contractLabel)
            Text("·")
                .foregroundStyle(.tertiary)
            Label(job.workModality, systemImage: modalityIcon)
                .labelStyle(.titleAndIcon)
        }
        .font(.caption.monospacedDigit())
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .fixedSize(horizontal: true, vertical: false)
    }

    private var modalityIcon: String {
        switch job.workModality.lowercased() {
        case let value where value.contains("remote") || value.contains("home"):
            return "house"
        case let value where value.contains("hybrid"):
            return "arrow.triangle.branch"
        default:
            return "building.2"
        }
    }
}
