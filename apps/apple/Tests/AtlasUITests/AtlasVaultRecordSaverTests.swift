import XCTest
@testable import AtlasUI

final class AtlasVaultRecordSaverTests: XCTestCase {
    func testCreateSavedSearchProducesEncryptedRecord() throws {
        let record = try saveSingleCreate(recordType: "saved_search")

        XCTAssertFalse(record.deleted)
        XCTAssertEqual(record.id, "00000000-0000-4000-8000-000000000401")
        XCTAssertNil(record.parentRevision)
        XCTAssertEqual(record.revision, "00000000-0000-4000-9000-000000000401")
        XCTAssertEqual(record.keyID, Self.keyID)
        XCTAssertFalse(record.ciphertext.isEmpty)
    }

    func testCreateSavedJobProducesEncryptedRecord() throws {
        let record = try saveSingleCreate(recordType: "saved_job")

        XCTAssertFalse(record.deleted)
        XCTAssertEqual(record.id, "00000000-0000-4000-8000-000000000401")
        XCTAssertFalse(record.ciphertext.isEmpty)
    }

    func testCreateApplicationNoteProducesEncryptedRecord() throws {
        let record = try saveSingleCreate(recordType: "application_note")

        XCTAssertFalse(record.deleted)
        XCTAssertEqual(record.id, "00000000-0000-4000-8000-000000000401")
        XCTAssertFalse(record.ciphertext.isEmpty)
    }

    func testCreateProfileSnippetProducesEncryptedRecord() throws {
        let record = try saveSingleCreate(recordType: "profile_snippet")

        XCTAssertFalse(record.deleted)
        XCTAssertEqual(record.id, "00000000-0000-4000-8000-000000000401")
        XCTAssertFalse(record.ciphertext.isEmpty)
    }

    func testCreateDraftMetadataProducesEncryptedRecord() throws {
        let record = try saveSingleCreate(recordType: "draft_metadata")

        XCTAssertFalse(record.deleted)
        XCTAssertEqual(record.id, "00000000-0000-4000-8000-000000000401")
        XCTAssertFalse(record.ciphertext.isEmpty)
    }

    func testEncryptedOutputHasNoPrivateSentinelsOrPlaintextRecordTypes() throws {
        let records = try saveCreates(recordTypes: Self.requiredRecordTypes)
        let serializedRecords = try serialized(records: records)

        for forbidden in try forbiddenPlaintextStrings() {
            XCTAssertFalse(serializedRecords.contains(forbidden), forbidden)
        }
    }

    func testHydratorRoundTripsSaverOutputBackToInMemoryPrivateState() throws {
        let records = try saveCreates(recordTypes: Self.requiredRecordTypes)
        let state = try AtlasVaultRecordHydrator().hydrate(records: records, session: session())

        XCTAssertEqual(state.savedSearches.count, 1)
        XCTAssertEqual(state.savedSearches[0].payload.name, "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK")
        XCTAssertEqual(state.savedSearches[0].payload.request.text, "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK")
        XCTAssertEqual(state.savedJobs.count, 1)
        XCTAssertEqual(state.savedJobs[0].payload.jobKey, "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK")
        XCTAssertEqual(state.applicationNotes.count, 1)
        XCTAssertEqual(state.applicationNotes[0].payload.body, "FAKE_PRIVATE_NOTE_DO_NOT_LEAK")
        XCTAssertEqual(state.profileSnippets.count, 1)
        XCTAssertEqual(state.profileSnippets[0].payload.body, "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK")
        XCTAssertEqual(state.draftMetadata.count, 1)
        XCTAssertEqual(
            state.draftMetadata[0].payload.generatedDocumentReference,
            "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK"
        )
        XCTAssertTrue(state.tombstones.isEmpty)
    }

