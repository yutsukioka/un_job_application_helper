import SwiftUI

@MainActor
public struct AtlasVaultProductionRootView: View {
    @ObservedObject private var owner:
        AtlasVaultProductionPresentationOwner
    private let publicShellActions: AtlasLockedPublicShellActions
    private let unlockActions: AtlasExplicitUnlockViewActions
    private let creationContext: AtlasLocalVaultCreationContext?
    private let recoveryExportContext: AtlasVaultRecoveryExportContext?

    public init(
        owner: AtlasVaultProductionPresentationOwner,
        publicShellActions: AtlasLockedPublicShellActions,
        unlockActions: AtlasExplicitUnlockViewActions
    ) {
        self.owner = owner
        self.publicShellActions = publicShellActions
        self.unlockActions = unlockActions
        creationContext = nil
        recoveryExportContext = nil
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
        recoveryExportContext = nil
    }

    public init(
        owner: AtlasVaultProductionPresentationOwner,
        publicShellActions: AtlasLockedPublicShellActions,
        unlockActions: AtlasExplicitUnlockViewActions,
        recoveryExportContext: AtlasVaultRecoveryExportContext
    ) {
        self.owner = owner
        self.publicShellActions = publicShellActions
        self.unlockActions = unlockActions
        creationContext = nil
        self.recoveryExportContext = recoveryExportContext
    }

    public init(
        owner: AtlasVaultProductionPresentationOwner,
        publicShellActions: AtlasLockedPublicShellActions,
        unlockActions: AtlasExplicitUnlockViewActions,
        creationContext: AtlasLocalVaultCreationContext,
        recoveryExportContext: AtlasVaultRecoveryExportContext
    ) {
        self.owner = owner
        self.publicShellActions = publicShellActions
        self.unlockActions = unlockActions
        self.creationContext = creationContext
        self.recoveryExportContext = recoveryExportContext
    }

    public var body: some View {
        AtlasVaultProductionRootContent(
            state: owner.flowState,
            publicShellActions: publicShellActions,
            unlockActions: unlockActions,
            creationContext: creationContext,
            recoveryExportContext: recoveryExportContext
        )
    }
}

@MainActor
private struct AtlasVaultProductionRootContent: View {
    let state: AtlasLockedShellUnlockFlowState
    let publicShellActions: AtlasLockedPublicShellActions
    let unlockActions: AtlasExplicitUnlockViewActions
    let creationContext: AtlasLocalVaultCreationContext?
    let recoveryExportContext: AtlasVaultRecoveryExportContext?

    @ViewBuilder
    var body: some View {
        if let recoveryExportContext {
            AtlasVaultRecoveryEnabledRoot(
                flowState: state,
                publicShellActions: publicShellActions,
                unlockActions: unlockActions,
                creationContext: creationContext,
                recoveryOwner: recoveryExportContext.owner,
                recoveryActions: recoveryExportContext.actions
            )
        } else if let creationContext {
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
private struct AtlasVaultRecoveryEnabledRoot: View {
    let flowState: AtlasLockedShellUnlockFlowState
    let publicShellActions: AtlasLockedPublicShellActions
    let unlockActions: AtlasExplicitUnlockViewActions
    let creationContext: AtlasLocalVaultCreationContext?
    @ObservedObject var recoveryOwner:
        AtlasVaultRecoveryExportPresentationOwner
    let recoveryActions: AtlasVaultRecoveryExportActions
    @State private var isRecoveryExportPresented = false
    @State private var recoveryPresentationClaim =
        AtlasVaultRecoveryExportPresentationClaim()

    var body: some View {
        baseFlow
            .safeAreaInset(edge: .bottom) {
                if showsRecoveryExportAction {
                    HStack {
                        Spacer()
                        Button {
                            presentRecoveryExport()
                        } label: {
                            Label(
                                "Recovery & Encrypted Export",
                                systemImage: "lock.doc"
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
                        isRecoveryExportPresented
                            && recoveryActions.ownsPresentation(
                                recoveryPresentationClaim
                            )
                            && recoveryOwner.presentation != .hidden
                    },
                    set: { isPresented in
                        isRecoveryExportPresented = isPresented
                        if !isPresented,
                           recoveryActions.releasePresentation(
                               recoveryPresentationClaim
                           ) {
                            recoveryActions.dismiss()
                        }
                    }
                )
            ) {
                AtlasVaultRecoveryExportView(
                    owner: recoveryOwner,
                    actions: recoveryActions
                )
                .interactiveDismissDisabled(
                    recoveryOwner.presentation == .generating
                        || recoveryOwner.presentation == .verifying
                        || recoveryOwner.presentation == .resetting
                )
            }
            .onChange(of: recoveryOwner.presentation) {
                _, presentation in
                if presentation == .hidden {
                    isRecoveryExportPresented = false
                }
            }
    }

    @ViewBuilder
    private var baseFlow: some View {
        if let creationContext {
            AtlasVaultCreationEnabledRoot(
                flowState: flowState,
                publicShellActions: publicShellActions,
                unlockActions: unlockActions,
                creationOwner: creationContext.owner,
                creationActions: creationContext.actions
            )
        } else {
            AtlasLockedShellUnlockFlowView(
                state: flowState,
                publicShellActions: publicShellActions,
                unlockActions: unlockActions
            )
        }
    }

    private func presentRecoveryExport() {
        if recoveryOwner.presentation == .hidden {
            recoveryActions.present()
        }
        guard recoveryActions.claimPresentation(
            recoveryPresentationClaim
        ) else {
            return
        }
        isRecoveryExportPresented = true
    }

    private var showsRecoveryExportAction: Bool {
        flowState.mode == .unlockedTransition
            && (
                !isRecoveryExportPresented
                    || !recoveryActions.ownsPresentation(
                        recoveryPresentationClaim
                    )
            )
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
    @State private var isCreationPresented = false
    @State private var presentationClaim =
        AtlasLocalVaultCreationPresentationClaim()

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
                        presentCreation()
                    } label: {
                        Label(
                            creationActionTitle,
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
                    isCreationPresented
                        && creationActions.ownsPresentation(
                            presentationClaim
                        )
                        && creationOwner.presentation != .hidden
                },
                set: { isPresented in
                    isCreationPresented = isPresented
                    if !isPresented,
                       creationActions.releasePresentation(
                           presentationClaim
                       ) {
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
        .onChange(of: creationOwner.presentation) { _, presentation in
            if presentation == .hidden {
                isCreationPresented = false
            }
        }
    }

    private func presentCreation() {
        if creationOwner.presentation == .hidden {
            creationActions.present()
        }
        guard creationActions.claimPresentation(
            presentationClaim
        ) else {
            return
        }
        isCreationPresented = true
    }

    private var showsCreateAction: Bool {
        flowState.mode == .lockedPublic
            && flowState.publicShell.vaultStatus == .noVault
            && (
                !isCreationPresented
                    || !creationActions.ownsPresentation(
                        presentationClaim
                    )
            )
    }

    private var creationActionTitle: String {
        if creationOwner.presentation == .hidden {
            "Create Local Vault"
        } else {
            "Continue Local Vault Setup"
        }
    }
}
