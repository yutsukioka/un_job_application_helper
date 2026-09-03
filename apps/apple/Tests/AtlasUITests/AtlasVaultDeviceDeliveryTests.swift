import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultDeviceDeliveryTests: XCTestCase {
  func testSharedProofAndTamperRejections() throws {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    func load(_ name: String) throws -> [String: Any] {
      try JSONSerialization.jsonObject(
        with: Data(contentsOf: root.appendingPathComponent("contracts/sync/test_vectors/" + name)))
        as! [String: Any]
    }
    let v = try load("atlasvault_device_delivery_v2.json")
    let record = (try load("atlasvault_activation_v1.json"))["record"] as! [String: Any]
    let original = record["proof"] as! [String: Any]
    let plan = original["plan"] as! [String: Any]
    func verify(_ packet: [String: Any]) throws -> [String: Any] {
      try AtlasVaultDeviceDelivery.verify(
        packet, registry: original["registry"] as! [[String: Any]],
        accountID: plan["account_id"] as! String, vaultID: plan["vault_id"] as! String,
        previousEpoch: 3,
        stateRoot: plan["state_root"] as! String, activationID: original["root"] as! String,
        recipientDeviceID: v["recipient_device_id"] as! String)
    }
    let packet = v["packet"] as! [String: Any]
    let proof = packet["proof"] as! [String: Any]
    XCTAssertEqual(try verify(packet)["new_epoch"] as? Int, 4)
    XCTAssertEqual(
      try AtlasVaultDeviceDelivery.canonicalHash(proof), v["canonical_sha256"] as? String)
    XCTAssertNil(proof["deliveries"])
    for field in proof.keys {
      var bad = packet
      var changed = proof
      changed[field] = "substitution"
      bad["proof"] = changed
      XCTAssertThrowsError(try verify(bad), field)
    }
    var wrong = packet
    wrong["wrapper"] = (original["deliveries"] as! [[String: Any]]).first {
      $0["device_id"] as? String != v["recipient_device_id"] as? String
    }!
    XCTAssertThrowsError(try verify(wrong))
    XCTAssertThrowsError(try verify(record))
  }
}
