import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultEncryptedExportTests: XCTestCase {
    func testStrictCanonicalEncryptedExportSurfaceExists() throws {
        let source = try phaseSource("AtlasVaultEncryptedExport.swift")

        for required in [
            "atlasvault-export",
            "supportedVersion = 1",
            "export_id",
            "created_at",
            "vault_metadata",
            "records",
            "sortedKeys",
            "AtlasVault-Encrypted-Backup.atlasvault",
            "FileDocument",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "store_id",
            "selected_vault",
            "Keychain",
            "plaintext",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
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
