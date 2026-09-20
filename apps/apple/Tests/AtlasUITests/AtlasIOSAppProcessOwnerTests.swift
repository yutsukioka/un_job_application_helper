import Combine
import Foundation
import SwiftUI
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSAppProcessOwnerTests: XCTestCase {
    private static let fakeEnvironmentValue =
        "FAKE_PHASE_2D59_ENVIRONMENT_VALUE"
    private static let fakeErrorDetail =
        "FAKE_PHASE_2D59_ERROR_DETAIL"

    func testConstructionPublishesRoutesWithoutStartingProduction() {
        requireObservableObject(AtlasIOSAppProcessOwner.self)

        for (route, expected) in [
            (
                AtlasIOSAppEntryRoute.referenceCapture(.search),
                AtlasIOSAppProcessPresentation.referenceCapture(.search)
            ),
            (
                .invalidReferenceCapture,
                .invalidReferenceCapture
            ),
            (
                .production,
                .productionPending
            ),
        ] {
            let probe = ProcessFactoryProbe(
                harness: ProcessHarnessFake()
            )
            let owner = makeOwner(route: route, probe: probe)

            XCTAssertEqual(owner.presentation, expected)
            XCTAssertEqual(probe.calls, 0)
            XCTAssertNil(owner.productionRootView())
        }
    }

    func testReferenceAndInvalidRoutesNeverConstructProduction() async {
        for (route, expected) in [
            (
                AtlasIOSAppEntryRoute.referenceCapture(.detail),
                AtlasIOSAppProcessPresentation.referenceCapture(.detail)
            ),
            (
                .invalidReferenceCapture,
                .invalidReferenceCapture
            ),
        ] {
            let probe = ProcessFactoryProbe(
                error: ProcessOwnerTestError.production(
                    Self.fakeErrorDetail
                )
            )
            let owner = makeOwner(route: route, probe: probe)

            owner.beginStart()
            expectPresentation(await owner.start(), expected)
            XCTAssertEqual(probe.calls, 0)
            XCTAssertNil(owner.productionRootView())

            expectPresentation(await owner.stop(), .stopped)
            XCTAssertEqual(probe.calls, 0)
        }
    }

    func testProductionStartIsLazyRetainedAndReadyOnlyAfterHarnessStart()
        async
    {
        let startGate = ProcessSuspensionGate()
        let harness = ProcessHarnessFake(startGate: startGate)
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)

        XCTAssertEqual(owner.presentation, .productionPending)
        XCTAssertEqual(probe.calls, 0)
        XCTAssertEqual(harness.startCalls, 0)

        owner.beginStart()
        await startGate.waitUntilEntered()

        XCTAssertEqual(owner.presentation, .productionStarting)
        XCTAssertEqual(probe.calls, 1)
        XCTAssertEqual(harness.startCalls, 1)
        XCTAssertNil(owner.productionRootView())

        await startGate.release()
        expectPresentation(await owner.start(), .productionReady)
        XCTAssertEqual(owner.presentation, .productionReady)
        XCTAssertNotNil(owner.productionRootView())
        XCTAssertEqual(harness.makeRootCalls, 1)

        expectPresentation(await owner.start(), .productionReady)
        XCTAssertEqual(probe.calls, 1)
        XCTAssertEqual(harness.startCalls, 1)
    }

    func testConcurrentStartCallersShareOneOperation() async {
        let startGate = ProcessSuspensionGate()
        let harness = ProcessHarnessFake(startGate: startGate)
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)

        let first = Task { await owner.start() }
        let second = Task { await owner.start() }
        await startGate.waitUntilEntered()

        XCTAssertEqual(probe.calls, 1)
        XCTAssertEqual(harness.startCalls, 1)

        await startGate.release()
        expectPresentation(await first.value, .productionReady)
        expectPresentation(await second.value, .productionReady)
        XCTAssertEqual(probe.calls, 1)
        XCTAssertEqual(harness.startCalls, 1)
    }

    func testCallerCancellationDoesNotOrphanRetainedStartup() async {
        let startGate = ProcessSuspensionGate()
        let harness = ProcessHarnessFake(startGate: startGate)
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)

        let caller = Task { await owner.start() }
        await startGate.waitUntilEntered()
        caller.cancel()
        await startGate.release()
        _ = await caller.value

        expectPresentation(await owner.start(), .productionReady)
        XCTAssertEqual(probe.calls, 1)
        XCTAssertEqual(harness.startCalls, 1)
    }

    func testProductionFactoryClosureIsReleasedAfterHarnessTransfer() async {
        let harness = ProcessHarnessFake()
        var token: ProcessFactoryLifetimeToken? =
            ProcessFactoryLifetimeToken()
        weak var weakToken = token
        let owner = AtlasIOSAppProcessOwner(
            route: .production,
            productionFactory: { [capturedToken = token!] in
                _ = capturedToken
                return harness
            }
        )
        token = nil

        XCTAssertNotNil(weakToken)
        expectPresentation(await owner.start(), .productionReady)
        XCTAssertNil(weakToken)
    }

    func testRetainedRoutePlanCannotRetainTransferredProcessHarness() async {
        var planFactoryCalls = 0
        let plan = AtlasIOSAppEntryIntegrationPlan(
            environment: [:],
            productionFactory: {
                planFactoryCalls += 1
                throw ProcessOwnerTestError.production(
                    "route plan factory must remain unused"
                )
            }
        )
        var offeredHarness: ProcessHarnessFake? = ProcessHarnessFake()
        weak var weakHarness = offeredHarness
        let owner = AtlasIOSAppProcessOwner(
            plan: plan,
            productionFactory: {
                guard let harness = offeredHarness else {
                    throw ProcessOwnerTestError.production(
                        "harness already transferred"
                    )
                }
                offeredHarness = nil
                return harness
            }
        )

        expectPresentation(await owner.start(), .productionReady)
        XCTAssertEqual(planFactoryCalls, 0)
        XCTAssertNotNil(weakHarness)

        expectPresentation(await owner.stop(), .stopped)
        XCTAssertEqual(planFactoryCalls, 0)
        XCTAssertNil(weakHarness)
        withExtendedLifetime(plan) {}
    }

    func testStopBeforeStartReleasesUnusedProductionFactory() async {
        var token: ProcessFactoryLifetimeToken? =
            ProcessFactoryLifetimeToken()
        weak var weakToken = token
        let owner = AtlasIOSAppProcessOwner(
            route: .production,
            productionFactory: { [capturedToken = token!] in
                _ = capturedToken
                return ProcessHarnessFake()
            }
        )
        token = nil

        XCTAssertNotNil(weakToken)
        expectPresentation(await owner.stop(), .stopped)
        XCTAssertNil(weakToken)
    }

    func testFactoryFailureIsRedactedUnavailableAndIsNotRetried() async {
        let probe = ProcessFactoryProbe(
            error: ProcessOwnerTestError.production(Self.fakeErrorDetail)
        )
        let owner = makeOwner(route: .production, probe: probe)

        expectPresentation(await owner.start(), .productionUnavailable)
        expectPresentation(await owner.start(), .productionUnavailable)
        XCTAssertEqual(probe.calls, 1)
        XCTAssertNil(owner.productionRootView())
        XCTAssertFalse(owner.description.contains(Self.fakeErrorDetail))
        XCTAssertFalse(
            owner.presentation.description.contains(Self.fakeErrorDetail)
        )
    }

    func testHarnessStartFailureIsUnavailableAndIsNotRetried() async {
        let harness = ProcessHarnessFake(startFails: true)
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)

        expectPresentation(await owner.start(), .productionUnavailable)
        expectPresentation(await owner.start(), .productionUnavailable)
        XCTAssertEqual(probe.calls, 1)
        XCTAssertEqual(harness.startCalls, 1)
        XCTAssertNil(owner.productionRootView())

        expectPresentation(await owner.stop(), .stopped)
        XCTAssertEqual(harness.stopCalls, 1)
    }

    func testTerminalStopReleasesHarnessAfterTeardown() async {
        var offeredHarness: ProcessHarnessFake? = ProcessHarnessFake()
        weak var weakHarness = offeredHarness
        let owner = AtlasIOSAppProcessOwner(
            route: .production,
            productionFactory: {
                guard let harness = offeredHarness else {
                    throw ProcessOwnerTestError.production(
                        "harness already transferred"
                    )
                }
                offeredHarness = nil
                return harness
            }
        )

        expectPresentation(await owner.start(), .productionReady)
        XCTAssertNotNil(weakHarness)

        expectPresentation(await owner.stop(), .stopped)
        XCTAssertNil(weakHarness)
    }

    func testStopBeforeStartConstructsNothingAndRejectsRestart() async {
        let harness = ProcessHarnessFake()
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)

        expectPresentation(await owner.stop(), .stopped)
        XCTAssertEqual(probe.calls, 0)
        XCTAssertEqual(harness.stopCalls, 0)
        expectPresentation(await owner.start(), .stopped)
        XCTAssertEqual(probe.calls, 0)
    }

    func testSynchronousStopAfterBeginStartWinsBeforeFactoryRuns() async {
        let harness = ProcessHarnessFake()
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)

        owner.beginStart()
        owner.beginTerminalStop()

        expectPresentation(await owner.stop(), .stopped)
        expectPresentation(await owner.start(), .stopped)
        XCTAssertEqual(probe.calls, 0)
        XCTAssertEqual(harness.startCalls, 0)
        XCTAssertEqual(harness.stopCalls, 0)
    }

    func testStopDuringHarnessStartDrainsAndPreventsLateReady() async {
        let startGate = ProcessSuspensionGate()
        let harness = ProcessHarnessFake(
            startGate: startGate,
            stopReleasesStart: true
        )
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)

        let startCaller = Task { await owner.start() }
        await startGate.waitUntilEntered()
        XCTAssertEqual(owner.presentation, .productionStarting)

        owner.beginTerminalStop()
        XCTAssertEqual(owner.presentation, .productionStopping)
        expectPresentation(await owner.stop(), .stopped)
        _ = await startCaller.value

        XCTAssertEqual(owner.presentation, .stopped)
        XCTAssertEqual(harness.stopCalls, 1)
        expectPresentation(await owner.start(), .stopped)
        XCTAssertNil(owner.productionRootView())
    }

    func testConcurrentStopCallersShareOneRetainedOperation() async {
        let stopGate = ProcessSuspensionGate()
        let harness = ProcessHarnessFake(stopGate: stopGate)
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)
        expectPresentation(await owner.start(), .productionReady)

        owner.beginTerminalStop()
        let first = Task { await owner.stop() }
        let second = Task { await owner.stop() }
        await stopGate.waitUntilEntered()
        XCTAssertEqual(harness.stopCalls, 1)

        await stopGate.release()
        expectPresentation(await first.value, .stopped)
        expectPresentation(await second.value, .stopped)
        expectPresentation(await owner.stop(), .stopped)
        XCTAssertEqual(harness.stopCalls, 1)
    }

    func testCallerCancellationDoesNotOrphanRetainedStop() async {
        let stopGate = ProcessSuspensionGate()
        let harness = ProcessHarnessFake(stopGate: stopGate)
        let probe = ProcessFactoryProbe(harness: harness)
        let owner = makeOwner(route: .production, probe: probe)
        expectPresentation(await owner.start(), .productionReady)

        let caller = Task { await owner.stop() }
        await stopGate.waitUntilEntered()
        caller.cancel()
        await stopGate.release()
        _ = await caller.value

        expectPresentation(await owner.stop(), .stopped)
        XCTAssertEqual(harness.stopCalls, 1)
    }

    func testProductionConfigurationUsesExplicitReviewedPolicy() throws {
        let defaultConfiguration = try AtlasIOSAppProcessOwner
            .productionConfiguration(environment: [:])
        XCTAssertEqual(
            defaultConfiguration.apiBaseURL,
            URL(string: "http://127.0.0.1:8765")
        )
        XCTAssertEqual(defaultConfiguration.publicSearchLimit, 50)
        XCTAssertEqual(
            defaultConfiguration.unlockTimeout,
            .seconds(30)
        )
        XCTAssertEqual(
            defaultConfiguration.lifecycleLockPolicy,
            .immediate
        )
        XCTAssertTrue(defaultConfiguration.lockOnInactive)

        for rawURL in [
            "http://example.invalid",
            "https://example.invalid/",
        ] {
            let configuration = try AtlasIOSAppProcessOwner
                .productionConfiguration(
                    environment: ["ATLAS_API_BASE_URL": rawURL]
                )
            XCTAssertEqual(
                configuration.apiBaseURL.scheme,
                URL(string: rawURL)?.scheme
            )
            XCTAssertEqual(
                configuration.apiBaseURL.host,
                URL(string: rawURL)?.host
            )
        }
    }

    func testProductionConfigurationRejectsUnsafeOriginsWithFixedError() {
        for rawURL in [
            "ftp://example.invalid",
            "http://",
            "http://user@example.invalid",
            "http://user:password@example.invalid",
            "http://example.invalid?query=value",
            "http://example.invalid#fragment",
            "http://example.invalid/non-root",
        ] {
            XCTAssertThrowsError(
                try AtlasIOSAppProcessOwner.productionConfiguration(
                    environment: ["ATLAS_API_BASE_URL": rawURL]
                )
            ) { error in
                XCTAssertEqual(
                    String(describing: error),
                    "productionUnavailable"
                )
                XCTAssertFalse(String(describing: error).contains(rawURL))
            }
        }
    }

    func testDescriptionsAndSourceRemainPrivateFreeAndMergeStable()
        throws
    {
        let owner = makeOwner(
            route: .referenceCapture(.search),
            probe: ProcessFactoryProbe(harness: ProcessHarnessFake())
        )
        XCTAssertEqual(
            owner.description,
            "AtlasIOSAppProcessOwner(<redacted>)"
        )
        XCTAssertEqual(
            owner.presentation.description,
            "AtlasIOSAppProcessPresentation(<redacted>)"
        )
        XCTAssertFalse(owner.description.contains(Self.fakeEnvironmentValue))

        let source = try Self.processOwnerSource()
        for forbidden in [
            "AtlasVaultPrivateState",
            "AtlasVaultHydratedState",
            "savedSearch",
            "savedJob",
            "tracker",
            "applicationNote",
            "profileSnippet",
            "draftMetadata",
            "generatedDocument",
            "UserDefaults",
            "defaultBaseURL",
            "192.168.",
            "Task." + "detached",
            "nonisolated(" + "unsafe)",
            "@unchecked " + "Sendable",
            "Codable",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        XCTAssertTrue(source.contains("@MainActor"))
        XCTAssertTrue(source.contains("ObservableObject"))
        XCTAssertTrue(source.contains("@Published"))
        XCTAssertTrue(source.contains("ATLAS_API_BASE_URL"))
        XCTAssertTrue(source.contains("http://127.0.0.1:8765"))
        XCTAssertFalse(source.contains("plan.productionHarnessIfNeeded"))
    }

    private func makeOwner(
        route: AtlasIOSAppEntryRoute,
        probe: ProcessFactoryProbe
    ) -> AtlasIOSAppProcessOwner {
        AtlasIOSAppProcessOwner(
            route: route,
            productionFactory: {
                try probe.makeHarness()
            }
        )
    }

    private func requireObservableObject<Owner: ObservableObject>(
        _ type: Owner.Type
    ) {}

    private func expectPresentation(
        _ actual: AtlasIOSAppProcessPresentation,
        _ expected: AtlasIOSAppProcessPresentation,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertEqual(actual, expected, file: file, line: line)
    }

    private static func processOwnerSource() throws -> String {
        try String(
            contentsOf: appleRoot
                .appendingPathComponent("Sources/AtlasUI")
                .appendingPathComponent("AtlasIOSAppProcessOwner.swift"),
            encoding: .utf8
        )
    }

    private static var appleRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}

