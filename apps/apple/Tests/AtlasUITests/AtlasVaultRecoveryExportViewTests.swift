import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRecoveryExportViewTests: XCTestCase {
    func testExplicitPrivateFreeRecoveryExportViewSurfaceExists() throws {
        let source = try phaseSource("AtlasVaultRecoveryExportView.swift")

        for required in [
            "Recovery & Encrypted Export",
            "Restart Recovery Setup",
            "SecureField",
            "@State",
            "fileExporter",
            "import",
            "claimPresentation",
            "releasePresentation",
            "ownsPresentation",
            ".accessibilityValue(displayedRecoveryCode)",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "UIPasteboard",
            "NSPasteboard",
            ".task",
            ".onAppear",
            "AtlasVaultPrivateState",
            "savedSearch",
            "savedJob",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    @MainActor
    func testOwnerConstructionAndPresentationAreSideEffectFree()
        async throws
    {
        let fake = try RecoveryExportCoordinatorViewFake(
            document: makeDocument()
        )
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: fake
        )

        XCTAssertEqual(owner.presentation, .hidden)
        let initialCalls = await fake.calls()
        XCTAssertEqual(initialCalls, [])

        owner.present()

        XCTAssertEqual(owner.presentation, .ready)
        let presentedCalls = await fake.calls()
        XCTAssertEqual(presentedCalls, [])
    }

    @MainActor
    func testGenerateReturnsOneShotCodeWithoutPublishingSecret()
        async throws
    {
        let fake = try RecoveryExportCoordinatorViewFake(
            document: makeDocument()
        )
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: fake
        )
        owner.present()

        let generatedHandle = await owner.generate()
        let handle = try XCTUnwrap(generatedHandle)
        let generatedCode = await handle.take()
        let code = try XCTUnwrap(generatedCode)

        XCTAssertEqual(owner.presentation, .awaitingConfirmation)
        XCTAssertTrue(code.hasPrefix("AVRK1-"))
        XCTAssertFalse(owner.description.contains(code))
        let calls = await fake.calls()
        XCTAssertEqual(calls, ["prepare"])
    }

    @MainActor
    func testConfirmationProducesEncryptedDocumentThenCompletesOnSave()
        async throws
    {
        let document = try makeDocument()
        let fake = RecoveryExportCoordinatorViewFake(document: document)
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: fake
        )
        owner.present()
        _ = await owner.generate()

        let preparedDocument = await owner.confirm(
            secret: "FAKE_TEST_ONLY_RECOVERY"
        )
        let prepared = try XCTUnwrap(preparedDocument)

        XCTAssertEqual(prepared.encryptedData, document.encryptedData)
        XCTAssertEqual(owner.presentation, .exportReady)

        await owner.exportDidFinish(success: true)

        XCTAssertEqual(owner.presentation, .complete)
        let calls = await fake.calls()
        XCTAssertEqual(calls, ["prepare", "confirm", "exportSuccess"])
    }

    @MainActor
    func testFailureMappingResumeAndExplicitResetAreFixed()
        async throws
    {
        let fake = try RecoveryExportCoordinatorViewFake(
            document: makeDocument()
        )
        await fake.setFailure(.pendingSetupRequiresRecoveryKey)
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: fake
        )
        owner.present()

        let failedGeneration = await owner.generate()
        XCTAssertNil(failedGeneration)
        XCTAssertEqual(owner.presentation, .resumeRequired)

        await fake.setFailure(nil)
        let resumedDocument = await owner.resume(
            secret: "FAKE_TEST_ONLY_RECOVERY"
        )
        XCTAssertNotNil(resumedDocument)
        XCTAssertEqual(owner.presentation, .exportReady)

        await owner.exportDidFinish(success: false)
        XCTAssertEqual(owner.presentation, .resumeRequired)

        await owner.resetPendingSetup(confirmed: false)
        XCTAssertEqual(owner.presentation, .resumeRequired)
        await owner.resetPendingSetup(confirmed: true)
        XCTAssertEqual(owner.presentation, .ready)
    }

    @MainActor
    func testDurabilityAndCompletionPendingStatesPermitExplicitReset()
        async throws
    {
        let durabilityFake = try RecoveryExportCoordinatorViewFake(
            document: makeDocument()
        )
        await durabilityFake.setFailure(
            .durabilityVerificationRequired
        )
        let durabilityOwner =
            AtlasVaultRecoveryExportPresentationOwner(
                coordinator: durabilityFake
            )
        durabilityOwner.present()

        let durabilityGeneration = await durabilityOwner.generate()
        XCTAssertNil(durabilityGeneration)
        XCTAssertEqual(
            durabilityOwner.presentation,
            .durabilityVerificationRequired
        )
        await durabilityFake.setFailure(nil)
        await durabilityOwner.resetPendingSetup(confirmed: true)
        XCTAssertEqual(durabilityOwner.presentation, .ready)
        let durabilityCalls = await durabilityFake.calls()
        XCTAssertEqual(durabilityCalls, ["prepare", "reset"])

        let completionFake = try RecoveryExportCoordinatorViewFake(
            document: makeDocument()
        )
        let completionOwner =
            AtlasVaultRecoveryExportPresentationOwner(
                coordinator: completionFake
        )
        completionOwner.present()
        _ = await completionOwner.generate()
        let completionDocument = await completionOwner.confirm(
            secret: "FAKE_TEST_ONLY_RECOVERY"
        )
        XCTAssertNotNil(completionDocument)
        await completionFake.setFailure(.completionPending)
        await completionOwner.exportDidFinish(success: true)
        XCTAssertEqual(
            completionOwner.presentation,
            .completionPending
        )
        await completionFake.setFailure(nil)
        await completionOwner.resetPendingSetup(confirmed: true)
        XCTAssertEqual(completionOwner.presentation, .ready)
        let completionCalls = await completionFake.calls()
        XCTAssertEqual(
            completionCalls,
            ["prepare", "confirm", "exportSuccess", "reset"]
        )
    }

    @MainActor
    func testWrongInitialConfirmationRemainsExplicitlyRetryable()
        async throws
    {
        let fake = try RecoveryExportCoordinatorViewFake(
            document: makeDocument()
        )
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: fake
        )
        owner.present()
        _ = await owner.generate()
        await fake.setFailure(.invalidConfirmation)

        let rejected = await owner.confirm(secret: "WRONG_TEST_VALUE")

        XCTAssertNil(rejected)
        XCTAssertEqual(owner.presentation, .awaitingConfirmation)
        await fake.setFailure(nil)
        let retried = await owner.confirm(secret: "SAVED_TEST_VALUE")
        XCTAssertNotNil(retried)
        XCTAssertEqual(owner.presentation, .exportReady)
    }

    @MainActor
    func testPresentationClaimPreventsDuplicateSceneOwnership()
        async throws
    {
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: try RecoveryExportCoordinatorViewFake(
                document: makeDocument()
            )
        )
        let first = AtlasVaultRecoveryExportPresentationClaim()
        let second = AtlasVaultRecoveryExportPresentationClaim()
        owner.present()

        XCTAssertTrue(owner.claimPresentation(first))
        XCTAssertTrue(owner.ownsPresentation(first))
        XCTAssertFalse(owner.claimPresentation(second))
        XCTAssertFalse(owner.ownsPresentation(second))
        XCTAssertFalse(owner.releasePresentation(second))
        XCTAssertTrue(owner.releasePresentation(first))
    }

    @MainActor
    func testPauseAndStopDrainCoordinatorAndHideSecretBearingUI()
        async throws
    {
        let fake = try RecoveryExportCoordinatorViewFake(
            document: makeDocument()
        )
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: fake
        )
        owner.present()
        _ = await owner.generate()

        await owner.pause()
        XCTAssertEqual(owner.presentation, .paused)

        await owner.stop()
        XCTAssertEqual(owner.presentation, .hidden)
        let calls = await fake.calls()
        XCTAssertEqual(calls, ["prepare", "exportCancel", "stop"])
    }

    @MainActor
    func testUnsafeLifecycleDismissesSharedPresentationClaim()
        async throws
    {
        let fake = try RecoveryExportCoordinatorViewFake(
            document: makeDocument()
        )
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: fake
        )
        let claim = AtlasVaultRecoveryExportPresentationClaim()
        owner.present()
        XCTAssertTrue(owner.claimPresentation(claim))

        await owner.dismissForUnsafeLifecycle()

        XCTAssertEqual(owner.presentation, .hidden)
        XCTAssertFalse(owner.ownsPresentation(claim))
        let calls = await fake.calls()
        XCTAssertEqual(calls, ["exportCancel"])
    }

    @MainActor
    func testGeneratedCodeCannotPublishAfterSceneClaimIsInvalidated()
        async throws
    {
        let owner = AtlasVaultRecoveryExportPresentationOwner(
            coordinator: try RecoveryExportCoordinatorViewFake(
                document: makeDocument()
            )
        )
        let claim = AtlasVaultRecoveryExportPresentationClaim()
        owner.present()
        XCTAssertTrue(owner.claimPresentation(claim))
        let generatedHandle = await owner.generate()
        let handle = try XCTUnwrap(generatedHandle)

        await owner.dismissForUnsafeLifecycle()
        let invalidatedCode = await handle.take()
        XCTAssertNotNil(invalidatedCode)

        XCTAssertEqual(owner.presentation, .hidden)
        XCTAssertFalse(owner.ownsPresentation(claim))
        XCTAssertFalse(owner.canPublishGeneratedCode(for: claim))
    }

    func testViewKeepsSecretsAndEncryptedDocumentInLocalStateOnly()
        throws
    {
        let source = try phaseSource("AtlasVaultRecoveryExportView.swift")
        let publishedLines = source.split(separator: "\n").filter {
            $0.contains("@Published")
        }

        XCTAssertEqual(publishedLines.count, 2)
        XCTAssertTrue(
            source.contains("@State private var displayedRecoveryCode")
        )
        XCTAssertTrue(source.contains("@State private var confirmationEntry"))
        XCTAssertTrue(source.contains("@State private var resumeEntry"))
        XCTAssertTrue(source.contains("@State private var encryptedDocument"))
        XCTAssertFalse(source.contains("UIPasteboard"))
        XCTAssertFalse(source.contains("NSPasteboard"))
    }

    private func makeDocument() throws -> AtlasVaultEncryptedDocument {
        let data = try Data(contentsOf: try vectorFileURL())
        let root = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: data)
                as? [String: Any]
        )
        let vectors = try XCTUnwrap(root["vectors"] as? [[String: Any]])
        let vector = try XCTUnwrap(vectors.first)
        let encoded = try XCTUnwrap(
            vector["canonical_export_json_b64"] as? String
        )
        let canonical = try XCTUnwrap(Data(base64Encoded: encoded))
        return try AtlasVaultEncryptedDocument(
            verifiedEncryptedData: canonical
        )
    }

    private func vectorFileURL() throws -> URL {
        let current = URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath
        )
        let source = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let name = "atlasvault_recovery_export_vectors_v2.json"
        let candidates = [
            current.appendingPathComponent(
                "../../contracts/sync/test_vectors/\(name)"
            ),
            current.appendingPathComponent(
                "contracts/sync/test_vectors/\(name)"
            ),
            source.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/\(name)"
            ),
        ].map(\.standardizedFileURL)
        return try XCTUnwrap(
            candidates.first {
                FileManager.default.fileExists(atPath: $0.path)
            }
        )
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

