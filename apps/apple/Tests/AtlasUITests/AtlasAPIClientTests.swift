import XCTest
@testable import AtlasUI

final class AtlasAPIClientTests: XCTestCase {
    func testNormalizesPastedHealthURLToBaseURL() {
        let url = AtlasAPIClient.normalizedBaseURL(
            from: " http://192.168.50.22:8765/api/health?probe=1#status "
        )

        XCTAssertEqual(url?.absoluteString, "http://192.168.50.22:8765")
    }

    func testAddsHTTPPrefixForBareLANHost() {
        let url = AtlasAPIClient.normalizedBaseURL(from: "192.168.50.22:8765")

        XCTAssertEqual(url?.absoluteString, "http://192.168.50.22:8765")
    }

    func testDecodesCachedSearchResponseRows() throws {
        let json = """
        {
          "total": 1,
          "limit": 10000,
          "offset": 0,
          "facets": {},
          "facet_labels": {},
          "unclassified_count": 0,
          "results": [
            {
              "jobKey": "undp_oracle_hcm:34063",
              "title": "Programme Analyst",
              "organization": "UNDP Oracle HCM",
              "sourceID": "undp_oracle_hcm",
              "dutyStation": "Nairobi, Kenya",
              "gradeCode": "IPSA-9",
              "contractLabel": "Consultant",
              "workModality": "Onsite",
              "closingDate": "2026-06-30T23:59:00Z",
              "needsReview": false,
              "scoreReasons": [],
              "matchSummary": "Cached row",
              "description": "Cached description",
              "status": "open"
            }
          ]
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let response = try decoder.decode(AtlasSearchResponse.self, from: json)

        XCTAssertEqual(response.total, 1)
        XCTAssertEqual(response.results.first?.jobKey, "undp_oracle_hcm:34063")
        XCTAssertEqual(response.results.first?.gradeCode, "IPSA-9")
    }
}
