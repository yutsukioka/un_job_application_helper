import CoreFoundation
import CryptoKit
import Foundation

public enum AtlasVaultRevocationError: String, Error {
  case rejected = "ATLAS_REVOCATION_REJECTED"
  case authority = "ATLAS_REMOVAL_AUTHORITY"
  case pending = "ATLAS_REMOVAL_PENDING"
  case conflict = "ATLAS_REVOCATION_CONFLICT"
  case authorization = "ATLAS_REMOVAL_AUTHORIZATION"
  case rotation = "ATLAS_ROTATION_PLAN_REJECTED"
}

private let revFields = [
  "account_id", "vault_id", "target_device_id", "initiator_device_id", "prior_registry_root",
  "resulting_registry_root", "key_epoch", "sequence", "authorization_category",
]
private func revBoundary<T>(_ body: () throws -> T) throws -> T {
  do { return try body() } catch let error as AtlasVaultRevocationError { throw error } catch {
    throw AtlasVaultRevocationError.rejected
  }
}
private func revExact(_ value: [String: Any], _ fields: Set<String>) throws {
  guard Set(value.keys) == fields else { throw AtlasVaultRevocationError.rejected }
}
private func revHex(_ value: Any?) throws -> String {
  guard let s = value as? String, s.utf8.count == 64,
    s.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) })
  else { throw AtlasVaultRevocationError.rejected }
  return s
}
private func revNumber(_ value: Any?) throws -> Int64 {
  let n = try viewInteger(value)
  guard n < 9_007_199_254_740_991 else { throw AtlasVaultRevocationError.rejected }
  return n
}
private func revBytes(_ value: Any?, _ size: Int) throws -> Data {
  guard let s = value as? String, s.utf8.count == 4 * ((size + 2) / 3),
    let data = Data(base64Encoded: s), data.count == size, data.base64EncodedString() == s
  else { throw AtlasVaultRevocationError.rejected }
  return data
}
private func revCopy(_ value: [String: Any]) throws -> [String: Any] {
  try JSONSerialization.jsonObject(with: JSONSerialization.data(withJSONObject: value))
    as! [String: Any]
}

