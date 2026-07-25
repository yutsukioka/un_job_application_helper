import Foundation
import XCTest

final class AtlasIOSHostAppIntegrationTests: XCTestCase {
    func testActualAppEntryUsesProcessDelegateAndIntegratedRoot() throws {
        let source = try String(
            contentsOf: Self.appleRoot
                .appendingPathComponent("AtlasIOSHost/AtlasIOSHost")
                .appendingPathComponent("AtlasIOSHostApp.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("@UIApplicationDelegateAdaptor"))
        XCTAssertTrue(source.contains("AtlasIOSIntegratedAppRootView("))
        XCTAssertFalse(source.contains("AtlasRootView()"))
    }

    private static var appleRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}
