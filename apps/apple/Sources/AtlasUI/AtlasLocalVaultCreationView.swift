import Combine
import Foundation
import SwiftUI

public enum AtlasLocalVaultCreationPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case hidden
    case ready
    case creating
    case paused
    case failed
    case durabilityVerificationRequired
    case completionPending
    case recoveryRequired

    public var description: String {
        switch self {
        case .hidden:
            "hidden"
        case .ready:
            "ready"
        case .creating:
            "creating"
        case .paused:
            "paused"
        case .failed:
            "failed"
        case .durabilityVerificationRequired:
            "durabilityVerificationRequired"
        case .completionPending:
            "completionPending"
        case .recoveryRequired:
            "recoveryRequired"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasLocalVaultCreationPresentationClaim:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    fileprivate let identifier: UUID

    public init() {
        identifier = UUID()
    }

    public var description: String {
        "AtlasLocalVaultCreationPresentationClaim(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

@MainActor
public final class AtlasLocalVaultCreationPresentationOwner:
    ObservableObject,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private struct Operation {
        let identifier: UUID
        let operationTask: Task<Void, Never>
    }

    @Published public private(set) var presentation:
        AtlasLocalVaultCreationPresentation = .hidden
    @Published private var presentationClaimIdentifier: UUID?

    private let creator: any AtlasLocalVaultCreating
    private let continueToUnlock:
        @MainActor @Sendable () async -> AtlasLockedShellUnlockFlowState
    private var operation: Operation?
    private var terminalStopRequested = false

    public init(
        creator: any AtlasLocalVaultCreating,
        continueToUnlock:
            @escaping @MainActor @Sendable ()
                async -> AtlasLockedShellUnlockFlowState
    ) {
        self.creator = creator
        self.continueToUnlock = continueToUnlock
    }

    public func present() {
        guard !terminalStopRequested, operation == nil else {
            return
        }
        presentation = .ready
    }

    public func dismiss() {
        guard presentation != .creating else {
            return
        }
        presentationClaimIdentifier = nil
        presentation = .hidden
    }

    @discardableResult
    public func claimPresentation(
        _ claim: AtlasLocalVaultCreationPresentationClaim
    ) -> Bool {
        guard
            !terminalStopRequested,
            presentation != .hidden
        else {
            return false
        }
        presentationClaimIdentifier = claim.identifier
        return true
    }

    @discardableResult
    public func releasePresentation(
        _ claim: AtlasLocalVaultCreationPresentationClaim
    ) -> Bool {
        guard presentationClaimIdentifier == claim.identifier else {
            return false
        }
        presentationClaimIdentifier = nil
        return true
    }

    public func ownsPresentation(
        _ claim: AtlasLocalVaultCreationPresentationClaim
    ) -> Bool {
        presentationClaimIdentifier == claim.identifier
    }

    public func beginCreateOrResume() {
        guard
            !terminalStopRequested,
            operation == nil
        else {
            return
        }
        switch presentation {
        case .ready,
             .paused,
             .failed,
             .durabilityVerificationRequired,
             .completionPending:
            break
        case .hidden, .creating, .recoveryRequired:
            return
        }

        presentation = .creating
        let identifier = UUID()
        let creator = creator
        let task = Task { @MainActor [weak self] in
            let result: Result<
                AtlasLocalVaultCreationOutcome,
                AtlasLocalVaultCreationFailure
            >
            do {
                result = .success(try await creator.createOrResume())
            } catch let failure as AtlasLocalVaultCreationFailure {
                result = .failure(failure)
            } catch {
                result = .failure(.unavailable)
            }
            await self?.finish(
                identifier: identifier,
                result: result
            )
        }
        operation = Operation(
            identifier: identifier,
            operationTask: task
        )
    }

    public func pause() async {
        guard let operation else {
            if presentation == .creating {
                presentation = .paused
            }
            return
        }
        await creator.pause()
        operation.operationTask.cancel()
        await operation.operationTask.value
        let stillOwnsOperation =
            self.operation?.identifier == operation.identifier
        if stillOwnsOperation {
            self.operation = nil
        }
        if !terminalStopRequested,
           stillOwnsOperation,
           presentation == .creating {
            presentation = .paused
        }
    }

    public func terminalStop() async {
        terminalStopRequested = true
        let retainedOperation = operation
        await creator.pause()
        retainedOperation?.operationTask.cancel()
        await retainedOperation?.operationTask.value
        if operation?.identifier == retainedOperation?.identifier {
            operation = nil
        }
        presentationClaimIdentifier = nil
        presentation = .hidden
    }

    public func stop() async {
        await terminalStop()
    }

    public nonisolated var description: String {
        "AtlasLocalVaultCreationPresentationOwner(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    var hasRetainedOperationForTesting: Bool {
        operation != nil
    }

    func waitForCurrentOperationForTesting() async {
        await operation?.operationTask.value
    }

    private func finish(
        identifier: UUID,
        result: Result<
            AtlasLocalVaultCreationOutcome,
            AtlasLocalVaultCreationFailure
        >
    ) async {
        guard
            operation?.identifier == identifier,
            !terminalStopRequested
        else {
            return
        }

        switch result {
        case .success:
            let continuation = await continueToUnlock()
            guard
                continuation.publicShell.vaultStatus == .locked,
                continuation.mode == .unlockPanel,
                let panel = continuation.unlockPanelState,
                panel.selectedMethod == nil,
                panel.status != .unlocked
            else {
                presentation = .recoveryRequired
                operation = nil
                return
            }
            presentationClaimIdentifier = nil
            presentation = .hidden
        case let .failure(failure):
            switch failure {
            case .cancelled:
                presentation = .paused
            case .durabilityVerificationRequired:
                presentation = .durabilityVerificationRequired
            case .completionPending:
                presentation = .completionPending
            case .recoveryRequired:
                presentation = .recoveryRequired
            case .unavailable:
                presentation = .failed
            }
        }
        operation = nil
    }
}

@MainActor
public struct AtlasLocalVaultCreationActions {
    private let presentAction: @MainActor @Sendable () -> Void
    private let dismissAction: @MainActor @Sendable () -> Void
    private let createAction: @MainActor @Sendable () -> Void
    private let pauseAction:
        @MainActor @Sendable () async -> Void
    private let claimPresentationAction:
        @MainActor @Sendable (
            AtlasLocalVaultCreationPresentationClaim
        ) -> Bool
    private let releasePresentationAction:
        @MainActor @Sendable (
            AtlasLocalVaultCreationPresentationClaim
        ) -> Bool
    private let ownsPresentationAction:
        @MainActor @Sendable (
            AtlasLocalVaultCreationPresentationClaim
        ) -> Bool

    public init(
        present: @escaping @MainActor @Sendable () -> Void,
        dismiss: @escaping @MainActor @Sendable () -> Void,
        createOrResume:
            @escaping @MainActor @Sendable () -> Void,
        pause: @escaping @MainActor @Sendable () async -> Void,
        claimPresentation:
            @escaping @MainActor @Sendable (
                AtlasLocalVaultCreationPresentationClaim
            ) -> Bool,
        releasePresentation:
            @escaping @MainActor @Sendable (
                AtlasLocalVaultCreationPresentationClaim
            ) -> Bool,
        ownsPresentation:
            @escaping @MainActor @Sendable (
                AtlasLocalVaultCreationPresentationClaim
            ) -> Bool
    ) {
        presentAction = present
        dismissAction = dismiss
        createAction = createOrResume
        pauseAction = pause
        claimPresentationAction = claimPresentation
        releasePresentationAction = releasePresentation
        ownsPresentationAction = ownsPresentation
    }

    public func present() {
        presentAction()
    }

    public func dismiss() {
        dismissAction()
    }

    public func createOrResume() {
        createAction()
    }

    public func pause() async {
        await pauseAction()
    }

    @discardableResult
    public func claimPresentation(
        _ claim: AtlasLocalVaultCreationPresentationClaim
    ) -> Bool {
        claimPresentationAction(claim)
    }

    @discardableResult
    public func releasePresentation(
        _ claim: AtlasLocalVaultCreationPresentationClaim
    ) -> Bool {
        releasePresentationAction(claim)
    }

    public func ownsPresentation(
        _ claim: AtlasLocalVaultCreationPresentationClaim
    ) -> Bool {
        ownsPresentationAction(claim)
    }
}

@MainActor
public struct AtlasLocalVaultCreationContext {
    public let owner: AtlasLocalVaultCreationPresentationOwner
    public let actions: AtlasLocalVaultCreationActions

    public init(
        owner: AtlasLocalVaultCreationPresentationOwner,
        actions: AtlasLocalVaultCreationActions
    ) {
        self.owner = owner
        self.actions = actions
    }
}

@MainActor
public struct AtlasLocalVaultCreationView: View {
    @ObservedObject private var owner:
        AtlasLocalVaultCreationPresentationOwner
    private let actions: AtlasLocalVaultCreationActions
    @State private var acknowledgedDeviceLocalRisk = false

    public init(
        owner: AtlasLocalVaultCreationPresentationOwner,
        actions: AtlasLocalVaultCreationActions
    ) {
        self.owner = owner
        self.actions = actions
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label("Create Local Vault", systemImage: "externaldrive.badge.plus")
                .font(.title2)
                .fontWeight(.semibold)

            Text("This vault is currently protected by a device-local Keychain key only. Passphrase and recovery-key recovery are not available yet. Losing the app data or Keychain item may make this vault inaccessible.")
            .font(.callout)
            .foregroundStyle(.secondary)

            content
        }
        .padding(20)
        .frame(minWidth: 320, idealWidth: 460)
    }

    @ViewBuilder
    private var content: some View {
        switch owner.presentation {
        case .hidden:
            EmptyView()
        case .ready:
            Toggle(
                "I understand this vault has no recovery method.",
                isOn: $acknowledgedDeviceLocalRisk
            )
            actionRow(
                primaryTitle: "Create Local Vault",
                primaryEnabled: acknowledgedDeviceLocalRisk,
                primaryAction: actions.createOrResume
            )
        case .creating:
            HStack(spacing: 12) {
                ProgressView()
                Text("Creating the encrypted local vault.")
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Pause Setup") {
                    Task { @MainActor in
                        await actions.pause()
                    }
                }
            }
        case .paused:
            fixedStatus(
                "Setup is paused. Retry resumes without deleting "
                    + "completed local steps."
            )
            retryRow()
        case .failed:
            fixedStatus(
                "Local vault setup is temporarily unavailable."
            )
            retryRow()
        case .durabilityVerificationRequired:
            fixedStatus(
                "The encrypted store requires durability verification "
                    + "before it can be selected."
            )
            retryRow()
        case .completionPending:
            fixedStatus(
                "Vault setup completion is pending verification."
            )
            retryRow()
        case .recoveryRequired:
            fixedStatus(
                "Local vault setup requires non-destructive recovery."
            )
            Button("Close") {
                actions.dismiss()
            }
            .buttonStyle(.bordered)
        }
    }

    private func fixedStatus(_ message: String) -> some View {
        Text(message)
            .font(.callout)
            .foregroundStyle(.secondary)
    }

    private func retryRow() -> some View {
        actionRow(
            primaryTitle: "Retry Setup",
            primaryEnabled: true,
            primaryAction: actions.createOrResume
        )
    }

    private func actionRow(
        primaryTitle: String,
        primaryEnabled: Bool,
        primaryAction: @escaping @MainActor @Sendable () -> Void
    ) -> some View {
        HStack {
            Button("Close") {
                actions.dismiss()
            }
            .buttonStyle(.bordered)

            Spacer()

            Button(primaryTitle) {
                primaryAction()
            }
            .buttonStyle(.borderedProminent)
            .disabled(!primaryEnabled)
        }
    }
}
