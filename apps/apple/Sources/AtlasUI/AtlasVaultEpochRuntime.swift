import CryptoKit
import Foundation

// Staging adapters never write separate replica/outbox files. EpochPublication owns publication.
private final class AtlasRuntimeComponents {
  var values: [String: Any]
  init(_ values: [String: Any]) { self.values = values }

  func file(_ name: String, owner: AtlasVaultEpochVault) throws -> EncryptedQueueFile {
    try EncryptedQueueFile(
      fileURL: owner.file.fileURL, key: owner.key,
      read: { fallback in
        guard let value = self.values[name] else { return fallback }
        return try owner.map(value)
      },
      write: { value, beforeReplace in
        try beforeReplace?()
        self.values[name] = value
      })
  }
}

extension AtlasVaultEpochVault {
  func stageEnrollmentRuntime(_ initial: [String: Any], projection: Data) throws -> [String: Any] {
    guard let historyOrigin else { throw AtlasVaultBootstrapError.rejected }
    let checkpoint = try map(map(historyOrigin["anchor"])["checkpoint"])
    guard R.digest(projection) == checkpoint["collection_sha256"] as? String else {
      throw AtlasVaultBootstrapError.rejected
    }
    let payload = try map(JSONSerialization.jsonObject(with: projection))
    let staged = AtlasRuntimeComponents(try map(initial["components"]))
    let replica = try runtimeReplica(staged)
    for raw in try rows(payload["records"]) {
      let envelope = try AtlasVaultOpaqueCiphertextEnvelope(jsonObject: raw)
      var clear = try open(envelope)
      defer { clear.resetBytes(in: 0..<clear.count) }
      let body = try map(JSONSerialization.jsonObject(with: clear))
      var operation = body.filter {
        ["operation_id", "author_device_id", "author_sequence", "lamport"].contains($0.key)
      }
      operation["format"] = "atlasvault-encrypted-patch-operation"
      operation["version"] = 1
      operation["operation_type"] = envelope.tombstone ? "delete" : "upsert"
      operation["envelope"] = raw
      let op = try AtlasVaultEncryptedPatchOperation(jsonObject: operation)
      _ = try runtimeBody(op)
      _ = try replica.ingestRemote(op)
    }
    guard
      try R.canonical(["records": replica.currentRecords().map(\.jsonObject)])
        == R.canonical(["records": payload["records"]!])
    else {
      throw AtlasVaultBootstrapError.rejected
    }
    var result = initial
    result["components"] = staged.values
    return result
  }

  func verifyEnrollmentRuntime(_ projection: Data) throws {
    try run {
      let s = try load()
      try active(s)
      guard historyOrigin != nil else { throw AtlasVaultBootstrapError.rejected }
      let (_, replica) = try checkedRuntime(s)
      let body = try map(JSONSerialization.jsonObject(with: projection))
      guard
        try R.canonical(["records": replica.currentRecords().map(\.jsonObject)])
          == R.canonical(["records": body["records"]!])
      else {
        throw AtlasVaultBootstrapError.rejected
      }
      _ = try runtimeState()
    }
  }

  private func runtimeReplica(_ staged: AtlasRuntimeComponents) throws
    -> AtlasVaultDurableEncryptedConvergentReplica
  {
    let historyContext = try map(map(staged.values["history"])["context"])
    let replica = try AtlasVaultDurableEncryptedConvergentReplica(
      fileURL: file.fileURL, encryptionKey: key, authenticationKey: key,
      collectionID: historyContext["collection_id"] as! String)
    replica.store = try staged.file("runtime", owner: self)
    return replica
  }

  private func runtimeOperations(_ components: [String: Any]) throws
    -> [AtlasVaultEncryptedPatchOperation]
  {
    guard let raw = components["runtime"] else { return [] }
    let projection = try map(raw)
    // Runtime v1 retains signed operations; snapshot import is deliberately not implicit.
    guard try rows(projection["snapshots"]).isEmpty else { throw AtlasVaultRotationError.rejected }
    let operations = try rows(projection["operations"])
    guard operations.count <= 65_536 else { throw AtlasVaultRotationError.rejected }
    return try operations.map(AtlasVaultEncryptedPatchOperation.init(jsonObject:))
  }

