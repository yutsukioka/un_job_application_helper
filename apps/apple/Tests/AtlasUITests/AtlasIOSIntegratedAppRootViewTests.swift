import Foundation
import SwiftUI
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSIntegratedAppRootViewTests: XCTestCase {
    func testRootCompilesWithOneInjectedObservedOwner() throws {
        let owner = makeOwner(route: .invalidReferenceCapture)
        let root = AtlasIOSIntegratedAppRootView(owner: owner)

        requireView(type(of: root))
        XCTAssertTrue(try observedOwner(in: root) === owner)
    }

    func testTwoRootsShareOneProcessOwnerWithoutStartingProduction()
        throws
    {
        let probe = IntegratedRootFactoryProbe()
        let owner = AtlasIOSAppProcessOwner(
            route: .production,
            productionFactory: {
                probe.calls += 1
                return IntegratedRootHarnessFake()
            }
        )

        let first = AtlasIOSIntegratedAppRootView(owner: owner)
        let second = AtlasIOSIntegratedAppRootView(owner: owner)

        XCTAssertTrue(try observedOwner(in: first) === owner)
        XCTAssertTrue(try observedOwner(in: second) === owner)
        XCTAssertEqual(probe.calls, 0)
        XCTAssertEqual(owner.presentation, .productionPending)
    }

    func testRootConstructionForEveryNonProductionRouteInvokesNoFactory()
        throws
    {
        for route in [
            AtlasIOSAppEntryRoute.referenceCapture(.search),
            .invalidReferenceCapture,
        ] {
            let probe = IntegratedRootFactoryProbe()
            let owner = AtlasIOSAppProcessOwner(
                route: route,
                productionFactory: {
                    probe.calls += 1
                    return IntegratedRootHarnessFake()
                }
            )

            let root = AtlasIOSIntegratedAppRootView(owner: owner)
            requireView(type(of: root))
            XCTAssertEqual(probe.calls, 0)
        }
    }

    func testReadyRootUsesRetainedProductionHarness() async throws {
        let harness = IntegratedRootHarnessFake()
        let owner = AtlasIOSAppProcessOwner(
            route: .production,
            productionFactory: { harness }
        )

        let presentation = await owner.start()
        XCTAssertEqual(presentation, .productionReady)
        let root = AtlasIOSIntegratedAppRootView(owner: owner)
        requireView(type(of: root))
        _ = root.body
        XCTAssertEqual(harness.startCalls, 1)
        XCTAssertEqual(harness.makeRootCalls, 1)
    }

    func testSourceRendersEveryFailClosedStateWithoutLifecycleOwnership()
        throws
    {
        let source = try Self.integratedRootSource()

        for required in [
            "@MainActor",
            "@ObservedObject",
            "AtlasReferenceCaptureView(mode: mode)",
            "Reference Capture Unavailable",
            "The requested reference-capture mode is invalid.",
            "productionPending",
            "productionStarting",
            "AtlasVaultProductionRootView",
            "productionUnavailable",
            "productionStopping",
            "stopped",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }

        for forbidden in [
            "@StateObject",
            ".task",
            ".onAppear",
            ".onDisappear",
            "scenePhase",
            "NotificationCenter",
            "AtlasRootView",
            "SearchViewModel",
            "AtlasAPIClient",
            "AtlasVaultRuntimeFacade",
            "AtlasVaultLifecycleCoordinator",
            "AtlasIOSProcessLifecycleEventSource",
            "Keychain",
            "SecItem",
            "FileManager",
            "URLSession",
            "UserDefaults",
            "NavigationStack",
            "NavigationLink",
            "AtlasVaultPrivateState",
            "savedSearch",
            "savedJob",
            "tracker",
            "applicationNote",
            "profileSnippet",
            "draftMetadata",
            "generatedDocument",
            "beginStart",
            "beginTerminalStop",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private func makeOwner(
        route: AtlasIOSAppEntryRoute
    ) -> AtlasIOSAppProcessOwner {
        AtlasIOSAppProcessOwner(
            route: route,
            productionFactory: {
                XCTFail("root construction invoked production")
                return IntegratedRootHarnessFake()
            }
        )
    }

    private func requireView<Content: View>(_ type: Content.Type) {}

    private func observedOwner(
        in root: AtlasIOSIntegratedAppRootView
    ) throws -> AtlasIOSAppProcessOwner {
        let wrapper = try XCTUnwrap(
            Mirror(reflecting: root).descendant("_owner")
                as? ObservedObject<AtlasIOSAppProcessOwner>
        )
        return wrapper.wrappedValue
    }

    private static func integratedRootSource() throws -> String {
        try String(
            contentsOf: appleRoot
                .appendingPathComponent("Sources/AtlasUI")
                .appendingPathComponent(
                    "AtlasIOSIntegratedAppRootView.swift"
                ),
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

@MainActor
private final class IntegratedRootFactoryProbe {
    var calls = 0
}

@MainActor
private final class IntegratedRootHarnessFake:
    AtlasIOSAppProcessHarness
{
    private let owner = AtlasVaultProductionPresentationOwner()
    private(set) var startCalls = 0
    private(set) var makeRootCalls = 0

    func start() async throws -> AtlasLockedShellUnlockFlowState {
        startCalls += 1
        return owner.flowState
    }

    func stop() async -> AtlasLockedShellUnlockFlowState {
        owner.flowState
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