    func testUpdateSavedJobPreservesRecordIDAndSetsParentRevision() throws {
        let existingRecordID = "00000000-0000-4000-8000-000000000777"
        let currentRevision = "00000000-0000-4000-9000-000000000777"
        let saver = deterministicSaver(recordIDs: [], revisions: ["00000000-0000-4000-9001-000000000777"])
        let records = try saver.save(
            mutations: AtlasVaultMutationSet(updates: [
                AtlasVaultUpdateMutation(
                    recordID: existingRecordID,
                    currentRevision: currentRevision,
                    payload: payload(recordType: "saved_job"),
                    keyID: Self.keyID
                ),
            ]),
            session: session()
        )

        XCTAssertEqual(records.count, 1)
        XCTAssertEqual(records[0].id, existingRecordID)
        XCTAssertEqual(records[0].parentRevision, currentRevision)
        XCTAssertEqual(records[0].revision, "00000000-0000-4000-9001-000000000777")
        XCTAssertFalse(records[0].deleted)

        let state = try AtlasVaultRecordHydrator().hydrate(records: records, session: session())
        XCTAssertEqual(state.savedJobs.count, 1)
        XCTAssertEqual(state.savedJobs[0].metadata.id, existingRecordID)
        XCTAssertEqual(state.savedJobs[0].metadata.parentRevision, currentRevision)
        XCTAssertEqual(state.savedJobs[0].payload.jobKey, "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK")
    }

    func testDeleteCreatesTombstoneEnvelope() throws {
        let existingRecordID = "00000000-0000-4000-8000-000000000888"
        let currentRevision = "00000000-0000-4000-9000-000000000888"
        let saver = deterministicSaver(recordIDs: [], revisions: ["00000000-0000-4000-9001-000000000888"])
        let records = try saver.save(
            mutations: AtlasVaultMutationSet(deletes: [
                AtlasVaultDeleteMutation(
                    recordID: existingRecordID,
                    currentRevision: currentRevision,
                    keyID: Self.keyID
                ),
            ]),
            session: session()
        )

        XCTAssertEqual(records.count, 1)
        XCTAssertEqual(records[0].id, existingRecordID)
        XCTAssertEqual(records[0].parentRevision, currentRevision)
        XCTAssertEqual(records[0].revision, "00000000-0000-4000-9001-000000000888")
        XCTAssertTrue(records[0].deleted)

        let state = try AtlasVaultRecordHydrator().hydrate(records: records, session: session())
        XCTAssertTrue(state.savedJobs.isEmpty)
        XCTAssertEqual(state.tombstones.map(\.metadata.id), [existingRecordID])
    }

    func testTombstoneOutputContainsNoPlaintextPayload() throws {
        let saver = deterministicSaver(recordIDs: [], revisions: ["00000000-0000-4000-9001-000000000889"])
        let records = try saver.save(
            mutations: AtlasVaultMutationSet(deletes: [
                AtlasVaultDeleteMutation(
                    recordID: "00000000-0000-4000-8000-000000000889",
                    currentRevision: "00000000-0000-4000-9000-000000000889",
                    keyID: Self.keyID
                ),
            ]),
            session: session()
        )
        let serializedRecords = try serialized(records: records)

        for forbidden in try forbiddenPlaintextStrings() {
            XCTAssertFalse(serializedRecords.contains(forbidden), forbidden)
        }
        XCTAssertEqual(
            try data(base64: records[0].ciphertext).count,
            AtlasVaultRecordCrypto.gcmTagByteCount
        )
        XCTAssertEqual(
            try AtlasVaultRecordCrypto.open(record: records[0], vaultKey: Self.vaultKey, vaultID: Self.vaultID),
            Data()
        )
    }

    func testFreshNonceIsUsedForSeparateSaves() throws {
        let saver = AtlasVaultRecordSaver()
        let mutation = AtlasVaultMutationSet(creates: [
            AtlasVaultCreateMutation(payload: try payload(recordType: "saved_search"), keyID: Self.keyID),
        ])

        let first = try saver.save(mutations: mutation, session: session())[0]
        let second = try saver.save(mutations: mutation, session: session())[0]

        XCTAssertNotEqual(first.nonce, second.nonce)
    }

