import CoreFoundation
import CryptoKit
import Foundation

public enum AtlasVaultRotationError: String, Error {
  case rejected = "ATLAS_EPOCH_ROTATION_REJECTED"
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
