import Foundation
import XCTest
@testable import AtlasUI

final class AtlasIOSPrivateSavedSearchEndToEndTests: XCTestCase {
    func testProductionCompositionContainsPrivateSavedSearchJourney()
        throws
    {
        let feature = try requiredSource(
            named: "AtlasVaultSavedSearchFeature.swift"
        )
        let host = try requiredSource(
            named: "AtlasVaultProductionHost.swift"
        )
        let harness = try requiredSource(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )

        XCTAssertTrue(feature.contains("AtlasVaultCreateMutation"))
        XCTAssertTrue(feature.contains("AtlasVaultDeleteMutation"))
        XCTAssertTrue(feature.contains("tombstones"))
        XCTAssertTrue(host.contains("activatePrivateSession"))
        XCTAssertTrue(host.contains("applyPrivateMutation"))
        XCTAssertTrue(harness.contains("savedSearchContext"))
        XCTAssertTrue(harness.contains("AtlasVaultSavedSearchCoordinator"))
        XCTAssertTrue(
            harness.contains("AtlasVaultSavedSearchPresentationOwner")
        )
    }

    func testJourneyKeepsPublicPipelineAndCompatibilityEndpointUnused()
        throws
    {
        let feature = try requiredSource(
            named: "AtlasVaultSavedSearchFeature.swift"
        )
        let harness = try requiredSource(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )
        let combined = feature + harness

        for forbidden in [
            "/api/saved-searches",
            "AtlasLocalCache",
            "UserDefaults",
            "Task.detached",
        ] {
            XCTAssertFalse(combined.contains(forbidden), forbidden)
        }
    }

    private func requiredSource(named name: String) throws -> String {
        let url = Self.appleRoot()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: url.path),
            "Missing Phase 2D-63 source: \(name)"
        )
        guard FileManager.default.fileExists(atPath: url.path) else {
            return ""
        }
        return try String(contentsOf: url, encoding: .utf8)
    }

    private static func appleRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}
