import Foundation
import SwiftUI

public struct AtlasLockedPublicShellView: View {
    private let model: AtlasLockedPublicShellModel
    private let actions: AtlasLockedPublicShellActions

    @State private var query: String
    @State private var activeAction: Task<Void, Never>?
    @State private var activeActionID: UUID?

    public init(
        model: AtlasLockedPublicShellModel,
        actions: AtlasLockedPublicShellActions
    ) {
        self.model = model
        self.actions = actions
        _query = State(initialValue: model.searchQuery)
    }

    public var body: some View {
        VStack(spacing: 0) {
            statusBar
            Divider()
            searchBar
            Divider()
            resultContent
        }
        .frame(minWidth: 320, minHeight: 420)
        .onChange(of: model.searchQuery) { _, newValue in
            query = newValue
        }
        .onDisappear {
            activeActionID = nil
            activeAction?.cancel()
            activeAction = nil
        }
    }

    private var statusBar: some View {
        HStack(spacing: 12) {
            Image(systemName: "lock.fill")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text("AtlasVault")
                    .font(.headline)
                Text(vaultStatusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 16)

            if model.canRequestUnlock {
                Button {
                    dispatchUnlockRequest()
                } label: {
                    Label("Unlock", systemImage: "lock.open")
                }
                .buttonStyle(.borderedProminent)
                .accessibilityLabel("Request vault unlock")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.bar)
    }

    private var searchBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                TextField("Search public jobs", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(dispatchSearch)
                    .accessibilityLabel("Public job search")

                Button(action: dispatchSearch) {
                    Image(systemName: "magnifyingglass")
                }
                .buttonStyle(.bordered)
                .disabled(model.isSearching)
                .help("Search")
                .accessibilityLabel("Search public jobs")
            }

            HStack(spacing: 14) {
                Label(serviceStatusText, systemImage: serviceStatusIcon)
                Label(cacheStatusText, systemImage: "externaldrive")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(12)
    }

    @ViewBuilder
    private var resultContent: some View {
        if model.isSearching, model.publicJobs.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityLabel("Searching public jobs")
        } else if model.publicJobs.isEmpty {
            ContentUnavailableView(
                "No public results",
                systemImage: "magnifyingglass"
            )
        } else {
            List(model.publicJobs) { job in
                AtlasLockedPublicJobRow(job: job)
            }
            .listStyle(.plain)
        }
    }

    private var vaultStatusText: String {
        switch model.vaultStatus {
        case .locked:
            "Locked"
        case .noVault:
            "No local vault"
        case .keyUnavailable:
            "Vault key unavailable"
        }
    }

    private func dispatchSearch() {
        let submittedQuery = query
        let actions = actions
        replaceActiveAction {
            await actions.search(query: submittedQuery)
        }
    }

    private func dispatchUnlockRequest() {
        let actions = actions
        replaceActiveAction {
            await actions.requestUnlock()
        }
    }

    private var serviceStatusText: String {
        switch model.serviceStatus {
        case .checking:
            "Checking public service"
        case .available:
            "Public service available"
        case .unavailable:
            "Public service unavailable"
        }
    }

    private var serviceStatusIcon: String {
        switch model.serviceStatus {
        case .checking:
            "clock"
        case .available:
            "checkmark.circle"
        case .unavailable:
            "exclamationmark.triangle"
        }
    }

    private var cacheStatusText: String {
        switch model.cacheFreshness {
        case .unavailable:
            "No public snapshot"
        case .current:
            "Public snapshot current"
        case .stale:
            "Public snapshot stale"
        }
    }

    private func replaceActiveAction(
        _ operation: @escaping @Sendable () async -> Void
    ) {
        activeAction?.cancel()
        let identifier = UUID()
        activeActionID = identifier
        activeAction = Task { @MainActor in
            await operation()
            guard activeActionID == identifier else {
                return
            }
            activeAction = nil
            activeActionID = nil
        }
    }
}

private struct AtlasLockedPublicJobRow: View {
    let job: AtlasLockedPublicJob

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(job.title)
                .font(.headline)
                .lineLimit(2)

            Text(job.organization)
                .font(.subheadline)
                .foregroundStyle(.secondary)

            HStack(spacing: 8) {
                Label(job.location, systemImage: "mappin.and.ellipse")
                if let closingDateText = job.closingDateText {
                    Label(closingDateText, systemImage: "calendar")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }
}
