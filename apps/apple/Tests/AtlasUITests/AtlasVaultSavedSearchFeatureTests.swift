import Foundation
import Synchronization
import XCTest
@testable import AtlasUI

final class AtlasVaultSavedSearchFeatureTests: XCTestCase {
    private static let selectedVault =
        "00000000-0000-4000-8000-000000000263"
    private static let timestamp = "2026-07-27T00:00:00Z"
    private static let updatedTimestamp = "2026-07-27T01:00:00Z"

    func testSavedSearchFeatureSurfaceExists() throws {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchFeature.swift"
        )

        for required in [
            "AtlasVaultSavedSearchCoordinating",
            "AtlasVaultSavedSearchCoordinator",
            "AtlasVaultSavedSearchDraft",
            "AtlasVaultSavedSearchSnapshot",
            "AtlasVaultSavedSearchMutationResult",
            "AtlasVaultPresentationGeneration",
            "AtlasVaultPresentationID",
            "AtlasVaultSavedSearchPresentation",
            "primary-local-key-v1",
            "AtlasVaultCreateMutation",
            "AtlasVaultUpdateMutation",
            "AtlasVaultDeleteMutation",
            "applyPrivateMutation",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
    }

    func testPrivateSessionAndMutationContractsExist() throws {
        let source = try requiredSource(
            named: "AtlasVaultProductionHostContracts.swift"
        )

        for required in [
            "AtlasVaultPrivateSessionBoundary",
            "AtlasNoopVaultPrivateSessionBoundary",
            "AtlasVaultPrivateSessionBoundaryBridge",
            "AtlasVaultPrivateMutationHosting",
            "AtlasVaultPrivateMutationContainmentHosting",
            "AtlasVaultPrivateMutationResult",
            "activatePrivateSession",
            "hidePrivatePresentation",
            "stopAndDrainPrivateSession",
            "applyPrivateMutation",
            "containCommittedPrivateMutationFailure",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
    }

    func testFeatureUsesOnlyEncryptedRuntimeMutationBoundary() throws {
        let source = try requiredSource(
            named: "AtlasVaultSavedSearchFeature.swift"
        )

        for forbidden in [
            "UserDefaults",
            "URLSession",
            "AtlasAPIClient",
            "SearchViewModel",
            "AtlasLocalCache",
            "/api/saved-searches",
            "FileManager",
            "Keychain",
            "SecItem",
            "Task.detached",
            "@unchecked Sendable",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    @MainActor
    func testConstructionIsSideEffectFreeAndBridgeAttachesOnce() async {
        let environment = SavedSearchEnvironmentFake(reads: [])
        _ = makeCoordinator(environment)

        let constructionReadCount = await environment.readCount()
        let constructionMutationCount = await environment.mutationCount()
        let constructionContainmentCount =
            await environment.containmentCount()
        XCTAssertEqual(constructionReadCount, 0)
        XCTAssertEqual(constructionMutationCount, 0)
        XCTAssertEqual(constructionContainmentCount, 0)

        let noop = AtlasNoopVaultPrivateSessionBoundary()
        let noopActivated = await noop.activatePrivateSession(
            selectedVault: Self.selectedVault
        )
        XCTAssertTrue(noopActivated)
        noop.hidePrivatePresentation()
        await noop.stopAndDrainPrivateSession()

        let bridge = AtlasVaultPrivateSessionBoundaryBridge()
        let first = SavedSearchBoundaryFake()
        let second = SavedSearchBoundaryFake()
        XCTAssertTrue(bridge.attach(first))
        XCTAssertFalse(bridge.attach(second))
        XCTAssertEqual(first.totalCalls, 0)
        XCTAssertEqual(second.totalCalls, 0)
        let bridgeActivated = await bridge.activatePrivateSession(
            selectedVault: Self.selectedVault
        )
        XCTAssertTrue(bridgeActivated)
        bridge.hidePrivatePresentation()
        await bridge.stopAndDrainPrivateSession()
        XCTAssertEqual(first.totalCalls, 3)
        XCTAssertEqual(second.totalCalls, 0)
        XCTAssertTrue(bridge.description.contains("<redacted>"))
    }

    func testActivationProjectsOnlySavedSearchesAndRegeneratesIDs()
        async throws
    {
        let active = makeSavedSearch(
            id: "search-1",
            name: "PRIVATE_NAME_SENTINEL",
            text: "PRIVATE_QUERY_SENTINEL"
        )
        let state = AtlasVaultHydratedState(
            savedSearches: [active],
            tombstones: [
                makeTombstone(id: "deleted-search")
            ]
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [.state(state), .state(state)]
        )
        let coordinator = makeCoordinator(environment)

        let first = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )
        let second = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        XCTAssertEqual(first.searches.count, 1)
        XCTAssertEqual(first.searches[0].name, "PRIVATE_NAME_SENTINEL")
        XCTAssertEqual(
            first.searches[0].request.text,
            "PRIVATE_QUERY_SENTINEL"
        )
        XCTAssertNotEqual(first.searches[0].id, second.searches[0].id)
        XCTAssertFalse(
            first.searches[0].description.contains(
                "PRIVATE_NAME_SENTINEL"
            )
        )

        let stale = try await coordinator.delete(
            presentationID: first.searches[0].id
        )
        guard case let .staleItem(snapshot) = stale else {
            return XCTFail("Expected stale opaque identifier")
        }
        XCTAssertEqual(snapshot, second)
        let mutationCount = await environment.mutationCount()
        XCTAssertEqual(mutationCount, 0)
    }

    func testActivationIgnoresEveryOtherPrivateRecordFamily()
        async throws
    {
        let savedSearch = makeSavedSearch(
            id: "visible-search",
            name: "Visible search",
            text: "visible terms"
        )
        let hiddenSentinel = "OTHER_PRIVATE_FAMILY_MUST_NOT_PROJECT"
        let state = AtlasVaultHydratedState(
            savedSearches: [savedSearch],
            savedJobs: [
                AtlasHydratedSavedJob(
                    metadata: makeMetadata(id: "hidden-job"),
                    payload: AtlasSavedJobVaultPayload(
                        jobKey: hiddenSentinel,
                        status: hiddenSentinel,
                        notes: hiddenSentinel
                    ),
                    clientCreatedAt: Self.timestamp,
                    clientUpdatedAt: Self.timestamp
                ),
            ],
            applicationNotes: [
                AtlasHydratedApplicationNote(
                    metadata: makeMetadata(id: "hidden-note"),
                    payload: AtlasApplicationNoteVaultPayload(
                        title: hiddenSentinel,
                        body: hiddenSentinel,
                        noteKind: hiddenSentinel,
                        createdAt: Self.timestamp,
                        updatedAt: Self.timestamp
                    ),
                    clientCreatedAt: Self.timestamp,
                    clientUpdatedAt: Self.timestamp
                ),
            ],
            profileSnippets: [
                AtlasHydratedProfileSnippet(
                    metadata: makeMetadata(id: "hidden-snippet"),
                    payload: AtlasProfileSnippetVaultPayload(
                        title: hiddenSentinel,
                        body: hiddenSentinel,
                        createdAt: Self.timestamp,
                        updatedAt: Self.timestamp
                    ),
                    clientCreatedAt: Self.timestamp,
                    clientUpdatedAt: Self.timestamp
                ),
            ],
            draftMetadata: [
                AtlasHydratedDraftMetadata(
                    metadata: makeMetadata(id: "hidden-draft"),
                    payload: AtlasDraftMetadataVaultPayload(
                        targetSystem: hiddenSentinel,
                        documentType: hiddenSentinel,
                        generatedDocumentReference: hiddenSentinel,
                        draftStatus: hiddenSentinel,
                        generatedAt: Self.timestamp
                    ),
                    clientCreatedAt: Self.timestamp,
                    clientUpdatedAt: Self.timestamp
                ),
            ],
            tombstones: [makeTombstone(id: "hidden-tombstone")]
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [.state(state)]
        )
        let coordinator = makeCoordinator(environment)

        let snapshot = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        XCTAssertEqual(snapshot.searches.count, 1)
        XCTAssertEqual(snapshot.searches[0].name, "Visible search")
        XCTAssertFalse(
            String(describing: snapshot).contains(hiddenSentinel)
        )
    }

    func testActivationRejectsInvalidVaultAndDuplicateRecords()
        async
    {
        let duplicate = makeSavedSearch(
            id: "duplicate",
            name: "First",
            text: nil
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(
                    AtlasVaultHydratedState(
                        savedSearches: [duplicate, duplicate]
                    )
                ),
            ]
        )
        let coordinator = makeCoordinator(environment)

        do {
            _ = try await coordinator.activate(
                selectedVault: "../invalid"
            )
            XCTFail("Invalid vault identifier was accepted")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultSavedSearchFailure,
                .unavailable
            )
        }
        let readCount = await environment.readCount()
        XCTAssertEqual(readCount, 0)

        do {
            _ = try await coordinator.activate(
                selectedVault: Self.selectedVault
            )
            XCTFail("Duplicate active records were projected")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultSavedSearchFailure,
                .unavailable
            )
        }
    }

    func testDraftValidationRejectsInvalidValuesWithoutMutation()
        async throws
    {
        let environment = SavedSearchEnvironmentFake(
            reads: [.state(AtlasVaultHydratedState())]
        )
        let coordinator = makeCoordinator(environment)
        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        let invalidDrafts = [
            AtlasVaultSavedSearchDraft(name: "", searchText: ""),
            AtlasVaultSavedSearchDraft(name: "   ", searchText: ""),
            AtlasVaultSavedSearchDraft(
                name: String(repeating: "a", count: 121),
                searchText: ""
            ),
            AtlasVaultSavedSearchDraft(
                name: "line\nbreak",
                searchText: ""
            ),
            AtlasVaultSavedSearchDraft(
                name: "control\u{0000}",
                searchText: ""
            ),
            AtlasVaultSavedSearchDraft(
                name: "Valid",
                searchText: String(repeating: "q", count: 513)
            ),
            AtlasVaultSavedSearchDraft(
                name: "Valid",
                searchText: "line\nbreak"
            ),
            AtlasVaultSavedSearchDraft(
                name: "Valid",
                searchText: "control\u{0007}"
            ),
        ]

        for draft in invalidDrafts {
            do {
                _ = try await coordinator.create(draft)
                XCTFail("Invalid draft was accepted")
            } catch {
                XCTAssertEqual(
                    error as? AtlasVaultSavedSearchFailure,
                    .invalidDraft
                )
                if !draft.name.isEmpty {
                    XCTAssertFalse(
                        String(describing: error).contains(draft.name)
                    )
                }
            }
        }
        let mutationCount = await environment.mutationCount()
        XCTAssertEqual(mutationCount, 0)
    }

    func testCreateBuildsCanonicalPayloadAndRefreshesCommittedState()
        async throws
    {
        let created = makeSavedSearch(
            id: "created-search",
            name: "国際機関",
            text: "data governance"
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(AtlasVaultHydratedState()),
                .state(
                    AtlasVaultHydratedState(savedSearches: [created])
                ),
            ],
            mutationResults: [.committed]
        )
        let coordinator = makeCoordinator(environment)
        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        let result = try await coordinator.create(
            AtlasVaultSavedSearchDraft(
                name: "  国際機関  ",
                searchText: "  data governance  "
            )
        )
        guard case let .committed(snapshot) = result else {
            return XCTFail("Expected committed create")
        }
        XCTAssertEqual(snapshot.searches.count, 1)

        let requests = await environment.requests()
        XCTAssertEqual(requests.count, 1)
        let request = try XCTUnwrap(requests.first)
        XCTAssertEqual(request.expectedVaultID, Self.selectedVault)
        XCTAssertEqual(request.mutations.creates.count, 1)
        XCTAssertTrue(request.mutations.updates.isEmpty)
        XCTAssertTrue(request.mutations.deletes.isEmpty)
        let create = try XCTUnwrap(request.mutations.creates.first)
        XCTAssertEqual(create.keyID, "primary-local-key-v1")
        guard case let .savedSearch(envelope) = create.payload else {
            return XCTFail("Expected saved-search payload")
        }
        XCTAssertEqual(envelope.type, .savedSearch)
        XCTAssertEqual(envelope.payloadSchema, 1)
        XCTAssertEqual(envelope.payload.name, "国際機関")
        XCTAssertEqual(envelope.payload.summary, "data governance")
        XCTAssertNil(envelope.payload.description)
        XCTAssertEqual(envelope.payload.request.text, "data governance")
        XCTAssertEqual(envelope.payload.request.status, ["open"])
        XCTAssertTrue(envelope.payload.request.organizations.isEmpty)
        XCTAssertTrue(envelope.payload.request.sourceIDs.isEmpty)
        XCTAssertTrue(envelope.payload.request.cities.isEmpty)
        XCTAssertTrue(envelope.payload.request.countriesISO3.isEmpty)
        XCTAssertTrue(
            envelope.payload.request.nationalInternational.isEmpty
        )
        XCTAssertTrue(envelope.payload.request.gradeCodes.isEmpty)
        XCTAssertTrue(envelope.payload.request.ccogFamilies.isEmpty)
        XCTAssertTrue(envelope.payload.request.capabilityTags.isEmpty)
        XCTAssertTrue(envelope.payload.request.contractGroups.isEmpty)
        XCTAssertTrue(envelope.payload.request.seniorityGroups.isEmpty)
        XCTAssertTrue(envelope.payload.request.workModalities.isEmpty)
        XCTAssertTrue(envelope.payload.request.volunteerKinds.isEmpty)
        XCTAssertTrue(envelope.payload.request.unvCategories.isEmpty)
        XCTAssertTrue(envelope.payload.request.unvVolunteerTypes.isEmpty)
        XCTAssertNil(envelope.payload.request.closingDateTo)
        XCTAssertFalse(envelope.payload.request.includeLowConfidence)
        XCTAssertTrue(envelope.payload.request.includeFacets)
        XCTAssertEqual(envelope.payload.request.limit, 50)
        XCTAssertEqual(envelope.payload.request.offset, 0)
        XCTAssertEqual(
            envelope.payload.request.sort,
            "closing_date_asc"
        )
        XCTAssertEqual(envelope.payload.createdAt, Self.timestamp)
        XCTAssertEqual(envelope.payload.updatedAt, Self.timestamp)
        XCTAssertEqual(envelope.clientCreatedAt, Self.timestamp)
        XCTAssertEqual(envelope.clientUpdatedAt, Self.timestamp)
    }

    func testCreateUsesAllOpenJobsForEmptySearchText()
        async throws
    {
        let created = makeSavedSearch(
            id: "all-open",
            name: "All",
            text: nil,
            summary: "All open jobs"
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(AtlasVaultHydratedState()),
                .state(
                    AtlasVaultHydratedState(savedSearches: [created])
                ),
            ],
            mutationResults: [.committed]
        )
        let coordinator = makeCoordinator(environment)
        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        _ = try await coordinator.create(
            AtlasVaultSavedSearchDraft(
                name: "All",
                searchText: "   "
            )
        )
        let recordedRequests = await environment.requests()
        let request = try XCTUnwrap(recordedRequests.first)
        guard case let .savedSearch(envelope) =
            request.mutations.creates[0].payload else {
            return XCTFail("Expected saved-search payload")
        }
        XCTAssertNil(envelope.payload.request.text)
        XCTAssertEqual(envelope.payload.summary, "All open jobs")
    }

    func testInvalidTimestampFailsBeforeMutation() async throws {
        let environment = SavedSearchEnvironmentFake(
            reads: [.state(AtlasVaultHydratedState())]
        )
        let coordinator = makeCoordinator(
            environment,
            timestamp: "2026-07-27T00:00:00.000Z"
        )
        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        do {
            _ = try await coordinator.create(
                AtlasVaultSavedSearchDraft(
                    name: "Timestamp",
                    searchText: ""
                )
            )
            XCTFail("Non-seconds timestamp was accepted")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultSavedSearchFailure,
                .unavailable
            )
        }
        let mutationCount = await environment.mutationCount()
        XCTAssertEqual(mutationCount, 0)
    }

    func testUpdatePreservesIdentityLineageCreationMetadataAndFilters()
        async throws
    {
        let originalRequest = AtlasSearchRequest(
            text: "old terms",
            status: ["open"],
            organizations: ["UNDP"],
            sourceIDs: ["undp"],
            cities: ["Tokyo"],
            countriesISO3: ["JPN"],
            nationalInternational: ["international"],
            gradeCodes: ["P3"],
            ccogFamilies: ["Information Systems"],
            capabilityTags: ["data"],
            contractGroups: ["staff"],
            seniorityGroups: ["professional"],
            workModalities: ["hybrid"],
            volunteerKinds: ["specialist"],
            unvCategories: ["international"],
            unvVolunteerTypes: ["expert"],
            closingDateTo: "2026-12-31",
            includeLowConfidence: false,
            includeFacets: true,
            limit: 75,
            offset: 25,
            sort: "closing_date_asc"
        )
        let original = makeSavedSearch(
            id: "edit-me",
            name: "Original name",
            text: "old terms",
            summary: "old terms",
            revision: "revision-7",
            key: "existing-key",
            request: originalRequest,
            description: "Preserved private description"
        )
        var refreshedRequest = originalRequest
        refreshedRequest.text = "new terms"
        refreshedRequest.offset = 0
        let refreshed = makeSavedSearch(
            id: "edit-me",
            name: "Renamed search",
            text: "new terms",
            summary: "new terms",
            revision: "revision-8",
            parentRevision: "revision-7",
            key: "existing-key",
            request: refreshedRequest,
            description: "Preserved private description",
            updatedAt: Self.updatedTimestamp,
            clientUpdatedAt: Self.updatedTimestamp
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(
                    AtlasVaultHydratedState(savedSearches: [original])
                ),
                .state(
                    AtlasVaultHydratedState(savedSearches: [refreshed])
                ),
            ],
            mutationResults: [.committed]
        )
        let coordinator = makeCoordinator(
            environment,
            timestamp: Self.updatedTimestamp
        )
        let activated = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )
        let originalPresentationID = try XCTUnwrap(
            activated.searches.first?.id
        )

        let result = try await coordinator.update(
            presentationID: originalPresentationID,
            draft: AtlasVaultSavedSearchDraft(
                name: "  Renamed search  ",
                searchText: "  new terms  "
            )
        )

        guard case let .committed(snapshot) = result else {
            return XCTFail("Expected committed update")
        }
        XCTAssertEqual(snapshot.searches.first?.id, originalPresentationID)
        XCTAssertEqual(snapshot.searches.first?.name, "Renamed search")
        XCTAssertEqual(snapshot.searches.first?.request.text, "new terms")

        let requests = await environment.requests()
        XCTAssertEqual(requests.count, 1)
        let request = try XCTUnwrap(requests.first)
        XCTAssertTrue(request.mutations.creates.isEmpty)
        XCTAssertTrue(request.mutations.deletes.isEmpty)
        let update = try XCTUnwrap(request.mutations.updates.first)
        XCTAssertEqual(update.recordID, "edit-me")
        XCTAssertEqual(update.currentRevision, "revision-7")
        XCTAssertEqual(update.keyID, "existing-key")
        guard case let .savedSearch(envelope) = update.payload else {
            return XCTFail("Expected saved-search update payload")
        }
        XCTAssertEqual(envelope.payload.name, "Renamed search")
        XCTAssertEqual(envelope.payload.summary, "new terms")
        XCTAssertEqual(
            envelope.payload.description,
            "Preserved private description"
        )
        XCTAssertEqual(envelope.payload.createdAt, Self.timestamp)
        XCTAssertEqual(
            envelope.payload.updatedAt,
            Self.updatedTimestamp
        )
        XCTAssertEqual(envelope.clientCreatedAt, Self.timestamp)
        XCTAssertEqual(
            envelope.clientUpdatedAt,
            Self.updatedTimestamp
        )
        XCTAssertEqual(envelope.payload.request, refreshedRequest)
    }

    func testNormalizedNoOpUpdateCreatesNoMutationOrTimestamp()
        async throws
    {
        let existing = makeSavedSearch(
            id: "no-op",
            name: "Existing",
            text: "terms"
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(
                    AtlasVaultHydratedState(savedSearches: [existing])
                ),
            ]
        )
        let timestampCalls = Mutex(0)
        let coordinator = AtlasVaultSavedSearchCoordinator(
            environment: AtlasVaultSavedSearchEnvironment(
                readPrivateState: {
                    try await environment.read()
                },
                applyPrivateMutation: { request in
                    await environment.apply(request)
                },
                containCommittedPrivateMutationFailure: {
                    await environment.contain()
                },
                timestamp: {
                    timestampCalls.withLock { $0 += 1 }
                    return Self.updatedTimestamp
                }
            )
        )
        let activated = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )
        let identifier = try XCTUnwrap(activated.searches.first?.id)

        let result = try await coordinator.update(
            presentationID: identifier,
            draft: AtlasVaultSavedSearchDraft(
                name: "  Existing  ",
                searchText: "  terms  "
            )
        )

        XCTAssertEqual(result, .committed(activated))
        let mutationCount = await environment.mutationCount()
        XCTAssertEqual(mutationCount, 0)
        XCTAssertEqual(timestampCalls.withLock { $0 }, 0)
    }

    func testDeleteUsesInternalMetadataAndRequiresCommittedTombstone()
        async throws
    {
        let active = makeSavedSearch(
            id: "delete-me",
            name: "Delete",
            text: "query",
            revision: "revision-7",
            key: "existing-key"
        )
        let deleted = AtlasVaultHydratedState(
            tombstones: [makeTombstone(id: "delete-me")]
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(
                    AtlasVaultHydratedState(savedSearches: [active])
                ),
                .state(deleted),
            ],
            mutationResults: [.committed]
        )
        let coordinator = makeCoordinator(environment)
        let activated = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        let result = try await coordinator.delete(
            presentationID: activated.searches[0].id
        )
        guard case let .committed(snapshot) = result else {
            return XCTFail("Expected committed delete")
        }
        XCTAssertTrue(snapshot.searches.isEmpty)
        let recordedRequests = await environment.requests()
        let request = try XCTUnwrap(recordedRequests.first)
        XCTAssertTrue(request.mutations.creates.isEmpty)
        XCTAssertTrue(request.mutations.updates.isEmpty)
        let deletion = try XCTUnwrap(request.mutations.deletes.first)
        XCTAssertEqual(deletion.recordID, "delete-me")
        XCTAssertEqual(deletion.currentRevision, "revision-7")
        XCTAssertEqual(deletion.keyID, "existing-key")
    }

    func testDurabilityUnconfirmedRefreshesThenDisablesMutation()
        async throws
    {
        let created = makeSavedSearch(
            id: "durability",
            name: "Durability",
            text: nil
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(AtlasVaultHydratedState()),
                .state(
                    AtlasVaultHydratedState(savedSearches: [created])
                ),
            ],
            mutationResults: [.committedDurabilityUnconfirmed]
        )
        let coordinator = makeCoordinator(environment)
        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        let first = try await coordinator.create(
            AtlasVaultSavedSearchDraft(
                name: "Durability",
                searchText: ""
            )
        )
        guard case let .committedDurabilityUnconfirmed(snapshot) =
            first else {
            return XCTFail("Expected durability warning")
        }
        XCTAssertEqual(snapshot.searches.count, 1)

        let second = try await coordinator.create(
            AtlasVaultSavedSearchDraft(
                name: "Blocked",
                searchText: ""
            )
        )
        guard case let .failed(retained) = second else {
            return XCTFail("Expected mutation lockout")
        }
        XCTAssertEqual(retained, snapshot)
        let mutationCount = await environment.mutationCount()
        XCTAssertEqual(mutationCount, 1)
    }

    func testPrecommitFailurePreservesProjectionAndAllowsRetry()
        async throws
    {
        let existing = makeSavedSearch(
            id: "existing",
            name: "Existing",
            text: nil
        )
        let retried = makeSavedSearch(
            id: "retried",
            name: "Retried",
            text: nil
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(
                    AtlasVaultHydratedState(savedSearches: [existing])
                ),
                .state(
                    AtlasVaultHydratedState(
                        savedSearches: [existing, retried]
                    )
                ),
            ],
            mutationResults: [.failed, .committed]
        )
        let coordinator = makeCoordinator(environment)
        let initial = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        let failed = try await coordinator.create(
            AtlasVaultSavedSearchDraft(name: "Failed", searchText: "")
        )
        guard case let .failed(retained) = failed else {
            return XCTFail("Expected retryable failure")
        }
        XCTAssertEqual(retained, initial)
        let readCountAfterFailure = await environment.readCount()
        XCTAssertEqual(readCountAfterFailure, 1)

        let committed = try await coordinator.create(
            AtlasVaultSavedSearchDraft(name: "Retried", searchText: "")
        )
        guard case let .committed(refreshed) = committed else {
            return XCTFail("Expected retry commit")
        }
        XCTAssertEqual(refreshed.searches.count, 2)
        let mutationCount = await environment.mutationCount()
        XCTAssertEqual(mutationCount, 2)
    }

    func testCommittedRefreshFailureContainsAndInvalidatesSession()
        async throws
    {
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(AtlasVaultHydratedState()),
                .failure,
            ],
            mutationResults: [.committed]
        )
        let coordinator = makeCoordinator(environment)
        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        let result = try await coordinator.create(
            AtlasVaultSavedSearchDraft(name: "Contained", searchText: "")
        )
        XCTAssertEqual(result, .locked)
        let containmentCount = await environment.containmentCount()
        XCTAssertEqual(containmentCount, 1)

        do {
            _ = try await coordinator.create(
                AtlasVaultSavedSearchDraft(
                    name: "No session",
                    searchText: ""
                )
            )
            XCTFail("Invalidated session accepted another mutation")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultSavedSearchFailure,
                .locked
            )
        }
    }

    func testCommittedVerificationMismatchContainsAndInvalidatesSession()
        async throws
    {
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(AtlasVaultHydratedState()),
                .state(AtlasVaultHydratedState()),
            ],
            mutationResults: [.committed]
        )
        let coordinator = makeCoordinator(environment)
        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        let result = try await coordinator.create(
            AtlasVaultSavedSearchDraft(
                name: "Missing committed record",
                searchText: ""
            )
        )

        XCTAssertEqual(result, .locked)
        let containmentCount = await environment.containmentCount()
        XCTAssertEqual(containmentCount, 1)
        do {
            _ = try await coordinator.create(
                AtlasVaultSavedSearchDraft(
                    name: "No session",
                    searchText: ""
                )
            )
            XCTFail("Invalidated session accepted another mutation")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultSavedSearchFailure,
                .locked
            )
        }
    }

    func testPublicSearchRequestPreservesValidatedCriteriaAndRedactsMetadata()
        async throws
    {
        let savedRequest = AtlasSearchRequest(
            text: "climate policy",
            status: ["open"],
            organizations: ["UNDP"],
            sourceIDs: ["undp"],
            cities: ["Tokyo"],
            countriesISO3: ["JPN"],
            nationalInternational: ["international"],
            gradeCodes: ["P3"],
            ccogFamilies: ["Programme Management"],
            capabilityTags: ["policy"],
            contractGroups: ["staff"],
            seniorityGroups: ["professional"],
            workModalities: ["hybrid"],
            volunteerKinds: ["specialist"],
            unvCategories: ["international"],
            unvVolunteerTypes: ["expert"],
            closingDateTo: "2026-12-31",
            includeLowConfidence: false,
            includeFacets: true,
            limit: 75,
            offset: 25,
            sort: "closing_date_asc"
        )
        let record = makeSavedSearch(
            id: "PRIVATE_RECORD_ID_MUST_NOT_CROSS",
            name: "PRIVATE_NAME_MUST_NOT_CROSS",
            text: "climate policy",
            request: savedRequest
        )
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(
                    AtlasVaultHydratedState(savedSearches: [record])
                ),
            ]
        )
        let coordinator = makeCoordinator(environment)
        let snapshot = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )
        let identifier = try XCTUnwrap(snapshot.searches.first?.id)

        let request = try await coordinator.publicSearchRequest(
            presentationID: identifier,
            maximumLimit: 60
        )

        XCTAssertEqual(request.origin, .savedSearchHandoff)
        XCTAssertTrue(request.hasAdditionalCriteria)
        XCTAssertEqual(request.query, "climate policy")
        XCTAssertEqual(request.limit, 60)
        XCTAssertEqual(request.offset, 0)
        var expected = savedRequest
        expected.includeFacets = false
        expected.limit = 60
        expected.offset = 0
        XCTAssertEqual(request.apiRequest, expected)
        let rendered = [
            request.description,
            request.debugDescription,
        ].joined(separator: "\n")
        XCTAssertFalse(rendered.contains("PRIVATE_NAME_MUST_NOT_CROSS"))
        XCTAssertFalse(rendered.contains("PRIVATE_RECORD_ID_MUST_NOT_CROSS"))
        XCTAssertFalse(rendered.contains(Self.selectedVault))

        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )
        do {
            _ = try await coordinator.publicSearchRequest(
                presentationID: identifier,
                maximumLimit: 60
            )
            XCTFail("A prior unlock generation ID was accepted")
        } catch {
            XCTAssertEqual(
                error as? AtlasVaultSavedSearchFailure,
                .unavailable
            )
        }
        let mutationCount = await environment.mutationCount()
        XCTAssertEqual(mutationCount, 0)
    }

    func testSavedSearchPublicRequestRejectsUnsupportedCriteriaBeforeHandoff()
        throws
    {
        let valid = AtlasSearchRequest(
            text: "policy",
            organizations: ["UNDP"],
            closingDateTo: "2026-12-31",
            limit: 50,
            offset: 4
        )
        XCTAssertNoThrow(
            try AtlasPublicJobSearchRequest(
                validatingSavedSearch: valid,
                maximumLimit: 25
            )
        )

        var invalidRequests: [AtlasSearchRequest] = []
        var invalid = valid
        invalid.status = ["closed"]
        invalidRequests.append(invalid)
        invalid = valid
        invalid.includeLowConfidence = true
        invalidRequests.append(invalid)
        invalid = valid
        invalid.sort = "relevance"
        invalidRequests.append(invalid)
        invalid = valid
        invalid.limit = 0
        invalidRequests.append(invalid)
        invalid = valid
        invalid.offset = -1
        invalidRequests.append(invalid)
        invalid = valid
        invalid.text = "line\nbreak"
        invalidRequests.append(invalid)
        invalid = valid
        invalid.organizations = [" UNDP"]
        invalidRequests.append(invalid)
        invalid = valid
        invalid.organizations = ["UNDP", "UNDP"]
        invalidRequests.append(invalid)
        invalid = valid
        invalid.closingDateTo = "2026-02-30"
        invalidRequests.append(invalid)

        for request in invalidRequests {
            XCTAssertThrowsError(
                try AtlasPublicJobSearchRequest(
                    validatingSavedSearch: request,
                    maximumLimit: 25
                )
            ) { error in
                XCTAssertEqual(
                    error as? AtlasPublicJobServiceError,
                    .invalidRequest
                )
            }
        }
        XCTAssertThrowsError(
            try AtlasPublicJobSearchRequest(
                validatingSavedSearch: valid,
                maximumLimit: 0
            )
        )
        XCTAssertThrowsError(
            try AtlasPublicJobSearchRequest(
                validatingSavedSearch: valid,
                maximumLimit: 201
            )
        )
    }

    func testPublicHandoffCoordinatorRetainsOneCallerIndependentOperation()
        async throws
    {
        let gate = SavedSearchSuspensionGate()
        let host = SavedSearchPublicHandoffHostFake(gate: gate)
        let coordinator = AtlasVaultSavedSearchPublicHandoffCoordinator(
            host: host
        )
        let request = try AtlasPublicJobSearchRequest(
            validatingSavedSearch: AtlasSearchRequest(text: "policy"),
            maximumLimit: 25
        )

        let first = Task {
            await coordinator.perform(request)
        }
        await gate.waitUntilEntered()
        first.cancel()
        let duplicate = await coordinator.perform(request)
        XCTAssertEqual(duplicate, .cancelled)
        let callsWhileSuspended = await host.callCount()
        XCTAssertEqual(callsWhileSuspended, 1)

        await gate.release()
        let completed = await first.value
        XCTAssertEqual(completed, .completed)
        let recordedRequest = await host.lastRequest()
        XCTAssertEqual(recordedRequest, request)
        await coordinator.stop()
        let finalCalls = await host.callCount()
        XCTAssertEqual(finalCalls, 1)
    }

    func testConcurrentCreateDoesNotDuplicateMutation() async throws {
        let created = makeSavedSearch(
            id: "single",
            name: "Single",
            text: nil
        )
        let gate = SavedSearchSuspensionGate()
        let environment = SavedSearchEnvironmentFake(
            reads: [
                .state(AtlasVaultHydratedState()),
                .state(
                    AtlasVaultHydratedState(savedSearches: [created])
                ),
            ],
            mutationResults: [.committed],
            mutationGate: gate
        )
        let coordinator = makeCoordinator(environment)
        _ = try await coordinator.activate(
            selectedVault: Self.selectedVault
        )

        let first = Task {
            try await coordinator.create(
                AtlasVaultSavedSearchDraft(
                    name: "Single",
                    searchText: ""
                )
            )
        }
        await gate.waitUntilEntered()
        let second = try await coordinator.create(
            AtlasVaultSavedSearchDraft(
                name: "Second",
                searchText: ""
            )
        )
        guard case .failed = second else {
            return XCTFail("Concurrent create was not rejected")
        }
        let mutationCount = await environment.mutationCount()
        XCTAssertEqual(mutationCount, 1)
        await gate.release()
        guard case .committed = try await first.value else {
            return XCTFail("Retained create did not commit")
        }
    }

    private func makeCoordinator(
        _ environment: SavedSearchEnvironmentFake,
        timestamp: String = AtlasVaultSavedSearchFeatureTests.timestamp
    ) -> AtlasVaultSavedSearchCoordinator {
        AtlasVaultSavedSearchCoordinator(
            environment: AtlasVaultSavedSearchEnvironment(
                readPrivateState: {
                    try await environment.read()
                },
                applyPrivateMutation: { request in
                    await environment.apply(request)
                },
                containCommittedPrivateMutationFailure: {
                    await environment.contain()
                },
                timestamp: { timestamp }
            )
        )
    }

    private func makeSavedSearch(
        id: String,
        name: String,
        text: String?,
        summary: String? = nil,
        revision: String = "revision-1",
        parentRevision: String? = nil,
        key: String = "key-1",
        request: AtlasSearchRequest? = nil,
        description: String? = nil,
        createdAt: String = AtlasVaultSavedSearchFeatureTests.timestamp,
        updatedAt: String = AtlasVaultSavedSearchFeatureTests.timestamp,
        clientCreatedAt: String =
            AtlasVaultSavedSearchFeatureTests.timestamp,
        clientUpdatedAt: String =
            AtlasVaultSavedSearchFeatureTests.timestamp
    ) -> AtlasHydratedSavedSearch {
        AtlasHydratedSavedSearch(
            metadata: AtlasHydratedRecordMetadata(
                id: id,
                revision: revision,
                parentRevision: parentRevision,
                deleted: false,
                keyID: key
            ),
            payload: AtlasSavedSearchVaultPayload(
                name: name,
                summary: summary ?? text ?? "All open jobs",
                description: description,
                request: request ?? AtlasSearchRequest(text: text),
                createdAt: createdAt,
                updatedAt: updatedAt
            ),
            clientCreatedAt: clientCreatedAt,
            clientUpdatedAt: clientUpdatedAt
        )
    }

    private func makeTombstone(id: String) -> AtlasHydratedTombstone {
        AtlasHydratedTombstone(
            metadata: makeMetadata(
                id: id,
                deleted: true,
                revision: "tombstone-revision",
                parentRevision: "prior-revision"
            )
        )
    }

    private func makeMetadata(
        id: String,
        deleted: Bool = false,
        revision: String = "revision-1",
        parentRevision: String? = nil
    ) -> AtlasHydratedRecordMetadata {
        AtlasHydratedRecordMetadata(
            id: id,
            revision: revision,
            parentRevision: parentRevision,
            deleted: deleted,
            keyID: "key-1"
        )
    }

    private func requiredSource(named name: String) throws -> String {
        let url = Self.appleRoot()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: url.path),
            "Missing Phase 2D-63 source: \(name)"
        )
        guard FileManager.default.fileExists(atPath: url.path) else {
            return ""
        }
        return try String(contentsOf: url, encoding: .utf8)
    }

    private static func appleRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}

