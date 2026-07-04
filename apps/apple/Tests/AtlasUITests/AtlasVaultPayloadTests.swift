import XCTest
@testable import AtlasUI

final class AtlasVaultPayloadTests: XCTestCase {
    func testSavedSearchVaultPayloadEnvelopeEncodesAndDecodes() throws {
        let envelope = Fixtures.savedSearchEnvelope()

        let decoded: AtlasSavedSearchVaultRecordPayload = try roundTrip(envelope)

        XCTAssertEqual(decoded, envelope)
        XCTAssertEqual(decoded.type, .savedSearch)
        XCTAssertEqual(decoded.payloadSchema, 1)
    }

    func testSavedJobVaultPayloadEnvelopeEncodesAndDecodes() throws {
        let envelope = Fixtures.savedJobEnvelope()

        let decoded: AtlasSavedJobVaultRecordPayload = try roundTrip(envelope)

        XCTAssertEqual(decoded, envelope)
        XCTAssertEqual(decoded.type, .savedJob)
        XCTAssertEqual(decoded.payloadSchema, 1)
    }

    func testApplicationNoteVaultPayloadEnvelopeEncodesAndDecodes() throws {
        let envelope = Fixtures.applicationNoteEnvelope()

        let decoded: AtlasApplicationNoteVaultRecordPayload = try roundTrip(envelope)

        XCTAssertEqual(decoded, envelope)
        XCTAssertEqual(decoded.type, .applicationNote)
        XCTAssertEqual(decoded.payloadSchema, 1)
    }

    func testProfileSnippetVaultPayloadEnvelopeEncodesAndDecodes() throws {
        let envelope = Fixtures.profileSnippetEnvelope()

        let decoded: AtlasProfileSnippetVaultRecordPayload = try roundTrip(envelope)

        XCTAssertEqual(decoded, envelope)
        XCTAssertEqual(decoded.type, .profileSnippet)
        XCTAssertEqual(decoded.payloadSchema, 1)
    }

    func testDraftMetadataVaultPayloadEnvelopeEncodesAndDecodes() throws {
        let envelope = Fixtures.draftMetadataEnvelope()

        let decoded: AtlasDraftMetadataVaultRecordPayload = try roundTrip(envelope)

        XCTAssertEqual(decoded, envelope)
        XCTAssertEqual(decoded.type, .draftMetadata)
        XCTAssertEqual(decoded.payloadSchema, 1)
    }

