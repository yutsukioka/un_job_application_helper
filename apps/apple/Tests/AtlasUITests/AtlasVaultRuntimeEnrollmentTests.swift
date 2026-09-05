import CryptoKit
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultRuntimeEnrollmentTests: XCTestCase {
  func testIndependentDurableAdditiveEnrollment() throws {
    let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    func vector(_ name: String) throws -> [String: Any] {
      try JSONSerialization.jsonObject(
        with: Data(
          contentsOf: repo.appendingPathComponent(
            "contracts/sync/test_vectors/" + name))) as! [String: Any]
    }
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let v = try vector("atlasvault_activation_v1.json")
    let record = v["record"] as! [String: Any]
    let p = record["proof"] as! [String: Any]
    let plan = p["plan"] as! [String: Any]
    let ids = v["device_ids"] as! [String]
    let signer = try Curve25519.Signing.PrivateKey(
      rawRepresentation: Data(repeating: 10, count: 32))
    func owner(_ i: Int, initialize: Bool = false) throws -> AtlasVaultEpochVault {
      let dir = root.appendingPathComponent(String(i))
      let c = try AtlasVaultEpochVault(
        directory: dir,
        storageKey: Data(repeating: UInt8(50 + i), count: 32), deviceID: ids[i],
        registry: p["registry"] as! [[String: Any]], accountID: plan["account_id"] as! String,
        vaultID: "vault-c26", keyEpoch: 3, stateRoot: plan["state_root"] as! String)
      if initialize {
        let h = try AtlasVaultGuardedSyncState(
          fileURL: dir.appendingPathComponent("prior-history"),
          encryptionKey: Data(repeating: UInt8(60 + i), count: 32),
          accountID: plan["account_id"] as! String, vaultID: "vault-c26",
          collectionID: "collection-c26", keyEpoch: 3,
          trustedSigner: signer.publicKey.rawRepresentation)
        try h.initialize()
        _ = try h.ingest(
          view: v["initial_view"] as! [String: Any],
          registry: v["initial_registry"] as! [[String: Any]],
          collection: v["initial_collection"] as! [String: Any],
          opaqueState: Data(base64Encoded: v["opaque_state_b64"] as! String)!)
        try c.initialize(keys: [3: Data(repeating: 30, count: 32)], history: h)
        _ = try c.acceptRotation(
          p, acceptedRecord: record,
          agreementPrivateKey: Data(repeating: UInt8(20 + i), count: 32))
      }
      return c
    }
    let clients = try [owner(0, initialize: true), owner(1, initialize: true)]
    let enrollment = try vector("atlasvault_device_enrollment_v1.json")
    let context = try clients[0].enrollmentContext()
    let registry = try clients[0].enrollmentRegistry()
    var unsigned = (enrollment["proof"] as! [String: Any]).filter {
      !["root", "signature_b64"].contains($0.key)
    }
    unsigned.merge(context) { _, new in new }
    unsigned["issuer_device_id"] = ids[0]
    unsigned["next_registry_generation"] = 5
    unsigned["prior_registry_root"] = try AtlasVaultRevocation.registryRoot(registry)
    unsigned["resulting_registry_root"] = try AtlasVaultRevocation.registryRoot(
      registry + [enrollment["target"] as! [String: Any]])
    let transcript = unsigned["transcript_sha256"] as! String
    let proof = try AtlasVaultDeviceEnrollment.create(
      unsigned, registry: registry,
      context: context, confirmedTranscript: transcript, status: "ACTIVE", signingKey: signer)
    XCTAssertThrowsError(
      try clients[0].acceptEnrollment(
        proof, confirmedTranscript: String(repeating: "00", count: 32)))
    for i in 0..<2 {
      XCTAssertTrue(try clients[i].acceptEnrollment(proof, confirmedTranscript: transcript))
      XCTAssertFalse(try clients[i].acceptEnrollment(proof, confirmedTranscript: transcript))
      let reopened = try owner(i)
      XCTAssertEqual(try reopened.enrollmentContext()["registry_generation"] as? Int, 5)
      XCTAssertEqual(try reopened.observation()["key_epoch"] as? Int, 4)
      XCTAssertTrue(
        (try reopened.observation()["recipients"] as! [String]).contains(
          proof["target_device_id"] as! String))
      XCTAssertEqual(
        try AtlasVaultEpochRotation.canonical(reopened.load()["journal"] as! [String: Any]),
        try AtlasVaultEpochRotation.canonical(clients[i].load()["journal"] as! [String: Any]))
    }
    let published = try clients[0].createCommitment(
      Data(base64Encoded: v["opaque_state_b64"] as! String)!, signingKey: signer)
    XCTAssertEqual(
      (published["view"] as! [String: Any])["registry_root"] as? String,
      proof["resulting_registry_root"] as? String)
    XCTAssertEqual(try owner(0).observation()["key_epoch"] as? Int, 4)
    let packet = try AtlasVaultDeviceDelivery.create(record, recipientDeviceID: ids[0],
      issuerDeviceID: ids[0], signingKey: signer, currentRegistry: clients[0].enrollmentRegistry(),
      recoveryPending: false)
    XCTAssertTrue(try clients[0].catchUp([packet], currentActivationID: record["transition_id"] as! String,
      agreementPrivateKey: Data(repeating: 20, count: 32)))
    XCTAssertFalse(try clients[0].catchUp([packet], currentActivationID: record["transition_id"] as! String,
      agreementPrivateKey: Data(repeating: 20, count: 32)))
    XCTAssertEqual(try owner(0).observation()["registry_root"] as? String, proof["resulting_registry_root"] as? String)
    XCTAssertEqual(try owner(0).enrollmentContext()["registry_generation"] as? Int, 5)
  }
}
