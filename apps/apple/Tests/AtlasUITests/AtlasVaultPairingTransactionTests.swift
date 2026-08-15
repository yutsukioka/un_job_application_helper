import CryptoKit
import Foundation
import Security
import Synchronization
@testable import AtlasUI
import XCTest

final class AtlasVaultPairingTransactionTests: XCTestCase {
    func testDeviceFingerprintIsCanonicalAndCrossPlatformStable() {
        XCTAssertEqual(
            AtlasVaultPairingFoundation.deviceFingerprint(
                "avd1-e198a89d6d338fb22211bf507c441956c8775741a7c3e8dd7c9b003db935ce35"
            ),
            "E198-A89D-6D33-8FB2"
        )
        XCTAssertNil(AtlasVaultPairingFoundation.deviceFingerprint("invalid"))
    }

    func testCoordinatorCompletesExplicitBilateralJourneyInReviewedOrder()
        async throws
    {
        let timestamp = "2026-08-15T12:00:00Z"
        let inviterIdentity = try AtlasVaultDeviceIdentity.generate(
            createdAt: timestamp
        )
        let inviteeIdentity = try AtlasVaultDeviceIdentity.generate(
            createdAt: timestamp
        )
        let bootstrap = try AtlasVaultPairingBootstrap.decodeStrict(
            try vectorData(key: "bootstrap_canonical_b64")
        )
        var vaultKey = try vectorData(key: "test_only_vault_key_b64")
        defer { vaultKey.resetBytes(in: 0..<vaultKey.count) }
        let store = AtlasVaultLocalStoreEnvelope(
            storeID: "43000000-0000-4000-8000-000000000001",
            createdAt: timestamp,
            updatedAt: timestamp,
            vaultMetadata: try bootstrap.vaultMetadata.localStoreMetadata(),
            records: bootstrap.records
        )
        let activeVault = try AtlasVaultPairingActiveVault(
            vaultID: bootstrap.vaultMetadata.vaultID,
            store: store,
            keyMaterial: vaultKey
        )
        let inviterState = PairingCoordinatorState(
            identity: inviterIdentity,
            activeVault: activeVault,
            cleanInstall: .existingVault
        )
        let inviteeState = PairingCoordinatorState(
            identity: inviteeIdentity,
            activeVault: nil,
            cleanInstall: .clean
        )
        let identifiers = PairingIdentifierSequence()
        let random = PairingRandomSequence()
        let clock = PairingClock(timestamp)
        let inviter = AtlasVaultTrustedPairingCoordinator(
            environment: pairingEnvironment(
                state: inviterState,
                identifiers: identifiers,
                random: random,
                timestamp: { clock.now() }
            )
        )
        let invitee = AtlasVaultTrustedPairingCoordinator(
            environment: pairingEnvironment(
                state: inviteeState,
                identifiers: identifiers,
                random: random,
                timestamp: { clock.now() }
            )
        )

        let inviterInitial = await inviter.inspect()
        let inviteeInitial = await invitee.inspect()
        XCTAssertEqual(inviterInitial.disposition, .identityReady)
        XCTAssertEqual(inviteeInitial.disposition, .identityReady)
        let offerReady = await inviter.createPairingOffer()
        XCTAssertEqual(offerReady.disposition, .offerReady)
        let offer = try await inviter.artifactToSave(.offer)
        let offerSaved = await inviter.pairingArtifactSaveFinished(
            .offer,
            committed: true
        )
        XCTAssertEqual(offerSaved.disposition, .offerSaved)
        let acceptanceReady = await invitee.importPairingOffer(offer)
        XCTAssertEqual(acceptanceReady.disposition, .acceptanceReady)
        let acceptance = try await invitee.artifactToSave(.acceptance)
        let acceptanceSaved = await invitee.pairingArtifactSaveFinished(
            .acceptance,
            committed: true
        )
        XCTAssertEqual(acceptanceSaved.disposition, .acceptanceSaved)
        let inviterCodes = await inviter.importPairingAcceptance(acceptance)
        let inviteeCodes = await invitee.inspect()
        XCTAssertEqual(inviterCodes.disposition, .codesReady)
        XCTAssertEqual(inviterCodes.sas, inviteeCodes.sas)
        XCTAssertNotNil(inviterCodes.sas)

        let inviterConfirmed = await inviter.confirmCodesMatch()
        let inviteeConfirmed = await invitee.confirmCodesMatch()
        XCTAssertEqual(inviterConfirmed.disposition, .deliveryReady)
        XCTAssertEqual(inviteeConfirmed.disposition, .codesConfirmed)
        let delivery = try await inviter.artifactToSave(.delivery)
        let deliverySaved = await inviter.pairingArtifactSaveFinished(
            .delivery,
            committed: true
        )
        XCTAssertEqual(deliverySaved.disposition, .deliverySaved)
        clock.set("2026-08-15T12:09:00Z")
        let acknowledgementReady = await invitee.importKeyDelivery(delivery)
        XCTAssertEqual(
            acknowledgementReady.disposition,
            .acknowledgementReady
        )
        let acknowledgement = try await invitee.artifactToSave(
            .acknowledgement
        )
        XCTAssertEqual(
            try acknowledgement.signedAcknowledgement()
                .acknowledgement.installedAt,
            "2026-08-15T12:09:00Z"
        )
        let acknowledgementSaved = await invitee
            .pairingArtifactSaveFinished(
                .acknowledgement,
                committed: true
            )
        XCTAssertEqual(acknowledgementSaved.disposition, .completed)
        let inviterCompleted = await inviter.importPairingAcknowledgement(
            acknowledgement
        )
        XCTAssertEqual(inviterCompleted.disposition, .completed)

        let inviteeSnapshot = await inviteeState.snapshot()
        let inviterSnapshot = await inviterState.snapshot()
        XCTAssertEqual(inviteeSnapshot.selectedVault, activeVault.vaultID)
        XCTAssertEqual(
            inviteeSnapshot.stores[activeVault.vaultID]?.records,
            store.records
        )
        XCTAssertEqual(inviteeSnapshot.registry?.devices.count, 1)
        XCTAssertEqual(inviterSnapshot.registry?.devices.count, 1)
        XCTAssertNil(inviteeSnapshot.transaction)
        XCTAssertNil(inviterSnapshot.transaction)
        XCTAssertLessThan(
            try XCTUnwrap(inviteeSnapshot.events.firstIndex(of: "store.create")),
            try XCTUnwrap(inviteeSnapshot.events.firstIndex(of: "key.create"))
        )
        XCTAssertLessThan(
            try XCTUnwrap(inviteeSnapshot.events.firstIndex(of: "key.create")),
            try XCTUnwrap(
                inviteeSnapshot.events.firstIndex(of: "selection.create")
            )
        )
        XCTAssertLessThan(
            try XCTUnwrap(
                inviteeSnapshot.events.firstIndex(of: "runtime.activate")
            ),
            try XCTUnwrap(
                inviteeSnapshot.events.lastIndex(of: "registry.commit")
            )
        )
        XCTAssertLessThan(
            try XCTUnwrap(
                inviteeSnapshot.events.lastIndex(of: "registry.commit")
            ),
            try XCTUnwrap(
                inviteeSnapshot.events.firstIndex(
                    of: "artifact.create:acknowledgement"
                )
            )
        )
        XCTAssertEqual(inviteeSnapshot.events.last, "transaction.delete")
        XCTAssertEqual(inviterSnapshot.events.last, "transaction.delete")
    }

