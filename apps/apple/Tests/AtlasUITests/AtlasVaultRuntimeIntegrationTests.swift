import CryptoKit
import Foundation
import Security
import XCTest

@testable import AtlasUI

@MainActor
final class AtlasVaultRuntimeIntegrationTests: XCTestCase {
  func testProvisioningPinsOnlyAuthenticatedInitialRegistryAndPostBridgeUsesPublishedRegistry()
    throws
  {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll(provision: false)
    let store = AtlasKeychainRuntimeBindingStore(client: fixture.keychain)
    let rows = try epoch.rows(fixture.vector()["initial_registry"])
    var changed = rows
    changed[0]["descriptor_sha256"] = String(repeating: "ab", count: 32)
    let before = try Data(contentsOf: epoch.file.fileURL)
    XCTAssertThrowsError(try store.createAuthenticatedBinding(from: epoch))
    XCTAssertThrowsError(
      try store.createAuthenticatedBinding(from: epoch, authenticatedHistoryRegistry: changed))
    XCTAssertNil(try store.load(for: fixture.vaultID))
    XCTAssertEqual(
      fixture.keychain.copyMatching(
        .init(
          service: AtlasKeychainRuntimeBindingStore<RuntimeIntegrationKeychain>.storageKeyService,
          account: fixture.vaultID)
      ).status, errSecItemNotFound)
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == before)
    try store.createAuthenticatedBinding(from: epoch, authenticatedHistoryRegistry: rows)
    try fixture.activateEpoch(epoch)
    let binding = try XCTUnwrap(store.load(for: fixture.vaultID))
    let reopened = try binding.open(
      directory: fixture.epochURL,
      session: AtlasVaultUnlockedSession(
        vaultID: fixture.vaultID, vaultKey: fixture.legacyKey))
    _ = try reopened.commitRuntimeMutations(
      .init(creates: [.init(payload: fixture.note(), keyID: "runtime")]),
      signingKey: binding.signingKey())
    let prePublication = try Data(contentsOf: epoch.file.fileURL)
    XCTAssertThrowsError(
      try reopened.runtimePublication(signingKey: binding.signingKey(), authenticatedRegistry: rows)
    )
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == prePublication)
    let page = try reopened.runtimePublication(signingKey: binding.signingKey())
    XCTAssertTrue(
      try epoch.map(page["view"])["registry_root"] as? String
        == AtlasVaultRevocation.registryRoot(epoch.rows(epoch.load()["registry"])))
  }

  func testNewlyEnrolledOwnerPublishesBeforeFirstRotation() throws {
    let first = try RuntimeIntegrationFixture()
    let second = try RuntimeIntegrationFixture()
    defer {
      first.remove()
      second.remove()
    }
    let sender = try first.enroll()
    let receiver = try second.enroll(device: 1)
    _ = try sender.commitRuntimeMutations(
      .init(creates: [.init(payload: first.note(), keyID: "runtime")]), signingKey: first.signer())
    let registry = try sender.rows(first.vector()["initial_registry"])
    let before = try Data(contentsOf: sender.file.fileURL)
    XCTAssertThrowsError(try sender.runtimePublication(signingKey: first.signer()))
    var changed = registry
    changed[0]["descriptor_sha256"] = String(repeating: "ef", count: 32)
    XCTAssertThrowsError(
      try sender.runtimePublication(signingKey: first.signer(), authenticatedRegistry: changed))
    XCTAssertThrowsError(
      try sender.runtimePublication(
        signingKey: Curve25519.Signing.PrivateKey(
          rawRepresentation: Data(repeating: 11, count: 32)), authenticatedRegistry: registry))
    XCTAssertTrue(try Data(contentsOf: sender.file.fileURL) == before)
    let binding = try XCTUnwrap(
      AtlasKeychainRuntimeBindingStore(client: first.keychain).load(for: first.vaultID))
    let reopened = try binding.open(
      directory: first.epochURL,
      session: AtlasVaultUnlockedSession(
        vaultID: first.vaultID, vaultKey: first.legacyKey))
    let page = try reopened.runtimePublication(signingKey: binding.signingKey())
    try receiver.ingestRuntimePage(
      view: receiver.map(page["view"]), registry: receiver.rows(first.vector()["initial_registry"]),
      commitment: receiver.map(page["collection"]),
      opaqueState: Data(base64Encoded: page["opaque_state_b64"] as! String)!,
      operations: receiver.rows(page["operations"]).map(
        AtlasVaultEncryptedPatchOperation.init(jsonObject:)))
    XCTAssertTrue(try receiver.runtimeState() == sender.runtimeState())
    XCTAssertEqual(try receiver.observation()["key_epoch"] as? Int, 3)
    XCTAssertEqual(try receiver.observation()["sequence"] as? Int, 2)
  }

  func testProductionHostMarksPreEnrollmentLegacyReadOnlyAndSuppressesCreate() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    try fixture.createLegacy()
    let before = try Data(contentsOf: fixture.storeURL)
    try await AtlasKeychainVaultSelectionRegistry(client: fixture.keychain)
      .createSelection(AtlasSelectedVaultID(validating: fixture.vaultID))
    let time = RuntimeIntegrationTime()
    let harness = try AtlasVaultProductionCompositionFactory.makeUnwiredProductionLike(
      configuration: .init(
        apiBaseURL: URL(string: "https://example.invalid")!, publicSearchLimit: 50,
        unlockTimeout: .seconds(30), lifecycleLockPolicy: .immediate, lockOnInactive: true),
      lifecycleEvents: RuntimeIntegrationLifecycle(),
      directoryLocator: RuntimeIntegrationDirectory(root: fixture.root),
      keychainClient: fixture.keychain,
      atomicFileSystemClient: AtlasFoundationAtomicFileSystemClient(),
      lifecycleClock: time, lifecycleSleeper: time, publicJobs: RuntimeIntegrationPublicJobs(),
      publicSnapshotRestorer: RuntimeIntegrationSnapshot(),
      unlockRequestSleep: { _ in throw CancellationError() })
    _ = try await harness.start()
    await harness.publicShellActions.requestUnlock()
    await harness.unlockActions.select(.localKey)
    let outcome = await harness.unlockActions.submit(.localKey)
    XCTAssertEqual(outcome, .unlocked)
    let searches = try XCTUnwrap(harness.savedSearchContextForTesting)
    XCTAssertTrue(searches.owner.isReadOnly)
    await searches.actions.create(.init(name: "synthetic-read-only-create", searchText: "example"))
    XCTAssertTrue(searches.owner.items.isEmpty)
    let records = try XCTUnwrap(harness.recordsContextForTesting?.owner)
    XCTAssertTrue(records.isReadOnly)
    XCTAssertEqual(records.records.count, 1)
    var draft = try AtlasVaultRecordDraft(family: .savedJob)
    draft.values["job_key"] = "synthetic-read-only-create"
    await records.save(draft)
    XCTAssertEqual(records.records.count, 1)
    XCTAssertTrue(try Data(contentsOf: fixture.storeURL) == before)
    XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.epochURL.path))
    _ = await harness.stop()
  }
  func testMissingBindingCannotMaskExistingPendingEpochWithLegacyRead() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    try fixture.createLegacy()
    let epoch = try fixture.enroll()
    let proof = try epoch.map(epoch.map(fixture.vector()["record"])["proof"])
    try epoch.beginActivation(proof)
    XCTAssertEqual(
      fixture.keychain.delete(
        .init(
          service: AtlasKeychainRuntimeBindingStore<RuntimeIntegrationKeychain>.service,
          account: fixture.vaultID)), errSecSuccess)
    let oldFile = try Data(contentsOf: fixture.storeURL)
    let epochFile = try Data(contentsOf: epoch.file.fileURL)
    let runtime = fixture.runtime()
    do {
      try await runtime.activate(.init(vaultID: fixture.vaultID))
      XCTFail("Pending epoch hidden by legacy fallback")
    } catch {}
    do {
      _ = try await runtime.privateState()
      XCTFail("Legacy state exposed over pending epoch")
    } catch {}
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == epochFile)
    XCTAssertTrue(try Data(contentsOf: fixture.storeURL) == oldFile)
  }

  func testLegacyReadOnlySessionContainsNewEpochState() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    try fixture.createLegacy()
    let runtime = fixture.runtime()
    try await runtime.activate(.init(vaultID: fixture.vaultID))
    _ = try fixture.enroll()
    do {
      _ = try await runtime.privateState()
      XCTFail("Existing legacy session ignored enrollment")
    } catch {}
  }
  func testRuntimeBindingReopensDistinctOwnerKeyAndPreservesLegacyKey() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    XCTAssertFalse(fixture.storageKey == fixture.legacyKey)
    try fixture.createLegacy()
    let epoch = try fixture.enroll()
    let oldFile = try Data(contentsOf: fixture.storeURL)
    let runtime = fixture.runtime()
    try await runtime.activate(.init(vaultID: fixture.vaultID))
    XCTAssertEqual(try epoch.runtimeState().applicationNotes.count, 1)
    await runtime.lock()
    let reopened = fixture.runtime()
    try await reopened.activate(.init(vaultID: fixture.vaultID))
    let state = try await reopened.privateState().state
    XCTAssertEqual(state.applicationNotes.count, 1)
    XCTAssertTrue(
      try AtlasKeychainVaultKeyStore(client: fixture.keychain).loadVaultKey(for: fixture.vaultID)
        == fixture.legacyKey)
    XCTAssertTrue(try Data(contentsOf: fixture.storeURL) == oldFile)
    await reopened.lock()
    let epochFile = try Data(contentsOf: epoch.file.fileURL)
    XCTAssertEqual(
      fixture.keychain.delete(
        .init(
          service: AtlasKeychainRuntimeBindingStore<RuntimeIntegrationKeychain>.storageKeyService,
          account: fixture.vaultID)), errSecSuccess)
    do {
      try await fixture.runtime().activate(.init(vaultID: fixture.vaultID))
      XCTFail("Missing runtime key accepted")
    } catch {}
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == epochFile)
    XCTAssertTrue(try Data(contentsOf: fixture.storeURL) == oldFile)
  }

  func testRuntimePayloadSchemaRejectsUnknownFieldsAcrossAllFamilies() throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    for payload in fixture.payloads() {
      let value = try AtlasVaultDeviceDelivery.map(
        JSONSerialization.jsonObject(with: payload.encodedPayloadEnvelope()))
      XCTAssertNoThrow(try AtlasVaultRuntimePayloadValidation.validate(value))
      var envelope = value
      envelope["unknown"] = true
      XCTAssertThrowsError(try AtlasVaultRuntimePayloadValidation.validate(envelope))
      envelope = value
      var inner = try AtlasVaultDeviceDelivery.map(envelope["payload"])
      inner["unknown"] = true
      envelope["payload"] = inner
      XCTAssertThrowsError(try AtlasVaultRuntimePayloadValidation.validate(envelope))
    }
    let value = try AtlasVaultDeviceDelivery.map(
      JSONSerialization.jsonObject(with: fixture.payloads()[0].encodedPayloadEnvelope()))
    for field in ["unknown", "closing_date_to", "limit", "include_facets"] {
      var envelope = value
      var payload = try AtlasVaultDeviceDelivery.map(envelope["payload"])
      var request = try AtlasVaultDeviceDelivery.map(payload["request"])
      switch field {
      case "closing_date_to": request[field] = "2026-02-30"
      case "limit": request[field] = true
      default: request[field] = 1
      }
      payload["request"] = request
      envelope["payload"] = payload
      XCTAssertThrowsError(try AtlasVaultRuntimePayloadValidation.validate(envelope), field)
    }
  }
  func testFullHistoryAdmissionPreservesLocalUnsentEditsAndRejectsSidecarMismatch() throws {
    let first = try RuntimeIntegrationFixture()
    let second = try RuntimeIntegrationFixture()
    defer {
      first.remove()
      second.remove()
    }
    let sender = try first.enroll()
    let receiver = try second.enroll(device: 1)
    try first.activateEpoch(sender)
    try second.activateEpoch(receiver, device: 1)
    _ = try sender.commitRuntimeMutations(
      .init(creates: [.init(payload: first.note(), keyID: "local")]), signingKey: first.signer())
    for op in try sender.pendingOperations() { try sender.confirmRemoteAcceptance(op.operationID) }
    let page = try sender.runtimePublication(signingKey: first.signer())
    let registry = try sender.rows(sender.load()["registry"])
    let operations = try sender.rows(page["operations"]).map(
      AtlasVaultEncryptedPatchOperation.init(jsonObject:))
    XCTAssertEqual(operations.count, 1)
    _ = try receiver.commitRuntimeMutations(
      .init(creates: [.init(payload: second.note(), keyID: "local")]),
      signingKey: Curve25519.Signing.PrivateKey(rawRepresentation: Data(repeating: 11, count: 32)))
    let before = try Data(contentsOf: receiver.file.fileURL)
    XCTAssertThrowsError(
      try receiver.ingestRuntimePage(
        view: receiver.map(page["view"]), registry: registry,
        commitment: receiver.map(page["collection"]),
        opaqueState: Data(base64Encoded: page["opaque_state_b64"] as! String)!, operations: []))
    XCTAssertTrue(try Data(contentsOf: receiver.file.fileURL) == before)
    try receiver.ingestRuntimePage(
      view: receiver.map(page["view"]), registry: registry,
      commitment: receiver.map(page["collection"]),
      opaqueState: Data(base64Encoded: page["opaque_state_b64"] as! String)!, operations: operations
    )
    XCTAssertEqual(try receiver.runtimeState().applicationNotes.count, 2)
    XCTAssertEqual(try receiver.pendingOperations().count, 1)
  }

  func testUnmappedAuthenticatedP6RecordsFailClosed() throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    try fixture.activateEpoch(epoch)
    _ = try epoch.commitRuntimeMutations(
      .init(creates: [.init(payload: fixture.note(), keyID: "local")]), signingKey: fixture.signer()
    )
    _ = try epoch.runtimePublication(signingKey: fixture.signer())
    var s = try epoch.load()
    var components = try epoch.map(s["components"])
    components.removeValue(forKey: "runtime")
    s["components"] = components
    try epoch.file.write(s)
    let before = try Data(contentsOf: epoch.file.fileURL)
    XCTAssertThrowsError(try epoch.runtimeState()) { error in
      XCTAssertEqual(error as? AtlasVaultActivationFailure, .migrationRequired)
    }
    XCTAssertThrowsError(try epoch.commitRuntimeMutations(.init(), signingKey: fixture.signer()))
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == before)
  }

  func testLegacyImportAllFamiliesTombstonesAndAtomicAbort() throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    let session = try AtlasVaultUnlockedSession(
      vaultID: fixture.vaultID, vaultKey: fixture.legacyKey)
    var records = try AtlasVaultRecordSaver().save(
      mutations: .init(
        creates:
          fixture.payloads().map { .init(payload: $0, keyID: "legacy") }), session: session)
    records += try AtlasVaultRecordSaver().save(
      mutations: .init(deletes: [
        .init(
          recordID: UUID().uuidString.lowercased(), currentRevision: UUID().uuidString.lowercased(),
          keyID: "legacy")
      ]), session: session)
    let legacy = AtlasVaultLocalStoreEnvelope(
      storeID: "test", createdAt: "test", updatedAt: "test", vaultMetadata: [:], records: records)
    let before = try Data(contentsOf: epoch.file.fileURL)
    XCTAssertThrowsError(
      try epoch.importLegacyRuntimeForTesting(
        legacy, session: session, signingKey: fixture.signer(),
        beforeReplace: { throw RuntimeTestFailure.stop }))
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == before)
    XCTAssertTrue(try epoch.pendingOperations().isEmpty)
    _ = try epoch.importLegacyRuntime(legacy, session: session, signingKey: fixture.signer())
    XCTAssertEqual(try fixture.counts(epoch.runtimeState()), [1, 1, 1, 1, 1, 1])
    XCTAssertEqual(try epoch.pendingOperations().count, 6)
    for record in records {
      XCTAssertTrue(
        try epoch.pendingOperations().contains {
          $0.envelope.objectID == record.id && $0.envelope.revision == record.revision
            && $0.envelope.parentRevision == record.parentRevision
            && $0.envelope.tombstone == record.deleted
        })
    }
  }

  func testLegacyReplayChecksContentAndRevisionEvenAfterRuntimeEdit() throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    try fixture.createLegacy()
    let legacy = try AtlasVaultLocalStoreIO.read(from: fixture.storeURL)
    let epoch = try fixture.enroll()
    let session = try AtlasVaultUnlockedSession(
      vaultID: fixture.vaultID, vaultKey: fixture.legacyKey)
    _ = try epoch.importLegacyRuntime(legacy, session: session, signingKey: fixture.signer())
    let old = legacy.records[0]
    _ = try epoch.commitRuntimeMutations(
      .init(updates: [
        .init(
          recordID: old.id, currentRevision: old.revision,
          payload: fixture.note(), keyID: "local")
      ]), signingKey: fixture.signer())
    let before = try Data(contentsOf: epoch.file.fileURL)
    _ = try epoch.importLegacyRuntime(legacy, session: session, signingKey: fixture.signer())
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == before)
    for field in ["payload", "revision", "parent", "deleted"] {
      var payload = try epoch.map(
        JSONSerialization.jsonObject(with: fixture.note().encodedPayloadEnvelope()))
      if field == "payload" { payload["client_updated_at"] = "2026-01-02T00:00:00Z" }
      let header = AtlasVaultEncryptedRecordEnvelope(
        id: old.id, schemaVersion: old.schemaVersion,
        revision: field == "revision" ? UUID().uuidString.lowercased() : old.revision,
        parentRevision: field == "parent" ? UUID().uuidString.lowercased() : old.parentRevision,
        deleted: field == "deleted", keyID: old.keyID,
        nonce: Data(AES.GCM.Nonce()).base64EncodedString(), ciphertext: "")
      let record = try AtlasVaultRecordCrypto.seal(
        plaintext: field == "deleted" ? Data() : AtlasVaultEpochRotation.canonical(payload),
        vaultKey: fixture.legacyKey, vaultID: fixture.vaultID, record: header)
      let changed = AtlasVaultLocalStoreEnvelope(
        storeID: "test", createdAt: "test", updatedAt: "test", vaultMetadata: [:], records: [record]
      )
      XCTAssertThrowsError(
        try epoch.importLegacyRuntime(changed, session: session, signingKey: fixture.signer()),
        field
      ) { error in
        XCTAssertEqual(error as? AtlasVaultActivationFailure, .migrationRequired)
      }
      XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == before, field)
    }
  }

  func testRuntimePublicationUsesUnchangedGuardedCollectionDialect() throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    try fixture.activateEpoch(epoch)
    _ = try epoch.commitRuntimeMutations(
      .init(creates: [.init(payload: fixture.note(), keyID: "local")]), signingKey: fixture.signer()
    )
    let page = try epoch.runtimePublication(signingKey: fixture.signer())
    let body = try epoch.map(
      JSONSerialization.jsonObject(with: Data(base64Encoded: page["opaque_state_b64"] as! String)!))
    XCTAssertEqual(body["format"] as? String, "atlasvault-guarded-collection")
    XCTAssertEqual(Set(body.keys), ["format", "version", "route", "records"])
    XCTAssertNotNil(page["operations"])
  }

  func testRuntimeRejectsNoncanonicalParentsAndStrictPayloadViolations() throws {
    let first = try RuntimeIntegrationFixture()
    defer { first.remove() }
    let sender = try first.enroll()
    _ = try sender.commitRuntimeMutations(
      .init(creates: [.init(payload: first.note(), keyID: "local")]), signingKey: first.signer())
    let op = try XCTUnwrap(sender.pendingOperations().first)
    for field in [
      "parent", "uppercase_parent", "envelope_extra", "payload_extra", "null_optional",
      "invalid_date", "fractional_sort",
    ] {
      let second = try RuntimeIntegrationFixture()
      defer { second.remove() }
      let receiver = try second.enroll(device: 1)
      let before = try Data(contentsOf: receiver.file.fileURL)
      var body = try sender.map(JSONSerialization.jsonObject(with: sender.open(op.envelope)))
      var payload = try sender.map(body["payload"])
      var value = try sender.map(payload["payload"])
      switch field {
      case "parent": body["parent_revision"] = "not-a-uuid"
      case "uppercase_parent": body["parent_revision"] = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
      case "envelope_extra": payload["unknown"] = true
      case "payload_extra": value["unknown"] = true
      case "null_optional": value["title"] = NSNull()
      case "invalid_date": value["created_at"] = "2026-02-30T00:00:00Z"
      default: value["sort_order"] = 1
      }
      payload["payload"] = value
      body["payload"] = payload
      var bytes = try AtlasVaultEpochRotation.canonical(body)
      if field == "fractional_sort" {
        bytes = Data(
          String(decoding: bytes, as: UTF8.self).replacingOccurrences(
            of: "\"sort_order\":1", with: "\"sort_order\":1.0"
          ).utf8)
      }
      var sealed = try sender.seal(
        "patch", plaintext: bytes, objectID: op.envelope.objectID,
        revision: op.envelope.revision, signingKey: first.signer()
      ).jsonObject
      sealed["parent_revision"] = body["parent_revision"]
      var raw = op.jsonObject
      raw["envelope"] = sealed
      XCTAssertThrowsError(
        try first.admit([AtlasVaultEncryptedPatchOperation(jsonObject: raw)], to: receiver), field)
      XCTAssertTrue(try Data(contentsOf: receiver.file.fileURL) == before, field)
    }
  }

  func testProductionHostUnlockAndAllFamilyActionsReachEpochOutbox() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    try await AtlasKeychainVaultSelectionRegistry(client: fixture.keychain)
      .createSelection(AtlasSelectedVaultID(validating: fixture.vaultID))
    let time = RuntimeIntegrationTime()
    let harness = try AtlasVaultProductionCompositionFactory.makeUnwiredProductionLike(
      configuration: .init(
        apiBaseURL: URL(string: "https://example.invalid")!, publicSearchLimit: 50,
        unlockTimeout: .seconds(30), lifecycleLockPolicy: .immediate, lockOnInactive: true),
      lifecycleEvents: RuntimeIntegrationLifecycle(),
      directoryLocator: RuntimeIntegrationDirectory(root: fixture.root),
      keychainClient: fixture.keychain,
      atomicFileSystemClient: AtlasFoundationAtomicFileSystemClient(),
      lifecycleClock: time, lifecycleSleeper: time, publicJobs: RuntimeIntegrationPublicJobs(),
      publicSnapshotRestorer: RuntimeIntegrationSnapshot(),
      unlockRequestSleep: { _ in throw CancellationError() })
    _ = try await harness.start()
    await harness.publicShellActions.requestUnlock()
    await harness.unlockActions.select(.localKey)
    let outcome = await harness.unlockActions.submit(.localKey)
    XCTAssertEqual(outcome, .unlocked)
    let searches = try XCTUnwrap(harness.savedSearchContextForTesting)
    await searches.actions.create(.init(name: "synthetic-runtime-host", searchText: "example"))
    XCTAssertEqual(searches.owner.items.count, 1)
    let owner = try XCTUnwrap(harness.recordsContextForTesting?.owner)
    for family in AtlasVaultRecordFamily.allCases {
      var draft = try AtlasVaultRecordDraft(family: family)
      for field in family.required { draft.values[field] = "synthetic-runtime-host" }
      await owner.save(draft)
    }
    XCTAssertEqual(try fixture.counts(epoch.runtimeState()), [1, 1, 1, 1, 1, 0])
    for var draft in owner.records {
      draft.values[draft.family.required[0]] = "synthetic-runtime-host-edited"
      await owner.save(draft)
    }
    for draft in owner.records { await owner.delete(draft) }
    let search = try XCTUnwrap(searches.owner.items.first)
    await searches.actions.update(
      search.id, draft: .init(name: "synthetic-runtime-host-edited", searchText: "example"))
    await searches.actions.delete(search.id)
    XCTAssertEqual(try epoch.pendingOperations().count, 15)
    _ = await harness.stop()
    XCTAssertFalse(owner.isAvailable)
    XCTAssertTrue(owner.records.isEmpty)
    XCTAssertEqual(searches.owner.status, .hidden)
  }
  func testAuthenticatedPublicationPinsP6SignerAndPersistsForkWithoutProjection() throws {
    let first = try RuntimeIntegrationFixture()
    let second = try RuntimeIntegrationFixture()
    defer {
      first.remove()
      second.remove()
    }
    let sender = try first.enroll()
    let receiver = try second.enroll(device: 1)
    try first.activateEpoch(sender)
    try second.activateEpoch(receiver, device: 1)
    _ = try sender.commitRuntimeMutations(
      .init(creates: [.init(payload: first.note(), keyID: "local")]), signingKey: first.signer())
    let before = try Data(contentsOf: sender.file.fileURL)
    XCTAssertThrowsError(
      try sender.runtimePublication(
        signingKey: Curve25519.Signing.PrivateKey(rawRepresentation: Data(repeating: 11, count: 32))
      ))
    XCTAssertTrue(try Data(contentsOf: sender.file.fileURL) == before)
    let page = try sender.runtimePublication(signingKey: first.signer())
    let registry = try sender.rows(sender.load()["registry"])
    try receiver.ingestRuntimePage(
      view: page["view"] as! [String: Any], registry: registry,
      commitment: page["collection"] as! [String: Any],
      opaqueState: Data(base64Encoded: page["opaque_state_b64"] as! String)!,
      operations: receiver.rows(page["operations"]).map(
        AtlasVaultEncryptedPatchOperation.init(jsonObject:)))
    XCTAssertTrue(try sender.runtimeState() == receiver.runtimeState())
    let projection = try AtlasVaultEpochRotation.canonical(
      receiver.map(receiver.map(receiver.load()["components"])["runtime"]))
    let fork = try RuntimeIntegrationFixture()
    defer { fork.remove() }
    let forked = try fork.enroll()
    try fork.activateEpoch(forked)
    _ = try forked.commitRuntimeMutations(
      .init(creates: [.init(payload: fork.note(), keyID: "local")]), signingKey: fork.signer())
    let other = try forked.runtimePublication(signingKey: fork.signer())
    XCTAssertThrowsError(
      try receiver.ingestRuntimePage(
        view: other["view"] as! [String: Any], registry: registry,
        commitment: other["collection"] as! [String: Any],
        opaqueState: Data(base64Encoded: other["opaque_state_b64"] as! String)!,
        operations: receiver.rows(other["operations"]).map(
          AtlasVaultEncryptedPatchOperation.init(jsonObject:))))
    XCTAssertEqual(try receiver.observation()["status"] as? String, "RECOVERY_PENDING")
    XCTAssertTrue(
      try AtlasVaultEpochRotation.canonical(
        receiver.map(receiver.map(receiver.load()["components"])["runtime"])) == projection)
    XCTAssertFalse(try receiver.rows(receiver.recovery()["peer"]).isEmpty)
  }

  func testExportRuntimePageForCrossLanguageConformance() throws {
    guard let path = ProcessInfo.processInfo.environment["ATLAS_C29_EXPORT_PAGE"] else {
      throw XCTSkip("Optional cross-language encrypted-page export")
    }
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    try fixture.activateEpoch(epoch)
    _ = try epoch.commitRuntimeMutations(
      .init(creates: fixture.payloads().map { .init(payload: $0, keyID: "runtime") }),
      signingKey: fixture.signer())
    var page = try epoch.runtimePublication(signingKey: fixture.signer())
    page["registry"] = try epoch.load()["registry"]
    try AtlasVaultEpochRotation.canonical(page).write(to: URL(fileURLWithPath: path))
  }

  func testImportDartRuntimePageForCrossLanguageConformance() throws {
    guard let path = ProcessInfo.processInfo.environment["ATLAS_C29_DART_PAGE"] else {
      throw XCTSkip("Optional cross-language encrypted-page import")
    }
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll(device: 1)
    try fixture.activateEpoch(epoch, device: 1)
    let page = try AtlasVaultDeviceDelivery.map(
      JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))))
    try epoch.ingestRuntimePage(
      view: epoch.map(page["view"]), registry: epoch.rows(page["registry"]),
      commitment: epoch.map(page["collection"]),
      opaqueState: Data(base64Encoded: page["opaque_state_b64"] as! String)!,
      operations: epoch.rows(page["operations"]).map(
        AtlasVaultEncryptedPatchOperation.init(jsonObject:)))
    let state = try epoch.runtimeState()
    XCTAssertEqual(fixture.counts(state), [1, 1, 1, 1, 1, 0])
    var repository = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { repository.deleteLastPathComponent() }
    let vectors = try epoch.map(
      JSONSerialization.jsonObject(
        with: Data(
          contentsOf: repository.appendingPathComponent(
            "contracts/sync/test_vectors/atlasvault_payload_vectors_v1.json"))))
    let expected = try epoch.map(vectors["payloads"])
    let actual: [AtlasVaultSavePayload] = [
      .savedSearch(
        .init(
          type: .savedSearch, payload: state.savedSearches[0].payload,
          clientCreatedAt: state.savedSearches[0].clientCreatedAt,
          clientUpdatedAt: state.savedSearches[0].clientUpdatedAt)),
      .savedJob(
        .init(
          type: .savedJob, payload: state.savedJobs[0].payload,
          clientCreatedAt: state.savedJobs[0].clientCreatedAt,
          clientUpdatedAt: state.savedJobs[0].clientUpdatedAt)),
      .applicationNote(
        .init(
          type: .applicationNote, payload: state.applicationNotes[0].payload,
          clientCreatedAt: state.applicationNotes[0].clientCreatedAt,
          clientUpdatedAt: state.applicationNotes[0].clientUpdatedAt)),
      .profileSnippet(
        .init(
          type: .profileSnippet, payload: state.profileSnippets[0].payload,
          clientCreatedAt: state.profileSnippets[0].clientCreatedAt,
          clientUpdatedAt: state.profileSnippets[0].clientUpdatedAt)),
      .draftMetadata(
        .init(
          type: .draftMetadata, payload: state.draftMetadata[0].payload,
          clientCreatedAt: state.draftMetadata[0].clientCreatedAt,
          clientUpdatedAt: state.draftMetadata[0].clientUpdatedAt)),
    ]
    for payload in actual {
      let value = try epoch.map(
        JSONSerialization.jsonObject(with: payload.encodedPayloadEnvelope()))
      let type = value["type"] as! String
      XCTAssertTrue(
        try AtlasVaultEpochRotation.canonical(value)
          == AtlasVaultEpochRotation.canonical(epoch.map(expected[type])), type)
    }
    XCTAssertEqual(try epoch.observation()["sequence"] as? Int, 2)
    XCTAssertTrue(try epoch.pendingOperations().isEmpty)
  }

  func testNativeEditorOwnerCRUDForFourAdditionalFamiliesAndClearsOnLock() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    let runtime = fixture.runtime()
    try await runtime.activate(.init(vaultID: fixture.vaultID))
    let owner = AtlasVaultRecordsOwner(
      read: { try await runtime.privateState().state },
      apply: { request in
        do {
          _ = try await runtime.apply(request)
          return .committed
        } catch { return .failed }
      }, contain: { await runtime.lock() })
    _ = await owner.activatePrivateSession(selectedVault: fixture.vaultID)
    for family in AtlasVaultRecordFamily.allCases {
      var draft = try AtlasVaultRecordDraft(family: family)
      for field in family.required { draft.values[field] = "synthetic-runtime-editor" }
      XCTAssertTrue(draft.isValid)
      await owner.save(draft)
    }
    XCTAssertEqual(owner.records.count, 4)
    for var draft in owner.records {
      draft.values[draft.family.required[0]] = "synthetic-runtime-edited"
      await owner.save(draft)
    }
    for draft in owner.records { await owner.delete(draft) }
    XCTAssertEqual(try epoch.pendingOperations().count, 12)
    XCTAssertTrue(owner.records.isEmpty)
    await owner.stopAndDrainPrivateSession()
    XCTAssertFalse(owner.isAvailable)
    await runtime.lock()
  }
  func testRuntimeRejectsFractionalJSONNumbersAndSignerDeputy() throws {
    let first = try RuntimeIntegrationFixture()
    defer { first.remove() }
    let sender = try first.enroll()
    _ = try sender.commitRuntimeMutations(
      .init(creates: [.init(payload: first.note(), keyID: "local")]), signingKey: first.signer())
    let op = try XCTUnwrap(sender.pendingOperations().first)
    let body = try sender.open(op.envelope)
    for field in ["version", "author_sequence", "lamport", "payload_schema", "deputy", "snapshot"] {
      let second = try RuntimeIntegrationFixture()
      defer { second.remove() }
      let receiver = try second.enroll(device: 1)
      let before = try Data(contentsOf: receiver.file.fileURL)
      let malicious =
        field == "deputy" || field == "snapshot"
        ? body
        : Data(
          String(decoding: body, as: UTF8.self)
            .replacingOccurrences(of: "\"\(field)\":1", with: "\"\(field)\":1.0").utf8)
      let author = field == "deputy" ? receiver : sender
      let key = try Curve25519.Signing.PrivateKey(
        rawRepresentation: Data(repeating: field == "deputy" ? 11 : 10, count: 32))
      let sealed = try author.seal(
        field == "snapshot" ? "snapshot" : "patch", plaintext: malicious,
        objectID: op.envelope.objectID, revision: op.envelope.revision, signingKey: key)
      var raw = op.jsonObject
      raw["envelope"] = sealed.jsonObject
      XCTAssertThrowsError(
        try first.admit([AtlasVaultEncryptedPatchOperation(jsonObject: raw)], to: receiver), field)
      XCTAssertTrue(try Data(contentsOf: receiver.file.fileURL) == before)
    }
  }

  func testProvisionedLegacyRecordsImportOnceWithoutModifyingOldStore() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    try fixture.createLegacy()
    let epoch = try fixture.enroll()
    let legacy = try Data(contentsOf: fixture.storeURL)
    let old = try AtlasVaultLocalStoreIO.read(from: fixture.storeURL).records[0]
    let runtime = fixture.runtime()
    try await runtime.activate(.init(vaultID: fixture.vaultID))
    let restored = try await runtime.privateState().state
    XCTAssertEqual(restored.applicationNotes.count, 1)
    XCTAssertTrue(restored.applicationNotes.first?.metadata.revision == old.revision)
    XCTAssertTrue(restored.applicationNotes.first?.metadata.id == old.id)
    XCTAssertEqual(try epoch.pendingOperations().count, 1)
    await runtime.lock()
    let activation = try Data(contentsOf: epoch.file.fileURL)
    try await fixture.runtime().activate(.init(vaultID: fixture.vaultID))
    XCTAssertTrue(try Data(contentsOf: fixture.storeURL) == legacy)
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == activation)
  }
  func testProductionRootExposesAllFamilyEditorThroughHostBoundary() throws {
    let apple = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent()
    let root = try String(
      contentsOf: apple.appendingPathComponent(
        "Sources/AtlasUI/AtlasVaultProductionRootView.swift"), encoding: .utf8)
    let harness = try String(
      contentsOf: apple.appendingPathComponent(
        "Sources/AtlasUI/AtlasVaultProductionCompositionHarness.swift"), encoding: .utf8)
    XCTAssertTrue(root.contains("AtlasVaultRecordsView"))
    XCTAssertTrue(harness.contains("AtlasVaultRecordsContext"))
    XCTAssertTrue(harness.contains("privateMutationHost.applyPrivateMutation"))
  }
  func testProductionAllFamiliesCRUDUsesSignedOutboxAndReopens() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    let runtime = fixture.runtime()
    try await runtime.activate(.init(vaultID: fixture.vaultID))
    let payloads = fixture.payloads()
    _ = try await runtime.apply(
      .init(
        expectedVaultID: fixture.vaultID,
        mutations: .init(creates: payloads.map { .init(payload: $0, keyID: "local") })))
    let first = try await runtime.privateState().state
    XCTAssertEqual(fixture.counts(first), [1, 1, 1, 1, 1, 0])
    let pending = try epoch.pendingOperations()
    XCTAssertEqual(pending.count, 5)
    XCTAssertTrue(
      pending.allSatisfy { $0.envelope.keyEpoch == 3 && $0.envelope.signatureBase64.count == 88 })
    for op in pending {
      let body = try JSONSerialization.jsonObject(with: epoch.open(op.envelope)) as! [String: Any]
      XCTAssertEqual(
        Set(body.keys),
        [
          "format", "version", "operation_id", "author_device_id", "author_sequence",
          "lamport", "object_id", "revision", "parent_revision", "tombstone", "payload",
        ])
      XCTAssertTrue(body["operation_id"] as? String == op.operationID)
    }
    let metadata = fixture.metadata(first)
    _ = try await runtime.apply(
      .init(
        expectedVaultID: fixture.vaultID,
        mutations: .init(
          updates:
            zip(metadata, payloads).map {
              .init(
                recordID: $0.0.id, currentRevision: $0.0.revision, payload: $0.1, keyID: "local")
            })))
    let updated = try await runtime.privateState().state
    XCTAssertEqual(fixture.counts(updated), [1, 1, 1, 1, 1, 0])
    XCTAssertTrue(
      zip(metadata, fixture.metadata(updated)).allSatisfy { $0.revision != $1.revision })
    await runtime.lock()
    let reopened = fixture.runtime()
    try await reopened.activate(.init(vaultID: fixture.vaultID))
    let restored = try await reopened.privateState().state
    XCTAssertTrue(restored == updated)
    _ = try await reopened.apply(
      .init(
        expectedVaultID: fixture.vaultID,
        mutations: .init(
          deletes:
            fixture.metadata(restored).map {
              .init(recordID: $0.id, currentRevision: $0.revision, keyID: "local")
            })))
    XCTAssertEqual(try epoch.pendingOperations().count, 15)
    XCTAssertEqual(try fixture.counts(epoch.runtimeState()), [0, 0, 0, 0, 0, 5])
    XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.storeURL.path))
    for name in ["activation", "activation-recovery"] {
      let url = fixture.epochURL.appendingPathComponent(name)
      if FileManager.default.fileExists(atPath: url.path) {
        XCTAssertFalse(try String(contentsOf: url, encoding: .utf8).contains("synthetic-runtime"))
      }
    }
    await reopened.lock()
  }

  func testMalformedOrMissingIdentityNeverFallsBackToLegacy() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    try fixture.createLegacy()
    let before = try Data(contentsOf: fixture.storeURL)
    _ = fixture.keychain.add(
      .init(
        service: AtlasKeychainRuntimeBindingStore<RuntimeIntegrationKeychain>.service,
        account: fixture.vaultID, valueData: Data("{}".utf8),
        accessibility: .afterFirstUnlockThisDeviceOnly))
    do {
      try await fixture.runtime().activate(.init(vaultID: fixture.vaultID))
      XCTFail("Invalid binding accepted")
    } catch {}
    XCTAssertTrue(try Data(contentsOf: fixture.storeURL) == before)
  }

  func testAtomicAbortAndPublicationRecoveryDoNotSplitOutboxFromProjection() throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    let before = try Data(contentsOf: epoch.file.fileURL)
    let mutation = AtlasVaultMutationSet(creates: [.init(payload: fixture.note(), keyID: "local")])
    XCTAssertThrowsError(
      try epoch.commitRuntimeMutationsForTesting(
        mutation, signingKey: fixture.signer(),
        beforeReplace: { throw RuntimeTestFailure.stop }))
    XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == before)
    XCTAssertTrue(try epoch.pendingOperations().isEmpty)
    XCTAssertTrue(try epoch.runtimeState().applicationNotes.isEmpty)
    try epoch.file.enable()
    XCTAssertThrowsError(
      try epoch.commitRuntimeMutationsForTesting(
        mutation, signingKey: fixture.signer(),
        afterRecord: { throw RuntimeTestFailure.stop }))
    XCTAssertThrowsError(try epoch.runtimeState())
    try epoch.recoverPublication()
    XCTAssertEqual(try epoch.observation()["status"] as? String, "CATCH_UP_PENDING")
    let s = try epoch.load()
    let components = try epoch.map(s["components"])
    XCTAssertEqual(try epoch.rows(epoch.map(components["runtime"])["operations"]).count, 1)
    XCTAssertEqual(try epoch.pendingOperations().count, 1)
  }

  func testEveryP7FenceBlocksAlreadyUnlockedFacadeWithoutDiskMutation() async throws {
    for fence in [
      "ACTIVATION_PENDING", "CATCH_UP_PENDING", "RECOVERY_PENDING", "CLEANUP_PENDING", "REVOKED",
    ] {
      let fixture = try RuntimeIntegrationFixture()
      defer { fixture.remove() }
      let epoch = try fixture.enroll(device: fence == "REVOKED" ? 2 : 0)
      let runtime = fixture.runtime()
      try await runtime.activate(.init(vaultID: fixture.vaultID))
      let record = try fixture.vector()["record"] as! [String: Any]
      let proof = record["proof"] as! [String: Any]
      switch fence {
      case "ACTIVATION_PENDING": try epoch.beginActivation(proof)
      case "CATCH_UP_PENDING":
        XCTAssertThrowsError(
          try epoch.catchUp(
            [], currentActivationID: "test", agreementPrivateKey: Data(repeating: 20, count: 32)))
      case "RECOVERY_PENDING":
        var view = try fixture.vector()["initial_view"] as! [String: Any]
        view.removeValue(forKey: "root")
        view.removeValue(forKey: "signature_b64")
        view["collection_root"] = String(repeating: "de", count: 32)
        XCTAssertThrowsError(
          try epoch.compareEvidence([
            AtlasVaultAuthenticatedStateView.sign(view, signingKey: fixture.signer())
          ]))
      case "CLEANUP_PENDING":
        _ = try epoch.acceptRotation(
          proof, acceptedRecord: record, agreementPrivateKey: Data(repeating: 20, count: 32))
        XCTAssertThrowsError(
          try epoch.cleanupEpochsForTesting(
            retainEpochs: [3, 4], deleteEpoch: { _ in },
            containsEpoch: { _ in false }, checkpoint: { _ in throw RuntimeTestFailure.stop }))
      default:
        XCTAssertThrowsError(
          try epoch.acceptRotation(
            proof, acceptedRecord: record, agreementPrivateKey: Data(repeating: 22, count: 32)))
      }
      XCTAssertEqual(try epoch.observation()["status"] as? String, fence)
      let before = try Data(contentsOf: epoch.file.fileURL)
      do {
        _ = try await runtime.apply(
          .init(
            expectedVaultID: fixture.vaultID,
            mutations: .init(creates: [.init(payload: fixture.note(), keyID: "local")])))
        XCTFail("Fenced write accepted")
      } catch {}
      XCTAssertTrue(try Data(contentsOf: epoch.file.fileURL) == before)
      XCTAssertTrue(try epoch.pendingOperations().isEmpty)
      do {
        _ = try await runtime.privateState()
        XCTFail("Fenced state exposed")
      } catch {}
      do {
        try await fixture.runtime().activate(.init(vaultID: fixture.vaultID))
        XCTFail("Fenced activation accepted")
      } catch {}
      await runtime.lock()
    }
  }

  func testMetadataMirrorsRejectTamperingBeforeP5IngestAndConverge() throws {
    let first = try RuntimeIntegrationFixture()
    let second = try RuntimeIntegrationFixture()
    defer {
      first.remove()
      second.remove()
    }
    let sender = try first.enroll()
    let receiver = try second.enroll(device: 1)
    _ = try sender.commitRuntimeMutations(
      .init(creates: [.init(payload: first.note(), keyID: "local")]), signingKey: first.signer())
    let op = try XCTUnwrap(sender.pendingOperations().first)
    let before = try Data(contentsOf: receiver.file.fileURL)
    for field in [
      "operation_id", "author_device_id", "author_sequence", "lamport", "parent_revision",
      "tombstone",
    ] {
      var raw = op.jsonObject
      var envelope = op.envelope.jsonObject
      switch field {
      case "parent_revision":
        envelope[field] = "substituted"
        raw["envelope"] = envelope
      case "tombstone":
        envelope[field] = true
        raw["envelope"] = envelope
        raw["operation_type"] = "delete"
      case "author_sequence", "lamport": raw[field] = 2
      case "operation_id": raw[field] = UUID().uuidString.lowercased()
      default: raw[field] = "substituted"
      }
      XCTAssertThrowsError(
        try first.admit([AtlasVaultEncryptedPatchOperation(jsonObject: raw)], to: receiver))
      XCTAssertTrue(try Data(contentsOf: receiver.file.fileURL) == before)
    }
    try first.admit([op], to: receiver)
    try first.admit([op], to: receiver)
    XCTAssertTrue(try receiver.runtimeState() == sender.runtimeState())
    XCTAssertTrue(try receiver.pendingOperations().isEmpty)
  }

  func testRotationPreservesProjectionAndCleanupRetainsRequiredKeys() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    let epoch = try fixture.enroll()
    let runtime = fixture.runtime()
    try await runtime.activate(.init(vaultID: fixture.vaultID))
    _ = try await runtime.apply(
      .init(
        expectedVaultID: fixture.vaultID,
        mutations: .init(creates: [.init(payload: fixture.note(), keyID: "local")])))
    for op in try epoch.pendingOperations() { try epoch.confirmRemoteAcceptance(op.operationID) }
    let record = try fixture.vector()["record"] as! [String: Any]
    _ = try epoch.acceptRotation(
      record["proof"] as! [String: Any], acceptedRecord: record,
      agreementPrivateKey: Data(repeating: 20, count: 32))
    let note = try await runtime.privateState().state.applicationNotes[0]
    _ = try await runtime.apply(
      .init(
        expectedVaultID: fixture.vaultID,
        mutations: .init(updates: [
          .init(
            recordID: note.metadata.id, currentRevision: note.metadata.revision,
            payload: fixture.note(), keyID: "local")
        ])))
    XCTAssertEqual(try epoch.pendingOperations().first?.envelope.keyEpoch, 4)
    var deletions = 0
    XCTAssertThrowsError(
      try epoch.cleanupEpochs(
        retainEpochs: [4], deleteEpoch: { _ in deletions += 1 }, containsEpoch: { _ in false }))
    XCTAssertEqual(deletions, 0)
    await runtime.lock()
    let reopened = fixture.runtime()
    try await reopened.activate(.init(vaultID: fixture.vaultID))
    let state = try await reopened.privateState().state
    XCTAssertEqual(state.applicationNotes.count, 1)
    await reopened.lock()
  }

  func testProductionWithoutEnrollmentRestoresLegacyReadOnly() async throws {
    let fixture = try RuntimeIntegrationFixture()
    defer { fixture.remove() }
    try fixture.createLegacy()
    let before = try Data(contentsOf: fixture.storeURL)
    let runtime = fixture.runtime()
    try await runtime.activate(.init(vaultID: fixture.vaultID))
    let snapshot = try await runtime.privateState()
    XCTAssertEqual(snapshot.state.applicationNotes.count, 1)
    XCTAssertTrue(snapshot.state.isReadOnly)
    do {
      _ = try await runtime.apply(
        .init(
          expectedVaultID: fixture.vaultID,
          mutations: .init(creates: [.init(payload: fixture.note(), keyID: "local")])
        ))
      XCTFail("Unenrolled production runtime must not write")
    } catch {}
    XCTAssertTrue(try Data(contentsOf: fixture.storeURL) == before)
    await runtime.lock()
  }
}

