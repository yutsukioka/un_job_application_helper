import Foundation
import XCTest
@testable import AtlasUI

final class AtlasIOSRecoveryExportEndToEndTests: XCTestCase {
    func testProductionHarnessExposesRecoveryExportJourneyWithoutRecoveryUnlock()
        throws
    {
        let harness = try phaseSource(
            "AtlasVaultProductionCompositionHarness.swift"
        )
        let recovery = try phaseSource("AtlasVaultRecoveryExport.swift")
        let root = try phaseSource("AtlasVaultProductionRootView.swift")

        XCTAssertTrue(
            harness.contains("AtlasVaultRecoveryExportCoordinator")
        )
        XCTAssertTrue(
            harness.contains("recoveryExportContext")
        )
        XCTAssertTrue(
            harness.contains("recoveryOwner.stop")
        )
        XCTAssertTrue(
            recovery.contains("resumeAndPrepareExport")
        )
        XCTAssertTrue(
            recovery.contains("resetPendingSetup")
        )
        XCTAssertTrue(
            root.contains("Recovery & Encrypted Export")
        )
        XCTAssertTrue(
            harness.contains(
                "productionCapabilities.availableMethods == [.localKey]"
            )
        )
        XCTAssertFalse(
            harness.contains(
                "recoveryKeyProvider: Atlas"
            )
        )
    }

    private func phaseSource(_ name: String) throws -> String {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
        return try String(contentsOf: sourceURL, encoding: .utf8)
    }
}
