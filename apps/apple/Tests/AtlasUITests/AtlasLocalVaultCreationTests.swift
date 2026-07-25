import Foundation
import XCTest
@testable import AtlasUI

final class AtlasLocalVaultCreationTests: XCTestCase {
    func testCreationCoreDeclaresExplicitResumableTransaction() throws {
        let sourceURL = Self.sourceURL(named: "AtlasLocalVaultCreation.swift")
        guard FileManager.default.fileExists(atPath: sourceURL.path) else {
            XCTFail("Phase 2D-60 creation core is missing")
            return
        }
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        for required in [
            "AtlasLocalVaultCreationCoordinator",
            "AtlasLocalVaultCreating",
            "SecRandomCopyBytes",
            "committedDurabilityUnconfirmed",
            "storeSelection",
            "clearJournal",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
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
