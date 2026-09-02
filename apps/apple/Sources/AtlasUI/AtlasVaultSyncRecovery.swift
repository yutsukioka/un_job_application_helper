import Foundation

public enum AtlasVaultSyncRecoveryError: String, Error {
  case rejected = "ATLAS_STATE_VIEW_REJECTED"
  case registry = "ATLAS_REGISTRY_SUBSTITUTION"
  case equivocation = "ATLAS_STATE_EQUIVOCATION"
  case rollback = "ATLAS_ROLLBACK_REJECTED"
  case resurrection = "ATLAS_TOMBSTONE_RESURRECTION"
  case stale = "ATLAS_STALE_STATE"
  case pending = "ATLAS_RECOVERY_PENDING"
  case limit = "ATLAS_HISTORY_LIMIT"
  case checkpoint = "ATLAS_CHECKPOINT_REQUIRED"
}

private let recoveryZero = String(repeating: "0", count: 64)
private let recoveryRegistry = viewDigest(Data("atlasvault-registry-root-v1\n".utf8))
private func recoveryChecked<T>(_ operation: () throws -> T) throws -> T {
  do { return try operation() } catch let e as AtlasVaultSyncRecoveryError { throw e } catch let e
    as AtlasVaultStateViewError
  { throw AtlasVaultSyncRecoveryError(rawValue: e.rawValue) ?? .rejected } catch {
    throw AtlasVaultSyncRecoveryError.rejected
  }
}

/// Single-owner atomic admission state. Manual selection never rewrites accepted roots.
public final class AtlasVaultGuardedSyncState {
  private let store: EncryptedQueueFile
  private let publicKey: Data
  private let context: [String: Any]
  private let lock = NSRecursiveLock()

  public init(
    fileURL: URL, encryptionKey: Data, accountID: String, vaultID: String,
    collectionID: String, keyEpoch: Int64, trustedSigner: Data
  ) throws {
    let config = try recoveryChecked { () -> ([String: Any], EncryptedQueueFile) in
      guard trustedSigner.count == 32 else { throw AtlasVaultSyncRecoveryError.rejected }
      return (
        [
          "account_id": try viewIdentifier(accountID), "vault_id": try viewIdentifier(vaultID),
          "collection_id": try viewIdentifier(collectionID), "key_epoch": try viewInteger(keyEpoch),
          "signing_public_b64": trustedSigner.base64EncodedString(),
        ],
        try EncryptedQueueFile(
          fileURL: fileURL, encryptionKey: encryptionKey, kind: "guarded-sync-state-v1")
      )
    }
    context = config.0
    store = config.1
    publicKey = trustedSigner
  }

  private func run<T>(_ operation: () throws -> T) throws -> T {
    lock.lock()
    defer { lock.unlock() }
    return try recoveryChecked(operation)
  }

  private func checkContext(_ view: [String: Any]) throws {
    guard view["account_id"] as? String == context["account_id"] as? String,
      view["vault_id"] as? String == context["vault_id"] as? String,
      try viewInteger(view["key_epoch"]) == viewInteger(context["key_epoch"])
    else { throw AtlasVaultSyncRecoveryError.rejected }
  }

  private func chain(_ raw: [[String: Any]]) throws -> [[String: Any]] {
    guard raw.count <= 256 else { throw AtlasVaultSyncRecoveryError.limit }
    var views = [[String: Any]]()
    var previous = recoveryZero
    var registry = recoveryRegistry
    for (i, item) in raw.enumerated() {
      let v = try verifiedView(item, publicKey: publicKey)
      try checkContext(v)
      guard try viewInteger(v["sequence"]) == Int64(i + 1),
        v["previous_root"] as? String == previous,
        v["previous_registry_root"] as? String == registry
      else { throw AtlasVaultSyncRecoveryError.rejected }
      views.append(v)
      previous = v["root"] as! String
      registry = v["registry_root"] as! String
    }
    return views
  }

  private func load() throws -> [String: Any] {
    var s = try store.read(default: [:])
    guard Set(s.keys) == ["context", "views", "records", "cases", "status"],
      let stored = s["context"] as? [String: Any],
      NSDictionary(dictionary: stored).isEqual(to: context),
      let views = s["views"] as? [[String: Any]], let records = s["records"] as? [String: Any],
      records.count <= 256,
      let cases = s["cases"] as? [[String: Any]], cases.count <= 8,
      ["ACTIVE", "MANUAL_REQUIRED", "RECOVERY_PENDING"].contains(s["status"] as? String ?? "")
    else { throw AtlasVaultSyncRecoveryError.rejected }
    s["views"] = try chain(views)
    return s
  }

