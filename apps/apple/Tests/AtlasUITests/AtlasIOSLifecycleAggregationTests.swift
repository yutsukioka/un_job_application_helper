import Foundation
import XCTest
@testable import AtlasUI

final class AtlasIOSLifecycleAggregationTests: XCTestCase {
    private let first = AtlasIOSSceneIdentifier("FAKE_SCENE_ONE")
    private let second = AtlasIOSSceneIdentifier("FAKE_SCENE_TWO")

    func testConstructionIsEmptyDeterministicAndRedacted() {
        let aggregator = AtlasIOSLifecycleAggregator()

        XCTAssertTrue(aggregator.description.contains("<redacted>"))
        XCTAssertFalse(aggregator.description.contains("FAKE_SCENE"))
        XCTAssertEqual(aggregator.debugDescription, aggregator.description)
    }

    func testBootstrapEmitsProtectedDataBeforeEveryInitialPhase() {
        var active = AtlasIOSLifecycleAggregator()
        XCTAssertEqual(
            active.bootstrap(
                bootstrap(
                    scenes: [first: .foregroundActive],
                    application: .background,
                    protectedDataAvailable: false
                )
            ),
            [.protectedDataBecameUnavailable, .didBecomeActive]
        )

        var inactive = AtlasIOSLifecycleAggregator()
        XCTAssertEqual(
            inactive.bootstrap(
                bootstrap(
                    scenes: [first: .foregroundInactive],
                    application: .active,
                    protectedDataAvailable: true
                )
            ),
            [.protectedDataBecameAvailable, .willResignActive]
        )

        var background = AtlasIOSLifecycleAggregator()
        XCTAssertEqual(
            background.bootstrap(
                bootstrap(
                    scenes: [first: .background],
                    application: .active,
                    protectedDataAvailable: true
                )
            ),
            [.protectedDataBecameAvailable, .didEnterBackground]
        )
    }

    func testNoSceneBootstrapUsesApplicationFallback() {
        for (state, expected) in [
            (AtlasIOSApplicationLifecycleState.active, AtlasVaultLifecycleEvent.didBecomeActive),
            (.inactive, .willResignActive),
            (.background, .didEnterBackground),
        ] {
            var aggregator = AtlasIOSLifecycleAggregator()
            XCTAssertEqual(
                aggregator.bootstrap(
                    bootstrap(
                        scenes: [:],
                        application: state,
                        protectedDataAvailable: true
                    )
                ),
                [.protectedDataBecameAvailable, expected]
            )
        }
    }

