import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultHistoricalAuthorityTests: XCTestCase {
  func testBoundedEvidenceRejectionAndIdempotency() throws {
    let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    func fixture(_ name: String) throws -> [String: Any] {
      let f =
        try JSONSerialization.jsonObject(
          with: Data(
            contentsOf: repo.appendingPathComponent("contracts/sync/test_vectors/\(name).json")))
        as! [String: Any]
      return (f["cases"] as! [[String: Any]])[1]
    }
    let v = try fixture("atlasvault_bootstrap_authority_diagnostic")
    let original = try fixture("atlasvault_historical_authority_v1")["proof"] as! [String: Any]
    let c = v["checkpoint"] as! [String: Any]
    var trust = c.filter {
      ["account_id", "vault_id", "collection_id", "key_epoch"].contains($0.key)
    }
    for k in [
      "trusted_signer_b64", "registry", "recipient_device_id", "confirmed_transcript",
      "current_context",
    ] { trust[k] = v[k] }
    trust["anchor_root"] = c["root"]
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let history = try AtlasVaultAnchoredSyncState(
      fileURL: root.appendingPathComponent("history"),
      encryptionKey: Data(repeating: 96, count: 32), trust: trust)
    XCTAssertTrue(
      try history.bootstrap(
        v.filter {
          [
            "checkpoint", "enrollment", "registry", "current_context", "recipient_device_id",
            "confirmed_transcript", "view", "collection", "opaque_b64",
          ].contains($0.key)
        }))
    let before = try history.exportEvidence()
    func install(_ p: [String: Any]) throws -> Bool {
      try history.installHistoricalAuthority(
        p, collection: v["collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: v["opaque_b64"] as! String)!)
    }
    for field in [
      "version", "account_id", "vault_id", "anchor_root", "registry_root", "key_epoch",
      "registry_generation", "collection_sha256", "recipient_device_id", "transcript_sha256",
    ] {
      var p = original
      p[field] = (p[field] as? Int).map { $0 + 1 } ?? 0
      XCTAssertThrowsError(try install(p), "bound context substitution must reject")
      XCTAssertTrue(NSArray(array: try history.exportEvidence()).isEqual(to: before))
    }
    for attack in 0..<14 {
      var p = original
      var views = p["views"] as! [[String: Any]]
      var descriptors = p["signed_descriptors"] as! [[String: Any]]
      var registry = p["prior_registry"] as! [[String: Any]]
      var rev = p["revocation"] as! [String: Any]
      switch attack {
      case 0:
        views.removeFirst()
        p["views"] = views
      case 1:
        views.append(views[0])
        p["views"] = views
      case 2: p["views"] = Array(views.reversed())
      case 3:
        views[0]["root"] = String(repeating: "0", count: 64)
        p["views"] = views
      case 4:
        views[0]["signature_b64"] = Data(repeating: 0, count: 64).base64EncodedString()
        p["views"] = views
      case 5:
        descriptors.removeFirst()
        p["signed_descriptors"] = descriptors
      case 6:
        descriptors.append(descriptors[0])
        p["signed_descriptors"] = descriptors
      case 7:
        descriptors[0]["signature_b64"] = Data(repeating: 0, count: 64).base64EncodedString()
        p["signed_descriptors"] = descriptors
      case 8: p.removeValue(forKey: "revocation")
      case 9:
        rev["initiator_device_id"] = rev["target_device_id"]
        p["revocation"] = rev
      case 10:
        registry[0]["state"] = "REVOKED"
        p["prior_registry"] = registry
      case 11:
        registry.removeFirst()
        p["prior_registry"] = registry
      case 12: p["untrusted"] = "extra"
      default: p["account_id"] = String(repeating: "x", count: 128 * 1024)
      }
      XCTAssertThrowsError(try install(p), "authority attack \(attack) must reject")
      XCTAssertTrue(NSArray(array: try history.exportEvidence()).isEqual(to: before))
    }
    XCTAssertTrue(try install(original))
    XCTAssertFalse(try install(original))
    let reopened = try AtlasVaultAnchoredSyncState(
      fileURL: root.appendingPathComponent("history"),
      encryptionKey: Data(repeating: 96, count: 32), trust: trust)
    XCTAssertTrue(NSArray(array: try reopened.exportEvidence()).isEqual(to: before))
    XCTAssertFalse(
      try reopened.installHistoricalAuthority(
        original, collection: v["collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: v["opaque_b64"] as! String)!))
    let attacks =
      try JSONSerialization.jsonObject(
        with: Data(
          contentsOf: repo.appendingPathComponent(
            "contracts/sync/test_vectors/atlasvault_historical_authority_attacks_v1.json")))
      as! [String: Any]
    let fork = (attacks["fork_views"] as! [[String: Any]])[1]
    XCTAssertThrowsError(try reopened.compareEvidence([fork]))
    XCTAssertEqual(try reopened.recovery()["status"] as? String, "RECOVERY_PENDING")
    XCTAssertTrue(NSArray(array: try reopened.exportEvidence()).isEqual(to: before))
    XCTAssertThrowsError(
      try reopened.installHistoricalAuthority(
        original, collection: v["collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: v["opaque_b64"] as! String)!))
    let again = try AtlasVaultAnchoredSyncState(
      fileURL: root.appendingPathComponent("history"),
      encryptionKey: Data(repeating: 96, count: 32), trust: trust)
    XCTAssertEqual(try again.recovery()["status"] as? String, "RECOVERY_PENDING")
    XCTAssertThrowsError(try again.automaticSync { XCTFail("unfenced fork") })
    let changed = attacks["changed_projection"] as! [String: Any]
    let cp = changed["checkpoint"] as! [String: Any]
    var changedTrust = cp.filter {
      ["account_id", "vault_id", "collection_id", "key_epoch"].contains($0.key)
    }
    for k in [
      "trusted_signer_b64", "registry", "recipient_device_id", "confirmed_transcript",
      "current_context",
    ] { changedTrust[k] = changed[k] }
    changedTrust["anchor_root"] = cp["root"]
    let unavailable = try AtlasVaultAnchoredSyncState(
      fileURL: root.appendingPathComponent("changed"),
      encryptionKey: Data(repeating: 96, count: 32), trust: changedTrust)
    XCTAssertTrue(
      try unavailable.bootstrap(
        changed.filter {
          [
            "checkpoint", "enrollment", "registry", "current_context", "recipient_device_id",
            "confirmed_transcript", "view", "collection", "opaque_b64",
          ].contains($0.key)
        }))
    XCTAssertThrowsError(
      try unavailable.installHistoricalAuthority(
        changed["proof"] as! [String: Any], collection: changed["collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: changed["opaque_b64"] as! String)!)
    ) { error in
      XCTAssertTrue(error as? AtlasVaultHistoricalAuthorityError == .preimageRequired)
    }
  }
}
