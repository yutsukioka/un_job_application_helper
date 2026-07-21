// Phase 2D-56 repository boundary.
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultProductionHostFactoryTests: XCTestCase {
    private static let fakeQuery = "FAKE_PUBLIC_QUERY_DO_NOT_LOG"
    private static let fakeJobID = "FAKE_PUBLIC_JOB_2D54"
    private static let fakeVaultID = "00000000-0000-4000-8000-000000000254"

    func testPhaseContractsAndFactoryAreAvailableAndSendable() throws {
        _ = AtlasPublicJobSearching.self
        _ = AtlasPublicSnapshotRestoring.self
        _ = AtlasVaultIDSelecting.self
        _ = AtlasVaultProductionHosting.self
        _ = AtlasVaultProductionHostBuilding.self
        _ = AtlasVaultUnlockPresentationControllerBuilding.self
        _ = AtlasVaultProductionPresentationCoordinating.self
        _ = AtlasVaultProductionPresentationOwnerResetting.self

        let graph = try makeDependencyGraph()
        let dependencies = graph.dependencies
        let factory = AtlasVaultProductionHostFactory(
            dependencies: dependencies,
            builder: graph.hostBuilder
        )

        requireSendable(dependencies)
        requireSendable(factory)
    }

    func testPublicSearchRequestAndResultAreSafeAndRedacted() throws {
        let request = try AtlasPublicJobSearchRequest(
            query: Self.fakeQuery,
            limit: 25,
            offset: 10
        )
        let result = try makeSearchResult()
        let rendered = [
            request.description,
            request.debugDescription,
            result.description,
            result.debugDescription,
        ].joined(separator: "\n")

        XCTAssertEqual(request.query, Self.fakeQuery)
        XCTAssertEqual(request.limit, 25)
        XCTAssertEqual(request.offset, 10)
        XCTAssertEqual(result.jobs.map(\.id), [Self.fakeJobID])
        XCTAssertEqual(result.total, 1)
        XCTAssertEqual(result.limit, 25)
        XCTAssertEqual(result.offset, 0)
        XCTAssertFalse(rendered.contains(Self.fakeQuery))
        XCTAssertFalse(rendered.contains(Self.fakeJobID))
        XCTAssertFalse(rendered.contains("Public role"))
        XCTAssertTrue(rendered.contains("<redacted>"))
        requireSendable(request)
        requireSendable(result)

        let labels = storedPropertyLabels(result)
        for forbidden in [
            "isSaved",
            "membership",
            "privateStatus",
            "notes",
            "baseURL",
            "databasePath",
            "endpointURL",
            "vaultID",
        ] {
            XCTAssertFalse(labels.contains(forbidden), forbidden)
        }
    }

    func testPublicRequestAndCountValidationUsesFixedErrors() {
        XCTAssertThrowsError(
            try AtlasPublicJobSearchRequest(query: "fake", limit: 0, offset: 0)
        ) { error in
            XCTAssertEqual(error as? AtlasPublicJobServiceError, .invalidRequest)
        }
        XCTAssertThrowsError(
            try AtlasPublicJobSearchRequest(query: "fake", limit: 201, offset: 0)
        ) { error in
            XCTAssertEqual(error as? AtlasPublicJobServiceError, .invalidRequest)
        }
        XCTAssertThrowsError(
            try AtlasPublicJobSearchRequest(query: "fake", limit: 1, offset: -1)
        ) { error in
            XCTAssertEqual(error as? AtlasPublicJobServiceError, .invalidRequest)
        }
        XCTAssertThrowsError(
            try AtlasPublicJobSearchResult(
                jobs: [],
                total: -1,
                limit: 1,
                offset: 0
            )
        ) { error in
            XCTAssertEqual(error as? AtlasPublicJobServiceError, .invalidResponse)
        }
        XCTAssertThrowsError(
            try AtlasPublicJobSearchResult(
                jobs: [publicJob(), publicJob()],
                total: 2,
                limit: 1,
                offset: 0
            )
        ) { error in
            XCTAssertEqual(error as? AtlasPublicJobServiceError, .invalidResponse)
        }
        XCTAssertThrowsError(
            try AtlasPublicJobSearchResult(
                jobs: [],
                total: 1,
                limit: 1,
                offset: 2
            )
        ) { error in
            XCTAssertEqual(error as? AtlasPublicJobServiceError, .invalidResponse)
        }
        XCTAssertThrowsError(
            try AtlasPublicJobSearchResult(
                jobs: [publicJob()],
                total: 1,
                limit: 1,
                offset: 1
            )
        ) { error in
            XCTAssertEqual(error as? AtlasPublicJobServiceError, .invalidResponse)
        }

        let rendered = [
            AtlasPublicJobServiceError.invalidRequest.description,
            AtlasPublicJobServiceError.invalidRequest.debugDescription,
            AtlasPublicJobServiceError.invalidResponse.description,
        ].joined(separator: "\n")
        XCTAssertFalse(rendered.contains(Self.fakeQuery))
    }

    func testPublicHealthSourceUpdateAndDetailModelsAreNarrowAndRedacted() throws {
        let syncTime = Date(timeIntervalSince1970: 1_725_400_000)
        let health = try AtlasPublicServiceHealth(
            availability: .available,
            openJobCount: 14,
            enabledSourceCount: 3,
            lastSyncAt: syncTime
        )
        let source = try AtlasPublicSourceStatus(
            sourceID: "FAKE_PUBLIC_SOURCE",
            displayName: "Fake Public Source",
            availability: .available,
            openJobCount: 8
        )
        let update = try AtlasPublicUpdateStatus(
            sourceID: "FAKE_PUBLIC_SOURCE",
            observedAt: syncTime,
            fetchedJobCount: 9,
            changedJobCount: 2,
            closedJobCount: 1
        )
        let reference = try AtlasPublicJobReference(
            publicJobID: Self.fakeJobID
        )
        let detail = try AtlasPublicJobDetailResult(
            reference: reference,
            job: publicJob(),
            detailText: "FAKE_PUBLIC_DETAIL_TEXT_DO_NOT_LOG"
        )
        let rendered = [
            health.description,
            source.description,
            update.description,
            reference.description,
            reference.debugDescription,
            detail.description,
        ].joined(separator: "\n")

        XCTAssertEqual(health.availability, .available)
        XCTAssertEqual(health.openJobCount, 14)
        XCTAssertEqual(health.enabledSourceCount, 3)
        XCTAssertEqual(health.lastSyncAt, syncTime)
        XCTAssertEqual(source.openJobCount, 8)
        XCTAssertEqual(update.changedJobCount, 2)
        XCTAssertEqual(reference.publicJobID, Self.fakeJobID)
        XCTAssertEqual(detail.job.id, Self.fakeJobID)
        XCTAssertFalse(rendered.contains(Self.fakeJobID))
        XCTAssertFalse(rendered.contains("FAKE_PUBLIC_SOURCE"))
        XCTAssertFalse(rendered.contains("FAKE_PUBLIC_DETAIL_TEXT_DO_NOT_LOG"))
        XCTAssertTrue(rendered.contains("<redacted>"))
        XCTAssertTrue(
            try contractsSource().contains("public let publicJobID: String")
        )

        for value in [health as Any, source as Any, update as Any, detail as Any] {
            let labels = storedPropertyLabels(value)
            XCTAssertFalse(labels.contains("databasePath"))
            XCTAssertFalse(labels.contains("endpointURL"))
            XCTAssertFalse(labels.contains("baseURL"))
            XCTAssertFalse(labels.contains("cache"))
        }
    }

    func testPublicModelValidationRejectsInvalidCountsAndReference() {
        XCTAssertThrowsError(
            try AtlasPublicServiceHealth(
                availability: .available,
                openJobCount: -1,
                enabledSourceCount: 1,
                lastSyncAt: nil
            )
        )
        XCTAssertThrowsError(
            try AtlasPublicSourceStatus(
                sourceID: "fake",
                displayName: "fake",
                availability: .available,
                openJobCount: -1
            )
        )
        XCTAssertThrowsError(
            try AtlasPublicUpdateStatus(
                sourceID: "fake",
                observedAt: nil,
                fetchedJobCount: -1,
                changedJobCount: 0,
                closedJobCount: 0
            )
        )
        XCTAssertThrowsError(try AtlasPublicJobReference(publicJobID: ""))
        XCTAssertThrowsError(
            try AtlasPublicJobDetailResult(
                reference: AtlasPublicJobReference(
                    publicJobID: Self.fakeJobID
                ),
                job: AtlasLockedPublicJob(
                    id: "FAKE_DIFFERENT_PUBLIC_JOB",
                    title: "Different public role",
                    organization: "Fake Public Organization",
                    location: "Remote"
                ),
                detailText: "Fake mismatched public detail"
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasPublicJobServiceError,
                .invalidResponse
            )
        }
    }

    func testPublicProtocolFakePerformsOnlyPublicOperations() async throws {
        let service = try FactoryPublicJobsSpy(
            health: publicHealth(),
            searchResult: makeSearchResult(),
            sources: [publicSource()],
            updates: [publicUpdate()],
            detail: publicDetail()
        )
        let request = try AtlasPublicJobSearchRequest(
            query: Self.fakeQuery,
            limit: 25,
            offset: 0
        )
        let reference = try AtlasPublicJobReference(publicJobID: Self.fakeJobID)

        _ = try await service.health()
        _ = try await service.search(request)
        _ = try await service.sources()
        _ = try await service.updates()
        _ = try await service.detail(for: reference)

        let calls = await service.calls()
        XCTAssertEqual(calls.health, 1)
        XCTAssertEqual(calls.search, 1)
        XCTAssertEqual(calls.sources, 1)
        XCTAssertEqual(calls.updates, 1)
        XCTAssertEqual(calls.detail, 1)
    }

    func testPublicProtocolMakesPrivateAndCacheWriteOperationsUnrepresentable() throws {
        let source = try contractsSource()
        let section = try sourceSection(
            source,
            from: "public protocol AtlasPublicJobSearching",
            to: "public enum AtlasPublicSnapshotRestoreError"
        )

        for forbidden in [
            "saved-search",
            "savedSearch",
            "savedJob",
            "tracker",
            "note",
            "snippet",
            "draft",
            "generatedDocument",
            "vaultRecord",
            "func mutate",
            "func sync",
            "func write",
            "func save",
            "func delete",
            "detailCache",
        ] {
            XCTAssertFalse(section.contains(forbidden), forbidden)
        }
        XCTAssertTrue(
            section.contains("throws(AtlasPublicJobServiceError)")
        )
    }

    func testSnapshotRestorerReturnsNoneOrSafePublicSnapshot() async throws {
        let emptyRestorer = FactorySnapshotRestorerSpy(snapshot: nil)
        let emptySnapshot = try await emptyRestorer.restore()
        XCTAssertNil(emptySnapshot)

        let snapshot = try makePublicSnapshot()
        let restorer = FactorySnapshotRestorerSpy(snapshot: snapshot)
        let restoredValue = try await restorer.restore()
        let restored = try XCTUnwrap(restoredValue)
        let restoreCount = await restorer.restoreCount()

        XCTAssertEqual(restored.savedAt, snapshot.savedAt)
        XCTAssertEqual(restored.health, snapshot.health)
        XCTAssertEqual(restored.jobs, snapshot.jobs)
        XCTAssertEqual(restored.sources, snapshot.sources)
        XCTAssertEqual(restored.updates, snapshot.updates)
        XCTAssertEqual(restoreCount, 1)
        XCTAssertFalse(restored.description.contains(Self.fakeJobID))
        XCTAssertTrue(restored.description.contains("<redacted>"))
        requireSendable(restored)
    }

    func testSnapshotIsRestoreOnlyAndExcludesUnsafeAuthority() throws {
        let snapshot = try makePublicSnapshot()
        let labels = storedPropertyLabels(snapshot)
        let source = try contractsSource()
        let section = try sourceSection(
            source,
            from: "public protocol AtlasPublicSnapshotRestoring",
            to: "public enum AtlasVaultIDSelectionError"
        )

        for forbidden in [
            "baseURL",
            "databasePath",
            "detailCache",
            "savedMembership",
            "savedOnly",
            "vaultID",
            "keychain",
            "fileURL",
            "privateState",
            "publicShell",
            "searchQuery",
            "canRequestUnlock",
        ] {
            XCTAssertFalse(labels.contains(forbidden), forbidden)
            XCTAssertFalse(section.contains(forbidden), forbidden)
        }
        for forbiddenMethod in [
            "func save",
            "func replace",
            "func delete",
            "func warm",
            "func restoreDetail",
        ] {
            XCTAssertFalse(section.contains(forbiddenMethod), forbiddenMethod)
        }
        XCTAssertFalse(section.contains("Codable"))
        XCTAssertTrue(
            section.contains("throws(AtlasPublicSnapshotRestoreError)")
        )
    }

    func testVaultSelectionRepresentsNoneOrOneValidatedID() throws {
        let none = AtlasVaultIDSelection.none
        let selectedID = try AtlasSelectedVaultID(validating: Self.fakeVaultID)
        let selected = AtlasVaultIDSelection.selected(selectedID)

        XCTAssertEqual(none, .none)
        XCTAssertEqual(
            selectedID.vaultID,
            try AtlasInjectedRootVaultPathLocator.validatedVaultID(
                Self.fakeVaultID
            )
        )
        guard case let .selected(value) = selected else {
            return XCTFail("Expected selected fake vault")
        }
        XCTAssertEqual(value.vaultID, Self.fakeVaultID)
        requireSendable(none)
        requireSendable(selected)
    }

    func testVaultSelectionReusesExistingValidationPolicy() {
        for invalid in [
            "",
            " leading",
            "trailing ",
            "folder/name",
            "folder\\name",
            ".",
            "..",
            "saved_search",
            "SAVED_JOB",
            "application_note",
            "profile_snippet",
            "draft_metadata",
            "vault-\u{00e9}",
        ] {
            XCTAssertThrowsError(
                try AtlasInjectedRootVaultPathLocator.validatedVaultID(invalid),
                invalid
            )
            XCTAssertThrowsError(
                try AtlasSelectedVaultID(validating: invalid),
                invalid
            ) { error in
                XCTAssertEqual(
                    error as? AtlasVaultIDSelectionError,
                    .invalidVaultID
                )
            }
        }
    }

    func testVaultSelectionDescriptionsAndShapeRevealNoIdentifierOrMetadata() throws {
        let selectedID = try AtlasSelectedVaultID(validating: Self.fakeVaultID)
        let selection = AtlasVaultIDSelection.selected(selectedID)
        let rendered = [
            selectedID.description,
            selectedID.debugDescription,
            selection.description,
            selection.debugDescription,
        ].joined(separator: "\n")

        XCTAssertFalse(rendered.contains(Self.fakeVaultID))
        XCTAssertTrue(rendered.contains("<redacted>"))
        for value in [selectedID as Any, selection as Any] {
            let labels = storedPropertyLabels(value)
            for forbidden in [
                "label",
                "count",
                "createdAt",
                "updatedAt",
                "timestamp",
                "path",
                "recordMetadata",
                "credential",
            ] {
                XCTAssertFalse(labels.contains(forbidden), forbidden)
            }
        }

        let source = try contractsSource()
        let section = try sourceSection(
            source,
            from: "public enum AtlasVaultIDSelectionError",
            to: "public protocol AtlasVaultProductionHosting"
        )
        XCTAssertFalse(section.contains("Codable"))
        XCTAssertFalse(section.contains("UserDefaults"))
        XCTAssertFalse(section.contains("FileManager"))
        XCTAssertTrue(
            section.contains("throws(AtlasVaultIDSelectionError)")
        )
        XCTAssertTrue(section.contains("public let vaultID: String"))
    }

    func testFakeHostConformsAndSupportsOnlyFirstJourneyCommands() async throws {
        let host = try FactoryProductionHostSpy(
            state: flowState(),
            searchResult: makeSearchResult()
        )
        let typed: any AtlasVaultProductionHosting = host
        let request = try AtlasPublicJobSearchRequest(
            query: Self.fakeQuery,
            limit: 25,
            offset: 0
        )

        _ = try await typed.start()
        _ = await typed.currentFlowState()
        _ = try await typed.searchPublicJobs(request)
        _ = await typed.requestUnlockPanel()
        _ = await typed.selectUnlockMethod(.localKey)
        _ = await typed.submitUnlock(.localKey, timeout: nil)
        _ = await typed.cancelUnlock()
        _ = await typed.unlockPanelDidDisappear()
        _ = await typed.lock()
        _ = await typed.handleLifecycleEvent(.didEnterBackground)
        _ = await typed.stop()

        let calls = await host.calls()
        XCTAssertEqual(calls.start, 1)
        XCTAssertEqual(calls.current, 1)
        XCTAssertEqual(calls.search, 1)
        XCTAssertEqual(calls.panel, 1)
        XCTAssertEqual(calls.select, 1)
        XCTAssertEqual(calls.submit, 1)
        XCTAssertEqual(calls.cancel, 1)
        XCTAssertEqual(calls.disappearance, 1)
        XCTAssertEqual(calls.lock, 1)
        XCTAssertEqual(calls.lifecycle, 1)
        XCTAssertEqual(calls.stop, 1)
    }

    func testHostContractStopsAtSanitizedFlowAndHasNoPrivateOrMutationSurface() throws {
        let source = try contractsSource()
        let section = try sourceSection(
            source,
            from: "public protocol AtlasVaultProductionHosting",
            to: "public protocol AtlasVaultUnlockPresentationControllerBuilding"
        )

        for required in [
            "func start(",
            "func stop(",
            "func currentFlowState(",
            "func searchPublicJobs(",
            "func requestUnlockPanel(",
            "func selectUnlockMethod(",
            "func submitUnlock(",
            "func cancelUnlock(",
            "func unlockPanelDidDisappear(",
            "func lock(",
            "func handleLifecycleEvent(",
            "AtlasLockedShellUnlockFlowState",
            "AtlasPublicJobSearchResult",
            "AtlasVaultUnlockMethod",
            "AtlasVaultUnlockSubmission",
            "AtlasVaultLifecycleEvent",
        ] {
            XCTAssertTrue(section.contains(required), required)
        }
        for forbidden in [
            "AtlasVaultRuntimeFacading",
            "AtlasVaultPrivate",
            "AtlasVaultHydrated",
            "AtlasVaultEncrypted",
            "persistenceCoordinator",
            "fileURL",
            "vaultKey",
            "vaultID",
            "secretBuffer",
            "func save",
            "func apply",
            "func mutate",
            "renderPrivate",
        ] {
            XCTAssertFalse(section.contains(forbidden), forbidden)
        }
        XCTAssertTrue(
            section.contains("throws(AtlasPublicJobServiceError)")
        )
    }

    func testDependencyAndFactoryConstructionInvokeNothing() async throws {
        let graph = try makeDependencyGraph()
        let dependencies = graph.dependencies
        let before = await graph.callCounts()

        XCTAssertEqual(before, .zero)

        _ = AtlasVaultProductionHostFactory(
            dependencies: dependencies,
            builder: graph.hostBuilder
        )
        let after = await graph.callCounts()

        XCTAssertEqual(after, .zero)
    }

    func testExplicitHostCreationCallsOnlyBuilderOnceWithExactDependencies() async throws {
        let graph = try makeDependencyGraph()
        let dependencies = graph.dependencies
        let factory = AtlasVaultProductionHostFactory(
            dependencies: dependencies,
            builder: graph.hostBuilder
        )

        let created = factory.makeHost()
        let callCounts = await graph.callCounts()

        XCTAssertEqual(graph.hostBuilder.callCount, 1)
        XCTAssertTrue(
            ObjectIdentifier(created as AnyObject)
                == ObjectIdentifier(graph.host)
        )
        let captured = try XCTUnwrap(graph.hostBuilder.capturedDependencies)
        XCTAssertTrue(
            sameObject(captured.publicJobs, graph.publicJobs)
        )
        XCTAssertTrue(
            sameObject(
                captured.publicSnapshotRestorer,
                graph.snapshotRestorer
            )
        )
        XCTAssertTrue(
            sameObject(captured.vaultIDSelector, graph.vaultSelector)
        )
        XCTAssertTrue(sameObject(captured.runtime, graph.runtime))
        XCTAssertTrue(sameObject(captured.lifecycle, graph.lifecycle))
        XCTAssertTrue(
            sameObject(captured.presentation, graph.presentation)
        )
        XCTAssertTrue(
            sameObject(
                captured.presentationOwner,
                graph.presentationOwner
            )
        )
        XCTAssertTrue(
            sameObject(
                captured.unlockCoordinator,
                graph.unlockCoordinator
            )
        )
        XCTAssertTrue(
            sameObject(
                captured.unlockControllerBuilder,
                graph.unlockControllerBuilder
            )
        )

        XCTAssertEqual(callCounts.publicJobs, 0)
        XCTAssertEqual(callCounts.snapshotRestore, 0)
        XCTAssertEqual(callCounts.vaultSelection, 0)
        XCTAssertEqual(callCounts.runtime, 0)
        XCTAssertEqual(callCounts.lifecycle, 0)
        XCTAssertEqual(callCounts.presentation, 0)
        XCTAssertEqual(callCounts.presentationOwner, 0)
        XCTAssertEqual(callCounts.unlockCoordinator, 0)
        XCTAssertEqual(callCounts.unlockControllerBuilder, 0)
        XCTAssertEqual(callCounts.hostBuilder, 1)
        XCTAssertEqual(callCounts.hostStart, 0)
    }

    func testConcreteHostBuilderReturnsInactiveHostWithoutInvokingDependencies() async throws {
        let graph = try makeDependencyGraph()
        let builder = AtlasVaultProductionHostBuilder()
        let before = await graph.callCounts()

        let built = builder.makeHost(dependencies: graph.dependencies)
        let host = try XCTUnwrap(built as? AtlasVaultProductionHost)
        let after = await graph.callCounts()
        let inactive = await host.isInactiveForTesting()

        XCTAssertEqual(before, .zero)
        XCTAssertEqual(after, .zero)
        XCTAssertTrue(inactive)
        XCTAssertTrue(builder.description.contains("<redacted>"))
    }

    func testConcreteControllerBuilderConstructsWithoutDispatching() async throws {
        let graph = try makeDependencyGraph()
        let selectedID = try AtlasSelectedVaultID(
            validating: Self.fakeVaultID
        )
        let builder =
            AtlasVaultProductionUnlockPresentationControllerBuilder()

        let controller = builder.makeController(
            selectedVaultID: selectedID,
            capabilities: .currentProduction,
            coordinator: graph.unlockCoordinator
        )
        let state = await controller.currentState()
        let coordinatorCalls = await graph.unlockCoordinator.totalCalls()

        XCTAssertEqual(state.status, .locked)
        XCTAssertEqual(state.capabilities, .currentProduction)
        XCTAssertEqual(coordinatorCalls, 0)
        XCTAssertTrue(builder.description.contains("<redacted>"))
    }

    func testLazyUnlockControllerBuilderReceivesValidatedIDCapabilitiesAndCoordinator() async throws {
        let graph = try makeDependencyGraph()
        let selectedID = try AtlasSelectedVaultID(
            validating: Self.fakeVaultID
        )

        let controller = graph.unlockControllerBuilder.makeController(
            selectedVaultID: selectedID,
            capabilities: .currentProduction,
            coordinator: graph.unlockCoordinator
        )
        let coordinatorCalls = await graph.unlockCoordinator.totalCalls()
        let controllerState = await controller.currentState()

        XCTAssertEqual(graph.unlockControllerBuilder.callCount, 1)
        XCTAssertEqual(
            graph.unlockControllerBuilder.capturedSelectedVaultID?.vaultID,
            Self.fakeVaultID
        )
        XCTAssertEqual(
            graph.unlockControllerBuilder.capturedCapabilities,
            .currentProduction
        )
        XCTAssertTrue(
            sameObject(
                try XCTUnwrap(
                    graph.unlockControllerBuilder.capturedCoordinator
                ),
                graph.unlockCoordinator
            )
        )
        XCTAssertEqual(coordinatorCalls, 0)
        XCTAssertEqual(controllerState.status, .locked)
    }

    func testFactoryAndDependencyDescriptionsAreFixedAndRedacted() throws {
        let graph = try makeDependencyGraph()
        let dependencies = graph.dependencies
        let factory = AtlasVaultProductionHostFactory(
            dependencies: dependencies,
            builder: graph.hostBuilder
        )
        let rendered = [
            dependencies.description,
            dependencies.debugDescription,
            factory.description,
            factory.debugDescription,
        ].joined(separator: "\n")

        XCTAssertTrue(rendered.contains("<redacted>"))
        for forbidden in [
            Self.fakeQuery,
            Self.fakeJobID,
            Self.fakeVaultID,
            "FAKE_DEPENDENCY_MARKER",
        ] {
            XCTAssertFalse(rendered.contains(forbidden), forbidden)
        }
    }

    func testProductionSourcesHaveNoForbiddenDependencyOrSideEffect() throws {
        let source = try phaseSources()
        for forbidden in [
            "import SwiftUI",
            "@main",
            "AtlasIOSHostApp",
            "AtlasRootView",
            "refreshSidebarData",
            "SearchViewModel",
            "AtlasAPIClient",
            "AtlasLocalCache",
            "/api/saved-searches",
            "/api/tracker",
            "savedSearches",
            "savedJobs",
            "tracker",
            "applicationNotes",
            "profileSnippets",
            "draftMetadata",
            "AtlasKeychain",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "FileManager",
            "Data.write",
            "URLSession",
            "UserDefaults",
            "@AppStorage",
            "@SceneStorage",
            "CryptoKit",
            "AtlasVaultRecordCrypto",
            "suppliedTestVaultKey",
            "AtlasVaultMutation",
            "AtlasVaultSave",
            "Task {",
            "Task.detached",
            "NavigationStack",
            "NavigationLink",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testFactoryHasNoDefaultConcreteConstructorOrAutomaticOperation() throws {
        let source = try factorySource()

        for forbidden in [
            "init()",
            ".production(",
            "func production(",
            "static func production",
            ".start(",
            ".subscribe(",
            ".restore(",
            ".selectVaultID(",
            ".search(",
            ".status(",
            ".handle(",
            ".dispatch(",
            ".cancel(",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        XCTAssertTrue(source.contains("builder.makeHost("))
    }

    func testCoordinationModelsAreNotCodableAndProtocolShapesStayNarrow() throws {
        let source = try phaseSources()
        XCTAssertFalse(source.contains("Codable"))

        let contract = try contractsSource()
        for required in [
            "public protocol AtlasPublicJobSearching",
            "public protocol AtlasPublicSnapshotRestoring",
            "public protocol AtlasVaultIDSelecting",
            "public protocol AtlasVaultProductionHosting",
            "public protocol AtlasVaultUnlockPresentationControllerBuilding",
            "public struct AtlasVaultProductionHostDependencies",
        ] {
            XCTAssertTrue(contract.contains(required), required)
        }
        for property in [
            "public let publicJobs:",
            "public let publicSnapshotRestorer:",
            "public let vaultIDSelector:",
            "public let runtime:",
            "public let lifecycle:",
            "public let presentation:",
            "public let presentationOwner:",
            "public let unlockCoordinator:",
            "public let unlockControllerBuilder:",
        ] {
            XCTAssertTrue(contract.contains(property), property)
        }
        let factory = try factorySource()
        XCTAssertTrue(
            factory.contains("public protocol AtlasVaultProductionHostBuilding")
        )
        XCTAssertTrue(
            factory.contains("public struct AtlasVaultProductionHostFactory")
        )
    }

    func testNoAtlasVaultOrReviewEnvironmentArtifactExists() throws {
        let enumerator = FileManager.default.enumerator(
            at: repositoryRootURL(),
            includingPropertiesForKeys: [.isDirectoryKey],
            // Hidden review artifacts are required findings; `.git` is skipped below.
            options: []
        )
        guard let enumerator else {
            XCTFail("Unable to enumerate the worktree")
            return
        }

        for case let url as URL in enumerator {
            if url.lastPathComponent == ".git" {
                enumerator.skipDescendants()
                continue
            }
            if url.pathExtension == "atlasvault"
                || url.lastPathComponent == ".venv-review"
            {
                XCTFail("Unexpected review artifact: \(url.lastPathComponent)")
                return
            }
        }
    }

    func testPhaseFileSetIsExactlyTheAllowlistedSixFiles() throws {
        let expected = Set([
            "docs/architecture/phase2d56_runtime_neutral_production_host.md",
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionHostContracts.swift",
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionPresentationPipeline.swift",
            "apps/apple/Sources/AtlasUI/AtlasVaultProductionHost.swift",
            "apps/apple/Tests/AtlasUITests/AtlasVaultProductionHostTests.swift",
            "apps/apple/Tests/AtlasUITests/AtlasVaultProductionHostFactoryTests.swift",
        ])

        XCTAssertEqual(try phaseChangedFiles(), expected)
    }

    func testPhaseFileDiscoveryIsIndependentOfRecentCommitSubjects() throws {
        let source = try String(
            contentsOf: URL(fileURLWithPath: #filePath),
            encoding: .utf8
        )
        let discovery = try sourceSection(
            source,
            from: "    private func phase"
                + "ChangedFiles() throws -> Set<String> {",
            to: "    private func gitOutput"
                + "(_ arguments: [String]) throws -> String {"
        )

        XCTAssertTrue(discovery.contains("--diff-filter=A"))
        XCTAssertTrue(discovery.contains("--is-shallow-repository"))
        XCTAssertTrue(discovery.contains("phase2D56BoundaryMarker"))
        XCTAssertFalse(discovery.contains("\"-n\""))
        XCTAssertFalse(
            discovery.contains(
                "Test AtlasVault runtime-neutral production host"
            )
        )
        XCTAssertFalse(
            discovery.contains(
                "Add AtlasVault runtime-neutral production host tests"
            )
        )
    }

    private func makeDependencyGraph() throws -> FactoryDependencyGraph {
        let publicJobs = try FactoryPublicJobsSpy(
            health: publicHealth(),
            searchResult: makeSearchResult(),
            sources: [publicSource()],
            updates: [publicUpdate()],
            detail: publicDetail()
        )
        let snapshotRestorer = FactorySnapshotRestorerSpy(
            snapshot: try makePublicSnapshot()
        )
        let vaultSelector = FactoryVaultSelectorSpy(selection: .none)
        let runtime = FactoryRuntimeSpy()
        let lifecycle = FactoryLifecycleSpy()
        let presentation = FactoryPresentationSpy()
        let presentationOwner = FactoryPresentationOwnerSpy()
        let unlockCoordinator = FactoryUnlockCoordinatorSpy()
        let unlockControllerBuilder =
            FactoryUnlockControllerBuilderSpy()
        let host = try FactoryProductionHostSpy(
            state: flowState(),
            searchResult: makeSearchResult()
        )
        let hostBuilder = FactoryHostBuilderSpy(host: host)
        let dependencies = AtlasVaultProductionHostDependencies(
            publicJobs: publicJobs,
            publicSnapshotRestorer: snapshotRestorer,
            vaultIDSelector: vaultSelector,
            runtime: runtime,
            lifecycle: lifecycle,
            presentation: presentation,
            presentationOwner: presentationOwner,
            unlockCoordinator: unlockCoordinator,
            unlockControllerBuilder: unlockControllerBuilder
        )
        return FactoryDependencyGraph(
            publicJobs: publicJobs,
            snapshotRestorer: snapshotRestorer,
            vaultSelector: vaultSelector,
            runtime: runtime,
            lifecycle: lifecycle,
            presentation: presentation,
            presentationOwner: presentationOwner,
            unlockCoordinator: unlockCoordinator,
            unlockControllerBuilder: unlockControllerBuilder,
            host: host,
            hostBuilder: hostBuilder,
            dependencies: dependencies
        )
    }

    private func publicJob() -> AtlasLockedPublicJob {
        AtlasLockedPublicJob(
            id: Self.fakeJobID,
            title: "Public role",
            organization: "Fake Public Organization",
            location: "Remote",
            closingDateText: "2099-12-31"
        )
    }

    private func makeSearchResult() throws -> AtlasPublicJobSearchResult {
        try AtlasPublicJobSearchResult(
            jobs: [publicJob()],
            total: 1,
            limit: 25,
            offset: 0
        )
    }

    private func publicHealth() throws -> AtlasPublicServiceHealth {
        try AtlasPublicServiceHealth(
            availability: .available,
            openJobCount: 1,
            enabledSourceCount: 1,
            lastSyncAt: Date(timeIntervalSince1970: 1_725_400_000)
        )
    }

    private func publicSource() throws -> AtlasPublicSourceStatus {
        try AtlasPublicSourceStatus(
            sourceID: "FAKE_PUBLIC_SOURCE",
            displayName: "Fake Public Source",
            availability: .available,
            openJobCount: 1
        )
    }

    private func publicUpdate() throws -> AtlasPublicUpdateStatus {
        try AtlasPublicUpdateStatus(
            sourceID: "FAKE_PUBLIC_SOURCE",
            observedAt: Date(timeIntervalSince1970: 1_725_400_000),
            fetchedJobCount: 1,
            changedJobCount: 1,
            closedJobCount: 0
        )
    }

    private func publicDetail() throws -> AtlasPublicJobDetailResult {
        try AtlasPublicJobDetailResult(
            reference: try AtlasPublicJobReference(
                publicJobID: Self.fakeJobID
            ),
            job: publicJob(),
            detailText: "Fake public detail"
        )
    }

    private func makePublicSnapshot() throws -> AtlasProductionPublicSnapshot {
        AtlasProductionPublicSnapshot(
            savedAt: Date(timeIntervalSince1970: 1_725_400_000),
            health: try publicHealth(),
            jobs: [publicJob()],
            sources: [try publicSource()],
            updates: [try publicUpdate()]
        )
    }

    private func publicShell() -> AtlasLockedPublicShellModel {
        AtlasLockedPublicShellModel(
            vaultStatus: .locked,
            serviceStatus: .available,
            cacheFreshness: .current,
            searchQuery: Self.fakeQuery,
            publicJobs: [publicJob()],
            isSearching: false,
            canRequestUnlock: true
        )
    }

    private func flowState() -> AtlasLockedShellUnlockFlowState {
        AtlasLockedShellUnlockFlowState(
            publicShell: publicShell(),
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: nil,
                status: .locked
            ),
            isUnlockPanelPresented: false
        )
    }

    private func contractsSource() throws -> String {
        try String(
            contentsOf: sourceURL(
                named: "AtlasVaultProductionHostContracts.swift"
            ),
            encoding: .utf8
        )
    }

    private func factorySource() throws -> String {
        try String(
            contentsOf: sourceURL(
                named: "AtlasVaultProductionHostFactory.swift"
            ),
            encoding: .utf8
        )
    }

    private func phaseSources() throws -> String {
        try [contractsSource(), factorySource()].joined(separator: "\n")
    }

    private func sourceSection(
        _ source: String,
        from startMarker: String,
        to endMarker: String
    ) throws -> String {
        let start = try XCTUnwrap(source.range(of: startMarker))
        let end = try XCTUnwrap(
            source.range(
                of: endMarker,
                range: start.upperBound..<source.endIndex
            )
        )
        return String(source[start.lowerBound..<end.lowerBound])
    }

    private func sourceURL(named filename: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(filename)
    }

    private func testURL(named filename: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent(filename)
    }

    private func architectureDocumentURL() -> URL {
        repositoryRootURL()
            .appendingPathComponent("docs/architecture")
            .appendingPathComponent(
                "phase2d56_runtime_neutral_production_host.md"
            )
    }

    private func repositoryRootURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private func phaseChangedFiles() throws -> Set<String> {
        let isShallow = try gitOutput([
            "rev-parse",
            "--is-shallow-repository",
        ]) == "true"
        let committed: String
        if isShallow {
            committed = try gitOutput([
                "grep",
                "-l",
                "-F",
                phase2D56BoundaryMarker,
                "--",
                "docs",
                "apps/apple/Sources/AtlasUI",
                "apps/apple/Tests/AtlasUITests",
            ])
        } else {
            let introduction = try XCTUnwrap(
                try gitOutput([
                    "log",
                    "--diff-filter=A",
                    "--format=%H",
                    "--",
                    phase2D56AnchorPath,
                ])
                .split(separator: "\n")
                .first
                .map(String.init),
                "Phase 2D-56 path introduction is unavailable"
            )
            let baseline = try gitOutput([
                "rev-parse",
                "\(introduction)^",
            ])
            committed = try gitOutput([
                "diff",
                "--name-only",
                "\(baseline)..HEAD",
            ])
        }

        let working = try gitOutput([
            "diff",
            "--name-only",
            "HEAD",
        ])
        let untracked = try gitOutput([
            "ls-files",
            "--others",
            "--exclude-standard",
        ])
        return Set(
            [committed, working, untracked]
                .joined(separator: "\n")
                .split(separator: "\n")
                .map(String.init)
        )
    }

    private var phase2D56BoundaryMarker: String {
        "Phase 2D-56 repository boundary"
    }

    private var phase2D56AnchorPath: String {
        "apps/apple/Tests/AtlasUITests/AtlasVaultProductionHostTests.swift"
    }

    private func gitOutput(_ arguments: [String]) throws -> String {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["git"] + arguments
        process.currentDirectoryURL = repositoryRootURL()
        process.standardOutput = output
        process.standardError = output
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw NSError(
                domain: "AtlasVaultProductionHostFactoryTests",
                code: Int(process.terminationStatus),
                userInfo: [
                    NSLocalizedDescriptionKey:
                        String(decoding: data, as: UTF8.self),
                ]
            )
        }
        return String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

private struct FactoryDependencyGraph {
    let publicJobs: FactoryPublicJobsSpy
    let snapshotRestorer: FactorySnapshotRestorerSpy
    let vaultSelector: FactoryVaultSelectorSpy
    let runtime: FactoryRuntimeSpy
    let lifecycle: FactoryLifecycleSpy
    let presentation: FactoryPresentationSpy
    let presentationOwner: FactoryPresentationOwnerSpy
    let unlockCoordinator: FactoryUnlockCoordinatorSpy
    let unlockControllerBuilder: FactoryUnlockControllerBuilderSpy
    let host: FactoryProductionHostSpy
    let hostBuilder: FactoryHostBuilderSpy
    let dependencies: AtlasVaultProductionHostDependencies

    func callCounts() async -> FactoryDependencyCallCounts {
        FactoryDependencyCallCounts(
            publicJobs: await publicJobs.totalCalls(),
            snapshotRestore: await snapshotRestorer.restoreCount(),
            vaultSelection: await vaultSelector.selectCount(),
            runtime: await runtime.totalCalls(),
            lifecycle: await lifecycle.totalCalls(),
            presentation: await presentation.totalCalls(),
            presentationOwner: await presentationOwner.totalCalls(),
            unlockCoordinator: await unlockCoordinator.totalCalls(),
            unlockControllerBuilder: unlockControllerBuilder.callCount,
            hostBuilder: hostBuilder.callCount,
            hostStart: await host.startCount()
        )
    }
}

private struct FactoryDependencyCallCounts: Equatable {
    static let zero = FactoryDependencyCallCounts(
        publicJobs: 0,
        snapshotRestore: 0,
        vaultSelection: 0,
        runtime: 0,
        lifecycle: 0,
        presentation: 0,
        presentationOwner: 0,
        unlockCoordinator: 0,
        unlockControllerBuilder: 0,
        hostBuilder: 0,
        hostStart: 0
    )

    let publicJobs: Int
    let snapshotRestore: Int
    let vaultSelection: Int
    let runtime: Int
    let lifecycle: Int
    let presentation: Int
    let presentationOwner: Int
    let unlockCoordinator: Int
    let unlockControllerBuilder: Int
    let hostBuilder: Int
    let hostStart: Int
}

private actor FactoryPublicJobsSpy: AtlasPublicJobSearching {
    private let healthValue: AtlasPublicServiceHealth
    private let searchValue: AtlasPublicJobSearchResult
    private let sourceValues: [AtlasPublicSourceStatus]
    private let updateValues: [AtlasPublicUpdateStatus]
    private let detailValue: AtlasPublicJobDetailResult
    private var healthCalls = 0
    private var searchCalls = 0
    private var sourceCalls = 0
    private var updateCalls = 0
    private var detailCalls = 0

    init(
        health: AtlasPublicServiceHealth,
        searchResult: AtlasPublicJobSearchResult,
        sources: [AtlasPublicSourceStatus],
        updates: [AtlasPublicUpdateStatus],
        detail: AtlasPublicJobDetailResult
    ) throws {
        healthValue = health
        searchValue = searchResult
        sourceValues = sources
        updateValues = updates
        detailValue = detail
    }

    func health() async throws(AtlasPublicJobServiceError)
        -> AtlasPublicServiceHealth
    {
        healthCalls += 1
        return healthValue
    }

    func search(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult {
        searchCalls += 1
        return searchValue
    }

    func sources() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicSourceStatus]
    {
        sourceCalls += 1
        return sourceValues
    }

    func updates() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicUpdateStatus]
    {
        updateCalls += 1
        return updateValues
    }

    func detail(
        for reference: AtlasPublicJobReference
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobDetailResult {
        detailCalls += 1
        return detailValue
    }

    func calls() -> (
        health: Int,
        search: Int,
        sources: Int,
        updates: Int,
        detail: Int
    ) {
        (
            healthCalls,
            searchCalls,
            sourceCalls,
            updateCalls,
            detailCalls
        )
    }

    func totalCalls() -> Int {
        healthCalls + searchCalls + sourceCalls + updateCalls + detailCalls
    }
}

private actor FactorySnapshotRestorerSpy: AtlasPublicSnapshotRestoring {
    private let snapshot: AtlasProductionPublicSnapshot?
    private var calls = 0

    init(snapshot: AtlasProductionPublicSnapshot?) {
        self.snapshot = snapshot
    }

    func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
    {
        calls += 1
        return snapshot
    }

    func restoreCount() -> Int {
        calls
    }
}

private actor FactoryVaultSelectorSpy: AtlasVaultIDSelecting {
    private let selection: AtlasVaultIDSelection
    private var calls = 0

    init(selection: AtlasVaultIDSelection) {
        self.selection = selection
    }

    func selectVaultID() async throws(AtlasVaultIDSelectionError)
        -> AtlasVaultIDSelection
    {
        calls += 1
        return selection
    }

    func selectCount() -> Int {
        calls
    }
}

private actor FactoryRuntimeSpy: AtlasVaultRuntimeFacading {
    private var statusCalls = 0
    private var activationCalls = 0
    private var lockCalls = 0
    private var applyCalls = 0

    func status() async -> AtlasVaultRuntimeStatus {
        statusCalls += 1
        return .locked
    }

    func activate(_ request: AtlasVaultRuntimeActivationRequest) async throws {
        activationCalls += 1
    }

    func lock() async {
        lockCalls += 1
    }

    func apply(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async throws -> AtlasVaultSaveOutcome {
        applyCalls += 1
        return .committed
    }

    func totalCalls() -> Int {
        statusCalls + activationCalls + lockCalls + applyCalls
    }
}

private actor FactoryLifecycleSpy: AtlasVaultLifecycleCoordinating {
    private var handleCalls = 0
    private var statusCalls = 0

    func handle(_ event: AtlasVaultLifecycleEvent) async {
        handleCalls += 1
    }

    func status() async -> AtlasVaultLifecycleStatus {
        statusCalls += 1
        return AtlasVaultLifecycleStatus(
            lastEvent: nil,
            hasPendingGraceLock: false,
            failure: nil
        )
    }

    func totalCalls() -> Int {
        handleCalls + statusCalls
    }
}

private struct FactoryNeverPresentationSource:
    AtlasVaultPresentationUpdateSourcing
{
    func updates() async -> AsyncStream<AtlasVaultPresentationUpdate> {
        AsyncStream { continuation in
            continuation.finish()
        }
    }
}

private actor FactoryPresentationSpy:
    AtlasVaultProductionPresentationCoordinating
{
    private let backing = AtlasVaultObservablePresentationAdapter(
        source: FactoryNeverPresentationSource()
    )
    private var subscribeCalls = 0
    private var snapshotCalls = 0
    private var startCalls = 0
    private var publishCalls = 0
    private var finishCalls = 0

    func start() async -> Bool {
        startCalls += 1
        return true
    }

    func publish(
        _ value: AtlasVaultPrivateFreePresentationSnapshot
    ) async -> Bool {
        publishCalls += 1
        return true
    }

    func finish() async -> Bool {
        finishCalls += 1
        return true
    }

    func subscribe() async -> AtlasVaultPresentationSubscription {
        subscribeCalls += 1
        return await backing.subscribe()
    }

    func currentSnapshot() async -> AtlasVaultPresentationSnapshot {
        snapshotCalls += 1
        return await backing.currentSnapshot()
    }

    func totalCalls() -> Int {
        subscribeCalls
            + snapshotCalls
            + startCalls
            + publishCalls
            + finishCalls
    }
}

private actor FactoryPresentationOwnerRecorder {
    private var calls = 0

    func record() {
        calls += 1
    }

    func totalCalls() -> Int {
        calls
    }
}

private final class FactoryPresentationOwnerSpy:
    AtlasVaultProductionPresentationOwnerResetting,
    @unchecked Sendable
{
    private let recorder = FactoryPresentationOwnerRecorder()

    @MainActor
    func resetPresentation(
        to state: AtlasLockedShellUnlockFlowState,
        generation: AtlasVaultProductionHostGeneration
    ) async -> Bool {
        await recorder.record()
        return true
    }

    @MainActor
    func supersedePresentationGeneration(
        _ generation: AtlasVaultProductionHostGeneration
    ) async {}

    func totalCalls() async -> Int {
        await recorder.totalCalls()
    }
}

private actor FactoryUnlockCoordinatorSpy:
    AtlasVaultUnlockRequestCoordinating
{
    private var dispatchCalls = 0
    private var cancelCalls = 0

    func dispatch(_ request: AtlasVaultUnlockRequest) async throws {
        dispatchCalls += 1
    }

    func cancel(_ request: AtlasVaultUnlockRequest) async -> Bool {
        cancelCalls += 1
        return true
    }

    func totalCalls() -> Int {
        dispatchCalls + cancelCalls
    }
}

private actor FactoryUnlockControllerSpy:
    AtlasVaultUnlockPresentationControlling
{
    private var state = AtlasVaultUnlockPresentationState(
        capabilities: .currentProduction,
        selectedMethod: nil,
        status: .locked
    )

    func currentState() async -> AtlasVaultUnlockPresentationState {
        state
    }

    func select(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasVaultUnlockPresentationState {
        state = AtlasVaultUnlockPresentationState(
            capabilities: .currentProduction,
            selectedMethod: method,
            status: method == nil ? .locked : .ready
        )
        return state
    }

    func submit(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration?
    ) async -> AtlasVaultUnlockPresentationState {
        state
    }

    func cancel() async -> AtlasVaultUnlockPresentationState {
        state
    }

    func didDisappear() async -> AtlasVaultUnlockPresentationState {
        state
    }

    func hostDidLock() async -> AtlasVaultUnlockPresentationState {
        state
    }
}

private final class FactoryUnlockControllerBuilderSpy:
    AtlasVaultUnlockPresentationControllerBuilding,
    @unchecked Sendable
{
    private(set) var callCount = 0
    private(set) var capturedSelectedVaultID: AtlasSelectedVaultID?
    private(set) var capturedCapabilities: AtlasVaultUnlockCapabilities?
    private(set) var capturedCoordinator:
        (any AtlasVaultUnlockRequestCoordinating)?
    private let controller = FactoryUnlockControllerSpy()

    func makeController(
        selectedVaultID: AtlasSelectedVaultID,
        capabilities: AtlasVaultUnlockCapabilities,
        coordinator: any AtlasVaultUnlockRequestCoordinating
    ) -> any AtlasVaultUnlockPresentationControlling {
        callCount += 1
        capturedSelectedVaultID = selectedVaultID
        capturedCapabilities = capabilities
        capturedCoordinator = coordinator
        return controller
    }
}

private actor FactoryProductionHostSpy: AtlasVaultProductionHosting {
    private let state: AtlasLockedShellUnlockFlowState
    private let searchResult: AtlasPublicJobSearchResult
    private var startCalls = 0
    private var stopCalls = 0
    private var currentCalls = 0
    private var searchCalls = 0
    private var panelCalls = 0
    private var selectCalls = 0
    private var submitCalls = 0
    private var cancelCalls = 0
    private var disappearanceCalls = 0
    private var lockCalls = 0
    private var lifecycleCalls = 0

    init(
        state: AtlasLockedShellUnlockFlowState,
        searchResult: AtlasPublicJobSearchResult
    ) throws {
        self.state = state
        self.searchResult = searchResult
    }

    func start() async throws -> AtlasLockedShellUnlockFlowState {
        startCalls += 1
        return state
    }

    func stop() async -> AtlasLockedShellUnlockFlowState {
        stopCalls += 1
        return state
    }

    func currentFlowState() async -> AtlasLockedShellUnlockFlowState {
        currentCalls += 1
        return state
    }

    func searchPublicJobs(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult {
        searchCalls += 1
        return searchResult
    }

    func requestUnlockPanel() async -> AtlasLockedShellUnlockFlowState {
        panelCalls += 1
        return state
    }

    func selectUnlockMethod(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasLockedShellUnlockFlowState {
        selectCalls += 1
        return state
    }

    func submitUnlock(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration?
    ) async -> AtlasLockedShellUnlockFlowState {
        submitCalls += 1
        return state
    }

    func cancelUnlock() async -> AtlasLockedShellUnlockFlowState {
        cancelCalls += 1
        return state
    }

    func unlockPanelDidDisappear() async -> AtlasLockedShellUnlockFlowState {
        disappearanceCalls += 1
        return state
    }

    func lock() async -> AtlasLockedShellUnlockFlowState {
        lockCalls += 1
        return state
    }

    func handleLifecycleEvent(
        _ event: AtlasVaultLifecycleEvent
    ) async -> AtlasLockedShellUnlockFlowState {
        lifecycleCalls += 1
        return state
    }

    func calls() -> (
        start: Int,
        stop: Int,
        current: Int,
        search: Int,
        panel: Int,
        select: Int,
        submit: Int,
        cancel: Int,
        disappearance: Int,
        lock: Int,
        lifecycle: Int
    ) {
        (
            startCalls,
            stopCalls,
            currentCalls,
            searchCalls,
            panelCalls,
            selectCalls,
            submitCalls,
            cancelCalls,
            disappearanceCalls,
            lockCalls,
            lifecycleCalls
        )
    }

    func startCount() -> Int {
        startCalls
    }
}

private final class FactoryHostBuilderSpy:
    AtlasVaultProductionHostBuilding,
    @unchecked Sendable
{
    private(set) var callCount = 0
    private(set) var capturedDependencies:
        AtlasVaultProductionHostDependencies?
    private let host: FactoryProductionHostSpy

    init(host: FactoryProductionHostSpy) {
        self.host = host
    }

    func makeHost(
        dependencies: AtlasVaultProductionHostDependencies
    ) -> any AtlasVaultProductionHosting {
        callCount += 1
        capturedDependencies = dependencies
        return host
    }
}

private func storedPropertyLabels(_ value: Any) -> Set<String> {
    Set(Mirror(reflecting: value).children.compactMap(\.label))
}

private func sameObject<ProtocolValue, Concrete: AnyObject>(
    _ value: ProtocolValue,
    _ expected: Concrete
) -> Bool {
    ObjectIdentifier(value as AnyObject) == ObjectIdentifier(expected)
}

private func requireSendable<Value: Sendable>(_ value: Value) {}