public enum AtlasVaultRevocation {
  public static func registryRoot(_ entries: [[String: Any]]) throws -> String {
    try revBoundary {
      guard (1...256).contains(entries.count) else { throw AtlasVaultRevocationError.rejected }
      var rows = [String: String]()
      for e in entries {
        try revExact(e, ["device_id", "signing_public_b64", "agreement_public_b64", "state"])
        let signing = try revBytes(e["signing_public_b64"], 32)
        let agreement = try revBytes(e["agreement_public_b64"], 32)
        let device =
          "avd1-" + viewDigest(Data("atlasvault-device-id-v1:".utf8) + signing + agreement)
        guard e["device_id"] as? String == device, rows[device] == nil,
          let state = e["state"] as? String, ["ACTIVE", "REVOKED"].contains(state)
        else { throw AtlasVaultRevocationError.rejected }
        func hex(_ bytes: Data) -> String { bytes.map { String(format: "%02x", $0) }.joined() }
        rows[device] = "\(device):\(hex(signing)):\(hex(agreement)):\(state)\n"
      }
      return viewDigest(
        Data(
          ("atlasvault-revocation-registry-v1\n" + rows.keys.sorted().map { rows[$0]! }.joined())
            .utf8))
    }
  }
  fileprivate static func root(_ value: [String: Any]) throws -> String {
    try revExact(value, Set(["format", "version"] + revFields))
    guard value["format"] as? String == "atlasvault-device-revocation",
      try revNumber(value["version"]) == 1
    else { throw AtlasVaultRevocationError.rejected }
    let fields = try revFields.map { f -> String in
      if ["key_epoch", "sequence"].contains(f) { return String(try revNumber(value[f])) }
      if f.hasSuffix("root") { return try revHex(value[f]) }
      return try viewIdentifier(value[f])
    }
    guard value["authorization_category"] as? String == "DEVICE_PRESENCE" else {
      throw AtlasVaultRevocationError.rejected
    }
    return viewDigest(
      Data(("atlasvault-device-revocation-v1\n" + fields.joined(separator: "\n") + "\n").utf8))
  }
  fileprivate static func message(_ root: String) -> Data {
    let c = Array(root)
    return Data("atlasvault-revocation-signature-v1\0".utf8)
      + Data(stride(from: 0, to: 64, by: 2).map { UInt8(String(c[$0...($0 + 1)]), radix: 16)! })
  }
  fileprivate static func removed(_ registry: [[String: Any]], target: String, initiator: String)
    throws -> [[String: Any]]
  {
    _ = try registryRoot(registry)
    let active = Set(
      registry.filter { $0["state"] as? String == "ACTIVE" }.compactMap {
        $0["device_id"] as? String
      })
    guard active.contains(target), active.contains(initiator), !active.subtracting([target]).isEmpty
    else { throw AtlasVaultRevocationError.authority }
    return registry.map { e in
      var row = e
      if e["device_id"] as? String == target { row["state"] = "REVOKED" }
      return row
    }
  }
  @discardableResult public static func verify(
    _ transition: [String: Any], registry: [[String: Any]]
  ) throws -> [[String: Any]] {
    try revBoundary {
      try revExact(transition, Set(["format", "version", "root", "signature_b64"] + revFields))
      var unsigned = transition
      unsigned.removeValue(forKey: "root")
      unsigned.removeValue(forKey: "signature_b64")
      let root = try root(unsigned)
      let after = try removed(
        registry, target: viewIdentifier(transition["target_device_id"]),
        initiator: viewIdentifier(transition["initiator_device_id"]))
      guard transition["root"] as? String == root,
        transition["prior_registry_root"] as? String == (try registryRoot(registry)),
        transition["resulting_registry_root"] as? String == (try registryRoot(after)),
        let signer = registry.first(where: {
          $0["device_id"] as? String == transition["initiator_device_id"] as? String
        }),
        try Curve25519.Signing.PublicKey(
          rawRepresentation: revBytes(signer["signing_public_b64"], 32)
        ).isValidSignature(revBytes(transition["signature_b64"], 64), for: message(root))
      else { throw AtlasVaultRevocationError.rejected }
      return after
    }
  }
  /// Metadata only, against a verified revocation. Does not apply an epoch or deliver keys.
  public static func validateRotationPlan(
    _ plan: [String: Any], transition: [String: Any], registry: [[String: Any]], stateRoot: String
  ) throws {
    try revBoundary {
      _ = try revHex(stateRoot)
      try revExact(transition, Set(["format", "version", "root", "signature_b64"] + revFields))
      var unsigned = transition
      unsigned.removeValue(forKey: "root")
      unsigned.removeValue(forKey: "signature_b64")
      guard transition["root"] as? String == (try root(unsigned)) else {
        throw AtlasVaultRevocationError.rotation
      }
      _ = try revBytes(transition["signature_b64"], 64)
      guard try registryRoot(registry) == transition["resulting_registry_root"] as? String else {
        throw AtlasVaultRevocationError.rotation
      }
      let expected: [String: Any] = [
        "format": "atlasvault-rotation-plan", "version": 1, "account_id": transition["account_id"]!,
        "vault_id": transition["vault_id"]!,
        "previous_epoch": try revNumber(transition["key_epoch"]),
        "new_epoch": try revNumber(transition["key_epoch"]) + 1,
        "prior_registry_root": transition["prior_registry_root"]!,
        "resulting_registry_root": transition["resulting_registry_root"]!, "state_root": stateRoot,
        "initiator_device_id": transition["initiator_device_id"]!,
        "revocation_root": transition["root"]!,
        "recipients": registry.filter { $0["state"] as? String == "ACTIVE" }.compactMap {
          $0["device_id"] as? String
        }.sorted(),
      ]
      try revExact(plan, Set(expected.keys))
      for f in ["version", "previous_epoch", "new_epoch"] {
        guard try viewInteger(plan[f]) == viewInteger(expected[f]) else {
          throw AtlasVaultRevocationError.rotation
        }
      }
      guard NSDictionary(dictionary: plan).isEqual(to: expected) else {
        throw AtlasVaultRevocationError.rotation
      }
    }
  }
}