    func testInviterJournalsOfferBeforeStagingAndCanDiscard() async throws {
        let journey = try makeJourney()

        let offer = await journey.inviter.createPairingOffer()
        XCTAssertEqual(offer.disposition, .offerReady)
        let created = await journey.inviterState.snapshot()
        XCTAssertLessThan(
            try XCTUnwrap(created.events.firstIndex(of: "transaction.create")),
            try XCTUnwrap(
                created.events.firstIndex(of: "artifact.create:offer")
            )
        )

        let discard = await journey.inviter.discardPairing()
        XCTAssertEqual(discard.disposition, .identityReady)
        let discarded = await journey.inviterState.snapshot()
        XCTAssertNil(discarded.transaction)
        XCTAssertEqual(discarded.selectedVault, journey.vaultID)
    }

    func testOfferStageFailureLeavesDiscardableJournal() async throws {
        let journey = try makeJourney(inviterArtifactFailure: .offer)

        let offer = await journey.inviter.createPairingOffer()
        XCTAssertEqual(offer.disposition, .recoveryRequired)
        let interrupted = await journey.inviterState.loadTransaction()
        let staged = await journey.inviterState.loadArtifact(.offer)
        XCTAssertNotNil(interrupted)
        XCTAssertNil(staged)
        let discard = await journey.inviter.discardPairing()
        XCTAssertEqual(discard.disposition, .identityReady)
        let cleared = await journey.inviterState.loadTransaction()
        XCTAssertNil(cleared)
    }

    func testExpiredAppleKeyRequestCannotCreateDelivery() async throws {
        let journey = try makeJourney()
        try await exchangeAcceptance(journey)
        journey.clock.set("2026-08-15T12:11:00Z")

        let result = await journey.inviter.confirmCodesMatch()
        let transaction = await journey.inviterState.loadTransaction()
        let delivery = await journey.inviterState.loadArtifact(.delivery)
        XCTAssertEqual(result.disposition, .recoveryRequired)
        XCTAssertEqual(transaction?.stage, .acceptanceImported)
        XCTAssertNil(delivery)
    }

