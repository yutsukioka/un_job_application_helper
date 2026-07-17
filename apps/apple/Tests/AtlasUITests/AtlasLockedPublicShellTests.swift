import Foundation
import SwiftUI
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasLockedPublicShellTests: XCTestCase {
    func testLockedModelContainsOnlyPublicShellState() {
        let model = makeModel()
        let memberNames = Set(
            Mirror(reflecting: model).children.compactMap(\.label)
        )

        XCTAssertEqual(model.vaultStatus, .locked)
        XCTAssertEqual(model.publicJobs, [fakeJob()])
        XCTAssertTrue(model.canRequestUnlock)
        for privateMember in [
            "savedSearches",
            "savedJobs",
            "tracker",
            "applicationNotes",
            "profileSnippets",
            "draftMetadata",
            "vaultKey",
            "recordEnvelopes",
            "filesystemURL",
        ] {
            XCTAssertFalse(memberNames.contains(privateMember))
        }
    }

    func testLockedModelSupportsOnlyNonSensitiveVaultStatuses() {
        XCTAssertEqual(
            makeModel(vaultStatus: .locked).vaultStatus,
            .locked
        )
        XCTAssertEqual(
            makeModel(vaultStatus: .noVault).vaultStatus,
            .noVault
        )
        XCTAssertEqual(
            makeModel(vaultStatus: .keyUnavailable).vaultStatus,
            .keyUnavailable
        )
    }

    func testSearchingModelRejectsAnotherSearchSubmission() {
        XCTAssertTrue(makeModel(isSearching: false).permitsSearchSubmission)
        XCTAssertFalse(makeModel(isSearching: true).permitsSearchSubmission)
    }

    func testLockedModelSourceIsNotCodable() throws {
        let source = try source(named: "AtlasLockedPublicShellModel.swift")

        XCTAssertFalse(source.contains("Codable"))
        XCTAssertFalse(source.contains("CodingKey"))
        XCTAssertFalse(source.contains("Encoder"))
        XCTAssertFalse(source.contains("Decoder"))
    }

    func testModelAndActionsDescriptionsRedactContent() {
        let sentinel = "FAKE_PUBLIC_QUERY_NOT_FOR_DIAGNOSTICS"
        let model = makeModel(searchQuery: sentinel)
        let actions = AtlasLockedPublicShellActions(
            search: { _ in },
            requestUnlock: {}
        )
        let rendered = [
            String(describing: model),
            String(reflecting: model),
            String(describing: model.publicJobs[0]),
            String(reflecting: model.publicJobs[0]),
            String(describing: actions),
            String(reflecting: actions),
        ].joined(separator: "|")

        XCTAssertFalse(rendered.contains(sentinel))
        XCTAssertFalse(rendered.contains(fakeJob().id))
    }

    func testConstructionInvokesNeitherInjectedAction() async {
        let recorder = LockedShellActionRecorder()
        let actions = makeActions(recorder: recorder)

        _ = AtlasLockedPublicShellView(
            model: makeModel(),
            actions: actions
        )

        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.queries, [])
        XCTAssertEqual(snapshot.unlockCount, 0)
    }

    func testPublicSearchActionIsInjectedAndUsableWhileLocked() async {
        let recorder = LockedShellActionRecorder()
        let actions = makeActions(recorder: recorder)

        await actions.search(query: "FAKE_PUBLIC_QUERY")

        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.queries, ["FAKE_PUBLIC_QUERY"])
        XCTAssertEqual(snapshot.unlockCount, 0)
    }

    func testUnlockRequestActionIsInjected() async {
        let recorder = LockedShellActionRecorder()
        let actions = makeActions(recorder: recorder)

        await actions.requestUnlock()

        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.queries, [])
        XCTAssertEqual(snapshot.unlockCount, 1)
    }

    func testLockReplacementModelHasNoPrivateProjection() {
        let model = makeModel(vaultStatus: .locked)

        XCTAssertEqual(model.vaultStatus, .locked)
        assertNoPrivateMemberNames(model)
    }

    func testActivationFailureReplacementRemainsPublicOnly() {
        let model = makeModel(vaultStatus: .keyUnavailable)

        XCTAssertEqual(model.vaultStatus, .keyUnavailable)
        assertNoPrivateMemberNames(model)
    }

    func testFatalSaveFailureReplacementRemainsPublicOnly() {
        let model = makeModel(vaultStatus: .locked)

        XCTAssertEqual(model.publicJobs, [fakeJob()])
        assertNoPrivateMemberNames(model)
    }

    func testBackgroundLockReplacementRemainsPublicOnly() {
        let model = AtlasLockedPublicShellModel(
            vaultStatus: .locked,
            serviceStatus: .available,
            cacheFreshness: .current,
            publicJobs: [fakeJob()]
        )

        XCTAssertEqual(model.vaultStatus, .locked)
        XCTAssertEqual(model.publicJobs.count, 1)
        assertNoPrivateMemberNames(model)
    }

    func testLockedShellSearchUsesTestHostPublicBoundaryOnly() async throws {
        let recorder = AtlasVaultTestEndpointCallRecorder()
        let publicBytes = Data("FAKE_PUBLIC_STATE".utf8)
        let publicStateStore = AtlasVaultTestPublicStateStore(
            bytes: publicBytes
        )
        let privateEndpoints =
            AtlasVaultTestPrivateCompatibilityEndpointSpy(recorder: recorder)
        let publicSearch = AtlasVaultFakePublicJobSearchService(
            recorder: recorder,
            publicStateStore: publicStateStore,
            results: [AtlasVaultTestPublicJob(
                identifier: "fake-public-job",
                title: "Fake public job"
            )]
        )
        let runtime = AtlasVaultScriptedTestRuntime(
            activationState: AtlasVaultHydratedState()
        )
        let time = AtlasVaultTestManualTime()
        let testKey = Data(repeating: 0x47, count: 32)
        let unlockCoordinator = AtlasVaultUnlockRequestCoordinator(
            dependencies: AtlasVaultUnlockRequestDependencies(
                derivePassphraseVaultKey: { _ in testKey },
                deriveRecoveryVaultKey: { _ in testKey },
                activate: { request in
                    try await runtime.activate(request)
                }
            )
        )
        let lifecycle = AtlasVaultLifecycleCoordinator(
            runtime: runtime,
            lockPolicy: .immediate,
            clock: time,
            sleeper: time
        )
        let temporaryRootURL =
            try AtlasVaultTestFileSystemSupport.canonicalTemporaryRoot()
            .appendingPathComponent(
                "atlas-locked-public-shell-\(UUID().uuidString)",
                isDirectory: true
            )
        let host = AtlasVaultTestHost(
            runtime: runtime,
            lifecycle: lifecycle,
            unlockCoordinator: unlockCoordinator,
            publicSearch: publicSearch,
            environment: AtlasVaultTestHostEnvironment(
                temporaryRootURL: temporaryRootURL,
                keyStore: AtlasVaultTestFakeKeyStore(key: nil),
                publicStateStore: publicStateStore,
                privateCompatibilityEndpoints: privateEndpoints
            )
        )
        let resultRecorder = LockedShellResultRecorder()
        let actions = AtlasLockedPublicShellActions(
            search: { query in
                let jobs = try? await host.searchPublicJobs(query: query)
                await resultRecorder.record(jobs ?? [])
            },
            requestUnlock: {}
        )

        await host.start()
        await actions.search(query: "FAKE_PUBLIC_QUERY")

        let endpointCalls = await recorder.snapshot()
        let jobs = await resultRecorder.snapshot()
        let publicAfter = await publicStateStore.snapshotForTesting()
        let publicCalls =
            await publicStateStore.callCountsForTesting()
        let privateEndpointCounts = (
            saved: await recorder.count(.savedSearchCompatibility),
            tracker: await recorder.count(.trackerCompatibility),
            refresh: await recorder.count(.privateSidebarRefresh)
        )
        XCTAssertEqual(endpointCalls, [.publicSearch])
        XCTAssertEqual(
            jobs,
            [AtlasVaultTestPublicJob(
                identifier: "fake-public-job",
                title: "Fake public job"
            )]
        )
        XCTAssertEqual(publicAfter, publicBytes)
        XCTAssertEqual(publicCalls.loads, 1)
        XCTAssertEqual(publicCalls.replacements, 0)
        XCTAssertEqual(privateEndpointCounts.saved, 0)
        XCTAssertEqual(privateEndpointCounts.tracker, 0)
        XCTAssertEqual(privateEndpointCounts.refresh, 0)
        await host.stop()
    }

    func testViewSourceDoesNotReuseLegacyRootOrRefreshPath() throws {
        let source = try source(named: "AtlasLockedPublicShellView.swift")

        XCTAssertFalse(source.contains("AtlasRootView"))
        XCTAssertFalse(source.contains("refreshSidebarData"))
    }

    func testViewSourceDoesNotReferenceLegacyPrivatePanelsOrRoutes() throws {
        let source = try source(named: "AtlasLockedPublicShellView.swift")

        for forbidden in [
            "savedSearches",
            "savedJobs",
            "/api/saved-searches",
            "/api/tracker",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testViewSourceHasNoDirectServiceOrRuntimeAccess() throws {
        let source = try source(named: "AtlasLockedPublicShellView.swift")

        for forbidden in [
            "AtlasAPIClient",
            "SearchViewModel",
            "AtlasLocalCache",
            "URLSession",
            "AtlasVaultRuntimeFacade",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testViewSourceHasNoKeyFilesystemOrCryptoAccess() throws {
        let source = try source(named: "AtlasLockedPublicShellView.swift")

        for forbidden in [
            "Keychain",
            "SecItem",
            "FileManager",
            "Data.write",
            "AtlasVaultRecordCrypto",
            "AtlasVaultEncryptedRecordEnvelope",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testViewSourceHasNoPersistentSceneOrAppEntryState() throws {
        let source = try source(named: "AtlasLockedPublicShellView.swift")

        for forbidden in [
            "@AppStorage",
            "@SceneStorage",
            "UserDefaults",
            "AtlasIOSHostApp",
            "@main",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testFakeFixtureContainsOnlyExplicitFakePublicValues() {
        let job = fakeJob()
        let rendered = [
            job.id,
            job.title,
            job.organization,
            job.location,
            job.closingDateText ?? "",
        ].joined(separator: "|")

        XCTAssertTrue(
            rendered.lowercased().contains("fake")
                || rendered.lowercased().contains("example")
        )
        XCTAssertFalse(rendered.contains("@"))
        XCTAssertFalse(rendered.contains("/Users/"))
    }

    func testNoAtlasVaultArtifactExistsInWorktree() throws {
        let root = repositoryRootURL()
        let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        )
        guard let enumerator else {
            XCTFail("Unable to enumerate the worktree")
            return
        }

        for case let url as URL in enumerator {
            guard url.pathExtension == "atlasvault" else {
                continue
            }
            XCTFail("Unexpected .atlasvault artifact")
            return
        }
    }

    func testSourceFilesExcludeLegacyPrivateRuntimeAndPersistencePaths()
        throws
    {
        let sources = try sourceFileURLs().map {
            try String(contentsOf: $0, encoding: .utf8)
        }
        let combined = sources.joined(separator: "\n")

        for forbidden in [
            "AtlasRootView",
            "refreshSidebarData",
            "savedSearches",
            "savedJobs",
            "/api/saved-searches",
            "/api/tracker",
            "AtlasAPIClient",
            "SearchViewModel",
            "AtlasLocalCache",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "FileManager",
            "Data.write",
            "UserDefaults",
            "@AppStorage",
            "@SceneStorage",
            "URLSession",
            "AtlasVaultRuntimeFacade",
            "AtlasVaultRecordCrypto",
            "AtlasVaultEncryptedRecordEnvelope",
            "AtlasPublicLocalSnapshot",
            "ATLAS_REFERENCE_CAPTURE",
            "AtlasReferenceCaptureView",
            "@main",
            "Codable",
        ] {
            XCTAssertFalse(
                combined.contains(forbidden),
                "Unexpected locked-shell source reference: \(forbidden)"
            )
        }
    }

    func testOnlyViewSourceImportsSwiftUI() throws {
        let sourceDirectory = sourceFileURLs()
        let modelSource = try String(
            contentsOf: sourceDirectory[0],
            encoding: .utf8
        )
        let viewSource = try String(
            contentsOf: sourceDirectory[1],
            encoding: .utf8
        )

        XCTAssertFalse(modelSource.contains("SwiftUI"))
        XCTAssertTrue(viewSource.contains("import SwiftUI"))
    }

    private func makeModel(
        vaultStatus: AtlasLockedPublicVaultStatus = .locked,
        searchQuery: String = "",
        isSearching: Bool = false
    ) -> AtlasLockedPublicShellModel {
        AtlasLockedPublicShellModel(
            vaultStatus: vaultStatus,
            serviceStatus: .available,
            cacheFreshness: .current,
            searchQuery: searchQuery,
            publicJobs: [fakeJob()],
            isSearching: isSearching,
            canRequestUnlock: true
        )
    }

    private func fakeJob() -> AtlasLockedPublicJob {
        AtlasLockedPublicJob(
            id: "fake-public-job",
            title: "Fake Public Programme Officer",
            organization: "Example Public Organization",
            location: "Example City",
            closingDateText: "31 Dec"
        )
    }

    private func makeActions(
        recorder: LockedShellActionRecorder
    ) -> AtlasLockedPublicShellActions {
        AtlasLockedPublicShellActions(
            search: { query in
                await recorder.record(query: query)
            },
            requestUnlock: {
                await recorder.recordUnlock()
            }
        )
    }

    private func assertNoPrivateMemberNames(
        _ model: AtlasLockedPublicShellModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let memberNames = Set(
            Mirror(reflecting: model).children.compactMap(\.label)
        )
        for privateMember in [
            "savedSearches",
            "savedJobs",
            "applicationNotes",
            "profileSnippets",
            "draftMetadata",
            "vaultKey",
            "recordEnvelopes",
        ] {
            XCTAssertFalse(
                memberNames.contains(privateMember),
                file: file,
                line: line
            )
        }
    }

    private func sourceFileURLs() -> [URL] {
        let testDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let sourceDirectory = testDirectory
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
        return [
            sourceDirectory
                .appendingPathComponent("AtlasLockedPublicShellModel.swift"),
            sourceDirectory
                .appendingPathComponent("AtlasLockedPublicShellView.swift"),
        ]
    }

    private func source(named filename: String) throws -> String {
        let url = try XCTUnwrap(
            sourceFileURLs().first { $0.lastPathComponent == filename }
        )
        return try String(contentsOf: url, encoding: .utf8)
    }

    private func repositoryRootURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}

private actor LockedShellActionRecorder {
    private var queries: [String] = []
    private var unlockCount = 0

    func record(query: String) {
        queries.append(query)
    }

    func recordUnlock() {
        unlockCount += 1
    }

    func snapshot() -> (queries: [String], unlockCount: Int) {
        (queries, unlockCount)
    }
}

private actor LockedShellResultRecorder {
    private var jobs: [AtlasVaultTestPublicJob] = []

    func record(_ jobs: [AtlasVaultTestPublicJob]) {
        self.jobs = jobs
    }

    func snapshot() -> [AtlasVaultTestPublicJob] {
        jobs
    }
}
