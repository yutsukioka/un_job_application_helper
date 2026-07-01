import SwiftUI

#if os(iOS)
import UIKit
import Network
#endif

enum AtlasSidebarSelection: Hashable {
    case search
    case updates
    case savedJobs
    case settings
    case savedSearch(String)
    case source(String)
}

enum AtlasMobileTab: Hashable {
    case search
    case saved
    case updates
    case sources
    case settings
}

struct AtlasNavigationSnapshot {
    let sidebarSelection: AtlasSidebarSelection?
    let selectedJob: JobSearchResult?
    let query: String
    let filters: AtlasSearchFilters
    let sortOrder: SortOrder
}

public struct AtlasRootView: View {
    @StateObject private var searchViewModel: AtlasSearchViewModel
    @State private var selectedJob: JobSearchResult?
    @State private var sidebarSelection: AtlasSidebarSelection? = .search
    @State private var selectedMobileTab: AtlasMobileTab = .search
    @State private var backStack: [AtlasNavigationSnapshot] = []
    @State private var forwardStack: [AtlasNavigationSnapshot] = []
    @State private var isRestoringNavigation = false
    @State private var suppressNextSidebarHistory = false

    public init(searchViewModel: AtlasSearchViewModel = AtlasSearchViewModel()) {
        _searchViewModel = StateObject(wrappedValue: searchViewModel)
        _selectedJob = State(initialValue: searchViewModel.results.first)
    }

    public var body: some View {
        #if os(macOS)
        NavigationSplitView {
            AtlasSidebarView(selection: $sidebarSelection, viewModel: searchViewModel)
                .navigationSplitViewColumnWidth(min: 180, ideal: 220, max: 280)
        } content: {
            switch sidebarSelection ?? .search {
            case .updates:
                UpdatesPanel(viewModel: searchViewModel) { sourceID in
                    Task { await showJobs(for: sourceID) }
                }
                    .navigationSplitViewColumnWidth(min: 340, ideal: 420, max: 560)
            case .savedJobs:
                SavedPanel(
                    selection: $selectedJob,
                    viewModel: searchViewModel,
                    onSelectJob: selectJob
                )
                    .navigationSplitViewColumnWidth(min: 340, ideal: 420, max: 560)
            case .settings:
                AtlasSettingsPanel(viewModel: searchViewModel)
                    .navigationSplitViewColumnWidth(min: 360, ideal: 460, max: 620)
            case .source(_):
                AtlasSearchScreen(
                    selection: $selectedJob,
                    viewModel: searchViewModel,
                    onSelectJob: selectJob
                )
                .navigationSplitViewColumnWidth(min: 340, ideal: 420, max: 560)
            case .search, .savedSearch:
                AtlasSearchScreen(
                    selection: $selectedJob,
                    viewModel: searchViewModel,
                    onSelectJob: selectJob
                )
                    .navigationSplitViewColumnWidth(min: 340, ideal: 420, max: 560)
            }
        } detail: {
            if let selectedJob {
                JobDetailView(job: selectedJob)
                    .id(selectedJob.jobKey)
                    .frame(minWidth: 360)
            } else {
                emptyDetailView
                    .frame(minWidth: 360)
            }
        }
        .navigationSplitViewStyle(.balanced)
        .frame(minWidth: 940, minHeight: 540)
        .toolbar {
            ToolbarItemGroup(placement: .navigation) {
                Button {
                    navigateBack()
                } label: {
                    Image(systemName: "chevron.left")
                }
                .disabled(backStack.isEmpty)
                .help("Back")

                Button {
                    navigateForward()
                } label: {
                    Image(systemName: "chevron.right")
                }
                .disabled(forwardStack.isEmpty)
                .help("Forward")
            }
        }
        .onChange(of: sidebarSelection) { oldValue, newValue in
            guard !isRestoringNavigation else { return }
            if suppressNextSidebarHistory {
                suppressNextSidebarHistory = false
            } else {
                pushNavigationSnapshot(
                    AtlasNavigationSnapshot(
                        sidebarSelection: oldValue,
                        selectedJob: selectedJob,
                        query: searchViewModel.query,
                        filters: searchViewModel.filters,
                        sortOrder: searchViewModel.sortOrder
                    )
                )
            }
            Task { await applySidebarSelection(newValue) }
        }
        .task {
            await searchViewModel.loadIfNeeded()
        }
        #else
        TabView(selection: $selectedMobileTab) {
            NavigationStack {
                AtlasSearchScreen(selection: $selectedJob, viewModel: searchViewModel)
            }
            .tabItem { Label("Search", systemImage: "magnifyingglass") }
            .tag(AtlasMobileTab.search)

            NavigationStack {
                SavedPanel(selection: $selectedJob, viewModel: searchViewModel, onRunSavedSearch: {
                    selectedMobileTab = .search
                })
            }
                .tabItem { Label("Saved", systemImage: "bookmark") }
                .tag(AtlasMobileTab.saved)

            UpdatesPanel(viewModel: searchViewModel)
                .tabItem { Label("Updates", systemImage: "clock.arrow.circlepath") }
                .tag(AtlasMobileTab.updates)

            SourcesPanel(viewModel: searchViewModel)
                .tabItem { Label("Sources", systemImage: "antenna.radiowaves.left.and.right") }
                .tag(AtlasMobileTab.sources)

            AtlasSettingsPanel(viewModel: searchViewModel)
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(AtlasMobileTab.settings)
        }
        .tint(AtlasTheme.accent)
        .task {
            await searchViewModel.loadIfNeeded()
        }
        #endif
    }

    @MainActor
    private func applySidebarSelection(_ selection: AtlasSidebarSelection?) async {
        switch selection {
        case .savedSearch(let name):
            guard let saved = searchViewModel.savedSearches.first(where: { $0.name == name }) else { return }
            selectedJob = nil
            await searchViewModel.runSavedSearch(saved)
        case .source(let sourceID):
            guard let source = searchViewModel.sources.first(where: { $0.sourceID == sourceID }) else { return }
            selectedJob = nil
            await searchViewModel.filterBySource(source)
        case .updates, .savedJobs, .settings, .none:
            selectedJob = nil
        case .search:
            break
        }
    }

    @MainActor
    private func selectJob(_ job: JobSearchResult) {
        pushNavigationSnapshot()
        selectedJob = job
    }

    @MainActor
    private func showJobs(for sourceID: String) async {
        pushNavigationSnapshot()
        selectedJob = nil
        if let source = searchViewModel.sources.first(where: { $0.sourceID == sourceID }) {
            await searchViewModel.filterBySource(source)
        } else {
            searchViewModel.filters.sourceIDs = [sourceID]
            await searchViewModel.search()
        }
        suppressNextSidebarHistory = true
        sidebarSelection = .source(sourceID)
    }

    private var currentNavigationSnapshot: AtlasNavigationSnapshot {
        AtlasNavigationSnapshot(
            sidebarSelection: sidebarSelection,
            selectedJob: selectedJob,
            query: searchViewModel.query,
            filters: searchViewModel.filters,
            sortOrder: searchViewModel.sortOrder
        )
    }

    @MainActor
    private func pushNavigationSnapshot(_ snapshot: AtlasNavigationSnapshot? = nil) {
        guard !isRestoringNavigation else { return }
        backStack.append(snapshot ?? currentNavigationSnapshot)
        if backStack.count > 60 {
            backStack.removeFirst(backStack.count - 60)
        }
        forwardStack.removeAll()
    }

