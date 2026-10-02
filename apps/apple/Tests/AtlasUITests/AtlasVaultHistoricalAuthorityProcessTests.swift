import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultHistoricalAuthorityProcessTests: XCTestCase {
  private func fixture(_ root: URL) throws -> (
    AtlasVaultAnchoredSyncState, [String: Any], [String: Any]
  ) {
    let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    func vector(_ name: String) throws -> [String: Any] {
      let f =
        try JSONSerialization.jsonObject(
          with: Data(
            contentsOf: repo.appendingPathComponent("contracts/sync/test_vectors/\(name).json")))
        as! [String: Any]
      return (f["cases"] as! [[String: Any]])[1]
    }
    let v = try vector("atlasvault_bootstrap_authority_diagnostic")
    let p = v["checkpoint"] as! [String: Any]
    var trust = p.filter {
      ["account_id", "vault_id", "collection_id", "key_epoch"].contains($0.key)
    }
    for k in [
      "trusted_signer_b64", "registry", "recipient_device_id", "confirmed_transcript",
      "current_context",
    ] { trust[k] = v[k] }
    trust["anchor_root"] = p["root"]
    let h = try AtlasVaultAnchoredSyncState(
      fileURL: root.appendingPathComponent("history"),
      encryptionKey: Data(repeating: 96, count: 32), trust: trust)
    return (h, v, try vector("atlasvault_historical_authority_v1")["proof"] as! [String: Any])
  }
  private func bootstrap(_ h: AtlasVaultAnchoredSyncState, _ v: [String: Any]) throws -> Bool {
    try h.bootstrap(
      v.filter {
        [
          "checkpoint", "enrollment", "registry", "current_context", "recipient_device_id",
          "confirmed_transcript", "view", "collection", "opaque_b64",
        ].contains($0.key)
      })
  }
  func testAuthorityCrashChild() throws {
    guard let path = ProcessInfo.processInfo.environment["ATLAS_C30_AUTHORITY_CHILD"],
      let point = ProcessInfo.processInfo.environment["ATLAS_C30_AUTHORITY_POINT"]
    else { return }
    let root = URL(fileURLWithPath: path)
    let (h, v, p) = try fixture(root)
    func barrier() throws {
      try Data("ready".utf8).write(to: root.appendingPathComponent("ready"))
      while true { Thread.sleep(forTimeInterval: 60) }
    }
    _ = try h.installHistoricalAuthorityForTesting(
      p, collection: v["collection"] as! [String: Any],
      opaqueState: Data(base64Encoded: v["opaque_b64"] as! String)!,
      beforeReplace: { if point == "before" { try barrier() } })
    try barrier()
  }
  func testRealAuthorityKillAndRestart() throws {
    for point in ["before", "after"] {
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      let (h, v, proof) = try fixture(root)
      XCTAssertTrue(try bootstrap(h, v))
      let before = try h.exportEvidence()
      let process = Process()
      process.executableURL = URL(fileURLWithPath: "/usr/bin/xcrun")
      process.arguments = [
        "xctest", "-XCTest",
        "AtlasUITests.AtlasVaultHistoricalAuthorityProcessTests/testAuthorityCrashChild",
        Bundle(for: Self.self).bundleURL.path,
      ]
      process.environment = ProcessInfo.processInfo.environment.merging([
        "ATLAS_C30_AUTHORITY_CHILD": root.path, "ATLAS_C30_AUTHORITY_POINT": point,
      ]) { _, new in new }
      process.standardOutput = FileHandle.nullDevice
      process.standardError = FileHandle.nullDevice
      try process.run()
      defer {
        if process.isRunning {
          Darwin.kill(process.processIdentifier, SIGKILL)
          process.waitUntilExit()
        }
        try? FileManager.default.removeItem(at: root)
      }
      let ready = root.appendingPathComponent("ready")
      let deadline = Date().addingTimeInterval(30)
      while !FileManager.default.fileExists(atPath: ready.path) && Date() < deadline
        && process.isRunning
      { Thread.sleep(forTimeInterval: 0.05) }
      XCTAssertTrue(FileManager.default.fileExists(atPath: ready.path), "authority barrier absent")
      guard process.isRunning else {
        XCTFail("authority worker exited before barrier")
        return
      }
      XCTAssertEqual(Darwin.kill(process.processIdentifier, SIGKILL), 0)
      process.waitUntilExit()
      let (reopened, _, _) = try fixture(root)
      XCTAssertTrue(NSArray(array: try reopened.exportEvidence()).isEqual(to: before))
      let anchor = try reopened.publicationOrigin()["anchor"] as! [String: Any]
      XCTAssertEqual(anchor["historical_authority"] != nil, point == "after")
      XCTAssertFalse(try bootstrap(reopened, v))
      XCTAssertEqual(
        try reopened.installHistoricalAuthority(
          proof, collection: v["collection"] as! [String: Any],
          opaqueState: Data(base64Encoded: v["opaque_b64"] as! String)!), point == "before")
    }
  }
}
