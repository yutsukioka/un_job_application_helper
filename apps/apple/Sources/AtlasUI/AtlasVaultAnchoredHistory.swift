import CryptoKit
import Foundation

public enum AtlasVaultBootstrapError: String, Error {
  case rejected = "ATLAS_BOOTSTRAP_REJECTED"
}

/// An immutable recipient-pinned store origin; all later admission uses the P6 core.
public final class AtlasVaultAnchoredSyncState {
  private typealias R = AtlasVaultEpochRotation
  private typealias E = AtlasVaultDeviceEnrollment
  private let trust: [String: Any]
  private let core: AtlasVaultGuardedSyncState
  private let actual: EncryptedQueueFile
  private var anchor: [String: Any]?
  private var historicalRecords = [String: Any]()
  private let publicKey: Data

  public func publicationOrigin() throws -> [String: Any] {
    try core.run {
      try core.active(core.load())
      guard let anchor else { throw AtlasVaultBootstrapError.rejected }
      return [
        "format": "atlasvault-anchored-publication-origin", "version": 1,
        "context": [
          "account_id": trust["account_id"]!, "vault_id": trust["vault_id"]!,
          "collection_id": trust["collection_id"]!, "key_epoch": trust["key_epoch"]!,
          "signing_public_b64": publicKey.base64EncodedString(),
        ],
        "pins": trust.filter {
          ["anchor_root", "recipient_device_id", "confirmed_transcript", "current_context"]
            .contains($0.key)
        },
        "registry": trust["registry"]!, "anchor": anchor,
      ]
    }
  }

  static func publicationReader(_ owner: AtlasVaultEpochVault) throws -> AtlasVaultGuardedSyncState
  {
    try publicationWrapper(owner).core
  }

  private static func publicationWrapper(_ owner: AtlasVaultEpochVault) throws
    -> AtlasVaultAnchoredSyncState
  {
    guard let o = owner.historyOrigin else { throw AtlasVaultBootstrapError.rejected }
    try R.exact(o, ["format", "version", "context", "pins", "registry", "anchor"])
    guard o["format"] as? String == "atlasvault-anchored-publication-origin",
      try E.integer(o["version"]) == 1
    else { throw AtlasVaultBootstrapError.rejected }
    let c = try AtlasVaultDeviceDelivery.map(o["context"])
    let pins = try AtlasVaultDeviceDelivery.map(o["pins"])
    try R.exact(c, ["account_id", "vault_id", "collection_id", "key_epoch", "signing_public_b64"])
    try R.exact(
      pins, ["anchor_root", "recipient_device_id", "confirmed_transcript", "current_context"])
    let a = try AtlasVaultDeviceDelivery.map(o["anchor"])
    let v = try AtlasVaultDeviceDelivery.map(a["view"])
    guard
      ["account_id", "vault_id"].allSatisfy({ c[$0] as? String == owner.context[$0] as? String }),
      try E.integer(c["key_epoch"]) == E.integer(owner.context["key_epoch"]),
      pins["recipient_device_id"] as? String == owner.context["device_id"] as? String,
      v["root"] as? String == owner.context["state_root"] as? String,
      try R.canonical(["registry": o["registry"]!]) == R.canonical(["registry": owner.registry])
    else { throw AtlasVaultBootstrapError.rejected }
    var trust = c.filter {
      ["account_id", "vault_id", "collection_id", "key_epoch"].contains($0.key)
    }
    trust["trusted_signer_b64"] = c["signing_public_b64"]
    trust["registry"] = o["registry"]
    trust.merge(pins) { _, rhs in rhs }
    let reader = try AtlasVaultAnchoredSyncState(
      fileURL: owner.file.fileURL, encryptionKey: owner.key, trust: trust)
    try reader.verifyAnchor(a)
    reader.core.store = try owner.componentFile("history")
    return reader
  }

  static func retainedAuthor(
    _ owner: AtlasVaultEpochVault, state: [String: Any],
    envelope: AtlasVaultOpaqueCiphertextEnvelope
  ) throws -> [[String: Any]] {
    try owner.active(state)
    let reader = try publicationWrapper(owner)
    try reader.core.active(reader.core.load())
    let row = try AtlasVaultDeviceDelivery.map(reader.historicalRecords[envelope.objectID])
    guard try E.integer(row["key_epoch"]) == envelope.keyEpoch,
      row["envelope_sha256"] as? String == R.digest(try R.canonical(envelope.jsonObject))
    else { throw AtlasVaultHistoricalAuthorityError.rejected }
    return [try AtlasVaultDeviceDelivery.map(row["author"])]
  }

  func publicationState() throws -> AtlasVaultGuardedSyncState {
    try core.active(core.load())
    return core
  }