    @MainActor
    private func navigateBack() {
        guard let snapshot = backStack.popLast() else { return }
        forwardStack.append(currentNavigationSnapshot)
        restoreNavigationSnapshot(snapshot)
    }

    @MainActor
    private func navigateForward() {
        guard let snapshot = forwardStack.popLast() else { return }
        backStack.append(currentNavigationSnapshot)
        restoreNavigationSnapshot(snapshot)
    }

    @MainActor
    private func restoreNavigationSnapshot(_ snapshot: AtlasNavigationSnapshot) {
        isRestoringNavigation = true
        sidebarSelection = snapshot.sidebarSelection
        selectedJob = snapshot.selectedJob
        searchViewModel.query = snapshot.query
        searchViewModel.filters = snapshot.filters
        searchViewModel.sortOrder = snapshot.sortOrder
        Task { @MainActor in
            await searchViewModel.search()
            isRestoringNavigation = false
        }
    }

    @ViewBuilder
    private var emptyDetailView: some View {
        switch sidebarSelection {
        case .updates:
            ContentUnavailableView(
                "Select a source update",
                systemImage: "clock.arrow.circlepath",
                description: Text("Choose an update row to drill into that source's current postings.")
            )
        case .savedJobs:
            ContentUnavailableView(
                "Select a saved job",
                systemImage: "tray.full",
                description: Text("Choose a saved job post to load its historical local detail.")
            )
        case .settings:
            ContentUnavailableView(
                "Settings",
                systemImage: "gearshape",
                description: Text("Configure the API server and local save options in the middle pane.")
            )
        case .source:
            ContentUnavailableView(
                "Select a posting",
                systemImage: "doc.text.magnifyingglass",
                description: Text("The middle pane is filtered to this source.")
            )
        default:
            ContentUnavailableView("Select a vacancy", systemImage: "doc.text.magnifyingglass")
        }
    }
}

public struct AtlasSearchScreen: View {
    @Binding private var selection: JobSearchResult?
    @ObservedObject private var viewModel: AtlasSearchViewModel
    private let onSelectJob: ((JobSearchResult) -> Void)?
    @State private var showFilters = false
    @State private var density: ResultDensity = .comfortable

    private static let quickFilterOptions: [(title: String, icon: String)] = [
        ("Closing soon", "clock.badge.exclamationmark"),
        ("Remote", "house"),
        ("Best fit", "target"),
    ]

    public init(
        selection: Binding<JobSearchResult?> = .constant(nil),
        viewModel: AtlasSearchViewModel = AtlasSearchViewModel(),
        onSelectJob: ((JobSearchResult) -> Void)? = nil
    ) {
        self._selection = selection
        self.viewModel = viewModel
        self.onSelectJob = onSelectJob
    }

    public var body: some View {
        VStack(spacing: 0) {
            filterRibbon
            countAndSortBar
            if let errorMessage = viewModel.errorMessage {
                ServerStatusBanner(message: errorMessage)
                    .padding(.horizontal)
                    .padding(.bottom, 10)
            }
            if let userMessage = viewModel.userMessage {
                UserStatusBanner(message: userMessage) {
                    viewModel.clearUserMessage()
                }
                .padding(.horizontal)
                .padding(.bottom, 10)
            }
            if viewModel.isCachingDetails, viewModel.detailCacheWorkTotal > 0 {
                DetailCacheProgressBanner(
                    completed: viewModel.detailCacheCompleted,
                    total: viewModel.detailCacheWorkTotal,
                    message: viewModel.detailCacheMessage ?? "Caching job details for offline use"
                )
                .padding(.horizontal)
                .padding(.bottom, 10)
            }
            Divider()
            resultList
        }
        .navigationTitle("Search")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        .searchable(
            text: $viewModel.query,
            placement: .navigationBarDrawer(displayMode: .always),
            prompt: "Title, keyword, skill, or organization"
        )
        #else
        .searchable(text: $viewModel.query, prompt: "Title, keyword, skill, or organization")
        #endif
        .onSubmit(of: .search) {
            Task { await viewModel.search() }
        }
        .onChange(of: viewModel.query) {
            viewModel.scheduleSearch()
        }
        .onChange(of: viewModel.sortOrder) {
            viewModel.scheduleSearch()
        }
        .onChange(of: viewModel.results) {
            syncSelection(with: viewModel.results)
        }
        .task {
            await viewModel.loadIfNeeded()
            syncSelection(with: viewModel.results)
        }
        .toolbar {
            ToolbarItemGroup {
                #if os(macOS)
                Menu {
                    Picker("Density", selection: $density) {
                        ForEach(ResultDensity.allCases) { value in
                            Text(value.rawValue).tag(value)
                        }
                    }
                } label: {
                    Label("Density", systemImage: density == .compact
                        ? "rectangle.compress.vertical"
                        : "rectangle.expand.vertical")
                }
                .help("Result density")

                Button {
                    showFilters.toggle()
                } label: {
                    Label("Filters", systemImage: viewModel.activeFilterCount == 0
                        ? "line.3.horizontal.decrease.circle"
                        : "line.3.horizontal.decrease.circle.fill")
                        .foregroundStyle(viewModel.activeFilterCount == 0 ? Color.primary : AtlasTheme.accent)
                }
                .keyboardShortcut("f", modifiers: [.command, .option])
                .help("Filters (\u{2318}\u{2325}F)")
                .accessibilityLabel("Filters, \(viewModel.activeFilterCount) active")
                .popover(isPresented: $showFilters, arrowEdge: .bottom) {
                    FilterSheetView(viewModel: viewModel, isPresented: $showFilters)
                        .frame(minWidth: 430, minHeight: 560)
                }

                Button {
                    Task { await viewModel.saveCurrentSearch() }
                } label: {
                    Label("Save Search", systemImage: "bookmark")
                }
                .help("Save the current search")
                #else
                Button {
                    showFilters = true
                } label: {
                    Label("Filters", systemImage: viewModel.activeFilterCount == 0
                        ? "line.3.horizontal.decrease.circle"
                        : "line.3.horizontal.decrease.circle.fill")
                        .foregroundStyle(viewModel.activeFilterCount == 0 ? Color.primary : AtlasTheme.accent)
                }
                .accessibilityLabel("Filters, \(viewModel.activeFilterCount) active")

                Button {
                    Task { await viewModel.saveCurrentSearch() }
                } label: {
                    Label("Save Search", systemImage: "bookmark")
                }
                #endif
            }
        }
        #if os(iOS)
        .sheet(isPresented: $showFilters) {
            FilterSheetView(viewModel: viewModel, isPresented: $showFilters)
                .presentationDetents([.medium, .large])
        }
        #endif
    }

    private var filterRibbon: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(viewModel.activeFilterChips) { chip in
                    FilterChip(viewModel.displayTitle(for: chip), onRemove: {
                        withAnimation(.snappy(duration: 0.2)) {
                            viewModel.removeActiveFilter(chip)
                        }
                    })
                    .transition(.scale(scale: 0.85).combined(with: .opacity))
                }

                if !viewModel.activeFilterChips.isEmpty {
                    Circle()
                        .fill(Color.secondary.opacity(0.35))
                        .frame(width: 3, height: 3)
                }

