import Foundation
import Security
import XCTest
@testable import AtlasUI

final class AtlasVaultProductionAdaptersTests: XCTestCase {
    private static let publicJobID = "PUBLIC_JOB_001"
    private static let secondPublicJobID = "PUBLIC_JOB_002"
    private static let thirdPublicJobID = "PUBLIC_JOB_003"
    private static let vaultID = "vault_01_TEST_RANDOM"
    private static let transportSentinel =
        "FAKE_TRANSPORT_URL_AND_QUERY_DO_NOT_LEAK"
    private static let bodySentinel =
        "FAKE_HTTP_BODY_DO_NOT_LEAK"
    private static let pathSentinel =
        "FAKE_SNAPSHOT_PATH_DO_NOT_LEAK"
    private static let repositoryScanIgnoredDirectoryNames: Set<String> = [
        ".agents",
        ".build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".swiftpm",
        ".venv",
        "DerivedData",
        "__pycache__",
        "bak",
        "build",
        "dist",
        "inputs",
        "logs",
        "node_modules",
        "output",
        "private",
        "tmp",
        "xcuserdata",
    ]

    // MARK: - Public API adapter

    func testAPIAdapterConstructionInvokesNothingAndIsRedacted() async {
        let client = RecordingPublicJobClient()
        let adapter = AtlasAPIClientPublicJobAdapter(
            client: client,
            provenanceCapacity: 2
        )

        let counts = await client.counts()
        XCTAssertEqual(counts, .zero)
        XCTAssertEqual(
            String(describing: adapter),
            "AtlasAPIClientPublicJobAdapter(<redacted>)"
        )
        XCTAssertEqual(String(reflecting: adapter), String(describing: adapter))
    }

    func testHealthCallsOnlyHealthAndDropsDiagnostics() async throws {
        let client = RecordingPublicJobClient(
            health: makeHealth(
                status: "ok",
                dbPath: "/\(Self.pathSentinel)/jobs.sqlite3",
                schemaVersion: "FAKE_SCHEMA_DIAGNOSTIC",
                openJobs: 7,
                enabledSources: 3,
                lastSyncAt: "2026-07-19T01:02:03Z"
            )
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)

        let health = try await adapter.health()

        XCTAssertEqual(health.availability, .available)
        XCTAssertEqual(health.openJobCount, 7)
        XCTAssertEqual(health.enabledSourceCount, 3)
        XCTAssertEqual(
            health.lastSyncAt,
            try date("2026-07-19T01:02:03Z")
        )
        let counts = await client.counts()
        XCTAssertEqual(counts, .init(health: 1))
        let labels = Set(Mirror(reflecting: health).children.compactMap(\.label))
        XCTAssertFalse(labels.contains("dbPath"))
        XCTAssertFalse(labels.contains("schemaVersion"))
        XCTAssertFalse(String(reflecting: health).contains(Self.pathSentinel))
        XCTAssertFalse(String(reflecting: health).contains("FAKE_SCHEMA"))
    }

    func testHealthUnavailableAndUnknownStatusMapping() async throws {
        let unavailableClient = RecordingPublicJobClient(
            health: makeHealth(status: "unavailable")
        )
        let unavailable = AtlasAPIClientPublicJobAdapter(
            client: unavailableClient
        )

        let unavailableHealth = try await unavailable.health()
        XCTAssertEqual(unavailableHealth.availability, .unavailable)

        let missingClient = RecordingPublicJobClient(
            health: makeHealth(status: "  ")
        )
        let missing = AtlasAPIClientPublicJobAdapter(client: missingClient)
        let missingHealth = try await missing.health()
        XCTAssertEqual(missingHealth.availability, .unavailable)

        let missingDatabaseClient = RecordingPublicJobClient(
            health: makeHealth(status: "missing_db")
        )
        let missingDatabase = AtlasAPIClientPublicJobAdapter(
            client: missingDatabaseClient
        )
        let missingDatabaseHealth = try await missingDatabase.health()
        XCTAssertEqual(missingDatabaseHealth.availability, .unavailable)

        let unknownClient = RecordingPublicJobClient(
            health: makeHealth(status: "FAKE_UNKNOWN_STATUS")
        )
        let unknown = AtlasAPIClientPublicJobAdapter(client: unknownClient)

        await assertPublicError(.invalidResponse) {
            try await unknown.health()
        }
    }

    func testHealthAndDecodeErrorsAreFixedAndRedacted() async {
        let transportClient = RecordingPublicJobClient(
            healthFailure: .transport(Self.transportSentinel)
        )
        let transport = AtlasAPIClientPublicJobAdapter(client: transportClient)

        await assertPublicError(.unavailable) {
            try await transport.health()
        }

        let httpClient = RecordingPublicJobClient(
            healthFailure: .http(Self.bodySentinel)
        )
        let http = AtlasAPIClientPublicJobAdapter(client: httpClient)

        await assertPublicError(.unavailable) {
            try await http.health()
        }

        let decodeClient = RecordingPublicJobClient(
            healthFailure: .decoding(Self.bodySentinel)
        )
        let decode = AtlasAPIClientPublicJobAdapter(client: decodeClient)

        await assertPublicError(.invalidResponse) {
            try await decode.health()
        }
        for error in [
            AtlasPublicJobServiceError.unavailable,
            .invalidResponse,
            .invalidRequest,
        ] {
            XCTAssertFalse(String(reflecting: error).contains(Self.bodySentinel))
            XCTAssertFalse(
                String(reflecting: error).contains(Self.transportSentinel)
            )
        }
    }

    func testSearchMapsOnlyPublicRequestAndSafeProjection() async throws {
        let closingDate = try date("2026-08-01T12:34:56Z")
        let response = try makeSearchResponse(
            jobs: [
                makeJob(
                    id: Self.publicJobID,
                    title: "Public Programme Officer",
                    organization: "UNICEF PageUp",
                    location: "Tokyo, Japan",
                    closingDate: closingDate,
                    score: 0.99,
                    description: "FAKE_SCORE_REASON_PRIVATE_TO_ADAPTER"
                ),
            ],
            total: 6,
            limit: 25,
            offset: 5
        )
        let client = RecordingPublicJobClient(searchResponses: [response])
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let request = try AtlasPublicJobSearchRequest(
            query: "public programme",
            limit: 25,
            offset: 5
        )

        let result = try await adapter.search(request)

        XCTAssertEqual(result.total, 6)
        XCTAssertEqual(result.limit, 25)
        XCTAssertEqual(result.offset, 5)
        XCTAssertEqual(result.jobs.count, 1)
        XCTAssertEqual(result.jobs[0].id, Self.publicJobID)
        XCTAssertEqual(result.jobs[0].title, "Public Programme Officer")
        XCTAssertEqual(result.jobs[0].organization, "UNICEF")
        XCTAssertEqual(result.jobs[0].location, "Tokyo, Japan")
        XCTAssertEqual(
            result.jobs[0].closingDateText,
            "2026-08-01T12:34:56Z"
        )
        let lastRequest = await client.lastSearchRequest()
        let mapped = try XCTUnwrap(lastRequest)
        XCTAssertEqual(mapped.text, "public programme")
        XCTAssertEqual(mapped.limit, 25)
        XCTAssertEqual(mapped.offset, 5)
        XCTAssertEqual(mapped.status, ["open"])
        XCTAssertFalse(mapped.includeFacets)
        XCTAssertFalse(mapped.includeLowConfidence)
        XCTAssertEqual(mapped.sort, "closing_date_asc")
        XCTAssertTrue(mapped.organizations.isEmpty)
        XCTAssertTrue(mapped.sourceIDs.isEmpty)
        XCTAssertFalse(
            String(reflecting: result).contains(
                "FAKE_SCORE_REASON_PRIVATE_TO_ADAPTER"
            )
        )
        let counts = await client.counts()
        XCTAssertEqual(counts, .init(search: 1))
    }

    func testSearchRowsNormalizeMachineOrganizationsWithSharedCandidateProjection()
        async throws
    {
        let cases = [
            (raw: "unicef_pageup", expected: "UNICEF"),
            (raw: "wmo_oracle_hcm", expected: "WMO"),
            (raw: "unv_uvp", expected: "UNV"),
            (raw: "wfp_workday", expected: "WFP"),
            (
                raw: "world_food_programme_workday",
                expected: "World Food Programme"
            ),
            (raw: "unicef-pageup", expected: "UNICEF"),
        ]
        let forbiddenTokens: Set<String> = [
            "pageup", "oracle", "hcm", "uvp", "workday",
        ]

        for (index, item) in cases.enumerated() {
            let response = try makeSearchResponse(
                jobs: [
                    makeJob(
                        id: "FAKE_ORGANIZATION_SLUG_JOB_\(index)",
                        organization: item.raw
                    ),
                ],
                total: 1,
                limit: 1,
                offset: 0
            )
            let client = RecordingPublicJobClient(searchResponses: [response])
            let adapter = AtlasAPIClientPublicJobAdapter(client: client)

            let result = try await adapter.search(
                AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
            )
            let job = try XCTUnwrap(result.jobs.first)

            XCTAssertEqual(result.jobs.count, 1, item.raw)
            XCTAssertEqual(
                job.id,
                "FAKE_ORGANIZATION_SLUG_JOB_\(index)",
                item.raw
            )
            XCTAssertEqual(job.title, "Public role", item.raw)
            XCTAssertEqual(job.organization, item.expected, item.raw)
            XCTAssertEqual(job.location, "Tokyo, Japan", item.raw)
            XCTAssertFalse(job.organization.contains("_"), item.raw)
            XCTAssertFalse(job.organization.contains("-"), item.raw)
            let words = Set(
                job.organization
                    .split(whereSeparator: \.isWhitespace)
                    .map { $0.lowercased() }
            )
            XCTAssertTrue(words.isDisjoint(with: forbiddenTokens), item.raw)
            let authorizedCount =
                await adapter.authorizedReferenceCountForTesting()
            let counts = await client.counts()
            XCTAssertEqual(authorizedCount, 1, item.raw)
            XCTAssertEqual(counts, .init(search: 1), item.raw)
        }
    }

    func testRawDTOSearchOrganizationUsesSharedCandidateProjection()
        async throws
    {
        for item in [
            (raw: "unicef_pageup", expected: "UNICEF"),
            (raw: "unicef-pageup", expected: "UNICEF"),
        ] {
            let response = try rawSearchResponse(
                replacingPublicField: "organization",
                with: item.raw
            )
            let client = RecordingPublicJobClient(searchResponses: [response])
            let adapter = AtlasAPIClientPublicJobAdapter(client: client)

            let result = try await adapter.search(
                AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
            )

            XCTAssertEqual(result.jobs.map(\.organization), [item.expected])
            let authorizedCount =
                await adapter.authorizedReferenceCountForTesting()
            XCTAssertEqual(authorizedCount, 1)
        }
    }

    func testJobOrganizationNormalizationPreservesCurrentCandidateDisplayContract()
        throws
    {
        let rawValues = [
            "UNICEF",
            "Unicef",
            "World Health Organization",
            "UNICEF PageUp",
        ]
        let inputs = rawValues.enumerated().map { index, organization in
            makeJob(
                id: "FAKE_CANDIDATE_ORGANIZATION_\(index)",
                organization: organization
            )
        }
        let existingDisplays = inputs.map(\.organizationDisplay)

        let projected = try inputs.map(AtlasProductionPublicProjection.job)

        XCTAssertEqual(
            existingDisplays,
            ["UNICEF", "UNICEF", "World Health Organization", "UNICEF"]
        )
        XCTAssertEqual(projected.map(\.organization), existingDisplays)
    }

    func testJobOrganizationNormalizationRemovesOnlyExactInfrastructureTokens()
        throws
    {
        let cases = [
            (
                raw: "fake_apiary-foundation",
                expected: "Fake Apiary Foundation"
            ),
            (
                raw: "workdaylight_institute",
                expected: "Workdaylight Institute"
            ),
            (raw: "oracleton-centre", expected: "Oracleton Centre"),
            (
                raw: "hcmuseum_collective",
                expected: "Hcmuseum Collective"
            ),
        ]

        let projected = try cases.enumerated().map { index, item in
            try AtlasProductionPublicProjection.job(
                makeJob(
                    id: "FAKE_EXACT_TOKEN_ORGANIZATION_\(index)",
                    organization: item.raw
                )
            )
        }

        XCTAssertEqual(
            projected.map(\.organization),
            cases.map(\.expected)
        )
    }