  private func runtimeBody(_ operation: AtlasVaultEncryptedPatchOperation) throws -> [String: Any] {
    let envelope = operation.envelope
    let body = try map(JSONSerialization.jsonObject(with: open(envelope)))
    try R.exact(
      body,
      [
        "format", "version", "operation_id", "author_device_id", "author_sequence",
        "lamport", "object_id", "revision", "parent_revision", "tombstone", "payload",
      ])
    let aad = try map(JSONSerialization.jsonObject(with: Data(base64Encoded: envelope.aadBase64)!))
    guard body["format"] as? String == "atlasvault-runtime-record",
      try viewInteger(body["version"]) == 1, envelope.version == 1,
      body["operation_id"] as? String == operation.operationID,
      body["author_device_id"] as? String == operation.authorDeviceID,
      aad["device_id"] as? String == operation.authorDeviceID,
      aad["kind"] as? String == "patch",
      try viewInteger(body["author_sequence"]) == operation.authorSequence,
      try viewInteger(body["lamport"]) == operation.lamport,
      body["object_id"] as? String == envelope.objectID,
      body["revision"] as? String == envelope.revision,
      UUID(uuidString: envelope.revision)?.uuidString.lowercased() == envelope.revision,
      envelope.parentRevision == nil
        || UUID(uuidString: envelope.parentRevision!)?.uuidString.lowercased()
          == envelope.parentRevision,
      (body["parent_revision"] is NSNull && envelope.parentRevision == nil)
        || (body["parent_revision"] as? String == envelope.parentRevision
          && envelope.parentRevision != nil),
      let tombstone = body["tombstone"] as? NSNumber,
      CFGetTypeID(tombstone) == CFBooleanGetTypeID(),
      tombstone.boolValue == envelope.tombstone,
      envelope.tombstone ? body["payload"] is NSNull : body["payload"] is [String: Any]
    else { throw AtlasVaultRotationError.rejected }
    if !envelope.tombstone {
      try AtlasVaultRuntimePayloadValidation.validate(map(body["payload"]))
    }
    var validation = AtlasVaultHydratedState()
    try hydrateRuntimeBody(body, envelope: envelope, into: &validation)
    return body
  }

  private func hydrateRuntimeBody(
    _ body: [String: Any], envelope: AtlasVaultOpaqueCiphertextEnvelope,
    into result: inout AtlasVaultHydratedState
  ) throws {
    let payload = try envelope.tombstone ? Data() : R.canonical(map(body["payload"]))
    try AtlasVaultRecordHydrator().hydratePayload(
      payload,
      metadata: .init(
        id: envelope.objectID, revision: envelope.revision, parentRevision: envelope.parentRevision,
        deleted: envelope.tombstone, keyID: "epoch-\(envelope.keyEpoch)"), into: &result)
  }

  private func checkedRuntime(_ s: [String: Any]) throws -> (
    AtlasRuntimeComponents, AtlasVaultDurableEncryptedConvergentReplica
  ) {
    let staged = AtlasRuntimeComponents(try map(s["components"]))
    let operations = try runtimeOperations(staged.values)
    for operation in operations { _ = try runtimeBody(operation) }
    // Authenticated legacy P6 records cannot silently become an empty runtime projection.
    let fingerprints = try Set(operations.map { try R.digest(R.canonical($0.envelope.jsonObject)) })
    let accepted = try map(map(staged.values["history"])["records"])
    guard
      accepted.values.allSatisfy({ value in
        guard let record = value as? [String: Any], let hash = record["envelope_sha256"] as? String
        else { return false }
        return fingerprints.contains(hash)
      })
    else { throw AtlasVaultActivationFailure.migrationRequired }
    let replica = try runtimeReplica(staged)
    _ = try replica.currentRecords()
    return (staged, replica)
  }

  public func runtimeState() throws -> AtlasVaultHydratedState {
    try run {
      let s = try load()
      try active(s)
      let (staged, replica) = try checkedRuntime(s)
      let operations = try runtimeOperations(staged.values)
      var state = AtlasVaultHydratedState()
      for envelope in try replica.currentRecords() {
        guard let operation = operations.first(where: { $0.envelope == envelope }) else {
          throw AtlasVaultRotationError.rejected
        }
        try hydrateRuntimeBody(runtimeBody(operation), envelope: envelope, into: &state)
      }
      return state
    }
  }

  public func commitRuntimeMutations(
    _ mutations: AtlasVaultMutationSet, signingKey: Curve25519.Signing.PrivateKey
  ) throws -> AtlasVaultAtomicWriteResult {
    try commitRuntimeMutationsForTesting(mutations, signingKey: signingKey)
  }

