import Foundation
import XCTest
@testable import AtlasUI

final class AtlasEndpointRecoveryTests: XCTestCase {
    private let primary = URL(string: "https://preferred.test")!
    private let fallback = URL(string: "https://fallback.test")!

    private func health() -> AtlasHealthSummary {
        AtlasHealthSummary(status: "ok", dbPath: nil, schemaVersion: nil,
                           openJobs: 0, enabledSources: 0, lastSyncAt: nil)
    }

    private func snapshot(_ client: AtlasAPIClient, _ health: AtlasHealthSummary) -> AtlasLocalSnapshot {
        AtlasLocalSnapshot(savedAt: .now, baseURL: client.baseURL, health: health,
                           searchResponse: AtlasSearchResponse(total: 0, limit: 0, offset: 0, results: [],
                                                               facets: [:], facetLabels: [:], unclassifiedCount: 0),
                           sources: [], recentRuns: [])
    }

    @MainActor
    func testURLSessionSecurityAndCancellationFailuresNeverTryRecovery() async {
        let codes: [URLError.Code] = [.secureConnectionFailed, .serverCertificateHasBadDate,
                                     .serverCertificateUntrusted, .serverCertificateHasUnknownRoot,
                                     .serverCertificateNotYetValid, .clientCertificateRejected,
                                     .clientCertificateRequired, .appTransportSecurityRequiresSecureConnection,
                                     .cancelled]
        for code in codes {
            var attempts: [URL] = []
            do {
                _ = try await AtlasAPIClient.healthWithRecovery(baseURL: primary, candidates: [fallback]) {
                    attempts.append($0)
                    throw AtlasAPIClient.transportFailure(URLError(code), url: $0)
                }
                XCTFail("Expected terminal URLSession failure")
            } catch {
                XCTAssertEqual((error as NSError).domain, NSURLErrorDomain)
                XCTAssertEqual((error as NSError).code, code.rawValue)
            }
            XCTAssertEqual(attempts, [primary])
        }
    }

    @MainActor
    func testNestedCertificateErrorDoesNotBecomeRetryableConnectionFailure() async {
        let error = NSError(domain: NSURLErrorDomain, code: URLError.cannotConnectToHost.rawValue,
                            userInfo: [NSUnderlyingErrorKey: URLError(.serverCertificateUntrusted)])
        var attempts = 0
        do {
            _ = try await AtlasAPIClient.healthWithRecovery(baseURL: primary, candidates: [fallback]) { endpoint in
                attempts += 1
                throw AtlasAPIClient.transportFailure(error, url: endpoint)
            }
            XCTFail("Expected terminal certificate failure")
        } catch { XCTAssertEqual((error as NSError).code, URLError.cannotConnectToHost.rawValue) }
        XCTAssertEqual(attempts, 1)
    }

    @MainActor
    func testHTTPSRecoverySkipsCleartextAndUnsupportedCandidates() async throws {
        let cleartext = URL(string: "http://lan.test:8765")!
        var attempts: [URL] = []
        let result = try await AtlasAPIClient.healthWithRecovery(
            baseURL: primary, candidates: [cleartext, URL(string: "ftp://other.test")!, fallback]
        ) { endpoint in
            attempts.append(endpoint)
            if endpoint == self.primary { throw AtlasAPIError.httpStatus(503, "restart") }
            return self.health()
        }
        XCTAssertEqual(result.0, fallback)
        XCTAssertEqual(attempts, [primary, fallback])
    }

    @MainActor
    func testForbiddenDowngradeKeepsOriginalFailureAtExhaustion() async {
        var attempts: [URL] = []
        do {
            _ = try await AtlasAPIClient.healthWithRecovery(
                baseURL: primary, candidates: [URL(string: "http://lan.test")!]
            ) { endpoint in
                attempts.append(endpoint)
                throw AtlasAPIError.httpStatus(502, "original")
            }
            XCTFail("Expected original failure")
        } catch { XCTAssertEqual(error as? AtlasAPIError, .httpStatus(502, "original")) }
        XCTAssertEqual(attempts, [primary])
    }

    @MainActor
    func testSupportedNetworkFailureStillAllowsHTTPToHTTPRecovery() async throws {
        let preferred = URL(string: "http://preferred.test")!
        let alternate = URL(string: "http://alternate.test")!
        let result = try await AtlasAPIClient.healthWithRecovery(baseURL: preferred, candidates: [alternate]) {
            if $0 == preferred { throw AtlasAPIClient.transportFailure(URLError(.timedOut), url: $0) }
            return self.health()
        }
        XCTAssertEqual(result.0, alternate)
    }

