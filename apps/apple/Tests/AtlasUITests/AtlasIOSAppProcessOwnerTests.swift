import Foundation
import XCTest

final class AtlasIOSAppProcessOwnerTests: XCTestCase {
    func testProcessOwnerSourceExists() {
        let source = Self.appleRoot
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent("AtlasIOSAppProcessOwner.swift")

        XCTAssertTrue(
            FileManager.default.fileExists(atPath: source.path),
            "Phase 2D-59 process-owner types are absent"
        )
    }

    private static var appleRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}
