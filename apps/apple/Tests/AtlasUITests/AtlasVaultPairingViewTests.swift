import Combine
import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultPairingViewTests: XCTestCase {
    func testC30LiveComparisonIsBoundAndClearedOnConfirmation() async {
        let transcript = String(repeating: "a", count: 64)
        let coordinator = PairingViewCancellationCoordinator(response: .init(
            disposition: .codesReady, stage: .acceptanceImported,
            sas: "ABCD-EF12-3456", transcriptSHA256: transcript,
            expiresAt: "2026-01-01T00:05:00Z", pendingTransaction: true))
        let owner = AtlasVaultTrustedPairingPresentationOwner(coordinator: coordinator,
            now: { Date(timeIntervalSince1970: 1767225600) })
        owner.resumePairing()
        for _ in 0..<1000 { if !owner.isBusy { break }; await Task.yield() }
        XCTAssertTrue(owner.sas != nil)
        let resultDescription = await coordinator.responseDescription()
        XCTAssertFalse(resultDescription.contains(owner.sas ?? "missing"))
        await coordinator.setResponse(.init(disposition: .codesConfirmed))
        owner.confirmCodesMatch()
        XCTAssertTrue(owner.sas == nil)
        for _ in 0..<1000 { if !owner.isBusy { break }; await Task.yield() }
        let confirmed = await coordinator.confirmedTranscript()
        XCTAssertEqual(confirmed, transcript)
        owner.confirmCodesMatch()
        let count = await coordinator.confirmationCount()
        XCTAssertEqual(count, 1)
        await owner.stopAndDrain()
    }

    func testC30ExpiryTaskClearsComparisonWithStationaryWallClock() async throws {
        let coordinator = PairingViewCancellationCoordinator(response: .init(
            disposition: .codesReady, stage: .acceptanceImported,
            sas: "ABCD-EF12-3456", transcriptSHA256: String(repeating: "a", count: 64),
            expiresAt: "1970-01-01T00:00:01Z", pendingTransaction: true))
        let owner = AtlasVaultTrustedPairingPresentationOwner(coordinator: coordinator,
            now: { Date(timeIntervalSince1970: 0.9) })
        owner.resumePairing()
        for _ in 0..<1000 { if !owner.isBusy { break }; await Task.yield() }
        XCTAssertTrue(owner.sas != nil)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertTrue(owner.sas == nil)
        owner.confirmCodesMatch()
        let count = await coordinator.confirmationCount()
        XCTAssertEqual(count, 0)
        await owner.stopAndDrain()
    }

    func testC30UnboundComparisonAndDismissalAreFenced() async {
        let coordinator = PairingViewCancellationCoordinator(response: .init(
            disposition: .codesReady, stage: .acceptanceImported,
            sas: "ABCD-EF12-3456", expiresAt: "2026-01-01T00:05:00Z", pendingTransaction: true))
        let owner = AtlasVaultTrustedPairingPresentationOwner(coordinator: coordinator,
            now: { Date(timeIntervalSince1970: 1767225600) })
        owner.resumePairing()
        for _ in 0..<1000 { if !owner.isBusy { break }; await Task.yield() }
        XCTAssertTrue(owner.sas == nil)
        owner.dismiss()
        owner.confirmCodesMatch()
        let count = await coordinator.confirmationCount()
        XCTAssertEqual(count, 0)
        await owner.stopAndDrain()
    }

    func testC30ConsumedAndExpiredComparisonIsNotDisplayed() async {
        for disposition: AtlasVaultTrustedPairingDisposition in [.completed, .codesConfirmed, .failed, .cancelled, .codesReady] {
            let coordinator = PairingViewCancellationCoordinator(response: .init(
                disposition: disposition, stage: .acceptanceImported,
                sas: "ABCD-EF12-3456", expiresAt: "2000-01-01T00:00:00Z",
                pendingTransaction: true))
            let owner = AtlasVaultTrustedPairingPresentationOwner(coordinator: coordinator)
            owner.resumePairing()
            for _ in 0..<1000 { if !owner.isBusy { break }; await Task.yield() }
            XCTAssertFalse(owner.isBusy)
            XCTAssertTrue(owner.sas == nil)
            await owner.stopAndDrain()
        }
    }

    func testC30ConfirmationRequiresDisplayedCeremony() async {
        let coordinator = PairingViewCancellationCoordinator()
        let owner = AtlasVaultTrustedPairingPresentationOwner(coordinator: coordinator)
        owner.confirmCodesMatch()
        for _ in 0..<1000 { if !owner.isBusy { break }; await Task.yield() }
        let count = await coordinator.confirmationCount()
        XCTAssertEqual(count, 0)
        await owner.stopAndDrain()
    }

    func testPairingViewExposesOnlyExplicitActions() throws {
        let source = try Self.source(named: "AtlasVaultPairingView.swift")

        for action in [
            "Create Device Identity",
            "Create Pairing Offer",
            "Save Pairing Offer",
            "Import Pairing Offer",
            "Save Pairing Acceptance",
            "Import Pairing Acceptance",
            "Codes Match",
            "Save Key Delivery",
            "Import Key Delivery",
            "Save Pairing Acknowledgement",
            "Import Pairing Acknowledgement",
            "Resume Pairing",
            "Discard Pairing",
        ] {
            XCTAssertTrue(source.contains(action), action)
        }
        for forbidden in [
            "privateKey",
            "vaultKey",
            "sessionKey",
            "ephemeralPrivateKey",
            "backendCredential",
            ".task",
            ".onAppear",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testPairingOwnerRetainsOneOperationAndDrainsOnStop() throws {
        let source = try Self.source(named: "AtlasVaultPairingView.swift")

        for required in [
            "AtlasVaultTrustedPairingPresentationOwner",
            "AtlasVaultTrustedPairingContext",
            "AtlasVaultTrustedPairingCoordinating",
            "operationTask",
            "createDeviceIdentity",
            "createPairingOffer",
            "confirmCodesMatch",
            "resumePairing",
            "discardPairing",
            "stopAndDrain",
            "operationTask?.cancel()",
            "await retained?.value",
            "defer",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
    }

    func testCodesMatchIsDisabledWhenSensitiveCodeIsAbsent() throws {
        let source = try Self.source(named: "AtlasVaultPairingView.swift")

        XCTAssertTrue(
            source.contains(
                "Button(\"Codes Match\") { owner.confirmCodesMatch() }\n" +
                    "                .disabled(owner.sas == nil)"
            )
        )
    }

    func testInteractiveDismissIsDisabledWhilePairingIsBusy() throws {
        let source = try Self.source(
            named: "AtlasVaultProductionRootView.swift"
        )

        XCTAssertTrue(
            source.contains(".interactiveDismissDisabled(owner.isBusy)")
        )
    }

    func testSuccessfulExportsAreReadBackBeforeJournalAdvance() throws {
        let source = try Self.source(named: "AtlasVaultPairingView.swift")

        for required in [
            "completePendingSave(at:",
            "savedPairingArtifactMatches",
            "readArtifact(from: url)",
            "constantTimeEqual",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        XCTAssertFalse(
            source.contains("committed: (try? result.get()) != nil")
        )
    }

    func testArtifactImportUsesAnOpenedBoundedFileHandle() throws {
        let source = try Self.source(named: "AtlasVaultPairingView.swift")

        XCTAssertTrue(source.contains("FileHandle("))
        XCTAssertTrue(source.contains("read(upToCount:"))
        XCTAssertTrue(source.contains("fstat("))
        XCTAssertTrue(source.contains("O_NOFOLLOW"))
        XCTAssertFalse(source.contains("contentsOf: url"))
    }

    func testBoundedArtifactReaderRejectsBeforeOverflowAppend() throws {
        var chunks = [Data([1, 2, 3]), Data([4, 5])]

        XCTAssertThrowsError(
            try AtlasVaultTrustedPairingPresentationOwner
                .readBoundedArtifactData(maximumByteCount: 4) { _ in
                    chunks.isEmpty ? nil : chunks.removeFirst()
                }
        )
    }

    func testUnsafeLifecycleCancellationDrainsTheRetainedOperation()
        async
    {
        let coordinator = PairingViewCancellationCoordinator()
        let owner = AtlasVaultTrustedPairingPresentationOwner(
            coordinator: coordinator
        )
        owner.createPairingOffer()
        await coordinator.waitUntilStarted()

        await owner.clearSensitiveInput()
        let observedCancellation = await coordinator.observedCancellation()
        await coordinator.release()
        await owner.stopAndDrain()

        XCTAssertTrue(observedCancellation)
        XCTAssertFalse(owner.isBusy)
    }

    func testPairingOwnerPublishesBusyBeforeOperationSideEffects() async {
        let coordinator = PairingViewCancellationCoordinator()
        let owner = AtlasVaultTrustedPairingPresentationOwner(
            coordinator: coordinator
        )
        var publicationCount = 0
        let observation = owner.objectWillChange.sink {
            publicationCount += 1
        }

        owner.createPairingOffer()
        await coordinator.waitUntilStarted()

        XCTAssertTrue(owner.isBusy)
        XCTAssertGreaterThan(publicationCount, 0)

        await coordinator.release()
        await owner.stopAndDrain()
        withExtendedLifetime(observation) {}
    }

    private static func source(named name: String) throws -> String {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let url = root
            .appendingPathComponent("apps/apple/Sources/AtlasUI")
            .appendingPathComponent(name)
        return try String(contentsOf: url, encoding: .utf8)
    }
}

private actor PairingViewCancellationCoordinator:
    AtlasVaultTrustedPairingCoordinating
{
    private var response: AtlasVaultTrustedPairingResult
    private var transcript: String?
    func setResponse(_ result: AtlasVaultTrustedPairingResult) { response = result }
    func responseDescription() -> String { String(reflecting: response) }
    func confirmedTranscript() -> String? { transcript }
    private var confirmations = 0
    init(response: AtlasVaultTrustedPairingResult = .init(disposition: .cancelled)) {
        self.response = response
    }
    func confirmationCount() -> Int { confirmations }
    private var started = false
    private var released = false
    private var cancelled = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func waitUntilStarted() async {
        guard !started else { return }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func release() { released = true }
    func observedCancellation() -> Bool { cancelled }

    func createPairingOffer() async -> AtlasVaultTrustedPairingResult {
        started = true
        let pending = waiters
        waiters.removeAll()
        for waiter in pending { waiter.resume() }
        while !released {
            if Task.isCancelled {
                cancelled = true
                break
            }
            await Task.yield()
        }
        return AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }

    func inspect() async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .ready)
    }
    func createDeviceIdentity() async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .identityReady)
    }
    func artifactToSave(
        _ kind: AtlasVaultPairingArtifactKind
    ) async throws -> AtlasVaultPairingArtifact {
        throw AtlasVaultPairingTransactionError.unavailable
    }
    func pairingArtifactSaveFinished(
        _ kind: AtlasVaultPairingArtifactKind,
        committed: Bool
    ) async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }
    func importPairingOffer(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }
    func importPairingAcceptance(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }
    func confirmCodesMatch() async -> AtlasVaultTrustedPairingResult {
        confirmations += 1
        return response
    }
    func confirmCodesMatch(expectedTranscriptSHA256: String) async -> AtlasVaultTrustedPairingResult {
        confirmations += 1
        transcript = expectedTranscriptSHA256
        return response
    }
    func importKeyDelivery(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }
    func importPairingAcknowledgement(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }
    func resumePairing() async -> AtlasVaultTrustedPairingResult {
        response
    }
    func discardPairing() async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }
    func stop() async { released = true }
}