  public init(fileURL: URL, encryptionKey: Data, trust: [String: Any]) throws {
    try R.exact(
      trust,
      [
        "account_id", "vault_id", "collection_id", "key_epoch", "trusted_signer_b64",
        "registry", "anchor_root", "recipient_device_id", "confirmed_transcript", "current_context",
      ])
    guard let account = trust["account_id"] as? String, let vault = trust["vault_id"] as? String,
      let collection = trust["collection_id"] as? String
    else { throw AtlasVaultBootstrapError.rejected }
    self.trust = try JSONSerialization.jsonObject(with: R.canonical(trust)) as! [String: Any]
    publicKey = try R.bytes(trust["trusted_signer_b64"], 32)
    core = try AtlasVaultGuardedSyncState(
      fileURL: fileURL, encryptionKey: encryptionKey,
      accountID: account, vaultID: vault,
      collectionID: collection, keyEpoch: viewInteger(trust["key_epoch"]),
      trustedSigner: publicKey, rotationRegistry: AtlasVaultDeviceDelivery.rows(trust["registry"]))
    actual = try EncryptedQueueFile(
      fileURL: fileURL, encryptionKey: encryptionKey,
      kind: "anchored-history-v1:\(R.digest(try R.canonical(trust)))")
    core.store = try EncryptedQueueFile(
      fileURL: fileURL, key: encryptionKey,
      read: { [weak self] _ in
        guard let self else { throw AtlasVaultBootstrapError.rejected }
        let outer = try actual.read(default: [:])
        try R.exact(outer, ["anchor", "state"])
        let a = try AtlasVaultDeviceDelivery.map(outer["anchor"])
        try verifyAnchor(a)
        anchor = a
        return try AtlasVaultDeviceDelivery.map(outer["state"])
      },
      write: { [weak self] state, before in
        guard let self, let anchor else { throw AtlasVaultBootstrapError.rejected }
        try actual.write(["anchor": anchor, "state": state], beforeReplace: before)
      })
  }