private enum ProcessOwnerTestError: Error {
    case production(String)
}

private final class ProcessFactoryLifetimeToken {}

@MainActor
private final class ProcessFactoryProbe {
    private let harness: (any AtlasIOSAppProcessHarness)?
    private let error: ProcessOwnerTestError?
    private(set) var calls = 0

    init(
        harness: (any AtlasIOSAppProcessHarness)? = nil,
        error: ProcessOwnerTestError? = nil
    ) {
        self.harness = harness
        self.error = error
    }

    func makeHarness() throws -> any AtlasIOSAppProcessHarness {
        calls += 1
        if let error {
            throw error
        }
        guard let harness else {
            throw ProcessOwnerTestError.production("missing harness")
        }
        return harness
    }
}

@MainActor
private final class ProcessHarnessFake: AtlasIOSAppProcessHarness {
    private let startGate: ProcessSuspensionGate?
    private let stopGate: ProcessSuspensionGate?
    private let startFails: Bool
    private let stopReleasesStart: Bool
    private let owner = AtlasVaultProductionPresentationOwner()

    private(set) var startCalls = 0
    private(set) var stopCalls = 0
    private(set) var makeRootCalls = 0

    init(
        startGate: ProcessSuspensionGate? = nil,
        stopGate: ProcessSuspensionGate? = nil,
        startFails: Bool = false,
        stopReleasesStart: Bool = false
    ) {
        self.startGate = startGate
        self.stopGate = stopGate
        self.startFails = startFails
        self.stopReleasesStart = stopReleasesStart
    }

