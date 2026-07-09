import XCTest
@testable import AtlasUI

final class AtlasVaultRecordHydratorTests: XCTestCase {
    func testHydratesSavedSearchVectorIntoInMemorySavedSearch() throws {
        let state = try hydratedState(recordTypes: ["saved_search"])

        XCTAssertEqual(state.savedSearches.count, 1)
        XCTAssertEqual(state.savedSearches[0].metadata.id, recordID(for: "saved_search"))
        XCTAssertEqual(state.savedSearches[0].payload.name, "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK")
        XCTAssertEqual(state.savedSearches[0].payload.request.text, "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK")
        XCTAssertEqual(state.savedSearches[0].payload.request.sourceIDs, ["FAKE_PRIVATE_FILTER_DO_NOT_LEAK"])
        XCTAssertTrue(state.savedJobs.isEmpty)
        XCTAssertTrue(state.tombstones.isEmpty)
    }

    func testHydratesSavedJobVectorIntoInMemorySavedJob() throws {
        let state = try hydratedState(recordTypes: ["saved_job"])

        XCTAssertEqual(state.savedJobs.count, 1)
        XCTAssertEqual(state.savedJobs[0].metadata.id, recordID(for: "saved_job"))
        XCTAssertEqual(state.savedJobs[0].payload.jobKey, "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK")
        XCTAssertEqual(state.savedJobs[0].payload.status, "FAKE_SAVED_JOB_STATUS_DO_NOT_LEAK")
        XCTAssertEqual(state.savedJobs[0].payload.notes, "FAKE_PRIVATE_NOTE_DO_NOT_LEAK")
        XCTAssertTrue(state.savedSearches.isEmpty)
        XCTAssertTrue(state.tombstones.isEmpty)
    }

    func testHydratesApplicationNoteVector() throws {
        let state = try hydratedState(recordTypes: ["application_note"])

        XCTAssertEqual(state.applicationNotes.count, 1)
        XCTAssertEqual(state.applicationNotes[0].metadata.id, recordID(for: "application_note"))
        XCTAssertEqual(state.applicationNotes[0].payload.title, "TOP_SECRET_SENTINEL_DO_NOT_LEAK application note")
        XCTAssertEqual(state.applicationNotes[0].payload.body, "FAKE_PRIVATE_NOTE_DO_NOT_LEAK")
        XCTAssertEqual(state.applicationNotes[0].payload.linkedJobKey, "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK")
        XCTAssertTrue(state.savedSearches.isEmpty)
        XCTAssertTrue(state.tombstones.isEmpty)
    }

    func testHydratesProfileSnippetVector() throws {
        let state = try hydratedState(recordTypes: ["profile_snippet"])

        XCTAssertEqual(state.profileSnippets.count, 1)
        XCTAssertEqual(state.profileSnippets[0].metadata.id, recordID(for: "profile_snippet"))
        XCTAssertEqual(state.profileSnippets[0].payload.body, "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK")
        XCTAssertEqual(state.profileSnippets[0].payload.provenanceNotes, "TOP_SECRET_SENTINEL_DO_NOT_LEAK provenance")
        XCTAssertEqual(state.profileSnippets[0].payload.tags, ["programme", "monitoring"])
        XCTAssertTrue(state.savedSearches.isEmpty)
        XCTAssertTrue(state.tombstones.isEmpty)
    }

    func testHydratesDraftMetadataVector() throws {
        let state = try hydratedState(recordTypes: ["draft_metadata"])

        XCTAssertEqual(state.draftMetadata.count, 1)
        XCTAssertEqual(state.draftMetadata[0].metadata.id, recordID(for: "draft_metadata"))
        XCTAssertEqual(state.draftMetadata[0].payload.linkedJobKey, "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK")
        XCTAssertEqual(
            state.draftMetadata[0].payload.generatedDocumentReference,
            "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK"
        )
        XCTAssertEqual(state.draftMetadata[0].payload.contextSummary, "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK")
        XCTAssertTrue(state.savedSearches.isEmpty)
        XCTAssertTrue(state.tombstones.isEmpty)
    }

