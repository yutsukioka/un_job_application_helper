import CryptoKit
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultEpochRotationTests: XCTestCase {
  func testIndependentEpochGenerationsAndRevokedReconnectSurviveReopen() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let vectors = try vector("atlasvault_activation_v1.json")
    let record = vectors["record"] as! [String: Any]
    let proof = record["proof"] as! [String: Any]
    func device(_ i: Int) throws -> AtlasVaultEpochVault {
      try AtlasVaultEpochVault(
        directory: root.appendingPathComponent(String(i)),
        storageKey: Data(repeating: UInt8(50 + i), count: 32),
        deviceID: (vectors["device_ids"] as! [String])[i],
        registry: proof["registry"] as! [[String: Any]],
        accountID: (proof["plan"] as! [String: Any])["account_id"] as! String, vaultID: "vault-c26",
        keyEpoch: 3, stateRoot: (proof["plan"] as! [String: Any])["state_root"] as! String)
    }
    let clients = try (0..<3).map(device)
    let signer = try Curve25519.Signing.PrivateKey(
      rawRepresentation: Data(repeating: 10, count: 32))
    for i in 0..<3 {
      let h = try AtlasVaultGuardedSyncState(
        fileURL: root.appendingPathComponent("history-\(i)"),
        encryptionKey: Data(repeating: UInt8(60 + i), count: 32),
        accountID: (proof["plan"] as! [String: Any])["account_id"] as! String, vaultID: "vault-c26",
        collectionID: "collection-c26", keyEpoch: 3,
        trustedSigner: signer.publicKey.rawRepresentation)
      try h.initialize()
      _ = try h.ingest(
        view: vectors["initial_view"] as! [String: Any],
        registry: vectors["initial_registry"] as! [[String: Any]],
        collection: vectors["initial_collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: vectors["opaque_state_b64"] as! String)!)
      try clients[i].initialize(keys: [3: Data(repeating: 30, count: 32)], history: h)
    }
    for i in 0..<2 {
      try clients[i].beginActivation(proof)
      XCTAssertEqual(try device(i).observation()["status"] as? String, "ACTIVATION_PENDING")
      XCTAssertTrue(
        try clients[i].acceptRotation(
          proof, acceptedRecord: record,
          agreementPrivateKey: Data(repeating: UInt8(20 + i), count: 32)))
      XCTAssertEqual(try device(i).observation()["key_epoch"] as? Int, 4)
      XCTAssertEqual(
        try device(i).observation()["recipient_commitment"] as? String,
        vectors["recipient_commitment"] as? String)
      XCTAssertFalse(
        try clients[i].acceptRotation(
          proof, acceptedRecord: record,
          agreementPrivateKey: Data(repeating: UInt8(20 + i), count: 32)))
    }
    XCTAssertEqual(try clients[2].observation()["key_epoch"] as? Int, 3)
    XCTAssertThrowsError(
      try clients[2].acceptRotation(
        proof, acceptedRecord: record, agreementPrivateKey: Data(repeating: 22, count: 32)))
    XCTAssertEqual(try device(2).observation()["status"] as? String, "REVOKED")
    for kind in ["patch", "snapshot"] {
      let sealed = try clients[0].seal(
        kind, plaintext: Data(repeating: 71, count: 32), objectID: "record-c26",
        revision: "revision-\(kind)", signingKey: signer)
      XCTAssertEqual(sealed.keyEpoch, 4)
      if kind == "patch" {
        try clients[0].queueOperation(
          AtlasVaultEncryptedPatchOperation(jsonObject: [
            "format": "atlasvault-encrypted-patch-operation", "version": 1,
            "operation_id": "10000000-0000-4000-8000-000000000001", "operation_type": "upsert",
            "author_device_id": (vectors["device_ids"] as! [String])[0],
            "author_sequence": 1, "lamport": 1, "envelope": sealed.jsonObject,
          ]))
        XCTAssertEqual(try device(0).pendingOperations().first?.envelope.keyEpoch, 4)
      }
      XCTAssertEqual(try clients[1].open(sealed), Data(repeating: 71, count: 32))
      XCTAssertThrowsError(try clients[2].open(sealed))
    }
    for recipient in (proof["plan"] as! [String: Any])["recipients"] as! [String] {
      XCTAssertEqual(try clients[0].delivery(recipient)["key_epoch"] as? Int, 4)
    }
    XCTAssertThrowsError(try clients[0].delivery((vectors["device_ids"] as! [String])[2]))
    let commitment = try clients[0].createCommitment(
      Data(base64Encoded: vectors["opaque_state_b64"] as! String)!, signingKey: signer)
    XCTAssertEqual((commitment["view"] as? [String: Any])?["key_epoch"] as? Int, 4)
    let first = try AtlasVaultEpochRotation.create(
      proof["revocation"] as! [String: Any], registry: proof["registry"] as! [[String: Any]],
      stateRoot: (proof["plan"] as! [String: Any])["state_root"] as! String, signingKey: signer)
    let second = try AtlasVaultEpochRotation.create(
      proof["revocation"] as! [String: Any], registry: proof["registry"] as! [[String: Any]],
      stateRoot: (proof["plan"] as! [String: Any])["state_root"] as! String, signingKey: signer)
    XCTAssertNotEqual(first["root"] as? String, second["root"] as? String)
    var unsigned = vectors["initial_view"] as! [String: Any]
    unsigned.removeValue(forKey: "root")
    unsigned.removeValue(forKey: "signature_b64")
    unsigned["collection_root"] = String(repeating: "de", count: 32)
    let fork = try AtlasVaultAuthenticatedStateView.sign(unsigned, signingKey: signer)
    XCTAssertThrowsError(try clients[0].compareEvidence([fork]))
    XCTAssertEqual(try device(0).observation()["status"] as? String, "RECOVERY_PENDING")
    XCTAssertFalse((try device(0).recovery()["peer"] as! [[String: Any]]).isEmpty)
    XCTAssertThrowsError(try device(0).beginActivation(proof))
  }
  func activationDevice(_ dir: URL, initialize: Bool = false) throws -> AtlasVaultEpochVault {
    let v = try vector("atlasvault_activation_v1.json")
    let r = v["record"] as! [String: Any]
    let p = r["proof"] as! [String: Any]
    let plan = p["plan"] as! [String: Any]
    let c = try AtlasVaultEpochVault(
      directory: dir, storageKey: Data(repeating: 50, count: 32),
      deviceID: (v["device_ids"] as! [String])[0], registry: p["registry"] as! [[String: Any]],
      accountID: plan["account_id"] as! String, vaultID: "vault-c26", keyEpoch: 3,
      stateRoot: plan["state_root"] as! String)
    if initialize {
      let h = try AtlasVaultGuardedSyncState(
        fileURL: dir.appendingPathComponent("initial-history"),
        encryptionKey: Data(repeating: 60, count: 32), accountID: plan["account_id"] as! String,
        vaultID: "vault-c26", collectionID: "collection-c26", keyEpoch: 3,
        trustedSigner: Curve25519.Signing.PrivateKey(
          rawRepresentation: Data(repeating: 10, count: 32)
        ).publicKey.rawRepresentation)
      try h.initialize()
      _ = try h.ingest(
        view: v["initial_view"] as! [String: Any],
        registry: v["initial_registry"] as! [[String: Any]],
        collection: v["initial_collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: v["opaque_state_b64"] as! String)!)
      try c.initialize(keys: [3: Data(repeating: 30, count: 32)], history: h)
    }
    return c
  }
  func testActivationCrashChild() throws {
    guard let path = ProcessInfo.processInfo.environment["ATLAS_C26_CHILD"],
      let stage = ProcessInfo.processInfo.environment["ATLAS_C26_STAGE"]
    else { return }
    let dir = URL(fileURLWithPath: path)
    let c = try activationDevice(dir, initialize: true)
    let v = try vector("atlasvault_activation_v1.json")
    let r = v["record"] as! [String: Any]
    let p = r["proof"] as! [String: Any]
    func point(_ name: String) throws {
      if name == stage {
        try Data(name.utf8).write(to: dir.appendingPathComponent("ready"))
        while true { Thread.sleep(forTimeInterval: 60) }
      }
    }
    if stage == "prepared" {
      try c.prepareRotation(p)
      try point(stage)
      return
    }
    try c.beginActivation(p)
    if stage == "missing_delivery" {
      XCTAssertThrowsError(
        try c.acceptRotation(
          p, acceptedRecord: r, agreementPrivateKey: Data(repeating: 99, count: 32)))
      XCTAssertEqual(try c.observation()["status"] as? String, "ACTIVATION_PENDING")
      try point(stage)
      return
    }
    _ = try c.acceptRotationForTesting(
      p, acceptedRecord: r, agreementPrivateKey: Data(repeating: 20, count: 32), checkpoint: point)
  }
  func testRealProcessKillAndRestartAcrossActivationBoundaries() throws {
    for stage in [
      "prepared", "backend_accepted", "local_publishing", "before_local_commit",
      "after_local_commit", "missing_delivery",
    ] {
      let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      let p = Process()
      p.executableURL = URL(fileURLWithPath: "/usr/bin/xcrun")
      p.arguments = [
        "xctest", "-XCTest", "AtlasUITests.AtlasVaultEpochRotationTests/testActivationCrashChild",
        Bundle(for: Self.self).bundleURL.path,
      ]
      p.environment = ProcessInfo.processInfo.environment.merging([
        "ATLAS_C26_CHILD": dir.path, "ATLAS_C26_STAGE": stage,
      ]) { _, new in new }
      p.standardOutput = FileHandle.nullDevice
      p.standardError = FileHandle.nullDevice
      try p.run()
      defer {
        if p.isRunning {
          Darwin.kill(p.processIdentifier, SIGKILL)
          p.waitUntilExit()
        }
        try? FileManager.default.removeItem(at: dir)
      }
      let ready = dir.appendingPathComponent("ready")
      let limit = Date().addingTimeInterval(30)
      while !FileManager.default.fileExists(atPath: ready.path) && Date() < limit && p.isRunning {
        Thread.sleep(forTimeInterval: 0.05)
      }
      XCTAssertTrue(FileManager.default.fileExists(atPath: ready.path), stage)
      guard p.isRunning else {
        XCTFail("Activation child exited before barrier")
        return
      }
      XCTAssertEqual(Darwin.kill(p.processIdentifier, SIGKILL), 0)
      p.waitUntilExit()
      let c = try activationDevice(dir)
      let observation = try c.observation()
      let complete = stage == "after_local_commit"
      let pending = stage != "prepared" && stage != "after_local_commit"
      XCTAssertEqual(observation["key_epoch"] as? Int, complete ? 4 : 3)
      XCTAssertEqual(observation["status"] as? String, pending ? "ACTIVATION_PENDING" : "ACTIVE")
      if !complete { XCTAssertNotNil(try c.pendingActivation()) }
      let signing = try Curve25519.Signing.PrivateKey(
        rawRepresentation: Data(repeating: 10, count: 32))
      if pending {
        XCTAssertThrowsError(
          try c.seal(
            "patch", plaintext: Data(repeating: 7, count: 32), objectID: "probe", revision: "probe",
            signingKey: signing)
        ) { XCTAssertEqual($0 as? AtlasVaultRotationError, .pending) }
        if stage != "missing_delivery" {
          let r = try vector("atlasvault_activation_v1.json")["record"] as! [String: Any]
          XCTAssertTrue(
            try c.acceptRotation(
              r["proof"] as! [String: Any], acceptedRecord: r,
              agreementPrivateKey: Data(repeating: 20, count: 32)))
          XCTAssertEqual(try activationDevice(dir).observation()["key_epoch"] as? Int, 4)
        }
      }
      print(
        "C26 D087 Swift SIGKILL stage=\(stage) pid=\(p.processIdentifier) status=\(observation["status"]!) epoch=\(observation["key_epoch"]!)"
      )
    }
  }
  func vector(_ name: String = "atlasvault_epoch_rotation_v1.json") throws -> [String: Any] {
    var root = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { root.deleteLastPathComponent() }
    return try JSONSerialization.jsonObject(
      with: Data(
        contentsOf: root.appendingPathComponent(
          "contracts/sync/test_vectors/\(name)"))) as! [String: Any]
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