    func testAppleSelectionInterruptionResumesWithoutRecreation() async throws {
        let journey = try makeJourney(
            inviteeReplaceFailure: .selectionCommitted
        )
        let delivery = try await prepareDelivery(journey)

        let interrupted = await journey.invitee.importKeyDelivery(delivery)
        let transaction = await journey.inviteeState.loadTransaction()
        let selected = await journey.inviteeState.selected()
        XCTAssertEqual(interrupted.disposition, .recoveryRequired)
        XCTAssertEqual(transaction?.stage, .keyCreated)
        XCTAssertEqual(selected, journey.vaultID)

        let resumed = await journey.invitee.resumePairing()
        XCTAssertEqual(resumed.disposition, .acknowledgementReady)
        let snapshot = await journey.inviteeState.snapshot()
        XCTAssertEqual(
            snapshot.events.filter { $0 == "selection.create" }.count,
            1
        )
    }

    func testAppleTrustRetryUsesStableInstallationTime() async throws {
        let journey = try makeJourney(
            inviteeReplaceFailure: .trustCommitted
        )
        let delivery = try await prepareDelivery(journey)
        journey.clock.set("2026-08-15T12:09:00Z")

        let interrupted = await journey.invitee.importKeyDelivery(delivery)
        XCTAssertEqual(interrupted.disposition, .recoveryRequired)
        let interruptedSnapshot = await journey.inviteeState.snapshot()
        let committed = try XCTUnwrap(
            interruptedSnapshot.registry?.devices.first
        )
        XCTAssertEqual(committed.linkedAt, "2026-08-15T12:09:00Z")
        journey.clock.set("2026-08-15T12:10:00Z")

        let resumed = await journey.invitee.resumePairing()
        XCTAssertEqual(resumed.disposition, .acknowledgementReady)
        let resumedSnapshot = await journey.inviteeState.snapshot()
        XCTAssertEqual(
            resumedSnapshot.registry?.devices.first,
            committed
        )
    }

    func testAppleAcknowledgementSavedResumeCompletesCleanup() async throws {
        let journey = try makeJourney(inviteeDeleteFailures: 1)
        let delivery = try await prepareDelivery(journey)
        let imported = await journey.invitee.importKeyDelivery(delivery)
        XCTAssertEqual(imported.disposition, .acknowledgementReady)

        let interrupted = await journey.invitee.pairingArtifactSaveFinished(
            .acknowledgement,
            committed: true
        )
        XCTAssertEqual(interrupted.disposition, .recoveryRequired)
        let transaction = await journey.inviteeState.loadTransaction()
        XCTAssertEqual(transaction?.stage, .acknowledgementSaved)

        let resumed = await journey.invitee.resumePairing()
        XCTAssertEqual(resumed.disposition, .completed)
        let cleared = await journey.inviteeState.loadTransaction()
        XCTAssertNil(cleared)
    }

    func testStrictTransactionAndKeychainStoresUseDeviceOnlyServices() throws {
        let transaction = try AtlasVaultPairingTransaction.decodeStrict(
            Data(Self.transactionJSON.utf8)
        )
        XCTAssertEqual(transaction.role, .invitee)
        XCTAssertEqual(transaction.stage, .acceptanceCreated)
        XCTAssertEqual(
            try transaction.canonicalData(),
            Data(Self.transactionJSON.utf8)
        )

        let client = PairingStateFakeKeychainClient()
        let registry = AtlasKeychainTrustedDeviceRegistryStore(client: client)
        let replay = AtlasKeychainPairingReplayStore(client: client)
        let journal = AtlasKeychainPairingTransactionStore(client: client)

        XCTAssertEqual(type(of: registry).service, "com.atlasvault.trusted-devices.v1")
        XCTAssertEqual(type(of: replay).service, "com.atlasvault.pairing-replay.v1")
        XCTAssertEqual(type(of: journal).service, "com.atlasvault.pairing-transaction.v1")

        try journal.create(transaction)
        XCTAssertEqual(try journal.load(), transaction)
        XCTAssertEqual(client.lastAdded?.accessibility, .afterFirstUnlockThisDeviceOnly)
        XCTAssertEqual(client.lastAdded?.account, "pending-v1")
        XCTAssertThrowsError(try journal.create(transaction)) { error in
            XCTAssertEqual(error as? AtlasVaultPairingStateStoreError, .collision)
        }
        try journal.replace(
            transaction,
            expectedSHA256: transaction.sha256Hex()
        )
        XCTAssertThrowsError(
            try journal.replace(
                transaction,
                expectedSHA256: String(repeating: "0", count: 64)
            )
        ) { error in
            XCTAssertEqual(error as? AtlasVaultPairingStateStoreError, .stale)
        }
        try journal.delete(expectedSHA256: try transaction.sha256Hex())
        XCTAssertNil(try journal.load())
        client.overwrite(
            service: type(of: journal).service,
            account: "pending-v1",
            data: Data("{}".utf8)
        )
        XCTAssertThrowsError(try journal.load())

        XCTAssertThrowsError(
            try AtlasVaultPairingTransaction.decodeStrict(
                Data(repeating: 0, count: AtlasVaultPairingTransaction.maximumByteCount + 1)
            )
        )
    }