    func testDeterministicInjectionIsInternalAndSupportsStableFakeVectors() throws {
        let saver = deterministicSaver(
            recordIDs: ["00000000-0000-4000-8000-000000000901"],
            revisions: ["00000000-0000-4000-9000-000000000901"],
            nonceByte: 91
        )

        let records = try saver.save(
            mutations: AtlasVaultMutationSet(creates: [
                AtlasVaultCreateMutation(payload: payload(recordType: "saved_search"), keyID: Self.keyID),
            ]),
            session: session()
        )

        XCTAssertEqual(records[0].id, "00000000-0000-4000-8000-000000000901")
        XCTAssertEqual(records[0].revision, "00000000-0000-4000-9000-000000000901")
        XCTAssertEqual(
            records[0].nonce,
            Data(repeating: 91, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString()
        )
    }

    func testInvalidSessionFailsSafely() throws {
        let invalidSession = try AtlasVaultUnlockedSession(vaultID: " ", vaultKey: Self.vaultKey)

        XCTAssertThrowsError(try AtlasVaultRecordSaver().save(
            mutations: AtlasVaultMutationSet(creates: [
                AtlasVaultCreateMutation(payload: payload(recordType: "saved_search"), keyID: Self.keyID),
            ]),
            session: invalidSession
        )) { error in
            XCTAssertEqual(error as? AtlasVaultSaveError, .invalidSession)
            assertErrorIsNonSensitive(error)
        }
    }

    func testUnsupportedPayloadSchemaFailsSafely() throws {
        let envelope = AtlasSavedSearchVaultRecordPayload(
            type: .savedSearch,
            payloadSchema: AtlasSavedSearchVaultRecordPayload.payloadSchema + 1,
            payload: try savedSearchEnvelope().payload,
            clientCreatedAt: "2026-01-02T03:04:05Z",
            clientUpdatedAt: "2026-01-02T04:05:06Z"
        )

        XCTAssertThrowsError(try AtlasVaultRecordSaver().save(
            mutations: AtlasVaultMutationSet(creates: [
                AtlasVaultCreateMutation(payload: .savedSearch(envelope), keyID: Self.keyID),
            ]),
            session: session()
        )) { error in
            XCTAssertEqual(error as? AtlasVaultSaveError, .unsupportedPayloadSchema)
            assertErrorIsNonSensitive(error)
        }
    }

    func testSaveErrorStringAndDebugOutputDoNotContainPrivateSentinels() throws {
        for error in [
            AtlasVaultSaveError.invalidSession,
            .unsupportedPayloadType,
            .unsupportedPayloadSchema,
            .encodingFailed,
            .encryptionFailed,
            .missingRecordID,
            .staleRevision,
            .invalidMutation,
            .unsupportedRecordVersion,
        ] {
            assertErrorIsNonSensitive(error)
        }
    }

    func testNoPublicSnapshotMutationOccurs() throws {
        let snapshot = try publicSnapshot()
        let before = try encodedSnapshot(snapshot)

        _ = try saveCreates(recordTypes: Self.requiredRecordTypes)

        let after = try encodedSnapshot(snapshot)
        XCTAssertEqual(before, after)
    }

    func testSaverSourceAvoidsRuntimePublicCacheFileIOAndNetworking() throws {
        let source = try String(contentsOf: sourceFileURL(), encoding: .utf8)
        for forbidden in [
            "SwiftUI",
            "SearchViewModel",
            "AtlasLocalCache",
            "UserDefaults",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "URLSession",
            "Data.write",
            "createFile",
            "FileManager.default",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private static let requiredRecordTypes = [
        "saved_search",
        "saved_job",
        "application_note",
        "profile_snippet",
        "draft_metadata",
    ]
    private static let vaultID = "00000000-0000-4000-8000-000000000200"
    private static let keyID = "phase2d20-test-key"
    private static let vaultKey = Data((0..<AtlasVaultRecordCrypto.vaultKeyByteCount).map { UInt8($0) })

    private func saveSingleCreate(recordType: String) throws -> AtlasVaultEncryptedRecordEnvelope {
        try saveCreates(recordTypes: [recordType])[0]
    }

    private func saveCreates(recordTypes: [String]) throws -> [AtlasVaultEncryptedRecordEnvelope] {
        let saver = deterministicSaver(
            recordIDs: recordTypes.enumerated().map { index, _ in
                String(format: "00000000-0000-4000-8000-0000000004%02d", index + 1)
            },
            revisions: recordTypes.enumerated().map { index, _ in
                String(format: "00000000-0000-4000-9000-0000000004%02d", index + 1)
            }
        )
        let creates = try recordTypes.map { recordType in
            AtlasVaultCreateMutation(payload: try payload(recordType: recordType), keyID: Self.keyID)
        }
        return try saver.save(
            mutations: AtlasVaultMutationSet(creates: creates),
            session: session()
        )
    }

    private func deterministicSaver(
        recordIDs: [String],
        revisions: [String],
        nonceByte: UInt8 = 27
    ) -> AtlasVaultRecordSaver {
        let recordIDSequence = LockedSequence(recordIDs)
        let revisionSequence = LockedSequence(revisions)
        return AtlasVaultRecordSaver(
            recordIDGenerator: { recordIDSequence.next() },
            revisionIDGenerator: { revisionSequence.next() },
            nonceGenerator: {
                Data(repeating: nonceByte, count: AtlasVaultRecordCrypto.nonceByteCount)
            }
        )
    }

    private func payload(recordType: String) throws -> AtlasVaultSavePayload {
        let data = try plaintextVectorData(recordType: recordType)
        switch recordType {
        case "saved_search":
            return .savedSearch(try JSONDecoder().decode(AtlasSavedSearchVaultRecordPayload.self, from: data))
        case "saved_job":
            return .savedJob(try JSONDecoder().decode(AtlasSavedJobVaultRecordPayload.self, from: data))
        case "application_note":
            return .applicationNote(try JSONDecoder().decode(AtlasApplicationNoteVaultRecordPayload.self, from: data))
        case "profile_snippet":
            return .profileSnippet(try JSONDecoder().decode(AtlasProfileSnippetVaultRecordPayload.self, from: data))
        case "draft_metadata":
            return .draftMetadata(try JSONDecoder().decode(AtlasDraftMetadataVaultRecordPayload.self, from: data))
        default:
            throw testError("Unsupported test record type \(recordType)")
        }
    }

    private func savedSearchEnvelope() throws -> AtlasSavedSearchVaultRecordPayload {
        guard case .savedSearch(let envelope) = try payload(recordType: "saved_search") else {
            throw testError("Expected saved_search payload")
        }
        return envelope
    }

    private func session() throws -> AtlasVaultUnlockedSession {
        try AtlasVaultUnlockedSession(vaultID: Self.vaultID, vaultKey: Self.vaultKey)
    }

    private func plaintextVectorData(recordType: String) throws -> Data {
        let payloads = try dictionary(payloadVectorRoot()["payloads"], context: "payloads")
        let vector = try dictionary(payloads[recordType], context: recordType)
        return try JSONSerialization.data(withJSONObject: vector, options: [.sortedKeys])
    }

    private func payloadVectorRoot() throws -> [String: Any] {
        let data = try Data(contentsOf: vectorFileURL(fileName: "atlasvault_payload_vectors_v1.json"))
        let object = try JSONSerialization.jsonObject(with: data)
        return try dictionary(object, context: "payload vector root")
    }

    private func forbiddenPlaintextStrings() throws -> [String] {
        let root = try payloadVectorRoot()
        let expectations = try dictionary(root["encrypted_record_expectations"], context: "encrypted_record_expectations")
        return try stringArray(expectations["forbidden_plaintext_strings"], context: "forbidden_plaintext_strings")
    }

    private func serialized(records: [AtlasVaultEncryptedRecordEnvelope]) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try records.map { record in
            String(data: try encoder.encode(record), encoding: .utf8) ?? ""
        }.joined(separator: "\n")
    }

    private func assertErrorIsNonSensitive(
        _ error: Error,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let rendered = "\(String(describing: error)) \(String(reflecting: error))"
        for forbidden in [
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata",
            "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
            "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
            "FAKE_PRIVATE_FILTER_DO_NOT_LEAK",
            "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
            "FAKE_SAVED_JOB_STATUS_DO_NOT_LEAK",
            "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
            "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
            "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK",
            "TOP_SECRET_SENTINEL_DO_NOT_LEAK",
        ] {
            XCTAssertFalse(rendered.contains(forbidden), forbidden, file: file, line: line)
        }
    }

    private func publicSnapshot() throws -> AtlasPublicLocalSnapshot {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(AtlasPublicLocalSnapshot.self, from: Data("""
        {
          "savedAt": "2026-01-07T00:00:00Z",
          "baseURL": "http://127.0.0.1:8765",
          "health": {
            "status": "ok",
            "db_path": null,
            "schema_version": "test",
            "open_jobs": 0,
            "enabled_sources": 0,
            "last_sync_at": null
          },
          "searchResponse": {
            "total": 0,
            "limit": 0,
            "offset": 0,
            "results": [],
            "facets": {},
            "facet_labels": {},
            "unclassified_count": 0
          },
          "sources": [],
          "recentRuns": []
        }
        """.utf8))
    }

    private func encodedSnapshot(_ snapshot: AtlasPublicLocalSnapshot) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(snapshot)
    }

    private func vectorFileURL(fileName: String) throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            currentDirectory.appendingPathComponent("../../contracts/sync/test_vectors/\(fileName)"),
            currentDirectory.appendingPathComponent("contracts/sync/test_vectors/\(fileName)"),
            sourceDirectory.appendingPathComponent("../../../../contracts/sync/test_vectors/\(fileName)"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find shared AtlasVault vector file \(fileName)")
    }

    private func sourceFileURL() throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let candidates = [
            currentDirectory.appendingPathComponent("Sources/AtlasUI/AtlasVaultRecordSaver.swift"),
            currentDirectory.appendingPathComponent("apps/apple/Sources/AtlasUI/AtlasVaultRecordSaver.swift"),
            URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .appendingPathComponent("../../Sources/AtlasUI/AtlasVaultRecordSaver.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find AtlasVaultRecordSaver.swift")
    }

    private func dictionary(_ value: Any?, context: String) throws -> [String: Any] {
        guard let dictionary = value as? [String: Any] else {
            throw testError("\(context) must be an object")
        }
        return dictionary
    }

    private func stringArray(_ value: Any?, context: String) throws -> [String] {
        guard let array = value as? [String] else {
            throw testError("\(context) must be a string array")
        }
        return array
    }

    private func data(base64: String) throws -> Data {
        guard let data = Data(base64Encoded: base64) else {
            throw testError("Expected base64 data")
        }
        return data
    }

    private func testError(_ message: String) -> NSError {
        NSError(
            domain: "AtlasVaultRecordSaverTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}

private final class LockedSequence<Element>: @unchecked Sendable {
    private let values: [Element]
    private var index = 0
    private let lock = NSLock()

    init(_ values: [Element]) {
        self.values = values
    }

    func next() -> Element {
        lock.lock()
        defer {
            index += 1
            lock.unlock()
        }
        precondition(index < values.count, "LockedSequence exhausted")
        return values[index]
    }
}
