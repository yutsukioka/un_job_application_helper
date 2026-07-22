import Foundation
import Security
import XCTest
@testable import AtlasUI

final class AtlasVaultProductionCompositionHarnessTests: XCTestCase {
    private static let fakeQuery = "FAKE_PHASE_2D57_QUERY_DO_NOT_LOG"
    private static let fakeURL = URL(string: "https://example.invalid")!

    func testIntendedNeutralLifecycleAndCompositionSurfaceExists() {
        _ = AtlasVaultPlatformLifecycleEventSourcing.self
        _ = AtlasVaultProductionLifecycleForwarder.self
        _ = AtlasVaultProductionCompositionConfiguration.self
        _ = AtlasVaultProductionCompositionError.self
        _ = AtlasContinuousVaultLifecycleTimebase.self
        _ = AtlasVaultProductionCompositionFactory.self
        _ = AtlasVaultProductionCompositionHarness.self
    }

    func testForwarderConstructionIsSideEffectFreeAndStartIsIdempotent()
        async
    {
        let source = HarnessLifecycleSource()
        let host = HarnessHostFake()
        let forwarder = AtlasVaultProductionLifecycleForwarder(
            source: source,
            host: host
        )

        await harnessExpectEqual(await source.subscriptionCount(), 0)
        await harnessExpectEqual(await host.lifecycleEvents(), [])
        await harnessExpectTrue(await forwarder.start())
        await harnessExpectTrue(await forwarder.start())
        await harnessExpectEqual(await source.subscriptionCount(), 1)
        XCTAssertTrue(forwarder.description.contains("<redacted>"))
        await forwarder.stop()
    }

    func testForwarderRegistersInitialStartWaiterBeforeLaunchingTask()
        throws
    {
        let source = try Self.source(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )
        let startMarker = try XCTUnwrap(
            source.range(of: "    public func start() async -> Bool {")
        )
        let stopMarker = try XCTUnwrap(
            source.range(
                of: "    public func stop() async {",
                range: startMarker.upperBound..<source.endIndex
            )
        )
        let startBody = String(
            source[startMarker.lowerBound..<stopMarker.lowerBound]
        )
        let initialWaiter = try XCTUnwrap(
            startBody.range(
                of: "startWaiters.append(continuation)",
                options: .backwards
            )
        )
        let taskLaunch = try XCTUnwrap(startBody.range(of: "let task = Task"))

        XCTAssertLessThan(initialWaiter.lowerBound, taskLaunch.lowerBound)
        XCTAssertFalse(startBody.contains("[weak self]"))
    }

    func testForwarderForwardsEventsInExactSerialOrder() async {
        let source = HarnessLifecycleSource()
        let eventGate = HarnessSuspensionGate()
        let host = HarnessHostFake(lifecycleGate: eventGate)
        let forwarder = AtlasVaultProductionLifecycleForwarder(
            source: source,
            host: host
        )
        await harnessExpectTrue(await forwarder.start())

        await source.emit(.didBecomeActive)
        await eventGate.waitUntilEntered(1)
        await source.emit(.willResignActive)
        await source.emit(.protectedDataBecameUnavailable)
        await harnessExpectEqual(
            await host.lifecycleEvents(),
            [.didBecomeActive]
        )
        await harnessExpectEqual(
            await host.maximumConcurrentLifecycleCalls(),
            1
        )

        await eventGate.release(call: 1)
        await eventGate.waitUntilEntered(2)
        await harnessExpectEqual(
            await host.lifecycleEvents(),
            [.didBecomeActive, .willResignActive]
        )
        await eventGate.release(call: 2)
        await eventGate.waitUntilEntered(3)
        await eventGate.release(call: 3)
        await host.waitUntilLifecycleEventCount(3)

        await harnessExpectEqual(
            await host.lifecycleEvents(),
            [
                .didBecomeActive,
                .willResignActive,
                .protectedDataBecameUnavailable,
            ]
        )
        await harnessExpectEqual(
            await host.maximumConcurrentLifecycleCalls(),
            1
        )
        await forwarder.stop()
    }

    func testForwarderStopCancelsAndAwaitsRetainedTask() async {
        let source = HarnessLifecycleSource()
        let eventGate = HarnessSuspensionGate()
        let host = HarnessHostFake(lifecycleGate: eventGate)
        let forwarder = AtlasVaultProductionLifecycleForwarder(
            source: source,
            host: host
        )
        let completion = HarnessBoolRecorder()
        await harnessExpectTrue(await forwarder.start())
        await source.emit(.didEnterBackground)
        await eventGate.waitUntilEntered(1)

        let stop = Task {
            await forwarder.stop()
            await completion.record(true)
        }
        await forwarder.waitUntilTerminalForTesting()
        await harnessExpectNil(await completion.value())

        await eventGate.release(call: 1)
        await stop.value
        await harnessExpectEqual(await completion.value(), true)
        await harnessExpectFalse(await forwarder.start())

        await source.emit(.didBecomeActive)
        await harnessExpectEqual(
            await host.lifecycleEvents(),
            [.didEnterBackground]
        )
        await forwarder.stop()
    }