  public func importLegacyRuntime(
    _ legacy: AtlasVaultLocalStoreEnvelope, session: AtlasVaultUnlockedSession,
    signingKey: Curve25519.Signing.PrivateKey
  ) throws -> AtlasVaultAtomicWriteResult {
    try importLegacyRuntimeForTesting(legacy, session: session, signingKey: signingKey)
  }

  func importLegacyRuntimeForTesting(
    _ legacy: AtlasVaultLocalStoreEnvelope, session: AtlasVaultUnlockedSession,
    signingKey: Curve25519.Signing.PrivateKey, beforeReplace: (() throws -> Void)? = nil
  ) throws -> AtlasVaultAtomicWriteResult {
    try run {
      var s = try load()
      try active(s)
      guard session.vaultID == context["vault_id"] as? String,
        legacy.records.count <= 1024,
        Set(legacy.records.map(\.id)).count == legacy.records.count
      else {
        throw AtlasVaultActivationFailure.migrationRequired
      }
      _ = try AtlasVaultLocalStoreIO.encode(legacy)
      let (staged, replica) = try checkedRuntime(s)
      let operations = try runtimeOperations(staged.values)
      let outbox = try AtlasVaultDurableEncryptedOutbox(fileURL: file.fileURL, encryptionKey: key)
      outbox.store = try staged.file("outbox", owner: self)
      let queued = try outbox.pendingOperations()
      let author = context["device_id"] as! String
      var sequence =
        (operations + queued).filter { $0.authorDeviceID == author }.map(\.authorSequence).max()
        ?? 0
      var lamport = (operations + queued).map(\.lamport).max() ?? 0
      var imported = false
      for record in legacy.records {
        try Task.checkCancellation()
        let plaintext = try session.withVaultKey {
          try AtlasVaultRecordCrypto.open(record: record, vaultKey: $0, vaultID: session.vaultID)
        }
        let payload: Any
        if record.deleted {
          guard plaintext.isEmpty else { throw AtlasVaultActivationFailure.migrationRequired }
          payload = NSNull()
        } else {
          let value = try map(JSONSerialization.jsonObject(with: plaintext))
          try AtlasVaultRuntimePayloadValidation.validate(value)
          payload = value
        }
        let sameID = operations.filter { $0.envelope.objectID == record.id }
        let sameRevision = sameID.filter { $0.envelope.revision == record.revision }
        if !sameRevision.isEmpty {
          for operation in sameRevision {
            let body = try runtimeBody(operation)
            guard operation.envelope.parentRevision == record.parentRevision,
              operation.envelope.tombstone == record.deleted,
              try R.canonical(["payload": body["payload"]!]) == R.canonical(["payload": payload])
            else {
              throw AtlasVaultActivationFailure.migrationRequired
            }
          }
          continue
        }
        guard sameID.isEmpty else { throw AtlasVaultActivationFailure.migrationRequired }
        sequence += 1
        lamport += 1
        let operation = try makeRuntimeOperation(
          id: record.id, revision: record.revision,
          parent: record.parentRevision, payload: payload, sequence: sequence, lamport: lamport,
          signingKey: signingKey)
        _ = try replica.ingestRemote(operation)
        try outbox.enqueue(operation)
        imported = true
      }
      guard imported else { return .init(commitState: .committed) }
      s["components"] = staged.values
      s["generation"] = try R.integer(s["generation"]) + 1
      return try publishRuntime(s, beforeReplace: beforeReplace)
    }
  }

  private func makeRuntimeOperation(
    id: String, revision: String, parent: String?, payload: Any,
    sequence: Int64, lamport: Int64, signingKey: Curve25519.Signing.PrivateKey
  ) throws -> AtlasVaultEncryptedPatchOperation {
    let author = context["device_id"] as! String
    let operationID = UUID().uuidString.lowercased()
    let deleted = payload is NSNull
    let body: [String: Any] = [
      "format": "atlasvault-runtime-record", "version": 1,
      "operation_id": operationID, "author_device_id": author, "author_sequence": sequence,
      "lamport": lamport, "object_id": id, "revision": revision,
      "parent_revision": parent as Any? ?? NSNull(), "tombstone": deleted, "payload": payload,
    ]
    var sealed = try seal(
      "patch", plaintext: R.canonical(body), objectID: id, revision: revision,
      signingKey: signingKey
    ).jsonObject
    sealed["parent_revision"] = parent as Any? ?? NSNull()
    sealed["tombstone"] = deleted
    let operation = try AtlasVaultEncryptedPatchOperation(jsonObject: [
      "format": "atlasvault-encrypted-patch-operation", "version": 1, "operation_id": operationID,
      "operation_type": deleted ? "delete" : "upsert", "author_device_id": author,
      "author_sequence": sequence, "lamport": lamport, "envelope": sealed,
    ])
    _ = try runtimeBody(operation)
    return operation
  }

