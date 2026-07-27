import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultSavedSearchViewTests: XCTestCase {
    private static let selectedVault =
        "00000000-0000-4000-8000-000000000263"

    func testSavedSearchOwnerContextAndViewExist() throws {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchView.swift"
        )

        for required in [
            "AtlasVaultSavedSearchPresentationOwner",
            "AtlasVaultSavedSearchPresentationStatus",
            "AtlasVaultSavedSearchActions",
            "AtlasVaultSavedSearchContext",
            "AtlasVaultSavedSearchView",
            "Saved Searches",
            "Add Saved Search",
            "Edit Saved Search",
            "Label(\"Edit\"",
            "Lock Vault",
            "confirmationDialog",
            "TextField",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
    }

    func testPrivateViewExcludesInternalAndOtherPrivateModels() throws {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchView.swift"
        )

        for forbidden in [
            "AtlasHydratedSavedJob",
            "AtlasHydratedApplicationNote",
            "AtlasHydratedProfileSnippet",
            "AtlasHydratedDraftMetadata",
            "recordID",
            "parentRevision",
            "keyID",
            "vaultID",
            "FileManager",
            "Keychain",
            "SecItem",
            "URLSession",
            "UserDefaults",
            ".task",
            ".onAppear",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testOwnerConstructionIsHiddenAndCreatesNoTask() async {
        let coordinator = SavedSearchViewCoordinatorFake(
            activationSnapshot: Self.snapshot(name: "Initial")
        )
        let owner = AtlasVaultSavedSearchPresentationOwner(
            coordinator: coordinator
        )

        XCTAssertEqual(owner.status, .hidden)
        XCTAssertTrue(owner.items.isEmpty)
        let calls = await coordinator.calls()
        XCTAssertEqual(calls, [String]())
        XCTAssertTrue(owner.description.contains("<redacted>"))
    }

    func testOwnerActivationPublishesOnlyCompletedSnapshot() async {
        let expected = Self.snapshot(name: "Private Search")
        let coordinator = SavedSearchViewCoordinatorFake(
            activationSnapshot: expected
        )
        let owner = AtlasVaultSavedSearchPresentationOwner(
            coordinator: coordinator
        )

        let activated = await owner.activatePrivateSession(
            selectedVault: Self.selectedVault
        )

        XCTAssertTrue(activated)
        XCTAssertEqual(owner.status, .ready)
        XCTAssertEqual(owner.items, expected.searches)
        let calls = await coordinator.calls()
        XCTAssertEqual(calls, ["activate"])
    }

    func testActivationDrainsCancelledMutationBeforePublishingReady()
        async throws
    {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchView.swift"
        )
        let activationStart = try XCTUnwrap(
            source.range(
                of: "public func activatePrivateSession("
            )
        )
        let activationEnd = try XCTUnwrap(
            source.range(
                of: "    public func create(",
                range: activationStart.upperBound..<source.endIndex
            )
        )
        let activationBody = String(
            source[
                activationStart.lowerBound..<activationEnd.lowerBound
            ]
        )
        let activationDrain = try XCTUnwrap(
            activationBody.range(of: "cancelAndDrainActivation")
        )
        let mutationDrain = try XCTUnwrap(
            activationBody.range(of: "cancelAndDrainMutation")
        )
        let loading = try XCTUnwrap(
            activationBody.range(of: "status = .loading")
        )

        XCTAssertLessThan(
            activationDrain.lowerBound,
            mutationDrain.lowerBound
        )
        XCTAssertLessThan(mutationDrain.lowerBound, loading.lowerBound)
        XCTAssertTrue(
            source.contains(
                "private func cancelAndDrainMutation() async"
            )
        )
    }

    func testImmediateHideFencesLateActivation() async {
        let gate = SavedSearchViewSuspensionGate()
        let coordinator = SavedSearchViewCoordinatorFake(
            activationSnapshot: Self.snapshot(name: "Late Private"),
            activationGate: gate
        )
        let owner = AtlasVaultSavedSearchPresentationOwner(
            coordinator: coordinator
        )

        let activation = Task { @MainActor in
            await owner.activatePrivateSession(
                selectedVault: Self.selectedVault
            )
        }
        await gate.waitUntilEntered()
        XCTAssertEqual(owner.status, .loading)
        XCTAssertTrue(owner.items.isEmpty)

        owner.hidePrivatePresentation()
        XCTAssertEqual(owner.status, .hidden)
        XCTAssertTrue(owner.items.isEmpty)
        await gate.release()
        let activated = await activation.value

        XCTAssertFalse(activated)
        XCTAssertEqual(owner.status, .hidden)
        XCTAssertTrue(owner.items.isEmpty)
    }

    func testOwnerDoesNotOptimisticallyMutateAndMapsSaveOutcomes()
        async
    {
        let initial = Self.snapshot(name: "Committed")
        let refreshed = Self.snapshot(name: "Created")
        let durability = Self.snapshot(name: "Durable Warning")
        let gate = SavedSearchViewSuspensionGate()
        let coordinator = SavedSearchViewCoordinatorFake(
            activationSnapshot: initial,
            createResults: [
                .committed(refreshed),
                .failed(refreshed),
                .committedDurabilityUnconfirmed(durability),
            ],
            mutationGate: gate
        )
        let owner = AtlasVaultSavedSearchPresentationOwner(
            coordinator: coordinator
        )
        let activated = await owner.activatePrivateSession(
            selectedVault: Self.selectedVault
        )
        XCTAssertTrue(activated)

        let first = Task { @MainActor in
            await owner.create(
                AtlasVaultSavedSearchDraft(
                    name: "Created",
                    searchText: ""
                )
            )
        }
        await gate.waitUntilEntered()
        XCTAssertEqual(owner.status, .saving)
        XCTAssertEqual(owner.items, initial.searches)
        await gate.release()
        await first.value
        XCTAssertEqual(owner.status, .ready)
        XCTAssertEqual(owner.items, refreshed.searches)

        await owner.create(
            AtlasVaultSavedSearchDraft(
                name: "Retry",
                searchText: ""
            )
        )
        XCTAssertEqual(owner.status, .saveFailed)
        XCTAssertEqual(owner.items, refreshed.searches)

        await owner.create(
            AtlasVaultSavedSearchDraft(
                name: "Warning",
                searchText: ""
            )
        )
        XCTAssertEqual(owner.status, .saveDurabilityUnconfirmed)
        XCTAssertEqual(owner.items, durability.searches)
    }

    func testOwnerUpdateIsExplicitAndDoesNotOptimisticallyMutate()
        async
    {
        let initial = Self.snapshot(name: "Original")
        let refreshed = Self.snapshot(name: "Renamed")
        let gate = SavedSearchViewSuspensionGate()
        let coordinator = SavedSearchViewCoordinatorFake(
            activationSnapshot: initial,
            updateResults: [.committed(refreshed)],
            mutationGate: gate
        )
        let owner = AtlasVaultSavedSearchPresentationOwner(
            coordinator: coordinator
        )
        _ = await owner.activatePrivateSession(
            selectedVault: Self.selectedVault
        )

        let update = Task { @MainActor in
            await owner.update(
                presentationID: initial.searches[0].id,
                draft: AtlasVaultSavedSearchDraft(
                    name: "Renamed",
                    searchText: "updated terms"
                )
            )
        }
        await gate.waitUntilEntered()
        XCTAssertEqual(owner.status, .saving)
        XCTAssertEqual(owner.items, initial.searches)
        await gate.release()
        await update.value

        XCTAssertEqual(owner.status, .ready)
        XCTAssertEqual(owner.items, refreshed.searches)
        let calls = await coordinator.calls()
        XCTAssertEqual(calls.filter { $0 == "update" }.count, 1)
    }

    func testHideDuringMutationClearsBeforeCompletionAndStopDrains()
        async
    {
        let initial = Self.snapshot(name: "Visible")
        let late = Self.snapshot(name: "Must Not Return")
        let gate = SavedSearchViewSuspensionGate()
        let coordinator = SavedSearchViewCoordinatorFake(
            activationSnapshot: initial,
            createResults: [.committed(late)],
            mutationGate: gate
        )
        let owner = AtlasVaultSavedSearchPresentationOwner(
            coordinator: coordinator
        )
        _ = await owner.activatePrivateSession(
            selectedVault: Self.selectedVault
        )

        let mutation = Task { @MainActor in
            await owner.create(
                AtlasVaultSavedSearchDraft(
                    name: "Late",
                    searchText: ""
                )
            )
        }
        await gate.waitUntilEntered()
        owner.beginLocking()
        XCTAssertEqual(owner.status, .locking)
        XCTAssertTrue(owner.items.isEmpty)
        await gate.release()
        await mutation.value
        XCTAssertEqual(owner.status, .locking)
        XCTAssertTrue(owner.items.isEmpty)

        await owner.stopAndDrainPrivateSession()
        XCTAssertEqual(owner.status, .hidden)
        XCTAssertTrue(owner.items.isEmpty)
        let calls = await coordinator.calls()
        XCTAssertTrue(calls.contains("stop"))
    }

    func testReactivationDrainsCancellationIgnoringMutationBeforeReady()
        async
    {
        let initial = Self.snapshot(name: "Initial Session")
        let stale = Self.snapshot(name: "Stale Mutation")
        let refreshed = Self.snapshot(name: "New Mutation")
        let mutationGate = SavedSearchViewSuspensionGate()
        let activationStartGate = SavedSearchViewSuspensionGate()
        let coordinator = SavedSearchViewCoordinatorFake(
            activationSnapshot: initial,
            createResults: [
                .committed(stale),
                .committed(refreshed),
            ],
            mutationGate: mutationGate
        )
        let owner = AtlasVaultSavedSearchPresentationOwner(
            coordinator: coordinator
        )
        let initialActivation = await owner.activatePrivateSession(
            selectedVault: Self.selectedVault
        )
        XCTAssertTrue(initialActivation)

        let oldMutation = Task { @MainActor in
            await owner.create(
                AtlasVaultSavedSearchDraft(
                    name: "Old mutation",
                    searchText: ""
                )
            )
        }
        await mutationGate.waitUntilEntered()
        owner.hidePrivatePresentation()
        XCTAssertEqual(owner.status, .hidden)
        XCTAssertTrue(owner.items.isEmpty)
        await mutationGate.waitUntilCancelled()

        let reactivation = Task { @MainActor in
            await activationStartGate.wait()
            return await owner.activatePrivateSession(
                selectedVault: Self.selectedVault
            )
        }
        await activationStartGate.waitUntilEntered()
        await activationStartGate.release()
        await Task.yield()

        XCTAssertEqual(
            owner.status,
            .hidden,
            "A new session must not become ready before stale mutation drain"
        )
        XCTAssertTrue(owner.items.isEmpty)

        await mutationGate.release()
        let reactivated = await reactivation.value
        XCTAssertTrue(reactivated)
        await oldMutation.value
        XCTAssertEqual(owner.status, .ready)
        XCTAssertEqual(owner.items, initial.searches)

        await owner.create(
            AtlasVaultSavedSearchDraft(
                name: "New mutation",
                searchText: ""
            )
        )
        XCTAssertEqual(owner.status, .ready)
        XCTAssertEqual(owner.items, refreshed.searches)
        let calls = await coordinator.calls()
        XCTAssertEqual(
            calls.filter { $0 == "create" }.count,
            2
        )
        XCTAssertEqual(
            calls.filter { $0 == "activate" }.count,
            2
        )
    }

    func testStopDuringCancelledMutationDrainPreventsReactivation()
        async
    {
        let initial = Self.snapshot(name: "Initial Session")
        let stale = Self.snapshot(name: "Stale Mutation")
        let mutationGate = SavedSearchViewSuspensionGate()
        let activationStartGate = SavedSearchViewSuspensionGate()
        let coordinator = SavedSearchViewCoordinatorFake(
            activationSnapshot: initial,
            createResults: [.committed(stale)],
            mutationGate: mutationGate
        )
        let owner = AtlasVaultSavedSearchPresentationOwner(
            coordinator: coordinator
        )
        let initialActivation = await owner.activatePrivateSession(
            selectedVault: Self.selectedVault
        )
        XCTAssertTrue(initialActivation)

        let oldMutation = Task { @MainActor in
            await owner.create(
                AtlasVaultSavedSearchDraft(
                    name: "Old mutation",
                    searchText: ""
                )
            )
        }
        await mutationGate.waitUntilEntered()
        owner.hidePrivatePresentation()
        await mutationGate.waitUntilCancelled()

        let reactivation = Task { @MainActor in
            await activationStartGate.wait()
            return await owner.activatePrivateSession(
                selectedVault: Self.selectedVault
            )
        }
        await activationStartGate.waitUntilEntered()
        await activationStartGate.release()
        await Task.yield()
        XCTAssertEqual(owner.status, .hidden)

        let stop = Task { @MainActor in
            await owner.stopAndDrainPrivateSession()
        }
        await Task.yield()
        await mutationGate.release()

        let reactivated = await reactivation.value
        XCTAssertFalse(reactivated)
        await oldMutation.value
        await stop.value
        XCTAssertEqual(owner.status, .hidden)
        XCTAssertTrue(owner.items.isEmpty)
        let calls = await coordinator.calls()
        XCTAssertEqual(
            calls.filter { $0 == "activate" }.count,
            1
        )
        XCTAssertEqual(calls.filter { $0 == "stop" }.count, 1)
    }

    func testActionsCarryOnlyDraftOpaqueIDAndLockCommands() async {
        let recorder = SavedSearchViewActionRecorder()
        let actions = AtlasVaultSavedSearchActions(
            create: { draft in
                await recorder.recordCreate(draft)
            },
            update: { identifier, draft in
                await recorder.recordUpdate(identifier, draft)
            },
            delete: { identifier in
                await recorder.recordDelete(identifier)
            },
            lock: {
                await recorder.recordLock()
            }
        )
        let presentation = Self.presentation(name: "Action")

        await actions.create(
            AtlasVaultSavedSearchDraft(
                name: "Action",
                searchText: "terms"
            )
        )
        await actions.update(
            presentation.id,
            draft: AtlasVaultSavedSearchDraft(
                name: "Renamed",
                searchText: "new terms"
            )
        )
        await actions.delete(presentation.id)
        await actions.lock()

        let counts = await recorder.counts()
        XCTAssertEqual(counts.creates, 1)
        XCTAssertEqual(counts.updates, 1)
        XCTAssertEqual(counts.deletes, 1)
        XCTAssertEqual(counts.locks, 1)
    }

    private static func snapshot(
        name: String
    ) -> AtlasVaultSavedSearchSnapshot {
        AtlasVaultSavedSearchSnapshot(
            searches: [presentation(name: name)]
        )
    }

    private static func presentation(
        name: String
    ) -> AtlasVaultSavedSearchPresentation {
        AtlasVaultSavedSearchPresentation(
            id: AtlasVaultPresentationID(
                recordID: name,
                generation: AtlasVaultPresentationGeneration()
            ),
            name: name,
            summary: "Summary",
            details: nil,
            request: AtlasVaultSavedSearchRequestPresentation(
                text: "terms",
                status: ["open"],
                organizations: [],
                sourceIDs: [],
                cities: [],
                countriesISO3: [],
                nationalInternational: [],
                gradeCodes: [],
                ccogFamilies: [],
                capabilityTags: [],
                contractGroups: [],
                seniorityGroups: [],
                workModalities: [],
                volunteerKinds: [],
                unvCategories: [],
                unvVolunteerTypes: [],
                closingDateTo: nil,
                includeLowConfidence: false,
                includeFacets: true,
                limit: 50,
                offset: 0,
                sort: "closing_date_asc"
            ),
            createdAt: "2026-07-27T00:00:00Z",
            updatedAt: "2026-07-27T00:00:00Z"
        )
    }

    private func requiredSource(named name: String) throws -> String {
        let url = Self.appleRoot()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: url.path),
            "Missing Phase 2D-63 source: \(name)"
        )
        guard FileManager.default.fileExists(atPath: url.path) else {
            return ""
        }
        return try String(contentsOf: url, encoding: .utf8)
    }

    private static func appleRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}

