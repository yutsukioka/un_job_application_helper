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

    func testSearchPagesLoadMoreAndOfflineSnapshotWithinServerBound() async throws {
        for requested in [400, 10_000] {
            var calls: [AtlasSearchRequest] = []
            let response = try await AtlasAPIClient.collectSearchPages(
                AtlasSearchRequest(text: "climate", limit: requested)
            ) { page in
                calls.append(page)
                let end = min(page.offset + page.limit, 450)
                let rows = (page.offset..<end).map { index in
                    JobSearchResult(jobKey: "job-\(index)", title: "Role", organization: "UN",
                                    sourceID: "source", dutyStation: "Nairobi", gradeCode: "P3",
                                    contractLabel: "Staff", workModality: "Onsite", closingDate: nil,
                                    needsReview: false, locationConfidence: nil, gradeConfidence: nil,
                                    score: nil, scoreReasons: [], matchSummary: "", description: "")
                }
                return AtlasSearchResponse(total: 450, limit: page.limit, offset: page.offset,
                                           results: rows, facets: page.includeFacets ? ["org": ["UN": 450]] : [:],
                                           facetLabels: [:], unclassifiedCount: 0)
            }
            XCTAssertEqual(response.results.count, min(requested, 450))
            XCTAssertEqual(Set(response.results.map(\.jobKey)).count, response.results.count)
            XCTAssertEqual(response.facets["org"]?["UN"], 450)
            XCTAssertEqual(calls.map(\.offset), requested == 400 ? [0, 200] : [0, 200, 400])
            XCTAssertTrue(calls.allSatisfy { $0.limit <= 200 && $0.text == "climate" })
            XCTAssertTrue(calls.dropFirst().allSatisfy { !$0.includeFacets })
        }
    }

    func testFacetOnlySearchUsesOneZeroLimitRequest() async throws {
        var calls = 0
        let response = try await AtlasAPIClient.collectSearchPages(AtlasSearchRequest(limit: 0)) { page in
            calls += 1
            XCTAssertEqual(page.limit, 0)
            return AtlasSearchResponse(total: 500, limit: 0, offset: 0, results: [],
                                       facets: ["org": ["UN": 500]], facetLabels: [:], unclassifiedCount: 0)
        }
        XCTAssertEqual(calls, 1)
        XCTAssertTrue(response.results.isEmpty)
        XCTAssertEqual(response.total, 500)
    }
}
