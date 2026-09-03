import CryptoKit
import Foundation

enum EpochCatchUp {
  typealias R = AtlasVaultEpochRotation
  typealias D = AtlasVaultDeviceDelivery
  static func records(_ state: [String: Any]) throws -> [[String: Any]] {
    if state["epoch_bridge"] != nil && state["epoch_bridges"] != nil {
      throw AtlasVaultRotationError.rejected
    }
    let result =
      try state["epoch_bridges"] != nil
      ? D.rows(state["epoch_bridges"])
      : state["epoch_bridge"] == nil ? [] : [D.map(state["epoch_bridge"])]
    guard result.count <= 32 else { throw AtlasVaultRotationError.rejected }
    return result
  }
  static func verify(_ records: [[String: Any]], registry: [[String: Any]], context: [String: Any])
    throws -> [[String: Any]]
  {
    var registry = registry
    var epoch = try R.integer(context["key_epoch"])
    var result = [[String: Any]]()
    for raw in records {
      let selective = raw["wrapper"] != nil
      let p = try raw["wrapper"] != nil ? D.map(raw["proof"]) : raw
      let plan = try D.map(p["plan"])
      let verified =
        try selective
        ? D.verify(
          raw, registry: registry, accountID: D.text(context["account_id"]),
          vaultID: D.text(context["vault_id"]),
          previousEpoch: epoch, stateRoot: D.text(plan["state_root"]),
          activationID: D.text(p["activation_id"]),
          recipientDeviceID: D.text(p["recipient_device_id"]))
        : R.verify(
          raw, registry: registry, accountID: D.text(context["account_id"]),
          vaultID: D.text(context["vault_id"]), previousEpoch: epoch,
          stateRoot: D.text(plan["state_root"]))
      result.append([
        "plan": plan, "registry": p["registry"]!,
        "rotation_signer_device_id": p["rotation_signer_device_id"]!,
      ])
      registry = try D.rows(verified["registry"])
      epoch = try R.integer(verified["new_epoch"])
    }
    return result
  }
}

final class EpochPublication {
  let base: EncryptedQueueFile
  let anchor: EncryptedQueueFile
  var fileURL: URL { base.fileURL }
  init(fileURL: URL, key: Data) throws {
    base = try EncryptedQueueFile(fileURL: fileURL, encryptionKey: key, kind: "epoch-activation-v1")
    anchor = try EncryptedQueueFile(
      fileURL: fileURL.deletingLastPathComponent().appendingPathComponent("activation-recovery"),
      encryptionKey: key, kind: "epoch-recovery-v1")
  }
  func digest(_ s: [String: Any]) throws -> String {
    AtlasVaultEpochRotation.digest(try AtlasVaultEpochRotation.canonical(s))
  }
  func record() throws -> [String: Any] {
    let r = try anchor.read(default: [:])
    try AtlasVaultEpochRotation.exact(r, ["state", "sha256"])
    let s = try AtlasVaultDeviceDelivery.map(r["state"])
    guard try digest(s) == r["sha256"] as? String else {
      throw AtlasVaultRotationError.publicationRecovery
    }
    return s
  }
  func enable() throws {
    if !FileManager.default.fileExists(atPath: anchor.fileURL.path) {
      let s = try base.read(default: [:])
      try anchor.write(["state": s, "sha256": digest(s)])
    }
  }
  func read(default fallback: [String: Any]) throws -> [String: Any] {
    let s = try base.read(default: fallback)
    if FileManager.default.fileExists(atPath: anchor.fileURL.path),
      try digest(s) != digest(record())
    {
      throw AtlasVaultRotationError.publicationRecovery
    }
    return s
  }
  func write(_ s: [String: Any], beforeReplace: (() throws -> Void)? = nil) throws {
    if FileManager.default.fileExists(atPath: anchor.fileURL.path) {
      try anchor.write(["state": s, "sha256": digest(s)], beforeReplace: beforeReplace)
      try base.write(s)
    } else {
      try base.write(s, beforeReplace: beforeReplace)
    }
  }
  func recover() throws {
    var s=try record()
    if s["status"] as? String == "ACTIVE" {s["status"]="CATCH_UP_PENDING"}
    try write(s)
  }
}

