import CryptoKit
import Foundation

private func load(_ path: String) throws -> [String: Any] {
  try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path)))
    as! [String: Any]
}
private func canonical(_ value: Any) throws -> Data {
  try JSONSerialization.data(
    withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes])
}
private func digest(_ value: Any) throws -> String {
  SHA256.hash(data: try canonical(value)).map { String(format: "%02x", $0) }.joined()
}

@main
private struct AtlasVaultMaliciousServerClient {
  static func main() throws {
    let args = Array(CommandLine.arguments.dropFirst())
    let mode = args[0]
    let plan = try load(args[2])
    let context = plan["context"] as! [String: Any]
    var results = [String: Any]()
    for (name, scenario) in plan["scenarios"] as! [String: [String: Any]] {
      let root = URL(fileURLWithPath: args[1]).appendingPathComponent(name)
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
      let key = Data(SHA256.hash(data: Data("c24-synthetic-client-storage".utf8)))
      let c = try AtlasVaultGuardedSyncState(
        fileURL: root.appendingPathComponent("accepted-recovery.state"), encryptionKey: key,
        accountID: context["account_id"] as! String, vaultID: context["vault_id"] as! String,
        collectionID: context["collection_id"] as! String,
        keyEpoch: (context["key_epoch"] as! NSNumber).int64Value,
        trustedSigner: Data(base64Encoded: plan["public_b64"] as! String)!)
      let inbox = try AtlasVaultDurableEncryptedInbox(
        fileURL: root.appendingPathComponent("inbox.state"), encryptionKey: key)
      let outbox = try AtlasVaultDurableEncryptedOutbox(
        fileURL: root.appendingPathComponent("outbox.state"), encryptionKey: key)
      if mode == "prepare" { try c.initialize() }
      var categories = [String]()
      let before = try c.checkpoint()
      let actions =
        mode == "prepare"
        ? scenario["baseline"] as! [[String: Any]]
        : mode == "attack" ? scenario["attack"] as! [[String: Any]] : []
      for action in actions {
        func interrupt(_ point: String) throws {
          if action["stop_after"] as? String == point {
            try canonical(["interrupted_after": point]).write(
              to: URL(fileURLWithPath: args[3]), options: .atomic)
            while true { Thread.sleep(forTimeInterval: 60) }
          }
        }
        do {
          if let path = action["peer"] as? String {
            let peer = try load(path)[name] as! [String: Any]
            _ = try c.compareEvidence(peer["history"] as! [[String: Any]])
            categories.append("COMPATIBLE_PREFIX_NOT_FRESHNESS")
          } else {
            let p = (plan["packets"] as! [String: [String: Any]])[action["packet"] as! String]!
            let view = p["view"] as! [String: Any]
            let accepted = try c.ingest(
              view: view, registry: p["registry"] as! [[String: Any]],
              collection: p["collection"] as! [String: Any],
              opaqueState: Data(base64Encoded: p["opaque_b64"] as! String)!)
            try interrupt("admission")
            let op = try AtlasVaultEncryptedPatchOperation(
              jsonObject: p["operation"] as! [String: Any])
            try outbox.enqueue(op)
            try interrupt("outbox")
            let pending = try inbox.pendingOperations()
            if !pending.isEmpty
              && (pending.count != 1
                || NSDictionary(dictionary: pending[0].jsonObject)
                  != NSDictionary(dictionary: op.jsonObject))
            {
              throw NSError(domain: "C24UnexpectedReceipt", code: 1)
            }
            if try inbox.cursor() != view["root"] as? String && pending.isEmpty {
              try inbox.stagePage(
                expectedCursor: inbox.cursor(), nextCursor: view["root"] as? String,
                operations: [op])
            }
            try interrupt("inbox")
            while try inbox.applyNext({ _ in }) != nil {}
            try interrupt("receipt")
            try outbox.confirmRemoteAcceptance(op.operationID)
            categories.append(accepted ? "ACCEPTED" : "IDEMPOTENT")
          }
        } catch let error as AtlasVaultSyncRecoveryError {
          categories.append(error.rawValue)
        }
      }
      var called = false
      do { try c.automaticSync { called = true } } catch let error as AtlasVaultSyncRecoveryError {
        guard error.rawValue == "ATLAS_RECOVERY_PENDING" else { throw error }
      }
      let checkpoint = try c.checkpoint()
      let recovery = try c.recovery()
      results[name] = [
        "before": before, "checkpoint": checkpoint, "recovery": recovery,
        "history": try c.exportEvidence(), "evidence": try c.evidence(),
        "categories": categories, "automatic_sync_fenced": !called,
        "cursor": try inbox.cursor() ?? NSNull(),
        "pending_outbox": try outbox.pendingOperations().count,
        "state_sha256": try digest(checkpoint), "recovery_sha256": try digest(recovery),
      ]
    }
    try canonical(results).write(to: URL(fileURLWithPath: args[3]), options: .atomic)
    if mode != "inspect" { while true { Thread.sleep(forTimeInterval: 60) } }
  }
}
