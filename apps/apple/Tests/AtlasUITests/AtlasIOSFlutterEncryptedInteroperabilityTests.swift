import CryptoKit
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasIOSFlutterEncryptedInteroperabilityTests: XCTestCase {
    func testFlutterOriginExportImportsThroughProductionCoordinator()
        async throws
    {
        let vector = try InteropVector.load(direction: "flutter_to_ios")
        let importData = try vector.directArtifactDataIfConfigured()
        let storage = InteropImportStorage()
        let envelope = try AtlasVaultEncryptedExportEnvelope.decodeStrict(
            importData
        )
        XCTAssertEqual(try envelope.canonicalData(), importData)
        XCTAssertEqual(importData, vector.exportData)
        XCTAssertEqual(envelope.records.count, vector.expectedRecordCount)

        let environment = AtlasVaultRecoveryImportEnvironment(
            authorize: { true },
            selectVault: {
                await storage.selection()
            },
            hasPendingCreation: { false },
            readFile: { _ in importData },
            loadJournal: {
                await storage.journal()
            },
            saveJournal: { journal in
                await storage.saveJournal(journal)
            },
            clearJournal: {
                await storage.clearJournal()
            },
            loadStore: { _ in
                await storage.store()
            },
            saveStore: { store, _, _, overwrite in
                try await storage.saveStore(
                    store,
                    overwrite: overwrite
                )
            },
            confirmStoreDurability: { _ in },
            confirmStoreDeletionDurability: { _ in },
            deleteStore: { _ in
                await storage.deleteStore()
            },
            loadVaultKey: { _ in
                await storage.key()
            },
            createVaultKey: { key, _ in
                try await storage.createKey(key)
            },
            deleteVaultKey: { _ in
                await storage.deleteKey()
            },
            createSelection: { selected in
                try await storage.createSelection(selected)
            },
            hydrate: { records, vaultID, vaultKey in
                let session = try AtlasVaultUnlockedSession(
                    vaultID: vaultID,
                    vaultKey: vaultKey
                )
                let state = try AtlasVaultRecordHydrator().hydrate(
                    records: records,
                    session: session
                )
                await storage.recordHydration(state)
            },
            generateImportID: {
                "30000000-0000-4000-8000-000000000301"
            },
            generateStoreID: {
                "30000000-0000-4000-8000-000000000302"
            },
            timestamp: {
                "2026-07-29T03:04:05Z"
            },
            pendingImportDidChange: { _ in }
        )
        let coordinator = AtlasVaultRecoveryImportCoordinator(
            environment: environment
        )

        try await coordinator.prepareImport(
            from: URL(fileURLWithPath: "/test/flutter-origin.atlasvault")
        )
        let outcome = try await coordinator.confirmAndImport(
            recoverySecret: AtlasVaultInMemorySecretBuffer(
                bytes: Data(vector.recoveryText.utf8)
            )
        )

        XCTAssertEqual(outcome, .committed)
        let snapshot = await storage.snapshot()
        XCTAssertEqual(
            snapshot.events.filter { $0 == "store.create" }.count,
            1
        )
        XCTAssertEqual(
            snapshot.events.filter { $0 == "key.create" }.count,
            1
        )
        XCTAssertEqual(
            snapshot.events.filter { $0 == "selection.create" }.count,
            1
        )
        XCTAssertLessThan(
            try XCTUnwrap(snapshot.events.firstIndex(of: "store.create")),
            try XCTUnwrap(snapshot.events.firstIndex(of: "key.create"))
        )
        XCTAssertLessThan(
            try XCTUnwrap(snapshot.events.firstIndex(of: "key.create")),
            try XCTUnwrap(snapshot.events.firstIndex(
                of: "selection.create"
            ))
        )
        let store = try XCTUnwrap(snapshot.store)
        XCTAssertNotEqual(store.storeID, vector.sourceStoreID)
        XCTAssertEqual(
            store.records.map(\.id),
            envelope.records.map(\.id)
        )
        XCTAssertEqual(store.records, envelope.records)
        let hydration = try XCTUnwrap(snapshot.hydration)
        XCTAssertEqual(hydration.savedSearches.count, 1)
        XCTAssertEqual(hydration.savedJobs.count, 1)
        XCTAssertEqual(hydration.tombstones.count, 1)
        XCTAssertNil(snapshot.journal)
        XCTAssertNotNil(snapshot.key)
        XCTAssertNotNil(snapshot.selection)
        for sentinel in vector.privateSentinels {
            XCTAssertFalse(
                String(decoding: importData, as: UTF8.self)
                    .contains(sentinel),
                sentinel
            )
        }
    }

    func testPhaseContractRegistersFlutterOriginInteroperabilityProof()
        throws
    {
        let root = try InteropVector.repositoryRoot()
        let readme = try String(
            contentsOf: root.appendingPathComponent(
                "contracts/sync/test_vectors/README.md"
            ),
            encoding: .utf8
        )

        XCTAssertTrue(
            readme.contains(
                "atlasvault_ios_flutter_interop_vectors_v1.json"
            ),
            "Phase 2E-4 vector consumer contract is not registered."
        )
    }

    func testAppleOriginExportVectorIsStrictAndCanonical() throws {
        let vector = try InteropVector.load(direction: "ios_to_flutter")
        let envelope = try AtlasVaultEncryptedExportEnvelope.decodeStrict(
            vector.exportData
        )

        XCTAssertEqual(try envelope.canonicalData(), vector.exportData)
        XCTAssertEqual(envelope.records.count, vector.expectedRecordCount)
    }

    func testAppleProductionCoordinatorWritesExactFlutterArtifact()
        async throws
    {
        let vector = try InteropVector.load(direction: "ios_to_flutter")
        let envelope = try AtlasVaultEncryptedExportEnvelope.decodeStrict(
            vector.exportData
        )
        let selected = try AtlasSelectedVaultID(
            validating: vector.vaultID
        )
        let store = AtlasVaultLocalStoreEnvelope(
            storeID: vector.sourceStoreID,
            createdAt: vector.exportTimestamp,
            updatedAt: vector.exportTimestamp,
            vaultMetadata: try envelope.vaultMetadata.localStoreMetadata(),
            records: envelope.records
        )
        let environment = AtlasVaultRecoveryExportEnvironment(
            authorize: { true },
            selectVault: { selected },
            loadVaultKey: { vaultID in
                guard vaultID == vector.vaultID else {
                    throw AtlasVaultRecoveryExportFailure.recoveryRequired
                }
                return vector.vaultKey
            },
            loadStore: { vaultID, vaultKey in
                guard
                    vaultID == vector.vaultID,
                    vaultKey == vector.vaultKey
                else {
                    throw AtlasVaultRecoveryExportFailure.recoveryRequired
                }
                return store
            },
            saveStore: { _, _, _ in
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            },
            hydrate: { records, vaultID, vaultKey in
                let session = try AtlasVaultUnlockedSession(
                    vaultID: vaultID,
                    vaultKey: vaultKey
                )
                _ = try AtlasVaultRecordHydrator().hydrate(
                    records: records,
                    session: session
                )
            },
            loadJournal: { nil },
            saveJournal: { _ in
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            },
            clearJournal: {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            },
            generateRecoveryKey: { vector.vaultKey },
            generateSalt: { Data(repeating: 0, count: 32) },
            generateNonce: { Data(repeating: 0, count: 12) },
            generateID: { vector.exportID },
            timestamp: { vector.exportTimestamp }
        )
        let coordinator = AtlasVaultRecoveryExportCoordinator(
            environment: environment
        )

        let document = try await coordinator.resumeAndPrepareExport(
            secret: vector.recoveryText
        )

        XCTAssertEqual(document.encryptedData, vector.exportData)
        XCTAssertEqual(
            InteropVector.sha256(document.encryptedData),
            vector.exportSHA256
        )
        if let artifactDirectory = InteropVector.artifactDirectory {
            try FileManager.default.createDirectory(
                at: artifactDirectory,
                withIntermediateDirectories: true
            )
            let artifactURL = artifactDirectory.appendingPathComponent(
                "ios-to-flutter.atlasvault"
            )
            try document.encryptedData.write(
                to: artifactURL,
                options: .atomic
            )
            try (vector.exportSHA256 + "\n").write(
                to: artifactDirectory.appendingPathComponent(
                    "ios-to-flutter.sha256"
                ),
                atomically: true,
                encoding: .utf8
            )
            XCTAssertEqual(
                try Data(contentsOf: artifactURL),
                vector.exportData
            )
        }
    }

    func testPhaseContractRegistersAppleOriginImportCompletion() throws {
        let root = try InteropVector.repositoryRoot()
        let architecture = try String(
            contentsOf: root.appendingPathComponent(
                "docs/architecture/"
                    + "phase2e4_ios_flutter_encrypted_interoperability.md"
            ),
            encoding: .utf8
        )

        XCTAssertTrue(
            architecture.contains(
                "Flutter import of Apple export: implemented."
            ),
            "Checkpoint B Apple-origin import is not implemented."
        )
    }
}

