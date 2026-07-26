import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultSavedSearchFeatureTests: XCTestCase {
    func testSavedSearchFeatureSurfaceExists() throws {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchFeature.swift"
        )

        for required in [
            "AtlasVaultSavedSearchCoordinating",
            "AtlasVaultSavedSearchCoordinator",
            "AtlasVaultSavedSearchDraft",
            "AtlasVaultSavedSearchSnapshot",
            "AtlasVaultSavedSearchMutationResult",
            "AtlasVaultPresentationGeneration",
            "AtlasVaultPresentationID",
            "AtlasVaultSavedSearchPresentation",
            "primary-local-key-v1",
            "AtlasVaultCreateMutation",
            "AtlasVaultDeleteMutation",
            "applyPrivateMutation",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
    }

    func testPrivateSessionAndMutationContractsExist() throws {
        let source = try requiredSource(
            named: "AtlasVaultProductionHostContracts.swift"
        )

        for required in [
            "AtlasVaultPrivateSessionBoundary",
            "AtlasNoopVaultPrivateSessionBoundary",
            "AtlasVaultPrivateSessionBoundaryBridge",
            "AtlasVaultPrivateMutationHosting",
            "AtlasVaultPrivateMutationResult",
            "activatePrivateSession",
            "hidePrivatePresentation",
            "stopAndDrainPrivateSession",
            "applyPrivateMutation",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
    }

    func testFeatureUsesOnlyEncryptedRuntimeMutationBoundary() throws {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchFeature.swift"
        )

        for forbidden in [
            "UserDefaults",
            "URLSession",
            "AtlasAPIClient",
            "SearchViewModel",
            "AtlasLocalCache",
            "/api/saved-searches",
            "FileManager",
            "Keychain",
            "SecItem",
            "Task.detached",
            "@unchecked Sendable",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
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
