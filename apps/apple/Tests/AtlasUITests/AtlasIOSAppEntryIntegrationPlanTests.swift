import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSAppEntryIntegrationPlanTests: XCTestCase {
    func testConstructionReadsSuppliedEnvironmentAndInvokesNoFactory() {
        let counter = EntryPlanFactoryCounter()
        let harness = makeHarness().harness
        let plan = AtlasIOSAppEntryIntegrationPlan(
            environment: [:],
            productionFactory: {
                counter.productionCalls += 1
                return harness
            }
        )

        XCTAssertEqual(plan.route, .production)
        XCTAssertEqual(counter.productionCalls, 0)
        XCTAssertTrue(plan.description.contains("<redacted>"))
    }

    func testEveryValidReferenceModeFailsClosedBeforeProduction() throws {
        for mode in AtlasReferenceCaptureMode.allCases {
            let counter = EntryPlanFactoryCounter()
            let harness = makeHarness().harness
            let plan = AtlasIOSAppEntryIntegrationPlan(
                environment: ["ATLAS_REFERENCE_CAPTURE": mode.rawValue],
                productionFactory: {
                    counter.productionCalls += 1
                    return harness
                }
            )

            XCTAssertEqual(plan.route, .referenceCapture(mode))
            XCTAssertNil(try plan.productionHarnessIfNeeded())
            XCTAssertEqual(counter.productionCalls, 0)
        }
    }

    func testEmptyAndUnknownReferenceValuesAreInvalidAndNeverProduction() throws {
        for rawValue in ["", "FAKE_UNKNOWN_CAPTURE"] {
            let counter = EntryPlanFactoryCounter()
            let harness = makeHarness().harness
            let plan = AtlasIOSAppEntryIntegrationPlan(
                environment: ["ATLAS_REFERENCE_CAPTURE": rawValue],
                productionFactory: {
                    counter.productionCalls += 1
                    return harness
                }
            )

            XCTAssertEqual(plan.route, .invalidReferenceCapture)
            XCTAssertNil(try plan.productionHarnessIfNeeded())
            XCTAssertEqual(counter.productionCalls, 0)
        }
    }

    func testProductionConstructionIsExplicitAndIdempotent() async throws {
        let counter = EntryPlanFactoryCounter()
        let fixture = makeHarness()
        let plan = AtlasIOSAppEntryIntegrationPlan(
            environment: [:],
            productionFactory: {
                counter.productionCalls += 1
                return fixture.harness
            }
        )

        XCTAssertEqual(counter.productionCalls, 0)
        let first = try XCTUnwrap(plan.productionHarnessIfNeeded())
        let second = try XCTUnwrap(plan.productionHarnessIfNeeded())

        XCTAssertTrue(first === second)
        XCTAssertTrue(first === fixture.harness)
        XCTAssertEqual(counter.productionCalls, 1)
        let startCount = await fixture.host.startCount()
        XCTAssertEqual(startCount, 0)
    }

    func testLifecycleAndCompositionFactoriesRunOnceOnlyForProduction()
        async throws
    {
        let counter = EntryPlanFactoryCounter()
        let fixture = makeHarness()
        let source = EntryPlanLifecycleSource()
        let plan = AtlasIOSAppEntryIntegrationPlan(
            environment: [:],
            lifecycleSourceFactory: {
                counter.sourceCalls += 1
                return source
            },
            compositionFactory: { suppliedSource in
                counter.compositionCalls += 1
                XCTAssertTrue((suppliedSource as AnyObject) === source)
                return fixture.harness
            }
        )

        XCTAssertEqual(counter.sourceCalls, 0)
        XCTAssertEqual(counter.compositionCalls, 0)
        _ = try plan.productionHarnessIfNeeded()
        _ = try plan.productionHarnessIfNeeded()

        XCTAssertEqual(counter.sourceCalls, 1)
        XCTAssertEqual(counter.compositionCalls, 1)
        let subscriptionCount = await source.subscriptionCount()
        let startCount = await fixture.host.startCount()
        XCTAssertEqual(subscriptionCount, 0)
        XCTAssertEqual(startCount, 0)
    }

    func testConstructionFailureIsRedactedAndNotRetried() {
        let counter = EntryPlanFactoryCounter()
        let plan = AtlasIOSAppEntryIntegrationPlan(
            environment: [:],
            productionFactory: {
                counter.productionCalls += 1
                throw EntryPlanFakeError.failure
            }
        )

        for _ in 0..<2 {
            XCTAssertThrowsError(try plan.productionHarnessIfNeeded()) { error in
                XCTAssertEqual(
                    error as? AtlasIOSAppEntryIntegrationError,
                    .productionUnavailable
                )
                XCTAssertFalse(String(describing: error).contains("failure"))
            }
        }
        XCTAssertEqual(counter.productionCalls, 1)
    }

    func testOnePlanCanShareOneHarnessAcrossFutureWindows() async throws {
        let fixture = makeHarness()
        let plan = AtlasIOSAppEntryIntegrationPlan(
            environment: [:],
            productionFactory: { fixture.harness }
        )
        let harness = try XCTUnwrap(plan.productionHarnessIfNeeded())

        _ = harness.makeRootView()
        _ = harness.makeRootView()

        XCTAssertTrue(harness.presentationOwner === fixture.owner)
        let startCount = await fixture.host.startCount()
        XCTAssertEqual(startCount, 0)
    }

    func testPlanSourceHasNoEnvironmentViewNavigationOrServiceLookup() throws {
        let source = try Self.source(
            at: "Sources/AtlasUI/AtlasIOSAppEntryIntegrationPlan.swift"
        )
        for forbidden in [
            "ProcessInfo", "AtlasReferenceCaptureView", "AtlasRootView",
            "@main", "WindowGroup", "NavigationStack", "NavigationLink",
            "Keychain", "SecItem", "FileManager", "URLSession",
            "UserDefaults", "AtlasVaultPrivateState", "passphrase",
            "recovery",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testCurrentAppEntryAndReferenceCaptureRemainUnmodified() throws {
        let appEntry = try Self.source(
            at: "AtlasIOSHost/AtlasIOSHost/AtlasIOSHostApp.swift"
        )
        XCTAssertTrue(appEntry.contains("ATLAS_REFERENCE_CAPTURE"))
        XCTAssertTrue(appEntry.contains("AtlasReferenceCaptureView"))
        XCTAssertTrue(appEntry.contains("AtlasRootView"))
        XCTAssertFalse(appEntry.contains("AtlasIOSProcessLifecycleEventSource"))
        XCTAssertFalse(appEntry.contains("AtlasIOSAppEntryIntegrationPlan"))
        XCTAssertFalse(appEntry.contains("AtlasVaultProductionCompositionHarness"))
        XCTAssertEqual(
            try Self.git(
                "rev-parse",
                "HEAD:apps/apple/AtlasIOSHost/AtlasIOSHost/AtlasIOSHostApp.swift"
            ),
            "f056a7b10a575e54f70622ca2f6aded60edaa335"
        )
        XCTAssertEqual(
            try Self.git(
                "diff",
                "--name-only",
                "origin/master",
                "--",
                "apps/apple/Sources/AtlasUI/AtlasReferenceCaptureView.swift"
            ),
            ""
        )
    }

    func testExactPhaseAllowlistAndNoArtifacts() throws {
        let expected: Set<String> = [
            "apps/apple/Sources/AtlasUI/AtlasIOSAppEntryIntegrationPlan.swift",
            "apps/apple/Sources/AtlasUI/AtlasIOSLifecycleAggregation.swift",
            "apps/apple/Sources/AtlasUI/AtlasIOSProcessLifecycleEventSource.swift",
            "apps/apple/Tests/AtlasUITests/AtlasIOSAppEntryIntegrationPlanTests.swift",
            "apps/apple/Tests/AtlasUITests/AtlasIOSLifecycleAggregationTests.swift",
            "apps/apple/Tests/AtlasUITests/AtlasIOSProcessLifecycleEventSourceTests.swift",
            "docs/architecture/phase2d58_ios_lifecycle_and_entry_integration.md",
        ]
        let committed = try Self.git(
            "diff", "--name-only", "origin/master...HEAD"
        )
        let status = try Self.git("status", "--porcelain=v1")
        let paths = Set(
            (committed.split(separator: "\n").map(String.init)
                + status.split(separator: "\n").map {
                    String($0.dropFirst(3))
                })
                .filter { !$0.isEmpty }
        )

        XCTAssertEqual(paths, expected)
        XCTAssertTrue(try Self.findArtifacts(named: ".atlasvault").isEmpty)
        XCTAssertTrue(try Self.findArtifacts(named: ".venv-review").isEmpty)
    }

    func testUnsupportedGitBackedAssertionsSkipInsteadOfReturningEmpty() throws {
        let source = try Self.source(
            at: "Tests/AtlasUITests/AtlasIOSAppEntryIntegrationPlanTests.swift"
        )
        let gitStart = try XCTUnwrap(
            source.range(
                of: "    private static func git(",
                options: .backwards
            )
        )
        let gitEnd = try XCTUnwrap(
            source.range(
                of: "    private static func appleRoot()",
                options: .backwards
            )
        )
        let gitHelper = String(
            source[gitStart.lowerBound..<gitEnd.lowerBound]
        )

        XCTAssertTrue(gitHelper.contains("throw XCTSkip("))
        XCTAssertFalse(gitHelper.contains("return \"\""))
    }

    private func makeHarness() -> (
        harness: AtlasVaultProductionCompositionHarness,
        owner: AtlasVaultProductionPresentationOwner,
        host: EntryPlanHostFake
    ) {
        let owner = AtlasVaultProductionPresentationOwner()
        let host = EntryPlanHostFake(flow: owner.flowState)
        let source = EntryPlanLifecycleSource()
        let forwarder = AtlasVaultProductionLifecycleForwarder(
            source: source,
            host: host
        )
        let harness = AtlasVaultProductionCompositionHarness(
            host: host,
            presentationOwner: owner,
            lifecycleForwarder: forwarder,
            publicSearchLimit: 1,
            unlockTimeout: .seconds(1)
        )
        return (harness, owner, host)
    }

    private static func source(at relativePath: String) throws -> String {
        try String(
            contentsOf: appleRoot().appendingPathComponent(relativePath),
            encoding: .utf8
        )
    }

    private static func findArtifacts(named name: String) throws -> [String] {
        try git("ls-files", "--others", "--exclude-standard")
            .split(separator: "\n")
            .map(String.init)
            .filter { $0.contains(name) }
    }

    private static func git(_ arguments: String...) throws -> String {
        #if os(macOS)
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = arguments
        process.currentDirectoryURL = repositoryRoot()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        process.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: .newlines)
        guard process.terminationStatus == 0 else {
            throw EntryPlanFakeError.command(output)
        }
        return output
        #else
        throw XCTSkip("Git-backed app-entry assertions require macOS")
        #endif
    }

    private static func appleRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private static func repositoryRoot() -> URL {
        appleRoot().deletingLastPathComponent().deletingLastPathComponent()
    }
}

@MainActor
private final class EntryPlanFactoryCounter {
    var productionCalls = 0
    var sourceCalls = 0
    var compositionCalls = 0
}

private enum EntryPlanFakeError: Error {
    case failure
    case command(String)
}

private actor EntryPlanLifecycleSource:
    AtlasVaultPlatformLifecycleEventSourcing
{
    private var subscriptions = 0

    func subscription() async
        -> AtlasVaultPlatformLifecycleEventSubscription
    {
        subscriptions += 1
        let pair =
            AsyncStream<AtlasVaultPlatformLifecycleEventDelivery>.makeStream(
                bufferingPolicy: .unbounded
            )
        return AtlasVaultPlatformLifecycleEventSubscription(
            bootstrapEvents: [],
            events: pair.stream,
            requestReadinessBoundary: { identifier in
                pair.continuation.yield(.readinessBoundary(identifier))
            }
        )
    }

    func subscriptionCount() -> Int {
        subscriptions
    }
}

private actor EntryPlanHostFake: AtlasVaultProductionHosting {
    private let flow: AtlasLockedShellUnlockFlowState
    private var starts = 0

    init(flow: AtlasLockedShellUnlockFlowState) {
        self.flow = flow
    }

    func start() async throws -> AtlasLockedShellUnlockFlowState {
        starts += 1
        return flow
    }

    func stop() async -> AtlasLockedShellUnlockFlowState { flow }
    func currentFlowState() async -> AtlasLockedShellUnlockFlowState { flow }

    func searchPublicJobs(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult {
        throw .unavailable
    }

    func requestUnlockPanel() async -> AtlasLockedShellUnlockFlowState { flow }

    func selectUnlockMethod(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasLockedShellUnlockFlowState { flow }

    func submitUnlock(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration?
    ) async -> AtlasLockedShellUnlockFlowState { flow }

    func cancelUnlock() async -> AtlasLockedShellUnlockFlowState { flow }
    func unlockPanelDidDisappear() async -> AtlasLockedShellUnlockFlowState { flow }
    func lock() async -> AtlasLockedShellUnlockFlowState { flow }

    func handleLifecycleEvent(
        _ event: AtlasVaultLifecycleEvent
    ) async -> AtlasLockedShellUnlockFlowState { flow }

    func startCount() -> Int { starts }
}
