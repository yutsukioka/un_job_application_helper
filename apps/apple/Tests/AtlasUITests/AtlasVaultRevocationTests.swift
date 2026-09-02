import CryptoKit
import Darwin
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
    for _ in 0..<5 { root.deleteLastPathComponent() }
    vector =
      try JSONSerialization.jsonObject(
        with: Data(
          contentsOf: root.appendingPathComponent(
            "contracts/sync/test_vectors/atlasvault_revocation_v1.json"))) as? [String: Any]
  }
  override func tearDownWithError() throws { try FileManager.default.removeItem(at: directory) }
  func testCrashChild() throws {
    guard let path = ProcessInfo.processInfo.environment["ATLAS_C25_CHILD"] else { return }
    let file = URL(fileURLWithPath: path)
    let registry = try AtlasVaultRevocationRegistry(
      fileURL: file, encryptionKey: Data(repeating: 7, count: 32), accountID: "account-c25",
      vaultID: "vault-c25", keyEpoch: 3, registry: entries(),
      stateRoot: String(repeating: "ab", count: 32))
    try registry.initialize()
    try registry.commit(object("transition"))
    try Data("DURABLE".utf8).write(to: file.appendingPathExtension("ready"))
    while true { Thread.sleep(forTimeInterval: 60) }
  }
  func testTwoIndependentDevicesSurviveKill() throws {
    for name in ["A", "B"] {
      let file = directory.appendingPathComponent(name)
      let ready = directory.appendingPathComponent(name).appendingPathExtension("ready")
      let child = Process()
      child.executableURL = URL(fileURLWithPath: "/usr/bin/xcrun")
      child.arguments = [
        "xctest", "-XCTest", "AtlasUITests.AtlasVaultRevocationTests/testCrashChild",
        Bundle(for: Self.self).bundleURL.path,
      ]
      child.environment = ProcessInfo.processInfo.environment.merging(["ATLAS_C25_CHILD": file.path]
      ) { _, new in new }
      try child.run()
      defer {
        if child.isRunning {
          Darwin.kill(child.processIdentifier, SIGKILL)
          child.waitUntilExit()
        }
      }
      let deadline = Date().addingTimeInterval(30)
      while !FileManager.default.fileExists(atPath: ready.path) && child.isRunning
        && Date() < deadline
      { Thread.sleep(forTimeInterval: 0.05) }
      XCTAssertTrue(FileManager.default.fileExists(atPath: ready.path))
      XCTAssertEqual(Darwin.kill(child.processIdentifier, SIGKILL), 0)
      child.waitUntilExit()
      let restarted = try store(name)
      XCTAssertEqual(try restarted.snapshot()["status"] as? String, "REVOCATION_PENDING")
      XCTAssertFalse(try restarted.commit(object("transition")))
      XCTAssertThrowsError(try restarted.initialize())
    }
  }
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
  func testNoSecretFieldsEnterTransitionOrRotationMetadata() throws {
    let client = try store()
    try client.initialize()
    for field in [
      "passphrase", "vault_key", "wrapped_vault_key", "access_token", "plaintext",
      "signing_private_key",
    ] {
      var transition = object("transition")
      var plan = object("rotation_plan")
      transition[field] = "forbidden-sentinel"
      plan[field] = "forbidden-sentinel"
      XCTAssertThrowsError(try client.commit(transition))
      XCTAssertThrowsError(
        try AtlasVaultRevocation.validateRotationPlan(
          plan, transition: object("transition"), registry: entries("revoked_registry"),
          stateRoot: String(repeating: "ab", count: 32)))
    }
    XCTAssertEqual(try client.snapshot()["sequence"] as? Int, 0)
  }
  @MainActor func testActualP6ForkFencesRemovalBeforeAuthorization() async throws {
    var root = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { root.deleteLastPathComponent() }
    let p6 =
      try JSONSerialization.jsonObject(
        with: Data(
          contentsOf: root.appendingPathComponent(
            "contracts/sync/test_vectors/atlasvault_sync_recovery_vectors_v1.json")))
      as! [String: Any]
    let history = try AtlasVaultGuardedSyncState(
      fileURL: directory.appendingPathComponent("history"),
      encryptionKey: Data(repeating: 8, count: 32), accountID: "account_c22", vaultID: "vault_c22",
      collectionID: "collection_c21", keyEpoch: 2,
      trustedSigner: Data(base64Encoded: p6["signing_public_b64"] as! String)!)
    try history.initialize()
    for name in ["one", "two", "fork_two"] {
      let p = (p6["packets"] as! [String: [String: Any]])[name]!
      let deliver = {
        try history.ingest(
          view: p["view"] as! [String: Any], registry: p["registry"] as! [[String: Any]],
          collection: p["collection"] as! [String: Any],
          opaqueState: Data(base64Encoded: p["opaque_b64"] as! String)!)
      }
      if name == "fork_two" { XCTAssertThrowsError(try deliver()) } else { _ = try deliver() }
    }
    let client = try AtlasVaultRevocationRegistry(
      fileURL: directory.appendingPathComponent("registry"),
      encryptionKey: Data(repeating: 7, count: 32), accountID: "account_c22", vaultID: "vault_c22",
      keyEpoch: 2, registry: entries(), stateRoot: history.checkpoint()["cursor"] as! String)
    try client.initialize()
    let controller = AtlasVaultRemovalController(
      registry: client, initiator: entries()[0]["device_id"] as! String, history: history,
      authorize: {
        XCTFail("P6 fork reached prompt")
        return false
      },
      sign: { _ in
        XCTFail("P6 fork reached signing")
        return Data()
      })
    let target = entries()[1]["device_id"] as! String
    controller.select(target)
    do {
      _ = try await controller.remove(confirmedTarget: target)
      XCTFail("fork accepted")
    } catch {}
    XCTAssertEqual(try client.snapshot()["status"] as? String, "RECOVERY_PENDING")
    XCTAssertEqual(try client.snapshot()["sequence"] as? Int, 0)
    XCTAssertEqual(try history.recovery()["status"] as? String, "MANUAL_REQUIRED")
  }
  func testValidlySignedHostileContextAndConflictingResult() throws {
    for attack in vector["attacks"] as! [[String: Any]] {
      let client = try store(attack["name"] as! String)
      try client.initialize()
      if attack["after_revocation"] as? Bool == true { try client.commit(object("transition")) }
      let before = try client.snapshot()
      XCTAssertThrowsError(try client.commit(attack["transition"] as! [String: Any]))
      XCTAssertTrue(NSDictionary(dictionary: try client.snapshot()).isEqual(to: before))
    }
  }
  func testLastDeviceAndUnauthorizedRemovalAreRejected() throws {
    let client = try AtlasVaultRevocationRegistry(
      fileURL: directory.appendingPathComponent("solo"),
      encryptionKey: Data(repeating: 7, count: 32), accountID: "account-c25", vaultID: "vault-c25",
      keyEpoch: 3, registry: Array(entries().prefix(1)),
      stateRoot: String(repeating: "ab", count: 32))
    try client.initialize()
    let a = entries()[0]["device_id"] as! String
    let b = entries()[1]["device_id"] as! String
    for pair in [[a, a], [a, b], [b, a]] {
      XCTAssertThrowsError(try client.prepare(target: pair[0], initiator: pair[1]))
    }
    XCTAssertEqual(try client.snapshot()["sequence"] as? Int, 0)
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
    for field in object("transition").keys where field != "signature_b64" {
      XCTAssertTrue(
        NSDictionary(dictionary: [field: signed[field]!]).isEqual(to: [
          field: object("transition")[field]!
        ]), "signed field differs: \(field)")
    }
    try AtlasVaultRevocation.verify(signed, registry: entries())
    XCTAssertFalse(try client.commit(object("transition")))
    if let output = ProcessInfo.processInfo.environment["ATLAS_C25_PUBLIC_TRANSITION"] {
      try JSONSerialization.data(withJSONObject: signed, options: [.sortedKeys]).write(
        to: URL(fileURLWithPath: output))
    }
    XCTAssertEqual(calls, 2)
  }
}
