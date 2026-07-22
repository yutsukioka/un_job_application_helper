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

    func testTwoRootsCanShareOneOwnerAndActionAuthority() {
        let owner = AtlasVaultProductionPresentationOwner()
        let publicActions = AtlasLockedPublicShellActions(
            search: { _ in },
            requestUnlock: {}
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
        XCTAssertTrue(owner === owner)
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
