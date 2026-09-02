import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultEpochRotationTests: XCTestCase {
  func vector() throws -> [String: Any] {
    var root = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { root.deleteLastPathComponent() }
    return try JSONSerialization.jsonObject(
      with: Data(
        contentsOf: root.appendingPathComponent(
          "contracts/sync/test_vectors/atlasvault_epoch_rotation_v1.json"))) as! [String: Any]
  }
  func check(_ proof: [String: Any], original: [String: Any]) throws -> [String: Any] {
    try AtlasVaultEpochRotation.verify(
      proof, registry: original["registry"] as! [[String: Any]],
      accountID: (original["plan"] as! [String: Any])["account_id"] as! String,
      vaultID: "vault-c26", previousEpoch: 3, stateRoot: String(repeating: "ab", count: 32))
  }
  func testSharedSignedBindingAndActiveRecipients() throws {
    let vectors = try vector()
    let proof = vectors["proof"] as! [String: Any]
    let result = try check(proof, original: proof)
    XCTAssertEqual(result["new_epoch"] as? Int, 4)
    XCTAssertEqual(result["binding_root"] as? String, vectors["binding_root"] as? String)
    XCTAssertFalse(
      (result["recipients"] as! [String]).contains((vectors["device_ids"] as! [String])[2]))
  }
  func testEachEpochContextFieldRejectsSubstitution() throws {
    let proof = try vector()["proof"] as! [String: Any]
    for field in (proof["plan"] as! [String: Any]).keys {
      var changed = proof
      var plan = proof["plan"] as! [String: Any]
      if let value = plan[field] as? Int {
        plan[field] = value + 1
      } else if plan[field] is [String] {
        plan[field] = [String]()
      } else {
        plan[field] = "substituted"
      }
      changed["plan"] = plan
      XCTAssertThrowsError(try check(changed, original: proof), field)
    }
  }
}