  func verifyAnchor(_ a: [String: Any]) throws {
    try R.exact(
      a,
      Set(
        ["checkpoint", "enrollment", "view"]
          + (a["historical_authority"] == nil ? [] : ["historical_authority"])))
    let p = try AtlasVaultDeviceDelivery.map(a["checkpoint"])
    let e = try AtlasVaultDeviceDelivery.map(a["enrollment"])
    try R.exact(
      p,
      E.contextFields.union([
        "format", "version", "registry_root", "sequence",
        "collection_id", "collection_root", "collection_sha256", "enrollment_root",
        "recipient_device_id",
        "recipient_agreement_sha256", "transcript_sha256", "issuer_device_id",
        "signature_algorithm", "root", "signature_b64",
      ]))
    let context = try AtlasVaultDeviceDelivery.map(trust["current_context"])
    try R.exact(context, E.contextFields)
    guard p["format"] as? String == "atlasvault-history-bootstrap",
      try E.integer(p["version"]) == 1,
      p["signature_algorithm"] as? String == "Ed25519"
    else { throw AtlasVaultBootstrapError.rejected }
    for k in E.contextFields {
      if ["key_epoch", "registry_generation"].contains(k) {
        guard try E.integer(p[k]) == E.integer(context[k]) else {
          throw AtlasVaultBootstrapError.rejected
        }
      } else {
        guard p[k] as? String == context[k] as? String else {
          throw AtlasVaultBootstrapError.rejected
        }
      }
    }
    let registry = try AtlasVaultDeviceDelivery.rows(trust["registry"])
    guard p["recipient_device_id"] as? String == trust["recipient_device_id"] as? String,
      p["recipient_device_id"] as? String == e["target_device_id"] as? String,
      p["recipient_agreement_sha256"] as? String == e["target_agreement_sha256"] as? String,
      p["transcript_sha256"] as? String == trust["confirmed_transcript"] as? String,
      p["transcript_sha256"] as? String == e["transcript_sha256"] as? String,
      p["enrollment_root"] as? String == e["root"] as? String,
      try E.integer(p["registry_generation"]) == E.integer(e["next_registry_generation"]),
      try E.integer(p["key_epoch"]) == E.integer(e["key_epoch"]),
      p["activation_id"] as? String == e["activation_id"] as? String,
      p["registry_root"] as? String == e["resulting_registry_root"] as? String,
      try AtlasVaultRevocation.registryRoot(registry) == p["registry_root"] as? String
    else { throw AtlasVaultBootstrapError.rejected }
    let prior = registry.filter {
      $0["device_id"] as? String != p["recipient_device_id"] as? String
    }
    let after = try E.verify(
      e, registry: prior, context: e.filter { E.contextFields.contains($0.key) },
      confirmedTranscript: trust["confirmed_transcript"] as! String, status: "ACTIVE")
    let sorted = registry.sorted { ($0["device_id"] as! String) < ($1["device_id"] as! String) }
    guard try R.canonical(["registry": after]) == R.canonical(["registry": sorted]),
      let issuer = prior.first(where: {
        $0["device_id"] as? String == p["issuer_device_id"] as? String
          && $0["state"] as? String == "ACTIVE"
      }), try R.bytes(issuer["signing_public_b64"], 32) == publicKey
    else { throw AtlasVaultBootstrapError.rejected }
    for k in ["state_root", "collection_root", "collection_sha256", "root"] { _ = try E.hex(p[k]) }
    _ = try E.integer(p["sequence"])
    let unsigned = p.filter { !["root", "signature_b64"].contains($0.key) }
    let root = R.digest(
      Data("atlasvault-history-bootstrap-v1\n".utf8) + (try R.canonical(unsigned)))
    let message =
      Data("atlasvault-history-bootstrap-signature-v1\0".utf8)
      + Data(stride(from: 0, to: 64, by: 2).map { UInt8(root.dropFirst($0).prefix(2), radix: 16)! })
    guard p["root"] as? String == root, root == trust["anchor_root"] as? String,
      try Curve25519.Signing.PublicKey(rawRepresentation: publicKey).isValidSignature(
        R.bytes(p["signature_b64"], 64), for: message)
    else { throw AtlasVaultBootstrapError.rejected }
    let v = try verifiedView(AtlasVaultDeviceDelivery.map(a["view"]), publicKey: publicKey)
    for k in ["account_id", "vault_id", "registry_root", "collection_root"] {
      guard v[k] as? String == p[k] as? String else { throw AtlasVaultBootstrapError.rejected }
    }
    for k in ["key_epoch", "sequence"] {
      guard try viewInteger(v[k]) == E.integer(p[k]) else {
        throw AtlasVaultBootstrapError.rejected
      }
    }
    for k in ["account_id", "vault_id", "collection_id"] {
      guard p[k] as? String == trust[k] as? String else { throw AtlasVaultBootstrapError.rejected }
    }
    guard v["root"] as? String == p["state_root"] as? String,
      try E.integer(p["key_epoch"]) == E.integer(trust["key_epoch"])
    else { throw AtlasVaultBootstrapError.rejected }
    core.historyOrigin = v
    core.anchorBridgeContext = p.filter {
      ["registry_generation", "activation_id", "issuer_device_id"].contains($0.key)
    }
    historicalRecords = [:]
    if let raw = a["historical_authority"] {
      let h = try AtlasVaultDeviceDelivery.map(raw)
      try R.exact(h, ["proof", "collection", "opaque_b64"])
      guard let encoded = h["opaque_b64"] as? String, encoded.count <= 1_398_104,
        let bytes = Data(base64Encoded: encoded), bytes.base64EncodedString() == encoded
      else { throw AtlasVaultHistoricalAuthorityError.rejected }
      historicalRecords = try AtlasVaultHistoricalAuthority.verify(
        AtlasVaultDeviceDelivery.map(h["proof"]), anchor: a, publicKey: publicKey,
        trust: trust, collection: AtlasVaultDeviceDelivery.map(h["collection"]), bytes: bytes)
    }
  }

  public func installHistoricalAuthority(
    _ proof: [String: Any], collection: [String: Any], opaqueState: Data
  ) throws -> Bool {
    try installHistoricalAuthorityForTesting(
      proof, collection: collection, opaqueState: opaqueState, beforeReplace: nil)
  }

  func installHistoricalAuthorityForTesting(
    _ proof: [String: Any], collection: [String: Any], opaqueState: Data,
    beforeReplace: (() throws -> Void)?
  ) throws -> Bool {
    let result: Result<Bool, AtlasVaultHistoricalAuthorityError> = try core.run {
      do {
        let state = try core.load()
        try core.active(state)
        guard let existing = anchor, let views = state["views"] as? [[String: Any]],
          views.count == 1
        else { throw AtlasVaultBootstrapError.rejected }
        let added: [String: Any] = [
          "proof": proof, "collection": collection, "opaque_b64": opaqueState.base64EncodedString(),
        ]
        if let installed = existing["historical_authority"] {
          guard try R.canonical(AtlasVaultDeviceDelivery.map(installed)) == R.canonical(added)
          else {
            throw AtlasVaultBootstrapError.rejected
          }
          return .success(false)
        }
        var updated = existing
        updated["historical_authority"] = added
        try verifyAnchor(updated)
        try actual.write(["anchor": updated, "state": state], beforeReplace: beforeReplace)
        anchor = updated
        return .success(true)
      } catch let error as AtlasVaultHistoricalAuthorityError { return .failure(error) }
    }
    return try result.get()
  }

