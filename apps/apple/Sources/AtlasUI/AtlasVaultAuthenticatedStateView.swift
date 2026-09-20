import CoreFoundation
import CryptoKit
import Foundation

public enum AtlasVaultStateViewError: String, Error {
  case rejected = "ATLAS_STATE_VIEW_REJECTED"
  case registrySubstitution = "ATLAS_REGISTRY_SUBSTITUTION"
  case equivocation = "ATLAS_STATE_EQUIVOCATION"
  case rollback = "ATLAS_ROLLBACK_REJECTED"
  case checkpointRequired = "ATLAS_CHECKPOINT_REQUIRED"
  case historyLimit = "ATLAS_HISTORY_LIMIT"
}

private let viewFields = [
  "account_id", "vault_id", "sequence", "previous_root", "collection_root",
  "registry_root", "previous_registry_root", "key_epoch",
]
private let viewFormat = "atlasvault-authenticated-state-view"
private let zeroViewRoot = String(repeating: "0", count: 64)
private let emptyRegistryRoot = viewDigest(Data("atlasvault-registry-root-v1\n".utf8))
private let viewLimit = 256

private func viewBoundary<T>(_ body: () throws -> T) throws -> T {
  do { return try body() } catch let error as AtlasVaultStateViewError { throw error } catch {
    throw AtlasVaultStateViewError.rejected
  }
}

func viewDigest(_ bytes: Data) -> String {
  SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
}

private func viewHex(_ raw: Any?) throws -> String {
  guard let value = raw as? String, value.utf8.count == 64,
    value.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) })
  else { throw AtlasVaultStateViewError.rejected }
  return value
}

func viewIdentifier(_ raw: Any?) throws -> String {
  guard let value = raw as? String, (1...128).contains(value.utf8.count),
    value.utf8.allSatisfy({
      (48...57).contains($0) || (65...90).contains($0)
        || (97...122).contains($0) || [45, 46, 95, 126].contains($0)
    })
  else { throw AtlasVaultStateViewError.rejected }
  return value
}

func viewInteger(_ raw: Any?) throws -> Int64 {
  guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
    ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(String(cString: number.objCType)),
    let value = Int64(number.stringValue), (1...9_007_199_254_740_991).contains(value)
  else { throw AtlasVaultStateViewError.rejected }
  return value
}

private func viewRoot(_ unsigned: [String: Any]) throws -> String {
  guard Set(unsigned.keys) == Set(["format", "version"] + viewFields),
    unsigned["format"] as? String == viewFormat, try viewInteger(unsigned["version"]) == 2
  else { throw AtlasVaultStateViewError.rejected }
  let values = try viewFields.map { field -> String in
    if field == "account_id" || field == "vault_id" { return try viewIdentifier(unsigned[field]) }
    if field == "sequence" || field == "key_epoch" {
      return String(try viewInteger(unsigned[field]))
    }
    return try viewHex(unsigned[field])
  }
  return viewDigest(
    Data(("atlasvault-authenticated-state-view-v2\n" + values.joined(separator: "\n") + "\n").utf8))
}

private func viewMessage(_ root: String) -> Data {
  let characters = Array(root)
  let bytes = stride(from: 0, to: characters.count, by: 2).map {
    UInt8(String(characters[$0...($0 + 1)]), radix: 16)!
  }
  return Data("atlasvault-state-view-signature-v2\0".utf8) + Data(bytes)
}

func verifiedView(_ value: [String: Any], publicKey: Data) throws -> [String: Any] {
  guard Set(value.keys) == Set(["format", "version", "root", "signature_b64"] + viewFields)
  else { throw AtlasVaultStateViewError.rejected }
  var unsigned = value
  unsigned.removeValue(forKey: "root")
  unsigned.removeValue(forKey: "signature_b64")
  let root = try viewHex(value["root"])
  guard root == (try viewRoot(unsigned)), let encoded = value["signature_b64"] as? String,
    encoded.utf8.count == 88, let signature = Data(base64Encoded: encoded), signature.count == 64,
    signature.base64EncodedString() == encoded,
    try Curve25519.Signing.PublicKey(rawRepresentation: publicKey).isValidSignature(
      signature, for: viewMessage(root))
  else { throw AtlasVaultStateViewError.rejected }
  return value
}

