import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultAnchoredHistoryTests: XCTestCase {
  func vector() throws -> [String: Any] {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    return try JSONSerialization.jsonObject(
      with: Data(
        contentsOf: root.appendingPathComponent(
          "contracts/sync/test_vectors/atlasvault_history_bootstrap_v1.json"))) as! [String: Any]
  }
  func exercise(
    _ operation: ([String: Any], [String: Any], (String) throws -> AtlasVaultAnchoredSyncState)
      throws -> Void
  ) throws {
    let v = try vector()
    let c = v["checkpoint"] as! [String: Any]
    var trust = c.filter {
      ["account_id", "vault_id", "collection_id", "key_epoch"].contains($0.key)
    }
    for key in [
      "trusted_signer_b64", "registry", "recipient_device_id", "confirmed_transcript",
      "current_context",
    ] { trust[key] = v[key] }
    trust["anchor_root"] = c["root"]
    let args = v.filter {
      [
        "checkpoint", "enrollment", "registry", "current_context", "recipient_device_id",
        "confirmed_transcript", "view", "collection", "opaque_b64",
      ].contains($0.key)
    }
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: dir) }
    try operation(
      v, args,
      { name in
        try AtlasVaultAnchoredSyncState(
          fileURL: dir.appendingPathComponent(name),
          encryptionKey: Data(repeating: 96, count: 32), trust: trust)
      })
  }
  func ingest(_ client: AtlasVaultAnchoredSyncState, _ name: String, _ v: [String: Any]) throws
    -> Bool
  {
    let p = (v["packets"] as! [String: [String: Any]])[name]!
    return try client.ingest(
      view: p["view"] as! [String: Any], registry: p["registry"] as! [[String: Any]],
      collection: p["collection"] as! [String: Any],
      opaqueState: Data(base64Encoded: p["opaque_b64"] as! String)!)
  }
  func testSharedAnchorForwardRestartAndDuplicate() throws {
    try exercise { v, args, open in
      XCTAssertTrue(try open("A").bootstrap(args))
      XCTAssertFalse(try open("A").bootstrap(args))
      XCTAssertTrue(try ingest(open("A"), "next", v))
      XCTAssertFalse(try ingest(open("A"), "next", v))
      XCTAssertEqual(try open("A").checkpoint()["sequence"] as? Int, 3)
    }
  }
  func testSubAnchorAndNonChainingRejectionSurviveReopen() throws {
    for attack in ["sub_anchor", "non_chaining"] {
      try exercise { v, args, open in
        _ = try open("A").bootstrap(args)
        let before = try open("A").checkpoint()
        XCTAssertThrowsError(try ingest(open("A"), attack, v))
        XCTAssertTrue(NSDictionary(dictionary: try open("A").checkpoint()).isEqual(to: before))
        XCTAssertEqual(try open("A").recovery()["status"] as? String, "RECOVERY_PENDING")
      }
    }
  }
  func testIndependentForkBranchesRemainFenced() throws {
    try exercise { v, args, open in
      _ = try open("A").bootstrap(args)
      _ = try open("B").bootstrap(args)
      _ = try ingest(open("A"), "next", v)
      _ = try ingest(open("B"), "fork", v)
      let a = try open("A").exportEvidence()
      let b = try open("B").exportEvidence()
      XCTAssertThrowsError(try open("A").compareEvidence(b))
      XCTAssertThrowsError(try open("B").compareEvidence(a))
      XCTAssertEqual(try open("A").recovery()["status"] as? String, "RECOVERY_PENDING")
      XCTAssertEqual(try open("B").recovery()["status"] as? String, "RECOVERY_PENDING")
    }
  }
  func testEveryCertificateFieldRejectsSubstitution() throws {
    try exercise { v, args, open in
      for key in (v["checkpoint"] as! [String: Any]).keys {
        var bad = args
        var p = bad["checkpoint"] as! [String: Any]
        if let old = p[key] as? Int { p[key] = old + 1 } else { p[key] = "substituted" }
        bad["checkpoint"] = p
        XCTAssertThrowsError(try open("A").bootstrap(bad))
      }
    }
  }
  func testEpochOwnerRetainsImmutableAnchorAfterReopen() throws {
    try exercise { v, args, open in
      let history = try open("source")
      _ = try history.bootstrap(args)
      let origin = try history.publicationOrigin()
      let c = v["checkpoint"] as! [String: Any]
      let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      defer { try? FileManager.default.removeItem(at: dir) }
      func owner(_ anchored: Bool = true) throws -> AtlasVaultEpochVault {
        try AtlasVaultEpochVault(
          directory: dir, storageKey: Data(repeating: 97, count: 32),
          deviceID: v["recipient_device_id"] as! String,
          registry: v["registry"] as! [[String: Any]],
          accountID: c["account_id"] as! String, vaultID: c["vault_id"] as! String,
          keyEpoch: c["key_epoch"] as! Int, stateRoot: c["state_root"] as! String,
          historyOrigin: anchored ? origin : nil)
      }
      try owner().initialize(keys: [4: Data(repeating: 98, count: 32)], history: history)
      let before = try owner().observation()
      XCTAssertEqual(before["sequence"] as? Int, 2)
      XCTAssertTrue(NSDictionary(dictionary: try owner().observation()).isEqual(to: before))
      XCTAssertTrue(
        NSDictionary(dictionary: try owner().enrollmentContext())
          .isEqual(to: args["current_context"] as! [String: Any]))
      XCTAssertThrowsError(try owner(false).observation())
      XCTAssertTrue(NSDictionary(dictionary: try owner().observation()).isEqual(to: before))
    }
  }
  func testPublicationOriginRejectsAdditionalContextFields() throws {
    try exercise { v, args, open in
      let history = try open("source")
      _ = try history.bootstrap(args)
      var origin = try history.publicationOrigin()
      var context = origin["context"] as! [String: Any]
      context["unexpected"] = true
      origin["context"] = context
      let c = v["checkpoint"] as! [String: Any]
      let directory = FileManager.default.temporaryDirectory.appendingPathComponent(
        UUID().uuidString)
      defer { try? FileManager.default.removeItem(at: directory) }
      let owner = try AtlasVaultEpochVault(
        directory: directory,
        storageKey: Data(repeating: 97, count: 32),
        deviceID: v["recipient_device_id"] as! String,
        registry: v["registry"] as! [[String: Any]],
        accountID: c["account_id"] as! String, vaultID: c["vault_id"] as! String,
        keyEpoch: c["key_epoch"] as! Int, stateRoot: c["state_root"] as! String,
        historyOrigin: origin)
      XCTAssertThrowsError(try AtlasVaultAnchoredSyncState.publicationReader(owner))
    }
  }
}