private actor RecoveryExportCoordinatorViewFake:
    AtlasVaultRecoveryExportCoordinating
{
    private let document: AtlasVaultEncryptedDocument
    private var recordedCalls: [String] = []
    private var failure: AtlasVaultRecoveryExportFailure?

    init(document: AtlasVaultEncryptedDocument) {
        self.document = document
    }

    func prepareNewRecovery() async throws
        -> AtlasVaultRecoveryDisplayCodeHandle
    {
        recordedCalls.append("prepare")
        try failIfNeeded()
        let code = try AtlasVaultRecoveryKeyCodec.canonicalText(
            for: Data((0..<32).map(UInt8.init))
        )
        return AtlasVaultRecoveryDisplayCodeHandle(code: code)
    }

    func confirmAndPrepareExport(
        secret _: String
    ) async throws -> AtlasVaultEncryptedDocument {
        recordedCalls.append("confirm")
        try failIfNeeded()
        return document
    }

    func resumeAndPrepareExport(
        secret _: String
    ) async throws -> AtlasVaultEncryptedDocument {
        recordedCalls.append("resume")
        try failIfNeeded()
        return document
    }

    func exportDidSucceed() async throws {
        recordedCalls.append("exportSuccess")
        try failIfNeeded()
    }

    func exportDidFailOrCancel() async {
        recordedCalls.append("exportCancel")
    }

    func resetPendingSetup() async throws {
        recordedCalls.append("reset")
        try failIfNeeded()
    }

    func hasPendingSetup() async throws -> Bool {
        recordedCalls.append("pending")
        try failIfNeeded()
        return false
    }

    func stop() async {
        recordedCalls.append("stop")
    }

    func setFailure(_ value: AtlasVaultRecoveryExportFailure?) {
        failure = value
    }

    func calls() -> [String] {
        recordedCalls
    }

    private func failIfNeeded() throws {
        if let failure {
            throw failure
        }
    }
}
