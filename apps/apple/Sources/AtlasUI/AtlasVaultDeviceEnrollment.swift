import CryptoKit
import Foundation

public enum AtlasVaultEnrollmentError: String, Error {
  case rejected = "ATLAS_ENROLLMENT_REJECTED"
}

/// D099 only: current context and confirmed transcript are local trust inputs.
public enum AtlasVaultDeviceEnrollment {
  typealias R = AtlasVaultEpochRotation
  static let contextFields: Set<String> = [
    "account_id", "vault_id", "registry_generation", "key_epoch", "state_root", "activation_id",
  ]
  static let fields = contextFields.union([
    "format", "version", "next_registry_generation",
    "prior_registry_root", "resulting_registry_root", "target_device_id",
    "target_signing_public_b64",
    "target_agreement_public_b64", "target_agreement_sha256", "transcript_sha256",
    "issuer_device_id", "authorization_category", "signature_algorithm",
  ])
  static func unsigned(_ p: [String: Any]) -> [String: Any] {
    p.filter { !["root", "signature_b64"].contains($0.key) }
  }
  public static func canonicalHash(_ p: [String: Any]) throws -> String {
    R.digest(try R.canonical(unsigned(p)))
  }
  static func root(_ p: [String: Any]) throws -> String {
    R.digest(Data("atlasvault-device-enrollment-v1\n".utf8) + (try R.canonical(unsigned(p))))
  }
  static func message(_ root: String) -> Data {
    Data("atlasvault-device-enrollment-signature-v1\0".utf8)
      + Data(stride(from: 0, to: 64, by: 2).map { UInt8(root.dropFirst($0).prefix(2), radix: 16)! })
  }
  static func hex(_ x: Any?) throws -> String {
    guard let x = x as? String, x.utf8.count == 64,
      x.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) })
    else { throw AtlasVaultEnrollmentError.rejected }
    return x
  }
  static func integer(_ value: Any?) throws -> Int {
    guard let number = value as? NSNumber,
      !["d", "f"].contains(String(cString: number.objCType))
    else { throw AtlasVaultEnrollmentError.rejected }
    return try R.integer(number)
  }
  public static func verify(
    _ p: [String: Any], registry: [[String: Any]], context: [String: Any],
    confirmedTranscript: String, status: String
  ) throws -> [[String: Any]] {
    do {
      try R.exact(p, fields.union(["root", "signature_b64"]))
      try R.exact(context, contextFields)
      guard status == "ACTIVE", try integer(p["version"]) == 1,
        p["format"] as? String == "atlasvault-device-enrollment",
        p["authorization_category"] as? String == "SAS_CONFIRMED",
        p["signature_algorithm"] as? String == "Ed25519"
      else { throw AtlasVaultEnrollmentError.rejected }
      for name in contextFields {
        if ["key_epoch", "registry_generation"].contains(name) {
          let n = try integer(context[name])
          guard n > 0, n < 9_007_199_254_740_991, try integer(p[name]) == n
          else { throw AtlasVaultEnrollmentError.rejected }
        } else {
          if ["state_root", "activation_id"].contains(name) {
            _ = try hex(context[name])
          } else {
            guard let s = context[name] as? String,
              s.range(of: "^[A-Za-z0-9_.~-]{1,128}$", options: .regularExpression) != nil
            else { throw AtlasVaultEnrollmentError.rejected }
          }
          guard p[name] as? String == context[name] as? String
          else { throw AtlasVaultEnrollmentError.rejected }
        }
      }
      let next = try integer(p["next_registry_generation"])
      guard next < 9_007_199_254_740_991,
        next == (try integer(context["registry_generation"])) + 1,
        try hex(confirmedTranscript) != String(repeating: "0", count: 64),
        p["transcript_sha256"] as? String == confirmedTranscript,
        try AtlasVaultRevocation.registryRoot(registry) == p["prior_registry_root"] as? String
      else { throw AtlasVaultEnrollmentError.rejected }
      let target: [String: Any] = [
        "device_id": p["target_device_id"]!,
        "signing_public_b64": p["target_signing_public_b64"]!,
        "agreement_public_b64": p["target_agreement_public_b64"]!, "state": "ACTIVE",
      ]
      guard
        !registry.contains(where: { $0["device_id"] as? String == target["device_id"] as? String })
      else { throw AtlasVaultEnrollmentError.rejected }
      let after = (registry + [target]).sorted {
        ($0["device_id"] as? String ?? "") < ($1["device_id"] as? String ?? "")
      }
      guard try AtlasVaultRevocation.registryRoot(after) == p["resulting_registry_root"] as? String,
        R.digest(try R.bytes(target["agreement_public_b64"], 32)) == p["target_agreement_sha256"]
          as? String,
        let signer = registry.first(where: {
          $0["device_id"] as? String == p["issuer_device_id"] as? String
            && $0["state"] as? String == "ACTIVE"
        })
      else { throw AtlasVaultEnrollmentError.rejected }
      let digest = try root(p)
      guard p["root"] as? String == digest,
        try Curve25519.Signing.PublicKey(
          rawRepresentation: R.bytes(signer["signing_public_b64"], 32)
        )
        .isValidSignature(R.bytes(p["signature_b64"], 64), for: message(digest))
      else { throw AtlasVaultEnrollmentError.rejected }
      return after
    } catch { throw AtlasVaultEnrollmentError.rejected }
  }
  public static func create(
    _ unsigned: [String: Any], registry: [[String: Any]], context: [String: Any],
    confirmedTranscript: String, status: String, signingKey: Curve25519.Signing.PrivateKey
  ) throws -> [String: Any] {
    do {
      try R.exact(unsigned, fields)
      guard status == "ACTIVE", try hex(confirmedTranscript) != String(repeating: "0", count: 64),
        unsigned["transcript_sha256"] as? String == confirmedTranscript
      else { throw AtlasVaultEnrollmentError.rejected }
      var p = unsigned
      let digest = try root(p)
      p["root"] = digest
      p["signature_b64"] = try signingKey.signature(for: message(digest)).base64EncodedString()
      _ = try verify(
        p, registry: registry, context: context, confirmedTranscript: confirmedTranscript,
        status: status)
      return p
    } catch { throw AtlasVaultEnrollmentError.rejected }
  }
}
