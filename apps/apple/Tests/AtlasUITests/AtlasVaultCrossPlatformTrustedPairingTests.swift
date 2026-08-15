import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultCrossPlatformTrustedPairingTests: XCTestCase {
    func testProductionPairingJourneyPreservesManualArtifactRing() throws {
        let pairing = try Self.source(named: "AtlasVaultPairingView.swift")
        let harness = try Self.source(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )

        for required in [
            "AtlasVaultPairingArtifact",
            "AtlasVaultPairingTransaction",
            "AtlasVaultTrustedDeviceRegistry",
            "AtlasVaultPairingReplayStore",
            "AtlasVaultPairingBootstrap",
            "AtlasVaultSignedVaultKeyDelivery",
            "AtlasVaultSignedPairingAcknowledgement",
        ] {
            XCTAssertTrue(pairing.contains(required), required)
        }
        for required in [
            "pairingContext",
            "pairingOwner.stopAndDrain",
            "pairingOwner.clearSensitiveInput",
        ] {
            XCTAssertTrue(harness.contains(required), required)
        }
    }

    private static func source(named name: String) throws -> String {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let url = root
            .appendingPathComponent("apps/apple/Sources/AtlasUI")
            .appendingPathComponent(name)
        return try String(contentsOf: url, encoding: .utf8)
    }
}