    func testRegistryAndReplayUseCreateOnlyCASAndVerifyDescriptors() throws {
        let registry = try AtlasVaultTrustedDeviceRegistry.decodeStrict(
            try vectorData(key: "trusted_registry_canonical_b64")
        )
        let replay = try AtlasVaultPairingReplayStore.decodeStrict(
            try vectorData(key: "replay_store_canonical_b64")
        )
        let client = PairingStateFakeKeychainClient()
        let registryStore = AtlasKeychainTrustedDeviceRegistryStore(client: client)
        let replayStore = AtlasKeychainPairingReplayStore(client: client)

        try registryStore.create(registry)
        XCTAssertEqual(try registryStore.load(), registry)
        XCTAssertEqual(client.lastAdded?.service, "com.atlasvault.trusted-devices.v1")
        XCTAssertEqual(client.lastAdded?.account, "state-v1")
        XCTAssertEqual(client.lastAdded?.accessibility, .afterFirstUnlockThisDeviceOnly)
        XCTAssertThrowsError(try registryStore.create(registry))
        try registryStore.replace(
            registry,
            expectedSHA256: sha256Hex(try registry.canonicalData())
        )
        XCTAssertThrowsError(
            try registryStore.replace(
                registry,
                expectedSHA256: String(repeating: "0", count: 64)
            )
        )

        try replayStore.create(replay)
        XCTAssertEqual(try replayStore.load(), replay)
        try replayStore.replace(
            replay,
            expectedSHA256: sha256Hex(try replay.canonicalData())
        )
        XCTAssertThrowsError(
            try replayStore.replace(
                replay,
                expectedSHA256: String(repeating: "0", count: 64)
            )
        )
    }

    func testSandboxStageIsHashBoundAndCleansUp() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(
            UUID().uuidString,
            isDirectory: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try AtlasVaultPairingArtifactStageStore(root: root)
        let artifact = try AtlasVaultPairingArtifact.decodeStrict(
            try vectorArtifact(kind: "offer")
        )
        try store.create(artifact)
        XCTAssertThrowsError(try store.create(artifact))
        XCTAssertEqual(
            try store.read(kind: .offer)?.canonicalData(),
            try artifact.canonicalData()
        )
        XCTAssertThrowsError(
            try store.delete(
                kind: .offer,
                expectedSHA256: String(repeating: "0", count: 64)
            )
        )
        XCTAssertNotNil(try store.read(kind: .offer))

        let staged = root.appendingPathComponent("offer.atlaspair")
        var tampered = try Data(contentsOf: staged)
        tampered[tampered.startIndex] ^= 1
        try tampered.write(to: staged, options: .atomic)
        XCTAssertThrowsError(try store.read(kind: .offer))
        try artifact.canonicalData().write(to: staged, options: .atomic)
        try store.delete(
            kind: .offer,
            expectedSHA256: try artifact.sha256Hex()
        )
        XCTAssertNil(try store.read(kind: .offer))
    }

