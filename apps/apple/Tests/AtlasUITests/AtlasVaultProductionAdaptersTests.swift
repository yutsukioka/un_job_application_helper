import Security
import XCTest
@testable import AtlasUI

final class AtlasVaultProductionAdaptersTests: XCTestCase {
    func testPhaseTypesDefineTheThreeConcreteDependencyBoundaries() {
        _ = AtlasAPIClientPublicJobAdapter.self
        _ = AtlasApplicationSupportPublicSnapshotRestorer.self
        _ = AtlasKeychainVaultSelectionRegistry<RedCheckpointKeychainClient>.self
    }
}

private struct RedCheckpointKeychainClient: AtlasKeychainClient {
    func add(_ item: AtlasKeychainItem) -> OSStatus {
        errSecSuccess
    }

    func copyMatching(
        _ query: AtlasKeychainQuery
    ) -> AtlasKeychainCopyResult {
        AtlasKeychainCopyResult(status: errSecItemNotFound, valueData: nil)
    }

    func update(
        _ query: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        errSecSuccess
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        errSecSuccess
    }
}
