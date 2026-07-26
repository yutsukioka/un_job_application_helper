import CryptoKit
import Foundation
import Security
import Synchronization
import XCTest
@testable import AtlasUI

final class AtlasVaultRecoveryImportTests: XCTestCase {
    func testJournalRoundTripUsesExactSecretFreeSchema() throws {
        let journal = try makeJournal()
        let data = try journal.encodedData()
        let decoded = try AtlasVaultRecoveryImportJournal.decode(data)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )

        XCTAssertEqual(decoded, journal)
        XCTAssertEqual(
            Set(object.keys),
            [
                "format",
                "version",
                "import_id",
                "export_id",
                "vault_id",
                "store_id",
                "created_at",
                "export_sha256",
                "local_store_sha256",
                "vault_key_sha256",
            ]
        )
        for forbidden in [
            "AVRK1-",
            "recovery_key",
            "file_url",
            "store_url",
            "records",
            "ciphertext",
        ] {
            XCTAssertFalse(
                String(decoding: data, as: UTF8.self).contains(forbidden),
                forbidden
            )
        }
        XCTAssertNil(object["vault_key"])
        XCTAssertEqual(
            String(describing: journal),
            "AtlasVaultRecoveryImportJournal(<redacted>)"
        )
    }

    func testJournalStrictlyRejectsBooleanVersionAndUnknownKeys()
        throws
    {
        let valid = try makeJournal().encodedData()
        var object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: valid) as? [String: Any]
        )
        object["version"] = true
        let booleanVersion = try JSONSerialization.data(
            withJSONObject: object
        )
        XCTAssertThrowsError(
            try AtlasVaultRecoveryImportJournal.decode(booleanVersion)
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .recoveryRequired
            )
        }

        object["version"] = 1
        object["extra"] = "TEST_ONLY_PRIVATE_SENTINEL"
        let unknownKey = try JSONSerialization.data(withJSONObject: object)
        XCTAssertThrowsError(
            try AtlasVaultRecoveryImportJournal.decode(unknownKey)
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .recoveryRequired
            )
            XCTAssertFalse(
                String(describing: error).contains(
                    "TEST_ONLY_PRIVATE_SENTINEL"
                )
            )
        }

        object.removeValue(forKey: "extra")
        let integerJSON = try XCTUnwrap(
            String(data: valid, encoding: .utf8)
        )
        let floatingVersion = Data(
            integerJSON.replacingOccurrences(
                of: "\"version\":1",
                with: "\"version\":1.0"
            ).utf8
        )
        XCTAssertThrowsError(
            try AtlasVaultRecoveryImportJournal.decode(floatingVersion)
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .recoveryRequired
            )
        }
    }

    func testJournalRequiresIndependentOpaqueIdentifiers() throws {
        let journal = try makeJournal()

        XCTAssertThrowsError(
            try AtlasVaultRecoveryImportJournal(
                importID: journal.storeID,
                exportID: journal.exportID,
                vaultID: journal.vaultID,
                storeID: journal.storeID,
                createdAt: journal.createdAt,
                exportSHA256: journal.exportSHA256,
                localStoreSHA256: journal.localStoreSHA256,
                vaultKeySHA256: journal.vaultKeySHA256
            )
        )
        XCTAssertThrowsError(
            try AtlasVaultRecoveryImportJournal(
                importID: journal.importID,
                exportID: journal.storeID,
                vaultID: journal.vaultID,
                storeID: journal.storeID,
                createdAt: journal.createdAt,
                exportSHA256: journal.exportSHA256,
                localStoreSHA256: journal.localStoreSHA256,
                vaultKeySHA256: journal.vaultKeySHA256
            )
        )
    }

    func testFileReaderAcceptsOneBoundedRegularFileAndRedactsFailures()
        throws
    {
        let manager = FileManager.default
        let directory = try AtlasVaultTestFileSystemSupport
            .canonicalTemporaryRoot()
            .appendingPathComponent(
                "atlasvault-import-reader-\(UUID().uuidString)",
                isDirectory: true
            )
        try manager.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        defer {
            try? manager.removeItem(at: directory)
        }
        let reader = AtlasVaultRecoveryImportFileReader()
        let payload = Data("TEST_ONLY_ENCRYPTED_BYTES".utf8)
        let regular = directory.appendingPathComponent(
            "backup.atlasvault"
        )
        try payload.write(to: regular)
        XCTAssertEqual(try reader.read(from: regular), payload)

        let empty = directory.appendingPathComponent("empty.atlasvault")
        XCTAssertTrue(manager.createFile(atPath: empty.path, contents: nil))
        assertInvalidImportFile(empty, reader: reader)
        assertInvalidImportFile(directory, reader: reader)
        assertInvalidImportFile(
            URL(string: "https://example.invalid/backup.atlasvault")!,
            reader: reader
        )

        let symbolic = directory.appendingPathComponent(
            "linked.atlasvault"
        )
        try manager.createSymbolicLink(
            at: symbolic,
            withDestinationURL: regular
        )
        assertInvalidImportFile(symbolic, reader: reader)

        let oversized = directory.appendingPathComponent(
            "oversized.atlasvault"
        )
        XCTAssertTrue(
            manager.createFile(atPath: oversized.path, contents: Data([0]))
        )
        let handle = try FileHandle(forWritingTo: oversized)
        try handle.truncate(
            atOffset: UInt64(
                AtlasVaultRecoveryImportFileReader.maximumByteCount + 1
            )
        )
        try handle.close()
        assertInvalidImportFile(oversized, reader: reader)
    }

    func testImportJournalUsesCanonicalExportDigestForNoncanonicalInput()
        async throws
    {
        let vector = try RecoveryImportVector.load()
        let object = try JSONSerialization.jsonObject(
            with: vector.canonicalData
        )
        let noncanonical = try JSONSerialization.data(
            withJSONObject: object,
            options: [.prettyPrinted]
        )
        XCTAssertNotEqual(noncanonical, vector.canonicalData)
        let fixture = try RecoveryImportFixture(fileData: noncanonical)
        await fixture.storage.setCommitState(
            .committedDurabilityUnconfirmed
        )
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        )

        let snapshot = await fixture.storage.snapshot()
        let journal = try XCTUnwrap(snapshot.journal)
        XCTAssertEqual(
            journal.exportSHA256,
            SHA256.hash(data: vector.canonicalData).map {
                String(format: "%02x", $0)
            }.joined()
        )
        XCTAssertNotEqual(
            journal.exportSHA256,
            SHA256.hash(data: noncanonical).map {
                String(format: "%02x", $0)
            }.joined()
        )
    }

    func testJournalDuplicateUpdateIsBoundToSameTransaction()
        throws
    {
        let client = RecoveryImportJournalKeychainClient()
        let store = AtlasKeychainVaultRecoveryImportJournalStore(
            client: client
        )
        let journal = try makeJournal()

        try store.saveJournal(journal)
        try store.saveJournal(journal)
        XCTAssertEqual(try store.loadJournal(), journal)
        XCTAssertEqual(client.updateCount(), 1)
        XCTAssertEqual(
            client.storedItem()?.accessibility,
            .afterFirstUnlockThisDeviceOnly
        )
        XCTAssertEqual(
            client.storedItem()?.service,
            AtlasKeychainVaultRecoveryImportJournalStore<
                RecoveryImportJournalKeychainClient
            >.service
        )
        XCTAssertEqual(
            client.storedItem()?.account,
            AtlasKeychainVaultRecoveryImportJournalStore<
                RecoveryImportJournalKeychainClient
            >.account
        )

        let conflicting = try AtlasVaultRecoveryImportJournal(
            importID: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            exportID: journal.exportID,
            vaultID: journal.vaultID,
            storeID: journal.storeID,
            createdAt: journal.createdAt,
            exportSHA256: journal.exportSHA256,
            localStoreSHA256: journal.localStoreSHA256,
            vaultKeySHA256: journal.vaultKeySHA256
        )
        XCTAssertThrowsError(try store.saveJournal(conflicting)) {
            error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .recoveryRequired
            )
        }
        XCTAssertEqual(try store.loadJournal(), journal)
        XCTAssertEqual(client.updateCount(), 1)
    }

    func testCoordinatorConstructionInvokesNoDependency() async throws {
        let fixture = try RecoveryImportFixture()

        _ = fixture.coordinator

        let events = await fixture.storage.events()
        XCTAssertEqual(events, [])
    }

    func testCombinedSelectionGateHidesPendingTransactionsAndFailures()
        async throws
    {
        let selected = try AtlasSelectedVaultID(
            validating: "00000000-0000-4000-8000-000000000262"
        )
        let selector = RecoveryImportSelector(
            selection: .selected(selected)
        )
        let ready = AtlasPendingVaultTransactionSelectionGate(
            selector: selector,
            hasPendingCreation: { false },
            hasPendingImport: { false }
        )
        let readySelection = try await ready.selectVaultID()
        XCTAssertEqual(readySelection, .selected(selected))

        let pendingCreation = AtlasPendingVaultTransactionSelectionGate(
            selector: selector,
            hasPendingCreation: { true },
            hasPendingImport: { false }
        )
        let creationSelection = try await pendingCreation.selectVaultID()
        XCTAssertEqual(creationSelection, .none)

        let pendingImport = AtlasPendingVaultTransactionSelectionGate(
            selector: selector,
            hasPendingCreation: { false },
            hasPendingImport: { true }
        )
        let importSelection = try await pendingImport.selectVaultID()
        XCTAssertEqual(importSelection, .none)

        let unreadableJournal =
            AtlasPendingVaultTransactionSelectionGate(
                selector: selector,
                hasPendingCreation: {
                    throw AtlasVaultRecoveryImportFailure.unavailable
                },
                hasPendingImport: { false }
            )
        let unreadableSelection = try await unreadableJournal.selectVaultID()
        XCTAssertEqual(unreadableSelection, .none)
    }

    func testSelectionGatePublishesPendingImportWithoutASelection()
        async throws
    {
        let recorder = RecoveryImportPendingStateRecorder()
        let gate = AtlasPendingVaultTransactionSelectionGate(
            selector: RecoveryImportSelector(selection: .none),
            hasPendingCreation: { false },
            hasPendingImport: { true },
            pendingImportDidChange: { pending in
                await recorder.record(pending)
            }
        )

        let selection = try await gate.selectVaultID()

        XCTAssertEqual(selection, .none)
        let values = await recorder.values()
        XCTAssertEqual(values, [true])
    }

    func testSelectionGateSeparatesCreationAndImportReadFailures()
        async throws
    {
        let creationFailureRecorder =
            RecoveryImportPendingStateRecorder()
        let creationFailureGate =
            AtlasPendingVaultTransactionSelectionGate(
                selector: RecoveryImportSelector(selection: .none),
                hasPendingCreation: {
                    throw AtlasVaultRecoveryImportFailure.unavailable
                },
                hasPendingImport: { false },
                pendingImportDidChange: { pending in
                    await creationFailureRecorder.record(pending)
                }
            )

        let creationFailureSelection =
            try await creationFailureGate.selectVaultID()

        XCTAssertEqual(creationFailureSelection, .none)
        let creationFailureValues =
            await creationFailureRecorder.values()
        XCTAssertEqual(creationFailureValues, [false])

        let importFailureRecorder = RecoveryImportPendingStateRecorder()
        let importFailureGate =
            AtlasPendingVaultTransactionSelectionGate(
                selector: RecoveryImportSelector(selection: .none),
                hasPendingCreation: { false },
                hasPendingImport: {
                    throw AtlasVaultRecoveryImportFailure.unavailable
                },
                pendingImportDidChange: { pending in
                    await importFailureRecorder.record(pending)
                }
            )

        let importFailureSelection =
            try await importFailureGate.selectVaultID()

        XCTAssertEqual(importFailureSelection, .none)
        let importFailureValues = await importFailureRecorder.values()
        XCTAssertEqual(importFailureValues, [true])
    }

    func testCreationGateBlocksPendingImportBeforeCreator() async throws {
        let creator = RecoveryImportCreationFake()
        let blocked = AtlasPendingRecoveryImportCreationGate(
            creator: creator,
            hasPendingImport: { true }
        )

        await XCTAssertThrowsErrorAsync(
            try await blocked.createOrResume()
        ) { error in
            XCTAssertEqual(
                error as? AtlasLocalVaultCreationFailure,
                .recoveryRequired
            )
        }
        let blockedCalls = await creator.createCallCount()
        XCTAssertEqual(blockedCalls, 0)

        let allowed = AtlasPendingRecoveryImportCreationGate(
            creator: creator,
            hasPendingImport: { false }
        )
        let outcome = try await allowed.createOrResume()
        XCTAssertEqual(outcome, .created)
        let allowedCalls = await creator.createCallCount()
        XCTAssertEqual(allowedCalls, 1)
    }

    func testPendingTransactionAuthoritySerializesCreationAndImport()
        async throws
    {
        let authority = AtlasVaultPendingTransactionAuthority()
        let importGate = RecoveryImportTestGate()
        let recorder = RecoveryImportTransactionRecorder()

        let importTask = Task {
            try await authority.perform {
                await recorder.enter("import")
                await importGate.wait()
                return "import"
            }
        }
        await importGate.waitUntilEntered()

        let creationTask = Task {
            await recorder.creationAttempted()
            return try await authority.perform {
                await recorder.enter("creation")
                return "creation"
            }
        }
        await recorder.waitUntilCreationAttempted()
        let creationQueued = await waitUntilPendingTransactionWaiter(
            authority
        )
        XCTAssertTrue(creationQueued)
        let entriesBeforeRelease = await recorder.entries()
        XCTAssertEqual(entriesBeforeRelease, ["import"])

        await recorder.enter("release")
        await importGate.release()
        let importResult = try await importTask.value
        let creationResult = try await creationTask.value
        XCTAssertEqual(importResult, "import")
        XCTAssertEqual(creationResult, "creation")
        let entriesAfterRelease = await recorder.entries()
        XCTAssertEqual(
            entriesAfterRelease,
            ["import", "release", "creation"]
        )
    }

    func testPendingTransactionAuthorityRemovesCancelledWaiter()
        async throws
    {
        let authority = AtlasVaultPendingTransactionAuthority()
        let holderGate = RecoveryImportTestGate()
        let recorder = RecoveryImportTransactionRecorder()

        let holderTask = Task {
            try await authority.perform {
                await holderGate.wait()
                return "holder"
            }
        }
        await holderGate.waitUntilEntered()

        let waiterTask = Task {
            await recorder.creationAttempted()
            return try await authority.perform {
                await recorder.enter("cancelled-waiter")
                return "waiter"
            }
        }
        await recorder.waitUntilCreationAttempted()
        let waiterQueued = await waitUntilPendingTransactionWaiter(authority)
        XCTAssertTrue(waiterQueued)
        waiterTask.cancel()

        do {
            _ = try await waiterTask.value
            XCTFail("Expected cancellation")
        } catch is CancellationError {
            // Expected: cancellation removes the queued waiter immediately.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
        let entries = await recorder.entries()
        XCTAssertEqual(entries, [])

        await holderGate.release()
        let holderResult = try await holderTask.value
        XCTAssertEqual(holderResult, "holder")
        let nextResult = try await authority.perform {
            "next"
        }
        XCTAssertEqual(nextResult, "next")
    }

    func testExistingVaultAndCreationTransactionBlockFileRead()
        async throws
    {
        let selectedFixture = try RecoveryImportFixture()
        try await selectedFixture.storage.createSelection(
            AtlasSelectedVaultID(
                validating: selectedFixture.vector.envelope
                    .vaultMetadata.vaultID
            )
        )
        await XCTAssertThrowsErrorAsync(
            try await selectedFixture.coordinator.prepareImport(
                from: Self.testOnlyFileURL
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .existingVault
            )
        }
        let selectedEvents = await selectedFixture.storage.events()
        XCTAssertFalse(selectedEvents.contains("readFile"))

        let creationFixture = try RecoveryImportFixture(
            hasPendingCreation: true
        )
        await XCTAssertThrowsErrorAsync(
            try await creationFixture.coordinator.prepareImport(
                from: Self.testOnlyFileURL
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .restoreUnavailable
            )
        }
        let creationEvents = await creationFixture.storage.events()
        XCTAssertFalse(creationEvents.contains("readFile"))
    }

    func testRecoveryWorkflowRejectsMissingAndDuplicateRecoveryWraps()
        async throws
    {
        let vector = try RecoveryImportVector.load()
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(
                with: vector.canonicalData
            ) as? [String: Any]
        )
        let metadata = try XCTUnwrap(
            object["vault_metadata"] as? [String: Any]
        )
        let wraps = try XCTUnwrap(
            metadata["key_wraps"] as? [[String: Any]]
        )
        XCTAssertEqual(wraps.count, 1)

        var missingObject = object
        var missingMetadata = metadata
        missingMetadata["key_wraps"] = []
        missingObject["vault_metadata"] = missingMetadata
        let missing = try JSONSerialization.data(
            withJSONObject: missingObject
        )
        let missingFixture = try RecoveryImportFixture(
            fileData: missing
        )
        await XCTAssertThrowsErrorAsync(
            try await missingFixture.coordinator.prepareImport(
                from: Self.testOnlyFileURL
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .invalidExport
            )
        }

        var duplicateObject = object
        var duplicateMetadata = metadata
        duplicateMetadata["key_wraps"] = [wraps[0], wraps[0]]
        duplicateObject["vault_metadata"] = duplicateMetadata
        let duplicate = try JSONSerialization.data(
            withJSONObject: duplicateObject
        )
        let duplicateFixture = try RecoveryImportFixture(
            fileData: duplicate
        )
        await XCTAssertThrowsErrorAsync(
            try await duplicateFixture.coordinator.prepareImport(
                from: Self.testOnlyFileURL
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .invalidExport
            )
        }
        let missingState = await missingFixture.storage.snapshot()
        let duplicateState = await duplicateFixture.storage.snapshot()
        XCTAssertNil(missingState.journal)
        XCTAssertNil(duplicateState.journal)
    }

    func testPrepareRejectsDuplicateRecordIDsBeforePersistence()
        async throws
    {
        let vector = try RecoveryImportVector.load()
        let record = AtlasVaultEncryptedRecordEnvelope(
            id: "duplicate-record",
            schemaVersion: 1,
            revision: "revision-1",
            parentRevision: nil,
            deleted: false,
            keyID: "primary-recovery-v2",
            nonce: Data(
                repeating: 1,
                count: AtlasVaultRecordCrypto.nonceByteCount
            ).base64EncodedString(),
            ciphertext: Data(
                repeating: 2,
                count: AtlasVaultRecordCrypto.gcmTagByteCount + 1
            ).base64EncodedString()
        )
        let duplicateEnvelope = try AtlasVaultEncryptedExportEnvelope(
            exportID: vector.envelope.exportID,
            createdAt: vector.envelope.createdAt,
            vaultMetadata: vector.envelope.vaultMetadata,
            records: [record, record]
        )
        let fixture = try RecoveryImportFixture(
            fileData: duplicateEnvelope.canonicalData()
        )

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.prepareImport(
                from: Self.testOnlyFileURL
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .invalidExport
            )
        }

        let snapshot = await fixture.storage.snapshot()
        XCTAssertNil(snapshot.journal)
        XCTAssertNil(snapshot.store)
        XCTAssertNil(snapshot.key)
        XCTAssertEqual(snapshot.selection, .none)
        XCTAssertFalse(snapshot.events.contains("hydrate"))
        XCTAssertFalse(snapshot.events.contains("saveJournal"))
    }

    func testHydrationFailureAndAuthorizationLossCreateNoJournal()
        async throws
    {
        let corrupt = try RecoveryImportFixture(hydrationFails: true)
        try await corrupt.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        await XCTAssertThrowsErrorAsync(
            try await corrupt.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(corrupt.vector.recoveryCode.utf8)
                )
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .invalidExport
            )
        }
        let corruptState = await corrupt.storage.snapshot()
        XCTAssertNil(corruptState.journal)

        let gate = RecoveryImportTestGate()
        let interrupted = try RecoveryImportFixture(hydrateGate: gate)
        try await interrupted.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        let confirmation = Task {
            try await interrupted.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(interrupted.vector.recoveryCode.utf8)
                )
            )
        }
        await gate.waitUntilEntered()
        await interrupted.storage.setAuthorized(false)
        await gate.release()
        await XCTAssertThrowsErrorAsync(try await confirmation.value) {
            error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .restoreUnavailable
            )
        }
        let interruptedState = await interrupted.storage.snapshot()
        XCTAssertNil(interruptedState.journal)
        XCTAssertNil(interruptedState.store)
        XCTAssertNil(interruptedState.key)
        XCTAssertEqual(interruptedState.selection, .none)
    }

    func testPauseAfterHydrationCancelsBeforePersistence() async throws {
        let gate = RecoveryImportTestGate()
        let fixture = try RecoveryImportFixture(hydrateGate: gate)
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        let confirmation = Task {
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        }

        await gate.waitUntilEntered()
        let pause = Task {
            await fixture.coordinator.pause()
        }
        await gate.waitUntilCancelled()
        await gate.release()
        await pause.value

        await XCTAssertThrowsErrorAsync(try await confirmation.value) {
            error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .cancelled
            )
        }
        let snapshot = await fixture.storage.snapshot()
        XCTAssertNil(snapshot.journal)
        XCTAssertNil(snapshot.store)
        XCTAssertNil(snapshot.key)
        XCTAssertEqual(snapshot.selection, .none)
        XCTAssertFalse(snapshot.events.contains("saveJournal"))
        XCTAssertFalse(snapshot.events.contains("saveStore:false"))
        XCTAssertFalse(snapshot.events.contains("createKey"))
        XCTAssertFalse(snapshot.events.contains("createSelection"))
    }

    func testWrongRecoveryKeyCreatesNoPersistentState() async throws {
        let fixture = try RecoveryImportFixture()
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        let wrong = AtlasVaultInMemorySecretBuffer(
            bytes: Data(Self.wrongRecoveryCode.utf8)
        )

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: wrong
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .invalidRecoveryKey
            )
        }

        let snapshot = await fixture.storage.snapshot()
        XCTAssertNil(snapshot.journal)
        XCTAssertNil(snapshot.store)
        XCTAssertNil(snapshot.key)
        XCTAssertEqual(snapshot.selection, .none)
        XCTAssertFalse(snapshot.events.contains("saveJournal"))
        XCTAssertFalse(snapshot.events.contains("saveStore"))
        XCTAssertFalse(snapshot.events.contains("createKey"))
        XCTAssertFalse(snapshot.events.contains("createSelection"))
    }

    func testConfirmedImportOrdersJournalStoreKeySelectionAndClear()
        async throws
    {
        let fixture = try RecoveryImportFixture()
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )

        let outcome = try await fixture.coordinator.confirmAndImport(
            recoverySecret: AtlasVaultInMemorySecretBuffer(
                bytes: Data(fixture.vector.recoveryCode.utf8)
            )
        )

        XCTAssertEqual(outcome, .committed)
        let snapshot = await fixture.storage.snapshot()
        XCTAssertNil(snapshot.journal)
        XCTAssertNotNil(snapshot.store)
        XCTAssertEqual(snapshot.key, fixture.vector.vaultKey)
        XCTAssertEqual(
            snapshot.selection,
            .selected(
                try AtlasSelectedVaultID(
                    validating: fixture.vector.envelope
                        .vaultMetadata.vaultID
                )
            )
        )
        let hydrate = try XCTUnwrap(
            snapshot.events.firstIndex(of: "hydrate")
        )
        let journal = try XCTUnwrap(
            snapshot.events.firstIndex(of: "saveJournal")
        )
        let store = try XCTUnwrap(
            snapshot.events.firstIndex(of: "saveStore:false")
        )
        let key = try XCTUnwrap(
            snapshot.events.firstIndex(of: "createKey")
        )
        let selection = try XCTUnwrap(
            snapshot.events.firstIndex(of: "createSelection")
        )
        let clear = try XCTUnwrap(
            snapshot.events.lastIndex(of: "clearJournal")
        )
        XCTAssertLessThan(hydrate, journal)
        XCTAssertLessThan(journal, store)
        XCTAssertLessThan(store, key)
        XCTAssertLessThan(key, selection)
        XCTAssertLessThan(selection, clear)
        XCTAssertEqual(snapshot.pendingImportStates, [true, false])

        let imported = try XCTUnwrap(snapshot.store)
        XCTAssertEqual(
            imported.vaultMetadata,
            try fixture.vector.envelope.vaultMetadata
                .localStoreMetadata()
        )
        XCTAssertEqual(imported.records, fixture.vector.envelope.records)
        XCTAssertNotEqual(
            imported.storeID,
            fixture.vector.envelope.exportID
        )
        XCTAssertNil(
            try AtlasVaultLocalStoreIO.encode(imported)
                .range(of: fixture.vector.vaultKey)
        )
    }

    func testConcurrentConfirmationCoalescesAndClearsJoiningSecret()
        async throws
    {
        let gate = RecoveryImportTestGate()
        let fixture = try RecoveryImportFixture(hydrateGate: gate)
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        let firstSecret = AtlasVaultInMemorySecretBuffer(
            bytes: Data(fixture.vector.recoveryCode.utf8)
        )
        let joiningSecret = AtlasVaultInMemorySecretBuffer(
            bytes: Data(fixture.vector.recoveryCode.utf8)
        )

        let first = Task {
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: firstSecret
            )
        }
        await gate.waitUntilEntered()
        let joining = Task {
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: joiningSecret
            )
        }

        let joiningSecretWasCleared = await waitUntilSecretCleared(
            joiningSecret
        )
        XCTAssertTrue(
            joiningSecretWasCleared,
            "A coalesced caller must not retain its unused recovery secret"
        )
        await gate.release()

        let firstOutcome = try await first.value
        let joiningOutcome = try await joining.value
        XCTAssertEqual(firstOutcome, .committed)
        XCTAssertEqual(joiningOutcome, .committed)
        let snapshot = await fixture.storage.snapshot()
        XCTAssertEqual(
            snapshot.events.filter { $0 == "hydrate" }.count,
            1
        )
        XCTAssertEqual(
            snapshot.events.filter { $0 == "saveJournal" }.count,
            1
        )
        XCTAssertEqual(
            snapshot.events.filter { $0 == "createKey" }.count,
            1
        )
        XCTAssertEqual(
            snapshot.events.filter { $0 == "createSelection" }.count,
            1
        )
    }

    func testJournalOwnershipIsRecheckedBeforeStoreCreation()
        async throws
    {
        let gate = RecoveryImportTestGate()
        let fixture = try RecoveryImportFixture(journalSaveGate: gate)
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        let confirmation = Task {
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        }
        await gate.waitUntilEntered()
        let pending = await fixture.storage.snapshot()
        let original = try XCTUnwrap(pending.journal)
        let conflicting = try AtlasVaultRecoveryImportJournal(
            importID: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            exportID: original.exportID,
            vaultID: original.vaultID,
            storeID: original.storeID,
            createdAt: original.createdAt,
            exportSHA256: original.exportSHA256,
            localStoreSHA256: original.localStoreSHA256,
            vaultKeySHA256: original.vaultKeySHA256
        )
        await fixture.storage.installJournal(conflicting)
        await gate.release()

        await XCTAssertThrowsErrorAsync(try await confirmation.value) {
            error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .recoveryRequired
            )
        }
        let snapshot = await fixture.storage.snapshot()
        XCTAssertEqual(snapshot.journal, conflicting)
        XCTAssertNil(snapshot.store)
        XCTAssertNil(snapshot.key)
        XCTAssertEqual(snapshot.selection, .none)
        XCTAssertFalse(snapshot.events.contains("saveStore:false"))
        XCTAssertFalse(snapshot.events.contains("createKey"))
        XCTAssertFalse(snapshot.events.contains("createSelection"))
    }

    func testDurabilityUnconfirmedStopsBeforeKeyAndSelection()
        async throws
    {
        let fixture = try RecoveryImportFixture()
        await fixture.storage.setCommitState(
            .committedDurabilityUnconfirmed
        )
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .durabilityVerificationRequired
            )
        }

        let snapshot = await fixture.storage.snapshot()
        XCTAssertNotNil(snapshot.journal)
        XCTAssertNotNil(snapshot.store)
        XCTAssertNil(snapshot.key)
        XCTAssertEqual(snapshot.selection, .none)
        XCTAssertFalse(snapshot.events.contains("createKey"))
        XCTAssertFalse(snapshot.events.contains("createSelection"))
    }

    func testResumeReconfirmsExistingStoreDurabilityBeforeReadiness()
        async throws
    {
        let fixture = try RecoveryImportFixture()
        await fixture.storage.setCommitState(
            .committedDurabilityUnconfirmed
        )
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        )

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.resumeImport(
                from: Self.testOnlyFileURL,
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .durabilityVerificationRequired
            )
        }

        let snapshot = await fixture.storage.snapshot()
        XCTAssertNotNil(snapshot.journal)
        XCTAssertNotNil(snapshot.store)
        XCTAssertNil(snapshot.key)
        XCTAssertEqual(snapshot.selection, .none)
        XCTAssertTrue(
            snapshot.events.contains("confirmStoreDurability")
        )
        XCTAssertFalse(snapshot.events.contains("createKey"))
        XCTAssertFalse(snapshot.events.contains("createSelection"))
    }

    func testResumeFromStoreAndKeyCreatesSelectionOnce() async throws {
        let fixture = try RecoveryImportFixture()
        await fixture.storage.setCommitState(
            .committedDurabilityUnconfirmed
        )
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        )
        await fixture.storage.setCommitState(.committed)

        let first = try await fixture.coordinator.resumeImport(
            from: Self.testOnlyFileURL,
            recoverySecret: AtlasVaultInMemorySecretBuffer(
                bytes: Data(fixture.vector.recoveryCode.utf8)
            )
        )
        XCTAssertEqual(first, .resumed)
        let complete = await fixture.storage.snapshot()
        XCTAssertNil(complete.journal)
        XCTAssertEqual(
            complete.events.filter { $0 == "createKey" }.count,
            1
        )
        XCTAssertEqual(
            complete.events.filter { $0 == "createSelection" }.count,
            1
        )
    }

    func testResumeFromJournalOnlyRecreatesAllResourcesInOrder()
        async throws
    {
        let fixture = try RecoveryImportFixture()
        await fixture.storage.setCommitState(
            .committedDurabilityUnconfirmed
        )
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        )
        await fixture.storage.deleteStore()
        await fixture.storage.setCommitState(.committed)

        let outcome = try await fixture.coordinator.resumeImport(
            from: Self.testOnlyFileURL,
            recoverySecret: AtlasVaultInMemorySecretBuffer(
                bytes: Data(fixture.vector.recoveryCode.utf8)
            )
        )

        XCTAssertEqual(outcome, .resumed)
        let complete = await fixture.storage.snapshot()
        XCTAssertNil(complete.journal)
        XCTAssertNotNil(complete.store)
        XCTAssertEqual(complete.key, fixture.vector.vaultKey)
        guard case .selected = complete.selection else {
            return XCTFail("Expected restored selection")
        }
        XCTAssertEqual(
            complete.events.filter { $0 == "saveStore:false" }.count,
            2
        )
        XCTAssertEqual(
            complete.events.filter { $0 == "createKey" }.count,
            1
        )
        XCTAssertEqual(
            complete.events.filter { $0 == "createSelection" }.count,
            1
        )
    }

    func testResumeFromStoreAndMatchingKeySkipsDuplicateKeyCreation()
        async throws
    {
        let fixture = try RecoveryImportFixture()
        await fixture.storage.setCommitState(
            .committedDurabilityUnconfirmed
        )
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        )
        let pending = await fixture.storage.snapshot()
        let journal = try XCTUnwrap(pending.journal)
        await fixture.storage.installKey(
            fixture.vector.vaultKey,
            vaultID: journal.vaultID
        )
        await fixture.storage.setCommitState(.committed)

        let outcome = try await fixture.coordinator.resumeImport(
            from: Self.testOnlyFileURL,
            recoverySecret: AtlasVaultInMemorySecretBuffer(
                bytes: Data(fixture.vector.recoveryCode.utf8)
            )
        )

        XCTAssertEqual(outcome, .resumed)
        let complete = await fixture.storage.snapshot()
        XCTAssertNil(complete.journal)
        XCTAssertEqual(complete.key, fixture.vector.vaultKey)
        guard case .selected = complete.selection else {
            return XCTFail("Expected restored selection")
        }
        XCTAssertFalse(complete.events.contains("createKey"))
        XCTAssertEqual(
            complete.events.filter { $0 == "createSelection" }.count,
            1
        )
    }

    func testCompletionPendingFinishesCommittedSelectionWithoutRewrite()
        async throws
    {
        let fixture = try RecoveryImportFixture()
        await fixture.storage.setClearJournalFailure(true)
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .completionPending
            )
        }
        let pending = await fixture.storage.snapshot()
        XCTAssertNotNil(pending.journal)
        XCTAssertNotNil(pending.store)
        XCTAssertNotNil(pending.key)
        guard case .selected = pending.selection else {
            return XCTFail("Expected committed selection")
        }

        await fixture.storage.setClearJournalFailure(false)
        let outcome = try await fixture.coordinator
            .finishCommittedImport(
                from: Self.testOnlyFileURL,
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )

        XCTAssertEqual(outcome, .resumed)
        let complete = await fixture.storage.snapshot()
        XCTAssertNil(complete.journal)
        XCTAssertEqual(
            complete.events.filter { $0 == "saveStore:false" }.count,
            1
        )
        XCTAssertEqual(
            complete.events.filter { $0 == "createKey" }.count,
            1
        )
        XCTAssertEqual(
            complete.events.filter { $0 == "createSelection" }.count,
            1
        )
    }

    func testExplicitResetDeletesOnlyMatchingPartialResources()
        async throws
    {
        let fixture = try RecoveryImportFixture()
        await fixture.storage.setCommitState(
            .committedDurabilityUnconfirmed
        )
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        )
        let pending = await fixture.storage.snapshot()
        let journal = try XCTUnwrap(pending.journal)
        await fixture.storage.installKey(
            fixture.vector.vaultKey,
            vaultID: journal.vaultID
        )
        let resetEventStart = pending.events.count

        try await fixture.coordinator.resetPendingImport()

        let reset = await fixture.storage.snapshot()
        let resetEvents = Array(reset.events.dropFirst(resetEventStart))
        XCTAssertNil(reset.store)
        XCTAssertNil(reset.key)
        XCTAssertNil(reset.journal)
        let deleteStore = try XCTUnwrap(
            reset.events.lastIndex(of: "deleteStore")
        )
        let deleteKey = try XCTUnwrap(
            reset.events.lastIndex(of: "deleteKey")
        )
        let clear = try XCTUnwrap(
            reset.events.lastIndex(of: "clearJournal")
        )
        XCTAssertLessThan(deleteStore, clear)
        XCTAssertLessThan(deleteKey, clear)
        XCTAssertEqual(
            resetEvents.filter { $0 == "loadStore" }.count,
            3
        )
        XCTAssertEqual(
            resetEvents.filter { $0 == "loadKey" }.count,
            3
        )
        XCTAssertEqual(reset.selection, .none)
    }

    func testResetValidatesStoreAndKeyBeforeDeletingEither()
        async throws
    {
        let fixture = try RecoveryImportFixture()
        await fixture.storage.setCommitState(
            .committedDurabilityUnconfirmed
        )
        try await fixture.coordinator.prepareImport(
            from: Self.testOnlyFileURL
        )
        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.confirmAndImport(
                recoverySecret: AtlasVaultInMemorySecretBuffer(
                    bytes: Data(fixture.vector.recoveryCode.utf8)
                )
            )
        )
        let pending = await fixture.storage.snapshot()
        let journal = try XCTUnwrap(pending.journal)
        let mismatchedKey = Data(repeating: 0x99, count: 32)
        await fixture.storage.installKey(
            mismatchedKey,
            vaultID: journal.vaultID
        )

        await XCTAssertThrowsErrorAsync(
            try await fixture.coordinator.resetPendingImport()
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .recoveryRequired
            )
        }

        let unchanged = await fixture.storage.snapshot()
        XCTAssertNotNil(unchanged.store)
        XCTAssertEqual(unchanged.key, mismatchedKey)
        XCTAssertNotNil(unchanged.journal)
        XCTAssertFalse(unchanged.events.contains("deleteStore"))
        XCTAssertFalse(unchanged.events.contains("deleteKey"))
    }

    func testImportSourceAvoidsAutomaticUnlockAndNetworkBoundaries()
        throws
    {
        let source = try phaseSource("AtlasVaultRecoveryImport.swift")

        for required in [
            "com.atlasvault.recovery-import",
            "pending-v1",
            "128 * 1_024 * 1_024",
            "overwrite: false",
            "createVaultKey",
            "createSelection",
            "committedDurabilityUnconfirmed",
            "confirmStoreDurability",
            "AtlasPendingRecoveryImportCreationGate",
            "AtlasVaultEncryptedExportEnvelope.decodeStrict",
            "canonicalData()",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "URLSession",
            "CloudKit",
            "UserDefaults",
            "Task." + "detached",
            "selectUnlockMethod",
            "submitUnlock",
            "runtime.activate",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testDuplicateRecordValidationUsesSinglePassInsertion()
        throws
    {
        let source = try phaseSource("AtlasVaultRecoveryImport.swift")

        XCTAssertTrue(source.contains("recordIDs.insert(record.id).inserted"))
        XCTAssertFalse(source.contains("records.map(\\.id)"))
    }

    private func makeJournal() throws -> AtlasVaultRecoveryImportJournal {
        try AtlasVaultRecoveryImportJournal(
            importID: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            exportID: "11111111-2222-4333-8444-555555555555",
            vaultID: "99999999-8888-4777-8666-555555555555",
            storeID: "12345678-1234-4234-8234-123456789abc",
            createdAt: "2026-07-27T01:02:03Z",
            exportSHA256: String(repeating: "a", count: 64),
            localStoreSHA256: String(repeating: "b", count: 64),
            vaultKeySHA256: String(repeating: "c", count: 64)
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

    private func assertInvalidImportFile(
        _ url: URL,
        reader: AtlasVaultRecoveryImportFileReader,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(
            try reader.read(from: url),
            file: file,
            line: line
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryImportFailure,
                .invalidFile,
                file: file,
                line: line
            )
            let description = String(describing: error)
            XCTAssertFalse(
                description.contains(url.path),
                file: file,
                line: line
            )
            XCTAssertFalse(
                description.contains(url.lastPathComponent),
                file: file,
                line: line
            )
        }
    }

    private static let testOnlyFileURL = URL(
        fileURLWithPath: "/TEST_ONLY/backup.atlasvault"
    )
    private static let wrongRecoveryCode =
        "AVRK1-AEBA-GBAF-AYDQ-QCIK-BMGA-2DQP-CAIR-EEYU-CULB-OGAZ-DINR-YHI6-D4QC-5SUP-EHGQ"
}

private struct RecoveryImportVector {
    let canonicalData: Data
    let envelope: AtlasVaultEncryptedExportEnvelope
    let recoveryCode: String
    let vaultKey: Data

    static func load() throws -> RecoveryImportVector {
        let testDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let candidates = [
            testDirectory.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/atlasvault_recovery_export_vectors_v2.json"
            ),
            testDirectory.appendingPathComponent(
                "../../../../../contracts/sync/test_vectors/atlasvault_recovery_export_vectors_v2.json"
            ),
        ].map(\.standardizedFileURL)
        let url = try XCTUnwrap(
            candidates.first {
                FileManager.default.fileExists(atPath: $0.path)
            }
        )
        let root = try XCTUnwrap(
            JSONSerialization.jsonObject(
                with: Data(contentsOf: url)
            ) as? [String: Any]
        )
        let vectors = try XCTUnwrap(root["vectors"] as? [[String: Any]])
        let vector = try XCTUnwrap(vectors.first)
        let exportBase64 = try XCTUnwrap(
            vector["canonical_export_json_b64"] as? String
        )
        let canonicalData = try XCTUnwrap(
            Data(base64Encoded: exportBase64)
        )
        return try RecoveryImportVector(
            canonicalData: canonicalData,
            envelope: AtlasVaultEncryptedExportEnvelope.decodeStrict(
                canonicalData
            ),
            recoveryCode: XCTUnwrap(
                vector["canonical_recovery_text"] as? String
            ),
            vaultKey: XCTUnwrap(
                Data(
                    base64Encoded: XCTUnwrap(
                        vector["test_only_vault_key_b64"] as? String
                    )
                )
            )
        )
    }
}

private struct RecoveryImportSnapshot: Sendable {
    let journal: AtlasVaultRecoveryImportJournal?
    let store: AtlasVaultLocalStoreEnvelope?
    let key: Data?
    let selection: AtlasVaultIDSelection
    let events: [String]
    let pendingImportStates: [Bool]
}

private actor RecoveryImportStorage {
    private var journal: AtlasVaultRecoveryImportJournal?
    private var store: AtlasVaultLocalStoreEnvelope?
    private var key: Data?
    private var selection: AtlasVaultIDSelection = .none
    private var recordedEvents: [String] = []
    private var recordedPendingImportStates: [Bool] = []
    private var commitState: AtlasVaultAtomicCommitState = .committed
    private var clearJournalFails = false
    private var authorized = true

    func events() -> [String] {
        recordedEvents
    }

    func snapshot() -> RecoveryImportSnapshot {
        RecoveryImportSnapshot(
            journal: journal,
            store: store,
            key: key,
            selection: selection,
            events: recordedEvents,
            pendingImportStates: recordedPendingImportStates
        )
    }

    func setPendingImport(_ value: Bool) {
        recordedPendingImportStates.append(value)
    }

    func setCommitState(_ value: AtlasVaultAtomicCommitState) {
        commitState = value
    }

    func setClearJournalFailure(_ value: Bool) {
        clearJournalFails = value
    }

    func setAuthorized(_ value: Bool) {
        authorized = value
    }

    func authorize() -> Bool {
        recordedEvents.append("authorize")
        return authorized
    }

    func selected() -> AtlasVaultIDSelection {
        recordedEvents.append("select")
        return selection
    }

    func readFile(_ data: Data) -> Data {
        recordedEvents.append("readFile")
        return data
    }

    func hydrate(fails: Bool) throws {
        recordedEvents.append("hydrate")
        if fails {
            throw AtlasVaultRecoveryImportFailure.invalidExport
        }
    }

    func loadJournal() -> AtlasVaultRecoveryImportJournal? {
        recordedEvents.append("loadJournal")
        return journal
    }

    func saveJournal(_ value: AtlasVaultRecoveryImportJournal) {
        recordedEvents.append("saveJournal")
        journal = value
    }

    func installJournal(_ value: AtlasVaultRecoveryImportJournal) {
        journal = value
    }

    func clearJournal() throws {
        recordedEvents.append("clearJournal")
        guard !clearJournalFails else {
            throw AtlasVaultRecoveryImportFailure.completionPending
        }
        journal = nil
    }

    func loadStore() -> AtlasVaultLocalStoreEnvelope? {
        recordedEvents.append("loadStore")
        return store
    }

    func saveStore(
        _ value: AtlasVaultLocalStoreEnvelope,
        overwrite: Bool
    ) -> AtlasVaultAtomicWriteResult {
        recordedEvents.append("saveStore:\(overwrite)")
        if store == nil {
            store = value
        }
        return AtlasVaultAtomicWriteResult(commitState: commitState)
    }

    func confirmStoreDurability() throws {
        recordedEvents.append("confirmStoreDurability")
        guard commitState == .committed else {
            throw AtlasVaultRecoveryImportFailure
                .durabilityVerificationRequired
        }
    }

    func deleteStore() {
        recordedEvents.append("deleteStore")
        store = nil
    }

    func loadKey() -> Data? {
        recordedEvents.append("loadKey")
        return key
    }

    func createKey(_ value: Data) throws {
        recordedEvents.append("createKey")
        guard key == nil else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        key = value
    }

    func installKey(_ value: Data, vaultID _: String) {
        key = value
    }

    func deleteKey() {
        recordedEvents.append("deleteKey")
        key = nil
    }

    func createSelection(_ value: AtlasSelectedVaultID) throws {
        recordedEvents.append("createSelection")
        guard selection == .none else {
            throw AtlasVaultRecoveryImportFailure.existingVault
        }
        selection = .selected(value)
    }
}

private actor RecoveryImportPendingStateRecorder {
    private var recordedValues: [Bool] = []

    func record(_ value: Bool) {
        recordedValues.append(value)
    }

    func values() -> [Bool] {
        recordedValues
    }
}

private actor RecoveryImportCreationFake: AtlasLocalVaultCreating {
    private var createCalls = 0

    func createOrResume()
        async throws(AtlasLocalVaultCreationFailure)
        -> AtlasLocalVaultCreationOutcome
    {
        createCalls += 1
        return .created
    }

    func pause() async {}

    func createCallCount() -> Int {
        createCalls
    }
}

private actor RecoveryImportTransactionRecorder {
    private var recordedEntries: [String] = []
    private var didAttemptCreation = false
    private var attemptWaiters: [CheckedContinuation<Void, Never>] = []

    func enter(_ value: String) {
        recordedEntries.append(value)
    }

    func creationAttempted() {
        didAttemptCreation = true
        let waiters = attemptWaiters
        attemptWaiters.removeAll(keepingCapacity: false)
        for waiter in waiters {
            waiter.resume()
        }
    }

    func waitUntilCreationAttempted() async {
        guard !didAttemptCreation else { return }
        await withCheckedContinuation { continuation in
            attemptWaiters.append(continuation)
        }
    }

    func entries() -> [String] {
        recordedEntries
    }
}

private func waitUntilPendingTransactionWaiter(
    _ authority: AtlasVaultPendingTransactionAuthority
) async -> Bool {
    for _ in 0..<1_000 {
        if await authority.waitingOperationCount == 1 {
            return true
        }
        await Task.yield()
    }
    return await authority.waitingOperationCount == 1
}

private struct RecoveryImportFixture {
    let vector: RecoveryImportVector
    let storage: RecoveryImportStorage
    let environment: AtlasVaultRecoveryImportEnvironment
    let coordinator: AtlasVaultRecoveryImportCoordinator

    init(
        hydrateGate: RecoveryImportTestGate? = nil,
        journalSaveGate: RecoveryImportTestGate? = nil,
        fileData: Data? = nil,
        hasPendingCreation: Bool = false,
        hydrationFails: Bool = false
    ) throws {
        let vector = try RecoveryImportVector.load()
        let selectedFileData = fileData ?? vector.canonicalData
        let storage = RecoveryImportStorage()
        let environment = AtlasVaultRecoveryImportEnvironment(
            authorize: {
                await storage.authorize()
            },
            selectVault: {
                await storage.selected()
            },
            hasPendingCreation: {
                hasPendingCreation
            },
            readFile: { _ in
                await storage.readFile(selectedFileData)
            },
            loadJournal: {
                await storage.loadJournal()
            },
            saveJournal: { journal in
                await storage.saveJournal(journal)
                await journalSaveGate?.wait()
            },
            clearJournal: {
                try await storage.clearJournal()
            },
            loadStore: { _ in
                await storage.loadStore()
            },
            saveStore: { store, _, _, overwrite in
                await storage.saveStore(store, overwrite: overwrite)
            },
            confirmStoreDurability: { _ in
                try await storage.confirmStoreDurability()
            },
            deleteStore: { _ in
                await storage.deleteStore()
            },
            loadVaultKey: { _ in
                await storage.loadKey()
            },
            createVaultKey: { key, _ in
                try await storage.createKey(key)
            },
            deleteVaultKey: { _ in
                await storage.deleteKey()
            },
            createSelection: { selection in
                try await storage.createSelection(selection)
            },
            hydrate: { _, _, _ in
                try await storage.hydrate(fails: hydrationFails)
                await hydrateGate?.wait()
            },
            generateImportID: {
                "dddddddd-eeee-4fff-8aaa-bbbbbbbbbbbb"
            },
            generateStoreID: {
                "cccccccc-dddd-4eee-8fff-aaaaaaaaaaaa"
            },
            timestamp: {
                "2026-07-27T01:02:03Z"
            },
            pendingImportDidChange: { pending in
                await storage.setPendingImport(pending)
            }
        )
        self.vector = vector
        self.storage = storage
        self.environment = environment
        coordinator = AtlasVaultRecoveryImportCoordinator(
            environment: environment
        )
    }
}

private actor RecoveryImportTestGate {
    private var continuation: CheckedContinuation<Void, Never>?
    private var entered = false
    private var enteredWaiters: [CheckedContinuation<Void, Never>] = []
    private var cancelled = false
    private var cancellationWaiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        entered = true
        let waiters = enteredWaiters
        enteredWaiters.removeAll(keepingCapacity: false)
        for waiter in waiters {
            waiter.resume()
        }
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                self.continuation = continuation
            }
        } onCancel: {
            Task {
                await self.recordCancellation()
            }
        }
    }

    func waitUntilEntered() async {
        guard !entered else { return }
        await withCheckedContinuation { continuation in
            enteredWaiters.append(continuation)
        }
    }

    func waitUntilCancelled() async {
        guard !cancelled else { return }
        await withCheckedContinuation { continuation in
            cancellationWaiters.append(continuation)
        }
    }

    func release() {
        continuation?.resume()
        continuation = nil
    }

    private func recordCancellation() {
        cancelled = true
        let waiters = cancellationWaiters
        cancellationWaiters.removeAll(keepingCapacity: false)
        for waiter in waiters {
            waiter.resume()
        }
    }
}