/// One owner per file. Caller pins genesis from its trusted P6 checkpoint.
public final class AtlasVaultRevocationRegistry {
  private let store: EncryptedQueueFile
  private let fileURL: URL
  private let initial: [[String: Any]]
  private let context: [String: Any]
  private let lock = NSRecursiveLock()
  public init(
    fileURL: URL, encryptionKey: Data, accountID: String, vaultID: String, keyEpoch: Int64,
    registry: [[String: Any]], stateRoot: String
  ) throws {
    self.context = try [
      "account_id": viewIdentifier(accountID), "vault_id": viewIdentifier(vaultID),
      "key_epoch": revNumber(keyEpoch), "state_root": revHex(stateRoot),
      "registry_root": AtlasVaultRevocation.registryRoot(registry),
    ]
    self.initial = try revCopy(["registry": registry])["registry"] as! [[String: Any]]
    self.fileURL = fileURL
    self.store = try EncryptedQueueFile(
      fileURL: fileURL, encryptionKey: encryptionKey, kind: "device-revocation-v1")
  }
  private func exclusive<T>(_ body: () throws -> T) throws -> T {
    lock.lock()
    defer { lock.unlock() }
    return try revBoundary(body)
  }
  private func checkContext(_ t: [String: Any]) throws {
    guard t["account_id"] as? String == context["account_id"] as? String,
      t["vault_id"] as? String == context["vault_id"] as? String,
      try revNumber(t["key_epoch"]) == revNumber(context["key_epoch"]),
      try revNumber(t["sequence"]) == 1
    else { throw AtlasVaultRevocationError.rejected }
  }
  fileprivate func assertHistory(_ views: [[String: Any]]) throws {
    guard let last = views.last, last["account_id"] as? String == context["account_id"] as? String,
      last["vault_id"] as? String == context["vault_id"] as? String,
      try revNumber(last["key_epoch"]) == revNumber(context["key_epoch"]),
      last["root"] as? String == context["state_root"] as? String
    else { throw AtlasVaultRevocationError.pending }
  }
  private func read() throws -> [String: Any] {
    guard
      let size = try FileManager.default.attributesOfItem(atPath: fileURL.path)[.size] as? NSNumber,
      size.intValue <= 1024 * 1024
    else { throw AtlasVaultRevocationError.rejected }
    let value = try store.read(default: [:])
    try revExact(value, ["context", "transition", "recovery_pending"])
    guard let stored = value["context"] as? [String: Any],
      NSDictionary(dictionary: stored).isEqual(to: context),
      let blocked = value["recovery_pending"] as? NSNumber,
      CFGetTypeID(blocked) == CFBooleanGetTypeID()
    else { throw AtlasVaultRevocationError.rejected }
    if let t = value["transition"] as? [String: Any] {
      try checkContext(t)
      try AtlasVaultRevocation.verify(t, registry: initial)
    } else if !(value["transition"] is NSNull) {
      throw AtlasVaultRevocationError.rejected
    }
    return value
  }
  public func initialize() throws {
    try exclusive {
      guard !FileManager.default.fileExists(atPath: fileURL.path) else {
        throw AtlasVaultRevocationError.rejected
      }
      try store.write(["context": context, "transition": NSNull(), "recovery_pending": false])
    }
  }
  public func snapshot() throws -> [String: Any] {
    try exclusive {
      let value = try read()
      let t = value["transition"] as? [String: Any]
      let entries = try t.map { try AtlasVaultRevocation.verify($0, registry: initial) } ?? initial
      return try revCopy([
        "registry": entries, "root": AtlasVaultRevocation.registryRoot(entries),
        "sequence": t == nil ? 0 : 1,
        "status": value["recovery_pending"] as? Bool == true
          ? "RECOVERY_PENDING" : t == nil ? "ACTIVE" : "REVOCATION_PENDING",
        "transition": t as Any? ?? NSNull(),
      ])
    }
  }
  public func prepare(target: String, initiator: String) throws -> [String: Any] {
    try exclusive {
      let state = try snapshot()
      guard state["status"] as? String == "ACTIVE" else { throw AtlasVaultRevocationError.pending }
      let after = try AtlasVaultRevocation.removed(
        state["registry"] as! [[String: Any]], target: target, initiator: initiator)
      return try [
        "format": "atlasvault-device-revocation", "version": 1,
        "account_id": context["account_id"]!, "vault_id": context["vault_id"]!,
        "target_device_id": target, "initiator_device_id": initiator,
        "prior_registry_root": state["root"]!,
        "resulting_registry_root": AtlasVaultRevocation.registryRoot(after),
        "key_epoch": context["key_epoch"]!, "sequence": 1,
        "authorization_category": "DEVICE_PRESENCE",
      ]
    }
  }
  @discardableResult public func commit(_ transition: [String: Any]) throws -> Bool {
    try exclusive {
      var value = try read()
      guard value["recovery_pending"] as? Bool == false else {
        throw AtlasVaultRevocationError.pending
      }
      try checkContext(transition)
      try AtlasVaultRevocation.verify(transition, registry: initial)
      if let old = value["transition"] as? [String: Any] {
        if old["root"] as? String == transition["root"] as? String { return false }
        throw AtlasVaultRevocationError.conflict
      }
      value["transition"] = try revCopy(transition)
      try store.write(value)
      return true
    }
  }
  public func fence() throws {
    try exclusive {
      var value = try read()
      value["recovery_pending"] = true
      try store.write(value)
    }
  }
}