public enum AtlasVaultAuthenticatedStateView {
  public static func registryRoot(_ entries: [[String: Any]]) throws -> String {
    try viewBoundary {
      guard (1...viewLimit).contains(entries.count) else {
        throw AtlasVaultStateViewError.registrySubstitution
      }
      var checked = [String: String]()
      for entry in entries {
        guard Set(entry.keys) == ["device_id", "descriptor_sha256"] else {
          throw AtlasVaultStateViewError.registrySubstitution
        }
        let device = try viewHex(entry["device_id"])
        let descriptor = try viewHex(entry["descriptor_sha256"])
        guard checked[device] == nil else { throw AtlasVaultStateViewError.registrySubstitution }
        checked[device] = descriptor
      }
      let transcript =
        "atlasvault-registry-root-v1\n"
        + checked.keys.sorted().map { "\($0):\(checked[$0]!)\n" }.joined()
      return viewDigest(Data(transcript.utf8))
    }
  }

  public static func sign(_ unsigned: [String: Any], signingKey: Curve25519.Signing.PrivateKey)
    throws -> [String: Any]
  {
    try viewBoundary {
      let root = try viewRoot(unsigned)
      var signed = unsigned
      signed["root"] = root
      signed["signature_b64"] = try signingKey.signature(for: viewMessage(root))
        .base64EncodedString()
      return try verifiedView(signed, publicKey: signingKey.publicKey.rawRepresentation)
    }
  }
}

/// One owner per file. Authenticated disagreement is latched; prefix agreement is not freshness.
public final class AtlasVaultAuthenticatedHistory {
  private let store: EncryptedQueueFile
  private let publicKey: Data
  private let context: [String: Any]
  private let lock = NSLock()

  public init(
    fileURL: URL, encryptionKey: Data, accountID: String, vaultID: String,
    collectionID: String, keyEpoch: Int64, trustedSigner: Data
  ) throws {
    let configuration = try viewBoundary { () -> ([String: Any], EncryptedQueueFile) in
      guard trustedSigner.count == 32 else { throw AtlasVaultStateViewError.rejected }
      let context: [String: Any] = [
        "account_id": try viewIdentifier(accountID), "vault_id": try viewIdentifier(vaultID),
        "collection_id": try viewIdentifier(collectionID), "key_epoch": try viewInteger(keyEpoch),
        "signing_public_b64": trustedSigner.base64EncodedString(),
      ]
      return (
        context,
        try EncryptedQueueFile(
          fileURL: fileURL, encryptionKey: encryptionKey, kind: "authenticated-state-history-v2")
      )
    }
    self.context = configuration.0
    self.store = configuration.1
    self.publicKey = trustedSigner
  }

  private func exclusive<T>(_ body: () throws -> T) throws -> T {
    lock.lock()
    defer { lock.unlock() }
    return try viewBoundary(body)
  }

  private func checkContext(_ view: [String: Any]) throws {
    guard view["account_id"] as? String == context["account_id"] as? String,
      view["vault_id"] as? String == context["vault_id"] as? String,
      try viewInteger(view["key_epoch"]) == viewInteger(context["key_epoch"])
    else { throw AtlasVaultStateViewError.rejected }
  }

  private func chain(_ views: [[String: Any]]) throws -> [[String: Any]] {
    guard views.count <= viewLimit else { throw AtlasVaultStateViewError.historyLimit }
    var checked = [[String: Any]]()
    var previous = zeroViewRoot
    var registry = emptyRegistryRoot
    for (i, raw) in views.enumerated() {
      let view = try verifiedView(raw, publicKey: publicKey)
      try checkContext(view)
      guard try viewInteger(view["sequence"]) == Int64(i + 1),
        view["previous_root"] as? String == previous,
        view["previous_registry_root"] as? String == registry
      else { throw AtlasVaultStateViewError.rejected }
      checked.append(view)
      previous = view["root"] as! String
      registry = view["registry_root"] as! String
    }
    return checked
  }