                ForEach(Self.quickFilterOptions, id: \.title) { option in
                    FilterChip(
                        option.title,
                        systemImage: option.icon,
                        style: viewModel.isQuickFilterActive(option.title) ? .active : .suggestion,
                        onTap: {
                            withAnimation(.snappy(duration: 0.2)) {
                                viewModel.toggleQuickFilter(option.title)
                            }
                        }
                    )
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 10)
        }
        .scrollIndicators(.hidden)
        .mask {
            HStack(spacing: 0) {
                Rectangle()
                LinearGradient(colors: [.black, .clear], startPoint: .leading, endPoint: .trailing)
                    .frame(width: 24)
            }
        }
        .animation(.snappy(duration: 0.2), value: viewModel.activeFilterChips)
    }

    private var countAndSortBar: some View {
        HStack {
            HStack(spacing: 8) {
                if viewModel.isLoading {
                    ProgressView()
                        .controlSize(.small)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(viewModel.total.formatted()) results")
                        .font(.subheadline.weight(.semibold))
                    HStack(spacing: 4) {
                        if viewModel.serverState.isOffline {
                            Image(systemName: "wifi.slash")
                                .imageScale(.small)
                                .foregroundStyle(AtlasTheme.warning)
                        }
                        Text(viewModel.statusSubtitle)
                            .lineLimit(1)
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Menu {
                Picker("Sort", selection: $viewModel.sortOrder) {
                    ForEach(SortOrder.allCases) { order in
                        Text(order.rawValue).tag(order)
                    }
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.up.arrow.down")
                        .imageScale(.small)
                    Text("Sort: \(viewModel.sortOrder.rawValue)")
                }
                .font(.subheadline)
                .foregroundStyle(AtlasTheme.accent)
            }
            .menuStyle(.borderlessButton)
            .tint(AtlasTheme.accent)
            .fixedSize()
        }
        .padding(.horizontal)
        .padding(.bottom, 10)
    }

    @ViewBuilder
    private var resultList: some View {
        #if os(macOS)
        if viewModel.results.isEmpty {
            ContentUnavailableView(
                emptyResultsTitle,
                systemImage: "doc.text.magnifyingglass",
                description: Text(emptyResultsDescription)
            )
        } else {
            List {
                ForEach(viewModel.results) { job in
                    JobResultRow(job: job, density: density)
                        .contentShape(Rectangle())
                        .onTapGesture {
                            select(job)
                        }
                }
                if viewModel.hasMoreResults {
                    LoadMoreResultsRow(
                        shown: viewModel.results.count,
                        total: viewModel.total,
                        remaining: viewModel.remainingResultCount
                    ) {
                        Task { await viewModel.loadMoreResults() }
                    }
                }
            }
            .listStyle(.plain)
        }
        #else
        Group {
            if viewModel.results.isEmpty {
                ContentUnavailableView(
                    emptyResultsTitle,
                    systemImage: "doc.text.magnifyingglass",
                    description: Text(emptyResultsDescription)
                )
            } else {
                List {
                    ForEach(viewModel.results) { job in
                        NavigationLink(value: job) {
                            JobResultRow(job: job)
                        }
                        .swipeActions(edge: .leading) {
                            Button {
                            } label: {
                                Label("Save", systemImage: "bookmark")
                            }
                            .tint(AtlasTheme.accent)
                        }
                        .swipeActions(edge: .trailing) {
                            Button {
                            } label: {
                                Label("Apply", systemImage: "paperplane")
                            }
                            Button {
                            } label: {
                                Label("Source", systemImage: "arrow.up.right.square")
                            }
                        }
                    }
                    if viewModel.hasMoreResults {
                        LoadMoreResultsRow(
                            shown: viewModel.results.count,
                            total: viewModel.total,
                            remaining: viewModel.remainingResultCount
                        ) {
                            Task { await viewModel.loadMoreResults() }
                        }
                    }
                }
                .listStyle(.plain)
            }
        }
        .navigationDestination(for: JobSearchResult.self) { job in
            JobDetailView(job: job)
        }
        .refreshable {
            await viewModel.refresh()
        }
        #endif
    }

    private var emptyResultsTitle: String {
        if viewModel.serverState.isOffline, viewModel.cachedJobCount == 0 {
            return "No local save available"
        }
        return "No matching vacancies"
    }

    private var emptyResultsDescription: String {
        if viewModel.serverState.isOffline, viewModel.cachedJobCount == 0 {
            return "Connect to the local server once and refresh the local save to enable offline search."
        }
        return "Adjust filters or search terms."
    }

    private func syncSelection(with results: [JobSearchResult]) {
        #if os(macOS)
        if let selection, results.contains(selection) {
            return
        }
        selection = nil
        #endif
    }

    private func select(_ job: JobSearchResult) {
        if let onSelectJob {
            onSelectJob(job)
        } else {
            selection = job
        }
    }
}

struct ServerStatusBanner: View {
    let message: String

    var body: some View {
        Label {
            Text(message)
                .lineLimit(2)
        } icon: {
            Image(systemName: "server.rack")
        }
        .font(.caption)
        .foregroundStyle(AtlasTheme.warning)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(AtlasTheme.warning.opacity(0.10))
        )
    }
}

struct UserStatusBanner: View {
    let message: String
    let onDismiss: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Label(message, systemImage: "checkmark.circle")
            Spacer()
            Button {
                onDismiss()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .imageScale(.small)
            }
            .buttonStyle(.plain)
        }
        .font(.caption)
        .foregroundStyle(AtlasTheme.success)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(AtlasTheme.success.opacity(0.10))
        )
    }
}

struct DetailCacheProgressBanner: View {
    let completed: Int
    let total: Int
    let message: String

    private var progressValue: Double {
        guard total > 0 else { return 0 }
        return min(Double(completed), Double(total))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "arrow.down.circle")
                    .foregroundStyle(AtlasTheme.accent)
                Text(message)
                    .lineLimit(2)
                Spacer()
            }
            ProgressView(value: progressValue, total: max(Double(total), 1))
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(AtlasTheme.accent.opacity(0.10))
        )
    }
}

struct LoadMoreResultsRow: View {
    let shown: Int
    let total: Int
    let remaining: Int
    let onLoadMore: () -> Void

    var body: some View {
        Button {
            onLoadMore()
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "chevron.down.circle")
                    .imageScale(.medium)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Show next \(min(200, remaining).formatted()) vacancies")
                        .font(.subheadline.weight(.semibold))
                    Text("\(shown.formatted()) of \(total.formatted()) currently displayed")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(.vertical, 8)
        }
        .buttonStyle(.plain)
        .foregroundStyle(AtlasTheme.accent)
    }
}

struct FilterSheetView: View {
    @ObservedObject var viewModel: AtlasSearchViewModel
    @Binding var isPresented: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var availabilityTask: Task<Void, Never>?

