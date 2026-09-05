import CryptoKit
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultDeviceEnrollmentTests: XCTestCase {
  func testSharedEnrollmentAndAllContextFences() throws {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    let v = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent(
      "contracts/sync/test_vectors/atlasvault_device_enrollment_v1.json"))) as! [String: Any]
    let proof = v["proof"] as! [String: Any], context = v["context"] as! [String: Any]
    let registry = v["registry"] as! [[String: Any]], transcript = proof["transcript_sha256"] as! String
    func verify(_ p: [String: Any]? = nil, _ c: [String: Any]? = nil,
      _ r: [[String: Any]]? = nil, _ t: String? = nil, _ status: String = "ACTIVE") throws -> [[String: Any]] {
      try AtlasVaultDeviceEnrollment.verify(p ?? proof, registry: r ?? registry,
        context: c ?? context, confirmedTranscript: t ?? transcript, status: status)
    }
    let after = try verify()
    XCTAssertEqual(after.count, registry.count + 1)
    XCTAssertTrue(after.contains { $0["device_id"] as? String == proof["target_device_id"] as? String })
    XCTAssertEqual(try AtlasVaultDeviceEnrollment.canonicalHash(proof), v["canonical_sha256"] as? String)
    let created = try AtlasVaultDeviceEnrollment.create(
      proof.filter { !["root", "signature_b64"].contains($0.key) }, registry: registry, context: context,
      confirmedTranscript: transcript, status: "ACTIVE",
      signingKey: Curve25519.Signing.PrivateKey(rawRepresentation: Data(repeating: 10, count: 32)))
    _ = try verify(created)
    XCTAssertEqual(created["root"] as? String, proof["root"] as? String)
    for key in proof.keys {
      var bad = proof; bad[key] = "substitution"
      XCTAssertThrowsError(try verify(bad), key)
    }
    for key in context.keys {
      var bad = context
      if let n = context[key] as? Int { bad[key] = n + 1 } else { bad[key] = "wrong-context" }
      XCTAssertThrowsError(try verify(nil, bad), key)
    }
    XCTAssertThrowsError(try verify(nil, nil, after))
    var revoked = registry; revoked[0]["state"] = "REVOKED"
    XCTAssertThrowsError(try verify(nil, nil, revoked))
    for status in ["RECOVERY_PENDING", "ACTIVATION_PENDING", "CATCH_UP_PENDING", "CLEANUP_PENDING", "REVOKED"] {
      XCTAssertThrowsError(try verify(nil, nil, nil, nil, status))
    }
    for value in ["", String(repeating: "00", count: 32), String(repeating: "aa", count: 32)] {
      XCTAssertThrowsError(try verify(nil, nil, nil, value))
    }
  }
}
