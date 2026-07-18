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

    func testSelectedSecretMethodCanRetryAfterGenericTerminalStates()
        async throws
    {
        let capabilities = fakeCapabilities(
            localKey: false,
            passphrase: true,
            recovery: false
        )

        for status in [
            AtlasVaultUnlockPresentationStatus.failed,
            .cancelled,
            .timedOut,
        ] {
            let state = makeViewState(
                capabilities: capabilities,
                selectedMethod: .passphrase,
                status: status
            )
            var draft = AtlasExplicitUnlockInputDraft(
                passphrase: Self.fakePassphrase
            )

            XCTAssertTrue(state.showsPassphraseInput, status.description)
            XCTAssertTrue(state.permitsSubmission, status.description)
            let submission = try XCTUnwrap(
                draft.consume(for: .passphrase, state: state)
            )
            XCTAssertTrue(draft.isEmpty, status.description)
            await submission.clearExplicitUnlockSecret()
        }
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

    func testConsumeWipesIntermediateDataAfterBufferCopy() throws {
        let source = try source(
            named: "AtlasExplicitUnlockViewState.swift"
        )
        let wipe = """
                    bytes.resetBytes(
                        in: bytes.startIndex..<bytes.endIndex
                    )
        """

        XCTAssertEqual(
            source.components(separatedBy: wipe).count - 1,
            2
        )
        XCTAssertEqual(
            source.components(
                separatedBy: "let buffer = AtlasVaultInMemorySecretBuffer("
            ).count - 1,
            2
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
        _ = await task.value
        gate.finish(attempt)
        XCTAssertFalse(gate.isActive)
    }

    func testInjectedActionsRouteSelectionSubmitCancelAndDisappearance() async {
        let recorder = ExplicitUnlockActionRecorder()
        let actions = makeActions(recorder: recorder)

        await actions.select(.passphrase)
        _ = await actions.submit(.localKey)
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

    func testActiveDisappearanceDelegatesBeforeSubmissionReturns() async {
        let recorder = ExplicitUnlockActionRecorder()
        let submitRecorder = GatedExplicitUnlockSubmitRecorder(
            result: .unlocked
        )
        let actions = AtlasExplicitUnlockViewActions(
            select: { _ in },
            submit: { submission in
                await submitRecorder.submit(submission)
            },
            cancel: {},
            didDisappear: {
                await recorder.recordDisappearance()
            }
        )
        let authorization =
            AtlasExplicitUnlockDisappearanceAuthorization()
        let identifier = authorization.beginSubmission()

        let submitTask = Task {
            await actions.submit(.localKey)
        }
        let didStart = await waitForSubmitStart(submitRecorder)
        XCTAssertTrue(didStart)
        let disappearanceTask = Task {
            guard authorization.shouldNotifyDisappearance() else {
                return
            }
            await actions.didDisappear()
        }

        let notifiedBeforeCompletion = await waitForDisappearance(recorder)
        await submitRecorder.release()
        let result = await submitTask.value
        authorization.finishSubmission(identifier, status: result)
        XCTAssertEqual(result, .unlocked)
        await disappearanceTask.value

        let snapshot = await recorder.snapshot()
        XCTAssertTrue(notifiedBeforeCompletion)
        XCTAssertEqual(snapshot.disappearanceCount, 1)
    }

    func testDisappearanceStillDelegatesAfterFailedSubmission() async {
        let recorder = ExplicitUnlockActionRecorder()
        let submitRecorder = GatedExplicitUnlockSubmitRecorder(result: .failed)
        let actions = AtlasExplicitUnlockViewActions(
            select: { _ in },
            submit: { submission in
                await submitRecorder.submit(submission)
            },
            cancel: {},
            didDisappear: {
                await recorder.recordDisappearance()
            }
        )
        let authorization =
            AtlasExplicitUnlockDisappearanceAuthorization()
        let identifier = authorization.beginSubmission()

        let submitTask = Task {
            await actions.submit(.localKey)
        }
        let didStart = await waitForSubmitStart(submitRecorder)
        XCTAssertTrue(didStart)
        let disappearanceTask = Task {
            guard authorization.shouldNotifyDisappearance() else {
                return
            }
            await actions.didDisappear()
        }

        await submitRecorder.release()
        let result = await submitTask.value
        authorization.finishSubmission(identifier, status: result)
        XCTAssertEqual(result, .failed)
        await disappearanceTask.value

        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.disappearanceCount, 1)
    }

    func testCompletedUnlockSuppressesNextDisappearance() async {
        let recorder = ExplicitUnlockActionRecorder()
        let actions = AtlasExplicitUnlockViewActions(
            select: { _ in },
            submit: { submission in
                await recorder.record(submission: submission)
                return .unlocked
            },
            cancel: {},
            didDisappear: {
                await recorder.recordDisappearance()
            }
        )
        let authorization =
            AtlasExplicitUnlockDisappearanceAuthorization()
        let identifier = authorization.beginSubmission()

        let result = await actions.submit(.localKey)
        authorization.finishSubmission(identifier, status: result)
        if authorization.shouldNotifyDisappearance() {
            await actions.didDisappear()
        }

        XCTAssertEqual(result, .unlocked)
        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.disappearanceCount, 0)
    }

    func testActiveDisappearanceDelegatesBeforeLateUnlockCompletes() async {
        let recorder = ExplicitUnlockActionRecorder()
        let authorization =
            AtlasExplicitUnlockDisappearanceAuthorization()
        let identifier = authorization.beginSubmission()

        let disappearanceTask = Task {
            guard authorization.shouldNotifyDisappearance() else {
                return
            }
            await recorder.recordDisappearance()
        }

        let notifiedBeforeCompletion = await waitForDisappearance(recorder)
        authorization.finishSubmission(identifier, status: .unlocked)
        await disappearanceTask.value

        XCTAssertTrue(notifiedBeforeCompletion)
        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.disappearanceCount, 1)
        let shouldNotifyAgain =
            authorization.shouldNotifyDisappearance()
        XCTAssertTrue(shouldNotifyAgain)
    }

    func testDuplicateActiveDisappearanceDelegatesOnlyOnce() {
        let authorization =
            AtlasExplicitUnlockDisappearanceAuthorization()
        let identifier = authorization.beginSubmission()

        XCTAssertTrue(authorization.shouldNotifyDisappearance())
        XCTAssertFalse(authorization.shouldNotifyDisappearance())

        authorization.finishSubmission(identifier, status: .cancelled)
        XCTAssertTrue(authorization.shouldNotifyDisappearance())
    }

    func testAuthorizationUsesStructuredLockReleaseWithoutWaiters() throws {
        let source = try source(
            named: "AtlasExplicitUnlockViewState.swift"
        )
        let typeStart = try XCTUnwrap(
            source.range(
                of: "final class AtlasExplicitUnlockDisappearanceAuthorization"
            )
        )
        let typeEnd = try XCTUnwrap(
            source.range(
                of: "struct AtlasExplicitUnlockSubmissionGate",
                range: typeStart.upperBound..<source.endIndex
            )
        )
        let authorizationSource = String(
            source[typeStart.lowerBound..<typeEnd.lowerBound]
        )

        XCTAssertTrue(
            authorizationSource.contains("defer { lock.unlock() }")
        )
        XCTAssertFalse(
            authorizationSource.contains("disappearanceWaiters")
        )
    }

    func testViewOwnedAuthorizationSpansRebuiltActionValues() async {
        let recorder = ExplicitUnlockActionRecorder()
        let submitRecorder = GatedExplicitUnlockSubmitRecorder(
            result: .unlocked
        )
        let submittingActions = AtlasExplicitUnlockViewActions(
            select: { _ in },
            submit: { submission in
                await submitRecorder.submit(submission)
            },
            cancel: {},
            didDisappear: {}
        )
        let rebuiltActions = AtlasExplicitUnlockViewActions(
            select: { _ in },
            submit: { _ in .failed },
            cancel: {},
            didDisappear: {
                await recorder.recordDisappearance()
            }
        )
        let authorization =
            AtlasExplicitUnlockDisappearanceAuthorization()
        let identifier = authorization.beginSubmission()

        let submitTask = Task {
            await submittingActions.submit(.localKey)
        }
        let didStart = await waitForSubmitStart(submitRecorder)
        XCTAssertTrue(didStart)

        await submitRecorder.release()
        let result = await submitTask.value
        authorization.finishSubmission(identifier, status: result)
        if authorization.shouldNotifyDisappearance() {
            await rebuiltActions.didDisappear()
        }

        XCTAssertEqual(result, .unlocked)
        let snapshot = await recorder.snapshot()
        XCTAssertEqual(snapshot.disappearanceCount, 0)
    }

    func testDisappearanceAuthorizationIsViewOwnedAcrossRerenders() throws {
        let viewSource = try source(named: "AtlasExplicitUnlockView.swift")
        let stateSource = try source(
            named: "AtlasExplicitUnlockViewState.swift"
        )

        XCTAssertTrue(
            viewSource.contains(
                "@State private var disappearanceAuthorization"
            )
        )
        XCTAssertFalse(
            stateSource.contains(
                "private let disappearanceAuthorization"
            )
        )
    }

    func testLocalKeyTaskChecksCancellationBeforeSelection() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")
        let methodStart = try XCTUnwrap(
            source.range(of: "private func submitLocalKey()")
        )
        let methodEnd = try XCTUnwrap(
            source.range(
                of: "private func submitSecret(",
                range: methodStart.upperBound..<source.endIndex
            )
        )
        let method = String(
            source[methodStart.lowerBound..<methodEnd.lowerBound]
        )
        let taskStart = try XCTUnwrap(
            method.range(of: "activeAction = Task { @MainActor in")
        )
        let selection = try XCTUnwrap(
            method.range(of: "await actions.select(.localKey)")
        )
        let beforeSelection = method[
            taskStart.upperBound..<selection.lowerBound
        ]

        XCTAssertTrue(beforeSelection.contains("guard !Task.isCancelled"))
    }

    func testSubmissionRecordsResultBeforeSecretCleanupCanSuspend() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")
        let completionBeforeCleanup = """
                    disappearanceAuthorization.finishSubmission(
                        disappearanceID,
                        status: completionStatus
                    )
                    didFinishAuthorization = true
                    await submission.clearExplicitUnlockSecret()
        """

        XCTAssertEqual(
            source.components(
                separatedBy: completionBeforeCleanup
            ).count - 1,
            2
        )
    }

    func testSelectingCurrentMethodDoesNotClearDraftOrInvokeAction() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")
        let selectionStart = try XCTUnwrap(
            source.range(
                of: "private func selectMethod("
            )
        )
        let selectionEnd = try XCTUnwrap(
            source.range(
                of: "private func submitLocalKey()",
                range: selectionStart.upperBound..<source.endIndex
            )
        )
        let selectionSource = String(
            source[selectionStart.lowerBound..<selectionEnd.lowerBound]
        )
        let guardBeforeClear = """
                guard state.selectedMethod != method else {
                    return
                }
                clearInputAndCancelAction()
        """

        XCTAssertTrue(selectionSource.contains(guardBeforeClear))
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

    func testViewOwnedStateCleanupIsExplicitlyMainActorIsolated() throws {
        let source = try source(named: "AtlasExplicitUnlockView.swift")

        XCTAssertTrue(
            source.contains("@MainActor\npublic struct AtlasExplicitUnlockView")
        )
        XCTAssertEqual(
            source.components(
                separatedBy: "Task { @MainActor in"
            ).count - 1,
            3
        )
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
                return .failed
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

    private func waitForDisappearance(
        _ recorder: ExplicitUnlockActionRecorder
    ) async -> Bool {
        for _ in 0..<200 {
            if await recorder.snapshot().disappearanceCount > 0 {
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
    private let result: AtlasVaultUnlockPresentationStatus
    private var started = false
    private var continuation: CheckedContinuation<Void, Never>?

    init(result: AtlasVaultUnlockPresentationStatus = .failed) {
        self.result = result
    }

    var hasStarted: Bool {
        started
    }

    func submit(
        _ submission: AtlasVaultUnlockSubmission
    ) async -> AtlasVaultUnlockPresentationStatus {
        started = true
        await withCheckedContinuation { continuation in
            self.continuation = continuation
        }
        switch submission {
        case .localKey:
            break
        case let .passphrase(buffer), let .recoveryKey(buffer):
            await buffer.clear()
        }
        return result
    }

    func release() {
        continuation?.resume()
        continuation = nil
    }
}
