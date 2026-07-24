import Foundation
import SwiftUI
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultProductionRootViewTests: XCTestCase {
    private static let fakeQuery = "FAKE_PHASE_2D57_ROOT_QUERY_DO_NOT_LOG"

    func testRootCompilesAsSwiftUIViewAndConstructionInvokesNoAction() {
        let owner = AtlasVaultProductionPresentationOwner()
        let publicActions = AtlasLockedPublicShellActions(
            search: { _ in XCTFail("root construction invoked search") },
            requestUnlock: { XCTFail("root construction invoked unlock") }
        )
        let unlockActions = AtlasExplicitUnlockViewActions(
            select: { _ in XCTFail("root construction invoked selection") },
            submit: { _ in
                XCTFail("root construction invoked submit")
                return .failed
            },
            cancel: { XCTFail("root construction invoked cancel") },
            didDisappear: {
                XCTFail("root construction invoked disappearance")
            }
        )

        let root = AtlasVaultProductionRootView(
            owner: owner,
            publicShellActions: publicActions,
            unlockActions: unlockActions
        )

        requireView(type(of: root))
        _ = root.body
        XCTAssertEqual(owner.flowState.mode, .lockedPublic)
    }

    func testTwoRootsCanShareOneOwnerAndActionAuthority() async throws {
        let owner = AtlasVaultProductionPresentationOwner()
        let recorder = RootActionRecorder()
        let publicActions = AtlasLockedPublicShellActions(
            search: { _ in },
            requestUnlock: { await recorder.recordUnlockRequest() }
        )
        let unlockActions = AtlasExplicitUnlockViewActions(
            select: { _ in },
            submit: { _ in .failed },
            cancel: {},
            didDisappear: {}
        )

        let first = AtlasVaultProductionRootView(
            owner: owner,
            publicShellActions: publicActions,
            unlockActions: unlockActions
        )
        let second = AtlasVaultProductionRootView(
            owner: owner,
            publicShellActions: publicActions,
            unlockActions: unlockActions
        )

        requireView(type(of: first))
        requireView(type(of: second))
        XCTAssertTrue(try observedOwner(in: first) === owner)
        XCTAssertTrue(try observedOwner(in: second) === owner)

        let sharedFlow = AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                vaultStatus: .noVault,
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
        let resetAccepted = await owner.resetPresentation(
            to: sharedFlow,
            generation: AtlasVaultProductionHostGeneration()
        )
        XCTAssertTrue(resetAccepted)

        let firstBody = first.body
        let secondBody = second.body
        XCTAssertEqual(try flowState(in: firstBody), sharedFlow)
        XCTAssertEqual(try flowState(in: secondBody), sharedFlow)
        let firstPublicActions = try embeddedPublicActions(in: firstBody)
        let secondPublicActions = try embeddedPublicActions(in: secondBody)
        await firstPublicActions.requestUnlock()
        await secondPublicActions.requestUnlock()
        let requestCount = await recorder.unlockRequestCount()
        XCTAssertEqual(requestCount, 2)
    }

    func testRootSourceIsThinObservedOwnerLockedFlowComposition() throws {
        let source = try Self.source(
            named: "AtlasVaultProductionRootView.swift"
        )

        XCTAssertTrue(source.contains("@MainActor"))
        XCTAssertTrue(source.contains("public struct AtlasVaultProductionRootView: View"))
        XCTAssertTrue(source.contains("@ObservedObject"))
        XCTAssertTrue(source.contains("owner.flowState"))
        XCTAssertTrue(source.contains("AtlasLockedShellUnlockFlowView("))
        XCTAssertTrue(source.contains("publicShellActions: publicShellActions"))
        XCTAssertTrue(source.contains("unlockActions: unlockActions"))

        for forbidden in [
            "@StateObject",
            ".task",
            ".onAppear",
            ".onDisappear",
            "scenePhase",
            "NotificationCenter",
            "@main",
            "WindowGroup",
            "AtlasRootView",
            "SearchViewModel",
            "AtlasAPIClient",
            "AtlasVaultRuntimeFacade",
            "AtlasVaultLifecycleCoordinator",
            "Keychain",
            "SecItem",
            "FileManager",
            "URLSession",
            "UserDefaults",
            "NavigationStack",
            "NavigationLink",
            "AtlasVaultPrivateState",
            "AtlasVaultHydratedState",
            "AtlasVaultPrivatePresentationState",
            "savedSearch",
            "savedJob",
            "applicationNote",
            "profileSnippet",
            "draftMetadata",
            Self.fakeQuery,
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testHistoricalScopeAssertionsDoNotPinCurrentAppEntry() throws {
        let source = try String(
            contentsOf: URL(fileURLWithPath: #filePath),
            encoding: .utf8
        )
        let obsoleteTestName = [
            "testActualAppEntryRemainsUnwired",
            "AndUsesExistingRoot",
        ].joined()
        let legacyRootAssertion = [
            "XCTAssertTrue(entry.contains(\"Atlas",
            "RootView()\"))",
        ].joined()
        let directReferenceAssertion = [
            "XCTAssertTrue(entry.contains(\"ATLAS_",
            "REFERENCE_CAPTURE\"))",
        ].joined()
        let currentEntryLoad = [
            "contentsOf: Self.",
            "appleRoot()",
        ].joined()

        XCTAssertFalse(source.contains(obsoleteTestName))
        XCTAssertFalse(source.contains(legacyRootAssertion))
        XCTAssertFalse(source.contains(directReferenceAssertion))
        XCTAssertFalse(source.contains(currentEntryLoad))
    }

    func testActualAppEntryRemainsUnwiredAndUsesExistingRoot() throws {
        let entry = try String(
            contentsOf: Self.appleRoot()
                .appendingPathComponent(
                    "AtlasIOSHost/AtlasIOSHost/AtlasIOSHostApp.swift"
                ),
            encoding: .utf8
        )

        XCTAssertTrue(entry.contains("AtlasRootView()"))
        XCTAssertTrue(entry.contains("ATLAS_REFERENCE_CAPTURE"))
        for forbidden in [
            "AtlasVaultProductionRootView",
            "AtlasVaultProductionCompositionHarness",
            "AtlasVaultProductionCompositionFactory",
            "AtlasVaultProductionPresentationOwner",
        ] {
            XCTAssertFalse(entry.contains(forbidden), forbidden)
        }
    }

    private func requireView<Content: View>(_ type: Content.Type) {}

    private func observedOwner(
        in root: AtlasVaultProductionRootView
    ) throws -> AtlasVaultProductionPresentationOwner {
        let child = Mirror(reflecting: root).children.first {
            $0.label == "_owner"
        }
        let storage = try XCTUnwrap(
            child?.value
                as? ObservedObject<AtlasVaultProductionPresentationOwner>
        )
        return storage.wrappedValue
    }

    private func flowState<Content: View>(
        in view: Content
    ) throws -> AtlasLockedShellUnlockFlowState {
        let child = Mirror(reflecting: view).children.first {
            $0.label == "state"
        }
        return try XCTUnwrap(
            child?.value as? AtlasLockedShellUnlockFlowState
        )
    }

    private func embeddedPublicActions<Content: View>(
        in view: Content
    ) throws -> AtlasLockedPublicShellActions {
        let child = Mirror(reflecting: view).children.first {
            $0.label == "publicShellActions"
        }
        return try XCTUnwrap(
            child?.value as? AtlasLockedPublicShellActions
        )
    }

    private static func source(named name: String) throws -> String {
        try String(
            contentsOf: appleRoot()
                .appendingPathComponent("Sources/AtlasUI")
                .appendingPathComponent(name),
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

private actor RootActionRecorder {
    private var unlockRequests = 0

    func recordUnlockRequest() {
        unlockRequests += 1
    }

    func unlockRequestCount() -> Int {
        unlockRequests
    }
}
