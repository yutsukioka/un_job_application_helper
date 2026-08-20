import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultPairingViewTests: XCTestCase {
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
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
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
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }
    func discardPairing() async -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(disposition: .cancelled)
    }
    func stop() async { released = true }
}