private struct RuntimeIntegrationDirectory: AtlasApplicationSupportDirectoryLocating {
  let root: URL
  func applicationSupportDirectory() throws -> URL { root }
}

private final class RuntimeIntegrationKeychain: AtlasKeychainClient, @unchecked Sendable {
  private let lock = NSLock()
  private var items: [String: Data] = [:]
  func add(_ item: AtlasKeychainItem) -> OSStatus {
    lock.withLock {
      let key = item.service + ":" + item.account
      guard items[key] == nil else { return errSecDuplicateItem }
      items[key] = item.valueData
      return errSecSuccess
    }
  }
  func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
    lock.withLock {
      let data = items[query.service + ":" + query.account]
      return .init(status: data == nil ? errSecItemNotFound : errSecSuccess, valueData: data)
    }
  }
  func update(_ query: AtlasKeychainQuery, with attributes: AtlasKeychainUpdate) -> OSStatus {
    lock.withLock {
      items[query.service + ":" + query.account] = attributes.valueData
      return errSecSuccess
    }
  }
  func delete(_ query: AtlasKeychainQuery) -> OSStatus {
    lock.withLock {
      items.removeValue(forKey: query.service + ":" + query.account)
      return errSecSuccess
    }
  }
}

private struct RuntimeIntegrationFixture {
  let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  let vaultID = "vault-c26"
  let storageKey = Data(repeating: 50, count: 32)
  let legacyKey = Data(repeating: 51, count: 32)
  let keychain = RuntimeIntegrationKeychain()
  var storeURL: URL {
    root.appendingPathComponent("Atlas/Vaults/\(vaultID)/atlasvault-local-store.json")
  }
  var epochURL: URL { storeURL.deletingLastPathComponent().appendingPathComponent("epoch") }
  func signer() throws -> Curve25519.Signing.PrivateKey {
    try .init(rawRepresentation: Data(repeating: 10, count: 32))
  }
  func activateEpoch(_ epoch: AtlasVaultEpochVault, device: Int = 0) throws {
    let record = try vector()["record"] as! [String: Any]
    _ = try epoch.acceptRotation(
      record["proof"] as! [String: Any], acceptedRecord: record,
      agreementPrivateKey: Data(repeating: UInt8(20 + device), count: 32))
  }
  func admit(_ operations: [AtlasVaultEncryptedPatchOperation], to receiver: AtlasVaultEpochVault)
    throws
  {
    let v = try vector()
    let initial = v["initial_view"] as! [String: Any]
    let replica = try AtlasVaultDurableEncryptedConvergentReplica(
      fileURL: root.appendingPathComponent(UUID().uuidString),
      encryptionKey: storageKey, authenticationKey: storageKey, collectionID: "collection-c26")
    for operation in operations { _ = try replica.ingestRemote(operation) }
    let opaque = try AtlasVaultEpochRotation.canonical([
      "format": "atlasvault-guarded-collection", "version": 1,
      "route": "patch", "records": replica.currentRecords().map(\.jsonObject),
    ])
    let commitment = try AtlasVaultSignedStateCommitment.sign(
      opaque, collectionID: "collection-c26", sequence: 2,
      previousRoot: initial["collection_root"] as! String, signingKey: signer())
    var view = initial
    view.removeValue(forKey: "root")
    view.removeValue(forKey: "signature_b64")
    view["sequence"] = 2
    view["previous_root"] = initial["root"]
    view["previous_registry_root"] = initial["registry_root"]
    view["collection_root"] = commitment.root
    try receiver.ingestRuntimePage(
      view: AtlasVaultAuthenticatedStateView.sign(view, signingKey: signer()),
      registry: v["initial_registry"] as! [[String: Any]], commitment: commitment.jsonObject,
      opaqueState: opaque, operations: operations)
  }
  func vector() throws -> [String: Any] {
    var repository = URL(fileURLWithPath: #filePath)
    for _ in 0..<5 { repository.deleteLastPathComponent() }
    return try JSONSerialization.jsonObject(
      with: Data(
        contentsOf: repository.appendingPathComponent(
          "contracts/sync/test_vectors/atlasvault_activation_v1.json"))) as! [String: Any]
  }
  func enroll(device: Int = 0, provision: Bool = true) throws -> AtlasVaultEpochVault {
    let v = try vector()
    let proof = (v["record"] as! [String: Any])["proof"] as! [String: Any]
    let plan = proof["plan"] as! [String: Any]
    let epoch = try AtlasVaultEpochVault(
      directory: epochURL, storageKey: storageKey,
      deviceID: (v["device_ids"] as! [String])[device],
      registry: proof["registry"] as! [[String: Any]],
      accountID: plan["account_id"] as! String, vaultID: vaultID, keyEpoch: 3,
      stateRoot: plan["state_root"] as! String)
    let history = try AtlasVaultGuardedSyncState(
      fileURL: root.appendingPathComponent("seed-history"),
      encryptionKey: Data(repeating: 60, count: 32), accountID: plan["account_id"] as! String,
      vaultID: vaultID, collectionID: "collection-c26", keyEpoch: 3,
      trustedSigner: signer().publicKey.rawRepresentation)
    try history.initialize()
    _ = try history.ingest(
      view: v["initial_view"] as! [String: Any],
      registry: v["initial_registry"] as! [[String: Any]],
      collection: v["initial_collection"] as! [String: Any],
      opaqueState: Data(base64Encoded: v["opaque_state_b64"] as! String)!)
    try epoch.initialize(keys: [3: Data(repeating: 30, count: 32)], history: history)
    let identity = try AtlasVaultDeviceIdentity(
      signingPrivateSeed: Data(repeating: UInt8(10 + device), count: 32),
      agreementPrivateKey: Data(repeating: UInt8(20 + device), count: 32),
      createdAt: "2026-01-01T00:00:00Z", keyEpoch: 3)
    try AtlasKeychainDeviceIdentityStore(client: keychain).createPrimaryIdentity(
      identity.secretBundle().canonicalData())
    if provision {
      try AtlasKeychainRuntimeBindingStore(client: keychain).createAuthenticatedBinding(
        from: epoch,
        authenticatedHistoryRegistry: v["initial_registry"] as! [[String: Any]])
    }
    return epoch
  }
  func counts(_ state: AtlasVaultHydratedState) -> [Int] {
    [
      state.savedSearches.count, state.savedJobs.count, state.applicationNotes.count,
      state.profileSnippets.count, state.draftMetadata.count, state.tombstones.count,
    ]
  }
  func metadata(_ state: AtlasVaultHydratedState) -> [AtlasHydratedRecordMetadata] {
    state.savedSearches.map(\.metadata) + state.savedJobs.map(\.metadata)
      + state.applicationNotes.map(\.metadata)
      + state.profileSnippets.map(\.metadata) + state.draftMetadata.map(\.metadata)
  }
  func payloads() -> [AtlasVaultSavePayload] {
    let t = "2026-01-01T00:00:00Z"
    return [
      .savedSearch(
        .init(
          type: .savedSearch,
          payload: .init(name: "synthetic-runtime-search", summary: "test", request: .init()),
          clientCreatedAt: t, clientUpdatedAt: t)),
      .savedJob(
        .init(
          type: .savedJob, payload: .init(jobKey: "synthetic-runtime-job", status: "saved"),
          clientCreatedAt: t, clientUpdatedAt: t)),
      note(),
      .profileSnippet(
        .init(
          type: .profileSnippet,
          payload: .init(
            title: "synthetic-runtime-profile", body: "test", createdAt: t, updatedAt: t),
          clientCreatedAt: t, clientUpdatedAt: t)),
      .draftMetadata(
        .init(
          type: .draftMetadata,
          payload: .init(
            targetSystem: "OTHER", documentType: "cv",
            generatedDocumentReference: "synthetic-runtime-reference", draftStatus: "draft",
            generatedAt: t), clientCreatedAt: t, clientUpdatedAt: t)),
    ]
  }
  init() throws {
    try AtlasKeychainVaultKeyStore(client: keychain).saveVaultKey(legacyKey, for: vaultID)
  }
  func remove() { try? FileManager.default.removeItem(at: root) }
  func runtime() -> AtlasVaultRuntimeFacade {
    AtlasVaultRuntimeFacade.runtimeServices(
      AtlasVaultRuntimeFactory.production(
        directoryLocator: RuntimeIntegrationDirectory(root: root), keychainClient: keychain,
        atomicFileSystemClient: AtlasFoundationAtomicFileSystemClient()
      ))
  }
  func note() -> AtlasVaultSavePayload {
    .applicationNote(
      .init(
        type: .applicationNote,
        payload: .init(
          body: "synthetic-runtime-note", noteKind: "general",
          createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z"),
        clientCreatedAt: "2026-01-01T00:00:00Z", clientUpdatedAt: "2026-01-01T00:00:00Z"
      ))
  }
  func createLegacy() throws {
    let session = try AtlasVaultUnlockedSession(vaultID: vaultID, vaultKey: legacyKey)
    let records = try AtlasVaultRecordSaver().save(
      mutations: .init(creates: [.init(payload: note(), keyID: "local")]), session: session
    )
    try FileManager.default.createDirectory(
      at: storeURL.deletingLastPathComponent(), withIntermediateDirectories: true)
    try AtlasVaultLocalStoreIO.write(
      .init(
        storeID: "test-store", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
        vaultMetadata: AtlasVaultVersionedWrappedKeyMetadata(
          vaultID: vaultID,
          crypto: AtlasVaultKeyWrapCryptoSuite(), keyWraps: []
        ).localStoreMetadata(), records: records
      ), to: storeURL)
  }
}

private enum RuntimeTestFailure: Error { case stop }

private actor RuntimeIntegrationTime: AtlasVaultLifecycleClock, AtlasVaultLifecycleSleeper {
  func now() async -> Duration { .zero }
  func sleep(until deadline: Duration) async throws { throw CancellationError() }
}
private actor RuntimeIntegrationLifecycle: AtlasVaultPlatformLifecycleEventSourcing {
  func subscription() async -> AtlasVaultPlatformLifecycleEventSubscription {
    let pair = AsyncStream<AtlasVaultPlatformLifecycleEventDelivery>.makeStream()
    return .init(
      bootstrapEvents: [.protectedDataBecameAvailable, .didBecomeActive], events: pair.stream,
      requestReadinessBoundary: { pair.continuation.yield(.readinessBoundary($0)) })
  }
}
private struct RuntimeIntegrationSnapshot: AtlasPublicSnapshotRestoring {
  func restore() async throws(AtlasPublicSnapshotRestoreError) -> AtlasProductionPublicSnapshot? {
    nil
  }
}
private actor RuntimeIntegrationPublicJobs: AtlasPublicJobSearching {
  func health() async throws(AtlasPublicJobServiceError) -> AtlasPublicServiceHealth {
    do {
      return try .init(
        availability: .available, openJobCount: 0, enabledSourceCount: 1, lastSyncAt: nil)
    } catch { throw .invalidResponse }
  }
  func search(_ request: AtlasPublicJobSearchRequest) async throws(AtlasPublicJobServiceError)
    -> AtlasPublicJobSearchResult
  {
    do { return try .init(jobs: [], total: 0, limit: request.limit, offset: request.offset) } catch
    { throw .invalidResponse }
  }
  func sources() async throws(AtlasPublicJobServiceError) -> [AtlasPublicSourceStatus] { [] }
  func updates() async throws(AtlasPublicJobServiceError) -> [AtlasPublicUpdateStatus] { [] }
  func detail(for reference: AtlasPublicJobReference) async throws(AtlasPublicJobServiceError)
    -> AtlasPublicJobDetailResult
  { throw .unavailable }
}
