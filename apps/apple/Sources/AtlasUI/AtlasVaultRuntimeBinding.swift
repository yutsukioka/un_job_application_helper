import CryptoKit
import Foundation
import Security

public enum AtlasVaultRuntimeBindingError: Error, Sendable, CustomStringConvertible {
  case unavailable
  case invalid
  public var description: String { "AtlasVault runtime binding unavailable." }
}

/// A local trust pin, persisted by authenticated enrollment, never synthesized during unlock.
public struct AtlasVaultRuntimeBinding: Sendable, CustomStringConvertible,
  CustomDebugStringConvertible
{
  private let encoded: Data
  private let storageKey: Data
  let identity: AtlasVaultDeviceIdentity

  init(encoded: Data, identity: AtlasVaultDeviceIdentity, vaultID: String, storageKey: Data) throws
  {
    guard storageKey.count == 32, encoded.count <= 256 * 1024,
      let value = try JSONSerialization.jsonObject(with: encoded) as? [String: Any]
    else {
      throw AtlasVaultRuntimeBindingError.invalid
    }
    typealias R = AtlasVaultEpochRotation
    try R.exact(
      value,
      Set(
        ["format", "version", "context", "registry", "history_context", "storage_key_ref"]
          + (value["history_registry"] == nil ? [] : ["history_registry"])
          + (value["history_origin"] == nil ? [] : ["history_origin"])))
    if let original = value["history_registry"] {
      _ = try AtlasVaultAuthenticatedStateView.registryRoot(AtlasVaultDeviceDelivery.rows(original))
    }
    let c = try AtlasVaultDeviceDelivery.map(value["context"])
    try R.exact(
      c,
      Set(
        ["account_id", "vault_id", "device_id", "key_epoch", "state_root", "registry_root"]
          + (value["history_origin"] == nil ? [] : ["history_origin_sha256"])))
    if let origin = value["history_origin"] {
      guard value["history_registry"] == nil,
        try R.digest(R.canonical(AtlasVaultDeviceDelivery.map(origin))) == c[
          "history_origin_sha256"] as? String
      else { throw AtlasVaultRuntimeBindingError.invalid }
    }
    let registry = try AtlasVaultDeviceDelivery.rows(value["registry"])
    let h = try AtlasVaultDeviceDelivery.map(value["history_context"])
    try R.exact(h, ["account_id", "vault_id", "collection_id", "key_epoch", "signing_public_b64"])
    guard value["format"] as? String == "atlasvault-runtime-context",
      try R.integer(value["version"]) == 1,
      value["storage_key_ref"] as? String == vaultID,
      c["vault_id"] as? String == vaultID, c["device_id"] as? String == identity.deviceID,
      h["account_id"] as? String == c["account_id"] as? String,
      h["vault_id"] as? String == vaultID,
      try viewInteger(h["key_epoch"]) == viewInteger(c["key_epoch"]),
      c["registry_root"] as? String == (try AtlasVaultRevocation.registryRoot(registry)),
      let member = registry.first(where: { $0["device_id"] as? String == identity.deviceID }),
      try R.bytes(member["signing_public_b64"], 32) == identity.signingPublicKey,
      try R.bytes(member["agreement_public_b64"], 32) == identity.agreementPublicKey
    else { throw AtlasVaultRuntimeBindingError.invalid }
    _ = try viewIdentifier(c["account_id"])
    _ = try viewIdentifier(h["collection_id"])
    _ = try R.bytes(h["signing_public_b64"], 32)
    self.encoded = encoded
    self.storageKey = storageKey
    self.identity = identity
  }

  func open(directory: URL, session: AtlasVaultUnlockedSession) throws -> AtlasVaultEpochVault {
    let value = try AtlasVaultDeviceDelivery.map(JSONSerialization.jsonObject(with: encoded))
    let c = try AtlasVaultDeviceDelivery.map(value["context"])
    guard session.vaultID == c["vault_id"] as? String else {
      throw AtlasVaultRuntimeBindingError.invalid
    }
    let epoch = try session.withVaultKey { _ in
      try AtlasVaultEpochVault(
        directory: directory, storageKey: storageKey, deviceID: identity.deviceID,
        registry: AtlasVaultDeviceDelivery.rows(value["registry"]),
        accountID: c["account_id"] as! String, vaultID: session.vaultID,
        keyEpoch: AtlasVaultEpochRotation.integer(c["key_epoch"]),
        stateRoot: c["state_root"] as? String ?? "",
        authenticatedHistoryRegistry: value["history_registry"].map {
          try AtlasVaultDeviceDelivery.rows($0)
        }, historyOrigin: value["history_origin"].map { try AtlasVaultDeviceDelivery.map($0) })
    }
    let s = try epoch.load()
    let history = try epoch.history(s).load()
    guard
      try AtlasVaultEpochRotation.canonical(epoch.map(history["context"]))
        == AtlasVaultEpochRotation.canonical(epoch.map(value["history_context"])),
      try epoch.rows(history["views"]).contains(where: {
        $0["root"] as? String == c["state_root"] as? String
      })
    else { throw AtlasVaultRuntimeBindingError.invalid }
    if let original = value["history_registry"] {
      let root = try AtlasVaultAuthenticatedStateView.registryRoot(epoch.rows(original))
      guard
        try epoch.rows(history["views"]).contains(where: { $0["registry_root"] as? String == root })
      else {
        throw AtlasVaultRuntimeBindingError.invalid
      }
    }
    return epoch
  }

  func signingKey() throws -> Curve25519.Signing.PrivateKey {
    let secret = try AtlasVaultDeviceDelivery.map(
      JSONSerialization.jsonObject(with: identity.secretBundle().canonicalData()))
    return try Curve25519.Signing.PrivateKey(
      rawRepresentation:
        AtlasVaultEpochRotation.bytes(secret["signing_private_key"], 32))
  }

  public var description: String { "AtlasVaultRuntimeBinding(<redacted>)" }
  public var debugDescription: String { description }
}

