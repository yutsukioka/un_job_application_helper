import CryptoKit
import Foundation
import Security
import XCTest

@testable import AtlasUI

final class AtlasVaultProductionEnrollmentTests: XCTestCase {
  func testNativeNamespacedKeychainInstallAndExactRetry() throws {
    let client = C30NamespacedKeychain()
    defer { client.cleanup() }
    let v = try fixture()
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let recipient = try AtlasVaultDeviceIdentity(
      signingPrivateSeed: Data(repeating: 90, count: 32),
      agreementPrivateKey: Data(repeating: 100, count: 32), createdAt: "2026-01-01T00:00:00Z",
      keyEpoch: 3)
    try AtlasKeychainDeviceIdentityStore(client: client).createPrimaryIdentity(
      recipient.secretBundle().canonicalData())
    let bindings = AtlasKeychainRuntimeBindingStore(client: client)
    for _ in 0..<2 {
      let owner = try bindings.installEnrollment(
        directory: root, packet: v["packet"] as! [String: Any],
        pins: v["pins"] as! [String: Any],
        trustedSigner: Data(base64Encoded: v["trusted_signer_b64"] as! String)!)
      XCTAssertEqual(try owner.runtimeState().profileSnippets.count, 2)
      XCTAssertEqual(try owner.runtimeState().tombstones.count, 1)
      XCTAssertNotNil(try bindings.load(for: "vault-c26"))
    }
    client.cleanup()
    XCTAssertNil(try bindings.load(for: "vault-c26"))
  }
  func testCeremonyAcknowledgementAndSubstitutions() throws {
    let v = try fixture()
    let packet = v["packet"] as! [String: Any]
    let recipient = try AtlasVaultDeviceIdentity(
      signingPrivateSeed: Data(repeating: 90, count: 32),
      agreementPrivateKey: Data(repeating: 100, count: 32), createdAt: "2026-01-01T00:00:00Z",
      keyEpoch: 3)
    let digest = String(repeating: "a1", count: 32)
    let ack = try AtlasVaultEnrollmentDelivery.acknowledge(
      packet, deliveryHash: digest, recipient: recipient)
    // Existing identity contract permits randomized CryptoKit signatures, not divergent signed bytes.
    XCTAssertTrue(
      AtlasVaultEpochRotation.digest(
        try AtlasVaultEpochRotation.canonical(ack.filter { $0.key != "signature_b64" })) == v[
          "acknowledgement_unsigned_sha256"] as? String)
    let vector = v["acknowledgement"] as! [String: Any]
    XCTAssertTrue(
      AtlasVaultEpochRotation.digest(try AtlasVaultEpochRotation.canonical(vector)) == v[
        "acknowledgement_sha256"] as? String)
    try AtlasVaultEnrollmentDelivery.verifyAcknowledgement(
      packet, deliveryHash: digest, receipt: vector, recipient: recipient.descriptor)
    try AtlasVaultEnrollmentDelivery.verifyAcknowledgement(
      packet, deliveryHash: digest,
      receipt: ack, recipient: recipient.descriptor)
    for field in [
      "format", "version", "delivery_sha256", "anchor_root", "transcript_sha256",
      "recipient_device_id", "signature_b64",
    ] {
      var altered = ack
      altered[field] = field == "version" ? (2 as Any) : ("invalid" as Any)
      XCTAssertThrowsError(
        try AtlasVaultEnrollmentDelivery.verifyAcknowledgement(
          packet, deliveryHash: digest,
          receipt: altered, recipient: recipient.descriptor))
    }
  }
  func fixture() throws -> [String: Any] {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    return
      (try JSONSerialization.jsonObject(
        with: Data(
          contentsOf: root.appendingPathComponent(
            "contracts/sync/test_vectors/atlasvault_production_enrollment_v1.json")))
      as! [String: Any])["delivery_case"] as! [String: Any]
  }
  func testNonemptyRetainedAuthorityRuntimeAndRetry() throws {
    let v = try fixture()
    print("C30_SWIFT_INSTALL_PHASE=fixture_verified")
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let recipient = try AtlasVaultDeviceIdentity(
      signingPrivateSeed: Data(repeating: 90, count: 32),
      agreementPrivateKey: Data(repeating: 100, count: 32), createdAt: "2026-01-01T00:00:00Z",
      keyEpoch: 3)
    for _ in 0..<2 {
      print("C30_SWIFT_INSTALL_PHASE=install_started")
      let owner = try AtlasVaultEnrollmentDelivery.installRuntime(
        directory: root,
        packet: v["packet"] as! [String: Any], pins: v["pins"] as! [String: Any],
        trustedSigner: Data(base64Encoded: v["trusted_signer_b64"] as! String)!,
        recipient: recipient,
        agreementPrivateKey: Data(repeating: 100, count: 32),
        storageKey: Data(repeating: 111, count: 32))
      print("C30_SWIFT_INSTALL_PHASE=installed")
      let state = try owner.runtimeState()
      print("C30_SWIFT_INSTALL_PHASE=restored")
      XCTAssertEqual(state.profileSnippets.count, 2)
      XCTAssertEqual(state.tombstones.count, 1)
      XCTAssertTrue(state.tombstones[0].metadata.id == "terminal-delete")
      XCTAssertEqual(try owner.pendingOperations().count, 0)
    }
  }
}

private final class C30NamespacedKeychain: AtlasKeychainClient, @unchecked Sendable {
  private let prefix = "com.atlasvault.c30.synthetic." + UUID().uuidString + "."
  private let native = SecItemAtlasKeychainClient()
  private var created: [AtlasKeychainQuery] = []
  func add(_ item: AtlasKeychainItem) -> OSStatus {
    let query = AtlasKeychainQuery(service: prefix + item.service, account: item.account)
    let result = native.add(
      .init(
        service: query.service, account: query.account, valueData: item.valueData,
        accessibility: item.accessibility))
    if result == errSecSuccess { created.append(query) }
    return result
  }
  func copyMatching(_ q: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
    native.copyMatching(.init(service: prefix + q.service, account: q.account))
  }
  func update(_ q: AtlasKeychainQuery, with a: AtlasKeychainUpdate) -> OSStatus {
    native.update(.init(service: prefix + q.service, account: q.account), with: a)
  }
  func delete(_ q: AtlasKeychainQuery) -> OSStatus {
    native.delete(.init(service: prefix + q.service, account: q.account))
  }
  func cleanup() {
    for q in created { _ = native.delete(q) }
    created.removeAll()
  }
}
