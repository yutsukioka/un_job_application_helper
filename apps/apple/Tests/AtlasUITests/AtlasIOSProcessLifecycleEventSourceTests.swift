import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSProcessLifecycleEventSourceTests: XCTestCase {
    private let scene = AtlasIOSSceneIdentifier("FAKE_SOURCE_SCENE")

    func testConstructionStartsNoObservationOrSystemRead() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)

        XCTAssertEqual(observer.beginCount, 0)
        XCTAssertEqual(observer.stopCount, 0)
        XCTAssertTrue(source.description.contains("<redacted>"))
        XCTAssertFalse(source.description.contains("FAKE_SOURCE_SCENE"))
        let hasProducer = await source.hasRetainedProducerTaskForTesting()
        XCTAssertFalse(hasProducer)
    }

    func testFirstSubscriptionStartsOnceAndSecondFinishesImmediately() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)

        let first = await source.events()
        let second = await source.events()
        var secondIterator = second.makeAsyncIterator()
        let secondEvent = await secondIterator.next()

        XCTAssertEqual(observer.beginCount, 1)
        XCTAssertNil(secondEvent)
        observer.finish()
        let events = await collect(first)
        XCTAssertEqual(
            events,
            [.protectedDataBecameAvailable, .didBecomeActive]
        )
        await source.waitUntilTerminalForTesting()
        XCTAssertEqual(observer.stopCount, 1)
    }

    func testBootstrapPrecedesImmediateBufferedLiveSignal() async {
        let observer = IOSLifecycleObserverFake(
            bootstrap: activeBootstrap(),
            immediateSignal: .protectedDataBecameUnavailable
        )
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let stream = await source.events()
        observer.finish()

        let events = await collect(stream)
        XCTAssertEqual(
            events,
            [
                .protectedDataBecameAvailable,
                .didBecomeActive,
                .protectedDataBecameUnavailable,
            ]
        )
    }

    func testSourcePreservesReducerOrderAndSuppressesDuplicates() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let stream = await source.events()

        observer.emit(.sceneWillResignActive(scene))
        observer.emit(.sceneWillResignActive(scene))
        observer.emit(.sceneDidEnterBackground(scene))
        observer.emit(.sceneDidEnterBackground(scene))
        observer.finish()

        let events = await collect(stream)
        XCTAssertEqual(
            events,
            [
                .protectedDataBecameAvailable,
                .didBecomeActive,
                .willResignActive,
                .didEnterBackground,
            ]
        )
    }

    func testInputCompletionStopsObservationAndFinishesOutput() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let stream = await source.events()

        observer.finish()
        let events = await collect(stream)
        XCTAssertEqual(
            events,
            [.protectedDataBecameAvailable, .didBecomeActive]
        )
        await source.waitUntilTerminalForTesting()

        XCTAssertEqual(observer.stopCount, 1)
        let isTerminal = await source.isTerminalForTesting()
        let hasProducer = await source.hasRetainedProducerTaskForTesting()
        XCTAssertTrue(isTerminal)
        XCTAssertFalse(hasProducer)
    }

    func testTerminationStopsObservationFinishesAndIgnoresLaterInput() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let stream = await source.events()

        observer.emit(.willTerminate)
        observer.emit(.protectedDataBecameUnavailable)
        observer.emit(.willTerminate)

        let events = await collect(stream)
        XCTAssertEqual(
            events,
            [
                .protectedDataBecameAvailable,
                .didBecomeActive,
                .willTerminate,
            ]
        )
        await source.waitUntilTerminalForTesting()
        XCTAssertEqual(observer.stopCount, 1)
        let hasProducer = await source.hasRetainedProducerTaskForTesting()
        XCTAssertFalse(hasProducer)
    }

    func testConsumerCancellationCancelsAndDrainsProducer() async {
        let observer = IOSLifecycleObserverFake(bootstrap: activeBootstrap())
        let source = AtlasIOSProcessLifecycleEventSource(observer: observer)
        let stream = await source.events()
        let consumer = Task {
            for await _ in stream {}
        }

        await observer.waitUntilBeginCount(1)
        consumer.cancel()
        await consumer.value
        await source.waitUntilTerminalForTesting()

        XCTAssertEqual(observer.stopCount, 1)
        let hasProducer = await source.hasRetainedProducerTaskForTesting()
        XCTAssertFalse(hasProducer)
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
        let stream = await source.events()

        observer.emit(.applicationDidBecomeActive)
        observer.emit(.applicationWillResignActive)
        observer.emit(.protectedDataBecameUnavailable)
        observer.emit(.protectedDataBecameAvailable)
        observer.emit(.applicationDidEnterBackground)
        observer.finish()

        let events = await collect(stream)
        XCTAssertEqual(
            events,
            [
                .protectedDataBecameAvailable,
                .didEnterBackground,
                .didBecomeActive,
                .willResignActive,
                .protectedDataBecameUnavailable,
                .protectedDataBecameAvailable,
                .didEnterBackground,
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
            "#if canImport(UIKit)",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        XCTAssertFalse(source.contains("import AppKit"))
    }

    func testSourceHasNoUnsafeOrPrivateCoupling() throws {
        let source = try Self.source(
            named: "AtlasIOSProcessLifecycleEventSource.swift"
        )
        for forbidden in [
            "AtlasVaultPrivateState", "vaultID", "passphrase", "recovery",
            "Keychain", "SecItem", "FileManager", "URLSession",
            "UserDefaults", "Task.detached", "nonisolated(unsafe)",
            "@unchecked Sendable", "Thread.sleep", "usleep",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }

        let package = try Self.packageSource()
        XCTAssertTrue(package.contains(".iOS(.v18)"))
    }

    private func activeBootstrap() -> AtlasIOSLifecycleBootstrap {
        AtlasIOSLifecycleBootstrap(
            scenes: [scene: .foregroundActive],
            applicationState: .background,
            protectedDataAvailable: true
        )
    }

    private func collect(
        _ stream: AsyncStream<AtlasVaultLifecycleEvent>
    ) async -> [AtlasVaultLifecycleEvent] {
        var events: [AtlasVaultLifecycleEvent] = []
        for await event in stream {
            events.append(event)
        }
        return events
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

@MainActor
private final class IOSLifecycleObserverFake:
    AtlasIOSLifecycleSystemObserving
{
    let bootstrap: AtlasIOSLifecycleBootstrap
    let immediateSignal: AtlasIOSLifecycleSignal?
    private(set) var beginCount = 0
    private(set) var stopCount = 0
    private var continuation: AsyncStream<AtlasIOSLifecycleSignal>.Continuation?
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
        let pair = AsyncStream<AtlasIOSLifecycleSignal>.makeStream(
            bufferingPolicy: .unbounded
        )
        continuation = pair.continuation
        if let immediateSignal {
            pair.continuation.yield(immediateSignal)
        }
        let waiters = beginWaiters
        beginWaiters.removeAll()
        waiters.forEach { $0.resume() }
        return AtlasIOSLifecycleSystemObservation(
            bootstrap: bootstrap,
            signals: pair.stream
        )
    }

    func stopObservation() {
        guard stopCount == 0 else {
            return
        }
        stopCount += 1
        continuation?.finish()
        continuation = nil
    }

    func emit(_ signal: AtlasIOSLifecycleSignal) {
        continuation?.yield(signal)
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
}
