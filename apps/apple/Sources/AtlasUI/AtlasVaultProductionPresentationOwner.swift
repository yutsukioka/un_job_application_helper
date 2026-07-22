import Combine
import Foundation

@MainActor
public final class AtlasVaultProductionPresentationOwner:
    ObservableObject,
    AtlasVaultProductionPresentationOwnerResetting,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum GenerationAuthority {
        case none
        case established(AtlasVaultProductionHostGeneration)
        case superseded(AtlasVaultProductionHostGeneration)
    }

    @Published public private(set) var flowState:
        AtlasLockedShellUnlockFlowState

    private var generationAuthority: GenerationAuthority = .none
    private var ownerRevision: UInt64 = 0
    private let beforeResetCommit: @MainActor @Sendable () async -> Void

    public init() {
        flowState = Self.initialFlowState
        beforeResetCommit = {}
    }

    init(
        beforeResetCommit: @escaping @MainActor @Sendable () async -> Void
    ) {
        flowState = Self.initialFlowState
        self.beforeResetCommit = beforeResetCommit
    }

    public func supersedePresentationGeneration(
        _ generation: AtlasVaultProductionHostGeneration
    ) async {
        ownerRevision &+= 1
        generationAuthority = .superseded(generation)
    }

    public func resetPresentation(
        to state: AtlasLockedShellUnlockFlowState,
        generation: AtlasVaultProductionHostGeneration
    ) async -> Bool {
        let requiresExactSupersededGeneration: Bool
        switch generationAuthority {
        case .none, .established:
            requiresExactSupersededGeneration = false
        case let .superseded(requiredGeneration):
            guard generation == requiredGeneration else {
                return false
            }
            requiresExactSupersededGeneration = true
        }

        ownerRevision &+= 1
        let resetRevision = ownerRevision
        if !requiresExactSupersededGeneration {
            generationAuthority = .established(generation)
        }

        await beforeResetCommit()

        guard ownerRevision == resetRevision else {
            return false
        }
        switch generationAuthority {
        case let .established(currentGeneration):
            guard !requiresExactSupersededGeneration,
                  currentGeneration == generation else {
                return false
            }
        case let .superseded(requiredGeneration):
            guard requiresExactSupersededGeneration,
                  requiredGeneration == generation else {
                return false
            }
        case .none:
            return false
        }

        flowState = state
        generationAuthority = .established(generation)
        return true
    }

    public nonisolated var description: String {
        "AtlasVaultProductionPresentationOwner(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private static var initialFlowState: AtlasLockedShellUnlockFlowState {
        AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                vaultStatus: .locked,
                serviceStatus: .unavailable,
                cacheFreshness: .unavailable,
                searchQuery: "",
                publicJobs: [],
                isSearching: false,
                canRequestUnlock: false
            ),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: nil,
                status: .locked
            ),
            isUnlockPanelPresented: false
        )
    }
}