  public func initialize() throws {
    try run {
      guard !FileManager.default.fileExists(atPath: store.fileURL.path) else {
        throw AtlasVaultSyncRecoveryError.rejected
      }
      try store.write([
        "context": context, "views": [], "records": [String: Any](), "cases": [],
        "status": "ACTIVE",
      ])
    }
  }

  private func active(_ state: [String: Any]) throws {
    guard state["status"] as? String == "ACTIVE" else { throw AtlasVaultSyncRecoveryError.pending }
    if (state["cases"] as! [[String: Any]]).count == 8 {
      var s = state
      s["status"] = "RECOVERY_PENDING"
      try store.write(s)
      throw AtlasVaultSyncRecoveryError.pending
    }
  }

  public func automaticSync<T>(_ operation: () throws -> T) throws -> T {
    try run {
      try active(load())
      return try operation()
    }
  }

  public func checkpoint() throws -> [String: Any] {
    try run {
      let s = try load()
      let views = s["views"] as! [[String: Any]]
      let records = s["records"] as! [String: Any]
      return [
        "sequence": views.count, "cursor": views.last?["root"] as? String ?? recoveryZero,
        "records": records.keys.sorted().map { records[$0]! },
      ]
    }
  }

  public func exportEvidence() throws -> [[String: Any]] {
    try run { try load()["views"] as! [[String: Any]] }
  }

  private func alarm(
    _ state: [String: Any], _ reason: AtlasVaultSyncRecoveryError, _ peer: [[String: Any]],
    _ registry: String? = nil
  ) throws -> Never {
    var s = state
    var cases = s["cases"] as! [[String: Any]]
    cases.append([
      "reason": reason.rawValue, "local": s["views"]!, "peer": peer,
      "presented_registry_root": registry as Any? ?? NSNull(), "disposition": NSNull(),
      "rejected_branch": NSNull(),
    ])
    s["cases"] = cases
    s["status"] = "MANUAL_REQUIRED"
    try store.write(s)
    throw reason
  }

  public func evidence() throws -> [String: Any] {
    try run {
      let s = try load()
      let c = (s["cases"] as! [[String: Any]]).last
      return ["local": c?["local"] ?? s["views"]!, "peer": c?["peer"] ?? []]
    }
  }

  public func recovery() throws -> [String: Any] {
    try run {
      let s = try load()
      let c = (s["cases"] as! [[String: Any]]).last
      func metadata(_ views: [[String: Any]]) -> [[String: Any]] {
        views.map { view in
          Dictionary(
            uniqueKeysWithValues: ["sequence", "root", "registry_root", "key_epoch"].map {
              ($0, view[$0]!)
            })
        }
      }
      return [
        "status": s["status"]!, "reason": c?["reason"] ?? NSNull(),
        "local": metadata(c?["local"] as? [[String: Any]] ?? s["views"] as! [[String: Any]]),
        "peer": metadata(c?["peer"] as? [[String: Any]] ?? []),
        "disposition": c?["disposition"] ?? NSNull(),
        "rejected_branch": c?["rejected_branch"] ?? NSNull(),
        "presented_registry_root": c?["presented_registry_root"] ?? NSNull(),
      ]
    }
  }

  public func resolve(_ disposition: String, localRoot: String, peerRoot: String) throws -> String {
    try run {
      var s = try load()
      guard s["status"] as? String == "MANUAL_REQUIRED",
        ["retain_accepted", "select_peer", "keep_blocked"].contains(disposition)
      else { throw AtlasVaultSyncRecoveryError.rejected }
      var cases = s["cases"] as! [[String: Any]]
      var c = cases.last!
      let local = c["local"] as! [[String: Any]]
      let peer = c["peer"] as! [[String: Any]]
      guard localRoot == (local.last?["root"] as? String ?? recoveryZero),
        peerRoot == (peer.last?["root"] as? String ?? recoveryZero)
      else { throw AtlasVaultSyncRecoveryError.rejected }
      let safe =
        try c["reason"] as? String == "ATLAS_ROLLBACK_REJECTED" && !peer.isEmpty
        && peer.allSatisfy {
          let n = try viewInteger($0["sequence"])
          return n <= local.count && $0["root"] as? String == local[Int(n) - 1]["root"] as? String
        }
      c["disposition"] = disposition
      c["rejected_branch"] =
        disposition == "retain_accepted"
        ? "peer" as Any : (disposition == "select_peer" ? "local" as Any : NSNull())
      cases[cases.count - 1] = c
      s["cases"] = cases
      s["status"] = disposition == "retain_accepted" && safe ? "ACTIVE" : "RECOVERY_PENDING"
      try store.write(s)
      return s["status"] as! String
    }
  }

