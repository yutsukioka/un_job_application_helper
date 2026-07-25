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

    func testCreationEnabledRootIsExplicitAndCompatibilityRemainsDisabled()
        throws
    {
        let source = try Self.source(
            named: "AtlasVaultProductionRootView.swift"
        )

        for required in [
            "AtlasLocalVaultCreationContext",
            "Create Local Vault",
            "@ObservedObject",
            ".sheet(",
            "creationContext = nil",
            "flowState.mode == .lockedPublic",
            "flowState.publicShell.vaultStatus == .noVault",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "static let disabled",
            "AtlasLocalVaultCreationCoordinator(",
            ".task",
            ".onAppear",
            "Keychain",
            "FileManager",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testCreationSheetIsLocalAndCanTransferToAnotherRoot()
        throws
    {
        let source = try Self.source(
            named: "AtlasVaultProductionRootView.swift"
        )

        for required in [
            "@State private var isCreationPresented = false",
            "AtlasLocalVaultCreationPresentationClaim()",
            "creationActions.claimPresentation(",
            "creationActions.releasePresentation(",
            "creationActions.ownsPresentation(",
            "isCreationPresented = isPresented",
            "isCreationPresented\n"
                + "                        && creationActions"
                + ".ownsPresentation",
            ".onChange(of: creationOwner.presentation)",
            "\"Continue Local Vault Setup\"",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        XCTAssertFalse(
            source.contains(
                "isPresented: Binding(\n"
                    + "                get: {\n"
                    + "                    creationOwner.presentation"
            )
        )
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
        let currentBranchDiff = "origin/master" + "...HEAD"

        XCTAssertFalse(source.contains(obsoleteTestName))
        XCTAssertFalse(source.contains(legacyRootAssertion))
        XCTAssertFalse(source.contains(directReferenceAssertion))
        XCTAssertFalse(source.contains(currentEntryLoad))
        XCTAssertFalse(source.contains(currentBranchDiff))
        XCTAssertNil(
            source.range(
                of: #""[0-9a-f]{40}""#,
                options: .regularExpression
            )
        )
    }

    func testPhase2D57IntroductionHasExactReviewedScopeAndDidNotWireAppEntry()
        throws
    {
        let shallowRepository = try Self.git(
            "rev-parse",
            "--is-shallow-repository"
        )
        guard shallowRepository == "false" else {
            throw XCTSkip(
                "Historical Phase 2D-57 scope assertions require complete Git history"
            )
        }
        let introduction = try Self.phase2D57IntroductionCommit()
        _ = try Self.git(
            "merge-base",
            "--is-ancestor",
            introduction,
            "HEAD"
        )
        let parent = try Self.firstParent(of: introduction)
        let paths = try Self.changedPaths(
            from: parent,
            to: introduction
        )
        let expected: Set<String> = [
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionCompositionHarness.swift",
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionPresentationOwner.swift",
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionRootView.swift",
            "apps/apple/Tests/AtlasUITests/AtlasVaultProductionCompositionHarnessTests.swift",
            "apps/apple/Tests/AtlasUITests/AtlasVaultProductionPresentationOwnerTests.swift",
            "apps/apple/Tests/AtlasUITests/AtlasVaultProductionRootViewTests.swift",
            "docs/architecture/phase2d57_mainactor_owner_and_composition_harness.md",
        ]

        XCTAssertEqual(paths, expected)
        XCTAssertFalse(
            paths.contains(
                "apps/apple/AtlasIOSHost/AtlasIOSHost/AtlasIOSHostApp.swift"
            )
        )
        XCTAssertFalse(
            paths.contains(
                "apps/apple/Sources/AtlasUI/AtlasReferenceCaptureView.swift"
            )
        )
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

    private static func phase2D57IntroductionCommit() throws -> String {
        let commits = try git(
            "log",
            "--reverse",
            "--format=%H",
            "--diff-filter=A",
            "--",
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionRootView.swift"
        )
        return try XCTUnwrap(
            commits.split(separator: "\n").first.map(String.init)
        )
    }

    private static func firstParent(of commit: String) throws -> String {
        try git("rev-parse", "\(commit)^1")
    }

    private static func changedPaths(
        from parent: String,
        to introduction: String
    ) throws -> Set<String> {
        Set(
            try git(
                "diff",
                "--name-only",
                parent,
                introduction,
                "--"
            )
            .split(separator: "\n")
            .map(String.init)
        )
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
            throw RootViewTestError.command(output)
        }
        return output
        #else
        throw XCTSkip("Git-backed Phase 2D-57 scope assertions require macOS")
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

private enum RootViewTestError: Error {
    case command(String)
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
