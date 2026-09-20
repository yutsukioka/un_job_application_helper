import SwiftUI

@MainActor
public struct AtlasLockedShellUnlockFlowView: View {
    private let state: AtlasLockedShellUnlockFlowState
    private let publicShellActions: AtlasLockedPublicShellActions
    private let unlockActions: AtlasExplicitUnlockViewActions

    public init(
        state: AtlasLockedShellUnlockFlowState,
        publicShellActions: AtlasLockedPublicShellActions,
        unlockActions: AtlasExplicitUnlockViewActions
    ) {
        self.state = state
        self.publicShellActions = publicShellActions
        self.unlockActions = unlockActions
    }

    @ViewBuilder
    public var body: some View {
        switch state.mode {
        case .lockedPublic:
            AtlasLockedPublicShellView(
                model: state.publicShell,
                actions: publicShellActions
            )
        case .unlockPanel:
            if let unlockPanelState = state.unlockPanelState {
                AtlasExplicitUnlockView(
                    state: unlockPanelState,
                    actions: unlockActions
                )
            }
        case .unlockedTransition:
            VStack(spacing: 12) {
                Image(systemName: "lock.open.fill")
                    .accessibilityHidden(true)
                Text("Unlock complete")
                    .font(.headline)
                Text("Waiting for the host to continue.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .padding(20)
            .frame(minWidth: 320, minHeight: 240)
            .accessibilityElement(children: .combine)
        }
    }
}
