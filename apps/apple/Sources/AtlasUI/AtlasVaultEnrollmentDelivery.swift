import CryptoKit
import Darwin
import Foundation

public enum AtlasVaultEnrollmentDeliveryError: Error, Equatable {
  case rejected
}

/// D100 recipient-only delivery. No activation/proof or HPKE format is rewritten.
public enum AtlasVaultEnrollmentDelivery {
  private typealias R = AtlasVaultEpochRotation
  private typealias E = AtlasVaultDeviceEnrollment
  private static let installLock = NSLock()
  private static func map(_ value: Any?) throws -> [String: Any] {
    guard let result = value as? [String: Any] else {
      throw AtlasVaultEnrollmentDeliveryError.rejected
    }
    return result
  }
  private static func unsigned(_ p: [String: Any]) -> [String: Any] {
    p.filter { !["root", "signature_b64"].contains($0.key) }
  }
  private static func root(_ p: [String: Any]) throws -> String {
    R.digest(Data("atlasvault-enrollment-delivery-v1\n".utf8) + (try R.canonical(unsigned(p))))
  }
  private static func message(_ root: String) throws -> Data {
    guard root.count == 64 else { throw AtlasVaultEnrollmentDeliveryError.rejected }
    let hex = Array(root.utf8)
    var data = Data("atlasvault-enrollment-delivery-signature-v1\0".utf8)
    for i in stride(from: 0, to: 64, by: 2) {
      guard let byte = UInt8(String(bytes: hex[i..<(i + 2)], encoding: .utf8)!, radix: 16) else {
        throw AtlasVaultEnrollmentDeliveryError.rejected
      }
      data.append(byte)
    }
    return data
  }
  private static func context(_ p: [String: Any]) throws -> Data {
    Data("atlasvault-enrollment-delivery-hpke-v1\0".utf8)
      + Data(SHA256.hash(data: try R.canonical(unsigned(p).filter { $0.key != "deliveries" })))
  }