    func testHydratesAllSupportedRecordTypesTogether() throws {
        let state = try hydratedState(recordTypes: Self.requiredRecordTypes)

        XCTAssertEqual(state.savedSearches.count, 1)
        XCTAssertEqual(state.savedJobs.count, 1)
        XCTAssertEqual(state.applicationNotes.count, 1)
        XCTAssertEqual(state.profileSnippets.count, 1)
        XCTAssertEqual(state.draftMetadata.count, 1)
        XCTAssertTrue(state.tombstones.isEmpty)
    }

    func testHydratedPrivateSentinelsOnlyAppearAfterInMemoryHydration() throws {
        let records = try encryptedRecords(recordTypes: Self.requiredRecordTypes)
        let serializedEncryptedRecords = try records.map { record in
            String(data: try JSONEncoder().encode(record), encoding: .utf8) ?? ""
        }.joined(separator: "\n")

        for forbidden in try forbiddenPlaintextStrings() {
            XCTAssertFalse(serializedEncryptedRecords.contains(forbidden), forbidden)
        }

        let state = try AtlasVaultRecordHydrator().hydrate(records: records, session: session())
        let hydratedText = privateHydratedText(state)
        for expected in [
            "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
            "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
            "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
            "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
            "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
            "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK",
            "TOP_SECRET_SENTINEL_DO_NOT_LEAK",
        ] {
            XCTAssertTrue(hydratedText.contains(expected), expected)
        }
    }

    func testTombstoneRecordsAreRetainedAndExcludedFromActiveState() throws {
        let tombstone = try encryptedRecord(recordType: "saved_job", deleted: true)
        let state = try AtlasVaultRecordHydrator().hydrate(records: [tombstone], session: session())

        XCTAssertTrue(state.savedSearches.isEmpty)
        XCTAssertTrue(state.savedJobs.isEmpty)
        XCTAssertTrue(state.applicationNotes.isEmpty)
        XCTAssertTrue(state.profileSnippets.isEmpty)
        XCTAssertTrue(state.draftMetadata.isEmpty)
        XCTAssertEqual(state.tombstones, [
            AtlasHydratedTombstone(metadata: AtlasHydratedRecordMetadata(
                id: recordID(for: "saved_job"),
                revision: revision(for: "saved_job", deleted: true),
                parentRevision: nil,
                deleted: true,
                keyID: Self.keyID
            )),
        ])
    }

    func testWrongKeyThrowsNonSensitiveAuthenticationError() throws {
        let records = try encryptedRecords(recordTypes: Self.requiredRecordTypes)
        let wrongKey = Data(repeating: 9, count: AtlasVaultRecordCrypto.vaultKeyByteCount)
        let wrongSession = try AtlasVaultUnlockedSession(vaultID: Self.vaultID, vaultKey: wrongKey)

        XCTAssertThrowsError(try AtlasVaultRecordHydrator().hydrate(records: records, session: wrongSession)) { error in
            XCTAssertEqual(error as? AtlasVaultHydrationError, .authenticationFailed)
            assertErrorIsNonSensitive(error)
        }
    }

