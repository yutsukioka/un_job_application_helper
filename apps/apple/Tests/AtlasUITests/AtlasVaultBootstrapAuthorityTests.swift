import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultBootstrapAuthorityTests: XCTestCase {
  func testStillActiveHistoricalAuthor() throws { try exercise(0) }
  func testNowRevokedHistoricalAuthor() throws { try exercise(1) }

  private func exercise(_ index: Int) throws {
    let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    let fixture =
      try JSONSerialization.jsonObject(
        with: Data(
          contentsOf:
            repo.appendingPathComponent(
              "contracts/sync/test_vectors/atlasvault_bootstrap_authority_diagnostic.json")))
      as! [String: Any]
    let v = (fixture["cases"] as! [[String: Any]])[index]
    let c = v["checkpoint"] as! [String: Any]
    var trust = c.filter {
      ["account_id", "vault_id", "collection_id", "key_epoch"].contains($0.key)
    }
    for key in [
      "trusted_signer_b64", "registry", "recipient_device_id", "confirmed_transcript",
      "current_context",
    ] {
      trust[key] = v[key]
    }
    trust["anchor_root"] = c["root"]
    let args = v.filter {
      [
        "checkpoint", "enrollment", "registry", "current_context", "recipient_device_id",
        "confirmed_transcript", "view", "collection", "opaque_b64",
      ].contains($0.key)
    }
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let history = try AtlasVaultAnchoredSyncState(
      fileURL: directory.appendingPathComponent("history"),
      encryptionKey: Data(repeating: 96, count: 32), trust: trust)
    XCTAssertTrue(try history.bootstrap(args))
    let owner = try AtlasVaultEpochVault(
      directory: directory.appendingPathComponent("owner"),
      storageKey: Data(repeating: 97, count: 32),
      deviceID: v["recipient_device_id"] as! String,
      registry: v["registry"] as! [[String: Any]],
      accountID: c["account_id"] as! String, vaultID: c["vault_id"] as! String,
      keyEpoch: 4, stateRoot: c["state_root"] as! String,
      historyOrigin: history.publicationOrigin())
    try owner.initialize(
      keys: [3: Data(repeating: 30, count: 32), 4: Data(repeating: 98, count: 32)], history: history
    )
    let before = try owner.observation()
    XCTAssertEqual(before["state_root"] as? String, c["state_root"] as? String)
    let envelope = try AtlasVaultOpaqueCiphertextEnvelope(
      jsonObject: v["ciphertext"] as! [String: Any])
    var opened: Data
    do {
      opened = try owner.open(envelope)
    } catch {
      XCTFail(
        "C30 retained-authority read rejected after authenticated anchor and key installation")
      return
    }
    defer { opened.resetBytes(in: 0..<opened.count) }
    XCTAssertTrue(opened == Data("c30-synthetic-current-view".utf8))
    XCTAssertTrue(NSDictionary(dictionary: try owner.observation()).isEqual(to: before))
  }
}
