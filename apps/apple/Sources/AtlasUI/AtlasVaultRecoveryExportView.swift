import Combine
import Foundation
import SwiftUI

public enum AtlasVaultRecoveryExportPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case hidden
    case ready
    case generating
    case awaitingConfirmation
    case resumeRequired
    case verifying
    case exportReady
    case paused
    case failed
    case durabilityVerificationRequired
    case completionPending
    case recoveryRequired
    case resetting
    case complete

    public var description: String {
        switch self {
        case .hidden: "hidden"
        case .ready: "ready"
        case .generating: "generating"
        case .awaitingConfirmation: "awaitingConfirmation"
        case .resumeRequired: "resumeRequired"
        case .verifying: "verifying"
        case .exportReady: "exportReady"
        case .paused: "paused"
        case .failed: "failed"
        case .durabilityVerificationRequired:
            "durabilityVerificationRequired"
        case .completionPending: "completionPending"
        case .recoveryRequired: "recoveryRequired"
        case .resetting: "resetting"
        case .complete: "complete"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultRecoveryExportPresentationClaim:
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
        "AtlasVaultRecoveryExportPresentationClaim(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

@MainActor
public final class AtlasVaultRecoveryExportPresentationOwner:
    ObservableObject,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum OperationKind: Equatable {
        case generate
        case confirm
        case resume
        case exportCompletion
        case reset
        case cleanup
    }

    private enum OperationValue: Sendable {
        case display(AtlasVaultRecoveryDisplayCodeHandle)
        case document(AtlasVaultEncryptedDocument)
        case complete
        case failure(AtlasVaultRecoveryExportFailure)
    }

    private struct Operation {
        let identifier: UUID
        let kind: OperationKind
        let operationTask: Task<OperationValue, Never>
    }

    @Published public private(set) var presentation:
        AtlasVaultRecoveryExportPresentation = .hidden
    @Published private var presentationClaimIdentifier: UUID?

    private let coordinator: any AtlasVaultRecoveryExportCoordinating
    private var operation: Operation?
    private var terminalStopRequested = false

    public init(
        coordinator: any AtlasVaultRecoveryExportCoordinating
    ) {
        self.coordinator = coordinator
    }

    public func present() {
        guard !terminalStopRequested, operation == nil else {
            return
        }
        presentation = .ready
    }

    public func dismiss() {
        guard
            presentation != .generating,
            presentation != .verifying,
            presentation != .resetting
        else {
            return
        }
        presentationClaimIdentifier = nil
        presentation = .hidden
        beginCleanup()
    }

    public func generate() async -> AtlasVaultRecoveryDisplayCodeHandle? {
        guard canBeginGenerate else {
            return nil
        }
        presentation = .generating
        let result = await retained(.generate) { [coordinator] in
            do {
                return .display(
                    try await coordinator.prepareNewRecovery()
                )
            } catch {
                return .failure(Self.failure(error))
            }
        }
        guard presentation == .generating else {
            return nil
        }
        switch result {
        case let .display(handle):
            presentation = .awaitingConfirmation
            return handle
        case let .failure(failure):
            publish(failure)
            return nil
        case .document, .complete:
            presentation = .failed
            return nil
        }
    }

    public func confirm(
        secret: String
    ) async -> AtlasVaultEncryptedDocument? {
        guard presentation == .awaitingConfirmation else {
            return nil
        }
        presentation = .verifying
        let result = await retained(.confirm) { [coordinator] in
            do {
                return .document(
                    try await coordinator.confirmAndPrepareExport(
                        secret: secret
                    )
                )
            } catch {
                return .failure(Self.failure(error))
            }
        }
        if case .failure(.invalidConfirmation) = result {
            presentation = .awaitingConfirmation
            return nil
        }
        return publishDocument(result)
    }

    public func resume(
        secret: String
    ) async -> AtlasVaultEncryptedDocument? {
        guard
            presentation == .resumeRequired
                || presentation == .paused
                || presentation == .completionPending
                || presentation == .durabilityVerificationRequired
        else {
            return nil
        }
        presentation = .verifying
        let result = await retained(.resume) { [coordinator] in
            do {
                return .document(
                    try await coordinator.resumeAndPrepareExport(
                        secret: secret
                    )
                )
            } catch {
                return .failure(Self.failure(error))
            }
        }
        return publishDocument(result)
    }

    public func exportDidFinish(success: Bool) async {
        guard presentation == .exportReady else {
            return
        }
        if !success {
            await coordinator.exportDidFailOrCancel()
            presentation = .resumeRequired
            return
        }
        presentation = .verifying
        let result = await retained(.exportCompletion) {
            [coordinator] in
            do {
                try await coordinator.exportDidSucceed()
                return .complete
            } catch {
                return .failure(Self.failure(error))
            }
        }
        switch result {
        case .complete:
            presentation = .complete
        case let .failure(failure):
            publish(failure)
        case .display, .document:
            presentation = .failed
        }
    }

    public func resetPendingSetup(confirmed: Bool) async {
        guard
            confirmed,
            presentation == .resumeRequired
                || presentation == .paused
                || presentation == .durabilityVerificationRequired
                || presentation == .completionPending
                || presentation == .recoveryRequired
        else {
            return
        }
        presentation = .resetting
        let result = await retained(.reset) { [coordinator] in
            do {
                try await coordinator.resetPendingSetup()
                return .complete
            } catch {
                return .failure(Self.failure(error))
            }
        }
        switch result {
        case .complete:
            presentation = .ready
        case let .failure(failure):
            publish(failure)
        case .display, .document:
            presentation = .failed
        }
    }

    public func pause() async {
        let pausedFrom = presentation
        let retainedOperation = operation
        retainedOperation?.operationTask.cancel()
        await coordinator.exportDidFailOrCancel()
        _ = await retainedOperation?.operationTask.value
        if operation?.identifier == retainedOperation?.identifier {
            operation = nil
        }
        if !terminalStopRequested {
            switch pausedFrom {
            case .generating, .awaitingConfirmation:
                presentation = .ready
            case .hidden,
                 .ready,
                 .resumeRequired,
                 .verifying,
                 .exportReady,
                 .paused,
                 .failed,
                 .durabilityVerificationRequired,
                 .completionPending,
                 .recoveryRequired,
                 .resetting,
                 .complete:
                presentation = .paused
            }
        }
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
        _ claim: AtlasVaultRecoveryExportPresentationClaim
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
        _ claim: AtlasVaultRecoveryExportPresentationClaim
    ) -> Bool {
        guard presentationClaimIdentifier == claim.identifier else {
            return false
        }
        presentationClaimIdentifier = nil
        return true
    }

    public func ownsPresentation(
        _ claim: AtlasVaultRecoveryExportPresentationClaim
    ) -> Bool {
        presentationClaimIdentifier == claim.identifier
    }

    func canPublishGeneratedCode(
        for claim: AtlasVaultRecoveryExportPresentationClaim?
    ) -> Bool {
        guard presentation == .awaitingConfirmation else {
            return false
        }
        guard let claim else {
            return true
        }
        return ownsPresentation(claim)
    }

    public nonisolated var description: String {
        "AtlasVaultRecoveryExportPresentationOwner(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    var hasRetainedOperationForTesting: Bool {
        operation != nil
    }

    private var canBeginGenerate: Bool {
        switch presentation {
        case .ready, .failed:
            true
        case .hidden,
             .generating,
             .awaitingConfirmation,
             .resumeRequired,
             .verifying,
             .exportReady,
             .paused,
             .durabilityVerificationRequired,
             .completionPending,
             .recoveryRequired,
             .resetting,
             .complete:
            false
        }
    }

    private func retained(
        _ kind: OperationKind,
        operation body: @escaping @MainActor @Sendable ()
            async -> OperationValue
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

    private func publishDocument(
        _ result: OperationValue
    ) -> AtlasVaultEncryptedDocument? {
        switch result {
        case let .document(document):
            presentation = .exportReady
            return document
        case let .failure(failure):
            publish(failure)
            return nil
        case .display, .complete:
            presentation = .failed
            return nil
        }
    }

    private func publish(_ failure: AtlasVaultRecoveryExportFailure) {
        switch failure {
        case .pendingSetupRequiresRecoveryKey,
             .alreadyRecoveryPrepared,
             .invalidConfirmation:
            presentation = .resumeRequired
        case .durabilityVerificationRequired:
            presentation = .durabilityVerificationRequired
        case .completionPending:
            presentation = .completionPending
        case .recoveryRequired:
            presentation = .recoveryRequired
        case .cancelled:
            presentation = .paused
        case .unavailable, .unauthorized:
            presentation = .failed
        }
    }

    private func beginCleanup() {
        guard operation == nil else {
            return
        }
        let identifier = UUID()
        let coordinator = coordinator
        let task = Task { @MainActor in
            await coordinator.exportDidFailOrCancel()
            return OperationValue.complete
        }
        operation = Operation(
            identifier: identifier,
            kind: .cleanup,
            operationTask: task
        )
        Task { @MainActor [weak self] in
            _ = await task.value
            if self?.operation?.identifier == identifier {
                self?.operation = nil
            }
        }
    }

    private static func failure(
        _ error: Error
    ) -> AtlasVaultRecoveryExportFailure {
        error as? AtlasVaultRecoveryExportFailure ?? .unavailable
    }
}

@MainActor
public struct AtlasVaultRecoveryExportActions {
    private let presentAction: @MainActor @Sendable () -> Void
    private let dismissAction: @MainActor @Sendable () -> Void
    private let generateAction:
        @MainActor @Sendable () async
            -> AtlasVaultRecoveryDisplayCodeHandle?
    private let confirmAction:
        @MainActor @Sendable (String) async
            -> AtlasVaultEncryptedDocument?
    private let resumeAction:
        @MainActor @Sendable (String) async
            -> AtlasVaultEncryptedDocument?
    private let exportCompletionAction:
        @MainActor @Sendable (Bool) async -> Void
    private let resetAction:
        @MainActor @Sendable (Bool) async -> Void
    private let pauseAction: @MainActor @Sendable () async -> Void
    private let claimAction:
        @MainActor @Sendable (
            AtlasVaultRecoveryExportPresentationClaim
        ) -> Bool
    private let releaseAction:
        @MainActor @Sendable (
            AtlasVaultRecoveryExportPresentationClaim
        ) -> Bool
    private let ownsAction:
        @MainActor @Sendable (
            AtlasVaultRecoveryExportPresentationClaim
        ) -> Bool

    public init(
        present: @escaping @MainActor @Sendable () -> Void,
        dismiss: @escaping @MainActor @Sendable () -> Void,
        generate:
            @escaping @MainActor @Sendable () async
                -> AtlasVaultRecoveryDisplayCodeHandle?,
        confirm:
            @escaping @MainActor @Sendable (String) async
                -> AtlasVaultEncryptedDocument?,
        resume:
            @escaping @MainActor @Sendable (String) async
                -> AtlasVaultEncryptedDocument?,
        exportDidFinish:
            @escaping @MainActor @Sendable (Bool) async -> Void,
        resetPendingSetup:
            @escaping @MainActor @Sendable (Bool) async -> Void,
        pause: @escaping @MainActor @Sendable () async -> Void,
        claimPresentation:
            @escaping @MainActor @Sendable (
                AtlasVaultRecoveryExportPresentationClaim
            ) -> Bool,
        releasePresentation:
            @escaping @MainActor @Sendable (
                AtlasVaultRecoveryExportPresentationClaim
            ) -> Bool,
        ownsPresentation:
            @escaping @MainActor @Sendable (
                AtlasVaultRecoveryExportPresentationClaim
            ) -> Bool
    ) {
        presentAction = present
        dismissAction = dismiss
        generateAction = generate
        confirmAction = confirm
        resumeAction = resume
        exportCompletionAction = exportDidFinish
        resetAction = resetPendingSetup
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

    public func generate() async -> AtlasVaultRecoveryDisplayCodeHandle? {
        await generateAction()
    }

    public func confirm(
        _ secret: String
    ) async -> AtlasVaultEncryptedDocument? {
        await confirmAction(secret)
    }

    public func resume(
        _ secret: String
    ) async -> AtlasVaultEncryptedDocument? {
        await resumeAction(secret)
    }

    public func exportDidFinish(_ success: Bool) async {
        await exportCompletionAction(success)
    }

    public func resetPendingSetup(confirmed: Bool) async {
        await resetAction(confirmed)
    }

    public func pause() async {
        await pauseAction()
    }

    public func claimPresentation(
        _ claim: AtlasVaultRecoveryExportPresentationClaim
    ) -> Bool {
        claimAction(claim)
    }

    public func releasePresentation(
        _ claim: AtlasVaultRecoveryExportPresentationClaim
    ) -> Bool {
        releaseAction(claim)
    }

    public func ownsPresentation(
        _ claim: AtlasVaultRecoveryExportPresentationClaim
    ) -> Bool {
        ownsAction(claim)
    }
}

@MainActor
public struct AtlasVaultRecoveryExportContext {
    public let owner: AtlasVaultRecoveryExportPresentationOwner
    public let actions: AtlasVaultRecoveryExportActions

    public init(
        owner: AtlasVaultRecoveryExportPresentationOwner,
        actions: AtlasVaultRecoveryExportActions
    ) {
        self.owner = owner
        self.actions = actions
    }
}

@MainActor
public struct AtlasVaultRecoveryExportView: View {
    @ObservedObject private var owner:
        AtlasVaultRecoveryExportPresentationOwner
    private let actions: AtlasVaultRecoveryExportActions
    private let presentationClaim:
        AtlasVaultRecoveryExportPresentationClaim?

    @State private var displayedRecoveryCode = ""
    @State private var confirmationEntry = ""
    @State private var resumeEntry = ""
    @State private var acknowledgedSavedCode = false
    @State private var confirmedReset = false
    @State private var encryptedDocument: AtlasVaultEncryptedDocument?
    @State private var isExporterPresented = false

    public init(
        owner: AtlasVaultRecoveryExportPresentationOwner,
        actions: AtlasVaultRecoveryExportActions,
        presentationClaim:
            AtlasVaultRecoveryExportPresentationClaim? = nil
    ) {
        self.owner = owner
        self.actions = actions
        self.presentationClaim = presentationClaim
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(
                "Recovery & Encrypted Export",
                systemImage: "lock.doc"
            )
            .font(.title2)
            .fontWeight(.semibold)

            Text(
                "Save the recovery key separately. The encrypted backup "
                    + "contains no readable record content. This app cannot "
                    + "import the backup yet; recovery import and unlock are "
                    + "the next reviewed feature. Losing both the local key "
                    + "and recovery material is unrecoverable."
            )
            .font(.callout)
            .foregroundStyle(.secondary)

            content
        }
        .padding(20)
        .frame(minWidth: 340, idealWidth: 520)
        .fileExporter(
            isPresented: $isExporterPresented,
            document: encryptedDocument,
            contentType:
                AtlasVaultEncryptedDocument.readableContentTypes[0],
            defaultFilename:
                AtlasVaultEncryptedExportEnvelope.defaultFilename
        ) { result in
            encryptedDocument = nil
            Task { @MainActor in
                switch result {
                case .success:
                    await actions.exportDidFinish(true)
                case .failure:
                    await actions.exportDidFinish(false)
                }
            }
        }
        .onChange(of: owner.presentation) { _, presentation in
            if presentation.clearsLocalSecrets {
                clearLocalValues()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch owner.presentation {
        case .hidden:
            EmptyView()
        case .ready:
            Button("Generate Recovery Key") {
                Task { @MainActor in
                    guard
                        let handle = await actions.generate(),
                        let code = await handle.take()
                    else {
                        return
                    }
                    guard owner.canPublishGeneratedCode(
                        for: presentationClaim
                    ) else {
                        return
                    }
                    displayedRecoveryCode = code
                }
            }
            .buttonStyle(.borderedProminent)
            closeButton()
        case .generating, .verifying, .resetting:
            progressRow()
        case .awaitingConfirmation:
            Text(displayedRecoveryCode)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
                .accessibilityLabel("Recovery key")
                .accessibilityValue(displayedRecoveryCode)
            Toggle(
                "I saved this recovery key separately.",
                isOn: $acknowledgedSavedCode
            )
            SecureField(
                "Re-enter the full recovery key",
                text: $confirmationEntry
            )
            Button("Verify and Prepare Encrypted Export") {
                let submitted = confirmationEntry
                confirmationEntry = ""
                displayedRecoveryCode = ""
                Task { @MainActor in
                    encryptedDocument = await actions.confirm(submitted)
                    if encryptedDocument != nil {
                        isExporterPresented = true
                    }
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(
                !acknowledgedSavedCode
                    || confirmationEntry.isEmpty
            )
            pauseButton()
        case .resumeRequired,
             .paused,
             .durabilityVerificationRequired,
             .completionPending:
            Text(
                "Enter the recovery key you saved to continue encrypted "
                    + "export."
            )
            .font(.callout)
            SecureField("Saved recovery key", text: $resumeEntry)
            Button("Continue Encrypted Export") {
                let submitted = resumeEntry
                resumeEntry = ""
                Task { @MainActor in
                    encryptedDocument = await actions.resume(submitted)
                    if encryptedDocument != nil {
                        isExporterPresented = true
                    }
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(resumeEntry.isEmpty)
            Toggle(
                "I understand restart removes only this unfinished wrap.",
                isOn: $confirmedReset
            )
            Button("Restart Recovery Setup") {
                Task { @MainActor in
                    await actions.resetPendingSetup(
                        confirmed: confirmedReset
                    )
                    confirmedReset = false
                }
            }
            .buttonStyle(.bordered)
            .disabled(!confirmedReset)
            closeButton()
        case .exportReady:
            progressRow()
        case .failed:
            fixedStatus("Recovery setup is temporarily unavailable.")
            closeButton()
        case .recoveryRequired:
            fixedStatus(
                "Recovery setup requires non-destructive attention."
            )
            Toggle(
                "I understand restart removes only this unfinished wrap.",
                isOn: $confirmedReset
            )
            Button("Restart Recovery Setup") {
                Task { @MainActor in
                    await actions.resetPendingSetup(
                        confirmed: confirmedReset
                    )
                    confirmedReset = false
                }
            }
            .disabled(!confirmedReset)
            closeButton()
        case .complete:
            fixedStatus(
                "Recovery key wrapping and encrypted export are complete."
            )
            closeButton()
        }
    }

    private func progressRow() -> some View {
        HStack(spacing: 12) {
            ProgressView()
            Text("Verifying encrypted recovery material.")
                .foregroundStyle(.secondary)
            Spacer()
            pauseButton()
        }
    }

    private func pauseButton() -> some View {
        Button("Pause Setup") {
            clearLocalValues()
            Task { @MainActor in
                await actions.pause()
            }
        }
        .buttonStyle(.bordered)
    }

    private func closeButton() -> some View {
        Button("Close") {
            clearLocalValues()
            actions.dismiss()
        }
        .buttonStyle(.bordered)
    }

    private func fixedStatus(_ value: String) -> some View {
        Text(value)
            .font(.callout)
            .foregroundStyle(.secondary)
    }

    private func clearLocalValues() {
        displayedRecoveryCode = ""
        confirmationEntry = ""
        resumeEntry = ""
        acknowledgedSavedCode = false
        confirmedReset = false
        encryptedDocument = nil
        isExporterPresented = false
    }
}

private extension AtlasVaultRecoveryExportPresentation {
    var clearsLocalSecrets: Bool {
        switch self {
        case .hidden,
             .ready,
             .resumeRequired,
             .paused,
             .failed,
             .durabilityVerificationRequired,
             .completionPending,
             .recoveryRequired,
             .complete:
            true
        case .generating,
             .awaitingConfirmation,
             .verifying,
             .exportReady,
             .resetting:
            false
        }
    }
}
