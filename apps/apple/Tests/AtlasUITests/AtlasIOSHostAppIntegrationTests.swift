import Foundation
import XCTest
@testable import AtlasUI

final class AtlasIOSHostAppIntegrationTests: XCTestCase {
    func testActualAppEntryHasOneProcessDelegateAndLifecycleCallbacks()
        throws
    {
        let source = try Self.appEntrySource()

        XCTAssertEqual(
            source.occurrences(of: "UIApplicationDelegate"),
            2
        )
        XCTAssertTrue(
            source.contains(
                "private final class AtlasIOSHostProcessDelegate"
            )
        )
        XCTAssertTrue(
            source.contains(
                "let processOwner: AtlasIOSAppProcessOwner"
            )
        )
        XCTAssertEqual(
            source.occurrences(
                of: "ProcessInfo.processInfo.environment"
            ),
            1
        )
        XCTAssertEqual(source.occurrences(of: "processOwner.beginStart()"), 1)
        XCTAssertEqual(
            source.occurrences(of: "processOwner.beginTerminalStop()"),
            1
        )
        XCTAssertTrue(source.contains("didFinishLaunchingWithOptions"))
        XCTAssertTrue(source.contains("return true"))
        XCTAssertTrue(source.contains("applicationWillTerminate"))
    }

    func testSwiftUIAppUsesOneDelegateAdaptorAndSharedOwner() throws {
        let source = try Self.appEntrySource()

        XCTAssertEqual(source.occurrences(of: "@main"), 1)
        XCTAssertEqual(
            source.occurrences(of: "@UIApplicationDelegateAdaptor"),
            1
        )
        XCTAssertEqual(source.occurrences(of: "WindowGroup"), 1)
        XCTAssertEqual(
            source.occurrences(of: "AtlasIOSIntegratedAppRootView("),
            1
        )
        XCTAssertTrue(
            source.contains("owner: processDelegate.processOwner")
        )

        for forbidden in [
            "AtlasRootView",
            "AtlasReferenceCaptureView",
            "ATLAS_REFERENCE_CAPTURE",
            ".task",
            ".onAppear",
            ".onDisappear",
            "scenePhase",
            "NotificationCenter",
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
            "savedSearch",
            "savedJob",
            "tracker",
            "applicationNote",
            "profileSnippet",
            "draftMetadata",
            "generatedDocument",
            "AtlasIOSProcessLifecycleEventSource",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testProductionConfigurationPolicyLivesInLazyOwnerSource()
        throws
    {
        let source = try Self.processOwnerSource()

        for required in [
            "ATLAS_API_BASE_URL",
            "http://127.0.0.1:8765",
            "publicSearchLimit: 50",
            "unlockTimeout: .seconds(30)",
            "lifecycleLockPolicy: .immediate",
            "lockOnInactive: true",
            "AtlasIOSProcessLifecycleEventSource()",
            "makeUnwiredProductionLike",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }

        for forbidden in [
            "AtlasAPIClient.defaultBaseURL",
            "AtlasAPIClient()",
            "UserDefaults",
            "192.168.",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testRoutePlanRemainsSoleCaptureParserAndModesRemainAvailable()
        throws
    {
        let appSource = try Self.appEntrySource()
        let ownerSource = try Self.processOwnerSource()
        let planSource = try Self.source(named: "AtlasIOSAppEntryIntegrationPlan")

        XCTAssertFalse(appSource.contains("ATLAS_REFERENCE_CAPTURE"))
        XCTAssertFalse(ownerSource.contains("rawCaptureMode"))
        XCTAssertTrue(planSource.contains("ATLAS_REFERENCE_CAPTURE"))
        XCTAssertFalse(AtlasReferenceCaptureMode.allCases.isEmpty)
    }

    func testNewPhaseTestsContainNoMergeUnstableHistoryPins() throws {
        let combined = try [
            "AtlasIOSAppProcessOwnerTests",
            "AtlasIOSIntegratedAppRootViewTests",
            "AtlasIOSHostAppIntegrationTests",
        ]
        .map { try Self.testSource(named: $0) }
        .joined(separator: "\n")
        let currentBranchDiff = "origin/master" + "...HEAD"
        let currentAppBlob = "HEAD:" + "apps/apple/AtlasIOSHost"
        let blobPin = "blob" + " SHA"

        XCTAssertFalse(combined.contains(currentBranchDiff))
        XCTAssertFalse(combined.contains(currentAppBlob))
        XCTAssertFalse(combined.contains(blobPin))
        XCTAssertNil(
            combined.range(
                of: #""[0-9a-f]{40}""#,
                options: .regularExpression
            )
        )
    }

    func testReferenceCaptureAndProtectedProjectSourcesStillExist() {
        for relativePath in [
            "Sources/AtlasUI/AtlasReferenceCaptureView.swift",
            "Sources/AtlasUI/SearchScreen.swift",
            "Package.swift",
            "AtlasIOSHost/AtlasIOSHost.xcodeproj/project.pbxproj",
        ] {
            XCTAssertTrue(
                FileManager.default.fileExists(
                    atPath: Self.appleRoot
                        .appendingPathComponent(relativePath)
                        .path
                ),
                relativePath
            )
        }
    }

    private static func appEntrySource() throws -> String {
        try String(
            contentsOf: appleRoot
                .appendingPathComponent("AtlasIOSHost/AtlasIOSHost")
                .appendingPathComponent("AtlasIOSHostApp.swift"),
            encoding: .utf8
        )
    }

    private static func processOwnerSource() throws -> String {
        try source(named: "AtlasIOSAppProcessOwner")
    }

    private static func source(named name: String) throws -> String {
        try String(
            contentsOf: appleRoot
                .appendingPathComponent("Sources/AtlasUI")
                .appendingPathComponent("\(name).swift"),
            encoding: .utf8
        )
    }

    private static func testSource(named name: String) throws -> String {
        try String(
            contentsOf: appleRoot
                .appendingPathComponent("Tests/AtlasUITests")
                .appendingPathComponent("\(name).swift"),
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

private extension String {
    func occurrences(of needle: String) -> Int {
        components(separatedBy: needle).count - 1
    }
}