private struct InteropVector {
    let direction: String
    let recoveryText: String
    let vaultID: String
    let vaultKey: Data
    let exportID: String
    let exportTimestamp: String
    let sourceStoreID: String
    let exportData: Data
    let exportSHA256: String
    let expectedRecordCount: Int
    let privateSentinels: [String]

    static var artifactDirectory: URL? {
        ProcessInfo.processInfo.environment[
            "ATLAS_INTEROP_ARTIFACT_DIR"
        ].map {
            URL(fileURLWithPath: $0, isDirectory: true)
        }
    }

    static func load(direction: String) throws -> Self {
        let root = try repositoryRoot()
        let data = try Data(
            contentsOf: root.appendingPathComponent(
                "contracts/sync/test_vectors/"
                    + "atlasvault_ios_flutter_interop_vectors_v1.json"
            )
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data)
                as? [String: Any]
        )
        XCTAssertEqual(
            object["_warning"] as? String,
            "FAKE TEST DATA ONLY. No real user data or production keys."
        )
        let value = try XCTUnwrap(object[direction] as? [String: Any])
        let encoded = try XCTUnwrap(
            value["canonical_encrypted_export_b64"] as? String
        )
        let vectorData = try XCTUnwrap(
            Data(base64Encoded: encoded)
        )
        return Self(
            direction: direction,
            recoveryText: try XCTUnwrap(
                value["test_only_recovery_key_text"] as? String
            ),
            vaultID: try XCTUnwrap(value["vault_id"] as? String),
            vaultKey: try XCTUnwrap(
                Data(
                    base64Encoded: try XCTUnwrap(
                        value["test_only_vault_key_b64"] as? String
                    )
                )
            ),
            exportID: try XCTUnwrap(value["export_id"] as? String),
            exportTimestamp: try XCTUnwrap(
                value["export_timestamp"] as? String
            ),
            sourceStoreID: try XCTUnwrap(
                value["local_source_store_id"] as? String
            ),
            exportData: vectorData,
            exportSHA256: try XCTUnwrap(
                value["canonical_encrypted_export_sha256"] as? String
            ),
            expectedRecordCount: try XCTUnwrap(
                value["expected_encrypted_record_count"] as? Int
            ),
            privateSentinels: [
                "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
                "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
                "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
                "saved_search",
                "saved_job",
            ]
        )
    }

    func directArtifactDataIfConfigured() throws -> Data {
        guard let directory = Self.artifactDirectory else {
            return exportData
        }
        let filename = direction == "flutter_to_ios"
            ? "flutter-to-ios.atlasvault"
            : "ios-to-flutter.atlasvault"
        let directURL = directory.appendingPathComponent(filename)
        guard FileManager.default.fileExists(atPath: directURL.path) else {
            XCTFail("Required direct interoperability artifact is absent.")
            throw CocoaError(.fileNoSuchFile)
        }
        let data = try Data(contentsOf: directURL)
        XCTAssertEqual(data, exportData)
        XCTAssertEqual(Self.sha256(data), exportSHA256)
        return data
    }

    static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    static func repositoryRoot() throws -> URL {
        var candidate = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        while candidate.path != "/" {
            let vectors = candidate.appendingPathComponent(
                "contracts/sync/test_vectors",
                isDirectory: true
            )
            var isDirectory = ObjCBool(false)
            if FileManager.default.fileExists(
                atPath: vectors.path,
                isDirectory: &isDirectory
            ), isDirectory.boolValue {
                return candidate
            }
            candidate.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }
}