    func testStablePublicDatesPreserveExactUTCOutputAcrossProjections()
        throws
    {
        let cases: [(date: Date, expected: String)] = [
            (
                Date(timeIntervalSince1970: 0),
                "1970-01-01T00:00:00Z"
            ),
            (
                Date(timeIntervalSince1970: 1_710_032_398),
                "2024-03-10T00:59:58Z"
            ),
            (
                Date(timeIntervalSince1970: 1_710_028_799),
                "2024-03-09T23:59:59Z"
            ),
            (
                Date(timeIntervalSince1970: 1_710_032_398.987_654),
                "2024-03-10T00:59:58Z"
            ),
        ]
        let jobs = cases.enumerated().map { index, item in
            makeJob(
                id: "FAKE_STABLE_DATE_JOB_\(index)",
                closingDate: item.date
            )
        }
        let expected = cases.map(\.expected)

        let direct = try jobs.map(AtlasProductionPublicProjection.job)
        XCTAssertEqual(
            direct.map(\.closingDateText),
            expected.map(Optional.some)
        )

        let response = try makeSearchResponse(
            jobs: jobs,
            total: jobs.count,
            limit: jobs.count,
            offset: 0
        )
        let live = try AtlasProductionPublicProjection.searchResult(
            response,
            expectedLimit: jobs.count,
            expectedOffset: 0
        )
        let snapshot = try AtlasProductionPublicProjection.snapshotJobs(
            response
        )

        XCTAssertEqual(
            live.jobs.map(\.closingDateText),
            expected.map(Optional.some)
        )
        XCTAssertEqual(
            snapshot.map(\.closingDateText),
            expected.map(Optional.some)
        )
        XCTAssertEqual(
            live.jobs.map(\.closingDateText),
            snapshot.map(\.closingDateText)
        )

        let nilDate = try AtlasProductionPublicProjection.job(
            makeJob(id: "FAKE_NIL_DATE_JOB", closingDate: nil)
        )
        XCTAssertNil(nilDate.closingDateText)
    }

    func testStablePublicDateProjectionHandlesTenThousandSnapshotRows()
        throws
    {
        let closingDate = Date(timeIntervalSince1970: 1_710_032_398.987_654)
        let jobs = (0..<10_000).map { index in
            makeJob(
                id: String(format: "FAKE_LARGE_JOB_%05d", index),
                closingDate: closingDate
            )
        }
        let response = try makeSearchResponse(
            jobs: jobs,
            total: jobs.count,
            limit: jobs.count,
            offset: 0
        )

        let projected = try AtlasProductionPublicProjection.snapshotJobs(
            response
        )

        XCTAssertEqual(projected.count, jobs.count)
        XCTAssertEqual(Set(projected.map(\.id)).count, jobs.count)
        XCTAssertEqual(projected.first?.id, "FAKE_LARGE_JOB_00000")
        XCTAssertEqual(projected.last?.id, "FAKE_LARGE_JOB_09999")
        XCTAssertTrue(
            projected.allSatisfy {
                $0.closingDateText == "2024-03-10T00:59:58Z"
            }
        )
    }

    func testStablePublicDateProjectionIsDeterministicUnderConcurrency()
        async throws
    {
        let taskCount = 16
        let iterationsPerTask = 512
        let expected = "2024-03-10T00:59:58Z"
        let closingDate = Date(
            timeIntervalSince1970: 1_710_032_398.987_654
        )

        let batches = try await withThrowingTaskGroup(
            of: [String].self,
            returning: [[String]].self
        ) { group in
            for taskIndex in 0..<taskCount {
                group.addTask {
                    var values: [String] = []
                    values.reserveCapacity(iterationsPerTask)
                    for index in 0..<iterationsPerTask {
                        let projected = try AtlasProductionPublicProjection.job(
                            makeJob(
                                id: "FAKE_CONCURRENT_\(taskIndex)_\(index)",
                                closingDate: closingDate
                            )
                        )
                        guard let text = projected.closingDateText else {
                            throw AtlasPublicJobServiceError.invalidResponse
                        }
                        values.append(text)
                    }
                    return values
                }
            }
            var values: [[String]] = []
            for try await result in group {
                values.append(result)
            }
            return values
        }

        XCTAssertEqual(batches.count, taskCount)
        XCTAssertEqual(
            batches.reduce(0) { $0 + $1.count },
            taskCount * iterationsPerTask
        )
        XCTAssertTrue(
            batches.joined().allSatisfy { $0 == expected }
        )
    }

    func testStableDateTextUsesOneReusableImmutableFormatStyle() throws {
        let adapterSource = try source("AtlasPublicJobAPIAdapter.swift")
        let functionStart = try XCTUnwrap(
            adapterSource.range(
                of: "private static func stableDateText(_ date: Date)"
            )
        )
        let functionAndRemainder = adapterSource[functionStart.lowerBound...]
        let functionEnd = try XCTUnwrap(
            functionAndRemainder.range(
                of: "\n    private static func trimmed"
            )
        )
        let functionSource = String(
            functionAndRemainder[..<functionEnd.lowerBound]
        )

        XCTAssertFalse(functionSource.contains("ISO8601DateFormatter()"))
        XCTAssertTrue(
            adapterSource.contains(
                "private static let stablePublicDateFormat"
                    + " = Date.ISO8601FormatStyle"
            )
        )
        XCTAssertTrue(
            adapterSource.contains("includingFractionalSeconds: false")
        )
        XCTAssertTrue(adapterSource.contains("timeZone: .gmt"))
        XCTAssertTrue(
            functionSource.contains(
                "date.formatted(stablePublicDateFormat)"
            )
        )
        XCTAssertFalse(adapterSource.contains("nonisolated(unsafe)"))
        XCTAssertFalse(adapterSource.contains("@unchecked Sendable"))
    }

    func testSearchRejectsDuplicatesAndInconsistentPagination() async throws {
        let duplicate = try makeSearchResponse(
            jobs: [
                makeJob(id: Self.publicJobID),
                makeJob(id: Self.publicJobID),
            ],
            total: 2,
            limit: 2,
            offset: 0
        )
        let inconsistent = try makeSearchResponse(
            jobs: [makeJob(id: Self.publicJobID)],
            total: 1,
            limit: 2,
            offset: 0
        )
        let client = RecordingPublicJobClient(
            searchResponses: [duplicate, inconsistent]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)

        await assertPublicError(.invalidResponse) {
            try await adapter.search(
                AtlasPublicJobSearchRequest(query: "", limit: 2, offset: 0)
            )
        }
        await assertPublicError(.invalidResponse) {
            try await adapter.search(
                AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
            )
        }
        let counts = await client.counts()
        XCTAssertEqual(counts, .init(search: 2))
    }

    func testSearchRejectsNonOpenRowsBeforeAuthorizingDetail() async throws {
        let response = try makeSearchResponse(
            jobs: [
                makeJob(id: Self.publicJobID, status: "closed"),
            ],
            total: 1,
            limit: 1,
            offset: 0
        )
        let client = RecordingPublicJobClient(
            searchResponses: [response],
            details: [
                Self.publicJobID: makeDetail(id: Self.publicJobID),
            ]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )

        await assertPublicError(.invalidResponse) {
            try await adapter.search(
                AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
            )
        }
        await assertPublicError(.invalidRequest) {
            try await adapter.detail(for: reference)
        }

        let authorizedCount = await adapter.authorizedReferenceCountForTesting()
        let counts = await client.counts()
        XCTAssertEqual(authorizedCount, 0)
        XCTAssertEqual(counts, .init(search: 1))
    }

    func testSearchRejectsDecoderFallbackFieldsBeforeAuthorizingDetail()
        async throws
    {
        let responses = try [
            "title",
            "organization",
            "duty_station",
        ].map(rawSearchResponse(omittingPublicField:))
        let client = RecordingPublicJobClient(
            searchResponses: responses,
            details: [
                Self.publicJobID: makeDetail(id: Self.publicJobID),
            ]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let request = try AtlasPublicJobSearchRequest(
            query: "",
            limit: 1,
            offset: 0
        )
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )

        for _ in responses {
            await assertPublicError(.invalidResponse) {
                try await adapter.search(request)
            }
        }
        await assertPublicError(.invalidRequest) {
            try await adapter.detail(for: reference)
        }

        let authorizedCount = await adapter.authorizedReferenceCountForTesting()
        let counts = await client.counts()
        XCTAssertEqual(authorizedCount, 0)
        XCTAssertEqual(counts, .init(search: 3))
    }

    func testSearchRejectsRawDTOPlaceholderLabelsBeforeAuthorization()
        async throws
    {
        for (field, value) in [
            ("organization", "unknown_organization"),
            ("duty_station", "location_not_classified"),
            ("title", "UNTITLED VACANCY"),
        ] {
            let response = try rawSearchResponse(
                replacingPublicField: field,
                with: value
            )
            try await assertSearchRejectsBeforeAuthorization(
                response,
                rejectedValue: value
            )
        }
    }

    func testSearchRejectsCaseSeparatorAndWhitespacePlaceholderVariants()
        async throws
    {
        let titleVariants = [
            "Untitled Vacancy",
            "UNTITLED VACANCY",
            "untitled_vacancy",
            "untitled-vacancy",
            " Untitled   Vacancy ",
        ]
        let organizationVariants = [
            "Unknown Organization",
            "UNKNOWN ORGANIZATION",
            "unknown_organization",
            "unknown-organization",
            " Unknown   Organization ",
        ]
        let locationVariants = [
            "Location Not Classified",
            "LOCATION NOT CLASSIFIED",
            "location_not_classified",
            "location-not-classified",
            " Location   Not Classified ",
        ]

        for value in titleVariants {
            try await assertSearchRejectsBeforeAuthorization(
                try makeSearchResponse(
                    jobs: [
                        makeJob(id: Self.publicJobID, title: value),
                    ],
                    total: 1,
                    limit: 1,
                    offset: 0
                ),
                rejectedValue: value
            )
        }
        for value in organizationVariants {
            try await assertSearchRejectsBeforeAuthorization(
                try makeSearchResponse(
                    jobs: [
                        makeJob(id: Self.publicJobID, organization: value),
                    ],
                    total: 1,
                    limit: 1,
                    offset: 0
                ),
                rejectedValue: value
            )
        }
        for value in locationVariants {
            try await assertSearchRejectsBeforeAuthorization(
                try makeSearchResponse(
                    jobs: [
                        makeJob(id: Self.publicJobID, location: value),
                    ],
                    total: 1,
                    limit: 1,
                    offset: 0
                ),
                rejectedValue: value
            )
        }
    }

    func testPlaceholderMatchingIsExactAndPreservesAcceptedLabels()
        async throws
    {
        let accepted = makeJob(
            id: Self.publicJobID,
            title: "Untitled Vacancy Programme",
            organization: "Unknown Organization Initiative",
            location: "Location Not Classified Research Centre"
        )
        let client = RecordingPublicJobClient(
            searchResponses: [
                try makeSearchResponse(
                    jobs: [accepted],
                    total: 1,
                    limit: 1,
                    offset: 0
                ),
            ],
            details: [
                Self.publicJobID: makeDetail(id: Self.publicJobID),
            ]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)

        let result = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )
        _ = try await adapter.detail(
            for: AtlasPublicJobReference(publicJobID: Self.publicJobID)
        )