private actor SavedSearchEnvironmentFake {
    enum Read: Sendable {
        case state(AtlasVaultHydratedState)
        case failure
    }

    private var reads: [Read]
    private var mutationResults: [AtlasVaultPrivateMutationResult]
    private let mutationGate: SavedSearchSuspensionGate?
    private var recordedRequests: [AtlasVaultRuntimeMutationRequest] = []
    private var readCalls = 0
    private var containmentCalls = 0

    init(
        reads: [Read],
        mutationResults: [AtlasVaultPrivateMutationResult] = [],
        mutationGate: SavedSearchSuspensionGate? = nil
    ) {
        self.reads = reads
        self.mutationResults = mutationResults
        self.mutationGate = mutationGate
    }

    func read() async throws -> AtlasVaultHydratedState {
        readCalls += 1
        guard !reads.isEmpty else {
            throw AtlasVaultSavedSearchFailure.unavailable
        }
        switch reads.removeFirst() {
        case let .state(state):
            return state
        case .failure:
            throw AtlasVaultSavedSearchFailure.unavailable
        }
    }

    func apply(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async -> AtlasVaultPrivateMutationResult {
        recordedRequests.append(request)
        if let mutationGate {
            await mutationGate.wait()
        }
        guard !mutationResults.isEmpty else {
            return .failed
        }
        return mutationResults.removeFirst()
    }

    func contain() {
        containmentCalls += 1
    }

    func requests() -> [AtlasVaultRuntimeMutationRequest] {
        recordedRequests
    }

    func readCount() -> Int {
        readCalls
    }

    func mutationCount() -> Int {
        recordedRequests.count
    }

    func containmentCount() -> Int {
        containmentCalls
    }
}

private actor SavedSearchSuspensionGate {
    private var entered = false
    private var released = false
    private var entryWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseWaiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        entered = true
        let entryWaiters = entryWaiters
        self.entryWaiters.removeAll()
        for waiter in entryWaiters {
            waiter.resume()
        }
        guard !released else {
            return
        }
        await withCheckedContinuation { continuation in
            releaseWaiters.append(continuation)
        }
    }

    func waitUntilEntered() async {
        guard !entered else {
            return
        }
        await withCheckedContinuation { continuation in
            entryWaiters.append(continuation)
        }
    }

    func release() {
        released = true
        let releaseWaiters = releaseWaiters
        self.releaseWaiters.removeAll()
        for waiter in releaseWaiters {
            waiter.resume()
        }
    }
}

private actor SavedSearchPublicHandoffHostFake:
    AtlasVaultSavedSearchPublicHandoffHosting
{
    private let gate: SavedSearchSuspensionGate
    private var calls = 0
    private var request: AtlasPublicJobSearchRequest?

    init(gate: SavedSearchSuspensionGate) {
        self.gate = gate
    }

    func performSavedSearchPublicHandoff(
        _ request: AtlasPublicJobSearchRequest
    ) async -> AtlasVaultSavedSearchPublicHandoffResult {
        calls += 1
        self.request = request
        await gate.wait()
        return .completed
    }

    func callCount() -> Int {
        calls
    }

    func lastRequest() -> AtlasPublicJobSearchRequest? {
        request
    }
}

@MainActor
private final class SavedSearchBoundaryFake:
    AtlasVaultPrivateSessionBoundary
{
    private(set) var totalCalls = 0

    func activatePrivateSession(selectedVault: String) async -> Bool {
        totalCalls += 1
        return true
    }

    func hidePrivatePresentation() {
        totalCalls += 1
    }

    func stopAndDrainPrivateSession() async {
        totalCalls += 1
    }
}
