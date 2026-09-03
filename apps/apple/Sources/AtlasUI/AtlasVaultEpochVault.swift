import CryptoKit
import Foundation

/// D087 single-owner activation journal. One encrypted atomic commit publishes all components.
public final class AtlasVaultEpochVault {
  let file: EpochPublication
  let key: Data
  let registry: [[String: Any]]
  let context: [String: Any]
  private let lock = NSRecursiveLock()
  typealias R = AtlasVaultEpochRotation
  public init(
    directory: URL, storageKey: Data, deviceID: String, registry: [[String: Any]],
    accountID: String, vaultID: String, keyEpoch: Int, stateRoot: String
  ) throws {
    guard registry.contains(where: { $0["device_id"] as? String == deviceID }),
      stateRoot.utf8.count == 64,
      stateRoot.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) })
    else { throw AtlasVaultRotationError.rejected }
    self.registry = registry
    key = storageKey
    context = [
      "account_id": try viewIdentifier(accountID), "vault_id": try viewIdentifier(vaultID),
      "device_id": try viewIdentifier(deviceID),
      "key_epoch": try viewInteger(keyEpoch), "state_root": stateRoot,
      "registry_root": try AtlasVaultRevocation.registryRoot(registry),
    ]
    file = try EpochPublication(
      fileURL: directory.appendingPathComponent("activation"), key: storageKey)
  }
  func run<T>(_ body: () throws -> T) throws -> T {
    lock.lock()
    defer { lock.unlock() }
    do { return try body() } catch let error as AtlasVaultRotationError { throw error } catch {
      throw AtlasVaultRotationError.rejected
    }
  }
  func map(_ value: Any?) throws -> [String: Any] {
    guard let v = value as? [String: Any] else { throw AtlasVaultRotationError.rejected }
    return v
  }
  func rows(_ value: Any?) throws -> [[String: Any]] {
    guard let v = value as? [[String: Any]] else { throw AtlasVaultRotationError.rejected }
    return v
  }
  private func verify(_ proof: [String: Any]) throws -> [String: Any] {
    try R.verify(
      proof, registry: registry, accountID: context["account_id"] as! String,
      vaultID: context["vault_id"] as! String,
      previousEpoch: R.integer(context["key_epoch"]), stateRoot: context["state_root"] as! String)
  }
  private func record(_ value: [String: Any]) throws -> [String: Any] {
    try R.exact(value, ["format", "version", "status", "transition_id", "proof"])
    let proof = try map(value["proof"])
    guard value["format"] as? String == "atlasvault-activation-record",
      try R.integer(value["version"]) == 1,
      value["status"] as? String == "ACTIVATION_ACCEPTED",
      value["transition_id"] as? String == proof["root"] as? String
    else { throw AtlasVaultRotationError.rejected }
    return try verify(proof)
  }
  func ring(_ state: [String: Any]) throws -> AtlasVaultKeyEpochRing {
    let raw = try map(state["keys"])
    var keys = [Int64: Data]()
    guard !raw.isEmpty, raw.count <= 32 else { throw AtlasVaultRotationError.rejected }
    for (name, value) in raw {
      guard let epoch = Int64(name), String(epoch) == name else {
        throw AtlasVaultRotationError.rejected
      }
      keys[epoch] = try R.bytes(value, 32)
    }
    return try AtlasVaultKeyEpochRing.fromEntries(
      currentKeyEpoch: viewInteger(state["epoch"]), keys: keys)
  }
  func load() throws -> [String: Any] {
    let s = try file.read(default: [:])
    try R.exact(
      s,
      [
        "context", "status", "epoch", "registry", "recipients", "keys", "components", "journal",
        "generation",
      ])
    guard try R.canonical(map(s["context"])) == R.canonical(context),
      [
        "ACTIVE", "ACTIVATION_PENDING", "REVOKED", "RECOVERY_PENDING", "CATCH_UP_PENDING",
        "CLEANUP_PENDING",
      ].contains(
        s["status"] as? String ?? "")
    else { throw AtlasVaultRotationError.rejected }
    _ = try AtlasVaultRevocation.registryRoot(rows(s["registry"]))
    _ = try ring(s)
    try R.exact(map(s["components"]), ["history", "outbox", "inbox"])
    if let j = s["journal"] as? [String: Any], j["kind"] as? String == "CATCH_UP" {
      guard ["ACTIVE", "CATCH_UP_PENDING"].contains(j["phase"] as? String ?? "") else {
        throw AtlasVaultRotationError.rejected
      }
      let bridges = try EpochCatchUp.verify(
        EpochCatchUp.records(map(map(s["components"])["history"])), registry: registry,
        context: context)
      if j["phase"] as? String == "ACTIVE" {
        guard let last = bridges.last else { throw AtlasVaultRotationError.rejected }
        let plan = try map(last["plan"])
        guard try R.integer(s["epoch"]) == R.integer(plan["new_epoch"]),
          try AtlasVaultRevocation.registryRoot(rows(s["registry"])) == plan[
            "resulting_registry_root"] as? String,
          s["recipients"] as? [String] == plan["recipients"] as? [String]
        else { throw AtlasVaultRotationError.rejected }
      }
      return s
    }
    if let j = s["journal"] as? [String: Any] {
      guard
        [
          "PREPARED", "BACKEND_SUBMITTED", "BACKEND_ACCEPTED", "LOCAL_PUBLISHING", "ACTIVE",
          "RECOVERY_PENDING",
        ].contains(j["phase"] as? String ?? "")
      else { throw AtlasVaultRotationError.rejected }
      let proof = try map(j["proof"])
      let verified = try verify(proof)
      if let r = j["record"] as? [String: Any] {
        _ = try record(r)
        guard try R.canonical(map(r["proof"])) == R.canonical(proof) else {
          throw AtlasVaultRotationError.rejected
        }
      }
      if j["phase"] as? String == "ACTIVE" {
        guard try R.integer(s["epoch"]) == R.integer(verified["new_epoch"]),
          NSDictionary(dictionary: ["r": s["registry"]!]).isEqual(to: ["r": verified["registry"]!]),
          s["recipients"] as? [String] == verified["recipients"] as? [String],
          try R.canonical(map(map(map(s["components"])["history"])["epoch_bridge"]))
            == R.canonical(proof)
        else { throw AtlasVaultRotationError.rejected }
      }
    }
    return s
  }
  func component(_ name: String, fallback: [String: Any]) throws -> [String: Any] {
    try run { try map(map(load()["components"])[name] ?? fallback) }
  }
  func writeComponent(_ name: String, value: [String: Any], beforeReplace: (() throws -> Void)?)
    throws
  {
    try run {
      var s = try load()
      var components = try map(s["components"])
      components[name] = value
      s["components"] = components
      if name == "history", value["status"] as? String != "ACTIVE" {
        s["status"] = "RECOVERY_PENDING"
      }
      try file.write(s, beforeReplace: beforeReplace)
    }
  }
  func history(_ s: [String: Any]) throws -> AtlasVaultGuardedSyncState {
    let c = try map(map(map(s["components"])["history"])["context"])
    guard
      ["account_id", "vault_id", "key_epoch"].allSatisfy({
        String(describing: c[$0]!) == String(describing: context[$0]!)
      })
    else { throw AtlasVaultRotationError.rejected }
    let h = try AtlasVaultGuardedSyncState(
      fileURL: file.fileURL, encryptionKey: key, accountID: c["account_id"] as! String,
      vaultID: c["vault_id"] as! String,
      collectionID: c["collection_id"] as! String, keyEpoch: viewInteger(c["key_epoch"]),
      trustedSigner: R.bytes(c["signing_public_b64"], 32), rotationRegistry: registry)
    h.store = try componentFile("history")
    return h
  }
  private func componentFile(_ name: String) throws -> EncryptedQueueFile {
    try EncryptedQueueFile(
      fileURL: file.fileURL, key: key, read: { try self.component(name, fallback: $0) },
      write: { try self.writeComponent(name, value: $0, beforeReplace: $1) })
  }
  private func active(_ s: [String: Any]) throws {
    if s["status"] as? String == "CATCH_UP_PENDING" { throw AtlasVaultRotationError.catchUpPending }
    if s["status"] as? String == "CLEANUP_PENDING" { throw AtlasVaultRotationError.cleanupPending }
    if s["status"] as? String == "REVOKED" { throw AtlasVaultRotationError.revoked }
    if s["status"] as? String == "ACTIVATION_PENDING" { throw AtlasVaultRotationError.pending }
    guard s["status"] as? String == "ACTIVE",
      try history(s).recovery()["status"] as? String == "ACTIVE"
    else { throw AtlasVaultRotationError.recovery }
  }
  public func initialize(
    keys: [Int64: Data], history: AtlasVaultGuardedSyncState,
    outbox: AtlasVaultDurableEncryptedOutbox? = nil, inbox: AtlasVaultDurableEncryptedInbox? = nil
  ) throws {
    try run {
      guard !FileManager.default.fileExists(atPath: file.fileURL.path) else {
        throw AtlasVaultRotationError.rejected
      }
      let h = try history.load()
      let views = try rows(h["views"])
      let historyContext = try map(h["context"])
      guard h["status"] as? String == "ACTIVE",
        views.last?["root"] as? String == context["state_root"] as? String,
        historyContext["account_id"] as? String == context["account_id"] as? String,
        historyContext["vault_id"] as? String == context["vault_id"] as? String,
        try viewInteger(historyContext["key_epoch"]) == viewInteger(context["key_epoch"])
      else { throw AtlasVaultRotationError.recovery }
      _ = try AtlasVaultKeyEpochRing.fromEntries(
        currentKeyEpoch: viewInteger(context["key_epoch"]), keys: keys)
      _ = try outbox?.pendingOperations()
      _ = try inbox?.pendingOperations()
      try file.write([
        "context": context, "status": "ACTIVE", "epoch": context["key_epoch"]!,
        "registry": registry,
        "recipients": registry.filter { $0["state"] as? String == "ACTIVE" }.map {
          $0["device_id"] as! String
        }.sorted(),
        "keys": Dictionary(
          uniqueKeysWithValues: keys.map { (String($0.key), $0.value.base64EncodedString()) }),
        "components": [
          "history": h,
          "outbox": try outbox?.store.read(default: outboxDefault()) ?? outboxDefault(),
          "inbox": try inbox?.store.read(default: inboxDefault()) ?? inboxDefault(),
        ],
        "journal": NSNull(), "generation": 1,
      ])
      _ = try self.history(load())
    }
  }
  public func observation() throws -> [String: Any] {
    try run {
      let s = try load()
      let h = try history(s).checkpoint()
      return [
        "status": s["status"]!, "key_epoch": s["epoch"]!,
        "registry_root": try AtlasVaultRevocation.registryRoot(rows(s["registry"])),
        "recipients": s["recipients"]!, "state_root": h["cursor"]!, "sequence": h["sequence"]!,
        "generation": s["generation"]!,
        "journal_phase": (s["journal"] as? [String: Any])?["phase"] ?? NSNull(),
        "recipient_commitment": R.digest(
          Data("atlasvault-active-recipients-v1\n".utf8)
            + (try R.canonical(["recipients": s["recipients"]!]))),
      ]
    }
  }
  public func prepareRotation(_ proof: [String: Any]) throws {
    try run {
      var s = try load()
      try active(s)
      _ = try verify(proof)
      guard
        try history(s).checkpoint()["cursor"] as? String == map(proof["plan"])["state_root"]
          as? String
      else { throw AtlasVaultRotationError.recovery }
      if let j = s["journal"] as? [String: Any],
        (j["proof"] as? [String: Any])?["root"] as? String != proof["root"] as? String
      {
        throw AtlasVaultRotationError.conflict
      }
      s["journal"] = ["phase": "PREPARED", "proof": proof, "record": NSNull()]
      try file.write(s)
    }
  }
  public func beginActivation(_ proof: [String: Any]) throws {
    try run {
      try prepareRotation(proof)
      var s = try load()
      var j = try map(s["journal"])
      s["status"] = "ACTIVATION_PENDING"
      j["phase"] = "BACKEND_SUBMITTED"
      s["journal"] = j
      try file.write(s)
    }
  }
  public func acceptRotation(
    _ proof: [String: Any], acceptedRecord: [String: Any], agreementPrivateKey: Data
  ) throws -> Bool {
    try acceptRotationForTesting(
      proof, acceptedRecord: acceptedRecord, agreementPrivateKey: agreementPrivateKey)
  }
  func acceptRotationForTesting(
    _ proof: [String: Any], acceptedRecord: [String: Any], agreementPrivateKey: Data,
    checkpoint: ((String) throws -> Void)? = nil
  ) throws -> Bool {
    try run {
      var s = try load()
      let verified = try record(acceptedRecord)
      guard try R.canonical(map(acceptedRecord["proof"])) == R.canonical(proof) else {
        throw AtlasVaultRotationError.rejected
      }
      if s["status"] as? String == "REVOKED" { throw AtlasVaultRotationError.revoked }
      guard s["status"] as? String != "RECOVERY_PENDING",
        try history(s).recovery()["status"] as? String == "ACTIVE"
      else { throw AtlasVaultRotationError.recovery }
      if let j = s["journal"] as? [String: Any] {
        guard (j["proof"] as? [String: Any])?["root"] as? String == proof["root"] as? String else {
          throw AtlasVaultRotationError.conflict
        }
        if j["phase"] as? String == "ACTIVE" { return false }
      }
      var j: [String: Any] = [
        "phase": "BACKEND_ACCEPTED", "proof": proof, "record": acceptedRecord,
      ]
      s["status"] = "ACTIVATION_PENDING"
      s["journal"] = j
      try file.write(s)
      try checkpoint?("backend_accepted")
      let device = context["device_id"] as! String
      guard (verified["recipients"] as! [String]).contains(device) else {
        s["status"] = "REVOKED"
        try file.write(s)
        throw AtlasVaultRotationError.revoked
      }
      let d = try rows(proof["deliveries"]).first { $0["device_id"] as? String == device }!
      let plan = try map(proof["plan"])
      let opened = try AtlasVaultKeyEpochHPKE.open(
        recipientPrivateKey: agreementPrivateKey,
        sealed: AtlasVaultKeyEpochHPKESealedVaultKeyV2(
          keyEpoch: viewInteger(d["key_epoch"]),
          encapsulatedKey: R.bytes(d["encapsulated_key_b64"], 32),
          ciphertext: R.bytes(d["ciphertext_b64"], 48)),
        context: Data("atlasvault-rotation-delivery-v1:\(try R.binding(plan)):\(device)".utf8),
        minimumKeyEpoch: viewInteger(verified["new_epoch"]))
      var staged = s
      var components = try map(s["components"])
      var keys = try map(s["keys"])
      components["history"] = try history(s).stageEpoch(proof)
      keys[String(opened.keyEpoch)] = opened.vaultKey.base64EncodedString()
      staged["components"] = components
      staged["keys"] = keys
      staged["epoch"] = verified["new_epoch"]
      staged["registry"] = verified["registry"]
      staged["recipients"] = verified["recipients"]
      staged["generation"] = try R.integer(s["generation"]) + 1
      staged["status"] = "ACTIVE"
      j["phase"] = "ACTIVE"
      staged["journal"] = j
      _ = try ring(staged)
      j["phase"] = "LOCAL_PUBLISHING"
      s["journal"] = j
      try file.write(s)
      try checkpoint?("local_publishing")
      try file.write(staged, beforeReplace: { try checkpoint?("before_local_commit") })
      try checkpoint?("after_local_commit")
      return true
    }
  }
  public func queueOperation(_ operation: AtlasVaultEncryptedPatchOperation) throws {
    try run {
      let s = try load()
      try active(s)
      guard operation.envelope.keyEpoch == (try viewInteger(s["epoch"])),
        operation.authorDeviceID == context["device_id"] as? String
      else { throw AtlasVaultRotationError.write }
      let outbox = try AtlasVaultDurableEncryptedOutbox(fileURL: file.fileURL, encryptionKey: key)
      outbox.store = try componentFile("outbox")
      try outbox.enqueue(operation)
    }
  }
  public func pendingOperations() throws -> [AtlasVaultEncryptedPatchOperation] {
    try run {
      let outbox = try AtlasVaultDurableEncryptedOutbox(fileURL: file.fileURL, encryptionKey: key)
      outbox.store = try componentFile("outbox")
      return try outbox.pendingOperations()
    }
  }
  public func delivery(_ recipient: String) throws -> [String: Any] {
    try run {
      let s = try load()
      try active(s)
      guard let j = s["journal"] as? [String: Any], j["phase"] as? String == "ACTIVE",
        (s["recipients"] as! [String]).contains(recipient),
        let d = try rows(map(j["proof"])["deliveries"]).first(where: {
          $0["device_id"] as? String == recipient
        })
      else { throw AtlasVaultRotationError.revoked }
      return d
    }
  }
  public func compareEvidence(_ peer: [[String: Any]]) throws -> Int {
    try run { try history(load()).compareEvidence(peer) }
  }
  public func recovery() throws -> [String: Any] {
    try run { try history(load()).recovery() }
  }
  public func pendingActivation() throws -> [String: Any]? {
    try run {
      let s = try load()
      guard !["REVOKED", "RECOVERY_PENDING"].contains(s["status"] as! String),
        try history(s).recovery()["status"] as? String == "ACTIVE"
      else { throw AtlasVaultRotationError.recovery }
      guard let j = s["journal"] as? [String: Any], j["phase"] as? String != "ACTIVE" else {
        return nil
      }
      return try map(j["proof"])
    }
  }
  public func createCommitment(_ opaqueState: Data, signingKey: Curve25519.Signing.PrivateKey)
    throws -> [String: Any]
  {
    try run {
      let s = try load()
      try active(s)
      guard let j = s["journal"] as? [String: Any], j["phase"] as? String == "ACTIVE" else {
        throw AtlasVaultRotationError.rejected
      }
      let h = try history(s)
      let prior = try history(s).exportEvidence().last!
      let c = try AtlasVaultSignedStateCommitment.sign(
        opaqueState,
        collectionID: map(map(map(s["components"])["history"])["context"])["collection_id"]
          as! String,
        sequence: viewInteger(prior["sequence"]) + 1,
        previousRoot: prior["collection_root"] as! String, signingKey: signingKey)
      let view = try AtlasVaultAuthenticatedStateView.sign(
        [
          "format": "atlasvault-authenticated-state-view", "version": 2,
          "account_id": context["account_id"]!, "vault_id": context["vault_id"]!,
          "sequence": c.sequence, "previous_root": prior["root"]!, "collection_root": c.root,
          "registry_root": AtlasVaultRevocation.registryRoot(rows(s["registry"])),
          "previous_registry_root": prior["registry_root"]!, "key_epoch": s["epoch"]!,
        ], signingKey: signingKey)
      _ = try h.ingest(
        view: view, registry: rows(s["registry"]), collection: c.jsonObject,
        opaqueState: opaqueState)
      return ["view": view, "collection": c.jsonObject]
    }
  }
  public func seal(
    _ kind: String, plaintext: Data, objectID: String, revision: String,
    signingKey: Curve25519.Signing.PrivateKey
  ) throws -> AtlasVaultOpaqueCiphertextEnvelope {
    try run {
      let s = try load()
      try active(s)
      guard ["patch", "snapshot"].contains(kind), plaintext.count <= 1024 * 1024 else {
        throw AtlasVaultRotationError.rejected
      }
      let m: [String: Any] = [
        "format": "atlasvault-epoch-ciphertext", "version": 1, "account_id": context["account_id"]!,
        "vault_id": context["vault_id"]!, "key_epoch": s["epoch"]!,
        "device_id": context["device_id"]!, "kind": kind, "object_id": try viewIdentifier(objectID),
        "revision": try viewIdentifier(revision),
      ]
      let aad = try R.canonical(m)
      let nonce = AES.GCM.Nonce()
      let encryptionKey = try ring(s).deriveRecordKey(
        keyEpoch: viewInteger(s["epoch"]), vaultID: context["vault_id"] as! String,
        recordID: objectID)
      let box = try AES.GCM.seal(
        plaintext, using: SymmetricKey(data: encryptionKey), nonce: nonce, authenticating: aad)
      let ciphertext = box.ciphertext + box.tag
      let message =
        Data("atlasvault-epoch-ciphertext-signature-v1\0".utf8) + aad + Data(nonce) + ciphertext
      let signature = try signingKey.signature(for: message)
      let entry = try rows(s["registry"]).first {
        $0["device_id"] as? String == context["device_id"] as? String
      }!
      guard
        try Curve25519.Signing.PublicKey(
          rawRepresentation: R.bytes(entry["signing_public_b64"], 32)
        ).isValidSignature(signature, for: message)
      else { throw AtlasVaultRotationError.rejected }
      return try AtlasVaultOpaqueCiphertextEnvelope(jsonObject: [
        "format": "atlasvault-opaque-ciphertext-envelope", "version": 1, "object_id": objectID,
        "revision": revision, "parent_revision": NSNull(), "key_epoch": s["epoch"]!,
        "nonce_b64": Data(nonce).base64EncodedString(),
        "ciphertext_b64": ciphertext.base64EncodedString(), "aad_b64": aad.base64EncodedString(),
        "signature_b64": signature.base64EncodedString(), "tombstone": false,
        "content_sha256": R.digest(ciphertext),
      ])
    }
  }
  public func open(_ envelope: AtlasVaultOpaqueCiphertextEnvelope) throws -> Data {
    try run {
      let s = try load()
      let raw = envelope.jsonObject
      guard let aad = Data(base64Encoded: raw["aad_b64"] as? String ?? ""),
        let ciphertext = Data(base64Encoded: raw["ciphertext_b64"] as? String ?? ""),
        ciphertext.count >= 16
      else { throw AtlasVaultRotationError.rejected }
      let m = try map(JSONSerialization.jsonObject(with: aad))
      try R.exact(
        m,
        [
          "format", "version", "account_id", "vault_id", "key_epoch", "device_id", "kind",
          "object_id", "revision",
        ])
      guard m["format"] as? String == "atlasvault-epoch-ciphertext",
        try R.integer(m["version"]) == 1,
        ["patch", "snapshot"].contains(m["kind"] as? String ?? ""),
        m["account_id"] as? String == context["account_id"] as? String,
        m["vault_id"] as? String == context["vault_id"] as? String,
        try viewInteger(m["key_epoch"]) == envelope.keyEpoch,
        m["object_id"] as? String == envelope.objectID,
        m["revision"] as? String == envelope.revision, try R.canonical(m) == aad
      else { throw AtlasVaultRotationError.rejected }
      if envelope.keyEpoch > (try viewInteger(context["key_epoch"])),
        !(s["recipients"] as! [String]).contains(m["device_id"] as? String ?? "")
      {
        throw AtlasVaultRotationError.revoked
      }
      let nonce = try R.bytes(raw["nonce_b64"], 12)
      guard
        let entry = registry.first(where: {
          $0["device_id"] as? String == m["device_id"] as? String
        }),
        try Curve25519.Signing.PublicKey(
          rawRepresentation: R.bytes(entry["signing_public_b64"], 32)
        ).isValidSignature(
          R.bytes(raw["signature_b64"], 64),
          for: Data("atlasvault-epoch-ciphertext-signature-v1\0".utf8) + aad + nonce + ciphertext)
      else { throw AtlasVaultRotationError.rejected }
      let encryptionKey = try ring(s).deriveRecordKey(
        keyEpoch: envelope.keyEpoch, vaultID: context["vault_id"] as! String,
        recordID: envelope.objectID)
      return try AES.GCM.open(
        AES.GCM.SealedBox(
          nonce: AES.GCM.Nonce(data: nonce), ciphertext: ciphertext.dropLast(16),
          tag: ciphertext.suffix(16)), using: SymmetricKey(data: encryptionKey), authenticating: aad
      )
    }
  }
}
