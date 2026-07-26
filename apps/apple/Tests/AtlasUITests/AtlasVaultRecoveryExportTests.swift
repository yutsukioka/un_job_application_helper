import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRecoveryExportTests: XCTestCase {
    func testRecoverySetupTransactionSurfaceExists() throws {
        let source = try phaseSource("AtlasVaultRecoveryExport.swift")

        for required in [
            "com.atlasvault.recovery-export",
            "pending-v2",
            "afterFirstUnlockThisDeviceOnly",
            "prepareNewRecovery",
            "confirmAndPrepareExport",
            "resumeAndPrepareExport",
            "exportDidSucceed",
            "exportDidFailOrCancel",
            "resetPendingSetup",
            "saveEncryptedStoreAtomically",
            "overwrite: true",
            "recordHydrator",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "Task.detached",
            "UserDefaults",
            "LocalAuthentication",
            "CloudKit",
            "deleteVaultKey",
            "clearSelection",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testConstructionInvokesNoDependency() async throws {
        let fixture = try RecoveryExportFixture()

        _ = fixture.coordinator
        let events = await fixture.fake.eventsSnapshot()

        XCTAssertEqual(events, [])
    }

    func testPrepareKeepsPersistentStateUntouchedAndReturnsCodeOnce()
        async throws
    {
        let fixture = try RecoveryExportFixture()

        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)
        let snapshot = await fixture.fake.snapshot()
        let secondTake = await handle.take()

        XCTAssertTrue(code.hasPrefix("AVRK1-"))
        XCTAssertNil(secondTake)
        XCTAssertNil(snapshot.journal)
        XCTAssertEqual(snapshot.store.vaultMetadata, fixture.initialMetadata)
        XCTAssertFalse(snapshot.events.contains("saveJournal"))
        XCTAssertFalse(snapshot.events.contains("saveStore"))
        XCTAssertFalse(snapshot.events.contains("clearJournal"))
    }

    func testConfirmedSetupWritesJournalBeforeStoreAndClearsAfterExport()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)

        let document = try await fixture.coordinator
            .confirmAndPrepareExport(secret: code)
        let pending = await fixture.fake.snapshot()

        XCTAssertNotNil(pending.journal)
        XCTAssertNotNil(
            try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: pending.store.vaultMetadata
            ).recoveryKeyWrap
        )
        let journalIndex = try XCTUnwrap(
            pending.events.firstIndex(of: "saveJournal")
        )
        let storeIndex = try XCTUnwrap(
            pending.events.firstIndex(of: "saveStore")
        )
        XCTAssertLessThan(journalIndex, storeIndex)
        XCTAssertEqual(
            pending.events.filter { $0 == "hydrate" }.count,
            2
        )
        XCTAssertNoThrow(
            try AtlasVaultEncryptedExportEnvelope.decodeStrict(
                document.encryptedData
            )
        )

        try await fixture.coordinator.exportDidSucceed()
        let complete = await fixture.fake.snapshot()
        XCTAssertNil(complete.journal)
        XCTAssertEqual(complete.events.last, "clearJournal")
    }

    func testWrongConfirmationCausesNoPersistentMutation() async throws {
        let fixture = try RecoveryExportFixture()
        _ = try await fixture.coordinator.prepareNewRecovery()
        let wrong = try AtlasVaultRecoveryKeyCodec.canonicalText(
            for: Data(repeating: 0xee, count: 32)
        )

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: wrong
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .invalidConfirmation
            )
        }
        let snapshot = await fixture.fake.snapshot()
        XCTAssertNil(snapshot.journal)
        XCTAssertEqual(snapshot.store.vaultMetadata, fixture.initialMetadata)
        XCTAssertFalse(snapshot.events.contains("saveJournal"))
        XCTAssertFalse(snapshot.events.contains("saveStore"))
    }

    func testDurabilityUnconfirmedRetainsJournalAndRequiresResume()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        await fixture.fake.setCommitState(
            .committedDurabilityUnconfirmed
        )
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .durabilityVerificationRequired
            )
        }
        let durabilitySnapshot = await fixture.fake.snapshot()
        XCTAssertNotNil(durabilitySnapshot.journal)

        await fixture.fake.setCommitState(.committed)
        let document = try await fixture.coordinator
            .resumeAndPrepareExport(secret: code)
        let resumedSnapshot = await fixture.fake.snapshot()
        XCTAssertFalse(document.encryptedData.isEmpty)
        XCTAssertNotNil(resumedSnapshot.journal)
    }

    func testJournalSaveWipesPreparedSecretBeforeDurabilityFailure()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        await fixture.fake.setCommitState(
            .committedDurabilityUnconfirmed
        )
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .durabilityVerificationRequired
            )
        }
        await fixture.fake.setCommitState(.committed)

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .recoveryRequired
            )
        }
        let document = try await fixture.coordinator
            .resumeAndPrepareExport(secret: code)
        let snapshot = await fixture.fake.snapshot()

        XCTAssertFalse(document.encryptedData.isEmpty)
        XCTAssertNotNil(snapshot.journal)
    }

    func testRelaunchResumeRequiresSavedKeyAndDoesNotDuplicateWrap()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)
        _ = try await fixture.coordinator.confirmAndPrepareExport(
            secret: code
        )
        await fixture.coordinator.stop()
        let relaunched = AtlasVaultRecoveryExportCoordinator(
            environment: fixture.environment
        )
        let wrong = try AtlasVaultRecoveryKeyCodec.canonicalText(
            for: Data(repeating: 0x44, count: 32)
        )

        await XCTAssertThrowsErrorAsync(
            try await relaunched.resumeAndPrepareExport(secret: wrong)
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .invalidConfirmation
            )
        }
        _ = try await relaunched.resumeAndPrepareExport(secret: code)
        let relaunchedSnapshot = await fixture.fake.snapshot()
        let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
            localStoreMetadata: relaunchedSnapshot.store.vaultMetadata
        )
        XCTAssertEqual(
            metadata.keyWraps.compactMap(\.recoveryKeyEnvelope).count,
            1
        )
    }

    func testWrongResumeKeyCannotCommitJournalWrapBeforeVerification()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        await fixture.fake.setDiscardStoreWrites(true)
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .recoveryRequired
            )
        }
        let interrupted = await fixture.fake.snapshot()
        XCTAssertNotNil(interrupted.journal)
        XCTAssertNil(
            try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: interrupted.store.vaultMetadata
            ).recoveryKeyWrap
        )
        let writesBeforeResume = interrupted.events.filter {
            $0 == "saveStore"
        }.count
        await fixture.fake.setDiscardStoreWrites(false)
        let relaunched = AtlasVaultRecoveryExportCoordinator(
            environment: fixture.environment
        )
        let wrong = try AtlasVaultRecoveryKeyCodec.canonicalText(
            for: Data(repeating: 0x44, count: 32)
        )

        await XCTAssertThrowsErrorAsync(
            try await relaunched.resumeAndPrepareExport(secret: wrong)
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .invalidConfirmation
            )
        }
        let rejected = await fixture.fake.snapshot()
        XCTAssertEqual(rejected.store, interrupted.store)
        XCTAssertEqual(rejected.journal, interrupted.journal)
        XCTAssertEqual(
            rejected.events.filter { $0 == "saveStore" }.count,
            writesBeforeResume
        )
    }

    func testExplicitPendingResetPreservesVaultKeyStoreAndRecords()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)
        _ = try await fixture.coordinator.confirmAndPrepareExport(
            secret: code
        )
        let before = await fixture.fake.snapshot()

        try await fixture.coordinator.resetPendingSetup()
        let after = await fixture.fake.snapshot()
        let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
            localStoreMetadata: after.store.vaultMetadata
        )

        XCTAssertNil(after.journal)
        XCTAssertNil(metadata.recoveryKeyWrap)
        XCTAssertEqual(after.store.records, before.store.records)
        XCTAssertEqual(after.vaultKey, before.vaultKey)
        XCTAssertEqual(after.selectedVaultID, before.selectedVaultID)
        XCTAssertEqual(after.events.last, "clearJournal")
    }

    func testPendingResetRequiresExactStoreReadBackBeforeJournalClear()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)
        _ = try await fixture.coordinator.confirmAndPrepareExport(
            secret: code
        )
        await fixture.fake.setDiscardStoreWrites(true)

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.resetPendingSetup()
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .recoveryRequired
            )
        }

        let snapshot = await fixture.fake.snapshot()
        XCTAssertNotNil(snapshot.journal)
        XCTAssertNotNil(
            try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: snapshot.store.vaultMetadata
            ).recoveryKeyWrap
        )
        XCTAssertNotEqual(snapshot.events.last, "clearJournal")
    }

    func testCorruptRecordVerificationBlocksExportAndRetainsJournal()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        await fixture.fake.setHydrationFailure(true)
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        )

        let snapshot = await fixture.fake.snapshot()
        XCTAssertNotNil(snapshot.journal)
    }

    func testMetadataCommitRequiresExactStoreReadBack() async throws {
        let fixture = try RecoveryExportFixture()
        await fixture.fake.setMutateStoredTimestamp(true)
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .recoveryRequired
            )
        }

        let snapshot = await fixture.fake.snapshot()
        XCTAssertNotNil(snapshot.journal)
        XCTAssertFalse(snapshot.events.contains("hydrate"))
        XCTAssertNotEqual(snapshot.events.last, "clearJournal")
    }

    func testAuthorizationIsRecheckedBeforePersistentSideEffects()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)
        await fixture.fake.setAuthorized(false)

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .unauthorized
            )
        }
        let snapshot = await fixture.fake.snapshot()
        XCTAssertNil(snapshot.journal)
        XCTAssertFalse(snapshot.events.contains("saveJournal"))
        XCTAssertFalse(snapshot.events.contains("saveStore"))
    }

    func testAuthorizationLossDuringFirstHydrationBlocksExport()
        async throws
    {
        let entered = RecoveryExportGate(open: false)
        let release = RecoveryExportGate(open: false)
        let fixture = try RecoveryExportFixture(
            hydrationPauseAtCall: 1,
            hydrationEntered: entered,
            hydrationRelease: release
        )
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)
        let operation = Task {
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        }
        await entered.wait()
        await fixture.fake.setAuthorized(false)
        await release.open()

        await XCTAssertThrowsErrorAsync(
            try await operation.value
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .unauthorized
            )
        }
        let snapshot = await fixture.fake.snapshot()

        XCTAssertNotNil(snapshot.journal)
        XCTAssertEqual(
            snapshot.events.filter { $0 == "hydrate" }.count,
            1
        )
    }

    func testTerminalStopDuringSecondHydrationCannotReturnDocument()
        async throws
    {
        let entered = RecoveryExportGate(open: false)
        let release = RecoveryExportGate(open: false)
        let cancellationObserved = RecoveryExportGate(open: false)
        let fixture = try RecoveryExportFixture(
            hydrationPauseAtCall: 2,
            hydrationEntered: entered,
            hydrationRelease: release,
            hydrationCancellationObserved: cancellationObserved
        )
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)
        let operation = Task {
            try await fixture.coordinator.confirmAndPrepareExport(
                secret: code
            )
        }
        await entered.wait()
        let stop = Task {
            await fixture.coordinator.stop()
        }
        await cancellationObserved.wait()
        await release.open()
        await stop.value

        await XCTAssertThrowsErrorAsync(
            try await operation.value
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryExportFailure,
                .cancelled
            )
        }
        let snapshot = await fixture.fake.snapshot()

        XCTAssertNotNil(snapshot.journal)
        XCTAssertEqual(
            snapshot.events.filter { $0 == "hydrate" }.count,
            2
        )
        XCTAssertFalse(snapshot.events.contains("clearJournal"))
    }

    func testConcurrentPrepareCallersShareCoordinatorOwnedOperation()
        async throws
    {
        let entered = RecoveryExportGate(open: false)
        let release = RecoveryExportGate(open: false)
        let fixture = try RecoveryExportFixture(
            selectionEntered: entered,
            selectionRelease: release
        )
        let first = Task {
            try await fixture.coordinator.prepareNewRecovery()
        }
        await entered.wait()
        let second = Task {
            try await fixture.coordinator.prepareNewRecovery()
        }
        first.cancel()
        await release.open()

        let firstHandle = try await first.value
        let secondHandle = try await second.value
        let code = await secondHandle.take()

        XCTAssertTrue(firstHandle === secondHandle)
        XCTAssertNotNil(code)
    }

    func testJournalSchemaContainsEncryptedWrapButNoRawSecret()
        async throws
    {
        let fixture = try RecoveryExportFixture()
        let handle = try await fixture.coordinator.prepareNewRecovery()
        let takenCode = await handle.take()
        let code = try XCTUnwrap(takenCode)
        _ = try await fixture.coordinator.confirmAndPrepareExport(
            secret: code
        )
        let snapshot = await fixture.fake.snapshot()
        let journal = try XCTUnwrap(snapshot.journal)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(journal)
        let object = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: data)
                as? [String: Any]
        )
        let text = try XCTUnwrap(String(data: data, encoding: .utf8))

        XCTAssertEqual(
            Set(object.keys),
            [
                "format",
                "version",
                "vault_id",
                "store_id",
                "wrap_id",
                "wrap",
                "export_id",
                "created_at",
            ]
        )
        XCTAssertFalse(text.contains(code))
        XCTAssertFalse(
            text.contains(fixture.recoveryKey.base64EncodedString())
        )
        XCTAssertFalse(
            text.contains(fixture.vaultKey.base64EncodedString())
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

private struct RecoveryExportFixture {
    let vaultKey = Data((0x20..<0x40).map(UInt8.init))
    let recoveryKey = Data((0x00..<0x20).map(UInt8.init))
    let initialMetadata: [String: AtlasJSONValue]
    let fake: RecoveryExportEnvironmentFake
    let environment: AtlasVaultRecoveryExportEnvironment
    let coordinator: AtlasVaultRecoveryExportCoordinator

    init(
        selectionEntered: RecoveryExportGate? = nil,
        selectionRelease: RecoveryExportGate? = nil,
        hydrationPauseAtCall: Int? = nil,
        hydrationEntered: RecoveryExportGate? = nil,
        hydrationRelease: RecoveryExportGate? = nil,
        hydrationCancellationObserved: RecoveryExportGate? = nil
    ) throws {
        let vaultID = "11111111-2222-3333-4444-555555555555"
        let storeID = "22222222-3333-4444-8555-666666666666"
        let crypto = try AtlasVaultKeyWrapCryptoSuite()
        let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
            vaultID: vaultID,
            crypto: crypto,
            keyWraps: []
        )
        initialMetadata = try metadata.localStoreMetadata()
        let store = AtlasVaultLocalStoreEnvelope(
            storeID: storeID,
            createdAt: "2026-01-01T00:00:00Z",
            updatedAt: "2026-01-01T00:00:00Z",
            vaultMetadata: initialMetadata,
            records: []
        )
        let fake = RecoveryExportEnvironmentFake(
            vaultID: vaultID,
            vaultKey: vaultKey,
            store: store,
            selectionEntered: selectionEntered,
            selectionRelease: selectionRelease,
            hydrationPauseAtCall: hydrationPauseAtCall,
            hydrationEntered: hydrationEntered,
            hydrationRelease: hydrationRelease,
            hydrationCancellationObserved:
                hydrationCancellationObserved
        )
        self.fake = fake
        let deterministicRecoveryKey = Data(
            (0x00..<0x20).map(UInt8.init)
        )
        environment = AtlasVaultRecoveryExportEnvironment(
            authorize: {
                await fake.authorize()
            },
            selectVault: {
                try await fake.selectVault()
            },
            loadVaultKey: { vaultID in
                try await fake.loadVaultKey(vaultID)
            },
            loadStore: { vaultID, vaultKey in
                try await fake.loadStore(
                    vaultID: vaultID,
                    vaultKey: vaultKey
                )
            },
            saveStore: { store, vaultID, vaultKey in
                try await fake.saveStore(
                    store,
                    vaultID: vaultID,
                    vaultKey: vaultKey
                )
            },
            hydrate: { records, vaultID, vaultKey in
                try await fake.hydrate(
                    records: records,
                    vaultID: vaultID,
                    vaultKey: vaultKey
                )
            },
            loadJournal: {
                await fake.loadJournal()
            },
            saveJournal: { journal in
                await fake.saveJournal(journal)
            },
            clearJournal: {
                await fake.clearJournal()
            },
            generateRecoveryKey: {
                deterministicRecoveryKey
            },
            generateSalt: {
                Data((0x40..<0x60).map(UInt8.init))
            },
            generateNonce: {
                Data((0x00..<0x0c).map(UInt8.init))
            },
            generateID: {
                "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
            },
            timestamp: {
                "2026-01-02T03:04:05Z"
            }
        )
        coordinator = AtlasVaultRecoveryExportCoordinator(
            environment: environment
        )
    }
}

