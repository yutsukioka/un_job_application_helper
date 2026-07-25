import Foundation
import SwiftUI
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasLocalVaultCreationViewTests: XCTestCase {
    func testOwnerConstructionIsIdleAndExplicitPresentationIsRequired()
        async
    {
        let creator = CreationPresenterFake(result: .success(.created))
        let continuation = CreationContinuationRecorder(
            state: Self.lockedPanelState()
        )
        let owner = AtlasLocalVaultCreationPresentationOwner(
            creator: creator,
            continueToUnlock: {
                await continuation.call()
            }
        )

        XCTAssertEqual(owner.presentation, .hidden)
        XCTAssertFalse(owner.hasRetainedOperationForTesting)
        owner.beginCreateOrResume()
        await owner.waitForCurrentOperationForTesting()
        let hiddenCreateCount = await creator.createCount()
        let hiddenContinuationCount = await continuation.callCount()
        XCTAssertEqual(hiddenCreateCount, 0)
        XCTAssertEqual(hiddenContinuationCount, 0)

        owner.present()
        XCTAssertEqual(owner.presentation, .ready)
        let presentedCreateCount = await creator.createCount()
        XCTAssertEqual(presentedCreateCount, 0)
        XCTAssertTrue(owner.description.contains("<redacted>"))
    }

    func testSuccessContinuesOnceToUnselectedLockedUnlockPanel()
        async
    {
        let creator = CreationPresenterFake(result: .success(.created))
        let continuation = CreationContinuationRecorder(
            state: Self.lockedPanelState()
        )
        let owner = AtlasLocalVaultCreationPresentationOwner(
            creator: creator,
            continueToUnlock: {
                await continuation.call()
            }
        )
        owner.present()

        owner.beginCreateOrResume()
        owner.beginCreateOrResume()
        await owner.waitForCurrentOperationForTesting()

        let createCount = await creator.createCount()
        let continuationCount = await continuation.callCount()
        XCTAssertEqual(createCount, 1)
        XCTAssertEqual(continuationCount, 1)
        XCTAssertEqual(owner.presentation, .hidden)
        XCTAssertFalse(owner.hasRetainedOperationForTesting)
    }

    func testUnexpectedContinuationFailsClosed() async {
        let unexpectedStates = [
            Self.noVaultState(),
            Self.unlockedState(),
        ]

        for state in unexpectedStates {
            let creator = CreationPresenterFake(
                result: .success(.resumed)
            )
            let owner = AtlasLocalVaultCreationPresentationOwner(
                creator: creator,
                continueToUnlock: { state }
            )
            owner.present()
            owner.beginCreateOrResume()
            await owner.waitForCurrentOperationForTesting()
            XCTAssertEqual(owner.presentation, .recoveryRequired)
        }
    }

    func testFixedFailuresMapToPrivateFreeRetryStates() async {
        let cases: [
            (
                AtlasLocalVaultCreationFailure,
                AtlasLocalVaultCreationPresentation
            )
        ] = [
            (.unavailable, .failed),
            (
                .durabilityVerificationRequired,
                .durabilityVerificationRequired
            ),
            (.completionPending, .completionPending),
            (.recoveryRequired, .recoveryRequired),
            (.cancelled, .paused),
        ]

        for (failure, expected) in cases {
            let creator = CreationPresenterFake(
                result: .failure(failure)
            )
            let owner = AtlasLocalVaultCreationPresentationOwner(
                creator: creator,
                continueToUnlock: {
                    XCTFail("Failure continued to unlock")
                    return Self.noVaultState()
                }
            )
            owner.present()
            owner.beginCreateOrResume()
            await owner.waitForCurrentOperationForTesting()
            XCTAssertEqual(owner.presentation, expected)
        }
    }

    func testRetainedCreateCoalescesAndPauseDrains() async {
        let gate = CreationPresentationGate()
        let creator = CreationPresenterFake(
            result: .success(.created),
            gate: gate
        )
        let owner = AtlasLocalVaultCreationPresentationOwner(
            creator: creator,
            continueToUnlock: {
                Self.lockedPanelState()
            }
        )
        owner.present()
        owner.beginCreateOrResume()
        owner.beginCreateOrResume()
        await gate.waitUntilEntered()

        XCTAssertTrue(owner.hasRetainedOperationForTesting)
        XCTAssertEqual(owner.presentation, .creating)
        let createCount = await creator.createCount()
        XCTAssertEqual(createCount, 1)

        await owner.pause()

        let pauseCount = await creator.pauseCount()
        XCTAssertEqual(pauseCount, 1)
        XCTAssertEqual(owner.presentation, .paused)
        XCTAssertFalse(owner.hasRetainedOperationForTesting)
    }

    func testTerminalStopDrainsAndPreventsRestart() async {
        let gate = CreationPresentationGate()
        let creator = CreationPresenterFake(
            result: .success(.created),
            gate: gate
        )
        let owner = AtlasLocalVaultCreationPresentationOwner(
            creator: creator,
            continueToUnlock: {
                Self.lockedPanelState()
            }
        )
        owner.present()
        owner.beginCreateOrResume()
        await gate.waitUntilEntered()

        await owner.stop()

        XCTAssertEqual(owner.presentation, .hidden)
        XCTAssertFalse(owner.hasRetainedOperationForTesting)
        owner.present()
        owner.beginCreateOrResume()
        let createCount = await creator.createCount()
        XCTAssertEqual(createCount, 1)
    }

    func testCreationViewRequiresAcknowledgementAndHasNoSecretInput()
        throws
    {
        let source = try String(
            contentsOf: Self.sourceURL(
                named: "AtlasLocalVaultCreationView.swift"
            ),
            encoding: .utf8
        )

        for required in [
            "AtlasLocalVaultCreationPresentationOwner",
            "Create Local Vault",
            "device-local Keychain key only",
            "Toggle(",
            "acknowledgedDeviceLocalRisk",
            ".disabled(!primaryEnabled)",
            "Pause Setup",
            "Retry Setup",
            "recovery are not available yet",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "TextField(",
            "SecureField(",
            ".task",
            ".onAppear",
            "FileManager",
            "SecItem",
            "URLSession",
            "UserDefaults",
            "AtlasVaultPrivateState",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private static func lockedPanelState()
        -> AtlasLockedShellUnlockFlowState
    {
        AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                vaultStatus: .locked,
                serviceStatus: .available,
                cacheFreshness: .current,
                canRequestUnlock: false
            ),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: nil,
                status: .locked
            ),
            isUnlockPanelPresented: true
        )
    }

    private static func noVaultState()
        -> AtlasLockedShellUnlockFlowState
    {
        AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                vaultStatus: .noVault,
                serviceStatus: .available,
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

    private static func unlockedState()
        -> AtlasLockedShellUnlockFlowState
    {
        AtlasLockedShellUnlockFlowState(
            publicShell: AtlasLockedPublicShellModel(
                vaultStatus: .locked,
                serviceStatus: .available,
                canRequestUnlock: false
            ),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: .localKey,
                status: .unlocked
            ),
            isUnlockPanelPresented: false
        )
    }

    private static func sourceURL(named name: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
    }
}