    private func makeJourney(
        inviterArtifactFailure: AtlasVaultPairingArtifactKind? = nil,
        inviteeReplaceFailure: AtlasVaultPairingStage? = nil,
        inviteeDeleteFailures: Int = 0
    ) throws -> PairingJourneyHarness {
        let timestamp = "2026-08-15T12:00:00Z"
        let inviterIdentity = try AtlasVaultDeviceIdentity.generate(
            createdAt: timestamp
        )
        let inviteeIdentity = try AtlasVaultDeviceIdentity.generate(
            createdAt: timestamp
        )
        let bootstrap = try AtlasVaultPairingBootstrap.decodeStrict(
            vectorData(key: "bootstrap_canonical_b64")
        )
        var vaultKey = try vectorData(key: "test_only_vault_key_b64")
        defer { vaultKey.resetBytes(in: 0..<vaultKey.count) }
        let store = AtlasVaultLocalStoreEnvelope(
            storeID: "43000000-0000-4000-8000-000000000001",
            createdAt: timestamp,
            updatedAt: timestamp,
            vaultMetadata: try bootstrap.vaultMetadata.localStoreMetadata(),
            records: bootstrap.records
        )
        let activeVault = try AtlasVaultPairingActiveVault(
            vaultID: bootstrap.vaultMetadata.vaultID,
            store: store,
            keyMaterial: vaultKey
        )
        let inviterState = PairingCoordinatorState(
            identity: inviterIdentity,
            activeVault: activeVault,
            cleanInstall: .existingVault,
            failArtifactCreateKind: inviterArtifactFailure
        )
        let inviteeState = PairingCoordinatorState(
            identity: inviteeIdentity,
            activeVault: nil,
            cleanInstall: .clean,
            failTransactionReplaceStage: inviteeReplaceFailure,
            failTransactionReplaceCount: inviteeReplaceFailure == nil ? 0 : 1,
            failTransactionDeleteCount: inviteeDeleteFailures
        )
        let identifiers = PairingIdentifierSequence()
        let random = PairingRandomSequence()
        let clock = PairingClock(timestamp)
        return PairingJourneyHarness(
            inviter: AtlasVaultTrustedPairingCoordinator(
                environment: pairingEnvironment(
                    state: inviterState,
                    identifiers: identifiers,
                    random: random,
                    timestamp: { clock.now() }
                )
            ),
            invitee: AtlasVaultTrustedPairingCoordinator(
                environment: pairingEnvironment(
                    state: inviteeState,
                    identifiers: identifiers,
                    random: random,
                    timestamp: { clock.now() }
                )
            ),
            inviterState: inviterState,
            inviteeState: inviteeState,
            clock: clock,
            vaultID: activeVault.vaultID
        )
    }

    private func exchangeAcceptance(_ journey: PairingJourneyHarness)
        async throws
    {
        let offerReady = await journey.inviter.createPairingOffer()
        XCTAssertEqual(offerReady.disposition, .offerReady)
        let offer = try await journey.inviter.artifactToSave(.offer)
        let offerSaved = await journey.inviter.pairingArtifactSaveFinished(
            .offer,
            committed: true
        )
        XCTAssertEqual(offerSaved.disposition, .offerSaved)
        let acceptanceReady = await journey.invitee.importPairingOffer(offer)
        XCTAssertEqual(acceptanceReady.disposition, .acceptanceReady)
        let acceptance = try await journey.invitee.artifactToSave(.acceptance)
        let acceptanceSaved = await journey.invitee.pairingArtifactSaveFinished(
            .acceptance,
            committed: true
        )
        XCTAssertEqual(acceptanceSaved.disposition, .acceptanceSaved)
        let codes = await journey.inviter.importPairingAcceptance(acceptance)
        XCTAssertEqual(codes.disposition, .codesReady)
    }

    private func prepareDelivery(
        _ journey: PairingJourneyHarness
    ) async throws -> AtlasVaultPairingArtifact {
        try await exchangeAcceptance(journey)
        let inviterConfirmed = await journey.inviter.confirmCodesMatch()
        let inviteeConfirmed = await journey.invitee.confirmCodesMatch()
        XCTAssertEqual(inviterConfirmed.disposition, .deliveryReady)
        XCTAssertEqual(inviteeConfirmed.disposition, .codesConfirmed)
        let delivery = try await journey.inviter.artifactToSave(.delivery)
        let deliverySaved = await journey.inviter.pairingArtifactSaveFinished(
            .delivery,
            committed: true
        )
        XCTAssertEqual(deliverySaved.disposition, .deliverySaved)
        return delivery
    }

    private func vectorArtifact(kind: String) throws -> Data {
        let root = try JSONSerialization.jsonObject(
            with: Data(contentsOf: vectorURL)
        ) as? [String: Any]
        let artifacts = root?["artifacts"] as? [String: Any]
        let artifact = artifacts?[kind] as? [String: Any]
        guard
            let encoded = artifact?["canonical_b64"] as? String,
            let data = Data(base64Encoded: encoded)
        else { throw PairingTransactionTestError.invalidVector }
        return data
    }

    private func vectorData(key: String) throws -> Data {
        let root = try JSONSerialization.jsonObject(
            with: Data(contentsOf: vectorURL)
        ) as? [String: Any]
        guard
            let encoded = root?[key] as? String,
            let data = Data(base64Encoded: encoded)
        else { throw PairingTransactionTestError.invalidVector }
        return data
    }

    private func sha256Hex(_ data: Data) -> String {
        Data(SHA256.hash(data: data))
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private var vectorURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("contracts/sync/test_vectors")
            .appendingPathComponent(
                "atlasvault_trusted_pairing_delivery_vectors_v1.json"
            )
    }

