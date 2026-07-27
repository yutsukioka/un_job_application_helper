import SwiftUI

public enum AtlasVaultSavedSearchPresentationStatus:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case hidden
    case loading
    case ready
    case saving
    case saveFailed
    case saveDurabilityUnconfirmed
    case locking
    case unavailable

    public var description: String {
        switch self {
        case .hidden:
            "hidden"
        case .loading:
            "loading"
        case .ready:
            "ready"
        case .saving:
            "saving"
        case .saveFailed:
            "saveFailed"
        case .saveDurabilityUnconfirmed:
            "saveDurabilityUnconfirmed"
        case .locking:
            "locking"
        case .unavailable:
            "unavailable"
        }
    }

    public var debugDescription: String {
        description
    }
}

@MainActor
public final class AtlasVaultSavedSearchPresentationOwner:
    ObservableObject,
    AtlasVaultPrivateSessionBoundary,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private struct ActivationOperation {
        let identifier: UUID
        let ownerGeneration: UInt64
        let work: Task<
            Result<
                AtlasVaultSavedSearchSnapshot,
                AtlasVaultSavedSearchFailure
            >,
            Never
        >
    }

    private struct MutationOperation {
        let identifier: UUID
        let ownerGeneration: UInt64
        let work: Task<
            Result<
                AtlasVaultSavedSearchMutationResult,
                AtlasVaultSavedSearchFailure
            >,
            Never
        >
    }

    @Published public private(set) var status:
        AtlasVaultSavedSearchPresentationStatus = .hidden
    @Published public private(set) var items:
        [AtlasVaultSavedSearchPresentation] = []

    private let coordinator: any AtlasVaultSavedSearchCoordinating
    private var ownerGeneration: UInt64 = 0
    private var activationOperation: ActivationOperation?
    private var mutationOperation: MutationOperation?

    public init(
        coordinator: any AtlasVaultSavedSearchCoordinating
    ) {
        self.coordinator = coordinator
    }

    public func activatePrivateSession(
        selectedVault: String
    ) async -> Bool {
        let drainGeneration = ownerGeneration
        await cancelAndDrainActivation()
        guard ownerGeneration == drainGeneration else {
            return false
        }
        await cancelAndDrainMutation()
        guard ownerGeneration == drainGeneration else {
            return false
        }
        ownerGeneration &+= 1
        let activationGeneration = ownerGeneration
        items = []
        status = .loading

        let identifier = UUID()
        let coordinator = coordinator
        let task = Task<
            Result<
                AtlasVaultSavedSearchSnapshot,
                AtlasVaultSavedSearchFailure
            >,
            Never
        > {
            do {
                return .success(
                    try await coordinator.activate(
                        selectedVault: selectedVault
                    )
                )
            } catch let failure as AtlasVaultSavedSearchFailure {
                return .failure(failure)
            } catch {
                return .failure(.unavailable)
            }
        }
        activationOperation = ActivationOperation(
            identifier: identifier,
            ownerGeneration: activationGeneration,
            work: task
        )
        let result = await task.value
        guard activationOperation?.identifier == identifier else {
            return false
        }
        activationOperation = nil
        guard ownerGeneration == activationGeneration else {
            return false
        }

        switch result {
        case let .success(snapshot):
            items = snapshot.searches
            status = .ready
            return true
        case .failure:
            items = []
            status = .unavailable
            return false
        }
    }

    public func create(
        _ draft: AtlasVaultSavedSearchDraft
    ) async {
        guard mayBeginMutation else {
            return
        }
        let coordinator = coordinator
        await performMutation {
            try await coordinator.create(draft)
        }
    }

    public func delete(
        presentationID: AtlasVaultPresentationID
    ) async {
        guard mayBeginMutation else {
            return
        }
        let coordinator = coordinator
        await performMutation {
            try await coordinator.delete(
                presentationID: presentationID
            )
        }
    }

    public func hidePrivatePresentation() {
        ownerGeneration &+= 1
        activationOperation?.work.cancel()
        mutationOperation?.work.cancel()
        items = []
        status = .hidden
    }

    public func beginLocking() {
        hidePrivatePresentation()
        status = .locking
    }

    public func stopAndDrainPrivateSession() async {
        hidePrivatePresentation()
        await cancelAndDrainActivation()
        await cancelAndDrainMutation()
        await coordinator.stop()
        items = []
        status = .hidden
    }

    public nonisolated var description: String {
        "AtlasVaultSavedSearchPresentationOwner(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private var mayBeginMutation: Bool {
        mutationOperation == nil
            && activationOperation == nil
            && (status == .ready || status == .saveFailed)
    }

    private func performMutation(
        _ operation:
            @escaping @Sendable () async throws
                -> AtlasVaultSavedSearchMutationResult
    ) async {
        let operationGeneration = ownerGeneration
        let identifier = UUID()
        status = .saving
        let task = Task<
            Result<
                AtlasVaultSavedSearchMutationResult,
                AtlasVaultSavedSearchFailure
            >,
            Never
        > {
            do {
                return .success(try await operation())
            } catch let failure as AtlasVaultSavedSearchFailure {
                return .failure(failure)
            } catch {
                return .failure(.unavailable)
            }
        }
        mutationOperation = MutationOperation(
            identifier: identifier,
            ownerGeneration: operationGeneration,
            work: task
        )
        let result = await task.value
        guard mutationOperation?.identifier == identifier else {
            return
        }
        mutationOperation = nil
        guard ownerGeneration == operationGeneration else {
            return
        }

        switch result {
        case let .success(.committed(snapshot)):
            items = snapshot.searches
            status = .ready
        case let .success(
            .committedDurabilityUnconfirmed(snapshot)
        ):
            items = snapshot.searches
            status = .saveDurabilityUnconfirmed
        case let .success(.failed(snapshot)),
             let .success(.staleItem(snapshot)):
            items = snapshot.searches
            status = .saveFailed
        case let .success(.cancelled(snapshot)):
            items = snapshot.searches
            status = .ready
        case .success(.locked), .failure(.locked):
            hidePrivatePresentation()
        case .failure:
            status = .saveFailed
        }
    }

    private func cancelAndDrainActivation() async {
        guard let operation = activationOperation else {
            return
        }
        operation.work.cancel()
        _ = await operation.work.value
        if activationOperation?.identifier == operation.identifier {
            activationOperation = nil
        }
    }

    private func cancelAndDrainMutation() async {
        guard let operation = mutationOperation else {
            return
        }
        operation.work.cancel()
        _ = await operation.work.value
        if mutationOperation?.identifier == operation.identifier {
            mutationOperation = nil
        }
    }
}

