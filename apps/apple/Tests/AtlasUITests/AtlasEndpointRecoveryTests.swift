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
}
