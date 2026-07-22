import Combine
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultProductionPresentationOwnerTests: XCTestCase {
    func testIntendedOwnerSurfaceIsObservableAndImplementsResetContract() async {
        let owner = AtlasVaultProductionPresentationOwner()

        requireObservableObject(owner)
        requireOwnerResetter(owner)
        XCTAssertEqual(
            owner.flowState.mode,
            AtlasLockedShellUnlockFlowMode.lockedPublic
        )
        XCTAssertFalse(owner.flowState.publicShell.canRequestUnlock)
    }

    private func requireObservableObject<T: ObservableObject>(_ value: T) {}

    private func requireOwnerResetter<Owner>(
        _ value: Owner
    ) where Owner: AtlasVaultProductionPresentationOwnerResetting {}
}