    private let workModeOptions = [
        "onsite",
        "home_based",
        "online_remote",
        "hybrid",
        "multiple_locations",
    ]
    private let contractFallbacks = [
        "staff",
        "consultant_contractor",
        "internship",
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        FilterGroupView(title: "Status", summary: statusSummary) {
                            Toggle("Open only", isOn: boolBinding(\.openOnly))
                            Toggle("Closing soon", isOn: boolBinding(\.closingSoon))
                        }

                        FilterGroupView(title: "Location", summary: locationSummary) {
                            LabeledContent {
                                TextField("Any city", text: textBinding(\.city))
                                    .textFieldStyle(.roundedBorder)
                            } label: {
                                Label("City", systemImage: "mappin.and.ellipse")
                            }
                            LabeledContent {
                                TextField("ISO3", text: textBinding(\.countryISO3))
                                    .textFieldStyle(.roundedBorder)
                                    .textCase(.uppercase)
                                    .frame(maxWidth: 120)
                            } label: {
                                Label("Country", systemImage: "globe.europe.africa")
                            }
                            Toggle("Include uncertain matches", isOn: boolBinding(\.includeLowConfidence))
                        }

                        FilterGroupView(title: "Scope", summary: viewModel.filters.scope.title) {
                            Picker("Scope", selection: scopeBinding) {
                                ForEach(AtlasScopeFilter.allCases) { scope in
                                    Text(scope.title).tag(scope)
                                }
                            }
                            .pickerStyle(.segmented)
                        }

                        FilterFacetGroupView(
                            title: "Contract",
                            emptySummary: "Any contract",
                            options: contractOptions,
                            isSelected: contractBinding,
                            isEnabled: contractOptionEnabled
                        )

                        if viewModel.filters.volunteerKinds.contains(AtlasVolunteerKind.unVolunteer.rawValue) {
                            FilterGroupView(title: "UN Volunteer Category", summary: unvCategorySummary) {
                                FilterChoiceGrid {
                                    ForEach(unvCategoryOptions) { option in
                                        FilterChoiceButton(
                                            title: option.title,
                                            subtitle: option.count.formatted(),
                                            isEnabled: optionEnabled("unv_categories", option.id, selected: viewModel.filters.unvCategories),
                                            isSelected: setBinding(option.id, \.unvCategories)
                                        )
                                    }
                                }
                                VStack(alignment: .leading, spacing: 4) {
                                    ForEach(atlasUNVCategoryInfo) { category in
                                        Text("\(category.title): \(category.detail)")
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }

                        FilterFacetGroupView(
                            title: "Seniority",
                            emptySummary: "Any seniority",
                            options: seniorityOptions,
                            isSelected: { setBinding($0, \.seniorityGroups) },
                            isEnabled: { optionEnabled("seniority_groups", $0, selected: viewModel.filters.seniorityGroups) }
                        )

                        FilterFacetGroupView(
                            title: "Grade",
                            emptySummary: "Any grade",
                            options: gradeOptions,
                            isSelected: { setBinding($0, \.gradeCodes) },
                            isEnabled: { optionEnabled("grades", $0, selected: viewModel.filters.gradeCodes) }
                        )

                        FilterFacetGroupView(
                            title: "CCOG Family",
                            emptySummary: "Any CCOG family",
                            options: viewModel.availabilityFacetOptions(
                                "ccog_families",
                                limit: 20,
                                selected: viewModel.filters.ccogFamilies
                            ),
                            isSelected: { setBinding($0, \.ccogFamilies) },
                            isEnabled: { optionEnabled("ccog_families", $0, selected: viewModel.filters.ccogFamilies) }
                        )

                        FilterFacetGroupView(
                            title: "Organizations",
                            emptySummary: "Any organization",
                            options: viewModel.availabilityFacetOptions(
                                "organizations",
                                limit: 10,
                                selected: viewModel.filters.organizations
                            ),
                            isSelected: { setBinding($0, \.organizations) },
                            isEnabled: { optionEnabled("organizations", $0, selected: viewModel.filters.organizations) }
                        )

                        FilterGroupView(title: "Work Mode", summary: viewModel.filters.workModalities.isEmpty ? "Any mode" : viewModel.filters.workModalitySummary) {
                            FilterChoiceGrid {
                                ForEach(workModeOptionsForDisplay) { option in
                                    FilterChoiceButton(
                                        title: option.title,
                                        subtitle: option.count.formatted(),
                                        isEnabled: optionEnabled("work_modalities", option.id, selected: viewModel.filters.workModalities),
                                        isSelected: setBinding(option.id, \.workModalities)
                                    )
                                }
                            }
                        }

                        FilterGroupView(title: "Capability Tags", summary: viewModel.filters.trimmedCapabilityQuery.isEmpty ? "Any capability" : viewModel.filters.trimmedCapabilityQuery) {
                            TextField("data, programme, reporting", text: textBinding(\.capabilityQuery))
                                .textFieldStyle(.roundedBorder)
                            if !capabilityOptions.isEmpty {
                                FilterChoiceGrid {
                                    ForEach(capabilityOptions) { option in
                                        FilterChoiceButton(
                                            title: option.title,
                                            subtitle: option.count.formatted(),
                                            isEnabled: optionEnabled("capability_tags", option.id, selected: viewModel.filters.capabilityTags),
                                            isSelected: setBinding(option.id, \.capabilityTags)
                                        )
                                    }
                                }
                            }
                            Text("Type comma-separated keywords, or select capability tags. Multiple values in this group match any selected value.")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(20)
                }

                HStack(spacing: 12) {
                    Button("Reset") {
                        viewModel.resetFilters()
                    }
                        .buttonStyle(.bordered)
                    Button {
                        Task {
                            await viewModel.search()
                            isPresented = false
                            dismiss()
                        }
                    } label: {
                        Text("Apply filters")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(AtlasTheme.accent)
                }
                .padding()
                .frame(maxWidth: .infinity)
                .background(.bar)
            }
            .navigationTitle("Filters")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        Task {
                            await viewModel.search()
                            isPresented = false
                            dismiss()
                        }
                    }
                }
            }
        }
        .task {
            await viewModel.refreshFilterAvailability()
        }
        .onChange(of: viewModel.filters) {
            scheduleAvailabilityRefresh()
        }
        .onDisappear {
            availabilityTask?.cancel()
        }
    }

    private var statusSummary: String {
        if viewModel.filters.openOnly && viewModel.filters.closingSoon {
            return "Open, closing soon"
        }
        if viewModel.filters.openOnly {
            return "Open only"
        }
        if viewModel.filters.closingSoon {
            return "Closing soon"
        }
        return "All statuses"
    }

    private var locationSummary: String {
        let city = viewModel.filters.trimmedCity
        let country = viewModel.filters.trimmedCountryISO3.uppercased()
        let values = [city.isEmpty ? nil : city, country.isEmpty ? nil : country].compactMap { $0 }
        let scope = viewModel.filters.scope
        if values.isEmpty {
            return scope == .any ? "Any location" : scope.title
        }
        let base = values.joined(separator: ", ")
        return scope == .any ? base : "\(base), \(scope.title.lowercased())"
    }

    private var contractOptions: [AtlasFacetOption] {
        var options = availabilityFacetOptions(
            "contract_groups",
            fallback: contractFallbacks,
            selected: viewModel.filters.contractGroups
        )
            .filter { $0.id != "volunteer" }
        let volunteerFacets = viewModel.availabilityFacetOptions(
            "volunteer_kinds",
            limit: 2,
            selected: viewModel.filters.volunteerKinds
        )
        let unVolunteerCount = volunteerFacets.first { $0.id == AtlasVolunteerKind.unVolunteer.rawValue }?.count ?? 0
        let genericVolunteerCount = volunteerFacets.first { $0.id == AtlasVolunteerKind.volunteer.rawValue }?.count ?? 0
        if unVolunteerCount > 0 || viewModel.filters.volunteerKinds.contains(AtlasVolunteerKind.unVolunteer.rawValue) {
            options.append(
                AtlasFacetOption(
                    id: AtlasVolunteerKind.unVolunteer.rawValue,
                    title: "UN Volunteer",
                    count: unVolunteerCount
                )
            )
        }
        if genericVolunteerCount > 0 || viewModel.filters.volunteerKinds.contains(AtlasVolunteerKind.volunteer.rawValue) {
            options.append(
                AtlasFacetOption(
                    id: AtlasVolunteerKind.volunteer.rawValue,
                    title: "Volunteer",
                    count: genericVolunteerCount
                )
            )
        }
        return options.sorted { lhs, rhs in
            contractSortKey(lhs.id) < contractSortKey(rhs.id)
        }
    }

    private var seniorityOptions: [AtlasFacetOption] {
        let rawOptions = viewModel.availabilityFacetOptions(
            "seniority_groups",
            limit: 20,
            selected: viewModel.filters.seniorityGroups
        )
        let byID = Dictionary(uniqueKeysWithValues: rawOptions.map { ($0.id, $0) })
        var ordered = atlasSeniorityOrder.compactMap { id -> AtlasFacetOption? in
            if id == "generic_volunteer" {
                let count = viewModel.availabilityFacetOptions("volunteer_kinds", limit: 2)
                    .first { $0.id == AtlasVolunteerKind.volunteer.rawValue }?.count ?? 0
                return count > 0 || viewModel.filters.seniorityGroups.contains(id)
                    ? AtlasFacetOption(id: id, title: atlasSeniorityLabels[id] ?? "Volunteer", count: count)
                    : nil
            }
            guard let option = byID[id] else { return nil }
            return AtlasFacetOption(
                id: option.id,
                title: atlasSeniorityLabels[option.id] ?? option.title,
                count: option.count
            )
        }
        let remaining = rawOptions
            .filter { option in !atlasSeniorityOrder.contains(option.id) }
            .map { AtlasFacetOption(id: $0.id, title: atlasSeniorityLabels[$0.id] ?? $0.title, count: $0.count) }
        ordered.append(contentsOf: remaining)
        return ordered
    }

    private var gradeOptions: [AtlasFacetOption] {
        viewModel.availabilityFacetOptions("grades", limit: 40, selected: viewModel.filters.gradeCodes)
            .sorted { lhs, rhs in gradeOptionSortKey(lhs.id) < gradeOptionSortKey(rhs.id) }
            .map { AtlasFacetOption(id: $0.id, title: displayGradeOption($0.id), count: $0.count) }
    }

    private var unvCategoryOptions: [AtlasFacetOption] {
        let facets = viewModel.availabilityFacetOptions(
            "unv_categories",
            limit: 12,
            selected: viewModel.filters.unvCategories
        )
        let counts = Dictionary(uniqueKeysWithValues: facets.map { ($0.id, $0.count) })
        return atlasUNVCategoryInfo.map {
            AtlasFacetOption(id: $0.id, title: $0.title, count: counts[$0.id] ?? 0)
        }
    }

    private var capabilityOptions: [AtlasFacetOption] {
        viewModel.availabilityFacetOptions(
            "capability_tags",
            limit: 18,
            selected: viewModel.filters.capabilityTags
        )
    }

    private var workModeOptionsForDisplay: [AtlasFacetOption] {
        let available = Dictionary(
            uniqueKeysWithValues: viewModel.availabilityFacetOptions(
                "work_modalities",
                limit: 20,
                selected: viewModel.filters.workModalities
            ).map { ($0.id, $0) }
        )
        return workModeOptions.map { mode in
            available[mode] ?? AtlasFacetOption(
                id: mode,
                title: displayAtlasFilterValue(mode),
                count: 0
            )
        }
    }

    private var unvCategorySummary: String {
        let selected = unvCategoryOptions.filter { viewModel.filters.unvCategories.contains($0.id) }
        guard let first = selected.first else { return "Any UNV category" }
        if selected.count == 1 {
            return first.title
        }
        return "\(first.title) +\(selected.count - 1)"
    }

    private func boolBinding(_ keyPath: WritableKeyPath<AtlasSearchFilters, Bool>) -> Binding<Bool> {
        Binding {
            viewModel.filters[keyPath: keyPath]
        } set: { newValue in
            viewModel.filters[keyPath: keyPath] = newValue
            viewModel.scheduleSearch()
        }
    }

    private func textBinding(_ keyPath: WritableKeyPath<AtlasSearchFilters, String>) -> Binding<String> {
        Binding {
            viewModel.filters[keyPath: keyPath]
        } set: { newValue in
            viewModel.filters[keyPath: keyPath] = newValue
        }
    }

    private var scopeBinding: Binding<AtlasScopeFilter> {
        Binding {
            viewModel.filters.scope
        } set: { newValue in
            viewModel.filters.scope = newValue
            viewModel.scheduleSearch()
        }
    }

    private func contractBinding(_ value: String) -> Binding<Bool> {
        Binding {
            if value == AtlasVolunteerKind.unVolunteer.rawValue || value == AtlasVolunteerKind.volunteer.rawValue {
                return viewModel.filters.volunteerKinds.contains(value)
            }
            return viewModel.filters.contractGroups.contains(value)
        } set: { isSelected in
            var next = viewModel.filters
            if value == AtlasVolunteerKind.unVolunteer.rawValue || value == AtlasVolunteerKind.volunteer.rawValue {
                if isSelected {
                    next.volunteerKinds.insert(value)
                    if value == AtlasVolunteerKind.unVolunteer.rawValue {
                        next.seniorityGroups.insert("volunteer")
                    } else {
                        next.seniorityGroups.insert("generic_volunteer")
                    }
                } else {
                    next.volunteerKinds.remove(value)
                    if value == AtlasVolunteerKind.unVolunteer.rawValue {
                        next.unvCategories.removeAll()
                        next.unvVolunteerTypes.removeAll()
                        next.seniorityGroups.remove("volunteer")
                    } else {
                        next.seniorityGroups.remove("generic_volunteer")
                    }
                }
            } else if isSelected {
                next.contractGroups.insert(value)
            } else {
                next.contractGroups.remove(value)
            }
            viewModel.filters = next
            viewModel.scheduleSearch()
        }
    }

    private func contractOptionEnabled(_ value: String) -> Bool {
        if value == AtlasVolunteerKind.unVolunteer.rawValue || value == AtlasVolunteerKind.volunteer.rawValue {
            return optionEnabled("volunteer_kinds", value, selected: viewModel.filters.volunteerKinds)
        }
        return optionEnabled("contract_groups", value, selected: viewModel.filters.contractGroups)
    }

    private func setBinding(
        _ value: String,
        _ keyPath: WritableKeyPath<AtlasSearchFilters, Set<String>>
    ) -> Binding<Bool> {
        Binding {
            viewModel.filters[keyPath: keyPath].contains(value)
        } set: { isSelected in
            var next = viewModel.filters
            if isSelected {
                next[keyPath: keyPath].insert(value)
            } else {
                next[keyPath: keyPath].remove(value)
            }
            viewModel.filters = next
            viewModel.scheduleSearch()
        }
    }

    private func optionEnabled(_ key: String, _ value: String, selected: Set<String>) -> Bool {
        viewModel.isFilterOptionEnabled(
            key: key,
            value: value,
            isSelected: selected.contains(value)
        )
    }

    private func scheduleAvailabilityRefresh() {
        availabilityTask?.cancel()
        availabilityTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 220_000_000)
            guard !Task.isCancelled else { return }
            await viewModel.refreshFilterAvailability()
        }
    }

    private func facetOptions(_ key: String, fallback: [String]) -> [AtlasFacetOption] {
        let options = viewModel.facetOptions(key, limit: 8)
        if !options.isEmpty {
            return options
        }
        return fallback.map {
            AtlasFacetOption(id: $0, title: displayAtlasFilterValue($0), count: 0)
        }
    }

    private func availabilityFacetOptions(
        _ key: String,
        fallback: [String],
        selected: Set<String>
    ) -> [AtlasFacetOption] {
        let options = viewModel.availabilityFacetOptions(key, limit: 8, selected: selected)
        if !options.isEmpty {
            return options
        }
        return fallback.map {
            AtlasFacetOption(id: $0, title: displayAtlasFilterValue($0), count: 0)
        }
    }

    private func selectionSummary(_ values: Set<String>) -> String {
        let sorted = values.sorted()
        guard let first = sorted.first else { return "Any" }
        let firstDisplay = displayAtlasFilterValue(first)
        if sorted.count == 1 {
            return firstDisplay
        }
        return "\(firstDisplay) +\(sorted.count - 1)"
    }

    private func contractSortKey(_ value: String) -> String {
        let order = [
            "staff",
            "consultant_contractor",
            AtlasVolunteerKind.unVolunteer.rawValue,
            AtlasVolunteerKind.volunteer.rawValue,
            "internship",
            "roster_pipeline",
            "other",
            "unknown",
        ]
        let index = order.firstIndex(of: value) ?? order.count
        return "\(String(format: "%03d", index))-\(value)"
    }

    private func displayGradeOption(_ value: String) -> String {
        let compact = value.replacingOccurrences(of: "-", with: "").uppercased()
        guard compact.count >= 2,
              let digitIndex = compact.firstIndex(where: \.isNumber),
              digitIndex > compact.startIndex else {
            return compact
        }
        return "\(compact[..<digitIndex])-\(compact[digitIndex...])"
    }

    private func gradeOptionSortKey(_ value: String) -> String {
        let compact = value.replacingOccurrences(of: "-", with: "").uppercased()
        let letters = compact.prefix { !$0.isNumber }
        let numbers = compact.drop { !$0.isNumber }
        let padded = String(format: "%03d", Int(numbers) ?? 0)
        return "\(letters)\(padded)"
    }
}

