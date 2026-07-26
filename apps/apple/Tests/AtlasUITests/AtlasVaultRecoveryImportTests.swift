import Foundation
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
            "vault_key",
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
    }

    func testCoordinatorConstructionInvokesNoDependency() async throws {
        let fixture = try RecoveryImportFixture()

        _ = fixture.coordinator

        XCTAssertEqual(await fixture.storage.events(), [])
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

        try await fixture.coordinator.resetPendingImport()

        let reset = await fixture.storage.snapshot()
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
        XCTAssertEqual(reset.selection, .none)
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
            "AtlasVaultEncryptedExportEnvelope.decodeStrict",
            "canonicalData()",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "URLSession",
            "CloudKit",
            "UserDefaults",
            "Task.detached",
            "selectUnlockMethod",
            "submitUnlock",
            "runtime.activate",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
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
}

private actor RecoveryImportStorage {
    private var journal: AtlasVaultRecoveryImportJournal?
    private var store: AtlasVaultLocalStoreEnvelope?
    private var key: Data?
    private var selection: AtlasVaultIDSelection = .none
    private var recordedEvents: [String] = []
    private var commitState: AtlasVaultAtomicCommitState = .committed

    func events() -> [String] {
        recordedEvents
    }

    func snapshot() -> RecoveryImportSnapshot {
        RecoveryImportSnapshot(
            journal: journal,
            store: store,
            key: key,
            selection: selection,
            events: recordedEvents
        )
    }

    func setCommitState(_ value: AtlasVaultAtomicCommitState) {
        commitState = value
    }

    func authorize() -> Bool {
        recordedEvents.append("authorize")
        return true
    }

    func selected() -> AtlasVaultIDSelection {
        recordedEvents.append("select")
        return selection
    }

    func readFile(_ data: Data) -> Data {
        recordedEvents.append("readFile")
        return data
    }

    func hydrate() {
        recordedEvents.append("hydrate")
    }

    func loadJournal() -> AtlasVaultRecoveryImportJournal? {
        recordedEvents.append("loadJournal")
        return journal
    }

    func saveJournal(_ value: AtlasVaultRecoveryImportJournal) {
        recordedEvents.append("saveJournal")
        journal = value
    }

    func clearJournal() {
        recordedEvents.append("clearJournal")
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

private struct RecoveryImportFixture {
    let vector: RecoveryImportVector
    let storage: RecoveryImportStorage
    let environment: AtlasVaultRecoveryImportEnvironment
    let coordinator: AtlasVaultRecoveryImportCoordinator

    init() throws {
        let vector = try RecoveryImportVector.load()
        let storage = RecoveryImportStorage()
        let environment = AtlasVaultRecoveryImportEnvironment(
            authorize: {
                await storage.authorize()
            },
            selectVault: {
                await storage.selected()
            },
            hasPendingCreation: {
                false
            },
            readFile: { _ in
                await storage.readFile(vector.canonicalData)
            },
            loadJournal: {
                await storage.loadJournal()
            },
            saveJournal: { journal in
                await storage.saveJournal(journal)
            },
            clearJournal: {
                await storage.clearJournal()
            },
            loadStore: { _ in
                await storage.loadStore()
            },
            saveStore: { store, _, _, overwrite in
                await storage.saveStore(store, overwrite: overwrite)
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
                await storage.hydrate()
            },
            generateImportID: {
                "dddddddd-eeee-4fff-8aaa-bbbbbbbbbbbb"
            },
            generateStoreID: {
                "cccccccc-dddd-4eee-8fff-aaaaaaaaaaaa"
            },
            timestamp: {
                "2026-07-27T01:02:03Z"
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
