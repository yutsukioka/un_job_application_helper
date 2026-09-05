import Foundation

extension AtlasVaultEpochVault {
  func enrollmentContext(_ s: [String: Any]) throws -> [String: Any] {
    let records = try EpochCatchUp.records(map(map(s["components"])["history"]))
    var activationID: String?
    var generation = try R.integer(s["epoch"])
    for raw in records {
      if raw["format"] as? String == "atlasvault-enrollment-bridge" {
        generation = try R.integer(map(raw["enrollment"])["next_registry_generation"])
      } else {
        let p = try raw["wrapper"] == nil ? raw : map(raw["proof"])
        activationID = (raw["wrapper"] == nil ? p["root"] : p["activation_id"]) as? String
        generation = try R.integer(map(p["plan"])["new_epoch"])
      }
    }
    guard let activationID else { throw AtlasVaultRotationError.rejected }
    return [
      "account_id": context["account_id"]!, "vault_id": context["vault_id"]!,
      "key_epoch": s["epoch"]!, "registry_generation": generation,
      "state_root": try stateRoot(s), "activation_id": activationID,
    ]
  }

  public func enrollmentContext() throws -> [String: Any] {
    try run {
      let s = try load()
      try active(s)
      return try enrollmentContext(s)
    }
  }

  public func enrollmentRegistry() throws -> [[String: Any]] {
    try run {
      let s = try load()
      try active(s)
      return try rows(s["registry"])
    }
  }

  public func acceptEnrollment(_ proof: [String: Any], confirmedTranscript: String) throws -> Bool {
    try run {
      var s = try load()
      try active(s)
      let records = try EpochCatchUp.records(map(map(s["components"])["history"]))
      for raw in records where raw["format"] as? String == "atlasvault-enrollment-bridge" {
        let prior = try map(raw["enrollment"])
        if prior["root"] as? String == proof["root"] as? String {
          guard try R.canonical(prior) == R.canonical(proof),
            prior["transcript_sha256"] as? String == confirmedTranscript
          else { throw AtlasVaultEnrollmentError.rejected }
          return false
        }
      }
      let after = try AtlasVaultDeviceEnrollment.verify(
        proof, registry: rows(s["registry"]),
        context: enrollmentContext(s), confirmedTranscript: confirmedTranscript,
        status: s["status"] as! String)
      var components = try map(s["components"])
      components["history"] = try history(s).stageEpoch([
        "format": "atlasvault-enrollment-bridge", "version": 1, "enrollment": proof,
      ])
      s["components"] = components
      s["registry"] = after
      s["recipients"] = after.filter { $0["state"] as? String == "ACTIVE" }.map {
        $0["device_id"] as! String
      }.sorted()
      s["generation"] = try R.integer(s["generation"]) + 1
      try file.write(s)
      return true
    }
  }
}