struct FilterGroupView<Content: View>: View {
    let title: String
    let summary: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(.headline)
                Spacer(minLength: 12)
                Text(summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            content
        }
    }
}

struct FilterFacetGroupView: View {
    let title: String
    let emptySummary: String
    let options: [AtlasFacetOption]
    let isSelected: (String) -> Binding<Bool>
    let isEnabled: (String) -> Bool

    var body: some View {
        FilterGroupView(title: title, summary: summary) {
            if options.isEmpty {
                Text("No compatible values with the current filters")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                FilterChoiceGrid {
                    ForEach(options) { option in
                        FilterChoiceButton(
                            title: option.title,
                            subtitle: option.count.formatted(),
                            isEnabled: isEnabled(option.id),
                            isSelected: isSelected(option.id)
                        )
                    }
                }
                Text("Dimmed values would return no jobs with the other active filters. Values in this group match any selected value.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var summary: String {
        let selected = options.filter { isSelected($0.id).wrappedValue }
        guard let first = selected.first else { return emptySummary }
        if selected.count == 1 {
            return first.title
        }
        return "\(first.title) +\(selected.count - 1)"
    }
}

struct FilterChoiceGrid<Content: View>: View {
    @ViewBuilder let content: Content

    private let columns = [
        GridItem(.adaptive(minimum: 150), spacing: 8, alignment: .leading),
    ]

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 8) {
            content
        }
    }
}

struct FilterChoiceButton: View {
    let title: String
    var subtitle: String?
    var isEnabled = true
    @Binding var isSelected: Bool