private func waitUntilSecretCleared(
    _ secret: AtlasVaultInMemorySecretBuffer
) async -> Bool {
    for _ in 0..<1_000 {
        if await secret.isClearedForTesting {
            return true
        }
        await Task.yield()
    }
    return await secret.isClearedForTesting
}

private final class RecoveryImportJournalKeychainClient:
    AtlasKeychainClient,
    Sendable
{
    private struct State: Sendable {
        var item: AtlasKeychainItem?
        var updates = 0
    }

    private let state = Mutex(State())

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        state.withLock {
            guard $0.item == nil else {
                return errSecDuplicateItem
            }
            $0.item = item
            return errSecSuccess
        }
    }

    func copyMatching(
        _: AtlasKeychainQuery
    ) -> AtlasKeychainCopyResult {
        state.withLock {
            guard let item = $0.item else {
                return AtlasKeychainCopyResult(
                    status: errSecItemNotFound,
                    valueData: nil
                )
            }
            return AtlasKeychainCopyResult(
                status: errSecSuccess,
                valueData: item.valueData
            )
        }
    }

    func update(
        _: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        state.withLock {
            guard let item = $0.item else {
                return errSecItemNotFound
            }
            $0.item = AtlasKeychainItem(
                service: item.service,
                account: item.account,
                valueData: attributes.valueData,
                accessibility: item.accessibility
            )
            $0.updates += 1
            return errSecSuccess
        }
    }

    func delete(_: AtlasKeychainQuery) -> OSStatus {
        state.withLock {
            guard $0.item != nil else {
                return errSecItemNotFound
            }
            $0.item = nil
            return errSecSuccess
        }
    }

    func updateCount() -> Int {
        state.withLock { $0.updates }
    }

    func storedItem() -> AtlasKeychainItem? {
        state.withLock { $0.item }
    }
}

private struct RecoveryImportSelector: AtlasVaultIDSelecting {
    let selection: AtlasVaultIDSelection

    func selectVaultID() async throws(AtlasVaultIDSelectionError)
        -> AtlasVaultIDSelection
    {
        selection
    }
}

private func XCTAssertThrowsErrorAsync<T>(
    _ expression: @autoclosure () async throws -> T,
    _ errorHandler: (Error) -> Void = { _ in },
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        _ = try await expression()
        XCTFail("Expected expression to throw", file: file, line: line)
    } catch {
        errorHandler(error)
    }
}