    private static let transactionJSON =
        #"{"acceptance_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","acknowledgement_sha256":null,"bootstrap_sha256":null,"created_at":"2026-08-15T10:00:00Z","delivery_sha256":null,"ephemeral_private_key":"AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=","format":"atlasvault-pairing-transaction","installed_at":null,"key_epoch":null,"local_device_id":"avd1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","offer_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","parent_revision":null,"peer_device_id":"avd1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","revision":"42000000-0000-4000-8000-000000000002","role":"invitee","selection_committed":false,"stage":"acceptance_created","staged_artifacts":[],"store_sha256":null,"transaction_id":"42000000-0000-4000-8000-000000000001","transcript_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","updated_at":"2026-08-15T10:01:00Z","vault_id":null,"vault_key_sha256":null,"version":1}"#
}

private struct PairingJourneyHarness {
    let inviter: AtlasVaultTrustedPairingCoordinator
    let invitee: AtlasVaultTrustedPairingCoordinator
    let inviterState: PairingCoordinatorState
    let inviteeState: PairingCoordinatorState
    let clock: PairingClock
    let vaultID: String
}

private struct PairingCoordinatorSnapshot: Sendable {
    let transaction: AtlasVaultPairingTransaction?
    let registry: AtlasVaultTrustedDeviceRegistry?
    let stores: [String: AtlasVaultLocalStoreEnvelope]
    let selectedVault: String?
    let events: [String]
}