private actor RecoveryExportEnvironmentFake {
    struct Snapshot: Sendable {
        let authorized: Bool
        let selectedVaultID: String
        let vaultKey: Data
        let store: AtlasVaultLocalStoreEnvelope
        let journal: AtlasVaultRecoveryExportJournal?
        let events: [String]
    }

    private var isAuthorized = true
    private let vaultID: String
    private let vaultKey: Data
    private var store: AtlasVaultLocalStoreEnvelope
    private var journal: AtlasVaultRecoveryExportJournal?
    private var events: [String] = []
    private var commitState: AtlasVaultAtomicCommitState = .committed
    private var hydrationFailure = false
    private var discardsStoreWrites = false
    private var mutatesStoredTimestamp = false
    private let selectionEntered: RecoveryExportGate?
    private let selectionRelease: RecoveryExportGate?
    private let hydrationPauseAtCall: Int?
    private let hydrationEntered: RecoveryExportGate?
    private let hydrationRelease: RecoveryExportGate?
    private let hydrationCancellationObserved: RecoveryExportGate?
    private var hydrationCallCount = 0

    init(
        vaultID: String,
        vaultKey: Data,
        store: AtlasVaultLocalStoreEnvelope,
        selectionEntered: RecoveryExportGate?,
        selectionRelease: RecoveryExportGate?,
        hydrationPauseAtCall: Int?,
        hydrationEntered: RecoveryExportGate?,
        hydrationRelease: RecoveryExportGate?,
        hydrationCancellationObserved: RecoveryExportGate?
    ) {
        self.vaultID = vaultID
        self.vaultKey = vaultKey
        self.store = store
        self.selectionEntered = selectionEntered
        self.selectionRelease = selectionRelease
        self.hydrationPauseAtCall = hydrationPauseAtCall
        self.hydrationEntered = hydrationEntered
        self.hydrationRelease = hydrationRelease
        self.hydrationCancellationObserved =
            hydrationCancellationObserved
    }

    func authorize() -> Bool {
        events.append("authorize")
        return isAuthorized
    }

    func selectVault() async throws -> AtlasSelectedVaultID {
        events.append("selectVault")
        await selectionEntered?.open()
        await selectionRelease?.wait()
        return try AtlasSelectedVaultID(validating: vaultID)
    }

    func loadVaultKey(_ candidate: String) throws -> Data? {
        events.append("loadVaultKey")
        guard candidate == vaultID else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        return vaultKey
    }

    func loadStore(
        vaultID candidate: String,
        vaultKey candidateKey: Data
    ) throws -> AtlasVaultLocalStoreEnvelope? {
        events.append("loadStore")
        guard
            candidate == vaultID,
            candidateKey == vaultKey
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        return store
    }

    func saveStore(
        _ newStore: AtlasVaultLocalStoreEnvelope,
        vaultID candidate: String,
        vaultKey candidateKey: Data
    ) throws -> AtlasVaultAtomicWriteResult {
        events.append("saveStore")
        guard
            candidate == vaultID,
            candidateKey == vaultKey
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        if !discardsStoreWrites {
            if mutatesStoredTimestamp {
                store = AtlasVaultLocalStoreEnvelope(
                    format: newStore.format,
                    version: newStore.version,
                    storeID: newStore.storeID,
                    createdAt: newStore.createdAt,
                    updatedAt: "2026-01-03T00:00:00Z",
                    vaultMetadata: newStore.vaultMetadata,
                    records: newStore.records
                )
            } else {
                store = newStore
            }
        }
        return AtlasVaultAtomicWriteResult(commitState: commitState)
    }

    func hydrate(
        records _: [AtlasVaultEncryptedRecordEnvelope],
        vaultID candidate: String,
        vaultKey candidateKey: Data
    ) async throws {
        events.append("hydrate")
        hydrationCallCount += 1
        if hydrationCallCount == hydrationPauseAtCall {
            await hydrationEntered?.open()
            if let hydrationRelease {
                await withTaskCancellationHandler {
                    await hydrationRelease.wait()
                } onCancel: {
                    guard let hydrationCancellationObserved else {
                        return
                    }
                    Task {
                        await hydrationCancellationObserved.open()
                    }
                }
            }
        }
        guard
            !hydrationFailure,
            candidate == vaultID,
            candidateKey == vaultKey
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
    }

    func loadJournal() -> AtlasVaultRecoveryExportJournal? {
        events.append("loadJournal")
        return journal
    }

    func saveJournal(_ value: AtlasVaultRecoveryExportJournal) {
        events.append("saveJournal")
        journal = value
    }

    func clearJournal() {
        events.append("clearJournal")
        journal = nil
    }

    func setAuthorized(_ value: Bool) {
        isAuthorized = value
    }

    func setCommitState(_ value: AtlasVaultAtomicCommitState) {
        commitState = value
    }

    func setHydrationFailure(_ value: Bool) {
        hydrationFailure = value
    }

    func setDiscardStoreWrites(_ value: Bool) {
        discardsStoreWrites = value
    }

    func setMutateStoredTimestamp(_ value: Bool) {
        mutatesStoredTimestamp = value
    }

    func snapshot() -> Snapshot {
        Snapshot(
            authorized: isAuthorized,
            selectedVaultID: vaultID,
            vaultKey: vaultKey,
            store: store,
            journal: journal,
            events: events
        )
    }

    func eventsSnapshot() -> [String] {
        events
    }
}

private actor RecoveryExportGate {
    private var isOpen: Bool
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(open: Bool) {
        isOpen = open
    }

    func wait() async {
        guard !isOpen else {
            return
        }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func open() {
        guard !isOpen else {
            return
        }
        isOpen = true
        let current = waiters
        waiters.removeAll()
        current.forEach { $0.resume() }
    }
}

private func XCTAssertThrowsErrorAsync<T>(
    _ expression: @autoclosure () async throws -> T,
    _ handler: (Error) -> Void = { _ in }
) async {
    do {
        _ = try await expression()
        XCTFail("Expected asynchronous expression to throw")
    } catch {
        handler(error)
    }
}