    func testBootstrapAndProtectedDataDuplicatesAreSuppressed() {
        var aggregator = AtlasIOSLifecycleAggregator()
        let value = bootstrap(
            scenes: [first: .foregroundActive],
            application: .active,
            protectedDataAvailable: true
        )

        XCTAssertEqual(
            aggregator.bootstrap(value),
            [.protectedDataBecameAvailable, .didBecomeActive]
        )
        XCTAssertEqual(aggregator.bootstrap(value), [])
        XCTAssertEqual(
            aggregator.consume(.protectedDataBecameAvailable),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.protectedDataBecameUnavailable),
            [.protectedDataBecameUnavailable]
        )
        XCTAssertEqual(
            aggregator.consume(.protectedDataBecameUnavailable),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.protectedDataBecameAvailable),
            [.protectedDataBecameAvailable]
        )
    }

    func testMultipleActiveScenesDoNotEmitFalseInactiveOrBackground() {
        var aggregator = bootstrapped(
            scenes: [first: .foregroundActive, second: .foregroundActive]
        )

        XCTAssertEqual(
            aggregator.consume(.sceneWillResignActive(first)),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.sceneDidEnterBackground(first)),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.sceneWillResignActive(second)),
            [.willResignActive]
        )
    }

    func testLastForegroundSceneEnteringBackgroundEmitsOnce() {
        var aggregator = bootstrapped(
            scenes: [first: .foregroundInactive, second: .foregroundInactive]
        )

        XCTAssertEqual(
            aggregator.consume(.sceneDidEnterBackground(first)),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.sceneDidEnterBackground(first)),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.sceneDidEnterBackground(second)),
            [.didEnterBackground]
        )
    }

    func testForegroundEntryWaitsForActivation() {
        var aggregator = bootstrapped(scenes: [first: .background])

        XCTAssertEqual(
            aggregator.consume(.sceneWillEnterForeground(first)),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.sceneWillEnterForeground(first)),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.sceneDidBecomeActive(first)),
            [.didBecomeActive]
        )
    }

    func testSceneConnectionsAggregateWithoutDuplicateEvents() {
        var aggregator = bootstrapped(scenes: [:], application: .background)

        XCTAssertEqual(
            aggregator.consume(
                .sceneConnected(first, state: .foregroundInactive)
            ),
            []
        )
        XCTAssertEqual(
            aggregator.consume(
                .sceneConnected(second, state: .foregroundActive)
            ),
            [.didBecomeActive]
        )
        XCTAssertEqual(
            aggregator.consume(
                .sceneConnected(second, state: .foregroundActive)
            ),
            []
        )
    }

    func testSceneDisconnectionUsesRemainingScenesThenFallback() {
        var aggregator = bootstrapped(
            scenes: [first: .foregroundActive, second: .foregroundInactive],
            application: .background
        )

        XCTAssertEqual(
            aggregator.consume(.sceneDisconnected(first)),
            [.willResignActive]
        )
        XCTAssertEqual(
            aggregator.consume(.sceneDisconnected(first)),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.sceneDisconnected(second)),
            [.didEnterBackground]
        )
    }

    func testApplicationSignalsAreFallbackOnly() {
        var withScene = bootstrapped(
            scenes: [first: .foregroundActive],
            application: .active
        )
        XCTAssertEqual(
            withScene.consume(.applicationWillResignActive),
            []
        )
        XCTAssertEqual(
            withScene.consume(.applicationDidEnterBackground),
            []
        )

        var withoutScenes = bootstrapped(
            scenes: [:],
            application: .background
        )
        XCTAssertEqual(
            withoutScenes.consume(.applicationDidBecomeActive),
            [.didBecomeActive]
        )
        XCTAssertEqual(
            withoutScenes.consume(.applicationDidBecomeActive),
            []
        )
        XCTAssertEqual(
            withoutScenes.consume(.applicationWillResignActive),
            [.willResignActive]
        )
        XCTAssertEqual(
            withoutScenes.consume(.applicationDidEnterBackground),
            [.didEnterBackground]
        )
    }

    func testTerminationIsExactlyOnceAndRejectsLaterSignals() {
        var aggregator = bootstrapped(scenes: [first: .foregroundActive])

        XCTAssertEqual(aggregator.consume(.willTerminate), [.willTerminate])
        XCTAssertEqual(aggregator.consume(.willTerminate), [])
        XCTAssertEqual(
            aggregator.consume(.sceneDidEnterBackground(first)),
            []
        )
        XCTAssertEqual(
            aggregator.consume(.protectedDataBecameUnavailable),
            []
        )
    }

    func testSceneChurnIsDeterministicAndDescriptionsAreRedacted() {
        let signals: [AtlasIOSLifecycleSignal] = [
            .sceneConnected(first, state: .foregroundInactive),
            .sceneDidBecomeActive(first),
            .sceneConnected(second, state: .foregroundActive),
            .sceneWillResignActive(first),
            .sceneDidEnterBackground(first),
            .sceneDisconnected(first),
            .sceneWillResignActive(second),
            .sceneDidEnterBackground(second),
            .sceneDisconnected(second),
        ]
        var left = bootstrapped(scenes: [:], application: .background)
        var right = bootstrapped(scenes: [:], application: .background)

        let leftOutput = signals.flatMap { left.consume($0) }
        let rightOutput = signals.flatMap { right.consume($0) }

        XCTAssertEqual(leftOutput, rightOutput)
        XCTAssertFalse(first.description.contains("FAKE_SCENE_ONE"))
        XCTAssertFalse(signals[0].description.contains("FAKE_SCENE_ONE"))
    }

    func testAggregationSourceIsPlatformAndPrivateDataFree() throws {
        let source = try Self.source(named: "AtlasIOSLifecycleAggregation.swift")

        for forbidden in [
            "import UIKit", "import AppKit", "UIApplication", "UIScene",
            "NotificationCenter", "Keychain", "SecItem", "FileManager",
            "URLSession", "UserDefaults", "AtlasVaultPrivateState",
            "vaultID", "passphrase", "recovery", "Codable",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private func bootstrap(
        scenes: [AtlasIOSSceneIdentifier: AtlasIOSSceneLifecycleState],
        application: AtlasIOSApplicationLifecycleState,
        protectedDataAvailable: Bool
    ) -> AtlasIOSLifecycleBootstrap {
        AtlasIOSLifecycleBootstrap(
            scenes: scenes,
            applicationState: application,
            protectedDataAvailable: protectedDataAvailable
        )
    }

    private func bootstrapped(
        scenes: [AtlasIOSSceneIdentifier: AtlasIOSSceneLifecycleState],
        application: AtlasIOSApplicationLifecycleState = .active,
        protectedDataAvailable: Bool = true
    ) -> AtlasIOSLifecycleAggregator {
        var aggregator = AtlasIOSLifecycleAggregator()
        _ = aggregator.bootstrap(
            bootstrap(
                scenes: scenes,
                application: application,
                protectedDataAvailable: protectedDataAvailable
            )
        )
        return aggregator
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
