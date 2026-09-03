import CoreFoundation
import CryptoKit
import Foundation

public enum AtlasVaultRotationError: String, Error {
  case rejected = "ATLAS_EPOCH_ROTATION_REJECTED"
  case pending = "ATLAS_ACTIVATION_PENDING"
  case revoked = "ATLAS_DEVICE_REVOKED"
  case recovery = "ATLAS_RECOVERY_PENDING"
  case conflict = "ATLAS_EPOCH_CONFLICT"
  case write = "ATLAS_EPOCH_WRITE_REJECTED"
  case catchUpPending = "ATLAS_CATCH_UP_PENDING"
  case cleanupPending = "ATLAS_CLEANUP_PENDING"
  case publicationRecovery = "ATLAS_PUBLICATION_RECOVERY_REQUIRED"
  case perDeviceProofRequired = "ATLAS_PER_DEVICE_PROOF_REQUIRED"
}

public enum AtlasVaultEpochRotation {
  static func canonical(_ value: [String: Any]) throws -> Data {
    try JSONSerialization.data(
      withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes])
  }
  static func digest(_ value: Data) -> String {
    SHA256.hash(data: value).map { String(format: "%02x", $0) }.joined()
  }
  static func exact(_ value: [String: Any], _ fields: Set<String>) throws {
    guard Set(value.keys) == fields else { throw AtlasVaultRotationError.rejected }
  }
  static func bytes(_ value: Any?, _ size: Int) throws -> Data {
    guard let value = value as? String, value.count == 4 * ((size + 2) / 3),
      let data = Data(base64Encoded: value), data.count == size,
      data.base64EncodedString() == value
    else { throw AtlasVaultRotationError.rejected }
    return data
  }
  static func integer(_ value: Any?) throws -> Int {
    guard let value = value as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
      value.doubleValue == Double(value.intValue)
    else { throw AtlasVaultRotationError.rejected }
    return value.intValue
  }
  public static func binding(_ plan: [String: Any]) throws -> String {
    digest(Data("atlasvault-rotation-binding-v1\n".utf8) + (try canonical(plan)))
  }
  public static func create(
    _ transition: [String: Any], registry: [[String: Any]], stateRoot: String,
    signingKey: Curve25519.Signing.PrivateKey
  ) throws -> [String: Any] {
    do {
      let after = try AtlasVaultRevocation.verify(transition, registry: registry)
      let recipients = after.filter { $0["state"] as? String == "ACTIVE" }.map {
        $0["device_id"] as! String
      }.sorted()
      let previous = try integer(transition["key_epoch"])
      guard previous < Int.max else { throw AtlasVaultRotationError.rejected }
      let plan: [String: Any] = [
        "format": "atlasvault-rotation-plan", "version": 1, "account_id": transition["account_id"]!,
        "vault_id": transition["vault_id"]!,
        "previous_epoch": previous, "new_epoch": previous + 1,
        "prior_registry_root": transition["prior_registry_root"]!,
        "resulting_registry_root": transition["resulting_registry_root"]!,
        "state_root": stateRoot, "initiator_device_id": transition["initiator_device_id"]!,
        "revocation_root": transition["root"]!, "recipients": recipients,
      ]
      try AtlasVaultRevocation.validateRotationPlan(
        plan, transition: transition, registry: after, stateRoot: stateRoot)
      let material = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
      let ring = try AtlasVaultKeyEpochRing.fromEntries(
        currentKeyEpoch: Int64(previous + 1), keys: [Int64(previous + 1): material])
      var deliveries = [[String: Any]]()
      for device in recipients {
        let entry = after.first { $0["device_id"] as? String == device }!
        let sealed = try ring.sealCurrentHPKEV2(
          recipientPublicKey: bytes(entry["agreement_public_b64"], 32),
          context: Data("atlasvault-rotation-delivery-v1:\(try binding(plan)):\(device)".utf8))
        deliveries.append([
          "device_id": device, "key_epoch": sealed.keyEpoch,
          "encapsulated_key_b64": sealed.encapsulatedKey.base64EncodedString(),
          "ciphertext_b64": sealed.ciphertext.base64EncodedString(),
        ])
      }
      var proof: [String: Any] = [
        "format": "atlasvault-epoch-rotation", "version": 1, "plan": plan, "revocation": transition,
        "registry": registry, "rotation_signer_device_id": transition["initiator_device_id"]!,
        "deliveries": deliveries,
      ]
      let root = digest(Data("atlasvault-epoch-rotation-v1\n".utf8) + (try canonical(proof)))
      let rootBytes = Data(
        stride(from: 0, to: 64, by: 2).map { UInt8(root.dropFirst($0).prefix(2), radix: 16)! })
      proof["root"] = root
      proof["signature_b64"] = try signingKey.signature(
        for: Data("atlasvault-epoch-rotation-signature-v1\0".utf8) + rootBytes
      ).base64EncodedString()
      _ = try verify(
        proof, registry: registry, accountID: plan["account_id"] as! String,
        vaultID: plan["vault_id"] as! String, previousEpoch: previous, stateRoot: stateRoot)
      return proof
    } catch { throw AtlasVaultRotationError.rejected }
  }
  public static func verify(
    _ proof: [String: Any], registry: [[String: Any]], accountID: String,
    vaultID: String, previousEpoch: Int, stateRoot: String
  ) throws -> [String: Any] {
    do {
      try exact(
        proof,
        [
          "format", "version", "plan", "revocation", "registry", "rotation_signer_device_id",
          "deliveries", "root", "signature_b64",
        ])
      guard proof["format"] as? String == "atlasvault-epoch-rotation",
        try integer(proof["version"]) == 1,
        let supplied = proof["registry"] as? [[String: Any]],
        try AtlasVaultRevocation.registryRoot(supplied)
          == AtlasVaultRevocation.registryRoot(registry),
        let revocation = proof["revocation"] as? [String: Any],
        let plan = proof["plan"] as? [String: Any]
      else { throw AtlasVaultRotationError.rejected }
      let after = try AtlasVaultRevocation.verify(revocation, registry: registry)
      guard revocation["account_id"] as? String == accountID,
        revocation["vault_id"] as? String == vaultID,
        try integer(revocation["key_epoch"]) == previousEpoch,
        try integer(revocation["sequence"]) == 1
      else { throw AtlasVaultRotationError.rejected }
      try AtlasVaultRevocation.validateRotationPlan(
        plan, transition: revocation, registry: after, stateRoot: stateRoot)
      guard let recipients = plan["recipients"] as? [String],
        let signerID = proof["rotation_signer_device_id"] as? String,
        recipients.contains(signerID), let deliveries = proof["deliveries"] as? [[String: Any]],
        deliveries.count == recipients.count
      else { throw AtlasVaultRotationError.rejected }
      for (device, delivery) in zip(recipients, deliveries) {
        try exact(delivery, ["device_id", "key_epoch", "encapsulated_key_b64", "ciphertext_b64"])
        guard delivery["device_id"] as? String == device,
          try integer(delivery["key_epoch"]) == integer(plan["new_epoch"])
        else { throw AtlasVaultRotationError.rejected }
        _ = try bytes(delivery["encapsulated_key_b64"], 32)
        _ = try bytes(delivery["ciphertext_b64"], 48)
      }
      var unsigned = proof
      unsigned.removeValue(forKey: "root")
      unsigned.removeValue(forKey: "signature_b64")
      let root = digest(Data("atlasvault-epoch-rotation-v1\n".utf8) + (try canonical(unsigned)))
      guard proof["root"] as? String == root,
        let signer = after.first(where: { $0["device_id"] as? String == signerID })
      else { throw AtlasVaultRotationError.rejected }
      let rootBytes = Data(
        stride(from: 0, to: 64, by: 2).map { i in UInt8(root.dropFirst(i).prefix(2), radix: 16)! })
      guard
        try Curve25519.Signing.PublicKey(rawRepresentation: bytes(signer["signing_public_b64"], 32))
          .isValidSignature(
            bytes(proof["signature_b64"], 64),
            for: Data("atlasvault-epoch-rotation-signature-v1\0".utf8) + rootBytes)
      else { throw AtlasVaultRotationError.rejected }
      return [
        "new_epoch": try integer(plan["new_epoch"]), "recipients": recipients,
        "binding_root": try binding(plan), "registry": after,
      ]
    } catch { throw AtlasVaultRotationError.rejected }
  }
}