    var body: some View {
        Button {
            isSelected.toggle()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .imageScale(.small)
                Text(title)
                    .lineLimit(1)
                if let subtitle {
                    Spacer(minLength: 4)
                    Text(subtitle)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            .font(.caption.weight(isSelected ? .semibold : .regular))
            .padding(.horizontal, 9)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .foregroundStyle(foregroundStyle)
            .background(
                Capsule()
                    .fill(backgroundStyle)
            )
            .overlay(
                Capsule()
                    .strokeBorder(borderStyle)
            )
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled && !isSelected)
        .opacity(isEnabled ? 1.0 : 0.42)
        .help(isEnabled ? title : "\(title) has no matches with the other active filters")
    }

    private var foregroundStyle: Color {
        if !isEnabled {
            return .secondary
        }
        return isSelected ? AtlasTheme.accent : .primary
    }

    private var backgroundStyle: Color {
        if !isEnabled {
            return Color.secondary.opacity(0.05)
        }
        return isSelected ? AtlasTheme.accent.opacity(0.14) : Color.secondary.opacity(0.08)
    }

    private var borderStyle: Color {
        if !isEnabled {
            return Color.secondary.opacity(0.10)
        }
        return isSelected ? AtlasTheme.accent.opacity(0.35) : Color.secondary.opacity(0.18)
    }
}

struct AtlasSidebarView: View {
    @Binding var selection: AtlasSidebarSelection?
    @ObservedObject var viewModel: AtlasSearchViewModel

    var body: some View {
        List(selection: $selection) {
            Section("Library") {
                Label("Search", systemImage: "magnifyingglass")
                    .tag(AtlasSidebarSelection.search)
                Label("Saved Jobs", systemImage: "tray.full")
                    .tag(AtlasSidebarSelection.savedJobs)
                Label("Updates", systemImage: "clock.arrow.circlepath")
                    .tag(AtlasSidebarSelection.updates)
                Label("Settings", systemImage: "gearshape")
                    .tag(AtlasSidebarSelection.settings)
            }
            Section("Saved Searches") {
                if viewModel.savedSearches.isEmpty {
                    Text("Use Save Search in the toolbar")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(viewModel.savedSearches) { search in
                        Label(search.name, systemImage: "bookmark")
                            .tag(AtlasSidebarSelection.savedSearch(search.name))
                            .contextMenu {
                                Button(role: .destructive) {
                                    Task { await viewModel.deleteSavedSearch(search) }
                                } label: {
                                    Label("Remove Saved Search", systemImage: "trash")
                                }
                            }
                    }
                }
            }
            Section("Sources") {
                if viewModel.sources.isEmpty {
                    Text("No sources loaded")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(viewModel.sources.prefix(18)) { source in
                        SourceHealthRow(source: source)
                            .tag(AtlasSidebarSelection.source(source.sourceID))
                    }
                }
            }
        }
        .navigationTitle("Atlas")
        .task {
            await viewModel.refreshSidebarData()
        }
    }
}

struct SourceHealthRow: View {
    let source: AtlasSourceSummary

