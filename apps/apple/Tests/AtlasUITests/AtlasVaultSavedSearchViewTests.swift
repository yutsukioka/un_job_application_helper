import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultSavedSearchViewTests: XCTestCase {
    func testSavedSearchOwnerContextAndViewExist() throws {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchView.swift"
        )

        for required in [
            "AtlasVaultSavedSearchPresentationOwner",
            "AtlasVaultSavedSearchPresentationStatus",
            "AtlasVaultSavedSearchActions",
            "AtlasVaultSavedSearchContext",
            "AtlasVaultSavedSearchView",
            "Saved Searches",
            "Add Saved Search",
            "Lock Vault",
            "confirmationDialog",
            "TextField",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
    }

    func testPrivateViewExcludesInternalAndOtherPrivateModels() throws {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchView.swift"
        )

        for forbidden in [
            "AtlasHydratedSavedJob",
            "AtlasHydratedApplicationNote",
            "AtlasHydratedProfileSnippet",
            "AtlasHydratedDraftMetadata",
            "recordID",
            "parentRevision",
            "keyID",
            "vaultID",
            "FileManager",
            "Keychain",
            "SecItem",
            "URLSession",
            "UserDefaults",
            ".task",
            ".onAppear",
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