private actor SavedSearchViewCoordinatorFake:
    AtlasVaultSavedSearchCoordinating
{
    private let activationSnapshot: AtlasVaultSavedSearchSnapshot
    private let activationGate: SavedSearchViewSuspensionGate?
    private var createResults: [AtlasVaultSavedSearchMutationResult]
    private var updateResults: [AtlasVaultSavedSearchMutationResult]
    private let mutationGate: SavedSearchViewSuspensionGate?
    private var recordedCalls: [String] = []

    init(
        activationSnapshot: AtlasVaultSavedSearchSnapshot,
        activationGate: SavedSearchViewSuspensionGate? = nil,
        createResults: [AtlasVaultSavedSearchMutationResult] = [],
        updateResults: [AtlasVaultSavedSearchMutationResult] = [],
        mutationGate: SavedSearchViewSuspensionGate? = nil
    ) {
        self.activationSnapshot = activationSnapshot
        self.activationGate = activationGate
        self.createResults = createResults
        self.updateResults = updateResults
        self.mutationGate = mutationGate
    }

    func activate(
        selectedVault: String
    ) async throws -> AtlasVaultSavedSearchSnapshot {
        recordedCalls.append("activate")
        if let activationGate {
            await activationGate.wait()
        }
        return activationSnapshot
    }

    func create(
        _ draft: AtlasVaultSavedSearchDraft
    ) async throws -> AtlasVaultSavedSearchMutationResult {
        recordedCalls.append("create")
        if let mutationGate {
            await mutationGate.wait()
        }
        guard !createResults.isEmpty else {
            return .failed(activationSnapshot)
        }
        return createResults.removeFirst()
    }

    func delete(
        presentationID: AtlasVaultPresentationID
    ) async throws -> AtlasVaultSavedSearchMutationResult {
        recordedCalls.append("delete")
        return .failed(activationSnapshot)
    }

    func update(
        presentationID: AtlasVaultPresentationID,
        draft: AtlasVaultSavedSearchDraft
    ) async throws -> AtlasVaultSavedSearchMutationResult {
        recordedCalls.append("update")
        if let mutationGate {
            await mutationGate.wait()
        }
        guard !updateResults.isEmpty else {
            return .failed(activationSnapshot)
        }
        return updateResults.removeFirst()
    }

    func stop() {
        recordedCalls.append("stop")
    }

    func calls() -> [String] {
        recordedCalls
    }
}

