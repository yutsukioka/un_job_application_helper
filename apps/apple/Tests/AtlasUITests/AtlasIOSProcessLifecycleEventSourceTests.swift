import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSProcessLifecycleEventSourceTests: XCTestCase {
    private let scene = AtlasIOSSceneIdentifier("FAKE_SOURCE_SCENE")
    private let boundary = UUID(uuidString: "11111111-2222-3333-4444-555555555555")!

    func testConstructionStartsNoObservationOrSystemRead() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)

        XCTAssertEqual(observer.beginCount, 0)
        XCTAssertEqual(observer.stopCount, 0)
        XCTAssertTrue(source.description.contains("<redacted>"))
        XCTAssertFalse(source.description.contains("FAKE_SOURCE_SCENE"))
        XCTAssertEqual(source.debugDescription, source.description)
        let hasProducer = await source.hasRetainedProducerTaskForTesting()
        XCTAssertFalse(hasProducer)
    }

    func testFirstSubscriptionStartsOnceAndSecondFinishesImmediately() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)

        let first = await source.subscription()
        let second = await source.subscription()
        var secondIterator = second.events.makeAsyncIterator()
        await second.requestReadinessBoundary(boundary)
        let secondDelivery = await secondIterator.next()

        XCTAssertEqual(observer.beginCount, 1)
        XCTAssertEqual(
            first.bootstrapEvents,
            [.protectedDataBecameAvailable, .didBecomeActive]
        )
        XCTAssertTrue(second.bootstrapEvents.isEmpty)
        XCTAssertNil(secondDelivery)

        observer.finish()
        let deliveries = await collect(first.events)
        XCTAssertTrue(deliveries.isEmpty)
        await source.waitUntilTerminalForTesting()
        XCTAssertEqual(observer.stopCount, 1)
    }

    func testBootstrapIsSeparateAndImmediateSignalPrecedesBoundary() async {
        let observer = IOSLifecycleObserverFake(
            bootstrap: activeBootstrap(),
            immediateSignal: .protectedDataBecameUnavailable
        )
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let subscription = await source.subscription()

        XCTAssertEqual(
            subscription.bootstrapEvents,
            [.protectedDataBecameAvailable, .didBecomeActive]
        )
        await subscription.requestReadinessBoundary(boundary)
        observer.finish()

        let deliveries = await collect(subscription.events)
        XCTAssertEqual(
            deliveries,
            [
                .event(.protectedDataBecameUnavailable),
                .readinessBoundary(boundary),
            ]
        )
    }

    func testMultiplePreBoundarySignalsPreserveReducerOrder() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let subscription = await source.subscription()

        observer.emit(.sceneWillResignActive(scene))
        observer.emit(.protectedDataBecameUnavailable)
        observer.emit(.sceneDidEnterBackground(scene))
        await subscription.requestReadinessBoundary(boundary)
        observer.finish()

        let deliveries = await collect(subscription.events)
        XCTAssertEqual(
            deliveries,
            [
                .event(.willResignActive),
                .event(.protectedDataBecameUnavailable),
                .event(.didEnterBackground),
                .readinessBoundary(boundary),
            ]
        )
    }

    func testPostBoundarySignalRemainsLiveAfterMatchingMarker() async {
        let observer = IOSLifecycleObserverFake(
            bootstrap: AtlasIOSLifecycleBootstrap(
                scenes: [:],
                applicationState: .background,
                protectedDataAvailable: true
            )
        )
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let subscription = await source.subscription()
        var iterator = subscription.events.makeAsyncIterator()

        await subscription.requestReadinessBoundary(boundary)
        let marker = await iterator.next()
        XCTAssertEqual(record(marker), .readinessBoundary(boundary))

        observer.emit(.applicationDidBecomeActive)
        let later = await iterator.next()
        XCTAssertEqual(record(later), .event(.didBecomeActive))

        observer.finish()
        let finishedDelivery = await iterator.next()
        XCTAssertNil(finishedDelivery)
        await source.waitUntilTerminalForTesting()
        XCTAssertEqual(observer.stopCount, 1)
    }

    func testSourcePreservesReducerOrderAndSuppressesDuplicates() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let subscription = await source.subscription()

        observer.emit(.sceneWillResignActive(scene))
        observer.emit(.sceneWillResignActive(scene))
        observer.emit(.sceneDidEnterBackground(scene))
        observer.emit(.sceneDidEnterBackground(scene))
        observer.finish()

        let deliveries = await collect(subscription.events)
        XCTAssertEqual(
            deliveries,
            [
                .event(.willResignActive),
                .event(.didEnterBackground),
            ]
        )
    }

    func testInputCompletionStopsObservationAndFinishesOutput() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let subscription = await source.subscription()

        observer.finish()
        let deliveries = await collect(subscription.events)
        XCTAssertTrue(deliveries.isEmpty)
        await source.waitUntilTerminalForTesting()

        XCTAssertEqual(observer.stopCount, 1)
        let isTerminal = await source.isTerminalForTesting()
        let hasProducer = await source.hasRetainedProducerTaskForTesting()
        XCTAssertTrue(isTerminal)
        XCTAssertFalse(hasProducer)
    }

    func testPreBoundaryTerminationStopsAndCannotBeResurrected() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let subscription = await source.subscription()

        observer.emit(.willTerminate)
        await subscription.requestReadinessBoundary(boundary)
        observer.emit(.protectedDataBecameUnavailable)

        let deliveries = await collect(subscription.events)
        XCTAssertEqual(deliveries, [.event(.willTerminate)])
        await source.waitUntilTerminalForTesting()
        XCTAssertEqual(observer.stopCount, 1)
        let hasProducer = await source.hasRetainedProducerTaskForTesting()
        XCTAssertFalse(hasProducer)

        await subscription.requestReadinessBoundary(boundary)
        let later = await source.subscription()
        XCTAssertTrue(later.bootstrapEvents.isEmpty)
        var laterIterator = later.events.makeAsyncIterator()
        let laterDelivery = await laterIterator.next()
        XCTAssertNil(laterDelivery)
        XCTAssertEqual(observer.beginCount, 1)
        XCTAssertEqual(observer.stopCount, 1)
    }

    func testConsumerCancellationCancelsAndDrainsProducer() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let subscription = await source.subscription()
        let consumer = Task {
            for await _ in subscription.events {}
        }

        await observer.waitUntilBeginCount(1)
        consumer.cancel()
        await consumer.value
        await source.waitUntilTerminalForTesting()

        XCTAssertEqual(observer.stopCount, 1)
        let hasProducer = await source.hasRetainedProducerTaskForTesting()
        XCTAssertFalse(hasProducer)
        observer.emit(.protectedDataBecameUnavailable)
        await subscription.requestReadinessBoundary(boundary)
        XCTAssertEqual(observer.deliveryCount, 0)
    }

    func testDeterministicBurstLosesNoRequiredLifecycleEvent() async {
        let observer = IOSLifecycleObserverFake(
            bootstrap: AtlasIOSLifecycleBootstrap(
                scenes: [:],
                applicationState: .background,
                protectedDataAvailable: true
            )
        )
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let subscription = await source.subscription()

        observer.emit(.applicationDidBecomeActive)
        observer.emit(.applicationWillResignActive)
        observer.emit(.protectedDataBecameUnavailable)
        observer.emit(.protectedDataBecameAvailable)
        observer.emit(.applicationDidEnterBackground)
        await subscription.requestReadinessBoundary(boundary)
        observer.finish()

        let deliveries = await collect(subscription.events)
        XCTAssertEqual(
            deliveries,
            [
                .event(.didBecomeActive),
                .event(.willResignActive),
                .event(.protectedDataBecameUnavailable),
                .event(.protectedDataBecameAvailable),
                .event(.didEnterBackground),
                .readinessBoundary(boundary),
            ]
        )
    }

    func testBackgroundBootstrapCompletesBeforeForwarderReadiness() async {
        await assertForwarderBootstrap(
            AtlasIOSLifecycleBootstrap(
                scenes: [scene: .background],
                applicationState: .active,
                protectedDataAvailable: true
            ),
            expected: [
                .protectedDataBecameAvailable,
                .didEnterBackground,
            ]
        )
    }

    func testInactiveBootstrapCompletesBeforeForwarderReadiness() async {
        await assertForwarderBootstrap(
            AtlasIOSLifecycleBootstrap(
                scenes: [scene: .foregroundInactive],
                applicationState: .active,
                protectedDataAvailable: true
            ),
            expected: [
                .protectedDataBecameAvailable,
                .willResignActive,
            ]
        )
    }

    func testActiveBootstrapPreservesProtectedDataFirstBeforeReadiness() async {
        await assertForwarderBootstrap(
            AtlasIOSLifecycleBootstrap(
                scenes: [scene: .foregroundActive],
                applicationState: .background,
                protectedDataAvailable: false
            ),
            expected: [
                .protectedDataBecameUnavailable,
                .didBecomeActive,
            ]
        )
    }

    func testUIKitBoundaryCoversRequiredNotificationsAndOrdering() throws {
        let source = try Self.source(
            named: "AtlasIOSProcessLifecycleEventSource.swift"
        )

        XCTAssertLessThan(
            try XCTUnwrap(source.range(of: "registerObservers"))
                .lowerBound,
            try XCTUnwrap(source.range(of: "captureBootstrap"))
                .lowerBound
        )
        for required in [
            "UIScene.willConnectNotification",
            "UIScene.didDisconnectNotification",
            "UIScene.willEnterForegroundNotification",
            "UIScene.didActivateNotification",
            "UIScene.willDeactivateNotification",
            "UIScene.didEnterBackgroundNotification",
            "UIApplication.didBecomeActiveNotification",
            "UIApplication.willResignActiveNotification",
            "UIApplication.didEnterBackgroundNotification",
            "UIApplication.protectedDataDidBecomeAvailableNotification",
            "UIApplication.protectedDataWillBecomeUnavailableNotification",
            "UIApplication.willTerminateNotification",
            "session.persistentIdentifier",
            "connectedScenes",
            "isProtectedDataAvailable",
            "removeObserver",
            "tokens.append",
            "MainActor.preconditionIsolated()",
            "bufferingPolicy: .unbounded",
            "#if canImport(UIKit)",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        XCTAssertFalse(source.contains("import AppKit"))
    }

    func testSourceUsesExplicitOrderedSubscriptionContract() throws {
        let source = try Self.source(
            named: "AtlasIOSProcessLifecycleEventSource.swift"
        )

        XCTAssertFalse(source.contains("public func " + "events() async"))
        XCTAssertTrue(source.contains("public func subscription() async"))
        XCTAssertTrue(source.contains("bootstrapEvents: bootstrapEvents"))
        XCTAssertTrue(
            source.contains(
                "AsyncStream<AtlasVaultPlatformLifecycleEventDelivery>"
            )
        )
        XCTAssertTrue(source.contains("AtlasIOSLifecycleSystemDelivery"))
        XCTAssertTrue(source.contains("case signal(AtlasIOSLifecycleSignal)"))
        XCTAssertTrue(source.contains("case readinessBoundary(UUID)"))
        XCTAssertTrue(
            source.contains(
                "requestReadinessBoundary: observation.requestReadinessBoundary"
            )
        )
        XCTAssertEqual(
            source.components(
                separatedBy: "output.yield(.readinessBoundary(identifier))"
            ).count - 1,
            1
        )
        let producerSwitch = try XCTUnwrap(
            source.range(of: "case let .readinessBoundary(identifier):")
        )
        let publicYield = try XCTUnwrap(
            source.range(of: "output.yield(.readinessBoundary(identifier))")
        )
        XCTAssertLessThan(producerSwitch.lowerBound, publicYield.lowerBound)
    }

    func testSourceHasNoUnsafeOrPrivateCoupling() throws {
        let source = try Self.source(
            named: "AtlasIOSProcessLifecycleEventSource.swift"
        )
        for forbidden in [
            "AtlasVaultPrivateState", "vaultID", "passphrase", "recovery",
            "Keychain", "SecItem", "FileManager", "URLSession",
            "UserDefaults", "Task." + "detached",
            "nonisolated" + "(unsafe)", "@unchecked" + " Sendable",
            "Thread." + "sleep", "u" + "sleep",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }

        let package = try Self.packageSource()
        XCTAssertTrue(package.contains(".iOS(.v18)"))
    }

    private func assertForwarderBootstrap(
        _ bootstrap: AtlasIOSLifecycleBootstrap,
        expected: [AtlasVaultLifecycleEvent],
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let observer = IOSLifecycleObserverFake(bootstrap: bootstrap)
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let gate = IOSLifecycleHostGate()
        let host = IOSLifecycleForwarderHostFake(
            flow: lockedFlow(),
            lifecycleGate: gate
        )
        let forwarder = AtlasVaultProductionLifecycleForwarder(
            source: source,
            host: host
        )
        let result = IOSLifecycleBoolResult()
        let startTask = Task {
            let started = await forwarder.start()
            await result.record(started)
            return started
        }

        for index in expected.indices {
            await gate.waitUntilEventCount(index + 1)
            let currentResult = await result.current()
            XCTAssertNil(currentResult, file: file, line: line)
            let currentEvents = await gate.recordedLifecycleEvents()
            XCTAssertEqual(
                currentEvents,
                Array(expected.prefix(index + 1)),
                file: file,
                line: line
            )
            await gate.releaseNext()
        }

        let didStart = await startTask.value
        let finalEvents = await gate.recordedLifecycleEvents()
        XCTAssertTrue(didStart, file: file, line: line)
        XCTAssertEqual(finalEvents, expected, file: file, line: line)
        XCTAssertEqual(observer.beginCount, 1, file: file, line: line)

        await forwarder.stop()
        await source.waitUntilTerminalForTesting()
        XCTAssertEqual(observer.stopCount, 1, file: file, line: line)
    }

    private func activeBootstrap() -> AtlasIOSLifecycleBootstrap {
        AtlasIOSLifecycleBootstrap(
            scenes: [scene: .foregroundActive],
            applicationState: .background,
            protectedDataAvailable: true
        )
    }

    private func lockedFlow() -> AtlasLockedShellUnlockFlowState {
        AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                serviceStatus: .unavailable,
                canRequestUnlock: false
            ),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: nil,
                status: .locked
            ),
            isUnlockPanelPresented: false
        )
    }

    private func collect(
        _ stream: AsyncStream<AtlasVaultPlatformLifecycleEventDelivery>
    ) async -> [RecordedLifecycleDelivery] {
        var deliveries: [RecordedLifecycleDelivery] = []
        for await delivery in stream {
            deliveries.append(record(delivery))
        }
        return deliveries
    }

    private func record(
        _ delivery: AtlasVaultPlatformLifecycleEventDelivery
    ) -> RecordedLifecycleDelivery {
        switch delivery {
        case let .event(event):
            return .event(event)
        case let .readinessBoundary(identifier):
            return .readinessBoundary(identifier)
        }
    }

    private func record(
        _ delivery: AtlasVaultPlatformLifecycleEventDelivery?
    ) -> RecordedLifecycleDelivery? {
        delivery.map(record)
    }

    private static func source(named name: String) throws -> String {
        try String(
            contentsOf: appleRoot()
                .appendingPathComponent("Sources/AtlasUI")
                .appendingPathComponent(name),
            encoding: .utf8
        )
    }

    private static func packageSource() throws -> String {
        try String(
            contentsOf: appleRoot().appendingPathComponent("Package.swift"),
            encoding: .utf8
        )
    }

    private static func appleRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}