private actor PairingCoordinatorState {
    private let identity: AtlasVaultDeviceIdentity
    private let inviterActiveVault: AtlasVaultPairingActiveVault?
    private let cleanInstallDisposition:
        AtlasVaultPairingCleanInstallDisposition
    private var transaction: AtlasVaultPairingTransaction?
    private var artifacts: [String: AtlasVaultPairingArtifact] = [:]
    private var registry: AtlasVaultTrustedDeviceRegistry?
    private var replay: AtlasVaultPairingReplayStore?
    private var stores: [String: AtlasVaultLocalStoreEnvelope] = [:]
    private var keys: [String: Data] = [:]
    private var selectedVault: String?
    private var active = false
    private var events: [String] = []
    private let failArtifactCreateKind: AtlasVaultPairingArtifactKind?
    private let failTransactionReplaceStage: AtlasVaultPairingStage?
    private var failTransactionReplaceCount: Int
    private var failTransactionDeleteCount: Int

    init(
        identity: AtlasVaultDeviceIdentity,
        activeVault: AtlasVaultPairingActiveVault?,
        cleanInstall: AtlasVaultPairingCleanInstallDisposition,
        failArtifactCreateKind: AtlasVaultPairingArtifactKind? = nil,
        failTransactionReplaceStage: AtlasVaultPairingStage? = nil,
        failTransactionReplaceCount: Int = 0,
        failTransactionDeleteCount: Int = 0
    ) {
        self.identity = identity
        inviterActiveVault = activeVault
        cleanInstallDisposition = cleanInstall
        selectedVault = activeVault?.vaultID
        self.failArtifactCreateKind = failArtifactCreateKind
        self.failTransactionReplaceStage = failTransactionReplaceStage
        self.failTransactionReplaceCount = failTransactionReplaceCount
        self.failTransactionDeleteCount = failTransactionDeleteCount
    }

    func loadIdentity() -> AtlasVaultDeviceIdentity { identity }
    func activeVault() -> AtlasVaultPairingActiveVault? {
        inviterActiveVault
    }
    func cleanInstall() -> AtlasVaultPairingCleanInstallDisposition {
        selectedVault == nil ? cleanInstallDisposition : .existingVault
    }

    func loadTransaction() -> AtlasVaultPairingTransaction? { transaction }
    func createTransaction(_ value: AtlasVaultPairingTransaction) throws {
        guard transaction == nil else {
            throw AtlasVaultPairingTransactionError.collision
        }
        transaction = value
        events.append("transaction.create")
    }
    func replaceTransaction(
        _ value: AtlasVaultPairingTransaction,
        expectedSHA256: String
    ) throws {
        if value.stage == failTransactionReplaceStage,
           failTransactionReplaceCount > 0
        {
            failTransactionReplaceCount -= 1
            throw AtlasVaultPairingTransactionError.unavailable
        }
        guard let transaction,
              try transaction.sha256Hex() == expectedSHA256 else {
            throw AtlasVaultPairingTransactionError.stale
        }
        self.transaction = value
        events.append("transaction.replace")
    }
    func deleteTransaction(expectedSHA256: String) throws {
        if failTransactionDeleteCount > 0 {
            failTransactionDeleteCount -= 1
            throw AtlasVaultPairingTransactionError.unavailable
        }
        guard let transaction,
              try transaction.sha256Hex() == expectedSHA256 else {
            throw AtlasVaultPairingTransactionError.stale
        }
        self.transaction = nil
        events.append("transaction.delete")
    }

    func loadArtifact(
        _ kind: AtlasVaultPairingArtifactKind
    ) -> AtlasVaultPairingArtifact? {
        artifacts[kind.rawValue]
    }
    func createArtifact(_ value: AtlasVaultPairingArtifact) throws {
        if value.kind == failArtifactCreateKind {
            throw AtlasVaultPairingTransactionError.unavailable
        }
        guard artifacts[value.kind.rawValue] == nil else {
            throw AtlasVaultPairingTransactionError.collision
        }
        artifacts[value.kind.rawValue] = value
        events.append("artifact.create:\(value.kind.rawValue)")
    }
    func deleteArtifact(
        _ kind: AtlasVaultPairingArtifactKind,
        expectedSHA256: String
    ) throws {
        guard let value = artifacts[kind.rawValue],
              try value.sha256Hex() == expectedSHA256 else {
            throw AtlasVaultPairingTransactionError.stale
        }
        artifacts[kind.rawValue] = nil
        events.append("artifact.delete:\(kind.rawValue)")
    }

    func loadRegistry() -> AtlasVaultTrustedDeviceRegistry? { registry }
    func createRegistry(_ value: AtlasVaultTrustedDeviceRegistry) throws {
        guard registry == nil else {
            throw AtlasVaultPairingTransactionError.collision
        }
        registry = value
        events.append("registry.commit")
    }
    func replaceRegistry(
        _ value: AtlasVaultTrustedDeviceRegistry,
        expectedSHA256: String
    ) throws {
        guard let registry,
              sha256(try registry.canonicalData()) == expectedSHA256 else {
            throw AtlasVaultPairingTransactionError.stale
        }
        self.registry = value
        events.append("registry.commit")
    }

    func loadReplay() -> AtlasVaultPairingReplayStore? { replay }
    func createReplay(_ value: AtlasVaultPairingReplayStore) throws {
        guard replay == nil else {
            throw AtlasVaultPairingTransactionError.collision
        }
        replay = value
        events.append("replay.commit")
    }
    func replaceReplay(
        _ value: AtlasVaultPairingReplayStore,
        expectedSHA256: String
    ) throws {
        guard let replay,
              sha256(try replay.canonicalData()) == expectedSHA256 else {
            throw AtlasVaultPairingTransactionError.stale
        }
        self.replay = value
        events.append("replay.commit")
    }

    func loadStore(_ vaultID: String) -> AtlasVaultLocalStoreEnvelope? {
        stores[vaultID]
    }
    func createStore(
        _ value: AtlasVaultLocalStoreEnvelope,
        vaultID: String
    ) throws {
        guard stores[vaultID] == nil else {
            throw AtlasVaultPairingTransactionError.collision
        }
        stores[vaultID] = value
        events.append("store.create")
    }
    func deleteStore(_ vaultID: String) {
        stores[vaultID] = nil
        events.append("store.delete")
    }
    func loadKey(_ vaultID: String) -> Data? {
        keys[vaultID].map { Data($0) }
    }
    func createKey(_ value: Data, vaultID: String) throws {
        guard keys[vaultID] == nil else {
            throw AtlasVaultPairingTransactionError.collision
        }
        keys[vaultID] = Data(value)
        events.append("key.create")
    }
    func deleteKey(_ vaultID: String) {
        keys[vaultID] = nil
        events.append("key.delete")
    }
    func selected() -> String? { selectedVault }
    func createSelection(_ vaultID: String) throws {
        guard selectedVault == nil else {
            throw AtlasVaultPairingTransactionError.collision
        }
        selectedVault = vaultID
        events.append("selection.create")
    }
    func activate(_ vaultID: String, key: Data) -> Bool {
        guard
            selectedVault == vaultID,
            stores[vaultID] != nil,
            keys[vaultID] == key
        else { return false }
        active = true
        events.append("runtime.activate")
        return true
    }
    func isActive() -> Bool { active }

    func snapshot() -> PairingCoordinatorSnapshot {
        PairingCoordinatorSnapshot(
            transaction: transaction,
            registry: registry,
            stores: stores,
            selectedVault: selectedVault,
            events: events
        )
    }

    private func sha256(_ data: Data) -> String {
        Data(SHA256.hash(data: data))
            .map { String(format: "%02x", $0) }
            .joined()
    }
}

private final class PairingIdentifierSequence: @unchecked Sendable {
    private let counter = Mutex<Int>(0)

