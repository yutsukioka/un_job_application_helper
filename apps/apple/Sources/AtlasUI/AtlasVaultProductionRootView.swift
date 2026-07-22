import SwiftUI

@MainActor
public struct AtlasVaultProductionRootView: View {
    @ObservedObject private var owner:
        AtlasVaultProductionPresentationOwner
    private let publicShellActions: AtlasLockedPublicShellActions
    private let unlockActions: AtlasExplicitUnlockViewActions

    public init(
        owner: AtlasVaultProductionPresentationOwner,
        publicShellActions: AtlasLockedPublicShellActions,
        unlockActions: AtlasExplicitUnlockViewActions
    ) {
        self.owner = owner
        self.publicShellActions = publicShellActions
        self.unlockActions = unlockActions
    }

    public var body: some View {
        AtlasLockedShellUnlockFlowView(
            state: owner.flowState,
            publicShellActions: publicShellActions,
            unlockActions: unlockActions
        )
    }
}