public struct AtlasVaultSavedSearchActions: Sendable {
    private let createAction:
        @Sendable (AtlasVaultSavedSearchDraft) async -> Void
    private let deleteAction:
        @Sendable (AtlasVaultPresentationID) async -> Void
    private let lockAction: @Sendable () async -> Void

    public init(
        create:
            @escaping @Sendable (
                AtlasVaultSavedSearchDraft
            ) async -> Void,
        delete:
            @escaping @Sendable (
                AtlasVaultPresentationID
            ) async -> Void,
        lock: @escaping @Sendable () async -> Void
    ) {
        createAction = create
        deleteAction = delete
        lockAction = lock
    }

    public func create(_ draft: AtlasVaultSavedSearchDraft) async {
        await createAction(draft)
    }

    public func delete(_ id: AtlasVaultPresentationID) async {
        await deleteAction(id)
    }

    public func lock() async {
        await lockAction()
    }
}

@MainActor
public struct AtlasVaultSavedSearchContext {
    public let owner: AtlasVaultSavedSearchPresentationOwner
    public let actions: AtlasVaultSavedSearchActions

    public init(
        owner: AtlasVaultSavedSearchPresentationOwner,
        actions: AtlasVaultSavedSearchActions
    ) {
        self.owner = owner
        self.actions = actions
    }
}

@MainActor
public struct AtlasVaultSavedSearchView: View {
    private struct DeleteCandidate {
        let id: AtlasVaultPresentationID
        let name: String
    }

    @ObservedObject private var owner:
        AtlasVaultSavedSearchPresentationOwner
    private let actions: AtlasVaultSavedSearchActions
    @State private var isCreatePresented = false
    @State private var name = ""
    @State private var searchText = ""
    @State private var deleteCandidate: DeleteCandidate?
    @State private var isDeletePresented = false

    public init(
        owner: AtlasVaultSavedSearchPresentationOwner,
        actions: AtlasVaultSavedSearchActions
    ) {
        self.owner = owner
        self.actions = actions
    }

