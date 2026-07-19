import XCTest
@testable import AtlasUI

final class AtlasVaultProductionHostFactoryTests: XCTestCase {
    func testPhaseContractsAndFactoryAreAvailable() {
        _ = AtlasPublicJobSearching.self
        _ = AtlasPublicSnapshotRestoring.self
        _ = AtlasVaultIDSelecting.self
        _ = AtlasVaultProductionHosting.self
        _ = AtlasVaultProductionHostDependencies.self
        _ = AtlasVaultProductionHostFactory.self
    }
}
