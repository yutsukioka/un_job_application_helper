import Combine
import Foundation
import SwiftUI
import UniformTypeIdentifiers

public enum AtlasVaultRecoveryImportPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case hidden
    case ready
    case reading
    case awaitingRecoveryKey
    case verifying
    case importing
    case paused
    case durabilityVerificationRequired
    case completionPending
    case failed
    case recoveryRequired
    case complete

    public var description: String {
        switch self {
        case .hidden: "hidden"
        case .ready: "ready"
        case .reading: "reading"
        case .awaitingRecoveryKey: "awaitingRecoveryKey"
        case .verifying: "verifying"
        case .importing: "importing"
        case .paused: "paused"
        case .durabilityVerificationRequired:
            "durabilityVerificationRequired"
        case .completionPending: "completionPending"
        case .failed: "failed"
        case .recoveryRequired: "recoveryRequired"
        case .complete: "complete"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultRecoveryImportPresentationClaim:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    fileprivate let identifier = UUID()

    public init() {}

    public var description: String {
        "AtlasVaultRecoveryImportPresentationClaim(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

@MainActor
public final class AtlasVaultRecoveryImportPresentationOwner:
    ObservableObject,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum OperationKind: Equatable {
        case prepare
        case restore
        case resume
        case finish
        case reset
    }

    private enum OperationValue: Sendable {
        case prepared
        case complete
        case failure(AtlasVaultRecoveryImportFailure)
    }

    private struct Operation {
        let identifier: UUID
        let kind: OperationKind
        let operationTask: Task<OperationValue, Never>
    }

    @Published public private(set) var presentation:
        AtlasVaultRecoveryImportPresentation = .hidden
    @Published private var presentationClaimIdentifier: UUID?

    private let coordinator: any AtlasVaultRecoveryImportCoordinating
    private let continueToUnlock: @MainActor @Sendable () async -> Void
    private var operation: Operation?
    private var terminalStopRequested = false

    public init(
        coordinator: any AtlasVaultRecoveryImportCoordinating,
        continueToUnlock:
            @escaping @MainActor @Sendable () async -> Void
    ) {
        self.coordinator = coordinator
        self.continueToUnlock = continueToUnlock
    }

    public func present() {
        guard !terminalStopRequested, operation == nil else {
            return
        }
        presentation = .ready
    }

    public func dismiss() {
        guard !presentation.requiresExplicitPauseBeforeDismiss else {
            return
        }
        presentationClaimIdentifier = nil
        presentation = .hidden
    }

    public func prepareImport(from url: URL) async {
        guard presentation == .ready || presentation == .failed else {
            return
        }
        presentation = .reading
        let result = await retained(.prepare) { [coordinator] in
            do {
                try await coordinator.prepareImport(from: url)
                return .prepared
            } catch {
                return .failure(Self.failure(error))
            }
        }
        guard presentation == .reading else {
            return
        }
        switch result {
        case .prepared:
            presentation = .awaitingRecoveryKey
        case let .failure(failure):
            await publish(failure)
        case .complete:
            presentation = .failed
        }
    }

    public func restore(secret: String, confirmed: Bool) async {
        guard confirmed, presentation == .awaitingRecoveryKey else {
            return
        }
        presentation = .verifying
        let result = await retained(.restore) { [coordinator] in
            let buffer = AtlasVaultInMemorySecretBuffer(
                bytes: Data(secret.utf8)
            )
            do {
                _ = try await coordinator.confirmAndImport(
                    recoverySecret: buffer
                )
                return .complete
            } catch {
                await buffer.clear()
                return .failure(Self.failure(error))
            }
        }
        await publishCompletion(result)
    }

    public func resume(from url: URL, secret: String) async {
        guard presentation.canResume else {
            return
        }
        presentation = .verifying
        let result = await retained(.resume) { [coordinator] in
            let buffer = AtlasVaultInMemorySecretBuffer(
                bytes: Data(secret.utf8)
            )
            do {
                _ = try await coordinator.resumeImport(
                    from: url,
                    recoverySecret: buffer
                )
                return .complete
            } catch {
                await buffer.clear()
                return .failure(Self.failure(error))
            }
        }
        await publishCompletion(result)
    }

    public func finishCommittedImport(
        from url: URL,
        secret: String
    ) async {
        guard presentation == .completionPending else {
            return
        }
        presentation = .verifying
        let result = await retained(.finish) { [coordinator] in
            let buffer = AtlasVaultInMemorySecretBuffer(
                bytes: Data(secret.utf8)
            )
            do {
                _ = try await coordinator.finishCommittedImport(
                    from: url,
                    recoverySecret: buffer
                )
                return .complete
            } catch {
                await buffer.clear()
                return .failure(Self.failure(error))
            }
        }
        await publishCompletion(result)
    }

    public func resetPendingImport(confirmed: Bool) async {
        guard confirmed, !presentation.isBusy else {
            return
        }
        presentation = .importing
        let result = await retained(.reset) { [coordinator] in
            do {
                try await coordinator.resetPendingImport()
                return .complete
            } catch {
                return .failure(Self.failure(error))
            }
        }
        switch result {
        case .complete:
            presentation = .ready
        case let .failure(failure):
            await publish(failure)
        case .prepared:
            presentation = .failed
        }
    }

    public func pause() async {
        let pausedFrom = presentation
        let retainedOperation = operation
        retainedOperation?.operationTask.cancel()
        await coordinator.pause()
        _ = await retainedOperation?.operationTask.value
        if operation?.identifier == retainedOperation?.identifier {
            operation = nil
        }
        guard !terminalStopRequested else {
            return
        }
        if pausedFrom == .reading
            || pausedFrom == .awaitingRecoveryKey
        {
            presentation = .ready
            return
        }
        let pending = (try? await coordinator.hasPendingImport()) == true
        presentation = pending ? .paused : .ready
    }

    public func dismissForUnsafeLifecycle() async {
        await pause()
        presentationClaimIdentifier = nil
        presentation = .hidden
    }

    public func stop() async {
        terminalStopRequested = true
        let retainedOperation = operation
        retainedOperation?.operationTask.cancel()
        await coordinator.stop()
        _ = await retainedOperation?.operationTask.value
        operation = nil
        presentationClaimIdentifier = nil
        presentation = .hidden
    }

    @discardableResult
    public func claimPresentation(
        _ claim: AtlasVaultRecoveryImportPresentationClaim
    ) -> Bool {
        guard
            !terminalStopRequested,
            presentation != .hidden,
            presentationClaimIdentifier == nil
                || presentationClaimIdentifier == claim.identifier
        else {
            return false
        }
        presentationClaimIdentifier = claim.identifier
        return true
    }

    @discardableResult
    public func releasePresentation(
        _ claim: AtlasVaultRecoveryImportPresentationClaim
    ) -> Bool {
        guard presentationClaimIdentifier == claim.identifier else {
            return false
        }
        presentationClaimIdentifier = nil
        return true
    }

    public func ownsPresentation(
        _ claim: AtlasVaultRecoveryImportPresentationClaim
    ) -> Bool {
        presentationClaimIdentifier == claim.identifier
    }

    public nonisolated var description: String {
        "AtlasVaultRecoveryImportPresentationOwner(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private func retained(
        _ kind: OperationKind,
        operation body: @escaping @MainActor @Sendable () async
            -> OperationValue
    ) async -> OperationValue {
        if let operation {
            guard operation.kind == kind else {
                return .failure(.unavailable)
            }
            return await operation.operationTask.value
        }
        let identifier = UUID()
        let task = Task { @MainActor in
            await body()
        }
        operation = Operation(
            identifier: identifier,
            kind: kind,
            operationTask: task
        )
        let result = await task.value
        if operation?.identifier == identifier {
            operation = nil
        }
        return result
    }

    private func publishCompletion(_ result: OperationValue) async {
        guard presentation == .verifying else {
            return
        }
        switch result {
        case .complete:
            presentation = .complete
            await continueToUnlock()
        case let .failure(failure):
            await publish(failure)
        case .prepared:
            presentation = .failed
        }
    }

    private func publish(
        _ failure: AtlasVaultRecoveryImportFailure
    ) async {
        switch failure {
        case .pendingImportRequiresResume, .cancelled:
            presentation = .paused
        case .durabilityVerificationRequired:
            presentation = .durabilityVerificationRequired
        case .completionPending:
            presentation = .completionPending
        case .recoveryRequired, .existingVault:
            presentation = .recoveryRequired
        case .invalidRecoveryKey:
            let pending =
                (try? await coordinator.hasPendingImport()) == true
            presentation = pending ? .paused : .awaitingRecoveryKey
        case .unavailable,
             .invalidFile,
             .invalidExport,
             .restoreUnavailable:
            presentation = .failed
        }
    }

    private static func failure(
        _ error: Error
    ) -> AtlasVaultRecoveryImportFailure {
        error as? AtlasVaultRecoveryImportFailure ?? .unavailable
    }
}

@MainActor
public struct AtlasVaultRecoveryImportActions {
    private let presentAction: @MainActor @Sendable () -> Void
    private let dismissAction: @MainActor @Sendable () -> Void
    private let prepareAction:
        @MainActor @Sendable (URL) async -> Void
    private let restoreAction:
        @MainActor @Sendable (String, Bool) async -> Void
    private let resumeAction:
        @MainActor @Sendable (URL, String) async -> Void
    private let finishAction:
        @MainActor @Sendable (URL, String) async -> Void
    private let resetAction:
        @MainActor @Sendable (Bool) async -> Void
    private let pauseAction: @MainActor @Sendable () async -> Void
    private let claimAction:
        @MainActor @Sendable (
            AtlasVaultRecoveryImportPresentationClaim
        ) -> Bool
    private let releaseAction:
        @MainActor @Sendable (
            AtlasVaultRecoveryImportPresentationClaim
        ) -> Bool
    private let ownsAction:
        @MainActor @Sendable (
            AtlasVaultRecoveryImportPresentationClaim
        ) -> Bool

    public init(
        present: @escaping @MainActor @Sendable () -> Void,
        dismiss: @escaping @MainActor @Sendable () -> Void,
        prepareImport:
            @escaping @MainActor @Sendable (URL) async -> Void,
        restore:
            @escaping @MainActor @Sendable (String, Bool) async -> Void,
        resume:
            @escaping @MainActor @Sendable (URL, String) async -> Void,
        finish:
            @escaping @MainActor @Sendable (URL, String) async -> Void,
        reset:
            @escaping @MainActor @Sendable (Bool) async -> Void,
        pause: @escaping @MainActor @Sendable () async -> Void,
        claimPresentation:
            @escaping @MainActor @Sendable (
                AtlasVaultRecoveryImportPresentationClaim
            ) -> Bool,
        releasePresentation:
            @escaping @MainActor @Sendable (
                AtlasVaultRecoveryImportPresentationClaim
            ) -> Bool,
        ownsPresentation:
            @escaping @MainActor @Sendable (
                AtlasVaultRecoveryImportPresentationClaim
            ) -> Bool
    ) {
        presentAction = present
        dismissAction = dismiss
        prepareAction = prepareImport
        restoreAction = restore
        resumeAction = resume
        finishAction = finish
        resetAction = reset
        pauseAction = pause
        claimAction = claimPresentation
        releaseAction = releasePresentation
        ownsAction = ownsPresentation
    }

    public func present() {
        presentAction()
    }

    public func dismiss() {
        dismissAction()
    }

    public func prepareImport(_ url: URL) async {
        await prepareAction(url)
    }

    public func restore(_ secret: String, _ confirmed: Bool) async {
        await restoreAction(secret, confirmed)
    }

    public func resume(_ url: URL, _ secret: String) async {
        await resumeAction(url, secret)
    }

    public func finish(_ url: URL, _ secret: String) async {
        await finishAction(url, secret)
    }

    public func resetPendingImport(confirmed: Bool) async {
        await resetAction(confirmed)
    }

    public func pause() async {
        await pauseAction()
    }

    public func claimPresentation(
        _ claim: AtlasVaultRecoveryImportPresentationClaim
    ) -> Bool {
        claimAction(claim)
    }

    public func releasePresentation(
        _ claim: AtlasVaultRecoveryImportPresentationClaim
    ) -> Bool {
        releaseAction(claim)
    }

    public func ownsPresentation(
        _ claim: AtlasVaultRecoveryImportPresentationClaim
    ) -> Bool {
        ownsAction(claim)
    }
}

@MainActor
public final class AtlasVaultRecoveryImportAvailability:
    ObservableObject
{
    @Published public private(set) var hasPendingImport = false

    public init() {}

    func setPendingImport(_ pending: Bool) {
        hasPendingImport = pending
    }
}

@MainActor
public struct AtlasVaultRecoveryImportContext {
    public let owner: AtlasVaultRecoveryImportPresentationOwner
    public let actions: AtlasVaultRecoveryImportActions
    public let availability: AtlasVaultRecoveryImportAvailability

    public init(
        owner: AtlasVaultRecoveryImportPresentationOwner,
        actions: AtlasVaultRecoveryImportActions,
        availability: AtlasVaultRecoveryImportAvailability =
            AtlasVaultRecoveryImportAvailability()
    ) {
        self.owner = owner
        self.actions = actions
        self.availability = availability
    }
}

@MainActor
public struct AtlasVaultRecoveryImportView: View {
    @ObservedObject private var owner:
        AtlasVaultRecoveryImportPresentationOwner
    private let actions: AtlasVaultRecoveryImportActions

    @State private var recoveryEntry = ""
    @State private var confirmedRestore = false
    @State private var confirmedReset = false
    @State private var importerPresented = false
    @State private var pendingFileAction = PendingFileAction.prepare

    private enum PendingFileAction {
        case prepare
        case resume
        case finish
    }

    public init(
        owner: AtlasVaultRecoveryImportPresentationOwner,
        actions: AtlasVaultRecoveryImportActions
    ) {
        self.owner = owner
        self.actions = actions
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(
                "Restore Encrypted Backup",
                systemImage: "externaldrive.badge.plus"
            )
            .font(.title2)
            .fontWeight(.semibold)

            Text(
                "Restore is available only on an empty installation. "
                    + "The selected backup remains encrypted while it is "
                    + "validated. No record content is displayed."
            )
            .font(.callout)
            .foregroundStyle(.secondary)

            content
        }
        .padding(20)
        .frame(minWidth: 340, idealWidth: 520)
        .fileImporter(
            isPresented: $importerPresented,
            allowedContentTypes: [Self.encryptedBackupType],
            allowsMultipleSelection: false
        ) { result in
            handleFileSelection(result)
        }
        .onChange(of: owner.presentation) { _, presentation in
            if presentation.clearsLocalSecret {
                clearLocalState()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch owner.presentation {
        case .hidden:
            EmptyView()
        case .ready, .failed:
            Button("Select Encrypted Backup") {
                pendingFileAction = .prepare
                importerPresented = true
            }
            .buttonStyle(.borderedProminent)
            closeButton()
        case .reading, .verifying, .importing:
            ProgressView()
        case .awaitingRecoveryKey:
            recoveryControls
            pauseButton()
        case .paused,
             .durabilityVerificationRequired,
             .recoveryRequired:
            Text(
                "Resuming requires selecting the same encrypted backup "
                    + "and entering the saved recovery key again."
            )
            .font(.callout)
            SecureField("Saved recovery key", text: $recoveryEntry)
            Button("Resume Restore") {
                pendingFileAction = .resume
                importerPresented = true
            }
            .buttonStyle(.borderedProminent)
            .disabled(recoveryEntry.isEmpty)
            resetControls
            closeButton()
        case .completionPending:
            Text(
                "Restore is committed. Select the same encrypted backup "
                    + "and enter the saved recovery key to finish."
            )
            .font(.callout)
            SecureField("Saved recovery key", text: $recoveryEntry)
            Button("Finish Restore") {
                pendingFileAction = .finish
                importerPresented = true
            }
            .buttonStyle(.borderedProminent)
            .disabled(recoveryEntry.isEmpty)
            pauseButton()
        case .complete:
            Text("Encrypted backup restored.")
            Button("Close") {
                clearLocalState()
                actions.dismiss()
            }
            .buttonStyle(.bordered)
        }
    }

    private var recoveryControls: some View {
        Group {
            SecureField("Saved recovery key", text: $recoveryEntry)
            Toggle(
                "I understand this restore installs the encrypted backup.",
                isOn: $confirmedRestore
            )
            Button("Restore Vault") {
                let submitted = recoveryEntry
                let submittedConfirmation = confirmedRestore
                recoveryEntry = ""
                confirmedRestore = false
                Task { @MainActor in
                    await actions.restore(submitted, submittedConfirmation)
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(!confirmedRestore || recoveryEntry.isEmpty)
        }
    }

    private var resetControls: some View {
        Group {
            Toggle(
                "I confirm only this incomplete restore should be discarded.",
                isOn: $confirmedReset
            )
            Button("Discard Incomplete Restore") {
                Task { @MainActor in
                    await actions.resetPendingImport(
                        confirmed: confirmedReset
                    )
                    confirmedReset = false
                }
            }
            .buttonStyle(.bordered)
            .disabled(!confirmedReset)
        }
    }

    private func pauseButton() -> some View {
        Button("Pause Restore") {
            clearLocalState()
            Task { @MainActor in
                await actions.pause()
            }
        }
        .buttonStyle(.bordered)
    }

    private func closeButton() -> some View {
        Button("Close") {
            clearLocalState()
            actions.dismiss()
        }
        .buttonStyle(.bordered)
    }

    private func handleFileSelection(
        _ result: Result<[URL], Error>
    ) {
        guard case let .success(urls) = result, urls.count == 1,
              let url = urls.first
        else {
            return
        }
        switch pendingFileAction {
        case .prepare:
            Task { @MainActor in
                await actions.prepareImport(url)
            }
        case .resume:
            let submitted = recoveryEntry
            recoveryEntry = ""
            Task { @MainActor in
                await actions.resume(url, submitted)
            }
        case .finish:
            let submitted = recoveryEntry
            recoveryEntry = ""
            Task { @MainActor in
                await actions.finish(url, submitted)
            }
        }
    }

    private func clearLocalState() {
        recoveryEntry = ""
        confirmedRestore = false
        confirmedReset = false
    }

    private static let encryptedBackupType =
        UTType(filenameExtension: "atlasvault")
        ?? UTType(importedAs: "com.atlasvault.encrypted-backup")
}

extension AtlasVaultRecoveryImportPresentation {
    var isBusy: Bool {
        self == .reading || self == .verifying || self == .importing
    }

    var canResume: Bool {
        switch self {
        case .ready,
             .paused,
             .durabilityVerificationRequired,
             .recoveryRequired:
            true
        case .hidden,
             .reading,
             .awaitingRecoveryKey,
             .verifying,
             .importing,
             .completionPending,
             .failed,
             .complete:
            false
        }
    }

    var clearsLocalSecret: Bool {
        switch self {
        case .hidden,
             .ready,
             .reading,
             .verifying,
             .importing,
             .failed,
             .complete:
            true
        case .awaitingRecoveryKey,
             .paused,
             .durabilityVerificationRequired,
             .completionPending,
             .recoveryRequired:
            false
        }
    }

    var requiresExplicitPauseBeforeDismiss: Bool {
        switch self {
        case .reading,
             .awaitingRecoveryKey,
             .verifying,
             .importing,
             .completionPending:
            true
        case .hidden,
             .ready,
             .paused,
             .durabilityVerificationRequired,
             .failed,
             .recoveryRequired,
             .complete:
            false
        }
    }
}