        XCTAssertEqual(result.jobs[0].title, "Untitled Vacancy Programme")
        XCTAssertEqual(
            result.jobs[0].organization,
            "Unknown Organization Initiative"
        )
        XCTAssertEqual(
            result.jobs[0].location,
            "Location Not Classified Research Centre"
        )
        XCTAssertEqual(
            try AtlasProductionPublicProjection.job(
                makeJob(
                    id: Self.secondPublicJobID,
                    organization: "Unknown Organizations Unit"
                )
            ).organization,
            "Unknown Organizations Unit"
        )
        XCTAssertEqual(
            try AtlasProductionPublicProjection.job(
                makeJob(
                    id: Self.thirdPublicJobID,
                    location: "Classified Location Office"
                )
            ).location,
            "Classified Location Office"
        )
        let authorizedCount = await adapter.authorizedReferenceCountForTesting()
        let counts = await client.counts()
        XCTAssertEqual(authorizedCount, 1)
        XCTAssertEqual(counts, .init(search: 1, detail: 1))
    }

    func testPlaceholderPoliciesAreFieldSpecific() throws {
        let projected = try AtlasProductionPublicProjection.job(
            makeJob(
                id: Self.publicJobID,
                title: "Unknown Organization",
                organization: "Untitled Vacancy",
                location: "Unknown Organization"
            )
        )

        XCTAssertEqual(projected.title, "Unknown Organization")
        XCTAssertEqual(projected.organization, "Untitled Vacancy")
        XCTAssertEqual(projected.location, "Unknown Organization")
    }

    func testPlaceholderValidationUsesFieldSpecificExactCanonicalKeys()
        throws
    {
        let adapterSource = try source("AtlasPublicJobAPIAdapter.swift")

        XCTAssertTrue(
            adapterSource.contains("rejectedTitlePlaceholderKeys")
        )
        XCTAssertTrue(
            adapterSource.contains("rejectedOrganizationPlaceholderKeys")
        )
        XCTAssertTrue(
            adapterSource.contains("rejectedLocationPlaceholderKeys")
        )
        XCTAssertTrue(
            adapterSource.contains("canonicalPublicPlaceholderKey")
        )
        for forbidden in [
            "contains(\"unknown\")",
            "hasPrefix(\"unknown\")",
            "hasSuffix(\"unknown\")",
            "NSRegularExpression",
            "Levenshtein",
            "similarity",
            "fuzzy",
            "Locale.current",
            "localized",
        ] {
            XCTAssertFalse(adapterSource.contains(forbidden), forbidden)
        }
    }

    func testATSOnlyAndPlaceholderOrganizationsFailBeforeAuthorization()
        async throws
    {
        for organization in [
            "oracle_hcm",
            "pageup_workday",
            "api_static",
            "---___",
            " \t ",
            "unknown_organization",
            "unknown-organization",
            "Unknown Organization",
        ] {
            try await assertSearchRejectsBeforeAuthorization(
                try rawSearchResponse(
                    replacingPublicField: "organization",
                    with: organization
                ),
                rejectedValue: organization
            )
        }
    }

    func testJobAndSourceProjectionShareCandidateOrganizationHelper() throws {
        let adapterSource = try source("AtlasPublicJobAPIAdapter.swift")
        let jobStart = try XCTUnwrap(
            adapterSource.range(of: "    static func job(")
        )
        let sourceStart = try XCTUnwrap(
            adapterSource.range(
                of: "\n    static func source(",
                range: jobStart.upperBound..<adapterSource.endIndex
            )
        )
        let updateStart = try XCTUnwrap(
            adapterSource.range(
                of: "\n    static func update(",
                range: sourceStart.upperBound..<adapterSource.endIndex
            )
        )
        let jobSource = String(
            adapterSource[jobStart.lowerBound..<sourceStart.lowerBound]
        )
        let sourceSource = String(
            adapterSource[sourceStart.lowerBound..<updateStart.lowerBound]
        )

        XCTAssertTrue(jobSource.contains("candidateOrganizationDisplay"))
        XCTAssertTrue(jobSource.contains("value.organizationDisplay"))
        XCTAssertFalse(
            jobSource.contains(
                "value.organizationDisplay.trimmingCharacters"
            )
        )
        XCTAssertTrue(sourceSource.contains("candidateOrganizationDisplay"))
        XCTAssertTrue(sourceSource.contains("value.organization"))
        XCTAssertEqual(
            adapterSource.components(
                separatedBy:
                    "private static let candidateOrganizationInfrastructureTokens"
            ).count - 1,
            1
        )
    }

    func testFailedSearchDoesNotAuthorizeDetail() async throws {
        let client = RecordingPublicJobClient(
            searchFailure: .transport(Self.transportSentinel)
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )

        await assertPublicError(.unavailable) {
            try await adapter.search(
                AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
            )
        }
        await assertPublicError(.invalidRequest) {
            try await adapter.detail(for: reference)
        }
        let counts = await client.counts()
        XCTAssertEqual(counts, .init(search: 1))
    }

    func testSourcesProjectSafeFieldsAndRejectInvalidValues() async throws {
        let valid = AtlasSourceSummary(
            sourceID: "public_source",
            organization: "UNICEF",
            totalJobs: 9,
            openJobs: 7,
            lastSeenAt: "2026-07-19T00:00:00Z",
            healthStatus: "ok",
            observedAt: "2026-07-19T00:00:00Z",
            detailAttempted: 4,
            detailFailed: 1,
            missingTransitionAllowed: false
        )
        let invalid = AtlasSourceSummary(
            sourceID: "",
            organization: "UNICEF",
            totalJobs: 0,
            openJobs: -1,
            lastSeenAt: nil,
            healthStatus: "ok",
            observedAt: nil,
            detailAttempted: nil,
            detailFailed: nil,
            missingTransitionAllowed: nil
        )
        let client = RecordingPublicJobClient(
            sourceResponses: [
                AtlasSourcesResponse(sources: [valid]),
                AtlasSourcesResponse(sources: [invalid]),
            ]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)

        let sources = try await adapter.sources()

        XCTAssertEqual(
            sources,
            [
                try AtlasPublicSourceStatus(
                    sourceID: "public_source",
                    displayName: "UNICEF",
                    availability: .available,
                    openJobCount: 7
                ),
            ]
        )
        await assertPublicError(.invalidResponse) {
            try await adapter.sources()
        }
        let counts = await client.counts()
        XCTAssertEqual(counts, .init(sources: 2))
    }

    func testSourcesNormalizeCandidateLabelsAndPreserveOpaqueIDs()
        async throws
    {
        let values = [
            makeSourceSummary(
                sourceID: "unicef_pageup",
                organization: "unicef_pageup",
                totalJobs: 9,
                openJobs: 7
            ),
            makeSourceSummary(
                sourceID: "wmo_oracle_hcm",
                organization: "wmo_oracle_hcm",
                totalJobs: 8,
                openJobs: 6
            ),
            makeSourceSummary(
                sourceID: "unv_uvp",
                organization: "unv_uvp",
                totalJobs: 7,
                openJobs: 5
            ),
            makeSourceSummary(
                sourceID: "wfp_workday",
                organization: "wfp_workday",
                totalJobs: 6,
                openJobs: 4
            ),
            makeSourceSummary(
                sourceID: "world_food_programme_workday",
                organization: "world_food_programme_workday",
                totalJobs: 5,
                openJobs: 3
            ),
        ]
        let client = RecordingPublicJobClient(
            sourceResponses: [AtlasSourcesResponse(sources: values)]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)

        let sources = try await adapter.sources()

        XCTAssertEqual(sources.map(\.sourceID), values.map(\.sourceID))
        XCTAssertEqual(
            sources.map(\.displayName),
            ["UNICEF", "WMO", "UNV", "WFP", "World Food Programme"]
        )
        XCTAssertEqual(sources.map(\.openJobCount), [7, 6, 5, 4, 3])
        XCTAssertTrue(sources.allSatisfy { $0.availability == .available })
        let forbiddenTokens: Set<String> = [
            "pageup", "oracle", "hcm", "uvp", "workday",
        ]
        for (source, rawValue) in zip(sources, values.map(\.organization)) {
            XCTAssertFalse(source.displayName.contains("_"))
            XCTAssertFalse(source.displayName.contains("-"))
            let words = Set(
                source.displayName
                    .split(whereSeparator: \.isWhitespace)
                    .map { $0.lowercased() }
            )
            XCTAssertTrue(words.isDisjoint(with: forbiddenTokens))
            XCTAssertFalse(String(describing: source).contains(rawValue))
            XCTAssertFalse(String(reflecting: source).contains(rawValue))
        }
        let counts = await client.counts()
        XCTAssertEqual(counts, .init(sources: 1))
    }

    func testSourceLabelsPreserveCandidateTextAndRemoveOnlyExactTokens()
        async throws
    {
        let values = [
            makeSourceSummary(
                sourceID: "who_public",
                organization: "World Health Organization"
            ),
            makeSourceSummary(
                sourceID: "unicef_pageup",
                organization: "UNICEF PageUp"
            ),
            makeSourceSummary(
                sourceID: "fake_candidate_text",
                organization: "Fake Capitol Hcmuseum Oracleson"
            ),
            makeSourceSummary(
                sourceID: "fake_machine_text",
                organization: "fake_apiary-oracular_guidance"
            ),
            makeSourceSummary(
                sourceID: "fake_whitespace_text",
                organization: "  World   Health\tOrganization  "
            ),
            makeSourceSummary(
                sourceID: "fake_short_candidate_text",
                organization: "Fakeco"
            ),
        ]
        let client = RecordingPublicJobClient(
            sourceResponses: [AtlasSourcesResponse(sources: values)]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)

        let sources = try await adapter.sources()

        XCTAssertEqual(
            sources.map(\.displayName),
            [
                "World Health Organization",
                "UNICEF",
                "Fake Capitol Hcmuseum Oracleson",
                "Fake Apiary Oracular Guidance",
                "World Health Organization",
                "Fakeco",
            ]
        )
        XCTAssertEqual(sources.map(\.sourceID), values.map(\.sourceID))
    }

    func testSourcesFailClosedForEmptySeparatorATSAndFallbackLabels()
        async
    {
        let invalidOrganizations = [
            "",
            " \t\n ",
            "oracle_hcm",
            "---___",
            "Unknown organization",
        ]

        for (index, organization) in invalidOrganizations.enumerated() {
            let client = RecordingPublicJobClient(
                sourceResponses: [
                    AtlasSourcesResponse(
                        sources: [
                            makeSourceSummary(
                                sourceID: "fake_invalid_source_\(index)",
                                organization: organization
                            ),
                        ]
                    ),
                ]
            )
            let adapter = AtlasAPIClientPublicJobAdapter(client: client)

            await assertPublicError(.invalidResponse) {
                try await adapter.sources()
            }
            let counts = await client.counts()
            XCTAssertEqual(counts, .init(sources: 1))
        }
    }

    func testSourcesMapDocumentedBackendHealthStatuses() async throws {
        let statuses: [(String, AtlasPublicServiceAvailability)] = [
            ("ok_empty", .available),
            ("warning", .unavailable),
            ("degraded", .unavailable),
            ("issue", .unavailable),
        ]
        let sourceValues = statuses.enumerated().map { index, item in
            AtlasSourceSummary(
                sourceID: "public_source_\(index)",
                organization: "Public Organization \(index)",
                totalJobs: 1,
                openJobs: 1,
                lastSeenAt: nil,
                healthStatus: item.0,
                observedAt: nil,
                detailAttempted: nil,
                detailFailed: nil,
                missingTransitionAllowed: nil
            )
        }
        let client = RecordingPublicJobClient(
            sourceResponses: [AtlasSourcesResponse(sources: sourceValues)]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)

        let projected = try await adapter.sources()

        XCTAssertEqual(
            projected.map(\.availability),
            statuses.map(\.1)
        )
    }

    func testUpdatesUseCheckedChangedCountAndRejectNegativeOrOverflow()
        async throws
    {
        let valid = AtlasSourceRun(
            sourceID: "public_source",
            fetched: 9,
            inserted: 2,
            updated: 3,
            missing: 1,
            closed: 1,
            observedAt: "2026-07-19T00:00:00Z"
        )
        let negative = AtlasSourceRun(
            sourceID: "public_source",
            fetched: -1,
            inserted: 0,
            updated: 0,
            missing: 0,
            closed: 0,
            observedAt: nil
        )
        let overflow = AtlasSourceRun(
            sourceID: "public_source",
            fetched: Int.max,
            inserted: Int.max,
            updated: 1,
            missing: 0,
            closed: 0,
            observedAt: nil
        )
        let invalidDate = AtlasSourceRun(
            sourceID: "public_source",
            fetched: 1,
            inserted: 0,
            updated: 0,
            missing: 0,
            closed: 0,
            observedAt: "FAKE_INVALID_DATE"
        )
        let client = RecordingPublicJobClient(
            updateResponses: [
                AtlasUpdatesResponse(recentSourceRuns: [valid]),
                AtlasUpdatesResponse(recentSourceRuns: [negative]),
                AtlasUpdatesResponse(recentSourceRuns: [overflow]),
                AtlasUpdatesResponse(recentSourceRuns: [invalidDate]),
            ]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)

        let updates = try await adapter.updates()

        XCTAssertEqual(updates[0].fetchedJobCount, 9)
        XCTAssertEqual(updates[0].changedJobCount, 5)
        XCTAssertEqual(updates[0].closedJobCount, 1)
        XCTAssertEqual(
            updates[0].observedAt,
            try date("2026-07-19T00:00:00Z")
        )
        await assertPublicError(.invalidResponse) {
            try await adapter.updates()
        }
        await assertPublicError(.invalidResponse) {
            try await adapter.updates()
        }
        await assertPublicError(.invalidResponse) {
            try await adapter.updates()
        }
    }

    func testDetailRequiresIssuedReferenceAndUsesPriorProjection() async throws {
        let publicJob = makeJob(
            id: Self.publicJobID,
            title: "Issued public title",
            organization: "UNDP Oracle",
            location: "Nairobi"
        )
        let response = try makeSearchResponse(
            jobs: [publicJob],
            total: 1,
            limit: 1,
            offset: 0
        )
        let detail = makeDetail(
            id: Self.publicJobID,
            title: "Changed upstream title",
            description: "Public detail body"
        )
        let client = RecordingPublicJobClient(
            searchResponses: [response],
            details: [Self.publicJobID: detail]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let issued = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )
        let unissued = try AtlasPublicJobReference(
            publicJobID: Self.secondPublicJobID
        )

        await assertPublicError(.invalidRequest) {
            try await adapter.detail(for: issued)
        }
        await assertPublicError(.invalidRequest) {
            try await adapter.detail(for: unissued)
        }
        let countsBeforeSearch = await client.counts()
        XCTAssertEqual(countsBeforeSearch.detail, 0)

        let search = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )
        let result = try await adapter.detail(for: issued)

        XCTAssertEqual(result.reference, issued)
        XCTAssertEqual(result.job, search.jobs[0])
        XCTAssertEqual(result.job.title, "Issued public title")
        XCTAssertEqual(result.detailText, "Public detail body")
        let countsAfterDetail = await client.counts()
        XCTAssertEqual(countsAfterDetail.detail, 1)
    }

    func testDetailCandidateSectionTakesPrecedenceOverDuplicateDescription()
        async throws
    {
        let duplicated = "FAKE_DUPLICATED_PUBLIC_DESCRIPTION"
        let detail = makeDetail(
            id: Self.publicJobID,
            description: duplicated,
            displaySections: [
                AtlasDetailSection(
                    title: "Full Description",
                    body: duplicated
                ),
            ]
        )
        let client = RecordingPublicJobClient(
            searchResponses: [
                try makeSearchResponse(
                    jobs: [makeJob(id: Self.publicJobID)],
                    total: 1,
                    limit: 1,
                    offset: 0
                ),
            ],
            details: [Self.publicJobID: detail]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )
        let search = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )

        let result = try await adapter.detail(for: reference)

        XCTAssertEqual(result.reference, reference)
        XCTAssertEqual(result.job, search.jobs[0])
        XCTAssertEqual(result.detailText, duplicated)
        XCTAssertEqual(
            result.detailText.components(separatedBy: duplicated).count - 1,
            1
        )
        let authorizedCount = await adapter.authorizedReferenceCountForTesting()
        let counts = await client.counts()
        XCTAssertEqual(authorizedCount, 1)
        XCTAssertEqual(counts, .init(search: 1, detail: 1))
    }

    func testDetailStructuredSectionsSuppressTopLevelDescription() throws {
        let topLevel = "FAKE_FULL_TOP_LEVEL_DESCRIPTION_DO_NOT_REPEAT"
        let detail = makeDetail(
            id: Self.publicJobID,
            description: topLevel,
            displaySections: [
                AtlasDetailSection(
                    title: "Summary",
                    body: "FAKE_SECTION_SUMMARY"
                ),
                AtlasDetailSection(
                    title: "Responsibilities",
                    body: "FAKE_SECTION_RESPONSIBILITIES"
                ),
                AtlasDetailSection(
                    title: "Qualifications",
                    body: "FAKE_SECTION_QUALIFICATIONS"
                ),
            ]
        )

        let text = try AtlasProductionPublicProjection.detailText(detail)

        XCTAssertEqual(
            text,
            [
                "FAKE_SECTION_SUMMARY",
                "FAKE_SECTION_RESPONSIBILITIES",
                "FAKE_SECTION_QUALIFICATIONS",
            ].joined(separator: "\n\n")
        )
        XCTAssertFalse(text.contains(topLevel))
    }

    func testDetailSectionBodyAndRowsPreserveOrderWithoutTopLevel() throws {
        let topLevel = "FAKE_DISTINCT_TOP_LEVEL_DESCRIPTION"
        let detail = makeDetail(
            id: Self.publicJobID,
            description: topLevel,
            displaySections: [
                AtlasDetailSection(
                    title: "Candidate Requirements",
                    body: "FAKE_SECTION_BODY",
                    rows: [
                        AtlasDetailRow(
                            label: "FAKE_ROW_LABEL_ONE_NOT_PUBLIC",
                            value: "FAKE_ROW_VALUE_ONE"
                        ),
                        AtlasDetailRow(
                            label: "FAKE_ROW_LABEL_TWO_NOT_PUBLIC",
                            value: "FAKE_ROW_VALUE_TWO"
                        ),
                    ]
                ),
            ]
        )

        let text = try AtlasProductionPublicProjection.detailText(detail)

        XCTAssertEqual(
            text,
            [
                "FAKE_SECTION_BODY",
                "FAKE_ROW_VALUE_ONE",
                "FAKE_ROW_VALUE_TWO",
            ].joined(separator: "\n\n")
        )
        XCTAssertFalse(text.contains(topLevel))
        XCTAssertFalse(text.contains("FAKE_ROW_LABEL_ONE_NOT_PUBLIC"))
        XCTAssertFalse(text.contains("Candidate Requirements"))
    }

    func testDetailDescriptionIsFallbackWhenSectionsAreAbsent() throws {
        let detail = makeDetail(
            id: Self.publicJobID,
            description: "  FAKE_DESCRIPTION_ONLY_FALLBACK  ",
            displaySections: []
        )

        XCTAssertEqual(
            try AtlasProductionPublicProjection.detailText(detail),
            "FAKE_DESCRIPTION_ONLY_FALLBACK"
        )
    }

    func testDetailMetadataOnlySectionsPermitDescriptionFallback() throws {
        let metadataSections = [
            "Job Record",
            "Classification",
            "Locations",
            "Source Features",
            "Raw Source Data",
        ].enumerated().map { index, title in
            AtlasDetailSection(
                title: title,
                body: "FAKE_METADATA_FALLBACK_BODY_\(index)",
                rows: [
                    AtlasDetailRow(
                        label: "FAKE_METADATA_FALLBACK_LABEL_\(index)",
                        value: "FAKE_METADATA_FALLBACK_VALUE_\(index)"
                    ),
                ]
            )
        }
        let detail = makeDetail(
            id: Self.publicJobID,
            description: "FAKE_DESCRIPTION_AFTER_METADATA_FILTER",
            displaySections: metadataSections
        )

        let text = try AtlasProductionPublicProjection.detailText(detail)

        XCTAssertEqual(text, "FAKE_DESCRIPTION_AFTER_METADATA_FILTER")
        for index in metadataSections.indices {
            XCTAssertFalse(text.contains("FAKE_METADATA_FALLBACK_BODY_\(index)"))
            XCTAssertFalse(text.contains("FAKE_METADATA_FALLBACK_VALUE_\(index)"))
        }
    }

    func testDetailEmptyCandidateSectionsPermitDescriptionFallback() throws {
        let detail = makeDetail(
            id: Self.publicJobID,
            description: "FAKE_DESCRIPTION_AFTER_EMPTY_SECTIONS",
            displaySections: [
                AtlasDetailSection(
                    title: "Responsibilities",
                    body: " \n ",
                    rows: [
                        AtlasDetailRow(
                            label: "FAKE_EMPTY_ROW_LABEL_NOT_PUBLIC",
                            value: " \t "
                        ),
                    ]
                ),
            ]
        )

        XCTAssertEqual(
            try AtlasProductionPublicProjection.detailText(detail),
            "FAKE_DESCRIPTION_AFTER_EMPTY_SECTIONS"
        )
    }

    func testDetailWithoutUsablePublicContentFailsClosed() {
        let detail = makeDetail(
            id: Self.publicJobID,
            description: " \n ",
            displaySections: [
                AtlasDetailSection(
                    title: "Responsibilities",
                    body: " \t ",
                    rows: [
                        AtlasDetailRow(label: "Empty", value: " \n "),
                    ]
                ),
                AtlasDetailSection(
                    title: "Job Record",
                    body: "FAKE_METADATA_NOT_A_FALLBACK"
                ),
            ]
        )

        XCTAssertThrowsError(
            try AtlasProductionPublicProjection.detailText(detail)
        ) { error in
            XCTAssertEqual(
                error as? AtlasPublicJobServiceError,
                .invalidResponse
            )
        }
    }

    func testDetailDoesNotDeduplicateRepeatedCandidateSectionContent() throws {
        let repeated = "FAKE_INTENTIONALLY_REPEATED_SECTION_BODY"
        let detail = makeDetail(
            id: Self.publicJobID,
            description: "FAKE_TOP_LEVEL_NOT_PROJECTED",
            displaySections: [
                AtlasDetailSection(title: "Summary", body: repeated),
                AtlasDetailSection(title: "Responsibilities", body: repeated),
            ]
        )

        XCTAssertEqual(
            try AtlasProductionPublicProjection.detailText(detail),
            [repeated, repeated].joined(separator: "\n\n")
        )
    }

    func testDetailExcludesCompleteCanonicalMetadataSections() async throws {
        let metadataSections = [
            "Job Record",
            "Classification",
            "Locations",
            "Source Features",
            "Raw Source Data",
        ].enumerated().map { index, title in
            AtlasDetailSection(
                title: title,
                body: "FAKE_METADATA_BODY_\(index)_DO_NOT_PROJECT",
                rows: [
                    AtlasDetailRow(
                        label: "FAKE_METADATA_LABEL_\(index)_DO_NOT_PROJECT",
                        value: "FAKE_METADATA_VALUE_\(index)_DO_NOT_PROJECT"
                    ),
                ]
            )
        }
        let detail = makeDetail(
            id: Self.publicJobID,
            description: "FAKE_SAFE_TOP_LEVEL_DESCRIPTION",
            displaySections: [
                AtlasDetailSection(
                    title: "Candidate Requirements",
                    body: "FAKE_SAFE_CANDIDATE_BODY",
                    rows: [
                        AtlasDetailRow(
                            label: "Experience",
                            value: "FAKE_SAFE_CANDIDATE_ROW_VALUE"
                        ),
                    ]
                ),
            ] + metadataSections
        )
        let client = RecordingPublicJobClient(
            searchResponses: [
                try makeSearchResponse(
                    jobs: [makeJob(id: Self.publicJobID)],
                    total: 1,
                    limit: 1,
                    offset: 0
                ),
            ],
            details: [Self.publicJobID: detail]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )
        let search = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )

        let result = try await adapter.detail(for: reference)

        XCTAssertEqual(result.reference, reference)
        XCTAssertEqual(result.job, search.jobs[0])
        XCTAssertEqual(
            result.detailText,
            [
                "FAKE_SAFE_CANDIDATE_BODY",
                "FAKE_SAFE_CANDIDATE_ROW_VALUE",
            ].joined(separator: "\n\n")
        )
        XCTAssertFalse(
            result.detailText.contains("FAKE_SAFE_TOP_LEVEL_DESCRIPTION")
        )
        for index in metadataSections.indices {
            XCTAssertFalse(
                result.detailText.contains(
                    "FAKE_METADATA_BODY_\(index)_DO_NOT_PROJECT"
                )
            )
            XCTAssertFalse(
                result.detailText.contains(
                    "FAKE_METADATA_LABEL_\(index)_DO_NOT_PROJECT"
                )
            )
            XCTAssertFalse(
                result.detailText.contains(
                    "FAKE_METADATA_VALUE_\(index)_DO_NOT_PROJECT"
                )
            )
        }
        for title in [
            "Job Record",
            "Classification",
            "Locations",
            "Source Features",
            "Raw Source Data",
        ] {
            XCTAssertFalse(result.detailText.contains(title))
        }
        let authorizedCount = await adapter.authorizedReferenceCountForTesting()
        let counts = await client.counts()
        XCTAssertEqual(authorizedCount, 1)
        XCTAssertEqual(counts, .init(search: 1, detail: 1))
    }

    func testDetailMetadataTitleMatchingIsNormalizedAndExact() async throws {
        let excludedVariants = [
            " job record ",
            " CLASSIFICATION ",
            "LOCATIONS",
            "Source Features ",
            " raw source data",
        ].enumerated().map { index, title in
            AtlasDetailSection(
                title: title,
                body: "FAKE_NORMALIZED_METADATA_BODY_\(index)",
                rows: [
                    AtlasDetailRow(
                        label: "FAKE_NORMALIZED_METADATA_LABEL_\(index)",
                        value: "FAKE_NORMALIZED_METADATA_VALUE_\(index)"
                    ),
                ]
            )
        }
        let detail = makeDetail(
            id: Self.publicJobID,
            description: nil,
            displaySections: excludedVariants + [
                AtlasDetailSection(
                    title: "Raw Source Data Guidance",
                    body: "FAKE_SAFE_SIMILAR_TITLE_BODY",
                    rows: [
                        AtlasDetailRow(
                            label: "Guidance",
                            value: "FAKE_SAFE_SIMILAR_TITLE_ROW"
                        ),
                    ]
                ),
            ]
        )
        let client = RecordingPublicJobClient(
            searchResponses: [
                try makeSearchResponse(
                    jobs: [makeJob(id: Self.publicJobID)],
                    total: 1,
                    limit: 1,
                    offset: 0
                ),
            ],
            details: [Self.publicJobID: detail]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )
        _ = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )

        let result = try await adapter.detail(for: reference)

        XCTAssertEqual(
            result.detailText,
            [
                "FAKE_SAFE_SIMILAR_TITLE_BODY",
                "FAKE_SAFE_SIMILAR_TITLE_ROW",
            ].joined(separator: "\n\n")
        )
        for index in excludedVariants.indices {
            XCTAssertFalse(
                result.detailText.contains(
                    "FAKE_NORMALIZED_METADATA_BODY_\(index)"
                )
            )
            XCTAssertFalse(
                result.detailText.contains(
                    "FAKE_NORMALIZED_METADATA_VALUE_\(index)"
                )
            )
        }
        let counts = await client.counts()
        XCTAssertEqual(counts, .init(search: 1, detail: 1))
    }

    func testDetailWithOnlyMetadataSectionsFailsClosed() async throws {
        let detail = makeDetail(
            id: Self.publicJobID,
            description: " \n ",
            displaySections: [
                AtlasDetailSection(
                    title: "Job Record",
                    body: "FAKE_METADATA_ONLY_BODY",
                    rows: [
                        AtlasDetailRow(
                            label: "FAKE_METADATA_ONLY_LABEL",
                            value: "FAKE_METADATA_ONLY_VALUE"
                        ),
                    ]
                ),
                AtlasDetailSection(
                    title: "Raw Source Data",
                    body: "FAKE_METADATA_ONLY_RAW_BODY"
                ),
            ]
        )
        let client = RecordingPublicJobClient(
            searchResponses: [
                try makeSearchResponse(
                    jobs: [makeJob(id: Self.publicJobID)],
                    total: 1,
                    limit: 1,
                    offset: 0
                ),
            ],
            details: [Self.publicJobID: detail]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )
        _ = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )

        await assertPublicError(.invalidResponse) {
            try await adapter.detail(for: reference)
        }

        let authorizedCount = await adapter.authorizedReferenceCountForTesting()
        let counts = await client.counts()
        XCTAssertEqual(authorizedCount, 1)
        XCTAssertEqual(counts, .init(search: 1, detail: 1))
    }

    func testDetailRejectsMismatchedReturnedIdentity() async throws {
        let response = try makeSearchResponse(
            jobs: [makeJob(id: Self.publicJobID)],
            total: 1,
            limit: 1,
            offset: 0
        )
        let client = RecordingPublicJobClient(
            searchResponses: [response],
            details: [
                Self.publicJobID: makeDetail(
                    id: Self.secondPublicJobID,
                    description: "Wrong detail"
                ),
            ]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        _ = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )

        await assertPublicError(.invalidResponse) {
            try await adapter.detail(
                for: AtlasPublicJobReference(publicJobID: Self.publicJobID)
            )
        }
    }

    func testDetailProvenanceIsBoundedWithDeterministicFIFOEviction()
        async throws
    {
        let first = try makeSearchResponse(
            jobs: [
                makeJob(id: Self.publicJobID),
                makeJob(id: Self.secondPublicJobID),
            ],
            total: 2,
            limit: 2,
            offset: 0
        )
        let second = try makeSearchResponse(
            jobs: [makeJob(id: Self.thirdPublicJobID)],
            total: 1,
            limit: 1,
            offset: 0
        )
        let client = RecordingPublicJobClient(
            searchResponses: [first, second],
            details: [
                Self.publicJobID: makeDetail(id: Self.publicJobID),
                Self.secondPublicJobID: makeDetail(id: Self.secondPublicJobID),
                Self.thirdPublicJobID: makeDetail(id: Self.thirdPublicJobID),
            ]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(
            client: client,
            provenanceCapacity: 2
        )
        _ = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 2, offset: 0)
        )
        _ = try await adapter.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )

        await assertPublicError(.invalidRequest) {
            try await adapter.detail(
                for: AtlasPublicJobReference(publicJobID: Self.publicJobID)
            )
        }
        _ = try await adapter.detail(
            for: AtlasPublicJobReference(
                publicJobID: Self.secondPublicJobID
            )
        )
        _ = try await adapter.detail(
            for: AtlasPublicJobReference(publicJobID: Self.thirdPublicJobID)
        )
        let authorizedCount = await adapter.authorizedReferenceCountForTesting()
        XCTAssertEqual(authorizedCount, 2)
        XCTAssertFalse(String(reflecting: adapter).contains(Self.publicJobID))
    }

    func testAPIAdapterSourceExposesOnlyNarrowPublicClientOperations()
        throws
    {
        let source = try source("AtlasPublicJobAPIAdapter.swift")

        for forbidden in [
            "savedSearch",
            "saveSearch",
            "deleteSavedSearch",
            "saveJob",
            "trackerRecords",
            "deleteTracker",
            "/api/saved-searches",
            "/api/tracker",
            "Keychain",
            "SecItem",
            "FileManager",
            "UserDefaults",
            "@main",
            "SwiftUI",
            "organizations.yaml",
            "Locale.current",
            "localizedCapitalized",
            "unicef_pageup",
            "wmo_oracle_hcm",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        XCTAssertTrue(source.contains("func health()"))
        XCTAssertTrue(source.contains("func search("))
        XCTAssertTrue(source.contains("func jobDetail("))
        XCTAssertTrue(source.contains("func sources()"))
        XCTAssertTrue(source.contains("func updates()"))
    }

    // MARK: - Public snapshot restorer

    func testSnapshotRestorerConstructionInvokesNothingAndIsRedacted() async {
        let root = RecordingRootProvider(
            outcome: .url(URL(fileURLWithPath: "/tmp/atlas-root"))
        )
        let reader = RecordingSnapshotFileReader(status: .missing)
        let restorer = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: root,
            fileReader: reader
        )

        XCTAssertEqual(root.callCount, 0)
        XCTAssertEqual(reader.counts, .zero)
        XCTAssertEqual(
            String(describing: restorer),
            "AtlasApplicationSupportPublicSnapshotRestorer(<redacted>)"
        )
    }

    func testFoundationSnapshotReaderMapsActualMissingFileToMissing() throws {
        let reader = AtlasFoundationPublicSnapshotFileReader()
        let missingURL = try AtlasVaultTestFileSystemSupport
            .canonicalTemporaryRoot()
            .appendingPathComponent(
                "atlas-phase2d55-missing-\(UUID().uuidString)",
                isDirectory: false
            )

        XCTAssertEqual(try reader.status(at: missingURL), .missing)
    }

    func testMissingSnapshotReturnsNilAndUsesExactReviewedPath() async throws {
        let rootURL = URL(fileURLWithPath: "/tmp/atlas-root", isDirectory: true)
        let root = RecordingRootProvider(outcome: .url(rootURL))
        let reader = RecordingSnapshotFileReader(status: .missing)
        let restorer = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: root,
            fileReader: reader
        )

        let restored = try await restorer.restore()
        XCTAssertNil(restored)

        XCTAssertEqual(root.callCount, 1)
        XCTAssertEqual(reader.counts.status, 1)
        XCTAssertEqual(
            reader.lastStatusURL?.standardizedFileURL,
            rootURL
                .appendingPathComponent("Atlas", isDirectory: true)
                .appendingPathComponent(
                    "atlas-local-snapshot.json",
                    isDirectory: false
                )
                .standardizedFileURL
        )
        XCTAssertEqual(reader.counts.read, 0)
    }

    func testValidSnapshotProjectsOnlyReviewedPublicValues() async throws {
        let rootURL = URL(fileURLWithPath: "/tmp/atlas-root", isDirectory: true)
        let root = RecordingRootProvider(outcome: .url(rootURL))
        let reader = RecordingSnapshotFileReader(
            status: .regularFile,
            data: try validSnapshotData()
        )
        let restorer = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: root,
            fileReader: reader
        )

        let restoredValue = try await restorer.restore()
        let restored = try XCTUnwrap(restoredValue)

        XCTAssertEqual(restored.savedAt, try date("2026-07-19T00:00:00Z"))
        XCTAssertEqual(restored.health.availability, .available)
        XCTAssertEqual(restored.health.openJobCount, 1)
        XCTAssertEqual(restored.jobs.map(\.id), [Self.publicJobID])
        XCTAssertEqual(restored.jobs[0].organization, "UNICEF")
        XCTAssertEqual(
            restored.jobs[0].closingDateText,
            "2026-08-01T12:34:56Z"
        )
        XCTAssertEqual(restored.sources.map(\.sourceID), ["public_source"])
        XCTAssertEqual(restored.updates[0].changedJobCount, 1)
        let labels = Set(
            Mirror(reflecting: restored).children.compactMap(\.label)
        )
        XCTAssertFalse(labels.contains("baseURL"))
        XCTAssertFalse(labels.contains("searchResponse"))
        XCTAssertFalse(String(reflecting: restored).contains("jobs.sqlite3"))
        XCTAssertEqual(reader.counts.read, 1)
    }

    func testSnapshotSourcesShareCandidateLabelProjectionWithLiveSources()
        async throws
    {
        let data = try snapshotData(
            replacingSourceID: "unicef_pageup",
            organization: "unicef_pageup"
        )
        let root = RecordingRootProvider(
            outcome: .url(
                URL(fileURLWithPath: "/tmp/atlas-root", isDirectory: true)
            )
        )
        let reader = RecordingSnapshotFileReader(
            status: .regularFile,
            data: data
        )
        let restorer = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: root,
            fileReader: reader
        )

        let restoredValue = try await restorer.restore()
        let restored = try XCTUnwrap(restoredValue)

        XCTAssertEqual(restored.sources.map(\.sourceID), ["unicef_pageup"])
        XCTAssertEqual(restored.sources.map(\.displayName), ["UNICEF"])
        XCTAssertFalse(
            restored.sources[0].displayName.contains("unicef_pageup")
        )
        XCTAssertEqual(reader.counts.read, 1)
        XCTAssertFalse(
            try XCTUnwrap(reader.lastStatusURL).path.contains("JobDetails")
        )

        let searchJob = try AtlasProductionPublicProjection.job(
            makeJob(
                id: Self.publicJobID,
                organization: "UNICEF PageUp"
            )
        )
        XCTAssertEqual(searchJob.organization, "UNICEF")
    }

    func testSnapshotJobsShareCandidateOrganizationProjectionWithLiveSearch()
        async throws
    {
        for item in [
            (raw: "unicef_pageup", expected: "UNICEF"),
            (raw: "wmo_oracle_hcm", expected: "WMO"),
            (
                raw: "world_food_programme_workday",
                expected: "World Food Programme"
            ),
        ] {
            let liveClient = RecordingPublicJobClient(
                searchResponses: [
                    try makeSearchResponse(
                        jobs: [
                            makeJob(
                                id: Self.publicJobID,
                                organization: item.raw
                            ),
                        ],
                        total: 1,
                        limit: 1,
                        offset: 0
                    ),
                ]
            )
            let liveAdapter = AtlasAPIClientPublicJobAdapter(client: liveClient)
            let live = try await liveAdapter.search(
                AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
            )
            let root = RecordingRootProvider(
                outcome: .url(
                    URL(fileURLWithPath: "/tmp/atlas-root", isDirectory: true)
                )
            )
            let reader = RecordingSnapshotFileReader(
                status: .regularFile,
                data: try snapshotData(
                    replacingPublicJobField: "organization",
                    with: item.raw
                )
            )
            let restorer = AtlasApplicationSupportPublicSnapshotRestorer(
                rootProvider: root,
                fileReader: reader
            )

            let restoredValue = try await restorer.restore()
            let restored = try XCTUnwrap(restoredValue)
            let liveJob = try XCTUnwrap(live.jobs.first)
            let restoredJob = try XCTUnwrap(restored.jobs.first)

            XCTAssertEqual(liveJob.organization, item.expected, item.raw)
            XCTAssertEqual(restoredJob.organization, liveJob.organization)
            XCTAssertEqual(restoredJob.id, liveJob.id)
            XCTAssertEqual(restoredJob.title, liveJob.title)
            XCTAssertEqual(restoredJob.location, liveJob.location)
            XCTAssertFalse(restoredJob.organization.contains(item.raw))
            XCTAssertEqual(root.callCount, 1)
            XCTAssertEqual(
                reader.counts,
                .init(status: 2, resolve: 2, read: 1)
            )
            XCTAssertFalse(
                try XCTUnwrap(reader.lastStatusURL).path.contains("JobDetails")
            )
        }
    }

    func testSnapshotProjectionMatchesAPIProjection() async throws {
        let job = makeJob(
            id: Self.publicJobID,
            organization: "UNICEF PageUp",
            closingDate: try date("2026-08-01T12:34:56Z")
        )
        let response = try makeSearchResponse(
            jobs: [job],
            total: 1,
            limit: 1,
            offset: 0
        )
        let apiClient = RecordingPublicJobClient(searchResponses: [response])
        let api = AtlasAPIClientPublicJobAdapter(client: apiClient)
        let apiResult = try await api.search(
            AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
        )

        let root = RecordingRootProvider(
            outcome: .url(URL(fileURLWithPath: "/tmp/atlas-root"))
        )
        let reader = RecordingSnapshotFileReader(
            status: .regularFile,
            data: try validSnapshotData()
        )
        let snapshot = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: root,
            fileReader: reader
        )
        let restoredValue = try await snapshot.restore()
        let restored = try XCTUnwrap(restoredValue)

        XCTAssertEqual(restored.jobs, apiResult.jobs)
    }

    func testSnapshotRejectsNormalizedPublicPlaceholderVariants()
        async throws
    {
        for (field, value) in [
            ("organization", "Unknown Organization"),
            ("organization", "unknown_organization"),
            ("dutyStation", "Location Not Classified"),
            ("dutyStation", "location_not_classified"),
            ("title", "UNTITLED VACANCY"),
        ] {
            let root = RecordingRootProvider(
                outcome: .url(
                    URL(fileURLWithPath: "/tmp/atlas-root", isDirectory: true)
                )
            )
            let reader = RecordingSnapshotFileReader(
                status: .regularFile,
                data: try snapshotData(
                    replacingPublicJobField: field,
                    with: value
                )
            )
            let restorer = AtlasApplicationSupportPublicSnapshotRestorer(
                rootProvider: root,
                fileReader: reader
            )

            await assertSnapshotError(.invalidSnapshot) {
                try await restorer.restore()
            }

            XCTAssertEqual(root.callCount, 1, field)
            XCTAssertEqual(reader.counts.status, 2, field)
            XCTAssertEqual(reader.counts.resolve, 2, field)
            XCTAssertEqual(reader.counts.read, 1, field)
            XCTAssertFalse(
                try XCTUnwrap(reader.lastStatusURL).path.contains("JobDetails"),
                field
            )
            XCTAssertFalse(
                String(describing: restorer).contains(value),
                field
            )
        }
    }

    func testSnapshotAcceptsCacheLimitAboveLiveSearchPageMaximum() async throws {
        let data = try snapshotData(
            replacingSearchLimit: 10_000
        )
        let restoredValue = try await snapshotRestorer(data: data).restore()
        let restored = try XCTUnwrap(restoredValue)

        XCTAssertEqual(restored.jobs.map(\.id), [Self.publicJobID])
    }

    func testSnapshotRejectsMalformedPrivateAndUnknownTopLevelKeys()
        async throws
    {
        for key in [
            "savedSearches",
            "savedJobs",
            "tracker",
            "applicationNotes",
            "profileSnippets",
            "draftMetadata",
            "generatedDocumentReferences",
            "futureUnknownKey",
        ] {
            let data = try snapshotData(addingTopLevelKey: key)
            let restorer = snapshotRestorer(data: data)
            await assertSnapshotError(.invalidSnapshot) {
                try await restorer.restore()
            }
        }

        let malformed = snapshotRestorer(
            data: Data("{not-json".utf8)
        )
        await assertSnapshotError(.invalidSnapshot) {
            try await malformed.restore()
        }
    }

    func testSnapshotRejectsUnsafeRootSymlinkEscapeAndNonRegularFile()
        async
    {
        let rootFailure = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: RecordingRootProvider(outcome: .failure),
            fileReader: RecordingSnapshotFileReader(status: .missing)
        )
        await assertSnapshotError(.unavailable) {
            try await rootFailure.restore()
        }

        let rootURL = URL(fileURLWithPath: "/tmp/atlas-root", isDirectory: true)
        let root = RecordingRootProvider(outcome: .url(rootURL))
        let escapedReader = RecordingSnapshotFileReader(
            status: .regularFile,
            data: Data(),
            resolvedCandidate: URL(
                fileURLWithPath: "/tmp/outside/\(Self.pathSentinel).json"
            )
        )
        let escaped = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: root,
            fileReader: escapedReader
        )
        await assertSnapshotError(.invalidSnapshot) {
            try await escaped.restore()
        }

        let nonRegular = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: root,
            fileReader: RecordingSnapshotFileReader(status: .nonRegular)
        )
        await assertSnapshotError(.invalidSnapshot) {
            try await nonRegular.restore()
        }

        let rootSlash = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: RecordingRootProvider(
                outcome: .url(URL(fileURLWithPath: "/"))
            ),
            fileReader: RecordingSnapshotFileReader(status: .missing)
        )
        await assertSnapshotError(.invalidSnapshot) {
            try await rootSlash.restore()
        }
    }

    func testSnapshotReadFailureMapsUnavailableWithoutPathLeak() async {
        let reader = RecordingSnapshotFileReader(
            status: .regularFile,
            readFailure: true
        )
        let restorer = AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: RecordingRootProvider(
                outcome: .url(
                    URL(
                        fileURLWithPath: "/tmp/\(Self.pathSentinel)",
                        isDirectory: true
                    )
                )
            ),
            fileReader: reader
        )

        await assertSnapshotError(.unavailable) {
            try await restorer.restore()
        }
        XCTAssertFalse(
            String(reflecting: AtlasPublicSnapshotRestoreError.unavailable)
                .contains(Self.pathSentinel)
        )
    }

    func testSnapshotSourceIsRestoreOnlyAndExcludesDetailCache() throws {
        let source = try source("AtlasPublicSnapshotRestorer.swift")

        for forbidden in [
            "AtlasLocalCache",
            "JobDetails",
            "prepareDetail",
            "loadDetail",
            "saveDetail",
            "copyExistingDetail",
            "cachedDetail",
            "missingDetail",
            "write(",
            "createDirectory",
            "removeItem",
            "UserDefaults",
            "SwiftUI",
            "@main",
            "URLSession",
            "Keychain",
            "SecItem",
            "func save",
            "func delete",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        XCTAssertTrue(source.contains("atlas-local-snapshot.json"))
        XCTAssertTrue(source.contains("func restore()"))
    }

    // MARK: - Vault-selection registry

    func testRegistryConstructionInvokesNothingAndUsesDistinctFixedMetadata()
        async
    {
        let client = RecordingSelectionKeychainClient()
        let registry = AtlasKeychainVaultSelectionRegistry(client: client)

        XCTAssertEqual(client.counts, .zero)
        XCTAssertNotEqual(
            AtlasKeychainVaultSelectionRegistry<
                RecordingSelectionKeychainClient
            >.registryService,
            AtlasKeychainVaultKeyStore<
                RecordingSelectionKeychainClient
            >.defaultService
        )
        XCTAssertEqual(
            String(describing: registry),
            "AtlasKeychainVaultSelectionRegistry(<redacted>)"
        )
    }

    func testRegistryMissingItemReturnsNone() async throws {
        let client = RecordingSelectionKeychainClient()
        let registry = AtlasKeychainVaultSelectionRegistry(client: client)

        let selection = try await registry.selectVaultID()
        XCTAssertEqual(selection, .none)
        XCTAssertEqual(client.counts, .init(copy: 1))
        XCTAssertEqual(
            client.copiedQueries,
            [
                AtlasKeychainQuery(
                    service: type(of: registry).registryService,
                    account: type(of: registry).registryAccount
                ),
            ]
        )
    }

    func testRegistryLoadsValidatedVersionedSelection() async throws {
        let client = RecordingSelectionKeychainClient()
        client.copyResult = AtlasKeychainCopyResult(
            status: errSecSuccess,
            valueData: try registryData(vaultID: Self.vaultID)
        )
        let registry = AtlasKeychainVaultSelectionRegistry(client: client)

        let selection = try await registry.selectVaultID()

        guard case let .selected(selected) = selection else {
            return XCTFail("Expected one selected vault")
        }
        XCTAssertEqual(selected.vaultID, Self.vaultID)
        XCTAssertFalse(String(reflecting: selection).contains(Self.vaultID))
    }

    func testRegistryRejectsMalformedUnsupportedAndInvalidEmbeddedID()
        async
    {
        let cases: [Data] = [
            Data("{malformed".utf8),
            (try? registryData(
                format: "wrong-format",
                version: 1,
                vaultID: Self.vaultID
            )) ?? Data(),
            (try? registryData(
                format: "atlas-vault-selection",
                version: 2,
                vaultID: Self.vaultID
            )) ?? Data(),
            (try? registryData(
                format: "atlas-vault-selection",
                version: 1,
                vaultID: "../invalid"
            )) ?? Data(),
            Data(
                """
                {
                  "format": "atlas-vault-selection",
                  "version": 1,
                  "vault_id": "\(Self.vaultID)",
                  "unexpected": "FAKE_REGISTRY_FIELD"
                }
                """.utf8
            ),
        ]

        for data in cases {
            let client = RecordingSelectionKeychainClient()
            client.copyResult = AtlasKeychainCopyResult(
                status: errSecSuccess,
                valueData: data
            )
            let registry = AtlasKeychainVaultSelectionRegistry(client: client)
            await assertSelectionError(.invalidRegistry) {
                try await registry.selectVaultID()
            }
        }

        let missingDataClient = RecordingSelectionKeychainClient()
        missingDataClient.copyResult = AtlasKeychainCopyResult(
            status: errSecSuccess,
            valueData: nil
        )
        let missingDataRegistry = AtlasKeychainVaultSelectionRegistry(
            client: missingDataClient
        )
        await assertSelectionError(.invalidRegistry) {
            try await missingDataRegistry.selectVaultID()
        }
    }

    func testRegistryReadFailureMapsUnavailableWithoutRawStatus() async {
        let client = RecordingSelectionKeychainClient()
        client.copyResult = AtlasKeychainCopyResult(
            status: errSecNotAvailable,
            valueData: nil
        )
        let registry = AtlasKeychainVaultSelectionRegistry(client: client)

        await assertSelectionError(.unavailable) {
            try await registry.selectVaultID()
        }
        XCTAssertEqual(String(describing: AtlasVaultIDSelectionError.unavailable), "unavailable")
        XCTAssertFalse(
            String(reflecting: AtlasVaultIDSelectionError.unavailable)
                .contains(String(errSecNotAvailable))
        )
    }

    func testRegistryStoreUsesValueDataOnlyAndDeviceLocalAccessibility()
        async throws
    {
        let client = RecordingSelectionKeychainClient()
        let registry = AtlasKeychainVaultSelectionRegistry(client: client)
        let selected = try AtlasSelectedVaultID(validating: Self.vaultID)

        try await registry.storeSelection(selected)

        let item = try XCTUnwrap(client.addedItems.first)
        XCTAssertEqual(item.service, type(of: registry).registryService)
        XCTAssertEqual(item.account, type(of: registry).registryAccount)
        XCTAssertEqual(item.accessibility, .afterFirstUnlockThisDeviceOnly)
        XCTAssertFalse(item.service.contains(Self.vaultID))
        XCTAssertFalse(item.account.contains(Self.vaultID))
        XCTAssertFalse(
            item.service.contains(
                AtlasKeychainVaultKeyStore<
                    RecordingSelectionKeychainClient
                >.defaultService
            )
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: item.valueData)
                as? [String: Any]
        )
        XCTAssertEqual(object["format"] as? String, "atlas-vault-selection")
        XCTAssertEqual(object["version"] as? Int, 1)
        XCTAssertEqual(object["vault_id"] as? String, Self.vaultID)
        XCTAssertEqual(Set(object.keys), ["format", "version", "vault_id"])
        let serialized = String(data: item.valueData, encoding: .utf8) ?? ""
        for forbidden in [
            "vault_key",
            "passphrase",
            "recovery",
            "path",
            "label",
            "timestamp",
            "record",
        ] {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testRegistryDuplicateStoreUpdatesSameFixedQuery() async throws {
        let client = RecordingSelectionKeychainClient()
        client.forcedAddStatuses = [errSecDuplicateItem]
        let registry = AtlasKeychainVaultSelectionRegistry(client: client)
        let selected = try AtlasSelectedVaultID(validating: Self.vaultID)

        try await registry.storeSelection(selected)

        XCTAssertEqual(client.addedItems.count, 1)
        XCTAssertEqual(client.updatedItems.count, 1)
        XCTAssertEqual(
            client.updatedItems[0].query,
            AtlasKeychainQuery(
                service: type(of: registry).registryService,
                account: type(of: registry).registryAccount
            )
        )
        XCTAssertEqual(
            client.updatedItems[0].attributes.valueData,
            client.addedItems[0].valueData
        )
    }

    func testRegistryClearDeletesAndMissingIsSuccess() async throws {
        let client = RecordingSelectionKeychainClient()
        client.forcedDeleteStatuses = [errSecSuccess, errSecItemNotFound]
        let registry = AtlasKeychainVaultSelectionRegistry(client: client)

        try await registry.clearSelection()
        try await registry.clearSelection()

        XCTAssertEqual(client.deletedQueries.count, 2)
        XCTAssertTrue(
            client.deletedQueries.allSatisfy {
                $0.service == type(of: registry).registryService
                    && $0.account == type(of: registry).registryAccount
            }
        )
    }

    func testRegistryMutationFailuresMapUnavailableWithoutRawStatus()
        async throws
    {
        let selected = try AtlasSelectedVaultID(validating: Self.vaultID)

        let addClient = RecordingSelectionKeychainClient()
        addClient.forcedAddStatuses = [errSecNotAvailable]
        let addRegistry = AtlasKeychainVaultSelectionRegistry(client: addClient)
        await assertSelectionError(.unavailable) {
            try await addRegistry.storeSelection(selected)
        }

        let updateClient = RecordingSelectionKeychainClient()
        updateClient.forcedAddStatuses = [errSecDuplicateItem]
        updateClient.forcedUpdateStatuses = [errSecNotAvailable]
        let updateRegistry = AtlasKeychainVaultSelectionRegistry(
            client: updateClient
        )
        await assertSelectionError(.unavailable) {
            try await updateRegistry.storeSelection(selected)
        }

        let deleteClient = RecordingSelectionKeychainClient()
        deleteClient.forcedDeleteStatuses = [errSecNotAvailable]
        let deleteRegistry = AtlasKeychainVaultSelectionRegistry(
            client: deleteClient
        )
        await assertSelectionError(.unavailable) {
            try await deleteRegistry.clearSelection()
        }
    }

    func testRegistryEncodingFailureMapsUnavailableBeforeKeychainCall()
        async throws
    {
        let client = RecordingSelectionKeychainClient()
        let registry = AtlasKeychainVaultSelectionRegistry(
            client: client,
            envelopeEncoder: FailingSelectionEnvelopeEncoder()
        )
        let selected = try AtlasSelectedVaultID(validating: Self.vaultID)

        await assertSelectionError(.unavailable) {
            try await registry.storeSelection(selected)
        }
        XCTAssertEqual(client.counts, .zero)
    }

    func testRegistrySourceHasNoImplicitSelectionOrAlternateStorage() throws {
        let source = try source("AtlasVaultSelectionRegistry.swift")

        for forbidden in [
            "UserDefaults",
            "@AppStorage",
            "@SceneStorage",
            "FileManager",
            "contentsOfDirectory",
            "enumerator",
            "URLSession",
            "SwiftUI",
            "@main",
            "loadVaultKey",
            "saveVaultKey",
            "deleteVaultKey",
            "CryptoKit",
            "AtlasVaultRecordCrypto",
            "selectAll",
            "listVault",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        XCTAssertFalse(source.contains("SecItemAdd"))
        XCTAssertFalse(source.contains("SecItemCopyMatching"))
        XCTAssertFalse(source.contains("SecItemUpdate"))
        XCTAssertFalse(source.contains("SecItemDelete"))
    }

    func testExpandedSelectionErrorsAreFixedAndRedacted() {
        XCTAssertEqual(
            String(describing: AtlasVaultIDSelectionError.invalidVaultID),
            "invalidVaultID"
        )
        XCTAssertEqual(
            String(describing: AtlasVaultIDSelectionError.unavailable),
            "unavailable"
        )
        XCTAssertEqual(
            String(describing: AtlasVaultIDSelectionError.invalidRegistry),
            "invalidRegistry"
        )
        for error in [
            AtlasVaultIDSelectionError.invalidVaultID,
            .unavailable,
            .invalidRegistry,
        ] {
            XCTAssertFalse(String(reflecting: error).contains(Self.vaultID))
            XCTAssertFalse(
                String(reflecting: error).contains(String(errSecNotAvailable))
            )
        }
    }

    // MARK: - Scope and artifacts

    func testNewProductionSourcesHaveNoHostUIOrLaterPhaseBehavior() throws {
        let combined = try [
            "AtlasPublicJobAPIAdapter.swift",
            "AtlasPublicSnapshotRestorer.swift",
            "AtlasVaultSelectionRegistry.swift",
        ].map(source).joined(separator: "\n")

        for forbidden in [
            "AtlasRootView",
            "refreshSidebarData",
            "SearchViewModel",
            "AtlasIOSHostApp",
            "NavigationStack",
            "NavigationLink",
            "LocalAuthentication",
            "LAContext",
            "migration",
            "cloud sync",
            "AtlasVaultProductionHosting",
            "AtlasVaultProductionHostFactory",
            "passphrase",
            "recovery-key provider",
        ] {
            XCTAssertFalse(combined.contains(forbidden), forbidden)
        }
    }

    func testPhaseFileSetIsExactlyTheSixAllowedPaths() throws {
        let root = try repositoryRoot()
        let expected = Set([
            "docs/architecture/phase2d55_concrete_public_adapters_and_vault_selection.md",
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionHostContracts.swift",
            "apps/apple/Sources/AtlasUI/AtlasPublicJobAPIAdapter.swift",
            "apps/apple/Sources/AtlasUI/AtlasPublicSnapshotRestorer.swift",
            "apps/apple/Sources/AtlasUI/AtlasVaultSelectionRegistry.swift",
            "apps/apple/Tests/AtlasUITests/AtlasVaultProductionAdaptersTests.swift",
        ])
        for path in expected {
            XCTAssertTrue(
                FileManager.default.fileExists(
                    atPath: root.appendingPathComponent(path).path
                ),
                path
            )
        }
        let phaseNamedFiles = try allRepositoryFiles(root: root).filter {
            $0.lastPathComponent.lowercased().contains("phase2d55")
                || $0.lastPathComponent == "AtlasPublicJobAPIAdapter.swift"
                || $0.lastPathComponent == "AtlasPublicSnapshotRestorer.swift"
                || $0.lastPathComponent == "AtlasVaultSelectionRegistry.swift"
                || $0.lastPathComponent
                    == "AtlasVaultProductionAdaptersTests.swift"
        }
        let relative = Set(
            phaseNamedFiles.map {
                $0.path.replacingOccurrences(
                    of: root.path + "/",
                    with: ""
                )
            }
        )
        XCTAssertEqual(relative, expected.subtracting([
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionHostContracts.swift",
        ]))
    }

    func testNoAtlasVaultOrReviewEnvironmentArtifactExists() throws {
        let root = try repositoryRoot()
        for url in try allRepositoryFiles(root: root) {
            XCTAssertNotEqual(url.pathExtension, "atlasvault", url.path)
            XCTAssertNotEqual(url.lastPathComponent, ".venv-review", url.path)
        }
    }

    func testRepositoryRootAcceptsTrackedMarkerWithoutGitMetadata() throws {
        let expectedRoot = URL(fileURLWithPath: "/FAKE_SOURCE_ARCHIVE")
        let nested = expectedRoot
            .appendingPathComponent("apps/apple/Tests/AtlasUITests")
        let root = try repositoryRoot(startingAt: nested) { path in
            path == expectedRoot.appendingPathComponent("AGENTS.md").path
        }

        XCTAssertEqual(
            root.standardizedFileURL.path,
            expectedRoot.standardizedFileURL.path
        )
    }

    func testRepositoryEnumerationSkipsIgnoredLocalDirectories() {
        for directory in [
            ".git",
            "private",
            "tmp",
            "logs",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".build",
            ".swiftpm",
            "DerivedData",
        ] {
            XCTAssertTrue(
                shouldSkipRepositoryDirectory(
                    URL(
                        fileURLWithPath: "/FAKE_REPOSITORY/\(directory)",
                        isDirectory: true
                    )
                ),
                directory
            )
        }
        XCTAssertFalse(
            shouldSkipRepositoryDirectory(
                URL(
                    fileURLWithPath: "/FAKE_REPOSITORY/apps",
                    isDirectory: true
                )
            )
        )
    }

    // MARK: - Helpers

    private func assertPublicError<T>(
        _ expected: AtlasPublicJobServiceError,
        operation: () async throws -> T
    ) async {
        do {
            _ = try await operation()
            XCTFail("Expected \(expected)")
        } catch {
            XCTAssertEqual(error as? AtlasPublicJobServiceError, expected)
            XCTAssertFalse(String(reflecting: error).contains(Self.bodySentinel))
            XCTAssertFalse(
                String(reflecting: error).contains(Self.transportSentinel)
            )
        }
    }

    private func assertSnapshotError<T>(
        _ expected: AtlasPublicSnapshotRestoreError,
        operation: () async throws -> T
    ) async {
        do {
            _ = try await operation()
            XCTFail("Expected \(expected)")
        } catch {
            XCTAssertEqual(error as? AtlasPublicSnapshotRestoreError, expected)
            XCTAssertFalse(String(reflecting: error).contains(Self.pathSentinel))
        }
    }

    private func assertSearchRejectsBeforeAuthorization(
        _ response: AtlasSearchResponse,
        rejectedValue: String
    ) async throws {
        let client = RecordingPublicJobClient(
            searchResponses: [response],
            details: [
                Self.publicJobID: makeDetail(id: Self.publicJobID),
            ]
        )
        let adapter = AtlasAPIClientPublicJobAdapter(client: client)
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.publicJobID
        )

        await assertPublicError(.invalidResponse) {
            try await adapter.search(
                AtlasPublicJobSearchRequest(query: "", limit: 1, offset: 0)
            )
        }
        await assertPublicError(.invalidRequest) {
            try await adapter.detail(for: reference)
        }

        let authorizedCount = await adapter.authorizedReferenceCountForTesting()
        let counts = await client.counts()
        XCTAssertEqual(authorizedCount, 0, rejectedValue)
        XCTAssertEqual(counts, .init(search: 1), rejectedValue)
        XCTAssertFalse(String(describing: adapter).contains(rejectedValue))
        XCTAssertFalse(
            String(reflecting: AtlasPublicJobServiceError.invalidResponse)
                .contains(rejectedValue)
        )
    }

    private func assertSelectionError<T>(
        _ expected: AtlasVaultIDSelectionError,
        operation: () async throws -> T
    ) async {
        do {
            _ = try await operation()
            XCTFail("Expected \(expected)")
        } catch {
            XCTAssertEqual(error as? AtlasVaultIDSelectionError, expected)
            XCTAssertFalse(String(reflecting: error).contains(Self.vaultID))
            XCTAssertFalse(
                String(reflecting: error).contains(String(errSecNotAvailable))
            )
        }
    }

    private func snapshotRestorer(
        data: Data
    ) -> AtlasApplicationSupportPublicSnapshotRestorer {
        AtlasApplicationSupportPublicSnapshotRestorer(
            rootProvider: RecordingRootProvider(
                outcome: .url(
                    URL(fileURLWithPath: "/tmp/atlas-root", isDirectory: true)
                )
            ),
            fileReader: RecordingSnapshotFileReader(
                status: .regularFile,
                data: data
            )
        )
    }

    private func validSnapshotData() throws -> Data {
        Data(
            """
            {
              "savedAt": "2026-07-19T00:00:00Z",
              "baseURL": "http://127.0.0.1:8765",
              "health": {
                "status": "ok",
                "db_path": "/FAKE_DB_PATH_DO_NOT_PROJECT/jobs.sqlite3",
                "schema_version": "FAKE_SCHEMA_DO_NOT_PROJECT",
                "open_jobs": 1,
                "enabled_sources": 1,
                "last_sync_at": "2026-07-19T00:00:00Z"
              },
              "searchResponse": {
                "total": 1,
                "limit": 1,
                "offset": 0,
                "facets": {
                  "organizations": {
                    "UNICEF": 1
                  }
                },
                "facet_labels": {},
                "unclassified_count": 0,
                "results": [
                  {
                    "jobKey": "\(Self.publicJobID)",
                    "title": "Public role",
                    "organization": "UNICEF PageUp",
                    "sourceID": "public_source",
                    "dutyStation": "Tokyo, Japan",
                    "gradeCode": "P-3",
                    "contractLabel": "Fixed Term",
                    "workModality": "Onsite",
                    "closingDate": "2026-08-01T12:34:56Z",
                    "needsReview": false,
                    "score": 0.99,
                    "scoreReasons": ["FAKE_SCORE_REASON_DO_NOT_PROJECT"],
                    "matchSummary": "FAKE_MATCH_SUMMARY_DO_NOT_PROJECT",
                    "description": "Public vacancy description",
                    "status": "open"
                  }
                ]
              },
              "sources": [
                {
                  "source_id": "public_source",
                  "organization": "UNICEF",
                  "total_jobs": 1,
                  "open_jobs": 1,
                  "health_status": "ok"
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
                  "observed_at": "2026-07-19T00:00:00Z"
                }
              ]
            }
            """.utf8
        )
    }

    private func snapshotData(addingTopLevelKey key: String) throws -> Data {
        var object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: validSnapshotData())
                as? [String: Any]
        )
        object[key] = ["FAKE_PRIVATE_SENTINEL_DO_NOT_DECODE": true]
        return try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys]
        )
    }

    private func snapshotData(replacingSearchLimit limit: Int) throws -> Data {
        var object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: validSnapshotData())
                as? [String: Any]
        )
        var search = try XCTUnwrap(
            object["searchResponse"] as? [String: Any]
        )
        search["limit"] = limit
        object["searchResponse"] = search
        return try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys]
        )
    }

    private func snapshotData(
        replacingSourceID sourceID: String,
        organization: String
    ) throws -> Data {
        var object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: validSnapshotData())
                as? [String: Any]
        )
        object["sources"] = [
            [
                "source_id": sourceID,
                "organization": organization,
                "total_jobs": 1,
                "open_jobs": 1,
                "health_status": "ok",
            ],
        ]
        return try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys]
        )
    }

    private func snapshotData(
        replacingPublicJobField field: String,
        with value: String
    ) throws -> Data {
        var object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: validSnapshotData())
                as? [String: Any]
        )
        var search = try XCTUnwrap(
            object["searchResponse"] as? [String: Any]
        )
        var results = try XCTUnwrap(search["results"] as? [[String: Any]])
        var row = try XCTUnwrap(results.first)
        row[field] = value
        results[0] = row
        search["results"] = results
        object["searchResponse"] = search
        return try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys]
        )
    }

    private func rawSearchResponse(
        omittingPublicField field: String
    ) throws -> AtlasSearchResponse {
        var row: [String: Any] = [
            "job_key": Self.publicJobID,
            "title": "Public role",
            "organization": "UNICEF",
            "duty_station": "Tokyo, Japan",
            "status": "open",
        ]
        row.removeValue(forKey: field)
        let data = try JSONSerialization.data(
            withJSONObject: [
                "total": 1,
                "limit": 1,
                "offset": 0,
                "results": [row],
            ],
            options: [.sortedKeys]
        )
        return try JSONDecoder().decode(AtlasSearchResponse.self, from: data)
    }

    private func rawSearchResponse(
        replacingPublicField field: String,
        with value: String
    ) throws -> AtlasSearchResponse {
        var row: [String: Any] = [
            "job_key": Self.publicJobID,
            "title": "Public role",
            "organization": "UNICEF",
            "duty_station": "Tokyo, Japan",
            "status": "open",
        ]
        row[field] = value
        let data = try JSONSerialization.data(
            withJSONObject: [
                "total": 1,
                "limit": 1,
                "offset": 0,
                "results": [row],
            ],
            options: [.sortedKeys]
        )
        return try JSONDecoder().decode(AtlasSearchResponse.self, from: data)
    }

    private func registryData(
        format: String = "atlas-vault-selection",
        version: Int = 1,
        vaultID: String
    ) throws -> Data {
        try JSONSerialization.data(
            withJSONObject: [
                "format": format,
                "version": version,
                "vault_id": vaultID,
            ],
            options: [.sortedKeys]
        )
    }

    private func source(_ fileName: String) throws -> String {
        let root = try repositoryRoot()
        let url = root
            .appendingPathComponent("apps/apple/Sources/AtlasUI")
            .appendingPathComponent(fileName)
        return try String(contentsOf: url, encoding: .utf8)
    }

    private func repositoryRoot(
        startingAt start: URL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent(),
        fileExists: (String) -> Bool = {
            FileManager.default.fileExists(atPath: $0)
        }
    ) throws -> URL {
        var candidate = start
        while candidate.path != "/" {
            let hasRootMarker = [".git", "AGENTS.md"].contains { marker in
                fileExists(candidate.appendingPathComponent(marker).path)
            }
            if hasRootMarker {
                return candidate
            }
            candidate.deleteLastPathComponent()
        }
        throw NSError(
            domain: "AtlasVaultProductionAdaptersTests",
            code: 1,
            userInfo: [
                NSLocalizedDescriptionKey: "Could not locate repository root",
            ]
        )
    }

    private func allRepositoryFiles(root: URL) throws -> [URL] {
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: []
        ) else {
            return []
        }
        var files: [URL] = []
        for case let url as URL in enumerator {
            if shouldSkipRepositoryDirectory(url) {
                if url.hasDirectoryPath {
                    enumerator.skipDescendants()
                }
                continue
            }
            files.append(url)
        }
        return files
    }

    private func shouldSkipRepositoryDirectory(_ url: URL) -> Bool {
        if url.lastPathComponent == ".git" {
            return true
        }
        return url.hasDirectoryPath
            && Self.repositoryScanIgnoredDirectoryNames.contains(
                url.lastPathComponent
            )
    }
}