    func testMalformedPlaintextPayloadThrowsNonSensitiveError() throws {
        let record = try encryptedRecord(
            recordType: "saved_search",
            plaintext: Data(#"{"type":"saved_search","payload_schema":1,"payload":"TOP_SECRET_SENTINEL_DO_NOT_LEAK""#.utf8)
        )

        XCTAssertThrowsError(try AtlasVaultRecordHydrator().hydrate(records: [record], session: session())) { error in
            XCTAssertEqual(error as? AtlasVaultHydrationError, .malformedPayload)
            assertErrorIsNonSensitive(error)
        }
    }

    func testUnknownRecordTypeThrowsNonSensitiveError() throws {
        let record = try encryptedRecord(
            recordType: "saved_search",
            plaintext: Data("""
            {
              "type": "unknown_private_type",
              "payload_schema": 1,
              "payload": {
                "value": "TOP_SECRET_SENTINEL_DO_NOT_LEAK"
              },
              "client_created_at": "2026-01-01T00:00:00Z",
              "client_updated_at": "2026-01-01T00:00:00Z"
            }
            """.utf8)
        )

        XCTAssertThrowsError(try AtlasVaultRecordHydrator().hydrate(records: [record], session: session())) { error in
            XCTAssertEqual(error as? AtlasVaultHydrationError, .unknownRecordType)
            assertErrorIsNonSensitive(error)
        }
    }

    func testUnsupportedPayloadSchemaThrowsNonSensitiveError() throws {
        let record = try encryptedRecord(
            recordType: "saved_search",
            plaintext: Data("""
            {
              "type": "saved_search",
              "payload_schema": 2,
              "payload": {
                "name": "TOP_SECRET_SENTINEL_DO_NOT_LEAK"
              },
              "client_created_at": "2026-01-01T00:00:00Z",
              "client_updated_at": "2026-01-01T00:00:00Z"
            }
            """.utf8)
        )

        XCTAssertThrowsError(try AtlasVaultRecordHydrator().hydrate(records: [record], session: session())) { error in
            XCTAssertEqual(error as? AtlasVaultHydrationError, .unsupportedPayloadSchema)
            assertErrorIsNonSensitive(error)
        }
    }

    func testUnsupportedEncryptedRecordVersionThrowsNonSensitiveError() throws {
        let record = try encryptedRecord(recordType: "saved_search")
        let unsupported = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion + 1,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: record.nonce,
            ciphertext: record.ciphertext
        )

        XCTAssertThrowsError(try AtlasVaultRecordHydrator().hydrate(records: [unsupported], session: session())) { error in
            XCTAssertEqual(error as? AtlasVaultHydrationError, .unsupportedRecordVersion)
            assertErrorIsNonSensitive(error)
        }
    }

    func testCorruptRecordThrowsNonSensitiveError() throws {
        let record = try encryptedRecord(recordType: "saved_search")
        let corrupt = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: "not base64",
            ciphertext: record.ciphertext
        )