    func next() -> String {
        counter.withLock { value in
            value += 1
            return String(
                format: "44000000-0000-4000-8000-%012x",
                value
            )
        }
    }
}

private final class PairingRandomSequence: @unchecked Sendable {
    private let counter = Mutex<UInt8>(0)

    func next(count: Int) throws -> Data {
        guard count > 0 else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        return counter.withLock { value in
            value &+= 1
            return Data((0..<count).map { offset in
                UInt8((Int(value) + offset) % 251 + 1)
            })
        }
    }
}

private final class PairingClock: @unchecked Sendable {
    private let value: Mutex<String>

    init(_ value: String) {
        self.value = Mutex(value)
    }

    func now() -> String {
        value.withLock { $0 }
    }

    func set(_ replacement: String) {
        value.withLock { $0 = replacement }
    }
}

private func pairingEnvironment(
    state: PairingCoordinatorState,
    identifiers: PairingIdentifierSequence,
    random: PairingRandomSequence,
    timestamp: @escaping @Sendable () -> String
) -> AtlasVaultTrustedPairingEnvironment {
    AtlasVaultTrustedPairingEnvironment(
        loadIdentity: { await state.loadIdentity() },
        createIdentity: { await state.loadIdentity() },
        loadTransaction: { await state.loadTransaction() },
        createTransaction: { try await state.createTransaction($0) },
        replaceTransaction: {
            try await state.replaceTransaction($0, expectedSHA256: $1)
        },
        deleteTransaction: {
            try await state.deleteTransaction(expectedSHA256: $0)
        },
        loadArtifact: { await state.loadArtifact($0) },
        createArtifact: { try await state.createArtifact($0) },
        deleteArtifact: {
            try await state.deleteArtifact($0, expectedSHA256: $1)
        },
        loadRegistry: { await state.loadRegistry() },
        createRegistry: { try await state.createRegistry($0) },
        replaceRegistry: {
            try await state.replaceRegistry($0, expectedSHA256: $1)
        },
        loadReplay: { await state.loadReplay() },
        createReplay: { try await state.createReplay($0) },
        replaceReplay: {
            try await state.replaceReplay($0, expectedSHA256: $1)
        },
        activeVault: { await state.activeVault() },
        cleanInstall: { await state.cleanInstall() },
        loadStore: { vaultID, _ in await state.loadStore(vaultID) },
        createStore: { store, vaultID, _ in
            try await state.createStore(store, vaultID: vaultID)
        },
        deleteStore: { await state.deleteStore($0) },
        loadStoredKey: { await state.loadKey($0) },
        createStoredKey: { key, vaultID in
            try await state.createKey(key, vaultID: vaultID)
        },
        deleteStoredKey: { await state.deleteKey($0) },
        selectedVault: { await state.selected() },
        createSelection: { try await state.createSelection($0) },
        activate: { await state.activate($0, key: $1) },
        validateProjection: { store, vaultID, key, active in
            let session = try AtlasVaultUnlockedSession(
                vaultID: vaultID,
                vaultKey: key
            )
            _ = try AtlasVaultRecordHydrator().hydrate(
                records: store.records,
                session: session
            )
            if !active { return true }
            return await state.isActive()
        },
        uuid: { identifiers.next() },
        timestamp: timestamp,
        randomBytes: { try random.next(count: $0) }
    )
}

private enum PairingTransactionTestError: Error {
    case invalidVector
}

private final class PairingStateFakeKeychainClient:
    AtlasKeychainClient,
    @unchecked Sendable
{
    private var values: [String: Data] = [:]
    private(set) var lastAdded: AtlasKeychainItem?

    func overwrite(service: String, account: String, data: Data) {
        values[Self.key(service: service, account: account)] = data
    }

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        let key = Self.key(service: item.service, account: item.account)
        guard values[key] == nil else { return errSecDuplicateItem }
        values[key] = item.valueData
        lastAdded = item
        return errSecSuccess
    }

    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        guard let value = values[Self.key(query)] else {
            return AtlasKeychainCopyResult(status: errSecItemNotFound, valueData: nil)
        }
        return AtlasKeychainCopyResult(status: errSecSuccess, valueData: value)
    }

    func update(
        _ query: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        let key = Self.key(query)
        guard values[key] != nil else { return errSecItemNotFound }
        values[key] = attributes.valueData
        return errSecSuccess
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        values.removeValue(forKey: Self.key(query)) == nil
            ? errSecItemNotFound
            : errSecSuccess
    }

    private static func key(_ query: AtlasKeychainQuery) -> String {
        key(service: query.service, account: query.account)
    }

    private static func key(service: String, account: String) -> String {
        "\(service)\u{0}\(account)"
    }
}
