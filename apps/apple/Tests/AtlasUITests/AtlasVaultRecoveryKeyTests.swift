import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRecoveryKeyTests: XCTestCase {
    func testRecoveryKeyCodecAndVaultBoundWrapSurfaceExists() throws {
        let source = try phaseSource("AtlasVaultRecoveryKey.swift")

        for required in [
            "SecRandomCopyBytes",
            "AVRK1-",
            "atlasvault-recovery-key-v1:",
            "HKDF<SHA256>",
            "AES.GCM",
            "atlas-vault-recovery-wrap-v2",
            "vault_id",
            "constantTime",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "UInt8.random",
            "Task.detached",
            "print(",
            "Logger",
            "os_log",
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
