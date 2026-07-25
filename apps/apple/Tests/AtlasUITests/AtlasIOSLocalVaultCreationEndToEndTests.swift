import Foundation
import XCTest
@testable import AtlasUI

final class AtlasIOSLocalVaultCreationEndToEndTests: XCTestCase {
    func testProductionCompositionExposesFreshInstallCreationJourney() throws {
        let harness = try Self.source(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )
        let root = try Self.source(
            named: "AtlasVaultProductionRootView.swift"
        )

        XCTAssertTrue(
            harness.contains("AtlasLocalVaultCreationCoordinator"),
            "Production composition has no local-vault creator"
        )
        XCTAssertTrue(
            harness.contains("creationContext"),
            "Production composition has no shared creation context"
        )
        XCTAssertTrue(
            root.contains("Create Local Vault"),
            "The production root cannot start the fresh-install journey"
        )
    }

    private static func source(named name: String) throws -> String {
        let appleRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try String(
            contentsOf: appleRoot
                .appendingPathComponent("Sources/AtlasUI")
                .appendingPathComponent(name),
            encoding: .utf8
        )
    }
}