  func commitRuntimeMutationsForTesting(
    _ mutations: AtlasVaultMutationSet, signingKey: Curve25519.Signing.PrivateKey,
    beforeReplace: (() throws -> Void)? = nil, afterRecord: (() throws -> Void)? = nil
  ) throws -> AtlasVaultAtomicWriteResult {
    try run {
      var s = try load()
      try active(s)
      let (staged, replica) = try checkedRuntime(s)
      let prior = try replica.currentRecords()
      let operations = try runtimeOperations(staged.values)
      let outbox = try AtlasVaultDurableEncryptedOutbox(fileURL: file.fileURL, encryptionKey: key)
      outbox.store = try staged.file("outbox", owner: self)
      let author = context["device_id"] as! String
      let queued = try outbox.pendingOperations()
      var sequence =
        (operations + queued).filter { $0.authorDeviceID == author }.map(\.authorSequence).max()
        ?? 0
      var lamport = (operations + queued).map(\.lamport).max() ?? 0
      let touched = mutations.updates.map(\.recordID) + mutations.deletes.map(\.recordID)
      guard Set(touched).count == touched.count,
        mutations.creates.count + touched.count <= 1024
      else { throw AtlasVaultRotationError.rejected }
      func check(_ id: String, _ revision: String) throws {
        guard
          prior.contains(where: { $0.objectID == id && $0.revision == revision && !$0.tombstone })
        else {
          throw AtlasVaultRotationError.write
        }
      }
      for update in mutations.updates {
        try check(update.recordID, update.currentRevision)
        guard
          let previous = operations.first(where: {
            $0.envelope.objectID == update.recordID
              && $0.envelope.revision == update.currentRevision
          })
        else { throw AtlasVaultRotationError.write }
        let oldPayload = try map(runtimeBody(previous)["payload"])
        let newPayload = try map(
          JSONSerialization.jsonObject(with: update.payload.encodedPayloadEnvelope()))
        guard oldPayload["type"] as? String == newPayload["type"] as? String else {
          throw AtlasVaultRotationError.write
        }
      }
      for delete in mutations.deletes { try check(delete.recordID, delete.currentRevision) }
      func append(id: String, parent: String?, payload: AtlasVaultSavePayload?) throws {
        guard sequence < 9_007_199_254_740_991, lamport < 9_007_199_254_740_991 else {
          throw AtlasVaultRotationError.rejected
        }
        sequence += 1
        lamport += 1
        let operation = try makeRuntimeOperation(
          id: id, revision: UUID().uuidString.lowercased(), parent: parent,
          payload: payload.map {
            try JSONSerialization.jsonObject(with: $0.encodedPayloadEnvelope())
          } ?? NSNull(),
          sequence: sequence, lamport: lamport, signingKey: signingKey)
        // P5 replica supplies convergence; the existing P5 outbox owns transfer intent.
        _ = try replica.ingestRemote(operation)
        try outbox.enqueue(operation)
      }
      for create in mutations.creates {
        try append(id: UUID().uuidString.lowercased(), parent: nil, payload: create.payload)
      }
      for update in mutations.updates {
        try append(id: update.recordID, parent: update.currentRevision, payload: update.payload)
      }
      for delete in mutations.deletes {
        try append(id: delete.recordID, parent: delete.currentRevision, payload: nil)
      }
      if mutations.creates.isEmpty && touched.isEmpty { return .init(commitState: .committed) }
      s["components"] = staged.values
      s["generation"] = try R.integer(s["generation"]) + 1
      return try publishRuntime(s, beforeReplace: beforeReplace, afterRecord: afterRecord)
    }
  }