  public static func install(
    directory: URL, packet: [String: Any], pins: [String: Any], trustedSigner: Data,
    recipient: AtlasVaultDeviceIdentity, agreementPrivateKey: Data, storageKey: Data
  ) throws -> AtlasVaultEpochVault {
    var keys = [Int64: Data]()
    defer {
      for epoch in Array(keys.keys) {
        let count = keys[epoch]?.count ?? 0
        keys[epoch]?.resetBytes(in: 0..<count)
      }
      keys.removeAll()
    }
    do {
      let encoded = try R.canonical(packet)
      guard encoded.count <= 2 * 1024 * 1024, storageKey.count == 32 else {
        throw AtlasVaultEnrollmentDeliveryError.rejected
      }
      let p = try map(JSONSerialization.jsonObject(with: encoded))
      try R.exact(
        p,
        [
          "format", "version", "hpke_suite", "anchor", "registry", "collection", "opaque_b64",
          "historical_authority", "deliveries", "root", "signature_b64",
        ])
      let digest = try root(p)
      guard p["format"] as? String == "atlasvault-enrollment-delivery",
        try E.integer(p["version"]) == 1,
        p["hpke_suite"] as? String == "0x0020/0x0001/0x0002", p["root"] as? String == digest,
        try Curve25519.Signing.PublicKey(rawRepresentation: trustedSigner).isValidSignature(
          R.bytes(p["signature_b64"], 64), for: message(digest))
      else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      let anchor = try map(p["anchor"])
      let checkpoint = try map(anchor["checkpoint"])
      var trust = checkpoint.filter {
        ["account_id", "vault_id", "collection_id", "key_epoch"].contains($0.key)
      }
      trust["trusted_signer_b64"] = trustedSigner.base64EncodedString()
      trust["registry"] = p["registry"]
      trust.merge(pins) { _, rhs in rhs }
      let historyKey = Data(
        HMAC<SHA256>.authenticationCode(
          for: Data("atlasvault-enrollment-history-v1".utf8), using: SymmetricKey(data: storageKey))
      )
      // Verification uses a nonexistent path and performs no recipient publication.
      // Actual encrypted stores are opened only after all authentication and HPKE checks.
      let probe = try AtlasVaultAnchoredSyncState(
        fileURL: directory.appendingPathComponent("verify-\(UUID().uuidString)/history"),
        encryptionKey: historyKey, trust: trust)
      try probe.verifyAnchor(anchor)
      guard recipient.deviceID == checkpoint["recipient_device_id"] as? String,
        R.digest(recipient.agreementPublicKey) == checkpoint["recipient_agreement_sha256"]
          as? String,
        let text = p["opaque_b64"] as? String, text.count <= 1_398_104,
        let bytes = Data(base64Encoded: text), bytes.base64EncodedString() == text,
        bytes.count <= 1024 * 1024
      else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      let collection = try AtlasVaultSignedStateCommitment(jsonObject: map(p["collection"]))
      guard collection.collectionID == checkpoint["collection_id"] as? String,
        collection.sequence == (try E.integer(checkpoint["sequence"])),
        collection.root == checkpoint["collection_root"] as? String,
        collection.stateSHA256 == checkpoint["collection_sha256"] as? String,
        collection.stateSHA256 == R.digest(bytes), try collection.verify(publicKey: trustedSigner)
      else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      let body = try map(JSONSerialization.jsonObject(with: bytes))
      try R.exact(body, ["format", "version", "route", "records"])
      guard body["format"] as? String == "atlasvault-guarded-collection",
        try E.integer(body["version"]) == 1,
        ["patch", "snapshot", "compaction"].contains(body["route"] as? String ?? ""),
        let rows = body["records"] as? [[String: Any]], rows.count <= 256
      else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      let epoch = try E.integer(checkpoint["key_epoch"])
      var epochs: Set<Int64> = [Int64(epoch)]
      var ids = Set<String>()
      for raw in rows {
        let r = try AtlasVaultOpaqueCiphertextEnvelope(jsonObject: raw)
        guard r.version == 1, r.keyEpoch <= epoch, ids.insert(r.objectID).inserted else {
          throw AtlasVaultEnrollmentDeliveryError.rejected
        }
        epochs.insert(Int64(r.keyEpoch))
      }
      guard epochs.count <= 32 else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      if epochs.count > 1 {
        _ = try AtlasVaultHistoricalAuthority.verify(
          map(p["historical_authority"]), anchor: anchor, publicKey: trustedSigner, trust: trust,
          collection: map(p["collection"]), bytes: bytes)
      } else if !(p["historical_authority"] is NSNull) {
        throw AtlasVaultEnrollmentDeliveryError.rejected
      }
      guard let deliveries = p["deliveries"] as? [[String: Any]],
        try deliveries.map({ Int64(try E.integer($0["key_epoch"])) }) == epochs.sorted()
      else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      for d in deliveries {
        try R.exact(d, ["key_epoch", "encapsulated_key_b64", "ciphertext_b64"])
        let e = Int64(try E.integer(d["key_epoch"]))
        let opened = try AtlasVaultKeyEpochHPKE.open(
          recipientPrivateKey: agreementPrivateKey,
          sealed: AtlasVaultKeyEpochHPKESealedVaultKeyV2(
            keyEpoch: e, encapsulatedKey: R.bytes(d["encapsulated_key_b64"], 32),
            ciphertext: R.bytes(d["ciphertext_b64"], 48)), context: context(p), minimumKeyEpoch: e)
        keys[e] = opened.vaultKey
      }
      installLock.lock()
      defer { installLock.unlock() }
      try FileManager.default.createDirectory(
        at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
      let fd = Darwin.open(
        directory.appendingPathComponent("enrollment.lock").path, O_CREAT | O_RDWR,
        S_IRUSR | S_IWUSR)
      guard fd >= 0 else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      defer { Darwin.close(fd) }
      guard flock(fd, LOCK_EX) == 0 else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      let receipt = try EncryptedQueueFile(
        fileURL: directory.appendingPathComponent("enrollment-receipt"), encryptionKey: storageKey,
        kind: "enrollment-receipt-v1")
      let prior = try receipt.read(default: [:])
      if prior.isEmpty {
        guard
          !FileManager.default.fileExists(
            atPath: directory.appendingPathComponent("activation").path)
        else { throw AtlasVaultEnrollmentDeliveryError.rejected }
        try receipt.write(["root": digest])
      } else if try R.canonical(prior) != R.canonical(["root": digest]) {
        throw AtlasVaultEnrollmentDeliveryError.rejected
      }
      let history = try AtlasVaultAnchoredSyncState(
        fileURL: directory.appendingPathComponent("enrollment-history"), encryptionKey: historyKey,
        trust: trust)
      var args = anchor
      for k in ["registry", "collection", "opaque_b64"] { args[k] = p[k] }
      for k in ["current_context", "recipient_device_id", "confirmed_transcript"] {
        args[k] = pins[k]
      }
      _ = try history.bootstrap(args)
      if let authority = p["historical_authority"] as? [String: Any] {
        _ = try history.installHistoricalAuthority(
          authority, collection: map(p["collection"]), opaqueState: bytes)
      }
      guard let registry = p["registry"] as? [[String: Any]],
        let account = checkpoint["account_id"] as? String,
        let vault = checkpoint["vault_id"] as? String,
        let stateRoot = checkpoint["state_root"] as? String
      else { throw AtlasVaultEnrollmentDeliveryError.rejected }
      let owner = try AtlasVaultEpochVault(
        directory: directory, storageKey: storageKey, deviceID: recipient.deviceID,
        registry: registry,
        accountID: account, vaultID: vault, keyEpoch: epoch, stateRoot: stateRoot,
        historyOrigin: history.publicationOrigin())
      if FileManager.default.fileExists(atPath: owner.file.fileURL.path) {
        let state = try owner.load()
        try owner.active(state)
        guard try R.canonical(map(state["context"])) == R.canonical(owner.context) else {
          throw AtlasVaultEnrollmentDeliveryError.rejected
        }
      } else {
        try owner.initialize(keys: keys, history: history)
      }
      return owner
    } catch { throw AtlasVaultEnrollmentDeliveryError.rejected }
  }
}