    public var body: some View {
        NavigationStack {
            Group {
                if owner.status == .loading {
                    ProgressView("Loading saved searches")
                } else if owner.items.isEmpty {
                    ContentUnavailableView(
                        "No Saved Searches",
                        systemImage: "magnifyingglass",
                        description: Text(
                            "Create a saved search for this unlocked vault."
                        )
                    )
                } else {
                    List(owner.items, id: \.id) { item in
                        savedSearchRow(item)
                    }
                }
            }
            .navigationTitle("Saved Searches")
            .toolbar {
                ToolbarItem(placement: .navigation) {
                    Label(
                        "Unlocked private vault",
                        systemImage: "lock.open"
                    )
                    .font(.caption)
                }
                ToolbarItemGroup(placement: .primaryAction) {
                    Button {
                        isCreatePresented = true
                    } label: {
                        Label(
                            "Add Saved Search",
                            systemImage: "plus"
                        )
                    }
                    .disabled(!allowsMutation)

                    Button {
                        clearDraft()
                        owner.beginLocking()
                        Task {
                            await actions.lock()
                        }
                    } label: {
                        Label("Lock Vault", systemImage: "lock")
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                statusBanner
            }
        }
        .sheet(isPresented: $isCreatePresented, onDismiss: clearDraft) {
            NavigationStack {
                Form {
                    TextField("Name", text: $name)
                    TextField("Search terms", text: $searchText)
                }
                .navigationTitle("Add Saved Search")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") {
                            clearDraft()
                            isCreatePresented = false
                        }
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") {
                            let draft = AtlasVaultSavedSearchDraft(
                                name: name,
                                searchText: searchText
                            )
                            clearDraft()
                            isCreatePresented = false
                            Task {
                                await actions.create(draft)
                            }
                        }
                        .disabled(!allowsMutation)
                    }
                }
            }
        }
        .confirmationDialog(
            "Delete Saved Search?",
            isPresented: $isDeletePresented,
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                guard let candidate = deleteCandidate else {
                    return
                }
                deleteCandidate = nil
                Task {
                    await actions.delete(candidate.id)
                }
            }
            Button("Cancel", role: .cancel) {
                deleteCandidate = nil
            }
        } message: {
            if let deleteCandidate {
                Text(deleteCandidate.name)
            }
        }
        .onChange(of: owner.status) { _, status in
            if status == .hidden || status == .locking {
                clearDraft()
                isCreatePresented = false
                deleteCandidate = nil
                isDeletePresented = false
            }
        }
        .onDisappear {
            clearDraft()
            deleteCandidate = nil
        }
    }

    @ViewBuilder
    private func savedSearchRow(
        _ item: AtlasVaultSavedSearchPresentation
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(item.name)
                .font(.headline)
            Text(item.summary)
                .font(.subheadline)
            if let query = item.request.text, !query.isEmpty {
                Text(query)
                    .font(.body)
            }
            if let updated = item.updatedAt ?? item.createdAt {
                Text(updated)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .swipeActions {
            Button(role: .destructive) {
                deleteCandidate = DeleteCandidate(
                    id: item.id,
                    name: item.name
                )
                isDeletePresented = true
            } label: {
                Label("Delete", systemImage: "trash")
            }
            .disabled(!allowsMutation)
        }
    }

    @ViewBuilder
    private var statusBanner: some View {
        switch owner.status {
        case .saving:
            ProgressView("Saving encrypted search")
                .padding(12)
                .frame(maxWidth: .infinity)
                .background(.bar)
        case .saveFailed:
            Text("The saved-search change could not be saved.")
                .padding(12)
                .frame(maxWidth: .infinity)
                .background(.bar)
        case .saveDurabilityUnconfirmed:
            Text(
                "The change was committed, but durability could not be confirmed. Lock the vault before retrying."
            )
            .padding(12)
            .frame(maxWidth: .infinity)
            .background(.bar)
        case .unavailable:
            Text("Saved searches are unavailable.")
                .padding(12)
                .frame(maxWidth: .infinity)
                .background(.bar)
        case .hidden, .loading, .ready, .locking:
            EmptyView()
        }
    }

    private var allowsMutation: Bool {
        owner.status == .ready || owner.status == .saveFailed
    }

    private func clearDraft() {
        name = ""
        searchText = ""
    }
}
