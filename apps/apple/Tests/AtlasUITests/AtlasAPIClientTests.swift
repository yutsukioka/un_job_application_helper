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

    func testTransportErrorMessageExplainsLocalNetworkPermission() throws {
        let error = NSError(domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet)
        let url = try XCTUnwrap(URL(string: "http://192.168.50.22:8765/api/health"))

        let message = transportErrorMessage(error, url: url)

        XCTAssertTrue(message.contains("iOS is blocking local network access"))
        XCTAssertTrue(message.contains("192.168.50.22"))
        XCTAssertTrue(message.contains("Enable Local Network"))
    }

    func testTransportErrorMessageExplainsUnreachableLANServer() throws {
        let error = NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotConnectToHost)
        let url = try XCTUnwrap(URL(string: "http://10.0.0.12:8765/api/search"))

        let message = transportErrorMessage(error, url: url)

        XCTAssertTrue(message.contains("Cannot reach http://10.0.0.12:8765/api/search"))
        XCTAssertTrue(message.contains("--host 0.0.0.0"))
        XCTAssertTrue(message.contains("current LAN IP"))
    }

    func testTransportErrorMessageKeepsRemoteErrorDescription() throws {
        let error = NSError(
            domain: NSURLErrorDomain,
            code: NSURLErrorTimedOut,
            userInfo: [NSLocalizedDescriptionKey: "The request timed out."]
        )
        let url = try XCTUnwrap(URL(string: "https://example.org/api/health"))

        let message = transportErrorMessage(error, url: url)

        XCTAssertEqual(message, "The request timed out.")
    }
}