  public func bootstrap(_ packet: [String: Any]) throws -> Bool {
    do {
      return try core.run {
        try R.exact(
          packet,
          [
            "checkpoint", "enrollment", "registry", "current_context", "recipient_device_id",
            "confirmed_transcript", "view", "collection", "opaque_b64",
          ])
        for k in ["registry", "current_context", "recipient_device_id", "confirmed_transcript"] {
          guard try R.canonical(["value": packet[k]!]) == R.canonical(["value": trust[k]!])
          else { throw AtlasVaultBootstrapError.rejected }
        }
        let a = packet.filter { ["checkpoint", "enrollment", "view"].contains($0.key) }
        try verifyAnchor(a)
        let p = try AtlasVaultDeviceDelivery.map(packet["checkpoint"])
        let c = try AtlasVaultSignedStateCommitment(
          jsonObject: AtlasVaultDeviceDelivery.map(packet["collection"]))
        guard let encoded = packet["opaque_b64"] as? String, encoded.count <= 1_398_104,
          let bytes = Data(base64Encoded: encoded), bytes.base64EncodedString() == encoded,
          bytes.count <= 1024 * 1024, c.collectionID == p["collection_id"] as? String,
          c.sequence == (try E.integer(p["sequence"])), c.root == p["collection_root"] as? String,
          c.stateSHA256 == p["collection_sha256"] as? String, c.stateSHA256 == R.digest(bytes),
          try c.verify(publicKey: publicKey)
        else { throw AtlasVaultBootstrapError.rejected }
        guard let body = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
          throw AtlasVaultBootstrapError.rejected
        }
        try R.exact(body, ["format", "version", "route", "records"])
        guard body["format"] as? String == "atlasvault-guarded-collection",
          try E.integer(body["version"]) == 1,
          ["patch", "snapshot", "compaction"].contains(body["route"] as? String ?? ""),
          let raw = body["records"] as? [[String: Any]], raw.count <= 256
        else { throw AtlasVaultBootstrapError.rejected }
        var records = [String: Any]()
        for item in raw {
          let r = try AtlasVaultOpaqueCiphertextEnvelope(jsonObject: item)
          guard r.version == 1, r.keyEpoch <= (try E.integer(p["key_epoch"])),
            records[r.objectID] == nil
          else { throw AtlasVaultBootstrapError.rejected }
          records[r.objectID] = [
            "object_id": r.objectID, "revision": r.revision, "content_sha256": r.contentSHA256,
            "envelope_sha256": R.digest(try R.canonical(r.jsonObject)), "tombstone": r.tombstone,
          ]
        }
        if FileManager.default.fileExists(atPath: actual.fileURL.path) {
          try core.active(core.load())
          guard let anchor,
            try R.canonical(anchor.filter { $0.key != "historical_authority" }) == R.canonical(a)
          else {
            throw AtlasVaultBootstrapError.rejected
          }
          return false
        }
        anchor = a
        let context: [String: Any] = [
          "account_id": trust["account_id"]!, "vault_id": trust["vault_id"]!,
          "collection_id": trust["collection_id"]!, "key_epoch": trust["key_epoch"]!,
          "signing_public_b64": publicKey.base64EncodedString(),
        ]
        try core.store.write([
          "context": context, "views": [a["view"]!], "records": records, "cases": [],
          "status": "ACTIVE",
        ])
        return true
      }
    } catch { throw AtlasVaultBootstrapError.rejected }
  }
  public func checkpoint() throws -> [String: Any] { try core.checkpoint() }
  public func exportEvidence() throws -> [[String: Any]] { try core.exportEvidence() }
  public func compareEvidence(_ peer: [[String: Any]]) throws -> Int {
    try core.compareEvidence(peer)
  }
  public func evidence() throws -> [String: Any] { try core.evidence() }
  public func automaticSync<T>(_ operation: () throws -> T) throws -> T {
    try core.automaticSync(operation)
  }
  public func recovery() throws -> [String: Any] {
    var v = try core.recovery()
    if v["status"] as? String == "MANUAL_REQUIRED" { v["status"] = "RECOVERY_PENDING" }
    return v
  }
  public func ingest(
    view: [String: Any], registry: [[String: Any]], collection: [String: Any], opaqueState: Data
  ) throws -> Bool {
    try core.ingest(
      view: view, registry: registry, collection: collection, opaqueState: opaqueState)
  }
}