@MainActor private final class RemovalAuthorizationWaiter {
  var continuation: CheckedContinuation<Bool, Error>?
  var worker: Task<Void, Never>?
  var timer: Task<Void, Never>?
  var settled = false
  func finish(_ result: Result<Bool, Error>) {
    guard !settled else { return }
    settled = true
    worker?.cancel()
    timer?.cancel()
    continuation?.resume(with: result)
    continuation = nil
  }
}

@MainActor public final class AtlasVaultRemovalController {
  public let registry: AtlasVaultRevocationRegistry
  private let initiator: String
  private let authorize: () async throws -> Any
  private let sign: (Data) async throws -> Data
  private let clock: () -> TimeInterval
  private let history: AtlasVaultGuardedSyncState?
  private var target: String?
  private var generation: UInt64 = 0
  private var busy = false
  private var pendingAuthorization: RemovalAuthorizationWaiter?
  public init(
    registry: AtlasVaultRevocationRegistry, initiator: String,
    clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
    history: AtlasVaultGuardedSyncState? = nil, authorize: @escaping () async throws -> Any,
    sign: @escaping (Data) async throws -> Data
  ) {
    self.registry = registry
    self.initiator = initiator
    self.clock = clock
    self.authorize = authorize
    self.sign = sign
    self.history = history
  }
  private func historyGuard<T>(_ operation: () throws -> T) throws -> T {
    guard let history else { return try operation() }
    do {
      try registry.assertHistory(history.exportEvidence())
      return try history.automaticSync(operation)
    } catch {
      try registry.fence()
      throw AtlasVaultRevocationError.pending
    }
  }
  public func select(_ target: String) {
    cancel()
    self.target = target
  }
  public func cancel() {
    generation &+= 1
    pendingAuthorization?.finish(.failure(AtlasVaultRevocationError.authorization))
  }
  private func freshAuthorization() async throws -> Bool {
    let pending = RemovalAuthorizationWaiter()
    pendingAuthorization = pending
    defer { pendingAuthorization = nil }
    return try await withTaskCancellationHandler {
      try Task.checkCancellation()
      return try await withCheckedThrowingContinuation { continuation in
        pending.continuation = continuation
        pending.worker = Task { @MainActor in
          do {
            let result = try await authorize()
            let value = result as? NSNumber
            pending.finish(
              .success(
                value.map { CFGetTypeID($0) == CFBooleanGetTypeID() && $0.boolValue } ?? false))
          } catch {
            pending.finish(.failure(AtlasVaultRevocationError.authorization))
          }
        }
        pending.timer = Task { @MainActor in
          do {
            try await Task.sleep(for: .seconds(60))
            pending.finish(.failure(AtlasVaultRevocationError.authorization))
          } catch {
            // The request completed or was cancelled.
          }
        }
      }
    } onCancel: {
      Task { @MainActor in pending.finish(.failure(AtlasVaultRevocationError.authorization)) }
    }
  }
  public func remove(confirmedTarget: String) async throws -> [String: Any] {
    guard !busy, target == confirmedTarget else { throw AtlasVaultRevocationError.authorization }
    busy = true
    defer { busy = false }
    let owner = generation
    let started = clock()
    do {
      let unsigned = try historyGuard {
        try registry.prepare(target: confirmedTarget, initiator: initiator)
      }
      func revalidate() throws {
        let elapsed = clock() - started
        guard !Task.isCancelled, elapsed.isFinite, elapsed >= 0, elapsed < 60, generation == owner,
          NSDictionary(
            dictionary: try historyGuard {
              try registry.prepare(target: confirmedTarget, initiator: initiator)
            }
          ).isEqual(to: unsigned)
        else { throw AtlasVaultRevocationError.authorization }
      }
      let result = try await freshAuthorization()
      guard result else { throw AtlasVaultRevocationError.authorization }
      try revalidate()
      let root = try AtlasVaultRevocation.root(unsigned)
      let signature = try await sign(AtlasVaultRevocation.message(root))
      try revalidate()
      var signed = unsigned
      signed["root"] = root
      signed["signature_b64"] = signature.base64EncodedString()
      try historyGuard { try registry.commit(signed) }
      cancel()
      return signed
    } catch { throw AtlasVaultRevocationError.authorization }
  }
}