private actor InteropImportStorage {
    struct Snapshot: Sendable {
        let journal: AtlasVaultRecoveryImportJournal?
        let store: AtlasVaultLocalStoreEnvelope?
        let key: Data?
        let selection: AtlasSelectedVaultID?
        let hydration: AtlasVaultHydratedState?
        let events: [String]
    }

    private var pendingJournal: AtlasVaultRecoveryImportJournal?
    private var installedStore: AtlasVaultLocalStoreEnvelope?
    private var installedKey: Data?
    private var selected: AtlasSelectedVaultID?
    private var hydrated: AtlasVaultHydratedState?
    private var events: [String] = []

    func selection() -> AtlasVaultIDSelection {
        selected.map(AtlasVaultIDSelection.selected) ?? .none
    }

    func journal() -> AtlasVaultRecoveryImportJournal? {
        pendingJournal
    }

    func saveJournal(_ value: AtlasVaultRecoveryImportJournal) {
        events.append("journal.save")
        pendingJournal = value
    }

    func clearJournal() {
        events.append("journal.clear")
        pendingJournal = nil
    }

    func store() -> AtlasVaultLocalStoreEnvelope? {
        installedStore
    }

    func saveStore(
        _ value: AtlasVaultLocalStoreEnvelope,
        overwrite: Bool
    ) throws -> AtlasVaultAtomicWriteResult {
        guard !overwrite, installedStore == nil else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        events.append("store.create")
        installedStore = value
        return AtlasVaultAtomicWriteResult(commitState: .committed)
    }

    func deleteStore() {
        events.append("store.delete")
        installedStore = nil
    }

    func key() -> Data? {
        installedKey
    }

    func createKey(_ value: Data) throws {
        guard installedKey == nil else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        events.append("key.create")
        installedKey = value
    }

    func deleteKey() {
        events.append("key.delete")
        installedKey = nil
    }

    func createSelection(_ value: AtlasSelectedVaultID) throws {
        guard selected == nil else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        events.append("selection.create")
        selected = value
    }

    func recordHydration(_ value: AtlasVaultHydratedState) {
        events.append("hydrate")
        hydrated = value
    }

    func snapshot() -> Snapshot {
        Snapshot(
            journal: pendingJournal,
            store: installedStore,
            key: installedKey,
            selection: selected,
            hydration: hydrated,
            events: events
        )
    }
}
