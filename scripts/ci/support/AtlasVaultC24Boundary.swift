import Foundation

// This standalone C24 proof compiles the pre-enrollment runtime subset. The
// production P8 implementation is compiled and exercised by the Swift package
// suites. If a C24 case reaches an excluded P8 path, fail rather than model it.
enum AtlasVaultBootstrapError: Error { case rejected }
enum AtlasVaultRuntimeSaveFailure: Error { case integrityUnknown }
enum AtlasVaultActivationFailure: Error { case unavailable }

public final class AtlasVaultAnchoredSyncState {
  static func publicationReader(_ owner: AtlasVaultEpochVault) throws -> AtlasVaultGuardedSyncState {
    throw AtlasVaultBootstrapError.rejected
  }
  func publicationOrigin() throws -> [String: Any] { throw AtlasVaultBootstrapError.rejected }
  func publicationState() throws -> AtlasVaultGuardedSyncState {
    throw AtlasVaultBootstrapError.rejected
  }
  static func retainedAuthor(
    _ owner: AtlasVaultEpochVault, state: [String: Any],
    envelope: AtlasVaultOpaqueCiphertextEnvelope
  ) throws -> [[String: Any]] { throw AtlasVaultBootstrapError.rejected }
}

extension AtlasVaultEpochVault {
  func stageEnrollmentRuntime(_ initial: [String: Any], projection: Data) throws -> [String: Any] {
    throw AtlasVaultBootstrapError.rejected
  }

  func runtimeRequiredEpochs(_ s: [String: Any]) throws -> Set<Int> {
    let components = try map(s["components"])
    guard let raw = components["runtime"] else { return [] }
    let projection = try map(raw)
    guard try rows(projection["snapshots"]).isEmpty else { throw AtlasVaultRotationError.rejected }
    let operations = try rows(projection["operations"])
    guard operations.count <= 65_536 else { throw AtlasVaultRotationError.rejected }
    return Set(try operations.map {
      Int(try AtlasVaultEncryptedPatchOperation(jsonObject: $0).envelope.keyEpoch)
    })
  }
}
