import CryptoKit
import Foundation

public enum AtlasVaultDeviceDeliveryError: String, Error {
  case perDeviceProofRequired = "ATLAS_PER_DEVICE_PROOF_REQUIRED"
}

/// D089 signs existing wrapper bytes without changing aggregate v1 or HPKE.
public enum AtlasVaultDeviceDelivery {
  typealias R = AtlasVaultEpochRotation
  static let suite = "0x0020/0x0001/0x0002"
  static let fields: Set<String> = [
    "format", "version", "activation_id", "plan", "revocation", "registry",
    "recipient_device_id", "recipient_agreement_sha256", "wrapper_sha256", "registry_generation",
    "issuer_device_id", "rotation_signer_device_id", "hpke_suite", "hpke_version",
    "signature_algorithm", "signature_version", "root", "signature_b64",
  ]
  static func unsigned(_ p: [String: Any]) -> [String: Any] {
    p.filter { !["root", "signature_b64"].contains($0.key) }
  }
  public static func canonicalHash(_ p: [String: Any]) throws -> String {
    R.digest(try R.canonical(unsigned(p)))
  }
  static func root(_ p: [String: Any]) throws -> String {
    R.digest(Data("atlasvault-device-delivery-proof-v2\n".utf8) + (try R.canonical(unsigned(p))))
  }
  static func message(_ root: String) -> Data {
    Data("atlasvault-device-delivery-signature-v2\0".utf8)
      + Data(stride(from: 0, to: 64, by: 2).map { UInt8(root.dropFirst($0).prefix(2), radix: 16)! })
  }
  static func map(_ x: Any?) throws -> [String: Any] {
    guard let x = x as? [String: Any] else { throw AtlasVaultRotationError.rejected }
    return x
  }
  static func rows(_ x: Any?) throws -> [[String: Any]] {
    guard let x = x as? [[String: Any]] else { throw AtlasVaultRotationError.rejected }
    return x
  }
  static func text(_ x: Any?) throws -> String {
    guard let x = x as? String else { throw AtlasVaultRotationError.rejected }
    return x
  }
  public static func create(
    _ record: [String: Any], recipientDeviceID: String, issuerDeviceID: String,
    signingKey: Curve25519.Signing.PrivateKey, currentRegistry: [[String: Any]],
    recoveryPending: Bool
  ) throws -> [String: Any] {
    do {
      try R.exact(record, ["format", "version", "status", "transition_id", "proof"])
      let old = try map(record["proof"])
      let plan = try map(old["plan"])
      guard !recoveryPending, record["format"] as? String == "atlasvault-activation-record",
        try R.integer(record["version"]) == 1,
        record["status"] as? String == "ACTIVATION_ACCEPTED",
        record["transition_id"] as? String == old["root"] as? String
      else { throw AtlasVaultRotationError.rejected }
      let result = try R.verify(
        old, registry: rows(old["registry"]), accountID: text(plan["account_id"]),
        vaultID: text(plan["vault_id"]), previousEpoch: R.integer(plan["previous_epoch"]),
        stateRoot: text(plan["state_root"]))
      _ = try AtlasVaultRevocation.registryRoot(currentRegistry)
      for id in [issuerDeviceID, recipientDeviceID] {
        guard
          let historic = try rows(result["registry"]).first(where: {
            $0["device_id"] as? String == id && $0["state"] as? String == "ACTIVE"
          }),
          let current = currentRegistry.first(where: {
            $0["device_id"] as? String == id && $0["state"] as? String == "ACTIVE"
          }),
          try R.canonical(historic) == R.canonical(current)
        else { throw AtlasVaultRotationError.rejected }
      }
      guard
        let wrapper = try rows(old["deliveries"]).first(where: {
          $0["device_id"] as? String == recipientDeviceID
        }),
        let recipient = try rows(result["registry"]).first(where: {
          $0["device_id"] as? String == recipientDeviceID
        })
      else { throw AtlasVaultRotationError.rejected }
      var p: [String: Any] = [
        "format": "atlasvault-device-delivery-proof", "version": 2, "activation_id": old["root"]!,
        "plan": plan, "revocation": old["revocation"]!, "registry": old["registry"]!,
        "recipient_device_id": recipientDeviceID,
        "recipient_agreement_sha256": R.digest(try R.bytes(recipient["agreement_public_b64"], 32)),
        "wrapper_sha256": R.digest(try R.canonical(wrapper)),
        "registry_generation": plan["new_epoch"]!, "issuer_device_id": issuerDeviceID,
        "rotation_signer_device_id": old["rotation_signer_device_id"]!,
        "hpke_suite": suite, "hpke_version": 2, "signature_algorithm": "Ed25519",
        "signature_version": 1,
      ]
      p["root"] = try root(p)
      p["signature_b64"] = try signingKey.signature(for: message(p["root"] as! String))
        .base64EncodedString()
      let packet: [String: Any] = ["proof": p, "wrapper": wrapper]
      _ = try verify(
        packet, registry: rows(old["registry"]), accountID: text(plan["account_id"]),
        vaultID: text(plan["vault_id"]),
        previousEpoch: R.integer(plan["previous_epoch"]), stateRoot: text(plan["state_root"]),
        activationID: text(old["root"]), recipientDeviceID: recipientDeviceID)
      return packet
    } catch { throw AtlasVaultRotationError.rejected }
  }
  public static func verify(
    _ packet: [String: Any], registry: [[String: Any]], accountID: String, vaultID: String,
    previousEpoch: Int, stateRoot: String, activationID: String, recipientDeviceID: String
  ) throws -> [String: Any] {
    if ["atlasvault-activation-record", "atlasvault-epoch-rotation"].contains(
      packet["format"] as? String ?? "")
    {
      throw AtlasVaultDeviceDeliveryError.perDeviceProofRequired
    }
    do {
      try R.exact(packet, ["proof", "wrapper"])
      let p = try map(packet["proof"])
      let w = try map(packet["wrapper"])
      try R.exact(p, fields)
      guard p["format"] as? String == "atlasvault-device-delivery-proof",
        try R.integer(p["version"]) == 2,
        p["hpke_suite"] as? String == suite, try R.integer(p["hpke_version"]) == 2,
        p["signature_algorithm"] as? String == "Ed25519",
        try R.integer(p["signature_version"]) == 1,
        activationID.utf8.count == 64,
        activationID.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
        p["activation_id"] as? String == activationID,
        p["recipient_device_id"] as? String == recipientDeviceID,
        try AtlasVaultRevocation.registryRoot(rows(p["registry"]))
          == AtlasVaultRevocation.registryRoot(registry)
      else { throw AtlasVaultRotationError.rejected }
      let t = try map(p["revocation"])
      let plan = try map(p["plan"])
      let after = try AtlasVaultRevocation.verify(t, registry: registry)
      guard t["account_id"] as? String == accountID, t["vault_id"] as? String == vaultID,
        try R.integer(t["key_epoch"]) == previousEpoch, try R.integer(t["sequence"]) == 1
      else { throw AtlasVaultRotationError.rejected }
      try AtlasVaultRevocation.validateRotationPlan(
        plan, transition: t, registry: after, stateRoot: stateRoot)
      guard try R.integer(p["registry_generation"]) == R.integer(plan["new_epoch"]),
        after.contains(where: {
          $0["device_id"] as? String == p["rotation_signer_device_id"] as? String
            && $0["state"] as? String == "ACTIVE"
        }),
        let issuer = after.first(where: {
          $0["device_id"] as? String == p["issuer_device_id"] as? String
            && $0["state"] as? String == "ACTIVE"
        }),
        let recipient = after.first(where: {
          $0["device_id"] as? String == recipientDeviceID && $0["state"] as? String == "ACTIVE"
        })
      else { throw AtlasVaultRotationError.rejected }
      try R.exact(w, ["device_id", "key_epoch", "encapsulated_key_b64", "ciphertext_b64"])
      guard w["device_id"] as? String == recipientDeviceID,
        try R.integer(w["key_epoch"]) == R.integer(plan["new_epoch"])
      else { throw AtlasVaultRotationError.rejected }
      _ = try R.bytes(w["encapsulated_key_b64"], 32)
      _ = try R.bytes(w["ciphertext_b64"], 48)
      let digest = try root(p)
      guard p["wrapper_sha256"] as? String == R.digest(try R.canonical(w)),
        p["recipient_agreement_sha256"] as? String
          == R.digest(try R.bytes(recipient["agreement_public_b64"], 32)),
        p["root"] as? String == digest,
        try Curve25519.Signing.PublicKey(
          rawRepresentation: R.bytes(issuer["signing_public_b64"], 32)
        ).isValidSignature(R.bytes(p["signature_b64"], 64), for: message(digest))
      else { throw AtlasVaultRotationError.rejected }
      return [
        "new_epoch": plan["new_epoch"]!, "registry": after, "recipients": plan["recipients"]!,
        "recipient_commitment": R.digest(
          Data("atlasvault-active-recipients-v1\n".utf8)
            + (try R.canonical(["recipients": plan["recipients"]!]))),
      ]
    } catch { throw AtlasVaultRotationError.rejected }
  }
}
