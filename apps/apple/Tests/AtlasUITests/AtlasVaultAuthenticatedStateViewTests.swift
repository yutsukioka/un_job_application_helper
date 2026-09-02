import CryptoKit
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultAuthenticatedStateViewTests: XCTestCase {
  private let key = Data((0..<32).map(UInt8.init))
  private func vectors() throws -> [String: Any] {
    var root = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { root.deleteLastPathComponent() }
    return try JSONSerialization.jsonObject(
      with: Data(
        contentsOf: root.appendingPathComponent(
          "contracts/sync/test_vectors/atlasvault_authenticated_state_view_vectors_v2.json")))
      as! [String: Any]
  }
  private func client(_ url: URL) throws -> AtlasVaultAuthenticatedHistory {
    try AtlasVaultAuthenticatedHistory(
      fileURL: url, encryptionKey: key, accountID: "account_c22", vaultID: "vault_c22",
      collectionID: "collection_c21", keyEpoch: 1,
      trustedSigner: Data(base64Encoded: vectors()["signing_public_b64"] as! String)!)
  }
  private struct MaliciousServer {
    var p: [String: Any]
    func serve(_ c: AtlasVaultAuthenticatedHistory) throws -> Bool {
      try c.observe(
        view: p["view"] as! [String: Any], registry: p["registry"] as! [[String: Any]],
        collection: p["collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: p["opaque_b64"] as! String)!)
    }
  }
  private func fixture(_ name: String) throws -> MaliciousServer {
    MaliciousServer(p: (try vectors()["packages"] as! [String: [String: Any]])[name]!)
  }
  func testSharedRootsAndSignedRegistryTransitions() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: dir) }
    let c = try client(dir.appendingPathComponent("C"))
    try c.initialize()
    for name in ["one", "two", "three"] {
      let s = try fixture(name)
      let v = s.p["view"] as! [String: Any]
      var unsigned = v
      unsigned.removeValue(forKey: "root")
      unsigned.removeValue(forKey: "signature_b64")
      let signed = try AtlasVaultAuthenticatedStateView.sign(
        unsigned, signingKey: Curve25519.Signing.PrivateKey(rawRepresentation: key))
      XCTAssertEqual(signed["root"] as? String, v["root"] as? String)
      XCTAssertEqual(
        try AtlasVaultAuthenticatedStateView.registryRoot(s.p["registry"] as! [[String: Any]]),
        v["registry_root"] as? String)
      var generated = s
      generated.p["view"] = signed
      XCTAssertTrue(try generated.serve(c))
      XCTAssertFalse(try s.serve(c))
    }
    XCTAssertEqual(try client(dir.appendingPathComponent("C")).exportEvidence().count, 3)
  }
  func testIndependentPersistedClientsCompareConflictingViews() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: dir) }
    for fork in ["fork_two", "fork_state", "fork_three"] {
      let aURL = dir.appendingPathComponent(fork + "/A/anchor")
      let bURL = dir.appendingPathComponent(fork + "/B/anchor")
      var a = try client(aURL)
      var b = try client(bURL)
      try a.initialize()
      try b.initialize()
      for c in [a, b] { _ = try fixture("one").serve(c) }
      _ = try fixture("two").serve(a)
      _ = try fixture(fork == "fork_three" ? "fork_two" : fork).serve(b)
      if fork == "fork_three" { _ = try fixture(fork).serve(b) }
      a = try client(aURL)
      b = try client(bURL)
      let left = try a.exportEvidence()
      let right = try b.exportEvidence()
      XCTAssertNotEqual(left[1]["root"] as? String, right[1]["root"] as? String)
      for (c, evidence) in [(a, right), (b, left)] {
        XCTAssertThrowsError(try c.compareEvidence(evidence)) {
          XCTAssertEqual($0 as? AtlasVaultStateViewError, .equivocation)
        }
      }
      XCTAssertThrowsError(try fixture("three").serve(client(aURL))) {
        XCTAssertEqual($0 as? AtlasVaultStateViewError, .equivocation)
      }
    }
  }
  func testSubstitutedAddedRemovedAndRolledBackRegistries() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: dir) }
    for attack in ["substituted", "added", "removed", "registry_rollback"] {
      let a = try client(dir.appendingPathComponent(attack + "/A"))
      let b = try client(dir.appendingPathComponent(attack + "/B"))
      try a.initialize()
      try b.initialize()
      for c in [a, b] { _ = try fixture("one").serve(c) }
      _ = try fixture("two").serve(a)
      var s = try fixture("two")
      var registry = s.p["registry"] as! [[String: Any]]
      if attack == "removed" || attack == "registry_rollback" {
        registry = try fixture("one").p["registry"] as! [[String: Any]]
      } else if attack == "added" {
        registry.append([
          "device_id": String(repeating: "f", count: 64),
          "descriptor_sha256": String(repeating: "e", count: 64),
        ])
      } else {
        registry[0]["descriptor_sha256"] = String(repeating: "f", count: 64)
      }
      s.p["registry"] = registry
      XCTAssertThrowsError(try s.serve(b)) {
        XCTAssertEqual($0 as? AtlasVaultStateViewError, .registrySubstitution)
      }
      XCTAssertEqual(try b.exportEvidence().count, 1)
    }
  }
  func testContextTamperRollbackAndMissingEvidence() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: dir) }
    let a = try client(dir.appendingPathComponent("A"))
    let b = try client(dir.appendingPathComponent("B"))
    try a.initialize()
    try b.initialize()
    for name in ["other_account", "other_vault", "other_epoch"] {
      XCTAssertThrowsError(try fixture(name).serve(a))
    }
    for field in try fixture("one").p["view"] as! [String: Any] {
      var s = try fixture("one")
      var v = s.p["view"] as! [String: Any]
      if let n = field.value as? NSNumber {
        v[field.key] = n.int64Value + 1
      } else {
        v[field.key] = "x" + (field.value as! String).dropFirst()
      }
      s.p["view"] = v
      XCTAssertThrowsError(try s.serve(a))
    }
    XCTAssertEqual(try a.exportEvidence().count, 0)
    XCTAssertThrowsError(try a.compareEvidence([]))
    for c in [a, b] { _ = try fixture("one").serve(c) }
    _ = try fixture("two").serve(a)
    XCTAssertEqual(try a.compareEvidence(b.exportEvidence()), 1)
    XCTAssertThrowsError(try fixture("one").serve(a)) {
      XCTAssertEqual($0 as? AtlasVaultStateViewError, .rollback)
    }
    var bad = try a.exportEvidence()
    bad[1]["previous_root"] = String(repeating: "e", count: 64)
    XCTAssertThrowsError(try b.compareEvidence(bad))
    XCTAssertThrowsError(try client(dir.appendingPathComponent("missing")).exportEvidence())
    try Data("corrupt".utf8).write(to: dir.appendingPathComponent("B"))
    XCTAssertThrowsError(try b.exportEvidence())
  }
}