    func testForwarderRetainsTerminalTaskUntilStopDrainsIt() async {
        let source = HarnessLifecycleSource()
        let forwarder = AtlasVaultProductionLifecycleForwarder(
            source: source,
            host: HarnessHostFake()
        )

        await harnessExpectTrue(await forwarder.start())
        await source.finish()
        await forwarder.waitUntilTerminalForTesting()
        await harnessExpectTrue(
            await forwarder.hasRetainedForwardingTaskForTesting()
        )

        await forwarder.stop()
        await harnessExpectFalse(
            await forwarder.hasRetainedForwardingTaskForTesting()
        )
    }

    func testForwarderHandlesStreamCompletionAndWillTerminateTerminally()
        async
    {
        let completedSource = HarnessLifecycleSource()
        let completedHost = HarnessHostFake()
        let completedForwarder = AtlasVaultProductionLifecycleForwarder(
            source: completedSource,
            host: completedHost
        )
        await harnessExpectTrue(await completedForwarder.start())
        await completedSource.finish()
        await completedForwarder.waitUntilTerminalForTesting()
        await harnessExpectFalse(await completedForwarder.start())

        let terminatingSource = HarnessLifecycleSource()
        let terminatingHost = HarnessHostFake()
        let terminatingForwarder = AtlasVaultProductionLifecycleForwarder(
            source: terminatingSource,
            host: terminatingHost
        )
        await harnessExpectTrue(await terminatingForwarder.start())
        await terminatingSource.emit(.willTerminate)
        await terminatingForwarder.waitUntilTerminalForTesting()
        await terminatingSource.emit(.didBecomeActive)

        await harnessExpectEqual(
            await terminatingHost.lifecycleEvents(),
            [.willTerminate]
        )
        await harnessExpectFalse(await terminatingForwarder.start())
        await terminatingForwarder.stop()
    }

    @MainActor
    func testHarnessConstructionStartsNothingAndStartOrdersAndCoalesces()
        async throws
    {
        let source = HarnessLifecycleSource()
        let startGate = HarnessSuspensionGate()
        let host = HarnessHostFake(startGate: startGate)
        let harness = Self.makeHarness(host: host, source: source)

        await harnessExpectEqual(await source.subscriptionCount(), 0)
        await harnessExpectEqual(await host.startCallCount(), 0)
        await harnessExpectEqual(await host.stopCallCount(), 0)

        let first = Task { @MainActor in try await harness.start() }
        await startGate.waitUntilEntered(1)
        await harnessExpectEqual(await source.subscriptionCount(), 1)
        await harnessExpectEqual(await host.startCallCount(), 1)
        await source.emit(.didBecomeActive)
        await host.waitUntilLifecycleEventCount(1)
        await harnessExpectEqual(
            await host.lifecycleEvents(),
            [.didBecomeActive]
        )
        let second = Task { @MainActor in try await harness.start() }
        await harnessExpectEqual(await host.startCallCount(), 1)

        await startGate.release(call: 1)
        let firstState = try await first.value
        let secondState = try await second.value
        XCTAssertEqual(firstState, secondState)
        _ = try await harness.start()
        await harnessExpectEqual(await host.startCallCount(), 1)
        await harnessExpectEqual(await source.subscriptionCount(), 1)
        _ = await harness.stop()
    }

    @MainActor
    func testCallerCancellationDoesNotOrphanRetainedHarnessStart()
        async throws
    {
        let source = HarnessLifecycleSource()
        let startGate = HarnessSuspensionGate()
        let host = HarnessHostFake(startGate: startGate)
        let harness = Self.makeHarness(host: host, source: source)

        let cancelledCaller = Task { @MainActor in
            try await harness.start()
        }
        await startGate.waitUntilEntered(1)
        cancelledCaller.cancel()

        let joiningCaller = Task { @MainActor in
            try await harness.start()
        }
        await harnessExpectEqual(await source.subscriptionCount(), 1)
        await harnessExpectEqual(await host.startCallCount(), 1)

        await startGate.release(call: 1)
        let cancelledCallerState = try await cancelledCaller.value
        let joiningCallerState = try await joiningCaller.value
        XCTAssertEqual(cancelledCallerState, joiningCallerState)
        await harnessExpectEqual(await host.startCallCount(), 1)
        _ = await harness.stop()
    }

