import XCTest
@testable import AtlasUI

final class AtlasVaultProductionHostTests: XCTestCase {
    func testPhase2D56ProductionHostTypesAreAvailable() {
        _ = AtlasVaultProductionHost.self
        _ = AtlasVaultProductionHostBuilder.self
        _ = AtlasVaultProductionUnlockPresentationControllerBuilder.self
        _ = AtlasVaultProductionPresentationPipeline.self
        _ = AtlasVaultProductionPresentationCoordinating.self
        _ = AtlasVaultProductionPresentationOwnerResetting.self
        _ = AtlasVaultProductionHostError.self
        _ = AtlasVaultProductionHostGeneration.self
    }
}