extension AtlasVaultEpochVault {
  public func catchUp(
    _ packets: [[String: Any]], currentActivationID: String, agreementPrivateKey: Data,
    historyUpdates: [[String:Any]] = []
  ) throws -> Bool {
    try catchUpForTesting(
      packets, currentActivationID: currentActivationID, agreementPrivateKey: agreementPrivateKey,historyUpdates:historyUpdates)
  }
  func catchUpForTesting(
    _ packets: [[String: Any]], currentActivationID: String, agreementPrivateKey: Data,
    historyUpdates: [[String:Any]] = [],
    checkpoint: ((String) throws -> Void)? = nil
  ) throws -> Bool {
    try run {
      var s = try load()
      guard
        !["REVOKED", "RECOVERY_PENDING", "CLEANUP_PENDING"].contains(s["status"] as? String ?? ""),
        try history(s).recovery()["status"] as? String == "ACTIVE"
      else { throw AtlasVaultRotationError.recovery }
      let j = s["journal"] as? [String: Any] ?? [:]
      if j["kind"] as? String == "CATCH_UP", j["phase"] as? String == "ACTIVE",
        j["target_id"] as? String == currentActivationID
      {
        guard try R.canonical(["packets": j["packets"]!]) == R.canonical(["packets": packets]),
        try R.canonical(["updates":j["history_updates"] ?? []]) == R.canonical(["updates":historyUpdates])
        else { throw AtlasVaultRotationError.rejected }
        if s["status"] as? String == "CATCH_UP_PENDING" {
          s["status"]="ACTIVE";try file.write(s);return true
        }
        return false
      }
      try file.enable()
      let prior =
        j["kind"] as? String == "CATCH_UP" && j["phase"] as? String != "ACTIVE"
        ? j["prior_journal"] : s["journal"]
      s["status"] = "CATCH_UP_PENDING"
      s["journal"] = [
        "kind": "CATCH_UP", "phase": "CATCH_UP_PENDING", "target_id": currentActivationID,
        "packets": [], "prior_journal": prior ?? NSNull(),
      ]
      try file.write(s)
      try checkpoint?("catch_up_pending")
      guard !packets.isEmpty, packets.count <= 32 else { throw AtlasVaultRotationError.rejected }
      guard historyUpdates.count<=256,try R.canonical(["updates":historyUpdates]).count<=4*1024*1024 else {throw AtlasVaultRotationError.rejected}
      var staged = s
      var components = try map(s["components"])
      var h = try map(components["history"])
      var keys = try map(s["keys"])
      guard h["status"] as? String == "ACTIVE",try !rows(h["views"]).isEmpty
      else { throw AtlasVaultRotationError.recovery }
      var currentRegistry = try rows(s["registry"])
      var epoch = try R.integer(s["epoch"])
      var bridges = try EpochCatchUp.records(h)
      let validator=try history(staged)
      validator.store=try EncryptedQueueFile(fileURL:file.fileURL,key:key,read:{_ in h},write:{value,_ in h=value})
      var updateIndex=0
      var verified = [String: Any]()
      for packet in packets {
        if packet["format"] as? String == "atlasvault-activation-record" {
          throw AtlasVaultRotationError.perDeviceProofRequired
        }
        let p = try map(packet["proof"])
        let w = try map(packet["wrapper"])
        let plan = try map(p["plan"])
        while try rows(h["views"]).last?["root"] as? String != plan["state_root"] as? String {
          guard updateIndex<historyUpdates.count else {throw AtlasVaultRotationError.rejected}
          let u=historyUpdates[updateIndex]
          try R.exact(u,["view","registry","collection","opaque_state_b64"])
          guard let body=Data(base64Encoded:try AtlasVaultDeviceDelivery.text(u["opaque_state_b64"])) else {throw AtlasVaultRotationError.rejected}
          do {
            _ = try validator.ingest(view:map(u["view"]),registry:rows(u["registry"]),collection:map(u["collection"]),opaqueState:body)
          } catch {
            if h["status"] as? String != "ACTIVE" {
              var original=try map(map(s["components"])["history"])
              original["cases"]=h["cases"];original["status"]="RECOVERY_PENDING"
              var oldComponents=try map(s["components"]);oldComponents["history"]=original
              s["components"]=oldComponents;s["status"]="RECOVERY_PENDING";try file.write(s)
            }
            throw error
          }
          updateIndex+=1
        }
        verified = try AtlasVaultDeviceDelivery.verify(
          packet, registry: currentRegistry, accountID: context["account_id"] as! String,
          vaultID: context["vault_id"] as! String,
          previousEpoch: epoch, stateRoot: AtlasVaultDeviceDelivery.text(rows(h["views"]).last?["root"]),
          activationID: AtlasVaultDeviceDelivery.text(p["activation_id"]),
          recipientDeviceID: context["device_id"] as! String)
        let opened = try AtlasVaultKeyEpochHPKE.open(
          recipientPrivateKey: agreementPrivateKey,
          sealed: AtlasVaultKeyEpochHPKESealedVaultKeyV2(
            keyEpoch: Int64(R.integer(w["key_epoch"])),
            encapsulatedKey: R.bytes(w["encapsulated_key_b64"], 32),
            ciphertext: R.bytes(w["ciphertext_b64"], 48)),
          context: Data(
            "atlasvault-rotation-delivery-v1:\(try R.binding(plan)):\(context["device_id"] as! String)"
              .utf8), minimumKeyEpoch: Int64(R.integer(verified["new_epoch"])))
        keys[String(opened.keyEpoch)] = opened.vaultKey.base64EncodedString()
        bridges.append(packet)
        h.removeValue(forKey:"epoch_bridge");h["epoch_bridges"]=bridges
        currentRegistry = try rows(verified["registry"])
        epoch = try R.integer(verified["new_epoch"])
        try checkpoint?("verified_epoch")
      }
      guard try map(packets.last?["proof"])["activation_id"] as? String == currentActivationID
      else { throw AtlasVaultRotationError.rejected }
      guard updateIndex==historyUpdates.count else {throw AtlasVaultRotationError.rejected}
      h.removeValue(forKey: "epoch_bridge")
      h["epoch_bridges"] = bridges
      components["history"] = h
      _ = try EpochCatchUp.verify(bridges, registry: registry, context: context)
      staged.merge([
        "epoch": epoch, "registry": currentRegistry, "keys": keys,
        "recipients": verified["recipients"]!, "generation": try R.integer(s["generation"]) + 1,
        "status": "ACTIVE", "components": components,
        "journal": [
          "kind": "CATCH_UP", "phase": "ACTIVE", "target_id": currentActivationID,
          "packets": packets, "prior_journal": NSNull(),
          "history_updates":historyUpdates,
        ],
      ]) { _, new in new }
      _ = try ring(staged)
      try file.write(staged, beforeReplace: { try checkpoint?("before_local_commit") })
      try checkpoint?("after_local_commit")
      return true
    }
  }
  public func recoverPublication() throws {
    try run {
      try file.recover()
      _ = try history(load()).recovery()
    }
  }
  public func availableEpochs() throws -> [Int] {
    try run { try map(load()["keys"]).keys.compactMap(Int.init).sorted() }
  }
  public func cleanupEpochs(
    retainEpochs: Set<Int>, deleteEpoch: (Int) throws -> Void, containsEpoch: (Int) throws -> Bool
  ) throws {
    try cleanupEpochsForTesting(
      retainEpochs: retainEpochs, deleteEpoch: deleteEpoch, containsEpoch: containsEpoch)
  }
  func cleanupEpochsForTesting(
    retainEpochs: Set<Int>, deleteEpoch: (Int) throws -> Void, containsEpoch: (Int) throws -> Bool,
    checkpoint: ((String) throws -> Void)? = nil
  ) throws {
    try run {
      var s = try load()
      var keys = try map(s["keys"])
      guard ["ACTIVE", "CLEANUP_PENDING"].contains(s["status"] as? String ?? ""),
        try history(s).recovery()["status"] as? String == "ACTIVE"
      else { throw AtlasVaultRotationError.recovery }
      let available = Set(keys.keys.compactMap(Int.init))
      guard retainEpochs.contains(try R.integer(s["epoch"])), retainEpochs.isSubset(of: available)
      else { throw AtlasVaultRotationError.rejected }
      guard try pendingOperations().allSatisfy({ retainEpochs.contains(Int($0.envelope.keyEpoch)) })
      else { throw AtlasVaultRotationError.cleanupPending }
      try file.enable()
      s["status"] = "CLEANUP_PENDING"
      try file.write(s)
      try checkpoint?("cleanup_pending")
      for epoch in available.subtracting(retainEpochs).sorted() {
        try deleteEpoch(epoch)
        guard try !containsEpoch(epoch) else { throw AtlasVaultRotationError.cleanupPending }
        keys.removeValue(forKey: String(epoch))
      }
      s["keys"] = keys
      s["status"] = "ACTIVE"
      try file.write(s)
    }
  }
}