    @MainActor
    func testTemporarySnapshotAndDetailRecoveryPreservePreferredEndpointAcrossRefreshes() async throws {
        let suite = "AtlasEndpointRecoveryTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(primary.absoluteString, forKey: AtlasAPIClient.baseURLDefaultsKey)
        let model = AtlasSearchViewModel(client: AtlasAPIClient(baseURL: primary),
                                         usesPreviewData: true, endpointDefaults: defaults)
        var attempts: [URL] = []
        var snapshots: [URL] = []
        var detailClients: [URL] = []
        for _ in 0..<2 {
            let succeeded = await model.refresh(
                candidates: [fallback],
                fetchHealth: { endpoint in
                    attempts.append(endpoint)
                    if endpoint == self.primary { throw AtlasAPIError.httpStatus(503, "restart") }
                    return self.health()
                },
                fetchSnapshot: { client, health in
                    XCTAssertEqual(client.baseURL, self.fallback)
                    return self.snapshot(client, health)
                },
                commitSnapshot: { snapshots.append($0.baseURL) },
                warmDetails: { snapshot, client in
                    XCTAssertEqual(snapshot.baseURL, client.baseURL)
                    detailClients.append(client.baseURL)
                }
            )
            XCTAssertTrue(succeeded)
            XCTAssertEqual(model.apiBaseURL, primary)
            XCTAssertEqual(defaults.string(forKey: AtlasAPIClient.baseURLDefaultsKey), primary.absoluteString)
        }
        XCTAssertEqual(attempts, [primary, fallback, primary, fallback])
        XCTAssertEqual(snapshots, [fallback, fallback])
        XCTAssertEqual(detailClients, [fallback, fallback])
    }

    @MainActor
    func testFailedSnapshotCommitCannotAdoptOrPersistFallback() async throws {
        let suite = "AtlasEndpointRecoveryTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(primary.absoluteString, forKey: AtlasAPIClient.baseURLDefaultsKey)
        let model = AtlasSearchViewModel(client: AtlasAPIClient(baseURL: primary),
                                         usesPreviewData: true, endpointDefaults: defaults)
        var detailCalls = 0
        let succeeded = await model.refresh(
            candidates: [fallback],
            fetchHealth: { endpoint in
                if endpoint == self.primary { throw AtlasAPIError.transport("unreachable") }
                return self.health()
            },
            fetchSnapshot: { self.snapshot($0, $1) },
            commitSnapshot: { _ in throw CocoaError(.fileWriteOutOfSpace) },
            warmDetails: { _, _ in detailCalls += 1 }
        )
        XCTAssertFalse(succeeded)
        XCTAssertEqual(detailCalls, 0)
        XCTAssertEqual(model.apiBaseURL, primary)
        XCTAssertEqual(defaults.string(forKey: AtlasAPIClient.baseURLDefaultsKey), primary.absoluteString)
    }

    private func snapshot(baseURL: URL, jobKeys: [String]) -> AtlasLocalSnapshot {
        let rows = jobKeys.map { key in
            JobSearchResult(jobKey: key, title: "Role", organization: "UN",
                            sourceID: "fixture", dutyStation: "Nairobi", gradeCode: "P3",
                            contractLabel: "Staff", workModality: "Onsite", closingDate: nil,
                            needsReview: false, locationConfidence: nil, gradeConfidence: nil,
                            score: nil, scoreReasons: [], matchSummary: "", description: "")
        }
        return AtlasLocalSnapshot(
            savedAt: .now, baseURL: baseURL, health: health(),
            searchResponse: AtlasSearchResponse(total: rows.count, limit: rows.count, offset: 0,
                                                results: rows, facets: [:], facetLabels: [:],
                                                unclassifiedCount: 0),
            sources: [], recentRuns: []
        )
    }