// MARK: - Public API fake

private actor RecordingPublicJobClient: AtlasPublicJobAPIClient {
    enum Failure: Sendable {
        case none
        case transport(String)
        case http(String)
        case invalidResponse
        case decoding(String)
    }

    struct Counts: Equatable, Sendable {
        var health = 0
        var search = 0
        var detail = 0
        var sources = 0
        var updates = 0

        static let zero = Counts()
    }

    private var callCounts = Counts()
    private var healthValue: AtlasHealthSummary
    private var queuedSearchResponses: [AtlasSearchResponse]
    private var detailValues: [String: AtlasJobDetail]
    private var queuedSourceResponses: [AtlasSourcesResponse]
    private var queuedUpdateResponses: [AtlasUpdatesResponse]
    private var recordedSearchRequests: [AtlasSearchRequest] = []
    private let healthFailure: Failure
    private let searchFailure: Failure

    init(
        health: AtlasHealthSummary = makeHealth(status: "ok"),
        searchResponses: [AtlasSearchResponse] = [],
        details: [String: AtlasJobDetail] = [:],
        sourceResponses: [AtlasSourcesResponse] = [],
        updateResponses: [AtlasUpdatesResponse] = [],
        healthFailure: Failure = .none,
        searchFailure: Failure = .none
    ) {
        healthValue = health
        queuedSearchResponses = searchResponses
        detailValues = details
        queuedSourceResponses = sourceResponses
        queuedUpdateResponses = updateResponses
        self.healthFailure = healthFailure
        self.searchFailure = searchFailure
    }

    func health() async throws -> AtlasHealthSummary {
        callCounts.health += 1
        try Self.raise(healthFailure)
        return healthValue
    }

    func search(_ request: AtlasSearchRequest) async throws
        -> AtlasSearchResponse
    {
        callCounts.search += 1
        recordedSearchRequests.append(request)
        try Self.raise(searchFailure)
        guard !queuedSearchResponses.isEmpty else {
            throw AtlasAPIError.invalidResponse
        }
        return queuedSearchResponses.removeFirst()
    }

    func jobDetail(_ jobKey: String) async throws -> AtlasJobDetail {
        callCounts.detail += 1
        guard let detail = detailValues[jobKey] else {
            throw AtlasAPIError.invalidResponse
        }
        return detail
    }

    func sources() async throws -> AtlasSourcesResponse {
        callCounts.sources += 1
        guard !queuedSourceResponses.isEmpty else {
            return AtlasSourcesResponse(sources: [])
        }
        return queuedSourceResponses.removeFirst()
    }

    func updates() async throws -> AtlasUpdatesResponse {
        callCounts.updates += 1
        guard !queuedUpdateResponses.isEmpty else {
            return AtlasUpdatesResponse(recentSourceRuns: [])
        }
        return queuedUpdateResponses.removeFirst()
    }

    func counts() -> Counts {
        callCounts
    }

    func lastSearchRequest() -> AtlasSearchRequest? {
        recordedSearchRequests.last
    }

    private static func raise(_ failure: Failure) throws {
        switch failure {
        case .none:
            return
        case let .transport(sentinel):
            throw AtlasAPIError.transport(sentinel)
        case let .http(sentinel):
            throw AtlasAPIError.httpStatus(500, sentinel)
        case .invalidResponse:
            throw AtlasAPIError.invalidResponse
        case let .decoding(sentinel):
            throw DecodingError.dataCorrupted(
                .init(codingPath: [], debugDescription: sentinel)
            )
        }
    }
}