private actor CreationPresenterFake: AtlasLocalVaultCreating {
    private let result: Result<
        AtlasLocalVaultCreationOutcome,
        AtlasLocalVaultCreationFailure
    >
    private let gate: CreationPresentationGate?
    private var creates = 0
    private var pauses = 0

    init(
        result: Result<
            AtlasLocalVaultCreationOutcome,
            AtlasLocalVaultCreationFailure
        >,
        gate: CreationPresentationGate? = nil
    ) {
        self.result = result
        self.gate = gate
    }

    func createOrResume()
        async throws(AtlasLocalVaultCreationFailure)
        -> AtlasLocalVaultCreationOutcome
    {
        creates += 1
        if let gate {
            await gate.wait()
        }
        return try result.get()
    }

    func pause() async {
        pauses += 1
        await gate?.release()
    }

    func createCount() -> Int {
        creates
    }

    func pauseCount() -> Int {
        pauses
    }
}

private actor CreationContinuationRecorder {
    private let state: AtlasLockedShellUnlockFlowState
    private var calls = 0

    init(state: AtlasLockedShellUnlockFlowState) {
        self.state = state
    }

    func call() -> AtlasLockedShellUnlockFlowState {
        calls += 1
        return state
    }

    func callCount() -> Int {
        calls
    }
}

private actor CreationPresentationGate {
    private var continuation:
        AsyncStream<Void>.Continuation?
    private var stream: AsyncStream<Void>?
    private var entered = false
    private var enteredWaiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if stream == nil {
            var captured: AsyncStream<Void>.Continuation?
            stream = AsyncStream { captured = $0 }
            continuation = captured
        }
        entered = true
        let waiters = enteredWaiters
        enteredWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
        var iterator = stream!.makeAsyncIterator()
        _ = await iterator.next()
    }

    func waitUntilEntered() async {
        guard !entered else {
            return
        }
        await withCheckedContinuation { continuation in
            enteredWaiters.append(continuation)
        }
    }

    func release() {
        continuation?.yield(())
        continuation?.finish()
    }
}