    func testPayloadEnvelopeUsesExpectedRecordTypeStrings() throws {
        let encoded = try encodedPayloadJSON()

        for recordType in [
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata",
        ] {
            XCTAssertTrue(encoded.contains(#""type":"\#(recordType)""#), recordType)
        }
    }

    func testPayloadJSONKeyNamesMatchPhase2Design() throws {
        let encoded = try encodedPayloadJSON()

        for key in [
            "payload_schema",
            "client_created_at",
            "client_updated_at",
            "source_ids",
            "countries_iso3",
            "grade_codes",
            "include_low_confidence",
            "created_at",
            "updated_at",
            "job_key",
            "applied_at",
            "note_kind",
            "linked_job_key",
            "linked_saved_job_record_id",
            "is_pinned",
            "sort_order",
            "target_system",
            "field_hint",
            "provenance_notes",
            "document_type",
            "generated_document_reference",
            "draft_status",
            "generated_at",
            "reviewed_at",
            "submitted_at",
            "archived_at",
            "personal_context_reference",
            "context_summary",
        ] {
            XCTAssertTrue(encoded.contains(#""\#(key)""#), key)
        }
    }

    func testPreEncryptionPayloadJSONContainsFakePrivateSentinels() throws {
        // This plaintext JSON is the in-memory payload before encryption only.
        // It must not be written to AtlasLocalSnapshot or other public caches.
        // Later encrypted store tests must assert these strings are absent from
        // serialized vault files and .atlasvault exports.
        let encoded = try encodedPayloadJSON()

        for privateValue in [
            Fixtures.topSecretSentinel,
            Fixtures.fakePrivateSearchText,
            Fixtures.fakePrivateFilter,
            Fixtures.fakeJobKey,
            Fixtures.fakePrivateNote,
            Fixtures.fakeProfileSnippet,
            Fixtures.fakeGeneratedDocumentReference,
        ] {
            XCTAssertTrue(encoded.contains(privateValue), privateValue)
        }
    }

    func testSavedSearchMappingPreservesFields() throws {
        let savedSearch = try decodedSavedSearchFixture()

        let payload = AtlasSavedSearchVaultPayload(savedSearch: savedSearch)

        XCTAssertEqual(payload.name, "FAKE saved search TOP_SECRET_SENTINEL_DO_NOT_LEAK")
        XCTAssertEqual(payload.summary, "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK summary")
        XCTAssertEqual(payload.description, "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK summary")
        XCTAssertEqual(payload.request.text, Fixtures.fakePrivateSearchText)
        XCTAssertEqual(payload.request.sourceIDs, [Fixtures.fakePrivateFilter])
        XCTAssertEqual(payload.createdAt, Fixtures.createdAt)
        XCTAssertEqual(payload.updatedAt, Fixtures.updatedAt)
    }

    func testSavedJobMappingPreservesFields() throws {
        let record = try decodedApplicationRecordFixture()

        let payload = AtlasSavedJobVaultPayload(applicationRecord: record)

        XCTAssertEqual(payload.id, "legacy-tracker-id")
        XCTAssertEqual(payload.jobKey, Fixtures.fakeJobKey)
        XCTAssertEqual(payload.status, "FAKE_STATUS_DO_NOT_LEAK")
        XCTAssertEqual(payload.notes, Fixtures.fakePrivateNote)
        XCTAssertEqual(payload.appliedAt, "2026-01-03T00:00:00Z")
        XCTAssertEqual(payload.updatedAt, Fixtures.updatedAt)
    }

    func testAtlasLocalSnapshotDoesNotReferenceVaultPayloadTypes() throws {
        let snapshot = try decodedMinimalSnapshot()

        let childTypeNames = Mirror(reflecting: snapshot).children.map {
            String(reflecting: Swift.type(of: $0.value))
        }

        XCTAssertFalse(childTypeNames.contains { $0.contains("AtlasVault") })
        XCTAssertFalse(childTypeNames.contains { $0.contains("VaultPayload") })
    }

    private func roundTrip<T>(_ value: T) throws -> T where T: Codable & Equatable {
        let data = try encoder.encode(value)
        return try decoder.decode(T.self, from: data)
    }

    private func encodedPayloadJSON() throws -> String {
        try [
            encodedString(Fixtures.savedSearchEnvelope()),
            encodedString(Fixtures.savedJobEnvelope()),
            encodedString(Fixtures.applicationNoteEnvelope()),
            encodedString(Fixtures.profileSnippetEnvelope()),
            encodedString(Fixtures.draftMetadataEnvelope()),
        ].joined(separator: "\n")
    }

    private func encodedString<T: Encodable>(_ value: T) throws -> String {
        let data = try encoder.encode(value)
        return try XCTUnwrap(String(data: data, encoding: .utf8))
    }

    private func decodedSavedSearchFixture() throws -> AtlasSavedSearch {
        try decoder.decode(AtlasSavedSearch.self, from: Data("""
        {
          "name": "FAKE saved search TOP_SECRET_SENTINEL_DO_NOT_LEAK",
          "description": "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK summary",
          "request": {
            "text": "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
            "source_ids": ["FAKE_PRIVATE_FILTER_DO_NOT_LEAK"],
            "status": ["open"]
          },
          "created_at": "\(Fixtures.createdAt)",
          "updated_at": "\(Fixtures.updatedAt)"
        }
        """.utf8))
    }

    private func decodedApplicationRecordFixture() throws -> AtlasApplicationRecord {
        try decoder.decode(AtlasApplicationRecord.self, from: Data("""
        {
          "id": "legacy-tracker-id",
          "job_key": "\(Fixtures.fakeJobKey)",
          "status": "FAKE_STATUS_DO_NOT_LEAK",
          "notes": "\(Fixtures.fakePrivateNote)",
          "applied_at": "2026-01-03T00:00:00Z",
          "updated_at": "\(Fixtures.updatedAt)"
        }
        """.utf8))
    }

    private func decodedMinimalSnapshot() throws -> AtlasLocalSnapshot {
        try decoder.decode(AtlasLocalSnapshot.self, from: Data("""
        {
          "savedAt": "\(Fixtures.updatedAt)",
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
          "savedSearches": [],
          "savedJobs": [],
          "sources": [],
          "recentRuns": []
        }
        """.utf8))
    }

    private var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }

    private var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

private enum Fixtures {
    static let topSecretSentinel = "TOP_SECRET_SENTINEL_DO_NOT_LEAK"
    static let fakePrivateSearchText = "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK"
    static let fakePrivateFilter = "FAKE_PRIVATE_FILTER_DO_NOT_LEAK"
    static let fakeJobKey = "FAKE_JOB_KEY_DO_NOT_LEAK"
    static let fakePrivateNote = "FAKE_PRIVATE_NOTE_DO_NOT_LEAK"
    static let fakeProfileSnippet = "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK"
    static let fakeGeneratedDocumentReference = "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK"
    static let createdAt = "2026-01-01T00:00:00Z"
    static let updatedAt = "2026-01-02T00:00:00Z"

    static func savedSearchEnvelope() -> AtlasSavedSearchVaultRecordPayload {
        .savedSearch(
            AtlasSavedSearchVaultPayload(
                name: "FAKE saved search \(topSecretSentinel)",
                summary: "\(fakePrivateSearchText) summary",
                description: "FAKE description \(topSecretSentinel)",
                request: AtlasSearchRequest(
                    text: fakePrivateSearchText,
                    status: ["open"],
                    organizations: ["FAKE_ORG_DO_NOT_LEAK"],
                    sourceIDs: [fakePrivateFilter],
                    countriesISO3: ["FAKE_COUNTRY_DO_NOT_LEAK"],
                    gradeCodes: ["FAKE_GRADE_DO_NOT_LEAK"],
                    includeLowConfidence: true
                ),
                createdAt: createdAt,
                updatedAt: updatedAt
            ),
            clientCreatedAt: createdAt,
            clientUpdatedAt: updatedAt
        )
    }

    static func savedJobEnvelope() -> AtlasSavedJobVaultRecordPayload {
        .savedJob(
            AtlasSavedJobVaultPayload(
                id: "legacy-tracker-id",
                jobKey: fakeJobKey,
                status: "FAKE_STATUS_DO_NOT_LEAK",
                notes: fakePrivateNote,
                appliedAt: "2026-01-03T00:00:00Z",
                updatedAt: updatedAt
            ),
            clientCreatedAt: createdAt,
            clientUpdatedAt: updatedAt
        )
    }

    static func applicationNoteEnvelope() -> AtlasApplicationNoteVaultRecordPayload {
        .applicationNote(
            AtlasApplicationNoteVaultPayload(
                title: "FAKE note \(topSecretSentinel)",
                body: fakePrivateNote,
                noteKind: "interview",
                linkedJobKey: fakeJobKey,
                linkedSavedJobRecordID: "legacy-tracker-id",
                createdAt: createdAt,
                updatedAt: updatedAt,
                isPinned: true,
                sortOrder: 7
            ),
            clientCreatedAt: createdAt,
            clientUpdatedAt: updatedAt
        )
    }

    static func profileSnippetEnvelope() -> AtlasProfileSnippetVaultRecordPayload {
        .profileSnippet(
            AtlasProfileSnippetVaultPayload(
                title: "FAKE profile \(topSecretSentinel)",
                body: fakeProfileSnippet,
                targetSystem: "INSPIRA",
                fieldHint: "summary",
                tags: ["FAKE_PRIVATE_TAG_DO_NOT_LEAK"],
                provenanceNotes: "FAKE_PROVENANCE_DO_NOT_LEAK",
                createdAt: createdAt,
                updatedAt: updatedAt
            ),
            clientCreatedAt: createdAt,
            clientUpdatedAt: updatedAt
        )
    }

    static func draftMetadataEnvelope() -> AtlasDraftMetadataVaultRecordPayload {
        .draftMetadata(
            AtlasDraftMetadataVaultPayload(
                linkedJobKey: fakeJobKey,
                linkedSavedJobRecordID: "legacy-tracker-id",
                targetSystem: "UNICEF",
                documentType: "cover_letter",
                generatedDocumentReference: fakeGeneratedDocumentReference,
                draftStatus: "reviewing",
                generatedAt: createdAt,
                reviewedAt: "2026-01-04T00:00:00Z",
                submittedAt: "2026-01-05T00:00:00Z",
                archivedAt: "2026-01-06T00:00:00Z",
                personalContextReference: "FAKE_CONTEXT_REF_DO_NOT_LEAK",
                contextSummary: "FAKE_CONTEXT_SUMMARY_DO_NOT_LEAK"
            ),
            clientCreatedAt: createdAt,
            clientUpdatedAt: updatedAt
        )
    }
}