private func makeHealth(
    status: String,
    dbPath: String? = nil,
    schemaVersion: String? = nil,
    openJobs: Int? = nil,
    enabledSources: Int? = nil,
    lastSyncAt: String? = nil
) -> AtlasHealthSummary {
    AtlasHealthSummary(
        status: status,
        dbPath: dbPath,
        schemaVersion: schemaVersion,
        openJobs: openJobs,
        enabledSources: enabledSources,
        lastSyncAt: lastSyncAt
    )
}

private func makeSourceSummary(
    sourceID: String,
    organization: String,
    totalJobs: Int = 1,
    openJobs: Int = 1,
    healthStatus: String? = "ok"
) -> AtlasSourceSummary {
    AtlasSourceSummary(
        sourceID: sourceID,
        organization: organization,
        totalJobs: totalJobs,
        openJobs: openJobs,
        lastSeenAt: nil,
        healthStatus: healthStatus,
        observedAt: nil,
        detailAttempted: nil,
        detailFailed: nil,
        missingTransitionAllowed: nil
    )
}

private func makeJob(
    id: String,
    title: String = "Public role",
    organization: String = "UNICEF",
    location: String = "Tokyo, Japan",
    closingDate: Date? = nil,
    score: Double? = nil,
    description: String = "Public description",
    status: String = "open"
) -> JobSearchResult {
    JobSearchResult(
        jobKey: id,
        title: title,
        organization: organization,
        sourceID: "public_source",
        dutyStation: location,
        gradeCode: "P-3",
        contractLabel: "Fixed Term",
        workModality: "Onsite",
        closingDate: closingDate,
        needsReview: false,
        locationConfidence: 0.95,
        gradeConfidence: 0.9,
        score: score,
        scoreReasons: ["FAKE_SCORE_REASON_DO_NOT_PROJECT"],
        matchSummary: "FAKE_MATCH_SUMMARY_DO_NOT_PROJECT",
        description: description,
        status: status
    )
}

