import Foundation

public enum AtlasLockedShellUnlockFlowMode:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case lockedPublic
    case unlockPanel
    case unlockedTransition

    public var description: String {
        switch self {
        case .lockedPublic:
            "lockedPublic"
        case .unlockPanel:
            "unlockPanel"
        case .unlockedTransition:
            "unlockedTransition"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasLockedShellUnlockFlowState:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let publicShell: AtlasLockedPublicShellModel
    public let mode: AtlasLockedShellUnlockFlowMode
    public let unlockPanelState: AtlasExplicitUnlockViewState?

    public init(
        publicShell: AtlasLockedPublicShellModel,
        unlockPresentationState: AtlasVaultUnlockPresentationState,
        isUnlockPanelPresented: Bool
    ) {
        self.publicShell = publicShell

        switch unlockPresentationState.status {
        case .unlocked:
            mode = .unlockedTransition
            unlockPanelState = nil
        case .cancelled:
            mode = .lockedPublic
            unlockPanelState = nil
        case .hostReconciliationRequired:
            mode = .unlockPanel
            unlockPanelState = AtlasExplicitUnlockViewState(
                presentationState: unlockPresentationState
            )
        case .locked,
             .ready,
             .methodUnavailable,
             .activating,
             .failed,
             .timedOut:
            if isUnlockPanelPresented {
                mode = .unlockPanel
                unlockPanelState = AtlasExplicitUnlockViewState(
                    presentationState: unlockPresentationState
                )
            } else {
                mode = .lockedPublic
                unlockPanelState = nil
            }
        }
    }

    public var description: String {
        "AtlasLockedShellUnlockFlowState(mode: \(mode), content: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}