    @MainActor
    func testTerminalStopWinsForInitiatingAndJoiningStartCallers()
        async
    {
        let source = HarnessLifecycleSource()
        let startGate = HarnessSuspensionGate()
        let stopGate = HarnessSuspensionGate()
        let host = HarnessHostFake(
            startGate: startGate,
            stopGate: stopGate
        )
        let harness = Self.makeHarness(host: host, source: source)

        let first = Task { @MainActor in try await harness.start() }
        await startGate.waitUntilEntered(1)

        let joinerEntered = expectation(description: "joining start entered")
        let second = Task { @MainActor in
            joinerEntered.fulfill()
            return try await harness.start()
        }
        await fulfillment(of: [joinerEntered], timeout: 1)
        await Task.yield()

        let stop = Task { @MainActor in await harness.stop() }
        await stopGate.waitUntilEntered(1)
        await harness.waitUntilStoppingForTesting()
        await harnessExpectEqual(await host.stopCallCount(), 1)

        await startGate.release(call: 1)
        await XCTAssertThrowsErrorAsync(try await first.value) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .stopped
            )
        }
        await XCTAssertThrowsErrorAsync(try await second.value) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .stopped
            )
        }
        await harnessExpectEqual(await host.startCallCount(), 1)

        await stopGate.release(call: 1)
        let stoppedState = await stop.value
        XCTAssertFalse(stoppedState.publicShell.canRequestUnlock)
        await harnessExpectEqual(await host.stopCallCount(), 1)
    }

    @MainActor
    func testTerminalStopWinsWhenRetainedStartFailsForAllCallers() async {
        let source = HarnessLifecycleSource()
        let startGate = HarnessSuspensionGate()
        let host = HarnessHostFake(
            startGate: startGate,
            failStartAfterStop: true
        )
        let harness = Self.makeHarness(host: host, source: source)

        let first = Task { @MainActor in try await harness.start() }
        await startGate.waitUntilEntered(1)

        let joinerEntered = expectation(
            description: "joining failed start entered"
        )
        let second = Task { @MainActor in
            joinerEntered.fulfill()
            return try await harness.start()
        }
        await fulfillment(of: [joinerEntered], timeout: 1)
        await Task.yield()

        let stoppedState = await harness.stop()
        XCTAssertFalse(stoppedState.publicShell.canRequestUnlock)
        await harnessExpectEqual(await host.stopCallCount(), 1)

        await startGate.release(call: 1)
        await XCTAssertThrowsErrorAsync(try await first.value) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .stopped
            )
        }
        await XCTAssertThrowsErrorAsync(try await second.value) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .stopped
            )
        }
    }

    func testStartOutcomeChecksTerminalWinnerForBothCallerPaths()
        throws
    {
        let source = try Self.source(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )

        XCTAssertTrue(
            source.contains(
                "return try startResultAfterAwait("
            )
        )
        XCTAssertTrue(
            source.contains(
                "return try startResultAfterOperation("
            )
        )
        XCTAssertTrue(
            source.contains(
                "throw AtlasVaultProductionCompositionError.stopped"
            )
        )
    }

    @MainActor
    func testHarnessStartFailureStopsForwardingAndLeavesPrivateFreeOwner()
        async
    {
        let source = HarnessLifecycleSource()
        let host = HarnessHostFake(startFailure: .start)
        let harness = Self.makeHarness(host: host, source: source)

        await XCTAssertThrowsErrorAsync(try await harness.start()) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .startUnavailable
            )
        }
        await source.emit(.didBecomeActive)

        await harnessExpectEqual(await host.lifecycleEvents(), [])
        await harnessExpectEqual(await host.stopCallCount(), 1)
        XCTAssertEqual(harness.presentationOwner.flowState.mode, .lockedPublic)
        XCTAssertFalse(
            harness.presentationOwner.flowState.publicShell.canRequestUnlock
        )
        await harnessExpectFalse(await harness.lifecycleIsRunningForTesting())
        await XCTAssertThrowsErrorAsync(try await harness.start()) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .stopped
            )
        }
        _ = await harness.stop()
        await harnessExpectEqual(await host.stopCallCount(), 1)
    }

    func testHarnessStartFailureAwaitsStructuredHostAndLifecycleDrain()
        throws
    {
        let source = try Self.source(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )
        let startTask = try XCTUnwrap(
            source.range(of: "        let task = Task<")
        )
        let operation = try XCTUnwrap(
            source.range(
                of: "        let operation = StartOperation(",
                range: startTask.upperBound..<source.endIndex
            )
        )
        let body = String(source[startTask.lowerBound..<operation.lowerBound])

        XCTAssertTrue(body.contains("async let hostState = host.stop()"))
        XCTAssertTrue(
            body.contains(
                "async let lifecycleStop: Void = lifecycleForwarder.stop()"
            )
        )
        XCTAssertTrue(body.contains("await hostState"))
        XCTAssertTrue(body.contains("await lifecycleStop"))
    }

    @MainActor
    func testHarnessStopStartsHostAndForwarderTeardownAndCoalesces()
        async throws
    {
        let source = HarnessLifecycleSource()
        let stopGate = HarnessSuspensionGate()
        let host = HarnessHostFake(stopGate: stopGate)
        let harness = Self.makeHarness(host: host, source: source)
        _ = try await harness.start()

        let first = Task { @MainActor in await harness.stop() }
        await stopGate.waitUntilEntered(1)
        await harness.waitUntilStoppingForTesting()
        await harnessExpectFalse(await harness.lifecycleIsRunningForTesting())
        let second = Task { @MainActor in await harness.stop() }
        await harnessExpectEqual(await host.stopCallCount(), 1)

        await stopGate.release(call: 1)
        let firstState = await first.value
        let secondState = await second.value
        XCTAssertEqual(firstState, secondState)
        XCTAssertFalse(firstState.publicShell.canRequestUnlock)
        _ = await harness.stop()
        await harnessExpectEqual(await host.stopCallCount(), 1)
        await XCTAssertThrowsErrorAsync(try await harness.start()) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .stopped
            )
        }
    }

    @MainActor
    func testWillTerminateMakesHarnessTerminalAndLaterStopIdempotent()
        async throws
    {
        let source = HarnessLifecycleSource()
        let host = HarnessHostFake()
        let harness = Self.makeHarness(host: host, source: source)
        _ = try await harness.start()

        await source.emit(.willTerminate)
        await host.waitUntilLifecycleEventCount(1)
        await harness.waitUntilLifecycleTerminationForTesting()

        await XCTAssertThrowsErrorAsync(try await harness.start()) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .stopped
            )
        }
        _ = await harness.stop()
        _ = await harness.stop()
        await harnessExpectEqual(
            await host.lifecycleEvents(),
            [.willTerminate]
        )
        await harnessExpectEqual(await host.stopCallCount(), 1)
    }

    @MainActor
    func testPublicAndUnlockActionsDelegateExactlyOnceAndMapStatuses()
        async throws
    {
        let host = HarnessHostFake()
        let harness = Self.makeHarness(
            host: host,
            source: HarnessLifecycleSource(),
            publicSearchLimit: 37,
            unlockTimeout: .seconds(17)
        )

        await harness.publicShellActions.search(query: Self.fakeQuery)
        let capturedRequest = await host.lastSearchRequest()
        let request = try XCTUnwrap(capturedRequest)
        XCTAssertEqual(request.query, Self.fakeQuery)
        XCTAssertEqual(request.limit, 37)
        XCTAssertEqual(request.offset, 0)
        await harnessExpectEqual(await host.searchCallCount(), 1)

        await harness.publicShellActions.requestUnlock()
        await harnessExpectEqual(await host.unlockRequestCallCount(), 1)
        await harness.unlockActions.select(.localKey)
        await harnessExpectEqual(await host.selectedMethods(), [.localKey])

        await host.setSubmitFlow(Self.unlockedFlow())
        await harnessExpectEqual(
            await harness.unlockActions.submit(.localKey),
            .unlocked
        )
        await harnessExpectEqual(
            await host.lastSubmitTimeout(),
            .seconds(17)
        )

        await host.setSubmitFlow(Self.failedPanelFlow())
        await harnessExpectEqual(
            await harness.unlockActions.submit(.localKey),
            .failed
        )

        await host.setSubmitFlow(Self.lockedFlow(canRequestUnlock: false))
        await harnessExpectEqual(
            await harness.unlockActions.submit(.localKey),
            .failed
        )
        await harnessExpectEqual(await host.submitCallCount(), 3)

        await harness.unlockActions.cancel()
        await harness.unlockActions.didDisappear()
        await harnessExpectEqual(await host.cancelCallCount(), 1)
        await harnessExpectEqual(await host.disappearanceCallCount(), 1)

        let rendered = [
            harness.publicShellActions.description,
            harness.publicShellActions.debugDescription,
            harness.unlockActions.description,
            harness.unlockActions.debugDescription,
            harness.description,
        ].joined(separator: "\n")
        XCTAssertTrue(rendered.contains("<redacted>"))
        XCTAssertFalse(rendered.contains(Self.fakeQuery))
    }

    @MainActor
    func testSearchActionDiscardsFixedFailureWithoutSecondHostCall() async {
        let host = HarnessHostFake(searchFailure: .unavailable)
        let harness = Self.makeHarness(
            host: host,
            source: HarnessLifecycleSource()
        )

        await harness.publicShellActions.search(query: Self.fakeQuery)

        await harnessExpectEqual(await host.searchCallCount(), 1)
    }

    func testConfigurationAcceptsExplicitOriginsAndRejectsUnsafeValues()
        throws
    {
        for value in ["http://example.invalid", "https://example.invalid/"] {
            let configuration = try AtlasVaultProductionCompositionConfiguration(
                apiBaseURL: XCTUnwrap(URL(string: value)),
                publicSearchLimit: 50,
                unlockTimeout: .seconds(30),
                lifecycleLockPolicy: .immediate,
                lockOnInactive: false
            )
            XCTAssertTrue(["http", "https"].contains(
                configuration.apiBaseURL.scheme
            ))
        }

        for value in [
            "file:///tmp/fake",
            "https:///missing-host",
            "https://fake-user@example.invalid",
            "https://fake-user:fake-password@example.invalid",
            "https://example.invalid/path",
            "https://example.invalid?fake=query",
            "https://example.invalid#fake-fragment",
        ] {
            XCTAssertThrowsError(
                try AtlasVaultProductionCompositionConfiguration(
                    apiBaseURL: XCTUnwrap(URL(string: value)),
                    publicSearchLimit: 50,
                    unlockTimeout: .seconds(30),
                    lifecycleLockPolicy: .immediate,
                    lockOnInactive: false
                )
            ) { error in
                XCTAssertEqual(
                    error as? AtlasVaultProductionCompositionError,
                    .invalidAPIBaseURL
                )
            }
        }

        for limit in [0, AtlasPublicJobSearchRequest.maximumLimit + 1] {
            XCTAssertThrowsError(
                try Self.configuration(publicSearchLimit: limit)
            ) { error in
                XCTAssertEqual(
                    error as? AtlasVaultProductionCompositionError,
                    .invalidSearchLimit
                )
            }
        }
        for timeout in [Duration.zero, .seconds(-1)] {
            XCTAssertThrowsError(
                try Self.configuration(unlockTimeout: timeout)
            ) { error in
                XCTAssertEqual(
                    error as? AtlasVaultProductionCompositionError,
                    .invalidUnlockTimeout
                )
            }
        }

        let configuration = try Self.configuration()
        let rendered = [configuration.description, configuration.debugDescription]
            .joined(separator: "\n")
        XCTAssertTrue(rendered.contains("<redacted>"))
        XCTAssertFalse(rendered.contains(Self.fakeURL.absoluteString))
        XCTAssertFalse(rendered.contains("50"))
        XCTAssertFalse(rendered.contains("30"))
    }

    @MainActor
    func testInjectedCompositionConstructionInvokesNoDependency() async throws {
        let source = HarnessLifecycleSource()
        let harness = try AtlasVaultProductionCompositionFactory
            .makeUnwiredProductionLike(
                configuration: Self.configuration(),
                lifecycleEvents: source,
                directoryLocator: FailingDirectoryLocator(),
                keychainClient: FailingKeychainClient(),
                atomicFileSystemClient: FailingAtomicFileSystemClient(),
                lifecycleClock: HarnessLifecycleTimeFake(),
                lifecycleSleeper: HarnessLifecycleTimeFake()
            )

        await harnessExpectEqual(await source.subscriptionCount(), 0)
        XCTAssertEqual(harness.presentationOwner.flowState.mode, .lockedPublic)
        XCTAssertFalse(
            harness.presentationOwner.flowState.publicShell.canRequestUnlock
        )
    }

    @MainActor
    func testProductionLikeInjectedGraphStartsNoVaultAndStopsPrivateFree()
        async throws
    {
        let temporaryRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryRoot,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: temporaryRoot) }

        let source = HarnessLifecycleSource()
        let keychain = ItemNotFoundKeychainClient()
        let timebase = HarnessLifecycleTimeFake()
        let harness = try AtlasVaultProductionCompositionFactory
            .makeUnwiredProductionLike(
                configuration: Self.configuration(),
                lifecycleEvents: source,
                directoryLocator: FixedDirectoryLocator(root: temporaryRoot),
                keychainClient: keychain,
                atomicFileSystemClient: FailingAtomicFileSystemClient(),
                lifecycleClock: timebase,
                lifecycleSleeper: timebase
            )

        await harnessExpectEqual(await source.subscriptionCount(), 0)
        let started = try await harness.start()
        await harnessExpectEqual(await source.subscriptionCount(), 1)
        XCTAssertEqual(started.mode, .lockedPublic)
        XCTAssertNil(started.unlockPanelState)
        XCTAssertEqual(harness.presentationOwner.flowState, started)

        await harness.publicShellActions.requestUnlock()
        XCTAssertEqual(
            harness.presentationOwner.flowState.publicShell.vaultStatus,
            .noVault
        )
        XCTAssertEqual(
            harness.presentationOwner.flowState.mode,
            .lockedPublic
        )

        let stopped = await harness.stop()
        XCTAssertEqual(stopped.mode, .lockedPublic)
        XCTAssertFalse(stopped.publicShell.canRequestUnlock)
        XCTAssertEqual(harness.presentationOwner.flowState, stopped)
        await harnessExpectFalse(await harness.lifecycleIsRunningForTesting())
        await XCTAssertThrowsErrorAsync(try await harness.start()) { error in
            XCTAssertEqual(
                error as? AtlasVaultProductionCompositionError,
                .stopped
            )
        }
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: temporaryRoot.appendingPathComponent(".atlasvault").path
        ))
    }

    @MainActor
    func testOneHarnessCreatesMultipleRootsOverOneOwnerWithoutStarting()
        async
    {
        let source = HarnessLifecycleSource()
        let host = HarnessHostFake()
        let harness = Self.makeHarness(host: host, source: source)
        let owner = harness.presentationOwner

        _ = harness.makeRootView()
        _ = harness.makeRootView()

        XCTAssertTrue(owner === harness.presentationOwner)
        await harnessExpectEqual(await host.startCallCount(), 0)
        await harnessExpectEqual(await host.stopCallCount(), 0)
        await harnessExpectEqual(await source.subscriptionCount(), 0)
    }

    func testCompositionSourceUsesReviewedConcreteAssemblyAndNoPlatformWork()
        throws
    {
        let source = try Self.source(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )
        for required in [
            "AtlasAPIClient(baseURL: configuration.apiBaseURL)",
            "AtlasAPIClientPublicJobAdapter",
            "AtlasApplicationSupportPublicSnapshotRestorer",
            "AtlasKeychainVaultSelectionRegistry",
            "AtlasVaultRuntimeFactory.production",
            "AtlasVaultRuntimeFacade.runtimeServices",
            "AtlasVaultLifecycleCoordinator",
            "AtlasVaultProductionPresentationPipeline",
            "AtlasVaultProductionPresentationOwner",
            "AtlasVaultUnlockRequestCoordinator",
            "AtlasVaultProductionUnlockPresentationControllerBuilder",
            "AtlasVaultProductionHostFactory",
            "AtlasVaultProductionHostBuilder",
            "AtlasVaultUnlockCapabilities.currentProduction",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "AtlasAPIClient()",
            "defaultBaseURL",
            "baseURLDefaultsKey",
            "UserDefaults",
            "UIKit",
            "AppKit",
            "UIApplication",
            "NSApplication",
            "ScenePhase",
            "scenePhase",
            "NotificationCenter",
            "@main",
            "WindowGroup",
            "AtlasRootView",
            "SearchViewModel",
            "NavigationStack",
            "NavigationLink",
            "suppliedTestVaultKey",
            "Task." + "detached",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        XCTAssertFalse(source.contains("derivePassphraseVaultKey: { data"))
        XCTAssertFalse(source.contains("deriveRecoveryVaultKey: { data"))
    }

    @MainActor
    private static func makeHarness(
        host: HarnessHostFake,
        source: HarnessLifecycleSource,
        publicSearchLimit: Int = 50,
        unlockTimeout: Duration = .seconds(30)
    ) -> AtlasVaultProductionCompositionHarness {
        let owner = AtlasVaultProductionPresentationOwner()
        let forwarder = AtlasVaultProductionLifecycleForwarder(
            source: source,
            host: host
        )
        return AtlasVaultProductionCompositionHarness(
            host: host,
            presentationOwner: owner,
            lifecycleForwarder: forwarder,
            publicSearchLimit: publicSearchLimit,
            unlockTimeout: unlockTimeout
        )
    }

    private static func configuration(
        publicSearchLimit: Int = 50,
        unlockTimeout: Duration = .seconds(30)
    ) throws -> AtlasVaultProductionCompositionConfiguration {
        try AtlasVaultProductionCompositionConfiguration(
            apiBaseURL: fakeURL,
            publicSearchLimit: publicSearchLimit,
            unlockTimeout: unlockTimeout,
            lifecycleLockPolicy: .immediate,
            lockOnInactive: false
        )
    }

    fileprivate static func lockedFlow(
        canRequestUnlock: Bool
    ) -> AtlasLockedShellUnlockFlowState {
        AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                serviceStatus: .unavailable,
                canRequestUnlock: canRequestUnlock
            ),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: nil,
                status: .locked
            ),
            isUnlockPanelPresented: false
        )
    }

    fileprivate static func unlockedFlow() -> AtlasLockedShellUnlockFlowState {
        AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                serviceStatus: .unavailable,
                canRequestUnlock: false
            ),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: .localKey,
                status: .unlocked
            ),
            isUnlockPanelPresented: false
        )
    }

    fileprivate static func failedPanelFlow()
        -> AtlasLockedShellUnlockFlowState
    {
        AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                serviceStatus: .unavailable,
                canRequestUnlock: false
            ),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: .localKey,
                status: .failed
            ),
            isUnlockPanelPresented: true
        )
    }

    private static func source(named name: String) throws -> String {
        let appleRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try String(
            contentsOf: appleRoot
                .appendingPathComponent("Sources/AtlasUI")
                .appendingPathComponent(name),
            encoding: .utf8
        )
    }

}

