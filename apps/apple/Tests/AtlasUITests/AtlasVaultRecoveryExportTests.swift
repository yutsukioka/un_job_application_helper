import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRecoveryExportTests: XCTestCase {
    func testRecoverySetupTransactionSurfaceExists() throws {
        let source = try phaseSource("AtlasVaultRecoveryExport.swift")

        for required in [
            "com.atlasvault.recovery-export",
            "pending-v2",
            "afterFirstUnlockThisDeviceOnly",
            "prepareNewRecovery",
            "confirmAndPrepareExport",
            "resumeAndPrepareExport",
            "exportDidSucceed",
            "exportDidFailOrCancel",
            "resetPendingSetup",
            "saveEncryptedStoreAtomically",
            "overwrite: true",
            "recordHydrator",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "Task.detached",
            "UserDefaults",
            "LocalAuthentication",
            "CloudKit",
            "deleteVaultKey",
            "clearSelection",
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