public struct AtlasKeychainRuntimeBindingStore<Client: AtlasKeychainClient>: Sendable {
  public static var service: String { "com.atlasvault.runtime-context.v1" }
  public static var storageKeyService: String { "com.atlasvault.runtime-storage-key.v1" }
  private let client: Client
  public init(client: Client) { self.client = client }

  public func load(for vaultID: String) throws -> AtlasVaultRuntimeBinding? {
    let id = try AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID)
    let result = client.copyMatching(.init(service: Self.service, account: id))
    if result.status == errSecItemNotFound { return nil }
    let storage = client.copyMatching(.init(service: Self.storageKeyService, account: id))
    guard result.status == errSecSuccess, let data = result.valueData,
      storage.status == errSecSuccess, let storageKey = storage.valueData,
      let secret = try AtlasKeychainDeviceIdentityStore(client: client).loadPrimaryIdentity()
    else {
      throw AtlasVaultRuntimeBindingError.unavailable
    }
    do {
      return try AtlasVaultRuntimeBinding(
        encoded: data,
        identity: AtlasVaultDeviceIdentitySecret.decodeStrict(secret).loadIdentity(), vaultID: id,
        storageKey: storageKey)
    } catch { throw AtlasVaultRuntimeBindingError.invalid }
  }

  /// Call only at the authenticated enrollment boundary with its already trusted P7 owner.
  /// Existing pins are not overwritten, and unlock never calls this method.
  public func createAuthenticatedBinding(
    from epoch: AtlasVaultEpochVault, authenticatedHistoryRegistry: [[String: Any]]? = nil
  ) throws {
    let value = try epoch.run { () throws -> [String: Any] in
      let s = try epoch.load()
      try epoch.active(s)
      let history = try epoch.history(s).load()
      var value: [String: Any] = [
        "format": "atlasvault-runtime-context", "version": 1, "context": epoch.context,
        "registry": epoch.registry, "history_context": history["context"]!,
        "storage_key_ref": epoch.context["vault_id"]!,
      ]
      if let origin = epoch.historyOrigin {
        guard authenticatedHistoryRegistry == nil else {
          throw AtlasVaultRuntimeBindingError.invalid
        }
        value["history_origin"] = origin
      } else if try EpochCatchUp.records(history).isEmpty {
        guard let authenticatedHistoryRegistry,
          try AtlasVaultAuthenticatedStateView.registryRoot(authenticatedHistoryRegistry)
            == epoch.rows(history["views"]).last?["registry_root"] as? String
        else {
          throw AtlasVaultRuntimeBindingError.invalid
        }
        value["history_registry"] = authenticatedHistoryRegistry
      } else if authenticatedHistoryRegistry != nil {
        throw AtlasVaultRuntimeBindingError.invalid
      }
      return value
    }
    let vaultID = epoch.context["vault_id"] as! String
    guard let secret = try AtlasKeychainDeviceIdentityStore(client: client).loadPrimaryIdentity()
    else {
      throw AtlasVaultRuntimeBindingError.unavailable
    }
    let data = try AtlasVaultEpochRotation.canonical(value)
    _ = try AtlasVaultRuntimeBinding(
      encoded: data,
      identity: AtlasVaultDeviceIdentitySecret.decodeStrict(secret).loadIdentity(),
      vaultID: vaultID, storageKey: epoch.key)
    guard
      client.copyMatching(.init(service: Self.service, account: vaultID)).status
        == errSecItemNotFound,
      client.add(
        .init(
          service: Self.storageKeyService, account: vaultID, valueData: epoch.key,
          accessibility: .afterFirstUnlockThisDeviceOnly)) == errSecSuccess
    else {
      throw AtlasVaultRuntimeBindingError.unavailable
    }
    guard
      client.add(
        .init(
          service: Self.service, account: vaultID, valueData: data,
          accessibility: .afterFirstUnlockThisDeviceOnly)) == errSecSuccess
    else {
      _ = client.delete(.init(service: Self.storageKeyService, account: vaultID))
      throw AtlasVaultRuntimeBindingError.unavailable
    }
  }
}