private enum HarnessFakeError: Error, Sendable {
    case start
}

private actor HarnessLifecycleSource: AtlasVaultPlatformLifecycleEventSourcing {
    private var subscriptions = 0
    private var continuation: AsyncStream<AtlasVaultLifecycleEvent>.Continuation?

    func events() async -> AsyncStream<AtlasVaultLifecycleEvent> {
        subscriptions += 1
        let pair = AsyncStream<AtlasVaultLifecycleEvent>.makeStream(
            bufferingPolicy: .unbounded
        )
        continuation = pair.continuation
        return pair.stream
    }

    func emit(_ event: AtlasVaultLifecycleEvent) {
        continuation?.yield(event)
    }

    func finish() {
        continuation?.finish()
    }

    func subscriptionCount() -> Int {
        subscriptions
    }
}

private actor HarnessSuspensionGate {
    private var entries = 0
    private var entryWaiters: [Int: [CheckedContinuation<Void, Never>]] = [:]
    private var releases: [Int: CheckedContinuation<Void, Never>] = [:]

    func suspend() async {
        entries += 1
        let call = entries
        let keys = entryWaiters.keys.filter { $0 <= entries }
        let waiters = keys.flatMap { entryWaiters.removeValue(forKey: $0) ?? [] }
        for waiter in waiters {
            waiter.resume()
        }
        await withCheckedContinuation { continuation in
            releases[call] = continuation
        }
    }

    func waitUntilEntered(_ count: Int) async {
        guard entries < count else {
            return
        }
        await withCheckedContinuation { continuation in
            entryWaiters[count, default: []].append(continuation)
        }
    }

    func release(call: Int) {
        releases.removeValue(forKey: call)?.resume()
    }
}

