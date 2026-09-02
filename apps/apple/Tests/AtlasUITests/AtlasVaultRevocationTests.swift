import CryptoKit
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultRevocationTests: XCTestCase {
  private var directory: URL!
  private var vector: [String: Any]!
  override func setUpWithError() throws {
    directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    var root = URL(fileURLWithPath: #filePath)
    for _ in 0..<6 { root.deleteLastPathComponent() }
    vector =
      try JSONSerialization.jsonObject(
        with: Data(
          contentsOf: root.appendingPathComponent(
            "contracts/sync/test_vectors/atlasvault_revocation_v1.json"))) as? [String: Any]
  }
  override func tearDownWithError() throws { try FileManager.default.removeItem(at: directory) }
  private func entries(_ name: String = "registry") -> [[String: Any]] {
    vector[name] as! [[String: Any]]
  }
  private func object(_ name: String) -> [String: Any] { vector[name] as! [String: Any] }
  private func store(_ name: String = "A") throws -> AtlasVaultRevocationRegistry {
    try AtlasVaultRevocationRegistry(
      fileURL: directory.appendingPathComponent(name), encryptionKey: Data(repeating: 7, count: 32),
      accountID: "account-c25", vaultID: "vault-c25", keyEpoch: 3, registry: entries(),
      stateRoot: String(repeating: "ab", count: 32))
  }
  func testSharedSignedVectorsAndIndependentPersistedDevices() throws {
    XCTAssertEqual(
      try AtlasVaultRevocation.registryRoot(entries()),
      object("transition")["prior_registry_root"] as? String)
    XCTAssertEqual(
      try AtlasVaultRevocation.registryRoot(entries("revoked_registry")),
      object("transition")["resulting_registry_root"] as? String)
    try AtlasVaultRevocation.verify(object("transition"), registry: entries())
    try AtlasVaultRevocation.validateRotationPlan(
      object("rotation_plan"), transition: object("transition"),
      registry: entries("revoked_registry"), stateRoot: String(repeating: "ab", count: 32))
    for name in ["A", "B"] {
      let client = try store(name)
      try client.initialize()
      XCTAssertTrue(try client.commit(object("transition")))
      XCTAssertFalse(try store(name).commit(object("transition")))
      let restarted = try store(name).snapshot()
      XCTAssertEqual(restarted["status"] as? String, "REVOCATION_PENDING")
      XCTAssertTrue(
        NSDictionary(dictionary: ["registry": restarted["registry"]!]).isEqual(to: [
          "registry": entries("revoked_registry")
        ]))
    }
  }
  func testEverySignedFieldAndEpochFieldRejectsSubstitution() throws {
    let client = try store()
    try client.initialize()
    for field in object("transition").keys {
      var bad = object("transition")
      if let number = bad[field] as? Int {
        bad[field] = number + 1
      } else {
        bad[field] = "substitution"
      }
      XCTAssertThrowsError(try client.commit(bad), field)
      XCTAssertEqual(try client.snapshot()["sequence"] as? Int, 0)
    }
    for field in object("rotation_plan").keys {
      var bad = object("rotation_plan")
      if let number = bad[field] as? Int {
        bad[field] = number + 1
      } else {
        bad[field] = "substitution"
      }
      XCTAssertThrowsError(
        try AtlasVaultRevocation.validateRotationPlan(
          bad, transition: object("transition"), registry: entries("revoked_registry"),
          stateRoot: String(repeating: "ab", count: 32)), field)
    }
  }
  @MainActor func testAuthorizationBindingFailuresNeverSign() async throws {
    let a = entries()[0]["device_id"] as! String
    let b = entries()[1]["device_id"] as! String
    for attack in [
      "denied", "cancelled", "unavailable", "malformed", "exception", "target", "registry",
      "cancel", "timeout", "wrong-confirmation", "fork",
    ] {
      let client = try store(attack)
      try client.initialize()
      var tick: TimeInterval = 0
      var controller: AtlasVaultRemovalController!
      controller = AtlasVaultRemovalController(
        registry: client, initiator: a, clock: { tick },
        authorize: {
          if attack == "target" { controller.select(a) }
          if attack == "registry" { _ = try client.commit(self.object("transition")) }
          if attack == "cancel" { controller.cancel() }
          if attack == "timeout" { tick = 61 }
          if attack == "exception" { throw NSError(domain: "private sentinel", code: 1) }
          if ["denied", "cancelled", "unavailable"].contains(attack) { return false }
          if attack == "malformed" { return "true" }
          return true
        },
        sign: { _ in
          XCTFail("unauthorized signing")
          return Data()
        })
      controller.select(b)
      if attack == "fork" { try client.fence() }
      do {
        _ = try await controller.remove(confirmedTarget: attack == "wrong-confirmation" ? a : b)
        XCTFail(attack)
      } catch { XCTAssertFalse(String(describing: error).contains("private sentinel")) }
      if attack != "registry" { XCTAssertEqual(try client.snapshot()["sequence"] as? Int, 0) }
    }
  }
  @MainActor func testFreshPromptAndExactSigning() async throws {
    let client = try store()
    try client.initialize()
    let key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(0..<32))
    var calls = 0
    let controller = AtlasVaultRemovalController(
      registry: client, initiator: entries()[0]["device_id"] as! String,
      authorize: {
        calls += 1
        return calls == 2
      }, sign: { try key.signature(for: $0) })
    let target = entries()[1]["device_id"] as! String
    controller.select(target)
    do {
      _ = try await controller.remove(confirmedTarget: target)
      XCTFail("denial accepted")
    } catch {}
    let signed = try await controller.remove(confirmedTarget: target)
    XCTAssertTrue(NSDictionary(dictionary: signed).isEqual(to: object("transition")))
    XCTAssertEqual(calls, 2)
  }
}
