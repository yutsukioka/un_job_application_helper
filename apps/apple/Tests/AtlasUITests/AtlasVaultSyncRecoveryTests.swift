import CryptoKit
import Darwin
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultSyncRecoveryTests: XCTestCase {
  private func vectors() throws -> [String: Any] {
    var root = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { root.deleteLastPathComponent() }
    return try JSONSerialization.jsonObject(
      with: Data(
        contentsOf: root.appendingPathComponent(
          "contracts/sync/test_vectors/atlasvault_sync_recovery_vectors_v1.json")))
      as! [String: Any]
  }
  private func client(_ file: URL) throws -> AtlasVaultGuardedSyncState {
    try AtlasVaultGuardedSyncState(
      fileURL: file, encryptionKey: Data((0..<32).map(UInt8.init)), accountID: "account_c22",
      vaultID: "vault_c22", collectionID: "collection_c21", keyEpoch: 2,
      trustedSigner: Data(base64Encoded: vectors()["signing_public_b64"] as! String)!)
  }
  private func serve(_ name: String, _ c: AtlasVaultGuardedSyncState) throws -> Bool {
    let p = (try vectors()["packets"] as! [String: [String: Any]])[name]!
    return try c.ingest(
      view: p["view"] as! [String: Any], registry: p["registry"] as! [[String: Any]],
      collection: p["collection"] as! [String: Any],
      opaqueState: Data(base64Encoded: p["opaque_b64"] as! String)!)
  }
  private func ready(_ file: URL) throws -> AtlasVaultGuardedSyncState {
    let c = try client(file)
    try c.initialize()
    _ = try serve("one", c)
    _ = try serve("two", c)
    return c
  }
  func testMaliciousDeliveriesFenceIndependentPersistedClients() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: dir) }
    for attack in try vectors()["attacks"] as! [[String: String]] {
      let a = try ready(dir.appendingPathComponent(attack["name"]! + "/A"))
      let bURL = dir.appendingPathComponent(attack["name"]! + "/B")
      let b = try ready(bURL)
      let before = try b.checkpoint()
      XCTAssertEqual(
        NSDictionary(dictionary: before), try vectors()["expected_checkpoint"] as? NSDictionary)
      XCTAssertThrowsError(try serve(attack["packet"]!, b)) {
        XCTAssertEqual(($0 as? AtlasVaultSyncRecoveryError)?.rawValue, attack["reason"])
      }
      let reopened = try client(bURL)
      XCTAssertEqual(
        NSDictionary(dictionary: try reopened.checkpoint()), NSDictionary(dictionary: before))
      XCTAssertEqual(NSDictionary(dictionary: try a.checkpoint()), NSDictionary(dictionary: before))
      let ui = try reopened.recovery()
      XCTAssertEqual(ui["status"] as? String, "MANUAL_REQUIRED")
      XCTAssertEqual(ui["reason"] as? String, attack["reason"])
      var calls = 0
      XCTAssertThrowsError(try reopened.automaticSync { calls += 1 })
      XCTAssertEqual(calls, 0)
      XCTAssertThrowsError(try serve("three", reopened))
      let text = String(decoding: try JSONSerialization.data(withJSONObject: ui), as: UTF8.self)
      XCTAssertLessThan(text.count, 160000)
      for forbidden in [
        "ciphertext_b64", "opaque_b64", "passphrase", "vault_key", "access_token", "nonce_b64",
      ] { XCTAssertFalse(text.contains(forbidden)) }
    }
  }
  func testManualForkKeepsBothEvidenceBranchesAndAcceptedHistory() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: dir) }
    let aURL = dir.appendingPathComponent("A")
    let a = try ready(aURL)
    let b = try client(dir.appendingPathComponent("B"))
    try b.initialize()
    _ = try serve("one", b)
    _ = try serve("fork_two", b)
    let left = try a.exportEvidence()
    let right = try b.exportEvidence()
    let before = try a.checkpoint()
    XCTAssertThrowsError(try a.compareEvidence(right))
    let evidence = try a.evidence()
    XCTAssertEqual(evidence["local"] as? NSArray, left as NSArray)
    XCTAssertEqual(evidence["peer"] as? NSArray, right as NSArray)
    let ui = try a.recovery()
    let local = (ui["local"] as! [[String: Any]]).last!["root"] as! String
    let peer = (ui["peer"] as! [[String: Any]]).last!["root"] as! String
    XCTAssertEqual(
      try a.resolve("select_peer", localRoot: local, peerRoot: peer), "RECOVERY_PENDING")
    let reopened = try client(aURL)
    XCTAssertEqual(
      NSDictionary(dictionary: try reopened.evidence()), NSDictionary(dictionary: evidence))
    XCTAssertEqual(
      NSDictionary(dictionary: try reopened.checkpoint()), NSDictionary(dictionary: before))
    XCTAssertEqual(try reopened.recovery()["disposition"] as? String, "select_peer")
    XCTAssertThrowsError(try reopened.automaticSync { 0 })
  }
  func testExplicitKnownReplayDispositionAllowsOnlyUnchangedHistory() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: dir) }
    let c = try ready(dir.appendingPathComponent("C"))
    let before = try c.checkpoint()
    XCTAssertThrowsError(try serve("one", c))
    let ui = try c.recovery()
    let local = (ui["local"] as! [[String: Any]]).last!["root"] as! String
    let peer = (ui["peer"] as! [[String: Any]]).last!["root"] as! String
    XCTAssertThrowsError(
      try c.resolve("retain_accepted", localRoot: String(repeating: "f", count: 64), peerRoot: peer)
    )
    XCTAssertEqual(try c.resolve("retain_accepted", localRoot: local, peerRoot: peer), "ACTIVE")
    XCTAssertEqual(NSDictionary(dictionary: try c.checkpoint()), NSDictionary(dictionary: before))
    XCTAssertEqual(try c.automaticSync { 7 }, 7)
    XCTAssertTrue(try serve("three", c))
    let accepted = try c.checkpoint()
    XCTAssertFalse(try serve("three", c))
    XCTAssertEqual(NSDictionary(dictionary: try c.checkpoint()), NSDictionary(dictionary: accepted))
  }
  func testCrashChild() throws {
    guard let path = ProcessInfo.processInfo.environment["ATLAS_C23_CHILD"] else { return }
    let dir = URL(fileURLWithPath: path)
    let c = try ready(dir.appendingPathComponent("state"))
    XCTAssertThrowsError(try serve("fork_two", c))
    XCTAssertEqual(try c.recovery()["status"] as? String, "MANUAL_REQUIRED")
    try Data("alarm durable".utf8).write(to: dir.appendingPathComponent("ready"))
    while true { Thread.sleep(forTimeInterval: 60) }
  }
  func testAlarmSurvivesProcessKill() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: dir) }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/xcrun")
    p.arguments = [
      "xctest", "-XCTest", "AtlasUITests.AtlasVaultSyncRecoveryTests/testCrashChild",
      Bundle(for: Self.self).bundleURL.path,
    ]
    p.environment = ProcessInfo.processInfo.environment.merging(["ATLAS_C23_CHILD": dir.path]) {
      _, new in new
    }
    try p.run()
    defer {
      if p.isRunning {
        Darwin.kill(p.processIdentifier, SIGKILL)
        p.waitUntilExit()
      }
    }
    let limit = Date().addingTimeInterval(30)
    let signal = dir.appendingPathComponent("ready")
    while !FileManager.default.fileExists(atPath: signal.path) && Date() < limit && p.isRunning {
      Thread.sleep(forTimeInterval: 0.05)
    }
    XCTAssertTrue(FileManager.default.fileExists(atPath: signal.path))
    guard p.isRunning else {
      XCTFail("child exited early")
      return
    }
    XCTAssertEqual(Darwin.kill(p.processIdentifier, SIGKILL), 0)
    p.waitUntilExit()
    let c = try client(dir.appendingPathComponent("state"))
    XCTAssertEqual(try c.recovery()["status"] as? String, "MANUAL_REQUIRED")
    XCTAssertThrowsError(try c.automaticSync { 0 })
  }
}