    func start() async throws -> AtlasLockedShellUnlockFlowState {
        startCalls += 1
        if let startGate {
            await startGate.suspend()
        }
        if startFails {
            throw ProcessOwnerTestError.production("start failed")
        }
        return owner.flowState
    }

    func stop() async -> AtlasLockedShellUnlockFlowState {
        stopCalls += 1
        if stopReleasesStart, let startGate {
            await startGate.release()
        }
        if let stopGate {
            await stopGate.suspend()
        }
        return owner.flowState
    }

    func makeRootView() -> AtlasVaultProductionRootView {
        makeRootCalls += 1
        return AtlasVaultProductionRootView(
            owner: owner,
            publicShellActions: AtlasLockedPublicShellActions(
                search: { _ in },
                requestUnlock: {}
            ),
            unlockActions: AtlasExplicitUnlockViewActions(
                select: { _ in },
                submit: { _ in .failed },
                cancel: {},
                didDisappear: {}
            )
        )
    }
}

private actor ProcessSuspensionGate {
    private var entered = false
    private var entryWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseContinuation: CheckedContinuation<Void, Never>?

    func suspend() async {
        entered = true
        let waiters = entryWaiters
        entryWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
        await withCheckedContinuation { continuation in
            releaseContinuation = continuation
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

    func release() {
        releaseContinuation?.resume()
        releaseContinuation = nil
    }
}