    var body: some View {
        HStack {
            Circle()
                .fill(healthColor)
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text(displayAtlasFilterValue(source.sourceID))
                    .lineLimit(1)
                Text("\(source.openJobs.formatted()) open")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
    }

    private var healthColor: Color {
        switch source.healthStatus?.lowercased() {
        case "ok", "ok_empty":
            return AtlasTheme.success
        case "warning", "degraded":
            return AtlasTheme.warning
        case "issue":
            return AtlasTheme.danger
        default:
            return Color.secondary
        }
    }
}

struct SavedPanel: View {
    @Binding var selection: JobSearchResult?
    @ObservedObject var viewModel: AtlasSearchViewModel
    var onSelectJob: ((JobSearchResult) -> Void)?
    var onRunSavedSearch: (() -> Void)?

    var body: some View {
        List {
            Section("Saved Job Posts") {
                if viewModel.savedJobs.isEmpty {
                    ContentUnavailableView(
                        "No saved job posts",
                        systemImage: "tray.full",
                        description: Text("Use Save on a vacancy detail page to add it here.")
                    )
                } else {
                    ForEach(viewModel.savedJobs) { record in
                        SavedJobRecordRow(
                            record: record,
                            selection: $selection,
                            onSelectJob: onSelectJob,
                            onRemove: {
                                Task { await viewModel.deleteSavedJob(record) }
                            }
                        )
                    }
                }
            }

            Section("Saved Searches") {
                if viewModel.savedSearches.isEmpty {
                    ContentUnavailableView(
                        "No saved searches",
                        systemImage: "bookmark",
                        description: Text("Run a search, adjust filters, then use Save Search.")
                    )
                } else {
                    ForEach(viewModel.savedSearches) { search in
                        HStack(alignment: .center, spacing: 10) {
                            Button {
                                Task {
                                    await viewModel.runSavedSearch(search)
                                    onRunSavedSearch?()
                                }
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(search.name)
                                        .font(.headline)
                                    if let description = search.description, !description.isEmpty {
                                        Text(description)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .buttonStyle(.plain)
                            Button(role: .destructive) {
                                Task { await viewModel.deleteSavedSearch(search) }
                            } label: {
                                Image(systemName: "trash")
                            }
                            .buttonStyle(.borderless)
                            .help("Remove saved search")
                        }
                    }
                }
            }
        }
        .navigationTitle("Saved")
        .navigationDestination(for: JobSearchResult.self) { job in
            JobDetailView(job: job)
        }
        .task { await viewModel.refreshSidebarData() }
    }
}

struct SavedJobRecordRow: View {
    let record: AtlasApplicationRecord
    @Binding var selection: JobSearchResult?
    let onSelectJob: ((JobSearchResult) -> Void)?
    let onRemove: () -> Void

    private var placeholder: JobSearchResult {
        JobSearchResult.savedPlaceholder(record)
    }

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            #if os(iOS)
            NavigationLink(value: placeholder) {
                savedJobLabel
            }
            #else
            Button {
                if let onSelectJob {
                    onSelectJob(placeholder)
                } else {
                    selection = placeholder
                }
            } label: {
                savedJobLabel
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            #endif

            Button(role: .destructive) {
                onRemove()
            } label: {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
            .help("Remove saved job")
        }
    }

    private var savedJobLabel: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(record.jobKey)
                .font(.headline)
                .lineLimit(1)
            HStack(spacing: 6) {
                Text(displayAtlasFilterValue(record.status))
                if let updatedAt = record.updatedAt, !updatedAt.isEmpty {
                    Text(updatedAt)
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
    }
}

private extension JobSearchResult {
    static func savedPlaceholder(_ record: AtlasApplicationRecord) -> JobSearchResult {
        let parts = record.jobKey.split(separator: ":", maxSplits: 1).map(String.init)
        let sourceID = parts.first ?? "saved"
        let externalID = parts.count > 1 ? parts[1] : record.jobKey
        return JobSearchResult(
            jobKey: record.jobKey,
            title: "Saved vacancy \(externalID)",
            organization: displayAtlasFilterValue(sourceID),
            sourceID: sourceID,
            dutyStation: "Load details to view location",
            gradeCode: "Unknown",
            contractLabel: displayAtlasFilterValue(record.status),
            workModality: "Unknown",
            closingDate: nil,
            needsReview: false,
            locationConfidence: nil,
            gradeConfidence: nil,
            score: nil,
            scoreReasons: [],
            matchSummary: "Saved from the application tracker.",
            description: "Loading the saved vacancy from the local job database.",
            status: record.status
        )
    }
}

struct UpdatesPanel: View {
    @ObservedObject var viewModel: AtlasSearchViewModel
    var onSelectSource: ((String) -> Void)?

    var body: some View {
        List {
            if viewModel.recentRuns.isEmpty {
                ContentUnavailableView(
                    "No source updates",
                    systemImage: "clock.arrow.circlepath",
                    description: Text("Run a sync to populate source run history.")
                )
            } else {
                ForEach(viewModel.recentRuns) { run in
                    Button {
                        if let onSelectSource {
                            onSelectSource(run.sourceID)
                        } else {
                            viewModel.filters.sourceIDs = [run.sourceID]
                            Task { await viewModel.search() }
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text(displayAtlasFilterValue(run.sourceID))
                                    .font(.headline)
                                Spacer()
                                Text(run.fetched.formatted())
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                            Text("Inserted \(run.inserted.formatted()) · Updated \(run.updated.formatted()) · Missing \(run.missing.formatted()) · Closed \(run.closed.formatted())")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                            if let observedAt = run.observedAt {
                                Text(observedAt)
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(.tertiary)
                            }
                        }
                        .padding(.vertical, 4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .navigationTitle("Updates")
        .task { await viewModel.refreshSidebarData() }
    }
}

struct SourcesPanel: View {
    @ObservedObject var viewModel: AtlasSearchViewModel
    var onSelectSource: ((AtlasSourceSummary) -> Void)?

    var body: some View {
        List {
            if viewModel.sources.isEmpty {
                ContentUnavailableView(
                    "No sources",
                    systemImage: "antenna.radiowaves.left.and.right",
                    description: Text("The API did not return source summaries.")
                )
            } else {
                ForEach(viewModel.sources) { source in
                    Button {
                        if let onSelectSource {
                            onSelectSource(source)
                        } else {
                            Task { await viewModel.filterBySource(source) }
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                SourceHealthRow(source: source)
                                Spacer()
                                Text("\(source.totalJobs.formatted()) total")
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                            if let health = source.healthStatus {
                                Text("Health: \(displayAtlasFilterValue(health))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            if let lastSeen = source.lastSeenAt {
                                Text(lastSeen)
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(.tertiary)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .navigationTitle("Sources")
        .task { await viewModel.refreshSidebarData() }
    }
}

struct AtlasSettingsPanel: View {
    @ObservedObject var viewModel: AtlasSearchViewModel
    @Environment(\.openURL) private var openURL
    #if os(iOS)
    @StateObject private var localNetworkProbe = AtlasLocalNetworkPermissionProbe()
    #endif
    @State private var apiBaseURLText = ""
    @State private var connectionMessage: String?
    @State private var isTesting = false
    @State private var isSaving = false
    @State private var isRefreshingLocalSave = false

    private let refreshOptions: [(String, Double)] = [
        ("Every hour", 1),
        ("Every 6 hours", 6),
        ("Every 12 hours", 12),
        ("Every 24 hours", 24),
        ("Every 48 hours", 48),
        ("Weekly", 168),
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("API base URL", text: $apiBaseURLText)
                        #if os(iOS)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        #endif
                        .onChange(of: apiBaseURLText) {
                            connectionMessage = nil
                            viewModel.clearErrorMessage()
                        }

                    Text("Saved server: \(viewModel.apiBaseURL.absoluteString)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                    if let draftBaseURL, draftBaseURL != viewModel.apiBaseURL {
                        Text("Editing: \(draftBaseURL.absoluteString)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                        Text("The saved server changes only after Save and Reload connects successfully.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    HStack {
                        Button {
                            Task { await testConnection() }
                        } label: {
                            Label("Test", systemImage: "network")
                        }
                        .disabled(isTesting || apiBaseURLText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                        Button {
                            Task { await saveAndReload() }
                        } label: {
                            Label("Save and Reload", systemImage: "arrow.clockwise")
                        }
                        .disabled(isSaving || apiBaseURLText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }

                Section("Status") {
                    Text(viewModel.statusSubtitle.isEmpty ? "Not connected" : viewModel.statusSubtitle)
                    if let connectionMessage {
                        Text(connectionMessage)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    } else if let errorMessage = viewModel.errorMessage,
                              draftBaseURL == nil || draftBaseURL == viewModel.apiBaseURL {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .textSelection(.enabled)
                    }
                }

                Section("Local Save") {
                    LabeledContent("Last updated", value: AtlasLocalCache.formattedSavedAt(viewModel.cacheSavedAt))
                    LabeledContent("Cached jobs", value: viewModel.cachedJobCount.formatted())
                    LabeledContent(
                        "Cached details",
                        value: "\(viewModel.cachedDetailCount.formatted()) / \(viewModel.detailCacheTotal.formatted())"
                    )
                    if let detailCacheMessage = viewModel.detailCacheMessage {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(
                                detailCacheMessage,
                                systemImage: viewModel.isCachingDetails ? "arrow.down.circle" : "externaldrive.badge.checkmark"
                            )
                            if viewModel.isCachingDetails, viewModel.detailCacheWorkTotal > 0 {
                                ProgressView(
                                    value: min(
                                        Double(viewModel.detailCacheCompleted),
                                        Double(viewModel.detailCacheWorkTotal)
                                    ),
                                    total: max(Double(viewModel.detailCacheWorkTotal), 1)
                                )
                                Text("\(viewModel.detailCacheCompleted.formatted()) of \(viewModel.detailCacheWorkTotal.formatted()) detail requests completed")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }

                    Picker("Auto refresh", selection: Binding(
                        get: { viewModel.refreshIntervalHours },
                        set: { viewModel.updateRefreshInterval(hours: $0) }
                    )) {
                        ForEach(refreshOptions, id: \.1) { option in
                            Text(option.0).tag(option.1)
                        }
                    }

                    Button {
                        Task { await refreshLocalSave() }
                    } label: {
                        Label("Refresh Local Save Now", systemImage: "arrow.down.circle")
                    }
                    .disabled(isRefreshingLocalSave)

                    Text("The app uses the local save immediately and refreshes from the Mac only when this interval has passed or when you refresh manually.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("Full vacancy details are also stored locally. If the app closes during detail caching, the next launch resumes the missing detail files.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("iPhone Setup") {
                    Text("Use the Mac LAN URL, for example http://<current-mac-ip>:8765. On the Mac, run ipconfig getifaddr en0 or ipconfig getifaddr en1 to find the current Wi-Fi IP. Keep job-api running while using the app.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("If the connection is blocked, enable Local Network for AtlasIOSHost in iPhone Settings.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    #if os(iOS)
                    Button {
                        localNetworkProbe.requestPermission()
                    } label: {
                        Label("Request Local Network Access", systemImage: "wifi.router")
                    }

                    Button {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            openURL(url)
                        }
                    } label: {
                        Label("Open App Settings", systemImage: "gearshape")
                    }
                    if let message = localNetworkProbe.message {
                        Text(message)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    #endif
                }
            }
            .navigationTitle("Settings")
            .onAppear {
                if apiBaseURLText.isEmpty {
                    apiBaseURLText = viewModel.apiBaseURL.absoluteString
                }
            }
        }
    }

    @MainActor
    private func testConnection() async {
        isTesting = true
        connectionMessage = nil
        viewModel.clearErrorMessage()
        defer { isTesting = false }
        connectionMessage = await viewModel.healthSummary(forBaseURL: apiBaseURLText)
    }

    @MainActor
    private func saveAndReload() async {
        isSaving = true
        connectionMessage = nil
        viewModel.clearErrorMessage()
        defer { isSaving = false }
        if let error = await viewModel.updateAPIBaseURL(apiBaseURLText) {
            connectionMessage = error
        } else {
            connectionMessage = "Saved \(viewModel.apiBaseURL.absoluteString) and reloaded search data."
            apiBaseURLText = viewModel.apiBaseURL.absoluteString
        }
    }

    @MainActor
    private func refreshLocalSave() async {
        isRefreshingLocalSave = true
        defer { isRefreshingLocalSave = false }
        await viewModel.refresh()
        connectionMessage = "Local save updated at \(AtlasLocalCache.formattedSavedAt(viewModel.cacheSavedAt)). Detail caching continues in the background until all cached jobs are available offline."
    }

    private var draftBaseURL: URL? {
        AtlasAPIClient.normalizedBaseURL(from: apiBaseURLText)
    }
}

#if os(iOS)
@MainActor
private final class AtlasLocalNetworkPermissionProbe: ObservableObject {
    @Published var message: String?
    private var browser: NWBrowser?

    func requestPermission() {
        message = "Requesting Local Network permission..."

        let parameters = NWParameters.tcp
        parameters.includePeerToPeer = true

        let browser = NWBrowser(for: .bonjour(type: "_http._tcp", domain: nil), using: parameters)
        self.browser = browser
        browser.stateUpdateHandler = { [weak self] state in
            Task { @MainActor in
                switch state {
                case .ready:
                    self?.message = "Local Network request sent. If prompted by iOS, tap Allow, then use Test."
                case .failed(let error):
                    self?.message = "Local Network probe failed: \(error.localizedDescription)"
                    self?.stop()
                case .waiting(let error):
                    self?.message = "Waiting for Local Network access: \(error.localizedDescription)"
                case .cancelled:
                    break
                default:
                    break
                }
            }
        }
        browser.start(queue: .main)

        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            stop()
            if message == "Requesting Local Network permission..." {
                message = "Local Network request completed. Use Test; if blocked, check Settings > Privacy & Security > Local Network."
            }
        }
    }

    private func stop() {
        browser?.cancel()
        browser = nil
    }
}
#endif

struct PlaceholderSection: View {
    let title: String
    let systemImage: String
    let message: String

    var body: some View {
        NavigationStack {
            ContentUnavailableView(title, systemImage: systemImage, description: Text(message))
                .navigationTitle(title)
        }
    }
}

#Preview("Atlas Search") {
    AtlasRootView(searchViewModel: .preview())
}