private actor SavedSearchViewSuspensionGate {
    private var entered = false
    private var released = false
    private var cancelled = false
    private var entryWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseWaiters: [CheckedContinuation<Void, Never>] = []
    private var cancellationWaiters:
        [CheckedContinuation<Void, Never>] = []

    func wait() async {
        await withTaskCancellationHandler {
            await waitForRelease()
        } onCancel: {
            Task {
                await self.recordCancellation()
            }
        }
    }

    private func waitForRelease() async {
        entered = true
        let entryWaiters = entryWaiters
        self.entryWaiters.removeAll()
        for waiter in entryWaiters {
            waiter.resume()
        }
        guard !released else {
            return
        }
        await withCheckedContinuation { continuation in
            releaseWaiters.append(continuation)
        }
    }

    func waitUntilEntered() async {
        guard !entered else {
            return
        }
        await withCheckedContinuation { continuation in
            entryWaiters.append(continuation)
        }
    }

    func waitUntilCancelled() async {
        guard !cancelled else {
            return
        }
        await withCheckedContinuation { continuation in
            cancellationWaiters.append(continuation)
        }
    }

    func release() {
        released = true
        let releaseWaiters = releaseWaiters
        self.releaseWaiters.removeAll()
        for waiter in releaseWaiters {
            waiter.resume()
        }
    }

    private func recordCancellation() {
        guard !cancelled else {
            return
        }
        cancelled = true
        let cancellationWaiters = cancellationWaiters
        self.cancellationWaiters.removeAll()
        for waiter in cancellationWaiters {
            waiter.resume()
        }
    }
}

private actor SavedSearchViewActionRecorder {
    private var creates = 0
    private var updates = 0
    private var deletes = 0
    private var locks = 0

    func recordCreate(_ draft: AtlasVaultSavedSearchDraft) {
        creates += 1
    }

    func recordDelete(_ identifier: AtlasVaultPresentationID) {
        deletes += 1
    }

    func recordUpdate(
        _ identifier: AtlasVaultPresentationID,
        _ draft: AtlasVaultSavedSearchDraft
    ) {
        updates += 1
    }

    func recordLock() {
        locks += 1
    }

    func counts() -> (
        creates: Int,
        updates: Int,
        deletes: Int,
        locks: Int
    ) {
        (creates, updates, deletes, locks)
    }
}
