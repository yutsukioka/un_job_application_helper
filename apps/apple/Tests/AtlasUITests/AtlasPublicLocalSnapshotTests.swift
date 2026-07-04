import XCTest
@testable import AtlasUI

final class AtlasPublicLocalSnapshotTests: XCTestCase {
    func testPublicSnapshotJSONExcludesPrivateLegacyFieldsAndSentinels() throws {
        let snapshot = try decodedLegacySnapshotWithPrivateState()
        let json = try encodedSnapshotString(snapshot)

        for forbidden in [
            "savedSearches",
            "savedJobs",
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
            XCTAssertFalse(json.contains(forbidden), forbidden)
        }
    }

    func testPublicSnapshotJSONExcludesVaultRecordTypeStrings() throws {
        let snapshot = try decodedLegacySnapshotWithPrivateState()
        let json = try encodedSnapshotString(snapshot)

        for forbidden in [
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata",
        ] {
            XCTAssertFalse(json.contains(forbidden), forbidden)
        }
    }

    func testLegacySnapshotDecodesWithoutHydratingPrivateFields() throws {
        let snapshot = try decodedLegacySnapshotWithPrivateState()
        let mirrorLabels = Set(Mirror(reflecting: snapshot).children.compactMap(\.label))

        XCTAssertFalse(mirrorLabels.contains("savedSearches"))
        XCTAssertFalse(mirrorLabels.contains("savedJobs"))
        XCTAssertEqual(snapshot.searchResponse.results.map(\.jobKey), [Fixtures.publicJobKey])
        XCTAssertEqual(snapshot.jobCount, 1)
    }

    func testPublicSearchResultAndCacheFieldsRoundTrip() throws {
        let snapshot = try decodedLegacySnapshotWithPrivateState()
        let decoded = try decoder.decode(AtlasPublicLocalSnapshot.self, from: AtlasLocalCache.encodedSnapshotData(snapshot))

        XCTAssertEqual(decoded.savedAt, snapshot.savedAt)
        XCTAssertEqual(decoded.baseURL, snapshot.baseURL)
        XCTAssertEqual(decoded.health.status, "ok")
        XCTAssertEqual(decoded.health.openJobs, 1)
        XCTAssertEqual(decoded.searchResponse.total, 1)
        XCTAssertEqual(decoded.searchResponse.results.first?.jobKey, Fixtures.publicJobKey)
        XCTAssertEqual(decoded.searchResponse.facets["organizations"]?["UNICEF"], 1)
        XCTAssertEqual(decoded.sources.first?.sourceID, "public_source")
        XCTAssertEqual(decoded.recentRuns.first?.sourceID, "public_source")
    }

    func testPublicDetailCacheMetadataDoesNotRevealSavedOnlyMembership() throws {
        let snapshot = try decodedLegacySnapshotWithPrivateState()
        let json = try encodedSnapshotString(snapshot)

        XCTAssertEqual(snapshot.jobCount, 1)
        XCTAssertEqual(snapshot.searchResponse.results.map(\.jobKey), [Fixtures.publicJobKey])
        XCTAssertFalse(json.contains(Fixtures.savedOnlyJobKey))
        XCTAssertFalse(json.contains("FAKE_SAVED_JOB_STATUS_DO_NOT_LEAK"))
        XCTAssertFalse(json.contains("FAKE_PRIVATE_NOTE_DO_NOT_LEAK"))
    }

    func testAtlasLocalCacheCanWritePublicSnapshotWithoutPrivateSentinels() throws {
        let snapshot = try decodedLegacySnapshotWithPrivateState()
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension("json")

        try AtlasLocalCache.saveSnapshot(snapshot, to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let json = try String(contentsOf: url, encoding: .utf8)
        XCTAssertFalse(json.contains("savedSearches"))
        XCTAssertFalse(json.contains("savedJobs"))
        XCTAssertFalse(json.contains(Fixtures.savedOnlyJobKey))
        XCTAssertFalse(json.contains("TOP_SECRET_SENTINEL_DO_NOT_LEAK"))
    }

    private func decodedLegacySnapshotWithPrivateState() throws -> AtlasPublicLocalSnapshot {
        try decoder.decode(AtlasPublicLocalSnapshot.self, from: Data("""
        {
          "savedAt": "\(Fixtures.savedAt)",
          "baseURL": "http://127.0.0.1:8765",
          "health": {
            "status": "ok",
            "db_path": null,
            "schema_version": "test",
            "open_jobs": 1,
            "enabled_sources": 1,
            "last_sync_at": "2026-01-01T00:00:00Z"
          },
          "searchResponse": {
            "total": 1,
            "limit": 50,
            "offset": 0,
            "facets": {
              "organizations": {
                "UNICEF": 1
              }
            },
            "facet_labels": {
              "organizations": {
                "UNICEF": "UNICEF"
              }
            },
            "unclassified_count": 0,
            "results": [
              {
                "jobKey": "\(Fixtures.publicJobKey)",
                "title": "Public programme role",
                "organization": "UNICEF",
                "sourceID": "public_source",
                "dutyStation": "Public City",
                "gradeCode": "P-3",
                "contractLabel": "Fixed Term",
                "workModality": "Onsite",
                "closingDate": "2026-02-01T00:00:00Z",
                "needsReview": false,
                "scoreReasons": [],
                "matchSummary": "Public result",
                "description": "Public vacancy description",
                "status": "open"
              }
            ]
          },
          "savedSearches": [
            {
              "name": "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
              "description": "TOP_SECRET_SENTINEL_DO_NOT_LEAK",
              "request": {
                "text": "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
                "source_ids": ["FAKE_PRIVATE_FILTER_DO_NOT_LEAK"],
                "status": ["open"]
              },
              "created_at": "2026-01-01T00:00:00Z",
              "updated_at": "2026-01-02T00:00:00Z"
            }
          ],
          "savedJobs": [
            {
              "id": "legacy-private-record",
              "job_key": "\(Fixtures.savedOnlyJobKey)",
              "status": "FAKE_SAVED_JOB_STATUS_DO_NOT_LEAK",
              "notes": "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
              "applied_at": null,
              "updated_at": "2026-01-02T00:00:00Z"
            }
          ],
          "profile_snippet": "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
          "draft_metadata": {
            "type": "draft_metadata",
            "generated_document_reference": "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK"
          },
          "vault_payload_types": [
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata"
          ],
          "sources": [
            {
              "source_id": "public_source",
              "organization": "UNICEF",
              "total_jobs": 1,
              "open_jobs": 1,
              "last_seen_at": "2026-01-01T00:00:00Z",
              "health_status": "ok",
              "observed_at": "2026-01-01T00:00:00Z",
              "detail_attempted": 1,
              "detail_failed": 0,
              "missing_transition_allowed": false
            }
          ],
          "recentRuns": [
            {
              "source_id": "public_source",
              "fetched": 1,
              "inserted": 1,
              "updated": 0,
              "missing": 0,
              "closed": 0,
              "observed_at": "2026-01-01T00:01:00Z"
            }
          ]
        }
        """.utf8))
    }

    private func encodedSnapshotString(_ snapshot: AtlasPublicLocalSnapshot) throws -> String {
        let data = try AtlasLocalCache.encodedSnapshotData(snapshot)
        return try XCTUnwrap(String(data: data, encoding: .utf8))
    }

    private var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

private enum Fixtures {
    static let savedAt = "2026-01-01T00:00:00Z"
    static let publicJobKey = "PUBLIC_JOB_KEY_OK_TO_CACHE"
    static let savedOnlyJobKey = "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK"
}
