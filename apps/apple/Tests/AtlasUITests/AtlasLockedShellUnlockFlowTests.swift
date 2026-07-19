import Foundation
import XCTest
@testable import AtlasUI

final class AtlasLockedShellUnlockFlowTests: XCTestCase {
    private static let vaultID = "00000000-0000-4000-8000-000000000552"
    private static let fakeVaultKey = Data(repeating: 0x52, count: 32)

    func testInitialStateProjectsLockedPublicAndRetainsModel() {
        let publicShell = makePublicShell()
        let state = makeFlow(
            publicShell: publicShell,
            status: .locked,
            isUnlockPanelPresented: false
        )

        XCTAssertEqual(state.mode, .lockedPublic)
        XCTAssertEqual(state.publicShell, publicShell)
        XCTAssertNil(state.unlockPanelState)
    }

    func testPanelStatusesForwardExactUnlockViewState() {
        let statuses: [AtlasVaultUnlockPresentationStatus] = [
            .locked,
            .ready,
            .methodUnavailable,
            .activating,
            .failed,
            .timedOut,
            .hostReconciliationRequired,
        ]

        for status in statuses {
            let state = makeFlow(
                status: status,
                selectedMethod: status == .ready ? .localKey : nil,
                isUnlockPanelPresented: true
            )

            XCTAssertEqual(state.mode, .unlockPanel, "\(status)")
            XCTAssertEqual(state.unlockPanelState?.status, status)
            XCTAssertEqual(
                state.unlockPanelState?.availableMethods,
                [.localKey]
            )
        }
    }

    func testCancelledReturnsToLockedPublicPresentation() {
        let state = makeFlow(
            status: .cancelled,
            isUnlockPanelPresented: true
        )

        XCTAssertEqual(state.mode, .lockedPublic)
        XCTAssertNil(state.unlockPanelState)
    }

    func testUnlockedProjectsOnlyNonSensitiveTransition() {
        let state = makeFlow(
            status: .unlocked,
            selectedMethod: .localKey,
            isUnlockPanelPresented: true
        )

        XCTAssertEqual(state.mode, .unlockedTransition)
        XCTAssertNil(state.unlockPanelState)
        assertFlowHasNoSecretPrivateOrSaveMembers(state)
    }

    func testPanelNotRequestedRemainsLockedForNonTerminalStatus() {
        let state = makeFlow(
            status: .ready,
            selectedMethod: .localKey,
            isUnlockPanelPresented: false
        )

        XCTAssertEqual(state.mode, .lockedPublic)
        XCTAssertNil(state.unlockPanelState)
    }

    func testProductionCapabilitiesExposeLocalKeyOnly() {
        let state = makeFlow(
            status: .ready,
            selectedMethod: .localKey,
            isUnlockPanelPresented: true
        )
        let panel = state.unlockPanelState

        XCTAssertEqual(panel?.availableMethods, [.localKey])
        XCTAssertEqual(panel?.showsLocalKeyAction, true)
        XCTAssertEqual(panel?.showsPassphraseInput, false)
        XCTAssertEqual(panel?.showsRecoveryKeyInput, false)
    }

    func testFakeCapabilitiesAreForwardedWithoutFlowRecomputation() {
        let provider = FlowNeverCalledUnwrapper()
        let capabilities = AtlasVaultUnlockCapabilities(
            localKeyAvailable: true,
            passphraseProvider: provider,
            recoveryKeyProvider: provider
        )
        let state = makeFlow(
            capabilities: capabilities,
            status: .ready,
            selectedMethod: .passphrase,
            isUnlockPanelPresented: true
        )

        XCTAssertEqual(
            state.unlockPanelState?.availableMethods,
            [.localKey, .passphrase, .recoveryKey]
        )
        XCTAssertEqual(state.unlockPanelState?.selectedMethod, .passphrase)
        XCTAssertEqual(state.unlockPanelState?.showsPassphraseInput, true)
    }