private enum RecordedLifecycleDelivery: Equatable {
    case event(AtlasVaultLifecycleEvent)
    case readinessBoundary(UUID)
}

@MainActor
private final class IOSLifecycleObserverFake:
    AtlasIOSLifecycleSystemObserving
{
    let bootstrap: AtlasIOSLifecycleBootstrap
    let immediateSignal: AtlasIOSLifecycleSignal?
    private(set) var beginCount = 0
    private(set) var stopCount = 0
    private(set) var deliveryCount = 0
    private var continuation:
        AsyncStream<AtlasIOSLifecycleSystemDelivery>.Continuation?
    private var beginWaiters: [CheckedContinuation<Void, Never>] = []

    init(
        bootstrap: AtlasIOSLifecycleBootstrap,
        immediateSignal: AtlasIOSLifecycleSignal? = nil
    ) {
        self.bootstrap = bootstrap
        self.immediateSignal = immediateSignal
    }

    func beginObservation() -> AtlasIOSLifecycleSystemObservation {
        beginCount += 1
        let pair = AsyncStream<AtlasIOSLifecycleSystemDelivery>.makeStream(
            bufferingPolicy: .unbounded
        )
        continuation = pair.continuation
        if let immediateSignal {
            emit(.signal(immediateSignal))
        }
        let waiters = beginWaiters
        beginWaiters.removeAll()
        waiters.forEach { $0.resume() }
        return AtlasIOSLifecycleSystemObservation(
            bootstrap: bootstrap,
            deliveries: pair.stream,
            requestReadinessBoundary: { [weak self] identifier in
                await self?.emit(.readinessBoundary(identifier))
            }
        )
    }

    func stopObservation() {
        guard stopCount == 0 else {
            return
        }
        stopCount += 1
        continuation?.finish()
        continuation = nil
        deliveryCount = 0
    }

    func emit(_ signal: AtlasIOSLifecycleSignal) {
        emit(.signal(signal))
    }

    func finish() {
        continuation?.finish()
    }

    func waitUntilBeginCount(_ count: Int) async {
        guard beginCount < count else {
            return
        }
        await withCheckedContinuation { continuation in
            beginWaiters.append(continuation)
        }
    }

    private func emit(_ delivery: AtlasIOSLifecycleSystemDelivery) {
        guard let continuation else {
            return
        }
        deliveryCount += 1
        continuation.yield(delivery)
    }
}

