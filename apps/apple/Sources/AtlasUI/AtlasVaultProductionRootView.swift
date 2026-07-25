import SwiftUI

@MainActor
public struct AtlasVaultProductionRootView: View {
    @ObservedObject private var owner:
        AtlasVaultProductionPresentationOwner
    private let publicShellActions: AtlasLockedPublicShellActions
    private let unlockActions: AtlasExplicitUnlockViewActions
    private let creationContext: AtlasLocalVaultCreationContext?

    public init(
        owner: AtlasVaultProductionPresentationOwner,
        publicShellActions: AtlasLockedPublicShellActions,
        unlockActions: AtlasExplicitUnlockViewActions
    ) {
        self.owner = owner
        self.publicShellActions = publicShellActions
        self.unlockActions = unlockActions
        creationContext = nil
    }

    public init(
        owner: AtlasVaultProductionPresentationOwner,
        publicShellActions: AtlasLockedPublicShellActions,
        unlockActions: AtlasExplicitUnlockViewActions,
        creationContext: AtlasLocalVaultCreationContext
    ) {
        self.owner = owner
        self.publicShellActions = publicShellActions
        self.unlockActions = unlockActions
        self.creationContext = creationContext
    }

    public var body: some View {
        AtlasVaultProductionRootContent(
            state: owner.flowState,
            publicShellActions: publicShellActions,
            unlockActions: unlockActions,
            creationContext: creationContext
        )
    }
}

@MainActor
private struct AtlasVaultProductionRootContent: View {
    let state: AtlasLockedShellUnlockFlowState
    let publicShellActions: AtlasLockedPublicShellActions
    let unlockActions: AtlasExplicitUnlockViewActions
    let creationContext: AtlasLocalVaultCreationContext?

    @ViewBuilder
    var body: some View {
        if let creationContext {
            AtlasVaultCreationEnabledRoot(
                flowState: state,
                publicShellActions: publicShellActions,
                unlockActions: unlockActions,
                creationOwner: creationContext.owner,
                creationActions: creationContext.actions
            )
        } else {
            AtlasLockedShellUnlockFlowView(
                state: state,
                publicShellActions: publicShellActions,
                unlockActions: unlockActions
            )
        }
    }
}

@MainActor
private struct AtlasVaultCreationEnabledRoot: View {
    let flowState: AtlasLockedShellUnlockFlowState
    let publicShellActions: AtlasLockedPublicShellActions
    let unlockActions: AtlasExplicitUnlockViewActions
    @ObservedObject var creationOwner:
        AtlasLocalVaultCreationPresentationOwner
    let creationActions: AtlasLocalVaultCreationActions

    var body: some View {
        AtlasLockedShellUnlockFlowView(
            state: flowState,
            publicShellActions: publicShellActions,
            unlockActions: unlockActions
        )
        .safeAreaInset(edge: .bottom) {
            if showsCreateAction {
                HStack {
                    Spacer()
                    Button {
                        creationActions.present()
                    } label: {
                        Label(
                            "Create Local Vault",
                            systemImage: "externaldrive.badge.plus"
                        )
                    }
                    .buttonStyle(.borderedProminent)
                    Spacer()
                }
                .padding(12)
                .background(.bar)
            }
        }
        .sheet(
            isPresented: Binding(
                get: {
                    creationOwner.presentation != .hidden
                },
                set: { isPresented in
                    if !isPresented {
                        creationActions.dismiss()
                    }
                }
            )
        ) {
            AtlasLocalVaultCreationView(
                owner: creationOwner,
                actions: creationActions
            )
            .interactiveDismissDisabled(
                creationOwner.presentation == .creating
            )
        }
    }

    private var showsCreateAction: Bool {
        flowState.mode == .lockedPublic
            && flowState.publicShell.vaultStatus == .noVault
            && creationOwner.presentation == .hidden
    }
}