private actor HarnessBoolRecorder {
    private var stored: Bool?

    func record(_ value: Bool) {
        stored = value
    }

    func value() -> Bool? {
        stored
    }
}

private actor HarnessHostFake: AtlasVaultProductionHosting {
    private let startGate: HarnessSuspensionGate?
    private let stopGate: HarnessSuspensionGate?
    private let lifecycleGate: HarnessSuspensionGate?
    private let startFailure: HarnessFakeError?
    private let failStartAfterStop: Bool
    private let searchFailure: AtlasPublicJobServiceError?
    private var flow = AtlasVaultProductionCompositionHarnessTests
        .lockedFlow(canRequestUnlock: true)
    private var submitFlow = AtlasVaultProductionCompositionHarnessTests
        .lockedFlow(canRequestUnlock: false)
    private var starts = 0
    private var stops = 0
    private var searches = 0
    private var unlockRequests = 0
    private var submits = 0
    private var cancels = 0
    private var disappearances = 0
    private var lastSearch: AtlasPublicJobSearchRequest?
    private var lastTimeout: Duration?
    private var methods: [AtlasVaultUnlockMethod?] = []
    private var handledEvents: [AtlasVaultLifecycleEvent] = []
    private var lifecycleInFlight = 0
    private var maxLifecycleInFlight = 0
    private var lifecycleWaiters:
        [Int: [CheckedContinuation<Void, Never>]] = [:]

    init(
        startGate: HarnessSuspensionGate? = nil,
        stopGate: HarnessSuspensionGate? = nil,
        lifecycleGate: HarnessSuspensionGate? = nil,
        startFailure: HarnessFakeError? = nil,
        failStartAfterStop: Bool = false,
        searchFailure: AtlasPublicJobServiceError? = nil
    ) {
        self.startGate = startGate
        self.stopGate = stopGate
        self.lifecycleGate = lifecycleGate
        self.startFailure = startFailure
        self.failStartAfterStop = failStartAfterStop
        self.searchFailure = searchFailure
    }

    func start() async throws -> AtlasLockedShellUnlockFlowState {
        starts += 1
        if let startGate {
            await startGate.suspend()
        }
        if let startFailure {
            throw startFailure
        }
        if failStartAfterStop, stops > 0 {
            throw HarnessFakeError.start
        }
        return flow
    }

    func stop() async -> AtlasLockedShellUnlockFlowState {
        stops += 1
        if let stopGate {
            await stopGate.suspend()
        }
        flow = AtlasVaultProductionCompositionHarnessTests
            .lockedFlow(canRequestUnlock: false)
        return flow
    }

    func currentFlowState() async -> AtlasLockedShellUnlockFlowState {
        flow
    }

    func searchPublicJobs(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult {
        searches += 1
        lastSearch = request
        if let searchFailure {
            throw searchFailure
        }
        return try! AtlasPublicJobSearchResult(
            jobs: [],
            total: 0,
            limit: request.limit,
            offset: request.offset
        )
    }

    func requestUnlockPanel() async -> AtlasLockedShellUnlockFlowState {
        unlockRequests += 1
        return flow
    }

    func selectUnlockMethod(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasLockedShellUnlockFlowState {
        methods.append(method)
        return flow
    }

    func submitUnlock(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration?
    ) async -> AtlasLockedShellUnlockFlowState {
        submits += 1
        lastTimeout = timeout
        return submitFlow
    }

    func cancelUnlock() async -> AtlasLockedShellUnlockFlowState {
        cancels += 1
        return flow
    }

    func unlockPanelDidDisappear() async -> AtlasLockedShellUnlockFlowState {
        disappearances += 1
        return flow
    }

    func lock() async -> AtlasLockedShellUnlockFlowState {
        flow
    }

    func handleLifecycleEvent(
        _ event: AtlasVaultLifecycleEvent
    ) async -> AtlasLockedShellUnlockFlowState {
        lifecycleInFlight += 1
        maxLifecycleInFlight = max(maxLifecycleInFlight, lifecycleInFlight)
        handledEvents.append(event)
        let count = handledEvents.count
        let keys = lifecycleWaiters.keys.filter { $0 <= count }
        let waiters = keys.flatMap {
            lifecycleWaiters.removeValue(forKey: $0) ?? []
        }
        for waiter in waiters {
            waiter.resume()
        }
        if let lifecycleGate {
            await lifecycleGate.suspend()
        }
        lifecycleInFlight -= 1
        if event == .willTerminate {
            flow = AtlasVaultProductionCompositionHarnessTests
                .lockedFlow(canRequestUnlock: false)
        }
        return flow
    }

    func waitUntilLifecycleEventCount(_ count: Int) async {
        guard handledEvents.count < count else {
            return
        }
        await withCheckedContinuation { continuation in
            lifecycleWaiters[count, default: []].append(continuation)
        }
    }

    func setSubmitFlow(_ state: AtlasLockedShellUnlockFlowState) {
        submitFlow = state
    }

    func startCallCount() -> Int { starts }
    func stopCallCount() -> Int { stops }
    func searchCallCount() -> Int { searches }
    func unlockRequestCallCount() -> Int { unlockRequests }
    func submitCallCount() -> Int { submits }
    func cancelCallCount() -> Int { cancels }
    func disappearanceCallCount() -> Int { disappearances }
    func lastSearchRequest() -> AtlasPublicJobSearchRequest? { lastSearch }
    func lastSubmitTimeout() -> Duration? { lastTimeout }
    func selectedMethods() -> [AtlasVaultUnlockMethod?] { methods }
    func lifecycleEvents() -> [AtlasVaultLifecycleEvent] { handledEvents }
    func maximumConcurrentLifecycleCalls() -> Int { maxLifecycleInFlight }
}

private struct FailingDirectoryLocator:
    AtlasApplicationSupportDirectoryLocating
{
    func applicationSupportDirectory() throws -> URL {
        XCTFail("directory locator invoked during construction")
        throw AtlasVaultRootDirectoryError.applicationSupportUnavailable
    }
}

private struct FixedDirectoryLocator:
    AtlasApplicationSupportDirectoryLocating
{
    let root: URL

    func applicationSupportDirectory() throws -> URL {
        root
    }
}

private struct FailingKeychainClient: AtlasKeychainClient {
    func add(_ item: AtlasKeychainItem) -> OSStatus {
        XCTFail("Keychain invoked during construction")
        return errSecNotAvailable
    }

    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        XCTFail("Keychain invoked during construction")
        return AtlasKeychainCopyResult(status: errSecNotAvailable, valueData: nil)
    }

    func update(
        _ query: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        XCTFail("Keychain invoked during construction")
        return errSecNotAvailable
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        XCTFail("Keychain invoked during construction")
        return errSecNotAvailable
    }
}

private struct ItemNotFoundKeychainClient: AtlasKeychainClient {
    func add(_ item: AtlasKeychainItem) -> OSStatus {
        XCTFail("selection must not add Keychain data")
        return errSecNotAvailable
    }

    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        return AtlasKeychainCopyResult(status: errSecItemNotFound, valueData: nil)
    }

    func update(
        _ query: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        XCTFail("selection must not update Keychain data")
        return errSecNotAvailable
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        XCTFail("selection must not delete Keychain data")
        return errSecNotAvailable
    }
}