    func testFlowStateHasFixedRedactedDescriptionsAndIsNotCodable() throws {
        let sentinel = "FAKE_FLOW_QUERY_NOT_FOR_DIAGNOSTICS"
        let state = makeFlow(
            publicShell: makePublicShell(searchQuery: sentinel),
            status: .ready,
            selectedMethod: .localKey,
            isUnlockPanelPresented: true
        )
        let rendered = [state.description, state.debugDescription]
            .joined(separator: "\n")
        let source = try source(named: "AtlasLockedShellUnlockFlowState.swift")

        XCTAssertFalse(rendered.contains(sentinel))
        XCTAssertTrue(rendered.contains("<redacted>"))
        XCTAssertFalse(source.contains("Codable"))
        assertFlowHasNoSecretPrivateOrSaveMembers(state)
    }

    @MainActor
    func testViewConstructionInvokesNoAction() async {
        let recorder = FlowActionRecorder()
        let publicActions = AtlasLockedPublicShellActions(
            search: { query in
                await recorder.recordSearch(query)
            },
            requestUnlock: {
                await recorder.recordUnlockRequest()
            }
        )
        let unlockActions = AtlasExplicitUnlockViewActions(
            select: { method in
                await recorder.recordSelection(method)
            },
            submit: { submission in
                await recorder.recordSubmission(submission)
                return .failed
            },
            cancel: {
                await recorder.recordCancel()
            },
            didDisappear: {
                await recorder.recordDisappearance()
            }
        )

        _ = AtlasLockedShellUnlockFlowView(
            state: makeFlow(
                status: .locked,
                isUnlockPanelPresented: false
            ),
            publicShellActions: publicActions,
            unlockActions: unlockActions
        )

        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.searches, [])
        XCTAssertEqual(snapshot.unlockRequests, 0)
        XCTAssertEqual(snapshot.selections, [])
        XCTAssertEqual(snapshot.submissions, 0)
        XCTAssertEqual(snapshot.cancellations, 0)
        XCTAssertEqual(snapshot.disappearances, 0)
    }

    func testViewSourceIsThinCompositionOfMergedViews() throws {
        let source = try source(named: "AtlasLockedShellUnlockFlowView.swift")

        XCTAssertTrue(source.contains("AtlasLockedPublicShellView("))
        XCTAssertTrue(source.contains("AtlasExplicitUnlockView("))
        XCTAssertTrue(source.contains("state.publicShell"))
        XCTAssertTrue(source.contains("state.unlockPanelState"))
        XCTAssertTrue(source.contains("publicShellActions"))
        XCTAssertTrue(source.contains("unlockActions"))
        XCTAssertTrue(source.contains("Unlock complete"))
    }

    func testViewSourceHasNoLegacyPrivateRuntimeOrEndpointAccess() throws {
        let source = try source(named: "AtlasLockedShellUnlockFlowView.swift")
        for forbidden in [
            "AtlasRootView",
            "refreshSidebarData",
            "SearchViewModel",
            "AtlasLocalCache",
            "AtlasAPIClient",
            "/api/saved-searches",
            "/api/tracker",
            "AtlasVaultHydratedState",
            "AtlasVaultPrivateState",
            "savedSearches",
            "savedJobs",
            "applicationNotes",
            "profileSnippets",
            "draftMetadata",
            "generatedDocument",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testProductionSourcesHaveNoForbiddenDependenciesOrSideEffects() throws {
        let source = try phaseSources()
        for forbidden in [
            "AtlasKeychain",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "CryptoKit",
            "AtlasVaultRecordCrypto",
            "FileManager",
            "Data.write",
            "URLSession",
            "@AppStorage",
            "@SceneStorage",
            "UserDefaults",
            "@main",
            "NavigationStack",
            "NavigationLink",
            "AtlasVaultSave",
            "SaveOutcome",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testPureFlowStateSourceDoesNotImportSwiftUIOrOwnServices() throws {
        let source = try source(named: "AtlasLockedShellUnlockFlowState.swift")
        for forbidden in [
            "import SwiftUI",
            "AtlasVaultTestHost",
            "AtlasVaultRuntimeFacade",
            "AtlasVaultUnlockPresentationController",
            "AtlasVaultKeyUnwrapping",
            "AtlasVaultSecretBuffer",
            "URL",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testRawTestKeyMethodIsAbsentFromPhaseSources() throws {
        let source = try phaseSources()

        XCTAssertFalse(source.contains("suppliedTestVaultKey"))
        XCTAssertFalse(source.contains("rawKey"))
    }

    func testExistingHostPublicSearchRemainsPublicOnlyWhileLocked() async throws {
        let harness = try await makeHarness()
        let constructionEndpointCounts = await harness.endpointCounts()
        let constructionPublicStoreCalls =
            await harness.publicStateStore.callCountsForTesting()
        let constructionAdapterCounts = await harness.unlockAdapter.snapshot()
        XCTAssertEqual(constructionEndpointCounts.publicSearch, 0)
        XCTAssertEqual(constructionEndpointCounts.savedSearch, 0)
        XCTAssertEqual(constructionEndpointCounts.tracker, 0)
        XCTAssertEqual(constructionEndpointCounts.privateSidebarRefresh, 0)
        XCTAssertEqual(constructionPublicStoreCalls.loads, 0)
        XCTAssertEqual(constructionPublicStoreCalls.replacements, 0)
        XCTAssertEqual(constructionAdapterCounts.dispatches, 0)
        XCTAssertEqual(constructionAdapterCounts.cancellations, 0)

        await harness.host.start()
        let initialRuntimeStatus = await harness.runtime.status()
        let initialSnapshot = await harness.host.latestPublishedSnapshot()
        let publicBefore = await harness.publicStateStore.snapshotForTesting()

        let jobs = try await harness.host.searchPublicJobs(
            query: "FAKE_PUBLIC_FLOW_QUERY"
        )

        let publicAfter = await harness.publicStateStore.snapshotForTesting()
        let publicStoreCalls =
            await harness.publicStateStore.callCountsForTesting()
        let endpointCounts = await harness.endpointCounts()
        XCTAssertEqual(initialRuntimeStatus, .locked)
        XCTAssertEqual(initialSnapshot.status, .locked)
        XCTAssertNil(initialSnapshot.privateState)
        XCTAssertEqual(
            jobs,
            [AtlasVaultTestPublicJob(
                identifier: "fake-flow-public-job",
                title: "Fake flow public job"
            )]
        )
        XCTAssertEqual(publicBefore, publicAfter)
        XCTAssertEqual(publicStoreCalls.loads, 1)
        XCTAssertEqual(publicStoreCalls.replacements, 0)
        XCTAssertEqual(endpointCounts.publicSearch, 1)
        XCTAssertEqual(endpointCounts.savedSearch, 0)
        XCTAssertEqual(endpointCounts.tracker, 0)
        XCTAssertEqual(endpointCounts.privateSidebarRefresh, 0)
        await harness.host.stop()
    }

    func testExplicitPanelRequestDoesNotActivateHost() async throws {
        let harness = try await makeHarness()
        let owner = FlowPresentationOwner()
        let actions = AtlasLockedPublicShellActions(
            search: { _ in },
            requestUnlock: {
                await owner.presentUnlockPanel()
            }
        )
        await harness.host.start()

        await actions.requestUnlock()

        let presented = await owner.isUnlockPanelPresented
        let runtimeStatus = await harness.runtime.status()
        let adapterCounts = await harness.unlockAdapter.snapshot()
        let controllerState = await harness.controller.currentState()
        let flow = AtlasLockedShellUnlockFlowState(
            publicShell: makePublicShell(),
            unlockPresentationState: controllerState,
            isUnlockPanelPresented: presented
        )
        XCTAssertTrue(presented)
        XCTAssertEqual(runtimeStatus, .locked)
        XCTAssertEqual(adapterCounts.dispatches, 0)
        XCTAssertEqual(flow.mode, .unlockPanel)
        await harness.host.stop()
    }

    func testExplicitLocalKeySubmissionUsesExistingHost() async throws {
        let harness = try await makeHarness()
        await harness.host.start()
        let selected = await harness.controller.select(.localKey)

        let result = await harness.controller.submit(.localKey)

        let adapterCounts = await harness.unlockAdapter.snapshot()
        let endpointCounts = await harness.endpointCounts()
        let runtimeStatus = await harness.runtime.status()
        let flow = AtlasLockedShellUnlockFlowState(
            publicShell: makePublicShell(),
            unlockPresentationState: result,
            isUnlockPanelPresented: true
        )
        XCTAssertEqual(selected.status, .ready)
        XCTAssertEqual(result.status, .unlocked)
        XCTAssertEqual(runtimeStatus, .unlocked)
        XCTAssertEqual(adapterCounts.dispatches, 1)
        XCTAssertEqual(flow.mode, .unlockedTransition)
        XCTAssertNil(flow.unlockPanelState)
        assertFlowHasNoSecretPrivateOrSaveMembers(flow)
        XCTAssertEqual(endpointCounts.savedSearch, 0)
        XCTAssertEqual(endpointCounts.tracker, 0)
        XCTAssertEqual(endpointCounts.privateSidebarRefresh, 0)
        await harness.host.stop()
    }

    func testCancellationReturnsToPublicLockedWithoutPrivateCalls() async throws {
        let harness = try await makeHarness()
        await harness.host.start()
        _ = await harness.controller.select(.localKey)

        let cancelled = await harness.controller.cancel()

        let flow = AtlasLockedShellUnlockFlowState(
            publicShell: makePublicShell(),
            unlockPresentationState: cancelled,
            isUnlockPanelPresented: true
        )
        let runtimeStatus = await harness.runtime.status()
        let endpointCounts = await harness.endpointCounts()
        let adapterCounts = await harness.unlockAdapter.snapshot()
        XCTAssertEqual(cancelled.status, .cancelled)
        XCTAssertEqual(flow.mode, .lockedPublic)
        XCTAssertEqual(runtimeStatus, .locked)
        XCTAssertEqual(adapterCounts.dispatches, 0)
        XCTAssertEqual(endpointCounts.savedSearch, 0)
        XCTAssertEqual(endpointCounts.tracker, 0)
        XCTAssertEqual(endpointCounts.privateSidebarRefresh, 0)
        await harness.host.stop()
    }

    func testExistingHostTypesRemainTheIntegrationAuthorities() throws {
        let existingHostSource = try String(
            contentsOf: testHostSourceURL(),
            encoding: .utf8
        )

        XCTAssertTrue(existingHostSource.contains("actor AtlasVaultTestHost:"))
        XCTAssertTrue(
            existingHostSource.contains(
                "actor AtlasVaultTestEndpointCallRecorder"
            )
        )
        XCTAssertTrue(
            existingHostSource.contains(
                "actor AtlasVaultFakePublicJobSearchService"
            )
        )
        XCTAssertFalse(
            try source(named: "AtlasLockedShellUnlockFlowView.swift")
                .contains("AtlasVaultTestHost")
        )
    }

    func testNoAtlasVaultOrReviewEnvironmentArtifactExists() throws {
        let root = repositoryRootURL()
        let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil
        )
        let urls = (enumerator?.allObjects as? [URL]) ?? []
        let vaultArtifacts = urls.filter { $0.pathExtension == "atlasvault" }
        let reviewEnvironments = urls.filter {
            $0.lastPathComponent == ".venv-review"
        }

        XCTAssertEqual(vaultArtifacts, [])
        XCTAssertEqual(reviewEnvironments, [])
    }

    func testPhaseSourceSetIsExactlyTheExpectedFourFiles() {
        let expected = Set([
            "AtlasLockedShellUnlockFlowState.swift",
            "AtlasLockedShellUnlockFlowView.swift",
            "AtlasLockedShellUnlockFlowTests.swift",
            "phase2d52_locked_shell_unlock_flow.md",
        ])
        let actual = Set([
            sourceDirectoryURL()
                .appendingPathComponent(
                    "AtlasLockedShellUnlockFlowState.swift"
                )
                .lastPathComponent,
            sourceDirectoryURL()
                .appendingPathComponent(
                    "AtlasLockedShellUnlockFlowView.swift"
                )
                .lastPathComponent,
            URL(fileURLWithPath: #filePath).lastPathComponent,
            "phase2d52_locked_shell_unlock_flow.md",
        ])

        XCTAssertEqual(actual, expected)
    }

    private func makeFlow(
        publicShell: AtlasLockedPublicShellModel? = nil,
        capabilities: AtlasVaultUnlockCapabilities = .currentProduction,
        status: AtlasVaultUnlockPresentationStatus,
        selectedMethod: AtlasVaultUnlockMethod? = nil,
        isUnlockPanelPresented: Bool
    ) -> AtlasLockedShellUnlockFlowState {
        AtlasLockedShellUnlockFlowState(
            publicShell: publicShell ?? makePublicShell(),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: capabilities,
                selectedMethod: selectedMethod,
                status: status
            ),
            isUnlockPanelPresented: isUnlockPanelPresented
        )
    }

    private func makePublicShell(
        searchQuery: String = ""
    ) -> AtlasLockedPublicShellModel {
        AtlasLockedPublicShellModel(
            vaultStatus: .locked,
            serviceStatus: .available,
            cacheFreshness: .current,
            searchQuery: searchQuery,
            publicJobs: [AtlasLockedPublicJob(
                id: "fake-flow-job",
                title: "Fake flow job",
                organization: "Fake organization",
                location: "Fake location"
            )]
        )
    }

    private func assertFlowHasNoSecretPrivateOrSaveMembers(
        _ state: AtlasLockedShellUnlockFlowState,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let members = Set(
            Mirror(reflecting: state).children.compactMap(\.label)
        )
        for forbidden in [
            "passphrase",
            "recoveryKey",
            "secret",
            "secretBuffer",
            "vaultKey",
            "privateState",
            "savedSearches",
            "savedJobs",
            "tracker",
            "notes",
            "snippets",
            "drafts",
            "recordCount",
            "saveOutcome",
            "host",
            "provider",
            "service",
            "fileURL",
        ] {
            XCTAssertFalse(
                members.contains(forbidden),
                forbidden,
                file: file,
                line: line
            )
        }
    }

    private func makeHarness() async throws -> FlowHarness {
        let recorder = AtlasVaultTestEndpointCallRecorder()
        let publicStateStore = AtlasVaultTestPublicStateStore(
            bytes: Data("FAKE_FLOW_PUBLIC_STATE".utf8)
        )
        let privateEndpoints =
            AtlasVaultTestPrivateCompatibilityEndpointSpy(recorder: recorder)
        let publicSearch = AtlasVaultFakePublicJobSearchService(
            recorder: recorder,
            publicStateStore: publicStateStore,
            results: [AtlasVaultTestPublicJob(
                identifier: "fake-flow-public-job",
                title: "Fake flow public job"
            )]
        )
        let runtime = AtlasVaultScriptedTestRuntime(
            activationState: AtlasVaultHydratedState()
        )
        let hostUnlockCoordinator = AtlasVaultUnlockRequestCoordinator(
            dependencies: AtlasVaultUnlockRequestDependencies(
                derivePassphraseVaultKey: { _ in Self.fakeVaultKey },
                deriveRecoveryVaultKey: { _ in Self.fakeVaultKey },
                activate: { request in
                    try await runtime.activate(request)
                }
            )
        )
        let time = AtlasVaultTestManualTime()
        let lifecycle = AtlasVaultLifecycleCoordinator(
            runtime: runtime,
            lockPolicy: .immediate,
            clock: time,
            sleeper: time
        )
        let rootURL =
            try AtlasVaultTestFileSystemSupport.canonicalTemporaryRoot()
            .appendingPathComponent(
                "atlas-phase2d52-\(UUID().uuidString)",
                isDirectory: true
            )
        let host = AtlasVaultTestHost(
            runtime: runtime,
            lifecycle: lifecycle,
            unlockCoordinator: hostUnlockCoordinator,
            publicSearch: publicSearch,
            environment: AtlasVaultTestHostEnvironment(
                temporaryRootURL: rootURL,
                keyStore: AtlasVaultTestFakeKeyStore(key: nil),
                publicStateStore: publicStateStore,
                privateCompatibilityEndpoints: privateEndpoints
            )
        )
        let adapter = FlowHostUnlockCoordinatorAdapter(host: host)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: .currentProduction,
            coordinator: adapter
        )
        return FlowHarness(
            recorder: recorder,
            publicStateStore: publicStateStore,
            publicSearch: publicSearch,
            runtime: runtime,
            host: host,
            unlockAdapter: adapter,
            controller: controller
        )
    }

    private func source(named filename: String) throws -> String {
        try String(
            contentsOf: sourceDirectoryURL()
                .appendingPathComponent(filename),
            encoding: .utf8
        )
    }

    private func phaseSources() throws -> String {
        try [
            "AtlasLockedShellUnlockFlowState.swift",
            "AtlasLockedShellUnlockFlowView.swift",
        ].map(source(named:)).joined(separator: "\n")
    }

    private func sourceDirectoryURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
    }

    private func repositoryRootURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private func testHostSourceURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("AtlasVaultTestHost.swift")
    }
}

private struct FlowHarness {
    let recorder: AtlasVaultTestEndpointCallRecorder
    let publicStateStore: AtlasVaultTestPublicStateStore
    let publicSearch: AtlasVaultFakePublicJobSearchService
    let runtime: AtlasVaultScriptedTestRuntime
    let host: AtlasVaultTestHost
    let unlockAdapter: FlowHostUnlockCoordinatorAdapter
    let controller: AtlasVaultUnlockPresentationController

    func endpointCounts() async -> (
        publicSearch: Int,
        savedSearch: Int,
        tracker: Int,
        privateSidebarRefresh: Int
    ) {
        (
            await recorder.count(.publicSearch),
            await recorder.count(.savedSearchCompatibility),
            await recorder.count(.trackerCompatibility),
            await recorder.count(.privateSidebarRefresh)
        )
    }
}

private actor FlowHostUnlockCoordinatorAdapter:
    AtlasVaultUnlockRequestCoordinating
{
    private let host: AtlasVaultTestHost
    private var dispatches = 0
    private var cancellations = 0

    init(host: AtlasVaultTestHost) {
        self.host = host
    }

    func dispatch(_ request: AtlasVaultUnlockRequest) async throws {
        dispatches += 1
        try await host.unlock(request)
    }

    func cancel(_ request: AtlasVaultUnlockRequest) async -> Bool {
        cancellations += 1
        await host.lock()
        return true
    }

    func snapshot() -> (dispatches: Int, cancellations: Int) {
        (dispatches, cancellations)
    }
}

private actor FlowPresentationOwner {
    private(set) var isUnlockPanelPresented = false

    func presentUnlockPanel() {
        isUnlockPanelPresented = true
    }
}

private actor FlowActionRecorder {
    private var searches: [String] = []
    private var unlockRequests = 0
    private var selections: [AtlasVaultUnlockMethod?] = []
    private var submissions = 0
    private var cancellations = 0
    private var disappearances = 0

    func recordSearch(_ query: String) {
        searches.append(query)
    }

    func recordUnlockRequest() {
        unlockRequests += 1
    }

    func recordSelection(_ method: AtlasVaultUnlockMethod?) {
        selections.append(method)
    }

    func recordSubmission(_ submission: AtlasVaultUnlockSubmission) {
        submissions += 1
    }

    func recordCancel() {
        cancellations += 1
    }

    func recordDisappearance() {
        disappearances += 1
    }

    func snapshot() -> (
        searches: [String],
        unlockRequests: Int,
        selections: [AtlasVaultUnlockMethod?],
        submissions: Int,
        cancellations: Int,
        disappearances: Int
    ) {
        (
            searches,
            unlockRequests,
            selections,
            submissions,
            cancellations,
            disappearances
        )
    }
}

private struct FlowNeverCalledUnwrapper: AtlasVaultKeyUnwrapping {
    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        XCTFail("Flow projection must not invoke a key provider")
        return Data()
    }
}
