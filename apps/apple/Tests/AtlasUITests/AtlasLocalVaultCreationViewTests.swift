import Foundation
import XCTest
@testable import AtlasUI

final class AtlasLocalVaultCreationViewTests: XCTestCase {
    func testCreationViewRequiresExplicitAcknowledgedAction() throws {
        let sourceURL = Self.sourceURL(
            named: "AtlasLocalVaultCreationView.swift"
        )
        guard FileManager.default.fileExists(atPath: sourceURL.path) else {
            XCTFail("Phase 2D-60 creation presentation and view are missing")
            return
        }
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        for required in [
            "AtlasLocalVaultCreationPresentationOwner",
            "Create Local Vault",
            "device-local Keychain key only",
            "Toggle(",
            "Pause Setup",
            "Retry Setup",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        XCTAssertFalse(source.contains("TextField("))
        XCTAssertFalse(source.contains("SecureField("))
    }

    private static func sourceURL(named name: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
    }
}