        XCTAssertThrowsError(try AtlasVaultRecordHydrator().hydrate(records: [corrupt], session: session())) { error in
            XCTAssertEqual(error as? AtlasVaultHydrationError, .corruptRecord)
            assertErrorIsNonSensitive(error)
        }
    }

    func testNoPublicSnapshotMutationOccurs() throws {
        let snapshot = try publicSnapshot()
        let before = try AtlasLocalCache.encodedSnapshotData(snapshot)

        _ = try hydratedState(recordTypes: Self.requiredRecordTypes)

        let after = try AtlasLocalCache.encodedSnapshotData(snapshot)
        XCTAssertEqual(before, after)
    }

    func testErrorStringAndDebugOutputDoNotContainPrivateSentinels() throws {
        for error in [
            AtlasVaultHydrationError.authenticationFailed,
            .malformedPayload,
            .unsupportedPayloadSchema,
            .unknownRecordType,
            .unsupportedRecordVersion,
            .invalidSession,
            .corruptRecord,
        ] {
            assertErrorIsNonSensitive(error)
        }
    }

    func testHydratorSourceAvoidsRuntimePublicCacheKeychainFileIOAndNetworking() throws {
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
    private static let keyID = "test-only-phase2d18"
    private static let vaultKey = Data((0..<AtlasVaultRecordCrypto.vaultKeyByteCount).map { UInt8($0) })

    private func hydratedState(recordTypes: [String]) throws -> AtlasVaultHydratedState {
        try AtlasVaultRecordHydrator().hydrate(records: encryptedRecords(recordTypes: recordTypes), session: session())
    }

    private func encryptedRecords(recordTypes: [String]) throws -> [AtlasVaultEncryptedRecordEnvelope] {
        try recordTypes.map { try encryptedRecord(recordType: $0) }
    }

    private func encryptedRecord(
        recordType: String,
        deleted: Bool = false,
        plaintext: Data? = nil
    ) throws -> AtlasVaultEncryptedRecordEnvelope {
        let plaintext = try plaintext ?? plaintextVectorData(recordType: recordType)
        let template = AtlasVaultEncryptedRecordEnvelope(
            id: recordID(for: recordType),
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion,
            revision: revision(for: recordType, deleted: deleted),
            parentRevision: nil,
            deleted: deleted,
            keyID: Self.keyID,
            nonce: nonce(for: recordType, deleted: deleted),
            ciphertext: ""
        )
        return try AtlasVaultRecordCrypto.seal(
            plaintext: plaintext,
            vaultKey: Self.vaultKey,
            vaultID: Self.vaultID,
            record: template
        )
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

    private func privateHydratedText(_ state: AtlasVaultHydratedState) -> String {
        [
            state.savedSearches.map {
                [
                    $0.payload.name,
                    $0.payload.summary,
                    $0.payload.request.text ?? "",
                    $0.payload.request.sourceIDs.joined(separator: " "),
                    $0.payload.request.organizations.joined(separator: " "),
                ].joined(separator: " ")
            }.joined(separator: " "),
            state.savedJobs.map {
                [$0.payload.jobKey, $0.payload.status, $0.payload.notes ?? ""].joined(separator: " ")
            }.joined(separator: " "),
            state.applicationNotes.map {
                [$0.payload.title ?? "", $0.payload.body, $0.payload.linkedJobKey ?? ""].joined(separator: " ")
            }.joined(separator: " "),
            state.profileSnippets.map {
                [$0.payload.body, $0.payload.provenanceNotes ?? ""].joined(separator: " ")
            }.joined(separator: " "),
            state.draftMetadata.map {
                [
                    $0.payload.linkedJobKey ?? "",
                    $0.payload.generatedDocumentReference,
                    $0.payload.personalContextReference ?? "",
                    $0.payload.contextSummary ?? "",
                ].joined(separator: " ")
            }.joined(separator: " "),
        ].joined(separator: " ")
    }

    private func assertErrorIsNonSensitive(
        _ error: Error,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let rendered = "\(String(describing: error)) \(String(reflecting: error))"
        for forbidden in [
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
        try JSONDecoder().decode(AtlasPublicLocalSnapshot.self, from: Data("""
        {
          "savedAt": 1767744000,
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

    private func recordID(for recordType: String) -> String {
        "00000000-0000-4000-8000-\(suffix(for: recordType))"
    }

    private func revision(for recordType: String, deleted: Bool = false) -> String {
        let revisionPrefix = deleted ? "9001" : "9000"
        return "00000000-0000-4000-\(revisionPrefix)-\(suffix(for: recordType))"
    }

    private func nonce(for recordType: String, deleted: Bool) -> String {
        let value = UInt8(recordTypeIndex(recordType) + (deleted ? 31 : 17))
        return Data(repeating: value, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString()
    }

    private func suffix(for recordType: String) -> String {
        String(format: "0000000003%02d", recordTypeIndex(recordType))
    }

    private func recordTypeIndex(_ recordType: String) -> Int {
        guard let index = Self.requiredRecordTypes.firstIndex(of: recordType) else {
            return 99
        }
        return index + 1
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
            currentDirectory.appendingPathComponent("Sources/AtlasUI/AtlasVaultRecordHydrator.swift"),
            currentDirectory.appendingPathComponent("apps/apple/Sources/AtlasUI/AtlasVaultRecordHydrator.swift"),
            URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .appendingPathComponent("../../Sources/AtlasUI/AtlasVaultRecordHydrator.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find AtlasVaultRecordHydrator.swift")
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

    private func testError(_ message: String) -> NSError {
        NSError(
            domain: "AtlasVaultRecordHydratorTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}
