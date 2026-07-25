import Foundation
import XCTest

final class AtlasIOSIntegratedAppRootViewTests: XCTestCase {
    func testIntegratedRootSourceExists() {
        let source = Self.appleRoot
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent("AtlasIOSIntegratedAppRootView.swift")

        XCTAssertTrue(
            FileManager.default.fileExists(atPath: source.path),
            "Phase 2D-59 integrated root is absent"
        )
    }

    private static var appleRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}
