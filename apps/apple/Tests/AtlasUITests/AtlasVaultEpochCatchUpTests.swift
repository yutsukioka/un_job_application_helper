import CryptoKit
import Foundation
import XCTest
import Darwin

@testable import AtlasUI

final class AtlasVaultEpochCatchUpTests: XCTestCase {
  func testCatchUpCrashChild() throws {
    guard let path=ProcessInfo.processInfo.environment["ATLAS_C27_CHILD"],let stage=ProcessInfo.processInfo.environment["ATLAS_C27_STAGE"] else {return}
    let root=URL(fileURLWithPath:path),v=try vector("atlasvault_epoch_catch_up_history_v2")
    try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
    let c=try device(root,2,v,initialize:true)
    func point(_ name:String) throws {if name==stage {try Data(name.utf8).write(to:root.appendingPathComponent("ready"));while true {Thread.sleep(forTimeInterval:60)}}}
    _ = try c.catchUpForTesting((v["packets"] as! [[[String:Any]]])[2],currentActivationID:v["target_activation_id"] as! String,agreementPrivateKey:Data(repeating:22,count:32),historyUpdates:v["history_updates"] as! [[String:Any]],checkpoint:point)
    if ["cleanup_pending","deleted_epoch"].contains(stage) {
      try c.cleanupEpochsForTesting(retainEpochs:[5],deleteEpoch:{epoch in try Data("deleted".utf8).write(to:root.appendingPathComponent("deleted-\(epoch)"))},containsEpoch:{epoch in !FileManager.default.fileExists(atPath:root.appendingPathComponent("deleted-\(epoch)").path)},checkpoint:point)
    }
  }
  func testRealCatchUpKillAndRestart() throws {
    let v=try vector("atlasvault_epoch_catch_up_history_v2")
    for stage in ["catch_up_pending","verified_epoch","before_local_commit","after_local_commit","cleanup_pending","deleted_epoch"] {
      let root=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString),p=Process()
      p.executableURL=URL(fileURLWithPath:"/usr/bin/xcrun")
      p.arguments=["xctest","-XCTest","AtlasUITests.AtlasVaultEpochCatchUpTests/testCatchUpCrashChild",Bundle(for:Self.self).bundleURL.path]
      p.environment=ProcessInfo.processInfo.environment.merging(["ATLAS_C27_CHILD":root.path,"ATLAS_C27_STAGE":stage]){_,new in new}
      p.standardOutput=FileHandle.nullDevice;p.standardError=FileHandle.nullDevice
      try p.run()
      defer {if p.isRunning {Darwin.kill(p.processIdentifier,SIGKILL);p.waitUntilExit()};try? FileManager.default.removeItem(at:root)}
      let ready=root.appendingPathComponent("ready"),deadline=Date().addingTimeInterval(30)
      while !FileManager.default.fileExists(atPath:ready.path)&&Date()<deadline&&p.isRunning {Thread.sleep(forTimeInterval:0.05)}
      XCTAssertTrue(FileManager.default.fileExists(atPath:ready.path),stage)
      guard p.isRunning else {XCTFail("C27 worker exited before barrier");return}
      XCTAssertEqual(Darwin.kill(p.processIdentifier,SIGKILL),0);p.waitUntilExit()
      let c=try device(root,2,v),o=try c.observation()
      XCTAssertEqual(o["key_epoch"] as? Int,["after_local_commit","cleanup_pending","deleted_epoch"].contains(stage) ? 5 : 3)
      if stage != "after_local_commit" {
        XCTAssertThrowsError(try c.seal("patch",plaintext:Data([7]),objectID:"probe",revision:"r1",signingKey:Curve25519.Signing.PrivateKey(rawRepresentation:Data(repeating:10,count:32))))
      }
      if ["cleanup_pending","deleted_epoch"].contains(stage) {
        try c.cleanupEpochs(retainEpochs:[5],deleteEpoch:{epoch in try Data("deleted".utf8).write(to:root.appendingPathComponent("deleted-\(epoch)"))},containsEpoch:{epoch in !FileManager.default.fileExists(atPath:root.appendingPathComponent("deleted-\(epoch)").path)})
        XCTAssertEqual(try c.availableEpochs(),[5])
      } else {
        _ = try c.catchUp((v["packets"] as! [[[String:Any]]])[2],currentActivationID:v["target_activation_id"] as! String,agreementPrivateKey:Data(repeating:22,count:32),historyUpdates:v["history_updates"] as! [[String:Any]])
      }
      XCTAssertEqual(try c.observation()["status"] as? String,"ACTIVE")
      print("C27 Swift SIGKILL stage=\(stage) pid=\(p.processIdentifier) resumed_epoch=5")
    }
  }
  func vector(_ name: String = "atlasvault_epoch_catch_up_v2") throws -> [String: Any] {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    return try JSONSerialization.jsonObject(
      with: Data(
        contentsOf: root.appendingPathComponent(
          "contracts/sync/test_vectors/\(name).json"))) as! [String: Any]
  }
  func device(_ root: URL, _ i: Int, _ v: [String: Any], initialize: Bool = false) throws
    -> AtlasVaultEpochVault
  {
    let view = v["initial_view"] as! [String: Any]
    let c = try AtlasVaultEpochVault(
      directory: root.appendingPathComponent(String(i)),
      storageKey: Data(repeating: UInt8(50 + i), count: 32),
      deviceID: (v["device_ids"] as! [String])[i],
      registry: v["initial_registry"] as! [[String: Any]], accountID: view["account_id"] as! String,
      vaultID: "vault-c26", keyEpoch: 3, stateRoot: view["root"] as! String)
    if initialize {
      let h = try AtlasVaultGuardedSyncState(
        fileURL: root.appendingPathComponent("history-\(i)"),
        encryptionKey: Data(repeating: UInt8(60 + i), count: 32),
        accountID: view["account_id"] as! String, vaultID: "vault-c26",
        collectionID: "collection-c26", keyEpoch: 3,
        trustedSigner: Curve25519.Signing.PrivateKey(
          rawRepresentation: Data(repeating: 10, count: 32)
        ).publicKey.rawRepresentation)
      try h.initialize()
      _ = try h.ingest(
        view: view, registry: v["initial_history_registry"] as! [[String: Any]],
        collection: v["initial_collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: v["opaque_state_b64"] as! String)!)
      try c.initialize(keys: [3: Data(repeating: 30, count: 32)], history: h)
    }
    return c
  }
  func testIndependentMultiEpochCatchUpRecoveryAndCleanup() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let v = try vector()
    var roots = Set<String>()
    for i in 0..<3 {
      let c = try device(root, i, v, initialize: true)
      let packets = (v["packets"] as! [[[String: Any]]])[i]
      XCTAssertTrue(
        try c.catchUp(
          packets, currentActivationID: v["target_activation_id"] as! String,
          agreementPrivateKey: Data(repeating: UInt8(20 + i), count: 32)))
      XCTAssertFalse(
        try c.catchUp(
          packets, currentActivationID: v["target_activation_id"] as! String,
          agreementPrivateKey: Data(repeating: UInt8(20 + i), count: 32)))
      let reopened = try device(root, i, v)
      XCTAssertEqual(try reopened.observation()["key_epoch"] as? Int, 5)
      roots.insert(try reopened.observation()["registry_root"] as! String)
      try Data("corrupted synthetic publication".utf8).write(
        to: root.appendingPathComponent("\(i)/activation"))
      try reopened.recoverPublication()
      XCTAssertEqual(try reopened.observation()["status"] as? String, "CATCH_UP_PENDING")
      _ = try reopened.catchUp(packets,currentActivationID:v["target_activation_id"] as! String,agreementPrivateKey:Data(repeating:UInt8(20+i),count:32))
      XCTAssertEqual(try reopened.observation()["status"] as? String, "ACTIVE")
      var removed = Set<Int>()
      try reopened.cleanupEpochs(
        retainEpochs: [5], deleteEpoch: { removed.insert($0) },
        containsEpoch: { !removed.contains($0) })
      XCTAssertEqual(removed, [3, 4])
      XCTAssertEqual(try reopened.availableEpochs(), [5])
    }
    XCTAssertEqual(roots.count, 1)
  }
  func testInterveningAuthenticatedHistoryIsRequiredAndPreserved() throws {
    let v=try vector("atlasvault_epoch_catch_up_history_v2")
    let root=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
    defer {try? FileManager.default.removeItem(at:root)}
    let c=try device(root,2,v,initialize:true)
    let packets=(v["packets"] as! [[[String:Any]]])[2]
    XCTAssertThrowsError(try c.catchUp(packets,currentActivationID:v["target_activation_id"] as! String,agreementPrivateKey:Data(repeating:22,count:32)))
    XCTAssertEqual(try c.observation()["sequence"] as? Int,1)
    XCTAssertTrue(try c.catchUp(packets,currentActivationID:v["target_activation_id"] as! String,agreementPrivateKey:Data(repeating:22,count:32),historyUpdates:v["history_updates"] as! [[String:Any]]))
    XCTAssertEqual(try c.observation()["sequence"] as? Int,2)
    XCTAssertEqual(try c.observation()["state_root"] as? String,((v["history_updates"] as! [[String:Any]])[0]["view"] as! [String:Any])["root"] as? String)
  }
  func testCleanupIntentAndKeychainDeletionBoundary() throws {
    let v=try vector(),root=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
    defer {try? FileManager.default.removeItem(at:root)}
    let c=try device(root,2,v,initialize:true)
    _ = try c.catchUp((v["packets"] as! [[[String:Any]]])[2],currentActivationID:v["target_activation_id"] as! String,agreementPrivateKey:Data(repeating:22,count:32))
    XCTAssertThrowsError(try c.cleanupEpochsForTesting(retainEpochs:[5],deleteEpoch:{_ in},containsEpoch:{_ in false},checkpoint:{stage in if stage=="cleanup_pending" {throw AtlasVaultRotationError.rejected}}))
    let reopened=try device(root,2,v)
    XCTAssertEqual(try reopened.recovery()["status"] as? String,"CLEANUP_PENDING")
    XCTAssertThrowsError(try reopened.cleanupEpochs(retainEpochs:[4,5],deleteEpoch:{_ in},containsEpoch:{_ in false}))
    let store=AtlasKeychainVaultKeyStore(client:SecItemAtlasKeychainClient(),service:"atlasvault.c27.synthetic.\(UUID().uuidString)")
    let ids=[3:UUID().uuidString,4:UUID().uuidString,5:UUID().uuidString]
    defer {for id in ids.values {try? store.deleteVaultKey(for:id)}}
    for id in ids.values {try store.createVaultKey(Data(repeating:7,count:32),for:id)}
    try reopened.cleanupSecureStorage(retainEpochs:[5],epochStorageIDs:ids,store:store)
    XCTAssertNil(try store.loadVaultKey(for:ids[3]!));XCTAssertNil(try store.loadVaultKey(for:ids[4]!))
    XCTAssertNotNil(try store.loadVaultKey(for:ids[5]!))
    XCTAssertEqual(try reopened.availableEpochs(),[5])
  }
  func testMalformedChainsDoNotPartiallyActivate() throws {
    let v = try vector()
    for attack in ["missing", "reordered", "recipient", "state-root"] {
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
      defer { try? FileManager.default.removeItem(at: root) }
      let c = try device(root, 2, v, initialize: true)
      var packets = (v["packets"] as! [[[String: Any]]])[2]
      if attack == "missing" { packets.removeFirst() }
      if attack == "reordered" { packets.reverse() }
      if attack == "recipient" { packets[1] = (v["packets"] as! [[[String: Any]]])[0][1] }
      if attack == "state-root" {
        var p = packets[0]["proof"] as! [String: Any]
        var plan = p["plan"] as! [String: Any]
        plan["state_root"] = String(repeating: "ab", count: 32)
        p["plan"] = plan
        packets[0]["proof"] = p
      }
      XCTAssertThrowsError(
        try c.catchUp(
          packets, currentActivationID: v["target_activation_id"] as! String,
          agreementPrivateKey: Data(repeating: 22, count: 32)))
      XCTAssertEqual(try device(root, 2, v).observation()["status"] as? String, "CATCH_UP_PENDING")
      XCTAssertEqual(try device(root, 2, v).observation()["key_epoch"] as? Int, 3)
    }
  }
}