  private func load() throws -> (views: [[String: Any]], blocked: Bool) {
    let value = try store.read(default: [:])
    guard Set(value.keys) == ["context", "views", "blocked"],
      let stored = value["context"] as? [String: Any],
      NSDictionary(dictionary: stored).isEqual(to: context),
      let blocked = value["blocked"] as? NSNumber, CFGetTypeID(blocked) == CFBooleanGetTypeID(),
      let views = value["views"] as? [[String: Any]]
    else { throw AtlasVaultStateViewError.rejected }
    return (try chain(views), blocked.boolValue)
  }

  private func save(_ views: [[String: Any]], blocked: Bool = false) throws {
    try store.write(["context": context, "views": views, "blocked": blocked])
  }

  public func initialize() throws {
    try exclusive {
      guard !FileManager.default.fileExists(atPath: store.fileURL.path) else {
        throw AtlasVaultStateViewError.rejected
      }
      try save([])
    }
  }

  public func exportEvidence() throws -> [[String: Any]] { try exclusive { try load().views } }

  private func fork(_ views: [[String: Any]]) throws -> Never {
    try save(views, blocked: true)
    throw AtlasVaultStateViewError.equivocation
  }

  public func compareEvidence(_ peer: [[String: Any]]) throws -> Int {
    try exclusive {
      let local = try load()
      guard !local.blocked else { throw AtlasVaultStateViewError.equivocation }
      let peer = try chain(peer)
      guard !local.views.isEmpty, !peer.isEmpty else {
        throw AtlasVaultStateViewError.checkpointRequired
      }
      for (left, right) in zip(local.views, peer)
      where left["root"] as? String != right["root"] as? String {
        try fork(local.views)
      }
      return min(local.views.count, peer.count)
    }
  }

  public func observe(
    view raw: [String: Any], registry: [[String: Any]], collection rawCollection: [String: Any],
    opaqueState: Data
  ) throws -> Bool {
    try exclusive {
      let local = try load()
      guard !local.blocked else { throw AtlasVaultStateViewError.equivocation }
      let view = try verifiedView(raw, publicKey: publicKey)
      try checkContext(view)
      guard
        try AtlasVaultAuthenticatedStateView.registryRoot(registry) == view["registry_root"]
          as? String
      else { throw AtlasVaultStateViewError.registrySubstitution }
      let collection = try AtlasVaultSignedStateCommitment(jsonObject: rawCollection)
      let sequence = try viewInteger(view["sequence"])
      guard (16...(128 * 1024 * 1024)).contains(opaqueState.count),
        collection.collectionID == context["collection_id"] as? String,
        collection.sequence == sequence,
        collection.root == view["collection_root"] as? String,
        collection.stateSHA256 == viewDigest(opaqueState),
        try collection.verify(publicKey: publicKey)
      else { throw AtlasVaultStateViewError.rejected }
      if sequence <= local.views.count,
        view["root"] as? String != local.views[Int(sequence) - 1]["root"] as? String
      {
        try fork(local.views)
      }
      guard sequence >= local.views.count else { throw AtlasVaultStateViewError.rollback }
      if sequence == local.views.count { return false }
      guard local.views.count < viewLimit else { throw AtlasVaultStateViewError.historyLimit }
      let previous = local.views.last
      guard sequence == local.views.count + 1,
        view["previous_root"] as? String == (previous?["root"] as? String ?? zeroViewRoot),
        view["previous_registry_root"] as? String
          == (previous?["registry_root"] as? String ?? emptyRegistryRoot),
        collection.previousRoot == (previous?["collection_root"] as? String ?? zeroViewRoot)
      else { throw AtlasVaultStateViewError.rejected }
      try save(local.views + [view])
      return true
    }
  }
}