private func makeSearchResponse(
    jobs: [JobSearchResult],
    total: Int,
    limit: Int,
    offset: Int
) throws -> AtlasSearchResponse {
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    let jobsData = try encoder.encode(jobs)
    let jobsObject = try JSONSerialization.jsonObject(with: jobsData)
    let data = try JSONSerialization.data(
        withJSONObject: [
            "total": total,
            "limit": limit,
            "offset": offset,
            "results": jobsObject,
            "facets": [:],
            "facet_labels": [:],
            "unclassified_count": 0,
        ]
    )
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    return try decoder.decode(AtlasSearchResponse.self, from: data)
}

private func makeDetail(
    id: String,
    title: String = "Public role",
    description: String? = "Public detail",
    displaySections: [AtlasDetailSection] = []
) -> AtlasJobDetail {
    AtlasJobDetail(
        jobKey: id,
        title: title,
        description: description,
        status: "open",
        closingDate: nil,
        closesAtLocal: nil,
        closesTimezone: nil,
        applyURL: nil,
        sourceURL: nil,
        deadlineInfo: nil,
        displaySections: displaySections
    )
}

private func date(_ value: String) throws -> Date {
    let formatter = ISO8601DateFormatter()
    guard let parsed = formatter.date(from: value) else {
        throw NSError(
            domain: "AtlasVaultProductionAdaptersTests",
            code: 2,
            userInfo: [NSLocalizedDescriptionKey: "Invalid test date"]
        )
    }
    return parsed
}

