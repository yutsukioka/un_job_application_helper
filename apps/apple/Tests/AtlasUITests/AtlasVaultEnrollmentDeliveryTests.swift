import CryptoKit
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultEnrollmentDeliveryTests: XCTestCase {
  func vectors() throws -> [[String: Any]] {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    return
      (try JSONSerialization.jsonObject(
        with: Data(
          contentsOf: root.appendingPathComponent(
            "contracts/sync/test_vectors/atlasvault_enrollment_delivery_v1.json")))
      as! [String: Any])["cases"] as! [[String: Any]]
  }
  func testRecipientOnlyDeliveryAndRestart() throws {
    for v in try vectors() {
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      defer { try? FileManager.default.removeItem(at: root) }
      let recipient = try AtlasVaultDeviceIdentity(
        signingPrivateSeed: Data(repeating: 90, count: 32),
        agreementPrivateKey: Data(repeating: 100, count: 32), createdAt: "2026-01-01T00:00:00Z",
        keyEpoch: 3)
      func install() throws -> AtlasVaultEpochVault {
        try AtlasVaultEnrollmentDelivery.install(
          directory: root, packet: v["packet"] as! [String: Any], pins: v["pins"] as! [String: Any],
          trustedSigner: Data(base64Encoded: v["trusted_signer_b64"] as! String)!,
          recipient: recipient, agreementPrivateKey: Data(repeating: 100, count: 32),
          storageKey: Data(repeating: 111, count: 32))
      }
      let owner = try install()
      var opened = try owner.open(
        AtlasVaultOpaqueCiphertextEnvelope(jsonObject: v["envelope"] as! [String: Any]))
      defer { opened.resetBytes(in: 0..<opened.count) }
      XCTAssertTrue(AtlasVaultEpochRotation.digest(opened) == v["opened_sha256"] as? String)
      XCTAssertTrue(
        NSDictionary(dictionary: try install().observation()).isEqual(to: try owner.observation()))
    }
  }
  func testDeliverySubstitutionNeverPublishes() throws {
    let v = try vectors()[0]
    for attack in [
      "wrapper", "missing", "duplicate", "reorder", "recipient", "anchor", "authority", "suite",
      "signature",
    ] {
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      defer { try? FileManager.default.removeItem(at: root) }
      let offset: UInt8 = attack == "recipient" ? 1 : 0
      let recipient = try AtlasVaultDeviceIdentity(
        signingPrivateSeed: Data(repeating: 90 + offset, count: 32),
        agreementPrivateKey: Data(repeating: 100 + offset, count: 32),
        createdAt: "2026-01-01T00:00:00Z", keyEpoch: 3)
      var p = v["packet"] as! [String: Any]
      var rows = p["deliveries"] as! [[String: Any]]
      switch attack {
      case "wrapper":
        rows[0]["ciphertext_b64"] = Data(repeating: 0, count: 48).base64EncodedString()
      case "missing": rows.removeLast()
      case "duplicate": rows.append(rows[0])
      case "reorder": rows.reverse()
      case "anchor":
        var a = p["anchor"] as! [String: Any]
        var c = a["checkpoint"] as! [String: Any]
        c["root"] = String(repeating: "01", count: 32)
        a["checkpoint"] = c
        p["anchor"] = a
      case "authority":
        var a = p["historical_authority"] as! [String: Any]
        a["views"] = Array((a["views"] as! [[String: Any]]).reversed())
        p["historical_authority"] = a
      case "suite": p["hpke_suite"] = "wrong"
      case "signature": p["signature_b64"] = Data(repeating: 0, count: 64).base64EncodedString()
      default: break
      }
      p["deliveries"] = rows
      XCTAssertThrowsError(
        try AtlasVaultEnrollmentDelivery.install(
          directory: root, packet: p, pins: v["pins"] as! [String: Any],
          trustedSigner: Data(base64Encoded: v["trusted_signer_b64"] as! String)!,
          recipient: recipient, agreementPrivateKey: Data(repeating: 100 + offset, count: 32),
          storageKey: Data(repeating: 111, count: 32))
      ) { error in
        XCTAssertTrue(error as? AtlasVaultEnrollmentDeliveryError == .rejected)
      }
      XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }
  }
}