  public func compareEvidence(_ peer: [[String: Any]]) throws -> Int {
    try run {
      let s = try load()
      try active(s)
      var signed = [[String: Any]]()
      do {
        return try recoveryChecked {
          guard peer.count <= 256 else { throw AtlasVaultSyncRecoveryError.limit }
          for v in peer { signed.append(try verifiedView(v, publicKey: publicKey)) }
          let checked = try chain(signed)
          let local = s["views"] as! [[String: Any]]
          guard !checked.isEmpty, !local.isEmpty else {
            throw AtlasVaultSyncRecoveryError.checkpoint
          }
          for (a, b) in zip(local, checked) where a["root"] as? String != b["root"] as? String {
            throw AtlasVaultSyncRecoveryError.equivocation
          }
          return min(local.count, checked.count)
        }
      } catch let e as AtlasVaultSyncRecoveryError { try alarm(s, e, signed) }
    }
  }

  public func ingest(
    view raw: [String: Any], registry: [[String: Any]], collection rawCollection: [String: Any],
    opaqueState: Data
  ) throws -> Bool {
    try run {
      var s = try load()
      try active(s)
      var peer = [[String: Any]]()
      var registryDigest: String?
      do {
        let duplicate = try recoveryChecked { () -> Bool in
          let v = try verifiedView(raw, publicKey: publicKey)
          peer = [v]
          try checkContext(v)
          registryDigest = try AtlasVaultAuthenticatedStateView.registryRoot(registry)
          guard registryDigest == v["registry_root"] as? String else {
            throw AtlasVaultSyncRecoveryError.registry
          }
          guard opaqueState.count <= 1024 * 1024 else { throw AtlasVaultSyncRecoveryError.limit }
          let c = try AtlasVaultSignedStateCommitment(jsonObject: rawCollection)
          let n = try viewInteger(v["sequence"])
          guard opaqueState.count >= 16, c.collectionID == context["collection_id"] as? String,
            c.sequence == n,
            c.root == v["collection_root"] as? String, c.stateSHA256 == viewDigest(opaqueState),
            try c.verify(publicKey: publicKey)
          else { throw AtlasVaultSyncRecoveryError.rejected }
          let views = s["views"] as! [[String: Any]]
          if n <= views.count, v["root"] as? String != views[Int(n) - 1]["root"] as? String {
            throw AtlasVaultSyncRecoveryError.equivocation
          }
          guard n >= views.count else { throw AtlasVaultSyncRecoveryError.rollback }
          if n == views.count { return true }
          guard n <= 256 else { throw AtlasVaultSyncRecoveryError.limit }
          let previous = views.last
          guard n == views.count + 1,
            v["previous_root"] as? String == (previous?["root"] as? String ?? recoveryZero),
            v["previous_registry_root"] as? String
              == (previous?["registry_root"] as? String ?? recoveryRegistry),
            c.previousRoot == (previous?["collection_root"] as? String ?? recoveryZero)
          else { throw AtlasVaultSyncRecoveryError.rejected }
          guard let body = try JSONSerialization.jsonObject(with: opaqueState) as? [String: Any],
            Set(body.keys) == ["format", "version", "route", "records"],
            body["format"] as? String == "atlasvault-guarded-collection",
            try viewInteger(body["version"]) == 1,
            ["patch", "snapshot", "compaction"].contains(body["route"] as? String ?? ""),
            let rawRecords = body["records"] as? [[String: Any]]
          else { throw AtlasVaultSyncRecoveryError.rejected }
          guard rawRecords.count <= 256 else { throw AtlasVaultSyncRecoveryError.limit }
          var records = [String: Any]()
          for raw in rawRecords {
            let r = try AtlasVaultOpaqueCiphertextEnvelope(jsonObject: raw)
            guard r.version == 1, records[r.objectID] == nil,
              r.keyEpoch == (try viewInteger(v["key_epoch"]))
            else {
              throw AtlasVaultSyncRecoveryError.rejected
            }
            let fingerprint = viewDigest(
              try JSONSerialization.data(
                withJSONObject: r.jsonObject, options: [.sortedKeys, .withoutEscapingSlashes]))
            records[r.objectID] = [
              "object_id": r.objectID, "revision": r.revision, "content_sha256": r.contentSHA256,
              "envelope_sha256": fingerprint, "tombstone": r.tombstone,
            ]
          }
          for (id, value) in s["records"] as! [String: Any] {
            let old = value as! [String: Any]
            if old["tombstone"] as? Bool == true,
              !NSDictionary(dictionary: old).isEqual(to: records[id] as? [String: Any] ?? [:])
            {
              throw AtlasVaultSyncRecoveryError.resurrection
            }
            guard records[id] != nil else { throw AtlasVaultSyncRecoveryError.stale }
          }
          s["records"] = records
          s["views"] = views + [v]
          return false
        }
        if duplicate { return false }
      } catch let e as AtlasVaultSyncRecoveryError { try alarm(s, e, peer, registryDigest) }
      try store.write(s)
      return true
    }
  }
}