// MARK: - Snapshot fakes

private final class RecordingRootProvider:
    AtlasVaultRootDirectoryProviding,
    @unchecked Sendable
{
    enum Outcome: Sendable {
        case url(URL)
        case failure
    }

    let outcome: Outcome
    private(set) var callCount = 0

    init(outcome: Outcome) {
        self.outcome = outcome
    }

    func rootDirectory() throws -> URL {
        callCount += 1
        switch outcome {
        case let .url(url):
            return url
        case .failure:
            throw SnapshotTestError.unavailable
        }
    }
}

private final class RecordingSnapshotFileReader:
    AtlasPublicSnapshotFileReading,
    @unchecked Sendable
{
    struct Counts: Equatable {
        var status = 0
        var resolve = 0
        var read = 0

        static let zero = Counts()
    }

    private let fileStatus: AtlasPublicSnapshotFileStatus
    private let data: Data
    private let resolvedCandidate: URL?
    private let readFailure: Bool
    private(set) var counts = Counts()
    private(set) var lastStatusURL: URL?

    init(
        status: AtlasPublicSnapshotFileStatus,
        data: Data = Data(),
        resolvedCandidate: URL? = nil,
        readFailure: Bool = false
    ) {
        fileStatus = status
        self.data = data
        self.resolvedCandidate = resolvedCandidate
        self.readFailure = readFailure
    }

    func status(
        at url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> AtlasPublicSnapshotFileStatus {
        counts.status += 1
        lastStatusURL = url
        return fileStatus
    }

    func resolvedURL(
        for url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> URL {
        counts.resolve += 1
        if url.lastPathComponent == "atlas-local-snapshot.json",
           let resolvedCandidate {
            return resolvedCandidate
        }
        return url.standardizedFileURL
    }

    func read(
        from url: URL
    ) throws(AtlasPublicSnapshotFileReadError) -> Data {
        counts.read += 1
        if readFailure {
            throw .unavailable
        }
        return data
    }
}

private enum SnapshotTestError: Error, Sendable {
    case unavailable
}

// MARK: - Keychain fake

private struct FailingSelectionEnvelopeEncoder:
    AtlasVaultSelectionEnvelopeEncoding
{
    func encode(vaultID: String) throws -> Data {
        throw SnapshotTestError.unavailable
    }
}

private final class RecordingSelectionKeychainClient:
    AtlasKeychainClient,
    @unchecked Sendable
{
    struct Counts: Equatable {
        var add = 0
        var copy = 0
        var update = 0
        var delete = 0

        static let zero = Counts()
    }

    struct UpdateCall: Equatable {
        let query: AtlasKeychainQuery
        let attributes: AtlasKeychainUpdate
    }

    var copyResult = AtlasKeychainCopyResult(
        status: errSecItemNotFound,
        valueData: nil
    )
    var forcedAddStatuses: [OSStatus] = []
    var forcedUpdateStatuses: [OSStatus] = []
    var forcedDeleteStatuses: [OSStatus] = []
    private(set) var counts = Counts()
    private(set) var addedItems: [AtlasKeychainItem] = []
    private(set) var copiedQueries: [AtlasKeychainQuery] = []
    private(set) var updatedItems: [UpdateCall] = []
    private(set) var deletedQueries: [AtlasKeychainQuery] = []

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        counts.add += 1
        addedItems.append(item)
        if !forcedAddStatuses.isEmpty {
            return forcedAddStatuses.removeFirst()
        }
        return errSecSuccess
    }

    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        counts.copy += 1
        copiedQueries.append(query)
        return copyResult
    }

    func update(
        _ query: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        counts.update += 1
        updatedItems.append(.init(query: query, attributes: attributes))
        if !forcedUpdateStatuses.isEmpty {
            return forcedUpdateStatuses.removeFirst()
        }
        return errSecSuccess
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        counts.delete += 1
        deletedQueries.append(query)
        if !forcedDeleteStatuses.isEmpty {
            return forcedDeleteStatuses.removeFirst()
        }
        return errSecSuccess
    }
}
