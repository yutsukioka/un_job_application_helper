import SwiftUI

@MainActor
public struct AtlasExplicitUnlockView: View {
    private let state: AtlasExplicitUnlockViewState
    private let actions: AtlasExplicitUnlockViewActions

    @State private var draft = AtlasExplicitUnlockInputDraft()
    @State private var submissionGate = AtlasExplicitUnlockSubmissionGate()
    @State private var activeAction: Task<Void, Never>?
    @State private var activeActionID: UUID?
    @State private var disappearanceAuthorization =
        AtlasExplicitUnlockDisappearanceAuthorization()

    public init(
        state: AtlasExplicitUnlockViewState,
        actions: AtlasExplicitUnlockViewActions
    ) {
        self.state = state
        self.actions = actions
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            methodControls
            secretInput
            footer
        }
        .padding(20)
        .frame(minWidth: 320, idealWidth: 420)
        .onChange(of: state.selectedMethod) { _, _ in
            draft.clear()
        }
        .onChange(of: state.status) { _, _ in
            guard state.requiresInputClear else {
                return
            }
            clearInputAndCancelAction()
        }
        .onDisappear {
            handleDisappearance()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Unlock AtlasVault", systemImage: "lock.open")
                .font(.headline)
            Text(state.message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .accessibilityLabel(state.message)
        }
    }

    @ViewBuilder
    private var methodControls: some View {
        VStack(alignment: .leading, spacing: 10) {
            if state.showsLocalKeyAction {
                Button {
                    submitLocalKey()
                } label: {
                    Label("Use local key", systemImage: "key")
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    state.controlsDisabled || submissionGate.isActive
                )
                .accessibilityLabel("Unlock with local key")
            }

            if state.availableMethods.contains(.passphrase) {
                Button {
                    selectMethod(.passphrase)
                } label: {
                    Label("Use passphrase", systemImage: "text.cursor")
                }
                .buttonStyle(.bordered)
                .disabled(
                    state.controlsDisabled || submissionGate.isActive
                )
                .accessibilityLabel("Choose passphrase unlock")
            }

            if state.availableMethods.contains(.recoveryKey) {
                Button {
                    selectMethod(.recoveryKey)
                } label: {
                    Label("Use recovery key", systemImage: "lifepreserver")
                }
                .buttonStyle(.bordered)
                .disabled(
                    state.controlsDisabled || submissionGate.isActive
                )
                .accessibilityLabel("Choose recovery-key unlock")
            }
        }
    }

    @ViewBuilder
    private var secretInput: some View {
        if state.showsPassphraseInput {
            VStack(alignment: .leading, spacing: 10) {
                SecureField("Passphrase", text: $draft.passphrase)
                    .textFieldStyle(.roundedBorder)
                    .atlasSecretInputPrivacy()
                    .disabled(
                        state.controlsDisabled || submissionGate.isActive
                    )
                    .accessibilityLabel("Vault passphrase")
                    .accessibilityValue("Protected input")

                Button("Unlock") {
                    submitSecret(.passphrase)
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    !state.permitsSubmission
                        || draft.passphrase.isEmpty
                        || submissionGate.isActive
                )
                .accessibilityLabel("Submit vault passphrase")
            }
        } else if state.showsRecoveryKeyInput {
            VStack(alignment: .leading, spacing: 10) {
                SecureField("Recovery key", text: $draft.recoveryKey)
                    .textFieldStyle(.roundedBorder)
                    .atlasSecretInputPrivacy()
                    .disabled(
                        state.controlsDisabled || submissionGate.isActive
                    )
                    .accessibilityLabel("Vault recovery key")
                    .accessibilityValue("Protected input")

                Button("Unlock") {
                    submitSecret(.recoveryKey)
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    !state.permitsSubmission
                        || draft.recoveryKey.isEmpty
                        || submissionGate.isActive
                )
                .accessibilityLabel("Submit vault recovery key")
            }
        }
    }

    private var footer: some View {
        HStack {
            if state.status == .activating {
                ProgressView()
                    .controlSize(.small)
                    .accessibilityLabel("Unlocking vault")
            }

            Spacer()

            Button("Cancel") {
                cancel()
            }
            .disabled(
                state.status == .unlocked
                    || state.status == .hostReconciliationRequired
            )
            .accessibilityLabel("Cancel vault unlock")
        }
    }