  private func publishRuntime(
    _ s: [String: Any], beforeReplace: (() throws -> Void)? = nil,
    afterRecord: (() throws -> Void)? = nil
  ) throws -> AtlasVaultAtomicWriteResult {
    try Task.checkCancellation()
    do {
      try file.write(
        s,
        beforeReplace: {
          try Task.checkCancellation()
          try beforeReplace?()
        }, afterRecord: afterRecord)
      return .init(commitState: .committed)
    } catch {
      // A publication may have renamed successfully before directory synchronization failed.
      guard let actual = try? file.read(default: [:]) else {
        throw AtlasVaultRuntimeSaveFailure.integrityUnknown
      }
      if try R.canonical(actual) == R.canonical(s) {
        return .init(commitState: .committedDurabilityUnconfirmed)
      }
      throw AtlasVaultRotationError.write
    }
  }

  public func ingestRuntimePage(
    view: [String: Any], registry: [[String: Any]], commitment: [String: Any], opaqueState: Data,
    operations incoming: [AtlasVaultEncryptedPatchOperation]
  ) throws {
    try run {
      var s = try load()
      try active(s)
      guard incoming.count <= 65_536 else { throw AtlasVaultRotationError.rejected }
      let (staged, replica) = try checkedRuntime(s)
      let admission = try history(s)
      admission.store = try staged.file("history", owner: self)
      do {
        _ = try admission.ingest(
          view: view, registry: registry, collection: commitment, opaqueState: opaqueState)
      } catch {
        if try map(staged.values["history"])["status"] as? String != "ACTIVE" {
          var original = try map(s["components"])
          original["history"] = staged.values["history"]
          s["components"] = original
          s["status"] = "RECOVERY_PENDING"
          _ = try publishRuntime(s)
        }
        throw error
      }
      for operation in incoming { _ = try runtimeBody(operation) }
      let remoteComponents = AtlasRuntimeComponents(["history": staged.values["history"]!])
      let remote = try runtimeReplica(remoteComponents)
      for operation in incoming { _ = try remote.ingestRemote(operation) }
      let body = try map(JSONSerialization.jsonObject(with: opaqueState))
      try R.exact(body, ["format", "version", "route", "records"])
      guard body["format"] as? String == "atlasvault-guarded-collection",
        try viewInteger(body["version"]) == 1, body["route"] as? String == "patch"
      else {
        throw AtlasVaultRotationError.rejected
      }
      let records = try rows(body["records"]).map(
        AtlasVaultOpaqueCiphertextEnvelope.init(jsonObject:))
      guard Set(records.map(\.objectID)).count == records.count,
        try remote.currentRecords() == records.sorted(by: { $0.objectID < $1.objectID })
      else {
        throw AtlasVaultRotationError.rejected
      }
      for operation in incoming { _ = try replica.ingestRemote(operation) }
      s["components"] = staged.values
      s["generation"] = try R.integer(s["generation"]) + 1
      _ = try publishRuntime(s)
    }
  }

  public func runtimePublication(
    signingKey: Curve25519.Signing.PrivateKey, authenticatedRegistry: [[String: Any]]? = nil
  ) throws -> [String: Any] {
    try run {
      var s = try load()
      try active(s)
      let (staged, replica) = try checkedRuntime(s)
      let h = try history(s)
      guard try h.runtimeSigningPublicKey() == signingKey.publicKey.rawRepresentation else {
        throw AtlasVaultRotationError.rejected
      }
      let operations = try runtimeOperations(staged.values)
      for operation in operations { _ = try runtimeBody(operation) }
      let bytes = try R.canonical([
        "format": "atlasvault-guarded-collection", "version": 1,
        "route": "patch", "records": replica.currentRecords().map(\.jsonObject),
      ])
      guard bytes.count <= 1024 * 1024 else {
        throw AtlasVaultRotationError.rejected
      }
      h.store = try staged.file("history", owner: self)
      var page = try stageCommitment(
        bytes, state: s, history: h, signingKey: signingKey,
        authenticatedRegistry: authenticatedRegistry)
      s["components"] = staged.values
      s["generation"] = try R.integer(s["generation"]) + 1
      _ = try publishRuntime(s)
      page["opaque_state_b64"] = bytes.base64EncodedString()
      page["operations"] = operations.map(\.jsonObject)
      return page
    }
  }

  func runtimeRequiredEpochs(_ s: [String: Any]) throws -> Set<Int> {
    // The bounded v1 projection retains its signed history, including tombstones.
    Set(try runtimeOperations(map(s["components"])).map { Int($0.envelope.keyEpoch) })
  }
}