    @MainActor
    func testStartupResumesMissingDetailsFromPersistedSnapshotWithoutChangingPreferredEndpoint() async throws {
        for snapshotEndpoint in [fallback, primary] {
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("AtlasRestartWarmup-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            defer { try? FileManager.default.removeItem(at: directory) }
            let suite = "AtlasRestartWarmup-\(UUID().uuidString)"
            let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
            defer { defaults.removePersistentDomain(forName: suite) }
            defaults.set(primary.absoluteString, forKey: AtlasAPIClient.baseURLDefaultsKey)
            let snapshotURL = directory.appendingPathComponent("snapshot.json")
            let jobKeys = ["already-cached", "interrupted-detail"]
            try AtlasLocalCache.saveSnapshot(snapshot(baseURL: snapshotEndpoint, jobKeys: jobKeys), to: snapshotURL)
            try Data("existing fixture detail".utf8).write(to: directory.appendingPathComponent(jobKeys[0]))
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            let loadSnapshot = {
                try? decoder.decode(AtlasLocalSnapshot.self, from: Data(contentsOf: snapshotURL))
            }
            let missingDetails: ([String]) -> [String] = { keys in
                keys.filter { !FileManager.default.fileExists(atPath: directory.appendingPathComponent($0).path) }
            }
            let countDetails: ([String]) -> Int = { keys in keys.count - missingDetails(keys).count }
            let model = AtlasSearchViewModel(client: AtlasAPIClient(baseURL: primary),
                                             usesPreviewData: true, endpointDefaults: defaults)
            let warmed = expectation(description: "Fresh snapshot resumes only interrupted details")
            var warmedEndpoints: [URL] = []
            await model.loadIfNeeded(
                loadSnapshot: loadSnapshot, cachedDetailCount: countDetails,
                missingDetailJobKeys: missingDetails,
                warmDetails: { missing, all, client in
                    XCTAssertEqual(missing, [jobKeys[1]])
                    XCTAssertEqual(all, jobKeys)
                    XCTAssertEqual(client.baseURL, snapshotEndpoint)
                    warmedEndpoints.append(client.baseURL)
                    try? Data("resumed fixture detail".utf8).write(to: directory.appendingPathComponent(jobKeys[1]))
                    warmed.fulfill()
                }
            )
            await fulfillment(of: [warmed], timeout: 2)
            XCTAssertEqual(warmedEndpoints, [snapshotEndpoint])
            XCTAssertEqual(model.results.map(\.jobKey), jobKeys)
            XCTAssertEqual(model.apiBaseURL, primary)
            XCTAssertEqual(defaults.string(forKey: AtlasAPIClient.baseURLDefaultsKey), primary.absoluteString)
            XCTAssertTrue(missingDetails(jobKeys).isEmpty)

            // A later launch sees the completed details and schedules no repeat work.
            let restarted = AtlasSearchViewModel(client: AtlasAPIClient(baseURL: primary),
                                                 usesPreviewData: true, endpointDefaults: defaults)
            await restarted.loadIfNeeded(
                loadSnapshot: loadSnapshot, cachedDetailCount: countDetails,
                missingDetailJobKeys: missingDetails,
                warmDetails: { _, _, _ in XCTFail("Completed details must not be fetched again") }
            )
            XCTAssertEqual(restarted.cachedDetailCount, jobKeys.count)
            XCTAssertEqual(restarted.apiBaseURL, primary)
        }
    }

    @MainActor
    func testStartupWarmupCannotDowngradeHTTPSOrUseUnsupportedCachedEndpoint() async throws {
        for endpoint in [URL(string: "http://cached.test")!, URL(string: "ftp://cached.test")!] {
            let suite = "AtlasRestartWarmup-\(UUID().uuidString)"
            let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
            defer { defaults.removePersistentDomain(forName: suite) }
            defaults.set(primary.absoluteString, forKey: AtlasAPIClient.baseURLDefaultsKey)
            let model = AtlasSearchViewModel(client: AtlasAPIClient(baseURL: primary),
                                             usesPreviewData: true, endpointDefaults: defaults)
            var selectedMissingDetails = false
            await model.loadIfNeeded(
                loadSnapshot: { self.snapshot(baseURL: endpoint, jobKeys: ["missing-detail"]) },
                cachedDetailCount: { _ in 0 },
                missingDetailJobKeys: { keys in selectedMissingDetails = true; return keys },
                warmDetails: { _, _, _ in XCTFail("Forbidden endpoint must not receive detail work") }
            )
            XCTAssertFalse(selectedMissingDetails)
            XCTAssertEqual(model.cachedDetailCount, 0)
            XCTAssertEqual(model.detailCacheTotal, 1)
            XCTAssertEqual(model.apiBaseURL, primary)
            XCTAssertEqual(defaults.string(forKey: AtlasAPIClient.baseURLDefaultsKey), primary.absoluteString)
        }
    }

}