    private func selectMethod(_ method: AtlasVaultUnlockMethod) {
        clearInputAndCancelAction()
        let actions = actions
        replaceActiveAction {
            await actions.select(method)
        }
    }

    private func submitLocalKey() {
        guard let identifier = submissionGate.begin() else {
            return
        }
        guard let submission = draft.consume(for: .localKey, state: state) else {
            submissionGate.finish(identifier)
            return
        }
        cancelActiveAction()
        let actions = actions
        let disappearanceAuthorization = disappearanceAuthorization
        let disappearanceID =
            disappearanceAuthorization.beginSubmission()
        let actionID = UUID()
        activeActionID = actionID
        activeAction = Task { @MainActor in
            var completionStatus: AtlasVaultUnlockPresentationStatus =
                .cancelled
            defer {
                disappearanceAuthorization.finishSubmission(
                    disappearanceID,
                    status: completionStatus
                )
            }
            guard !Task.isCancelled else {
                await submission.clearExplicitUnlockSecret()
                return
            }
            await actions.select(.localKey)
            guard !Task.isCancelled else {
                await submission.clearExplicitUnlockSecret()
                return
            }
            completionStatus = await actions.submit(submission)
            await submission.clearExplicitUnlockSecret()
            guard
                !Task.isCancelled,
                activeActionID == actionID
            else {
                return
            }
            submissionGate.finish(identifier)
            activeAction = nil
            activeActionID = nil
        }
    }

    private func submitSecret(_ method: AtlasVaultUnlockMethod) {
        guard let identifier = submissionGate.begin() else {
            return
        }
        guard let submission = draft.consume(for: method, state: state) else {
            submissionGate.finish(identifier)
            return
        }
        cancelActiveAction()
        let actions = actions
        let disappearanceAuthorization = disappearanceAuthorization
        let disappearanceID =
            disappearanceAuthorization.beginSubmission()
        let actionID = UUID()
        activeActionID = actionID
        activeAction = Task { @MainActor in
            var completionStatus: AtlasVaultUnlockPresentationStatus =
                .cancelled
            defer {
                disappearanceAuthorization.finishSubmission(
                    disappearanceID,
                    status: completionStatus
                )
            }
            guard !Task.isCancelled else {
                await submission.clearExplicitUnlockSecret()
                return
            }
            completionStatus = await actions.submit(submission)
            await submission.clearExplicitUnlockSecret()
            guard
                !Task.isCancelled,
                activeActionID == actionID
            else {
                return
            }
            submissionGate.finish(identifier)
            activeAction = nil
            activeActionID = nil
        }
    }

    private func cancel() {
        clearInputAndCancelAction()
        let actions = actions
        replaceActiveAction {
            await actions.cancel()
        }
    }

    private func handleDisappearance() {
        clearInputAndCancelAction()
        guard state.shouldNotifyDisappearance else {
            return
        }
        let actions = actions
        let disappearanceAuthorization = disappearanceAuthorization
        Task {
            guard await disappearanceAuthorization
                .shouldNotifyDisappearance()
            else {
                return
            }
            await actions.didDisappear()
        }
    }

    private func clearInputAndCancelAction() {
        draft.clear()
        cancelActiveAction()
        submissionGate.cancel()
    }

    private func cancelActiveAction() {
        activeActionID = nil
        activeAction?.cancel()
        activeAction = nil
    }

    private func replaceActiveAction(
        _ operation: @escaping @Sendable () async -> Void
    ) {
        activeActionID = nil
        activeAction?.cancel()
        let actionID = UUID()
        activeActionID = actionID
        activeAction = Task { @MainActor in
            await operation()
            guard
                !Task.isCancelled,
                activeActionID == actionID
            else {
                return
            }
            activeAction = nil
            activeActionID = nil
        }
    }
}

private extension View {
    @ViewBuilder
    func atlasSecretInputPrivacy() -> some View {
#if os(iOS)
        textInputAutocapitalization(.never)
            .autocorrectionDisabled(true)
#else
        autocorrectionDisabled(true)
#endif
    }
}