private struct FailingAtomicFileSystemClient:
    AtlasVaultAtomicFileSystemClient
{
    func validatePreparedParent(for destinationURL: URL) throws {
        XCTFail("atomic filesystem invoked")
    }

    func createTemporaryFile(at url: URL) throws {
        XCTFail("atomic filesystem invoked")
    }

    func protectTemporaryFile(at url: URL) throws {
        XCTFail("atomic filesystem invoked")
    }

    func write(_ data: Data, to url: URL) throws {
        XCTFail("atomic filesystem invoked")
    }

    func read(from url: URL) throws -> Data {
        XCTFail("atomic filesystem invoked")
        return Data()
    }

    func synchronizeFile(at url: URL) throws {
        XCTFail("atomic filesystem invoked")
    }

    func commitTemporaryFile(
        at temporaryURL: URL,
        to destinationURL: URL,
        overwrite: Bool
    ) throws {
        XCTFail("atomic filesystem invoked")
    }

    func synchronizeDirectory(at url: URL) throws {
        XCTFail("atomic filesystem invoked")
    }

    func removeItemIfExists(at url: URL) throws {
        XCTFail("atomic filesystem invoked")
    }
}

private actor HarnessLifecycleTimeFake:
    AtlasVaultLifecycleClock,
    AtlasVaultLifecycleSleeper
{
    func now() async -> Duration { .zero }

    func sleep(until deadline: Duration) async throws {
        throw CancellationError()
    }
}

@MainActor
private func XCTAssertThrowsErrorAsync<T>(
    _ expression: @autoclosure @MainActor () async throws -> T,
    _ verify: (Error) -> Void,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        _ = try await expression()
        XCTFail("Expected error", file: file, line: line)
    } catch {
        verify(error)
    }
}

@MainActor
private func harnessExpectEqual<Value: Equatable & Sendable>(
    _ actual: Value,
    _ expected: Value,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    XCTAssertEqual(actual, expected, file: file, line: line)
}

@MainActor
private func harnessExpectTrue(
    _ expression: Bool,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    XCTAssertTrue(expression, file: file, line: line)
}

@MainActor
private func harnessExpectFalse(
    _ expression: Bool,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    XCTAssertFalse(expression, file: file, line: line)
}

@MainActor
private func harnessExpectNil<Value>(
    _ expression: Value?,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    XCTAssertNil(expression, file: file, line: line)
}
