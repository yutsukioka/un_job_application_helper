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

    func testDecodesAPIURLTrustAnnotations() throws {
        let json = """
        {
          "total": 1,
          "limit": 50,
          "offset": 0,
          "facets": {},
          "facet_labels": {},
          "unclassified_count": 0,
          "results": [
            {
              "job_key": "unicef_pageup:593420",
              "title": "Emergency Specialist, P-3",
              "organization": "UNICEF PageUp",
              "source_id": "unicef_pageup",
              "duty_station": "Nairobi, Kenya",
              "grade_code": "p3",
              "contract_group": "fixed_term",
              "work_modality": "onsite",
              "closing_date": "2026-07-05T23:59:00Z",
              "status": "open",
              "apply_url": "https://apply.vendor.example/jobs/593420",
              "source_url": "https://careers.unicef.org/jobs/593420",
              "apply_url_trust": {
                "origin_host": "apply.vendor.example",
                "matches_source_org": false
              },
              "source_url_trust": {
                "origin_host": "careers.unicef.org",
                "matches_source_org": true
              },
              "needs_review": false,
              "score_reasons": []
            }
          ]
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let response = try decoder.decode(AtlasSearchResponse.self, from: json)
        let job = try XCTUnwrap(response.results.first)

        XCTAssertEqual(job.applyURLTrust?.originHost, "apply.vendor.example")
        XCTAssertEqual(job.applyURLTrust?.matchesSourceOrg, false)
        XCTAssertEqual(job.sourceURLTrust?.originHost, "careers.unicef.org")
        XCTAssertEqual(job.sourceURLTrust?.matchesSourceOrg, true)
    }

    func testDecodesDetailURLTrustAnnotations() throws {
        let json = """
        {
          "job_key": "unicef_pageup:593420",
          "title": "Emergency Specialist, P-3",
          "status": "open",
          "apply_url": "https://apply.vendor.example/jobs/593420",
          "source_url": "https://careers.unicef.org/jobs/593420",
          "apply_url_trust": {
            "origin_host": "apply.vendor.example",
            "matches_source_org": false
          },
          "source_url_trust": {
            "origin_host": "careers.unicef.org",
            "matches_source_org": true
          },
          "display_sections": []
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let detail = try decoder.decode(AtlasJobDetail.self, from: json)

        XCTAssertEqual(detail.applyURLTrust?.originHost, "apply.vendor.example")
        XCTAssertEqual(detail.applyURLTrust?.matchesSourceOrg, false)
        XCTAssertEqual(detail.sourceURLTrust?.originHost, "careers.unicef.org")
        XCTAssertEqual(detail.sourceURLTrust?.matchesSourceOrg, true)
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
