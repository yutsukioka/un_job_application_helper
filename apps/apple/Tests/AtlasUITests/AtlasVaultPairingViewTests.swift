import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultPairingViewTests: XCTestCase {
    func testPairingViewExposesOnlyExplicitActions() throws {
        let source = try Self.source(named: "AtlasVaultPairingView.swift")

        for action in [
            "Create Device Identity",
            "Create Pairing Offer",
            "Save Pairing Offer",
            "Import Pairing Offer",
            "Save Pairing Acceptance",
            "Import Pairing Acceptance",
            "Codes Match",
            "Save Key Delivery",
            "Import Key Delivery",
            "Save Pairing Acknowledgement",
            "Import Pairing Acknowledgement",
            "Resume Pairing",
            "Discard Pairing",
        ] {
            XCTAssertTrue(source.contains(action), action)
        }
        for forbidden in [
            "privateKey",
            "vaultKey",
            "sessionKey",
            "ephemeralPrivateKey",
            "backendCredential",
            ".task",
            ".onAppear",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testPairingOwnerRetainsOneOperationAndDrainsOnStop() throws {
        let source = try Self.source(named: "AtlasVaultPairingView.swift")

        for required in [
            "AtlasVaultTrustedPairingPresentationOwner",
            "AtlasVaultTrustedPairingContext",
            "AtlasVaultTrustedPairingCoordinating",
            "operationTask",
            "createDeviceIdentity",
            "createPairingOffer",
            "confirmCodesMatch",
            "resumePairing",
            "discardPairing",
            "stopAndDrain",
        ] {
            XCTAssertTrue(source.contains(required), required)
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
