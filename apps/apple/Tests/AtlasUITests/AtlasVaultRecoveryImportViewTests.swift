import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultRecoveryImportViewTests: XCTestCase {
    func testConstructionIsSideEffectFreeAndPresentRestoresPendingState()
        async
    {
        let coordinator = RecoveryImportViewCoordinatorFake()
        await coordinator.setPending(true)
        let owner = AtlasVaultRecoveryImportPresentationOwner(
            coordinator: coordinator,
            continueToUnlock: {}
        )

        XCTAssertEqual(owner.presentation, .hidden)
        let constructionPendingChecks =
            await coordinator.pendingChecks()
        XCTAssertEqual(constructionPendingChecks, 0)
        await owner.present()

        XCTAssertEqual(owner.presentation, .paused)
        let firstPresentationPendingChecks =
            await coordinator.pendingChecks()
        XCTAssertEqual(firstPresentationPendingChecks, 1)
        let constructionCalls = await coordinator.calls()
        XCTAssertEqual(constructionCalls, [])

        owner.dismiss()
        await coordinator.setPending(false)
        await owner.present()

        XCTAssertEqual(owner.presentation, .ready)
        let secondPresentationPendingChecks =
            await coordinator.pendingChecks()
        XCTAssertEqual(secondPresentationPendingChecks, 2)
    }

    func testPresentFailsClosedWhenPendingJournalReadFails() async {
        let coordinator = RecoveryImportViewCoordinatorFake()
        await coordinator.setPendingReadFailure(true)
        let owner = AtlasVaultRecoveryImportPresentationOwner(
            coordinator: coordinator,
            continueToUnlock: {}
        )

        await owner.present()

        XCTAssertEqual(owner.presentation, .paused)
        let pendingChecks = await coordinator.pendingChecks()
        XCTAssertEqual(pendingChecks, 1)
        let calls = await coordinator.calls()
        XCTAssertEqual(calls, [])
    }

    func testExplicitFileSelectionAndRestoreOpenExistingUnlockPath()
        async throws
    {
        let coordinator = RecoveryImportViewCoordinatorFake()
        let continuation = RecoveryImportViewContinuation()
        let owner = AtlasVaultRecoveryImportPresentationOwner(
            coordinator: coordinator,
            continueToUnlock: {
                await continuation.call()
            }
        )
        await owner.present()

        await owner.prepareImport(
            from: URL(fileURLWithPath: "/TEST_ONLY/backup.atlasvault")
        )
        XCTAssertEqual(owner.presentation, .awaitingRecoveryKey)

        await owner.restore(
            secret: "TEST_ONLY_RECOVERY_KEY",
            confirmed: false
        )
        XCTAssertEqual(owner.presentation, .awaitingRecoveryKey)

        await owner.restore(
            secret: "TEST_ONLY_RECOVERY_KEY",
            confirmed: true
        )

        XCTAssertEqual(owner.presentation, .complete)
        let continuationCount = await continuation.count()
        let restoreCalls = await coordinator.calls()
        XCTAssertEqual(continuationCount, 1)
        XCTAssertEqual(restoreCalls, ["prepare", "confirm"])
    }

    func testPauseRetainsPersistentResumeButClearsPreparedImport()
        async
    {
        let coordinator = RecoveryImportViewCoordinatorFake()
        let owner = AtlasVaultRecoveryImportPresentationOwner(
            coordinator: coordinator,
            continueToUnlock: {}
        )
        await owner.present()
        await owner.prepareImport(
            from: URL(fileURLWithPath: "/TEST_ONLY/backup.atlasvault")
        )

        await owner.pause()

        XCTAssertEqual(owner.presentation, .ready)
        let pauseCalls = await coordinator.calls()
        XCTAssertEqual(pauseCalls, ["prepare", "pause"])

        await coordinator.setPending(true)
        await owner.present()
        await owner.pause()
        XCTAssertEqual(owner.presentation, .paused)
    }

    func testPreconfirmationDismissRequiresExplicitDrainedPause()
        async
    {
        let coordinator = RecoveryImportViewCoordinatorFake()
        let owner = AtlasVaultRecoveryImportPresentationOwner(
            coordinator: coordinator,
            continueToUnlock: {}
        )
        await owner.present()
        await owner.prepareImport(
            from: URL(fileURLWithPath: "/TEST_ONLY/backup.atlasvault")
        )
        XCTAssertEqual(owner.presentation, .awaitingRecoveryKey)

        owner.dismiss()

        XCTAssertEqual(owner.presentation, .awaitingRecoveryKey)
        await owner.pause()
        XCTAssertEqual(owner.presentation, .ready)
        owner.dismiss()
        XCTAssertEqual(owner.presentation, .hidden)
        let calls = await coordinator.calls()
        XCTAssertEqual(calls, ["prepare", "pause"])
    }

    func testResumeRequiresFileAndRecoveryKeyAndResetIsConfirmed()
        async
    {
        let coordinator = RecoveryImportViewCoordinatorFake()
        await coordinator.setPending(true)
        let owner = AtlasVaultRecoveryImportPresentationOwner(
            coordinator: coordinator,
            continueToUnlock: {}
        )
        await owner.present()

        await owner.resume(
            from: URL(fileURLWithPath: "/TEST_ONLY/backup.atlasvault"),
            secret: "TEST_ONLY_RECOVERY"
        )
        XCTAssertEqual(owner.presentation, .complete)

        await owner.present()
        await owner.resetPendingImport(confirmed: false)
        let callsBeforeReset = await coordinator.calls()
        XCTAssertFalse(callsBeforeReset.contains("reset"))
        await owner.resetPendingImport(confirmed: true)
        let callsAfterReset = await coordinator.calls()
        XCTAssertTrue(callsAfterReset.contains("reset"))
        XCTAssertEqual(owner.presentation, .ready)
    }

    func testUnsafeLifecycleHidesPresentationAndDrainsCoordinator()
        async
    {
        let coordinator = RecoveryImportViewCoordinatorFake()
        let owner = AtlasVaultRecoveryImportPresentationOwner(
            coordinator: coordinator,
            continueToUnlock: {}
        )
        await owner.present()

        await owner.dismissForUnsafeLifecycle()

        XCTAssertEqual(owner.presentation, .hidden)
        let lifecycleCalls = await coordinator.calls()
        XCTAssertEqual(lifecycleCalls, ["pause"])
    }

    func testPrivateFreeImportViewSurface() throws {
        let source = try phaseSource("AtlasVaultRecoveryImportView.swift")

        for required in [
            "Restore Encrypted Backup",
            "fileImporter",
            "SecureField",
            "Restore Vault",
            "Pause Restore",
            "Resume Restore",
            "Discard Incomplete Restore",
            "@State",
            "claimPresentation",
            "releasePresentation",
            "ownsPresentation",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            ".task",
            ".onAppear",
            "UIPasteboard",
            "NSPasteboard",
            "AtlasVaultPrivateState",
            "vaultID",
            "exportID",
            "storeID",
            "FileManager",
            "Keychain",
            "SecItem",
            "URLSession",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testRestoreViewForwardsTheExplicitConfirmation() throws {
        let source = try phaseSource("AtlasVaultRecoveryImportView.swift")

        XCTAssertTrue(
            source.contains(
                "let submittedConfirmation = confirmedRestore"
            )
        )
        XCTAssertTrue(
            source.contains(
                "actions.restore(submitted, submittedConfirmation)"
            )
        )
        XCTAssertFalse(
            source.contains("actions.restore(submitted, true)")
        )
    }

    func testFileImporterNeverBroadensToGenericData() throws {
        let source = try phaseSource("AtlasVaultRecoveryImportView.swift")

        XCTAssertTrue(
            source.contains(
                "UTType(importedAs: \"com.atlasvault.encrypted-backup\")"
            )
        )
        XCTAssertFalse(source.contains("?? .data"))
    }

    func testPendingImportAvailabilityIsExplicitAndReversible() {
        let availability = AtlasVaultRecoveryImportAvailability()

        XCTAssertFalse(availability.hasPendingImport)
        availability.setPendingImport(true)
        XCTAssertTrue(availability.hasPendingImport)
        availability.setPendingImport(false)
        XCTAssertFalse(availability.hasPendingImport)
    }

    private func phaseSource(_ name: String) throws -> String {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
        return try String(contentsOf: sourceURL, encoding: .utf8)
    }
}

private actor RecoveryImportViewContinuation {
    private var calls = 0

    func call() {
        calls += 1
    }

    func count() -> Int {
        calls
    }
}

private actor RecoveryImportViewCoordinatorFake:
    AtlasVaultRecoveryImportCoordinating
{
    private var recordedCalls: [String] = []
    private var pending = false
    private var pendingReadFails = false
    private var recordedPendingChecks = 0

    func setPending(_ value: Bool) {
        pending = value
    }

    func setPendingReadFailure(_ value: Bool) {
        pendingReadFails = value
    }

    func calls() -> [String] {
        recordedCalls
    }

    func pendingChecks() -> Int {
        recordedPendingChecks
    }

    func prepareImport(from _: URL) async throws {
        recordedCalls.append("prepare")
    }

    func confirmAndImport(
        recoverySecret secret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome {
        recordedCalls.append("confirm")
        _ = try await secret.takeSecretBytes()
        pending = false
        return .committed
    }

    func resumeImport(
        from _: URL,
        recoverySecret secret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome {
        recordedCalls.append("resume")
        _ = try await secret.takeSecretBytes()
        pending = false
        return .resumed
    }

    func finishCommittedImport(
        from _: URL,
        recoverySecret secret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome {
        recordedCalls.append("finish")
        _ = try await secret.takeSecretBytes()
        pending = false
        return .resumed
    }

    func resetPendingImport() async throws {
        recordedCalls.append("reset")
        pending = false
    }

    func hasPendingImport() async throws -> Bool {
        recordedPendingChecks += 1
        if pendingReadFails {
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
        return pending
    }

    func pause() async {
        recordedCalls.append("pause")
    }

    func stop() async {
        recordedCalls.append("stop")
    }
}
