import Foundation
import SwiftUI
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasExplicitUnlockViewTests: XCTestCase {
    private static let fakePassphrase = "FAKE_PHASE2D51_PASSPHRASE"
    private static let fakeRecoveryKey = "FAKE_PHASE2D51_RECOVERY_KEY"

    func testCurrentProductionProjectsLocalKeyOnly() {
        let state = makeViewState(
            capabilities: .currentProduction,
            selectedMethod: nil,
            status: .locked
        )

        XCTAssertEqual(state.availableMethods, [.localKey])
        XCTAssertTrue(state.showsLocalKeyAction)
        XCTAssertFalse(state.showsPassphraseInput)
        XCTAssertFalse(state.showsRecoveryKeyInput)
    }

    func testUnavailableProductionSecretMethodsRemainHidden() {
        let passphrase = makeViewState(
            capabilities: .currentProduction,
            selectedMethod: .passphrase,
            status: .methodUnavailable
        )
        let recovery = makeViewState(
            capabilities: .currentProduction,
            selectedMethod: .recoveryKey,
            status: .methodUnavailable
        )

        XCTAssertNil(passphrase.selectedMethod)
        XCTAssertFalse(passphrase.showsPassphraseInput)
        XCTAssertFalse(passphrase.permitsSubmission)
        XCTAssertNil(recovery.selectedMethod)
        XCTAssertFalse(recovery.showsRecoveryKeyInput)
        XCTAssertFalse(recovery.permitsSubmission)
    }

    func testExplicitFakeCapabilitiesExposeOnlySelectedSecretInput() {
        let capabilities = fakeCapabilities(
            localKey: false,
            passphrase: true,
            recovery: true
        )
        let passphrase = makeViewState(
            capabilities: capabilities,
            selectedMethod: .passphrase,
            status: .ready
        )
        let recovery = makeViewState(
            capabilities: capabilities,
            selectedMethod: .recoveryKey,
            status: .ready
        )

        XCTAssertEqual(
            passphrase.availableMethods,
            [.passphrase, .recoveryKey]
        )
        XCTAssertTrue(passphrase.showsPassphraseInput)
        XCTAssertFalse(passphrase.showsRecoveryKeyInput)
        XCTAssertFalse(recovery.showsPassphraseInput)
        XCTAssertTrue(recovery.showsRecoveryKeyInput)
    }

    func testEveryMergedStatusMapsToFixedPresentationBehavior() {
        let cases: [
            (
                AtlasVaultUnlockPresentationStatus,
                String,
                Bool,
                Bool
            )
        ] = [
            (.locked, "Choose an unlock method.", false, true),
            (.ready, "Ready to unlock.", false, false),
            (
                .methodUnavailable,
                "This unlock method is unavailable.",
                false,
                true
            ),
            (.activating, "Unlocking vault.", true, false),
            (.unlocked, "Vault unlocked.", true, true),
            (.failed, "Unable to unlock the vault.", false, true),
            (.cancelled, "Unlock cancelled.", false, true),
            (.timedOut, "Unlock request timed out.", false, true),
            (
                .hostReconciliationRequired,
                "Host reconciliation is required.",
                true,
                true
            ),
        ]

        for (status, message, controlsDisabled, requiresInputClear) in cases {
            let state = makeViewState(
                capabilities: .currentProduction,
                selectedMethod: status == .ready ? .localKey : nil,
                status: status
            )

            XCTAssertEqual(state.status, status)
            XCTAssertEqual(state.message, message)
            XCTAssertEqual(state.controlsDisabled, controlsDisabled)
            XCTAssertEqual(state.requiresInputClear, requiresInputClear)
            XCTAssertEqual(state.permitsSubmission, status == .ready)
        }
    }

    func testPublicViewStateContainsNoSecretOrRuntimeFields() {
        let state = makeViewState(
            capabilities: fakeCapabilities(
                localKey: true,
                passphrase: true,
                recovery: true
            ),
            selectedMethod: .passphrase,
            status: .ready
        )
        let memberNames = Set(
            Mirror(reflecting: state).children.compactMap(\.label)
        )

        for forbidden in [
            "passphrase",
            "recoveryKey",
            "secret",
            "buffer",
            "vaultKey",
            "vaultID",
            "wrappedKey",
            "recordEnvelopes",
            "filesystemURL",
            "savedSearches",
            "savedJobs",
            "saveOutcome",
        ] {
            XCTAssertFalse(memberNames.contains(forbidden), forbidden)
        }
    }

    func testPublicStateAndActionsDescriptionsAreSanitized() {
        let state = makeViewState(
            capabilities: fakeCapabilities(
                localKey: true,
                passphrase: true,
                recovery: true
            ),
            selectedMethod: .passphrase,
            status: .ready
        )
        let actions = makeActions(recorder: ExplicitUnlockActionRecorder())
        let rendered = [
            String(describing: state),
            String(reflecting: state),
            String(describing: actions),
            String(reflecting: actions),
        ].joined(separator: "|")

        XCTAssertFalse(rendered.contains(Self.fakePassphrase))
        XCTAssertFalse(rendered.contains(Self.fakeRecoveryKey))
        XCTAssertTrue(rendered.contains("<redacted>"))
    }

    func testPassphraseConsumeClearsDraftAndReturnsOneShotBuffer() async throws {
        let state = makeViewState(
            capabilities: fakeCapabilities(
                localKey: false,
                passphrase: true,
                recovery: false
            ),
            selectedMethod: .passphrase,
            status: .ready
        )
        var draft = AtlasExplicitUnlockInputDraft(
            passphrase: Self.fakePassphrase,
            recoveryKey: Self.fakeRecoveryKey
        )

        let submission = try XCTUnwrap(
            draft.consume(for: .passphrase, state: state)
        )

        XCTAssertTrue(draft.isEmpty)
        guard case let .passphrase(buffer) = submission else {
            XCTFail("Expected passphrase submission")
            return
        }
        let firstTake = try await buffer.takeSecretBytes()
        XCTAssertEqual(firstTake, Data(Self.fakePassphrase.utf8))
        do {
            _ = try await buffer.takeSecretBytes()
            XCTFail("Expected one-shot buffer")
        } catch let error as AtlasVaultSecretBufferError {
            XCTAssertEqual(error, .unavailable)
        }
        await buffer.clear()
        await buffer.clear()
        let inMemory = try XCTUnwrap(
            buffer as? AtlasVaultInMemorySecretBuffer
        )
        let isCleared = await inMemory.isClearedForTesting
        XCTAssertTrue(isCleared)
    }

    func testRecoveryConsumeClearsDraftAndRoutesRecoveryBuffer() async throws {
        let state = makeViewState(
            capabilities: fakeCapabilities(
                localKey: false,
                passphrase: false,
                recovery: true
            ),
            selectedMethod: .recoveryKey,
            status: .ready
        )
        var draft = AtlasExplicitUnlockInputDraft(
            passphrase: Self.fakePassphrase,
            recoveryKey: Self.fakeRecoveryKey
        )

        let submission = try XCTUnwrap(
            draft.consume(for: .recoveryKey, state: state)
        )

        XCTAssertTrue(draft.isEmpty)
        guard case let .recoveryKey(buffer) = submission else {
            XCTFail("Expected recovery-key submission")
            return
        }
        let bytes = try await buffer.takeSecretBytes()
        XCTAssertEqual(bytes, Data(Self.fakeRecoveryKey.utf8))
    }

    func testLocalKeyConsumeClearsResidualDraft() throws {
        let state = makeViewState(
            capabilities: .currentProduction,
            selectedMethod: nil,
            status: .locked
        )
        var draft = AtlasExplicitUnlockInputDraft(
            passphrase: Self.fakePassphrase,
            recoveryKey: Self.fakeRecoveryKey
        )

        let submission = try XCTUnwrap(
            draft.consume(for: .localKey, state: state)
        )

        XCTAssertTrue(draft.isEmpty)
        guard case .localKey = submission else {
            XCTFail("Expected local-key submission")
            return
        }
    }

    func testEmptyOrUnavailableSecretCannotBeConsumed() {
        let available = makeViewState(
            capabilities: fakeCapabilities(
                localKey: false,
                passphrase: true,
                recovery: false
            ),
            selectedMethod: .passphrase,
            status: .ready
        )
        var emptyDraft = AtlasExplicitUnlockInputDraft()
        XCTAssertNil(emptyDraft.consume(for: .passphrase, state: available))
        XCTAssertTrue(emptyDraft.isEmpty)

        let unavailable = makeViewState(
            capabilities: .currentProduction,
            selectedMethod: .passphrase,
            status: .methodUnavailable
        )
        var rejectedDraft = AtlasExplicitUnlockInputDraft(
            passphrase: Self.fakePassphrase,
            recoveryKey: Self.fakeRecoveryKey
        )
        XCTAssertNil(
            rejectedDraft.consume(for: .passphrase, state: unavailable)
        )
        XCTAssertTrue(rejectedDraft.isEmpty)
    }

    func testDraftClearCoversCancelMethodChangeDisappearanceAndTerminalState() {
        var draft = AtlasExplicitUnlockInputDraft(
            passphrase: Self.fakePassphrase,
            recoveryKey: Self.fakeRecoveryKey
        )

        draft.clear()
        XCTAssertTrue(draft.isEmpty)

        for status in [
            AtlasVaultUnlockPresentationStatus.locked,
            .methodUnavailable,
            .unlocked,
            .failed,
            .cancelled,
            .timedOut,
            .hostReconciliationRequired,
        ] {
            draft = AtlasExplicitUnlockInputDraft(
                passphrase: Self.fakePassphrase,
                recoveryKey: Self.fakeRecoveryKey
            )
            let state = makeViewState(
                capabilities: .currentProduction,
                selectedMethod: nil,
                status: status
            )
            if state.requiresInputClear {
                draft.clear()
            }
            XCTAssertTrue(draft.isEmpty, status.description)
        }
    }

    func testDraftDescriptionsNeverRevealSecret() {
        let draft = AtlasExplicitUnlockInputDraft(
            passphrase: Self.fakePassphrase,
            recoveryKey: Self.fakeRecoveryKey
        )
        let rendered = [
            String(describing: draft),
            String(reflecting: draft),
        ].joined(separator: "|")

        XCTAssertFalse(rendered.contains(Self.fakePassphrase))
        XCTAssertFalse(rendered.contains(Self.fakeRecoveryKey))
        XCTAssertEqual(
            rendered,
            "AtlasExplicitUnlockInputDraft(<redacted>)|"
                + "AtlasExplicitUnlockInputDraft(<redacted>)"
        )
    }

    func testSubmissionGateBlocksDuplicateUntilExactAttemptFinishes() throws {
        var gate = AtlasExplicitUnlockSubmissionGate()

        let attempt = try XCTUnwrap(gate.begin())

        XCTAssertTrue(gate.isActive)
        XCTAssertNil(gate.begin())
        gate.finish(UUID())
        XCTAssertTrue(gate.isActive)
        gate.finish(attempt)
        XCTAssertFalse(gate.isActive)
        XCTAssertNotNil(gate.begin())
        gate.cancel()
        XCTAssertFalse(gate.isActive)
    }

    func testClearBeforeAwaitAndDuplicateSubmissionPrevention() async throws {
        let state = makeViewState(
            capabilities: fakeCapabilities(
                localKey: false,
                passphrase: true,
                recovery: false
            ),
            selectedMethod: .passphrase,
            status: .ready
        )
        var draft = AtlasExplicitUnlockInputDraft(
            passphrase: Self.fakePassphrase
        )
        var gate = AtlasExplicitUnlockSubmissionGate()
        let attempt = try XCTUnwrap(gate.begin())
        let submission = try XCTUnwrap(
            draft.consume(for: .passphrase, state: state)
        )
        let recorder = GatedExplicitUnlockSubmitRecorder()
        let actions = AtlasExplicitUnlockViewActions(
            select: { _ in },
            submit: { submission in
                await recorder.submit(submission)
            },
            cancel: {},
            didDisappear: {}
        )

        let task = Task {
            await actions.submit(submission)
        }
        let started = await waitForSubmitStart(recorder)

        XCTAssertTrue(started)
        XCTAssertTrue(draft.isEmpty)
        XCTAssertNil(gate.begin())

        await recorder.release()
        await task.value
        gate.finish(attempt)
        XCTAssertFalse(gate.isActive)
    }

    func testInjectedActionsRouteSelectionSubmitCancelAndDisappearance() async {
        let recorder = ExplicitUnlockActionRecorder()
        let actions = makeActions(recorder: recorder)

        await actions.select(.passphrase)
        await actions.submit(.localKey)
        await actions.cancel()
        await actions.didDisappear()

        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.selections, [.passphrase])
        XCTAssertEqual(snapshot.submissions, ["localKey"])
        XCTAssertEqual(snapshot.cancelCount, 1)
        XCTAssertEqual(snapshot.disappearanceCount, 1)
    }

    func testViewConstructionInvokesNoAction() async {
        let recorder = ExplicitUnlockActionRecorder()
        let actions = makeActions(recorder: recorder)

        _ = AtlasExplicitUnlockView(
            state: makeViewState(
                capabilities: .currentProduction,
                selectedMethod: nil,
                status: .locked
            ),
            actions: actions
        )

        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.selections, [])
        XCTAssertEqual(snapshot.submissions, [])
        XCTAssertEqual(snapshot.cancelCount, 0)
        XCTAssertEqual(snapshot.disappearanceCount, 0)
    }

    func testSuccessfulUnlockSuppressesDisappearanceNotification() throws {
        let locked = makeViewState(
            capabilities: .currentProduction,
            selectedMethod: nil,
            status: .locked
        )
        let unlocked = makeViewState(
            capabilities: .currentProduction,
            selectedMethod: .localKey,
            status: .unlocked
        )

        XCTAssertTrue(locked.shouldNotifyDisappearance)
        XCTAssertFalse(unlocked.shouldNotifyDisappearance)

        let source = try source(named: "AtlasExplicitUnlockView.swift")
        XCTAssertTrue(
            source.contains("guard state.shouldNotifyDisappearance")
        )
    }

    func testSubmissionCancelsPriorViewOwnedActionBeforeReplacement() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")
        let cancellationBeforeSubmission = """
                cancelActiveAction()
                let actions = actions
        """

        XCTAssertEqual(
            source.components(
                separatedBy: cancellationBeforeSubmission
            ).count - 1,
            2
        )
        XCTAssertTrue(source.contains("activeAction?.cancel()"))
    }

    func testViewSourceUsesSecureFieldsAndOmitsRawTestKey() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")

        XCTAssertTrue(source.contains("SecureField"))
        XCTAssertFalse(source.contains("suppliedTestVaultKey"))
        XCTAssertFalse(source.contains("rawKey"))
    }

    func testViewSourceHasNoLegacyPrivateRuntimeOrEndpointAccess() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")

        for forbidden in [
            "AtlasRootView",
            "refreshSidebarData",
            "SearchViewModel",
            "AtlasAPIClient",
            "AtlasLocalCache",
            "/api/saved-searches",
            "/api/tracker",
            "savedSearches",
            "savedJobs",
            "applicationNotes",
            "profileSnippets",
            "draftMetadata",
            "generatedDocument",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testViewSourceHasNoProviderCryptoKeychainFilesystemOrNetworkAccess()
        throws
    {
        let source = try source(named: "AtlasExplicitUnlockView.swift")

        for forbidden in [
            "AtlasVaultKeyUnwrapping",
            "CryptoKit",
            "AtlasVaultRecordCrypto",
            "AtlasKeychain",
            "Keychain",
            "SecItem",
            "FileManager",
            "Data.write",
            "URLSession",
            "UserDefaults",
            "@AppStorage",
            "@SceneStorage",
            "LocalAuthentication",
            "LAContext",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testViewSourceHasNoAppEntryNavigationOrSaveBehavior() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")

        for forbidden in [
            "@main",
            "AtlasIOSHostApp",
            "NavigationStack",
            "NavigationLink",
            "AtlasVaultSave",
            "SaveOutcome",
            "durability",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testPureStateSourceHasNoUIProviderPersistenceOrSaveDependency()
        throws
    {
        let source = try source(
            named: "AtlasExplicitUnlockViewState.swift"
        )

        for forbidden in [
            "import SwiftUI",
            "Codable",
            "AtlasVaultKeyUnwrapping",
            "AtlasKeychain",
            "Keychain",
            "SecItem",
            "FileManager",
            "URLSession",
            "UserDefaults",
            "@AppStorage",
            "@SceneStorage",
            "AtlasVaultSave",
            "SaveOutcome",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testViewSourceUsesStaticAccessibilityAndPrivacyControls() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")

        XCTAssertTrue(source.contains(".accessibilityLabel("))
        XCTAssertTrue(source.contains(".accessibilityValue(\"Protected input\")"))
        XCTAssertTrue(source.contains(".autocorrectionDisabled(true)"))
        XCTAssertTrue(source.contains("textInputAutocapitalization(.never)"))
        XCTAssertFalse(source.contains("accessibilityValue(draft."))

        for placeholder in ["Passphrase", "Recovery key"] {
            let fieldStart = try XCTUnwrap(
                source.range(of: "SecureField(\"\(placeholder)\"")
            )
            let buttonStart = try XCTUnwrap(
                source.range(
                    of: "Button(\"Unlock\")",
                    range: fieldStart.upperBound..<source.endIndex
                )
            )
            let fieldSource = source[
                fieldStart.lowerBound..<buttonStart.lowerBound
            ]

            XCTAssertTrue(
                fieldSource.contains(".disabled("),
                placeholder
            )
            XCTAssertTrue(
                fieldSource.contains(
                    "state.controlsDisabled || submissionGate.isActive"
                ),
                placeholder
            )
        }
    }

    func testNoInventedFailureSubtypeAppearsInPhaseSources() throws {
        let source = try sourceFiles()

        for invented in [
            "wrongPassphrase",
            "wrongSecret",
            "corruptVault",
            "missingKeychain",
            "keyUnavailable",
        ] {
            XCTAssertFalse(source.contains(invented), invented)
        }
    }

    func testNoAtlasVaultArtifactExistsInWorktree() throws {
        let enumerator = FileManager.default.enumerator(
            at: repositoryRootURL(),
            includingPropertiesForKeys: [.isDirectoryKey],
            options: []
        )
        guard let enumerator else {
            XCTFail("Unable to enumerate the worktree")
            return
        }

        for case let url as URL in enumerator
        where url.pathExtension == "atlasvault" {
            XCTFail("Unexpected .atlasvault artifact")
            return
        }
    }

    private func makeViewState(
        capabilities: AtlasVaultUnlockCapabilities,
        selectedMethod: AtlasVaultUnlockMethod?,
        status: AtlasVaultUnlockPresentationStatus
    ) -> AtlasExplicitUnlockViewState {
        AtlasExplicitUnlockViewState(
            presentationState: AtlasVaultUnlockPresentationState(
                capabilities: capabilities,
                selectedMethod: selectedMethod,
                status: status
            )
        )
    }

    private func fakeCapabilities(
        localKey: Bool,
        passphrase: Bool,
        recovery: Bool
    ) -> AtlasVaultUnlockCapabilities {
        let provider = ExplicitUnlockNeverCalledUnwrapper()
        return AtlasVaultUnlockCapabilities(
            localKeyAvailable: localKey,
            passphraseProvider: passphrase ? provider : nil,
            recoveryKeyProvider: recovery ? provider : nil
        )
    }

    private func makeActions(
        recorder: ExplicitUnlockActionRecorder
    ) -> AtlasExplicitUnlockViewActions {
        AtlasExplicitUnlockViewActions(
            select: { method in
                await recorder.record(selection: method)
            },
            submit: { submission in
                await recorder.record(submission: submission)
            },
            cancel: {
                await recorder.recordCancel()
            },
            didDisappear: {
                await recorder.recordDisappearance()
            }
        )
    }

    private func waitForSubmitStart(
        _ recorder: GatedExplicitUnlockSubmitRecorder
    ) async -> Bool {
        for _ in 0..<200 {
            if await recorder.hasStarted {
                return true
            }
            await Task.yield()
        }
        return false
    }

    private func source(named filename: String) throws -> String {
        let url = sourceDirectoryURL().appendingPathComponent(filename)
        return try String(contentsOf: url, encoding: .utf8)
    }

    private func sourceFiles() throws -> String {
        try [
            "AtlasExplicitUnlockViewState.swift",
            "AtlasExplicitUnlockView.swift",
        ].map(source(named:)).joined(separator: "\n")
    }

    private func sourceDirectoryURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
    }

    private func repositoryRootURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}

private struct ExplicitUnlockNeverCalledUnwrapper: AtlasVaultKeyUnwrapping {
    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        XCTFail("Presentation capability provider must not be called")
        return Data(repeating: 0, count: 32)
    }
}

private struct ExplicitUnlockActionSnapshot: Sendable {
    let selections: [AtlasVaultUnlockMethod?]
    let submissions: [String]
    let cancelCount: Int
    let disappearanceCount: Int
}

private actor ExplicitUnlockActionRecorder {
    private var selections: [AtlasVaultUnlockMethod?] = []
    private var submissions: [String] = []
    private var cancelCount = 0
    private var disappearanceCount = 0

    func record(selection: AtlasVaultUnlockMethod?) {
        selections.append(selection)
    }

    func record(submission: AtlasVaultUnlockSubmission) async {
        switch submission {
        case .localKey:
            submissions.append("localKey")
        case let .passphrase(buffer):
            submissions.append("passphrase")
            await buffer.clear()
        case let .recoveryKey(buffer):
            submissions.append("recoveryKey")
            await buffer.clear()
        }
    }

    func recordCancel() {
        cancelCount += 1
    }

    func recordDisappearance() {
        disappearanceCount += 1
    }

    func snapshot() -> ExplicitUnlockActionSnapshot {
        ExplicitUnlockActionSnapshot(
            selections: selections,
            submissions: submissions,
            cancelCount: cancelCount,
            disappearanceCount: disappearanceCount
        )
    }
}

private actor GatedExplicitUnlockSubmitRecorder {
    private var started = false
    private var continuation: CheckedContinuation<Void, Never>?

    var hasStarted: Bool {
        started
    }

    func submit(_ submission: AtlasVaultUnlockSubmission) async {
        started = true
        await withCheckedContinuation { continuation in
            self.continuation = continuation
        }
        switch submission {
        case .localKey:
            return
        case let .passphrase(buffer), let .recoveryKey(buffer):
            await buffer.clear()
        }
    }

    func release() {
        continuation?.resume()
        continuation = nil
    }
}
