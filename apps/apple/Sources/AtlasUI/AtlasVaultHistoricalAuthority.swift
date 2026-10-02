import CryptoKit
import Foundation

enum AtlasVaultHistoricalAuthorityError: String, Error {
  case rejected = "ATLAS_HISTORICAL_AUTHORITY_REJECTED"
  case preimageRequired = "ATLAS_HISTORY_PREIMAGE_REQUIRED"
}

// The two-view reconstruction has no fallback to a historical public-key lookup.
enum AtlasVaultHistoricalAuthority {
  private typealias R = AtlasVaultEpochRotation
  private typealias E = AtlasVaultDeviceEnrollment
  private typealias D = AtlasVaultDeviceDelivery
  private static let bindings: Set<String> = [
    "account_id", "vault_id", "registry_root", "key_epoch", "registry_generation",
    "collection_sha256", "recipient_device_id", "transcript_sha256",
  ]
  private static func equal(_ a: Any?, _ b: Any?) throws -> Bool {
    guard let a, let b else { return false }
    return try R.canonical(["value": a]) == R.canonical(["value": b])
  }
  static func verify(
    _ proof: [String: Any], anchor: [String: Any], publicKey: Data, trust: [String: Any],
    collection: [String: Any], bytes: Data
  ) throws -> [String: Any] {
    do {
      try R.exact(
        proof,
        bindings.union([
          "format", "version", "anchor_root", "signed_descriptors", "prior_registry", "revocation",
          "views",
        ]))
      guard try R.canonical(proof).count <= 128 * 1024,
        proof["format"] as? String == "atlasvault-historical-authority",
        try E.integer(proof["version"]) == 1
      else { throw AtlasVaultHistoricalAuthorityError.rejected }
      let p = try D.map(anchor["checkpoint"])
      guard try equal(proof["anchor_root"], p["root"]),
        try equal(proof["anchor_root"], trust["anchor_root"]),
        try bindings.allSatisfy({ try equal(proof[$0], p[$0]) })
      else { throw AtlasVaultHistoricalAuthorityError.rejected }
      for k in ["key_epoch", "registry_generation"] {
        guard try E.integer(proof[k]) == E.integer(p[k]) else {
          throw AtlasVaultHistoricalAuthorityError.rejected
        }
      }
      let c = try AtlasVaultSignedStateCommitment(jsonObject: collection)
      guard bytes.count >= 16, bytes.count <= 1024 * 1024,
        c.collectionID == p["collection_id"] as? String,
        c.sequence == (try E.integer(p["sequence"])), c.root == p["collection_root"] as? String,
        c.stateSHA256 == p["collection_sha256"] as? String, c.stateSHA256 == R.digest(bytes),
        try c.verify(publicKey: publicKey)
      else { throw AtlasVaultHistoricalAuthorityError.rejected }
      let body = try D.map(JSONSerialization.jsonObject(with: bytes))
      try R.exact(body, ["format", "version", "route", "records"])
      let records = try D.rows(body["records"])
      guard body["format"] as? String == "atlasvault-guarded-collection",
        try E.integer(body["version"]) == 1,
        ["patch", "snapshot", "compaction"].contains(body["route"] as? String ?? ""),
        records.count <= 256
      else { throw AtlasVaultHistoricalAuthorityError.rejected }
      var ids = Set<String>()
      for raw in records {
        let r = try AtlasVaultOpaqueCiphertextEnvelope(jsonObject: raw)
        guard r.version == 1, r.keyEpoch <= (try E.integer(p["key_epoch"])),
          ids.insert(r.objectID).inserted
        else { throw AtlasVaultHistoricalAuthorityError.rejected }
      }
      let views = try D.rows(proof["views"])
      guard views.count == 2 else { throw AtlasVaultHistoricalAuthorityError.preimageRequired }
      let prior = try verifiedView(views[0], publicKey: publicKey)
      let current = try verifiedView(views[1], publicKey: publicKey)
      guard try equal(current, anchor["view"]) else {
        throw AtlasVaultHistoricalAuthorityError.rejected
      }
      let old = try D.rows(proof["prior_registry"])
      let removal = try D.map(proof["revocation"])
      let admission = try D.map(anchor["enrollment"])
      let after = try AtlasVaultRevocation.verify(removal, registry: old)
      let admitted = try E.verify(
        admission, registry: after, context: admission.filter { E.contextFields.contains($0.key) },
        confirmedTranscript: trust["confirmed_transcript"] as! String, status: "ACTIVE")
      guard try AtlasVaultRevocation.registryRoot(admitted) == p["registry_root"] as? String else {
        throw AtlasVaultHistoricalAuthorityError.rejected
      }
      let descriptors = try D.rows(proof["signed_descriptors"])
      guard !descriptors.isEmpty, descriptors.count <= 32 else {
        throw AtlasVaultHistoricalAuthorityError.rejected
      }
      var entries = [[String: Any]]()
      var reconstructed = [[String: Any]]()
      for raw in descriptors {
        let d = try AtlasVaultSignedDeviceDescriptor.decodeStrict(R.canonical(raw))
          .verifiedDescriptor()
        entries.append([
          "device_id": R.digest(Data(d.deviceID.utf8)),
          "descriptor_sha256": R.digest(try d.canonicalData()),
        ])
        reconstructed.append([
          "device_id": d.deviceID, "state": "ACTIVE",
          "signing_public_b64": d.signingPublicKey.base64EncodedString(),
          "agreement_public_b64": d.agreementPublicKey.base64EncodedString(),
        ])
      }
      guard
        try AtlasVaultAuthenticatedStateView.registryRoot(entries) == prior["registry_root"]
          as? String,
        try AtlasVaultRevocation.registryRoot(reconstructed)
          == AtlasVaultRevocation.registryRoot(old),
        let signer = old.first(where: {
          $0["device_id"] as? String == p["issuer_device_id"] as? String
            && $0["state"] as? String == "ACTIVE"
        }), try R.bytes(signer["signing_public_b64"], 32) == publicKey,
        try equal(removal["initiator_device_id"], p["issuer_device_id"])
      else { throw AtlasVaultHistoricalAuthorityError.rejected }
      guard
        try ["account_id", "vault_id"].allSatisfy({
          try equal(prior[$0], current[$0]) && equal(removal[$0], current[$0])
        }), try E.integer(prior["sequence"]) == 1, try E.integer(current["sequence"]) == 2,
        prior["previous_root"] as? String == String(repeating: "0", count: 64),
        prior["previous_registry_root"] as? String
          == R.digest(Data("atlasvault-registry-root-v1\n".utf8)),
        try equal(current["previous_root"], prior["root"]),
        try equal(current["previous_registry_root"], prior["registry_root"]),
        try E.integer(prior["key_epoch"]) == E.integer(removal["key_epoch"]),
        try E.integer(current["key_epoch"]) == E.integer(prior["key_epoch"]) + 1,
        try E.integer(removal["sequence"]) == 1, try equal(admission["state_root"], prior["root"]),
        try E.integer(admission["registry_generation"]) == E.integer(current["key_epoch"])
      else { throw AtlasVaultHistoricalAuthorityError.rejected }
      let reconstructedRoot = R.digest(
        Data(
          "atlasvault-state-commitment-v1\n\(c.collectionID)\n1\n\(String(repeating:"0",count:64))\n\(R.digest(bytes))\n"
            .utf8))
      guard reconstructedRoot == prior["collection_root"] as? String else {
        throw AtlasVaultHistoricalAuthorityError.preimageRequired
      }
      var result = [String: Any]()
      for raw in records {
        let r = try AtlasVaultOpaqueCiphertextEnvelope(jsonObject: raw)
        if r.keyEpoch >= (try E.integer(p["key_epoch"])) { continue }
        guard r.keyEpoch == (try E.integer(prior["key_epoch"])) else {
          throw AtlasVaultHistoricalAuthorityError.preimageRequired
        }
        guard let encoded = raw["aad_b64"] as? String, let aad = Data(base64Encoded: encoded),
          aad.base64EncodedString() == encoded
        else { throw AtlasVaultHistoricalAuthorityError.rejected }
        let m = try D.map(JSONSerialization.jsonObject(with: aad))
        try R.exact(
          m,
          [
            "format", "version", "account_id", "vault_id", "key_epoch", "device_id", "kind",
            "object_id", "revision",
          ])
        guard try R.canonical(m) == aad, m["format"] as? String == "atlasvault-epoch-ciphertext",
          try E.integer(m["version"]) == 1,
          ["patch", "snapshot"].contains(m["kind"] as? String ?? ""),
          try ["account_id", "vault_id", "key_epoch"].allSatisfy({ try equal(m[$0], prior[$0]) }),
          try ["object_id", "revision", "key_epoch"].allSatisfy({ try equal(m[$0], raw[$0]) }),
          let author = old.first(where: {
            $0["device_id"] as? String == m["device_id"] as? String
              && $0["state"] as? String == "ACTIVE"
          })
        else { throw AtlasVaultHistoricalAuthorityError.rejected }
        let message =
          Data("atlasvault-epoch-ciphertext-signature-v1\0".utf8) + aad
          + Data(base64Encoded: r.nonceBase64)! + Data(base64Encoded: r.ciphertextBase64)!
        guard
          try Curve25519.Signing.PublicKey(
            rawRepresentation: R.bytes(author["signing_public_b64"], 32)
          ).isValidSignature(R.bytes(raw["signature_b64"], 64), for: message)
        else { throw AtlasVaultHistoricalAuthorityError.rejected }
        result[r.objectID] = [
          "key_epoch": r.keyEpoch, "envelope_sha256": R.digest(try R.canonical(raw)),
          "author": author,
        ]
      }
      return result
    } catch AtlasVaultHistoricalAuthorityError.preimageRequired {
      throw AtlasVaultHistoricalAuthorityError.preimageRequired
    } catch { throw AtlasVaultHistoricalAuthorityError.rejected }
  }
}
