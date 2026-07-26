import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRecoveryExportViewTests: XCTestCase {
    func testExplicitPrivateFreeRecoveryExportViewSurfaceExists() throws {
        let source = try phaseSource("AtlasVaultRecoveryExportView.swift")

        for required in [
            "Recovery & Encrypted Export",
            "Restart Recovery Setup",
            "SecureField",
            "@State",
            "fileExporter",
            "import",
            "claimPresentation",
            "releasePresentation",
            "ownsPresentation",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "UIPasteboard",
            "NSPasteboard",
            ".task",
            ".onAppear",
            "AtlasVaultPrivateState",
            "savedSearch",
            "savedJob",
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