private actor IOSLifecycleHostGate {
    private var recordedEvents: [AtlasVaultLifecycleEvent] = []
    private var releases: [CheckedContinuation<Void, Never>] = []
    private var eventWaiters:
        [Int: [CheckedContinuation<Void, Never>]] = [:]

    func recordAndSuspend(_ event: AtlasVaultLifecycleEvent) async {
        recordedEvents.append(event)
        let count = recordedEvents.count
        let readyKeys = eventWaiters.keys.filter { $0 <= count }
        let readyWaiters = readyKeys.flatMap {
            eventWaiters.removeValue(forKey: $0) ?? []
        }
        for waiter in readyWaiters {
            waiter.resume()
        }
        await withCheckedContinuation { continuation in
            releases.append(continuation)
        }
    }

    func waitUntilEventCount(_ count: Int) async {
        guard recordedEvents.count < count else {
            return
        }
        await withCheckedContinuation { continuation in
            eventWaiters[count, default: []].append(continuation)
        }
    }

    func releaseNext() {
        guard !releases.isEmpty else {
            return
        }
        releases.removeFirst().resume()
    }

    func recordedLifecycleEvents() -> [AtlasVaultLifecycleEvent] {
        recordedEvents
    }
}

private actor IOSLifecycleBoolResult {
    private var value: Bool?

    func record(_ value: Bool) {
        self.value = value
    }

    func current() -> Bool? {
        value
    }
}

private actor IOSLifecycleForwarderHostFake: AtlasVaultProductionHosting {
    private let flow: AtlasLockedShellUnlockFlowState
    private let lifecycleGate: IOSLifecycleHostGate

    init(
        flow: AtlasLockedShellUnlockFlowState,
        lifecycleGate: IOSLifecycleHostGate
    ) {
        self.flow = flow
        self.lifecycleGate = lifecycleGate
    }

    func start() async throws -> AtlasLockedShellUnlockFlowState { flow }
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
    ) async -> AtlasLockedShellUnlockFlowState {
        await lifecycleGate.recordAndSuspend(event)
        return flow
    }
}
