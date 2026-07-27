import Foundation
import Security
import Synchronization
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSPrivateSavedSearchEndToEndTests: XCTestCase {
    func testLocalAndRecoverySavedSearchCreateListDeleteJourney()
        async throws
    {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let keychain = PrivateSearchE2EKeychainClient()
        let directory = PrivateSearchE2EDirectoryLocator(root: root)
        let savedSearchTimestamps =
            PrivateSearchE2ETimestampProvider()

        let first = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: savedSearchTimestamps
        )
        XCTAssertEqual(keychain.operationCount(), 0)
        XCTAssertEqual(directory.callCount(), 0)
        let firstContext = try XCTUnwrap(
            first.savedSearchContextForTesting
        )
        XCTAssertEqual(firstContext.owner.status, .hidden)
        XCTAssertTrue(firstContext.owner.items.isEmpty)

        let selected = try await createAndUnlock(
            with: first,
            keychain: keychain
        )
        XCTAssertEqual(firstContext.owner.status, .ready)
        XCTAssertTrue(firstContext.owner.items.isEmpty)
        let publicShellBeforeCreate =
            first.presentationOwner.flowState.publicShell

        let privateName = "PRIVATE_SAVED_SEARCH_NAME_2D63"
        let privateQuery = "PRIVATE_SAVED_SEARCH_QUERY_2D63"
        let editedPrivateName = "PRIVATE_EDITED_NAME_2D64"
        let editedPrivateQuery = "PRIVATE_EDITED_QUERY_2D64"
        await firstContext.actions.create(
            AtlasVaultSavedSearchDraft(
                name: privateName,
                searchText: privateQuery
            )
        )
        XCTAssertEqual(firstContext.owner.status, .ready)
        XCTAssertEqual(firstContext.owner.items.count, 1)
        XCTAssertEqual(firstContext.owner.items[0].name, privateName)
        XCTAssertEqual(
            firstContext.owner.items[0].request.text,
            privateQuery
        )
        let firstPresentationIdentifier =
            firstContext.owner.items[0].id
        XCTAssertEqual(
            first.presentationOwner.flowState.publicShell,
            publicShellBeforeCreate
        )

        let createdStoreBytes = try storeBytes(
            root: root,
            selected: selected
        )
        let createdStoreText = String(
            decoding: createdStoreBytes,
            as: UTF8.self
        )
        for plaintext in [
            privateName,
            privateQuery,
            "saved_search",
            "\"name\"",
            "\"summary\"",
            "\"request\"",
        ] {
            XCTAssertFalse(
                createdStoreText.contains(plaintext),
                plaintext
            )
        }
        let createdStore = try AtlasVaultLocalStoreIO.decode(
            createdStoreBytes
        )
        XCTAssertEqual(createdStore.records.count, 1)
        XCTAssertFalse(createdStore.records[0].deleted)
        XCTAssertFalse(createdStore.records[0].ciphertext.isEmpty)
        let localVaultKey = try XCTUnwrap(
            AtlasKeychainVaultKeyStore(client: keychain)
                .loadVaultKey(for: selected.vaultID)
        )
        let localSession = try AtlasVaultUnlockedSession(
            vaultID: selected.vaultID,
            vaultKey: localVaultKey
        )
        let createdHydrated = try XCTUnwrap(
            AtlasVaultRecordHydrator()
                .hydrate(
                    records: createdStore.records,
                    session: localSession
                )
                .savedSearches
                .first
        )

        await firstContext.actions.update(
            firstPresentationIdentifier,
            draft: AtlasVaultSavedSearchDraft(
                name: editedPrivateName,
                searchText: editedPrivateQuery
            )
        )
        XCTAssertEqual(firstContext.owner.status, .ready)
        XCTAssertEqual(firstContext.owner.items.count, 1)
        XCTAssertEqual(
            firstContext.owner.items[0].id,
            firstPresentationIdentifier
        )
        XCTAssertEqual(
            firstContext.owner.items[0].name,
            editedPrivateName
        )
        XCTAssertEqual(
            firstContext.owner.items[0].request.text,
            editedPrivateQuery
        )

        let editedStoreBytes = try storeBytes(
            root: root,
            selected: selected
        )
        let editedStoreText = String(
            decoding: editedStoreBytes,
            as: UTF8.self
        )
        for plaintext in [
            privateName,
            privateQuery,
            editedPrivateName,
            editedPrivateQuery,
        ] {
            XCTAssertFalse(editedStoreText.contains(plaintext), plaintext)
        }
        let editedStore = try AtlasVaultLocalStoreIO.decode(
            editedStoreBytes
        )
        XCTAssertEqual(editedStore.records.count, 1)
        XCTAssertEqual(
            editedStore.records[0].id,
            createdStore.records[0].id
        )
        XCTAssertEqual(
            editedStore.records[0].keyID,
            createdStore.records[0].keyID
        )
        XCTAssertNotEqual(
            editedStore.records[0].revision,
            createdStore.records[0].revision
        )
        XCTAssertEqual(
            editedStore.records[0].parentRevision,
            createdStore.records[0].revision
        )
        let editedHydrated = try XCTUnwrap(
            AtlasVaultRecordHydrator()
                .hydrate(
                    records: editedStore.records,
                    session: localSession
                )
                .savedSearches
                .first
        )
        XCTAssertEqual(editedHydrated.payload.name, editedPrivateName)
        XCTAssertEqual(
            editedHydrated.payload.request.text,
            editedPrivateQuery
        )
        XCTAssertEqual(
            editedHydrated.payload.createdAt,
            createdHydrated.payload.createdAt
        )
        XCTAssertEqual(
            editedHydrated.clientCreatedAt,
            createdHydrated.clientCreatedAt
        )
        XCTAssertNotEqual(
            editedHydrated.payload.updatedAt,
            createdHydrated.payload.updatedAt
        )
        XCTAssertNotEqual(
            editedHydrated.clientUpdatedAt,
            createdHydrated.clientUpdatedAt
        )
        XCTAssertEqual(
            first.presentationOwner.flowState.publicShell,
            publicShellBeforeCreate
        )

        let recoveryCode = try await configureRecovery(
            with: first
        )
        await firstContext.actions.lock()
        XCTAssertTrue(firstContext.owner.items.isEmpty)
        XCTAssertEqual(firstContext.owner.status, .hidden)
        _ = await first.stop()

        let second = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: savedSearchTimestamps
        )
        try await unlockExistingLocally(with: second)
        let secondContext = try XCTUnwrap(
            second.savedSearchContextForTesting
        )
        XCTAssertEqual(secondContext.owner.items.count, 1)
        XCTAssertEqual(
            secondContext.owner.items[0].name,
            editedPrivateName
        )
        XCTAssertEqual(
            secondContext.owner.items[0].request.text,
            editedPrivateQuery
        )
        XCTAssertNotEqual(
            secondContext.owner.items[0].id,
            firstPresentationIdentifier
        )
        let deletionIdentifier = secondContext.owner.items[0].id
        await secondContext.actions.delete(deletionIdentifier)
        XCTAssertEqual(secondContext.owner.status, .ready)
        XCTAssertTrue(secondContext.owner.items.isEmpty)

        let deletedBytes = try storeBytes(
            root: root,
            selected: selected
        )
        let deletedText = String(decoding: deletedBytes, as: UTF8.self)
        XCTAssertFalse(deletedText.contains(privateName))
        XCTAssertFalse(deletedText.contains(privateQuery))
        XCTAssertFalse(deletedText.contains(editedPrivateName))
        XCTAssertFalse(deletedText.contains(editedPrivateQuery))
        XCTAssertFalse(deletedText.contains("saved_search"))
        let deletedStore = try AtlasVaultLocalStoreIO.decode(
            deletedBytes
        )
        XCTAssertEqual(deletedStore.records.count, 1)
        XCTAssertTrue(deletedStore.records[0].deleted)
        let tombstoneCiphertext = try XCTUnwrap(
            Data(base64Encoded: deletedStore.records[0].ciphertext)
        )
        XCTAssertEqual(
            tombstoneCiphertext.count,
            AtlasVaultRecordCrypto.gcmTagByteCount
        )
        let tombstoneVaultKey = try XCTUnwrap(
            AtlasKeychainVaultKeyStore(client: keychain)
                .loadVaultKey(for: selected.vaultID)
        )
        XCTAssertEqual(
            try AtlasVaultRecordCrypto.open(
                record: deletedStore.records[0],
                vaultKey: tombstoneVaultKey,
                vaultID: selected.vaultID
            ),
            Data()
        )
        _ = await second.stop()

        let third = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: savedSearchTimestamps
        )
        try await unlockExistingLocally(with: third)
        let thirdContext = try XCTUnwrap(
            third.savedSearchContextForTesting
        )
        XCTAssertTrue(thirdContext.owner.items.isEmpty)
        await thirdContext.actions.create(
            AtlasVaultSavedSearchDraft(
                name: "RECOVERY_SESSION_PRIVATE_NAME",
                searchText: "RECOVERY_SESSION_PRIVATE_QUERY"
            )
        )
        XCTAssertEqual(thirdContext.owner.items.count, 1)
        _ = await third.stop()

        let keyStore = AtlasKeychainVaultKeyStore(client: keychain)
        try keyStore.deleteVaultKey(for: selected.vaultID)
        XCTAssertNil(
            try keyStore.loadVaultKey(for: selected.vaultID)
        )

        let recoveryPublicJobs = PrivateSearchE2EPublicJobs()
        let recoveryOnly = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: savedSearchTimestamps,
            publicJobs: recoveryPublicJobs
        )
        _ = try await recoveryOnly.start()
        await recoveryOnly.publicShellActions.requestUnlock()
        XCTAssertEqual(
            recoveryOnly.presentationOwner.flowState
                .unlockPanelState?.availableMethods,
            [.recoveryKey]
        )
        await recoveryOnly.unlockActions.select(.recoveryKey)
        let wrong = await recoveryOnly.unlockActions.submit(
            .recoveryKey(
                AtlasVaultInMemorySecretBuffer(
                    bytes: Data("AVRK1-INVALID".utf8)
                )
            )
        )
        XCTAssertEqual(wrong, .failed)
        let recovered = await recoveryOnly.unlockActions.submit(
            .recoveryKey(
                AtlasVaultInMemorySecretBuffer(
                    bytes: Data(recoveryCode.utf8)
                )
            )
        )
        XCTAssertEqual(recovered, .unlocked)

        let recoveryContext = try XCTUnwrap(
            recoveryOnly.savedSearchContextForTesting
        )
        XCTAssertEqual(recoveryContext.owner.items.count, 1)
        XCTAssertEqual(
            recoveryContext.owner.items[0].name,
            "RECOVERY_SESSION_PRIVATE_NAME"
        )
        let recoveryIdentifier = recoveryContext.owner.items[0].id
        await recoveryContext.actions.update(
            recoveryIdentifier,
            draft: AtlasVaultSavedSearchDraft(
                name: "RECOVERY_EDITED_PRIVATE_NAME",
                searchText: "recovery public criteria"
            )
        )
        XCTAssertEqual(
            recoveryContext.owner.items[0].name,
            "RECOVERY_EDITED_PRIVATE_NAME"
        )
        XCTAssertNil(
            try keyStore.loadVaultKey(for: selected.vaultID)
        )

        await recoveryContext.actions.execute(
            recoveryContext.owner.items[0].id
        )
        XCTAssertEqual(
            recoveryOnly.presentationOwner.flowState.mode,
            .lockedPublic
        )
        XCTAssertTrue(recoveryContext.owner.items.isEmpty)
        let recordedRecoveryRequest =
            await recoveryPublicJobs.lastSearchRequest()
        let recoveryRequest = try XCTUnwrap(recordedRecoveryRequest)
        XCTAssertEqual(recoveryRequest.origin, .savedSearchHandoff)
        XCTAssertEqual(recoveryRequest.query, "recovery public criteria")
        XCTAssertNil(
            try keyStore.loadVaultKey(for: selected.vaultID)
        )

        await recoveryOnly.publicShellActions.requestUnlock()
        await recoveryOnly.unlockActions.select(.recoveryKey)
        let recoveredAgain = await recoveryOnly.unlockActions.submit(
            .recoveryKey(
                AtlasVaultInMemorySecretBuffer(
                    bytes: Data(recoveryCode.utf8)
                )
            )
        )
        XCTAssertEqual(recoveredAgain, .unlocked)
        XCTAssertEqual(recoveryContext.owner.items.count, 1)
        await recoveryContext.actions.delete(
            recoveryContext.owner.items[0].id
        )
        XCTAssertTrue(recoveryContext.owner.items.isEmpty)
        XCTAssertNil(
            try keyStore.loadVaultKey(for: selected.vaultID)
        )
        _ = await recoveryOnly.stop()

        let finalStore = try AtlasVaultLocalStoreIO.decode(
            storeBytes(root: root, selected: selected)
        )
        XCTAssertEqual(finalStore.records.count, 2)
        XCTAssertTrue(finalStore.records.allSatisfy(\.deleted))
        let finalText = String(
            decoding: try storeBytes(root: root, selected: selected),
            as: UTF8.self
        )
        XCTAssertFalse(finalText.contains("RECOVERY_SESSION_PRIVATE_NAME"))
        XCTAssertFalse(finalText.contains("RECOVERY_SESSION_PRIVATE_QUERY"))
        XCTAssertFalse(finalText.contains("RECOVERY_EDITED_PRIVATE_NAME"))
        XCTAssertFalse(finalText.contains("recovery public criteria"))
    }

    func testExplicitSavedSearchExecutionLocksBeforePublicHandoff()
        async throws
    {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let keychain = PrivateSearchE2EKeychainClient()
        let directory = PrivateSearchE2EDirectoryLocator(root: root)
        let timestamps = PrivateSearchE2ETimestampProvider()
        let publicJobs = PrivateSearchE2EPublicJobs()
        let harness = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: timestamps,
            publicJobs: publicJobs
        )
        _ = try await createAndUnlock(
            with: harness,
            keychain: keychain
        )
        let context = try XCTUnwrap(
            harness.savedSearchContextForTesting
        )
        await context.actions.create(
            AtlasVaultSavedSearchDraft(
                name: "PRIVATE_HANDOFF_NAME",
                searchText: "public handoff terms"
            )
        )
        let identifier = try XCTUnwrap(context.owner.items.first?.id)
        let callsBeforeHandoff = await publicJobs.searchCallCount()

        await context.actions.execute(identifier)

        XCTAssertEqual(context.owner.status, .hidden)
        XCTAssertTrue(context.owner.items.isEmpty)
        let flow = harness.presentationOwner.flowState
        XCTAssertEqual(flow.mode, .lockedPublic)
        XCTAssertEqual(
            flow.publicShell.searchOrigin,
            .savedSearchHandoff
        )
        let callsAfterHandoff = await publicJobs.searchCallCount()
        XCTAssertEqual(callsAfterHandoff, callsBeforeHandoff + 1)
        let recordedRequest = await publicJobs.lastSearchRequest()
        let request = try XCTUnwrap(recordedRequest)
        XCTAssertEqual(request.query, "public handoff terms")
        XCTAssertEqual(request.origin, .savedSearchHandoff)
        XCTAssertFalse(
            String(reflecting: request)
                .contains("PRIVATE_HANDOFF_NAME")
        )
        _ = await harness.stop()
    }

    func testFullFilterHandoffManualResetAndStaleIDRemainPrivate()
        async throws
    {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let keychain = PrivateSearchE2EKeychainClient()
        let directory = PrivateSearchE2EDirectoryLocator(root: root)
        let timestamps = PrivateSearchE2ETimestampProvider()

        let seed = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: timestamps
        )
        let selected = try await createAndUnlock(
            with: seed,
            keychain: keychain
        )
        let seedContext = try XCTUnwrap(
            seed.savedSearchContextForTesting
        )
        await seedContext.actions.create(
            AtlasVaultSavedSearchDraft(
                name: "PRIVATE_FULL_FILTER_NAME",
                searchText: "climate policy"
            )
        )
        _ = await seed.stop()

        let fullCriteria = AtlasSearchRequest(
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
            offset: 40,
            sort: "closing_date_asc"
        )
        try replaceOnlySavedSearchRequest(
            root: root,
            selected: selected,
            keychain: keychain,
            request: fullCriteria
        )

        let publicJobs = PrivateSearchE2EPublicJobs()
        let harness = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: timestamps,
            publicJobs: publicJobs
        )
        try await unlockExistingLocally(with: harness)
        let context = try XCTUnwrap(
            harness.savedSearchContextForTesting
        )
        let sessionAIdentifier = try XCTUnwrap(
            context.owner.items.first?.id
        )

        await context.actions.execute(sessionAIdentifier)

        XCTAssertTrue(context.owner.items.isEmpty)
        XCTAssertEqual(context.owner.status, .hidden)
        let lockedFlow = harness.presentationOwner.flowState
        XCTAssertEqual(lockedFlow.mode, .lockedPublic)
        XCTAssertEqual(
            lockedFlow.publicShell.searchOrigin,
            .savedSearchHandoff
        )
        XCTAssertTrue(lockedFlow.publicShell.hasAdditionalCriteria)
        let recordedForwardedRequest =
            await publicJobs.lastSearchRequest()
        let forwarded = try XCTUnwrap(recordedForwardedRequest)
        var expected = fullCriteria
        expected.includeFacets = false
        expected.limit = 50
        expected.offset = 0
        XCTAssertEqual(forwarded.apiRequest, expected)
        XCTAssertFalse(
            String(reflecting: forwarded)
                .contains("PRIVATE_FULL_FILTER_NAME")
        )

        await harness.publicShellActions.search(query: "manual query")
        let manualFlow = harness.presentationOwner.flowState
        XCTAssertEqual(manualFlow.publicShell.searchOrigin, .manual)
        XCTAssertFalse(manualFlow.publicShell.hasAdditionalCriteria)
        let recordedManualRequest =
            await publicJobs.lastSearchRequest()
        let manualRequest = try XCTUnwrap(recordedManualRequest)
        XCTAssertEqual(
            manualRequest.apiRequest,
            AtlasSearchRequest(
                text: "manual query",
                includeFacets: false,
                limit: 50,
                offset: 0
            )
        )

        await harness.publicShellActions.requestUnlock()
        await harness.unlockActions.select(.localKey)
        let unlockedAgain = await harness.unlockActions.submit(.localKey)
        XCTAssertEqual(unlockedAgain, .unlocked)
        XCTAssertEqual(context.owner.items.count, 1)
        XCTAssertNotEqual(
            context.owner.items[0].id,
            sessionAIdentifier
        )
        let callsBeforeStale = await publicJobs.searchCallCount()

        await context.actions.execute(sessionAIdentifier)

        XCTAssertEqual(context.owner.status, .handoffFailed)
        XCTAssertEqual(context.owner.items.count, 1)
        XCTAssertEqual(
            harness.presentationOwner.flowState.mode,
            .unlockedTransition
        )
        let callsAfterStale = await publicJobs.searchCallCount()
        XCTAssertEqual(callsAfterStale, callsBeforeStale)
        _ = await harness.stop()
    }

    func testMalformedCriteriaPreservePrivateSessionAndPublicFailureStaysLocked()
        async throws
    {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let keychain = PrivateSearchE2EKeychainClient()
        let directory = PrivateSearchE2EDirectoryLocator(root: root)
        let timestamps = PrivateSearchE2ETimestampProvider()
        let seed = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: timestamps
        )
        let selected = try await createAndUnlock(
            with: seed,
            keychain: keychain
        )
        let seedContext = try XCTUnwrap(
            seed.savedSearchContextForTesting
        )
        await seedContext.actions.create(
            AtlasVaultSavedSearchDraft(
                name: "PRIVATE_MALFORMED_NAME",
                searchText: "criteria"
            )
        )
        _ = await seed.stop()
        try replaceOnlySavedSearchRequest(
            root: root,
            selected: selected,
            keychain: keychain,
            request: AtlasSearchRequest(
                text: "criteria",
                status: ["closed"]
            )
        )

        let publicJobs = PrivateSearchE2EPublicJobs()
        let malformedHarness = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: timestamps,
            publicJobs: publicJobs
        )
        try await unlockExistingLocally(with: malformedHarness)
        let malformedContext = try XCTUnwrap(
            malformedHarness.savedSearchContextForTesting
        )
        let malformedIdentifier = try XCTUnwrap(
            malformedContext.owner.items.first?.id
        )

        await malformedContext.actions.execute(malformedIdentifier)

        XCTAssertEqual(malformedContext.owner.status, .handoffFailed)
        XCTAssertEqual(malformedContext.owner.items.count, 1)
        XCTAssertEqual(
            malformedHarness.presentationOwner.flowState.mode,
            .unlockedTransition
        )
        let malformedCalls = await publicJobs.searchCallCount()
        XCTAssertEqual(malformedCalls, 0)
        _ = await malformedHarness.stop()

        try replaceOnlySavedSearchRequest(
            root: root,
            selected: selected,
            keychain: keychain,
            request: AtlasSearchRequest(text: "public failure")
        )
        let failingPublicJobs = PrivateSearchE2EPublicJobs()
        let failingHarness = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: timestamps,
            publicJobs: failingPublicJobs
        )
        try await unlockExistingLocally(with: failingHarness)
        let failingContext = try XCTUnwrap(
            failingHarness.savedSearchContextForTesting
        )
        await failingPublicJobs.failNextSearch()

        await failingContext.actions.execute(
            failingContext.owner.items[0].id
        )

        XCTAssertEqual(failingContext.owner.status, .hidden)
        XCTAssertTrue(failingContext.owner.items.isEmpty)
        let failedFlow = failingHarness.presentationOwner.flowState
        XCTAssertEqual(failedFlow.mode, .lockedPublic)
        XCTAssertEqual(
            failedFlow.publicShell.searchOrigin,
            .savedSearchHandoff
        )
        XCTAssertEqual(
            failedFlow.publicShell.serviceStatus,
            .unavailable
        )
        let failedCalls = await failingPublicJobs.searchCallCount()
        XCTAssertEqual(failedCalls, 1)
        _ = await failingHarness.stop()
    }

    func testProductionCompositionContainsPrivateSavedSearchJourney()
        throws
    {
        let feature = try requiredSource(
            named: "AtlasVaultSavedSearchFeature.swift"
        )
        let host = try requiredSource(
            named: "AtlasVaultProductionHost.swift"
        )
        let harness = try requiredSource(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )

        XCTAssertTrue(feature.contains("AtlasVaultCreateMutation"))
        XCTAssertTrue(feature.contains("publicSearchRequest"))
        XCTAssertTrue(
            feature.contains(
                "AtlasVaultSavedSearchPublicHandoffCoordinator"
            )
        )
        XCTAssertTrue(feature.contains("AtlasVaultDeleteMutation"))
        XCTAssertTrue(feature.contains("tombstones"))
        XCTAssertTrue(host.contains("activatePrivateSession"))
        XCTAssertTrue(host.contains("applyPrivateMutation"))
        XCTAssertTrue(
            host.contains("performSavedSearchPublicHandoff")
        )
        XCTAssertTrue(harness.contains("savedSearchContext"))
        XCTAssertTrue(harness.contains("AtlasVaultSavedSearchCoordinator"))
        XCTAssertTrue(
            harness.contains("AtlasVaultSavedSearchPresentationOwner")
        )
    }

    func testJourneyKeepsPublicPipelineAndCompatibilityEndpointUnused()
        throws
    {
        let feature = try requiredSource(
            named: "AtlasVaultSavedSearchFeature.swift"
        )
        let harness = try requiredSource(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )
        let combined = feature + harness

        for forbidden in [
            "/api/saved-searches",
            "AtlasLocalCache",
            "UserDefaults",
            "Task.detached",
        ] {
            XCTAssertFalse(combined.contains(forbidden), forbidden)
        }
    }

    private func createAndUnlock(
        with harness: AtlasVaultProductionCompositionHarness,
        keychain: PrivateSearchE2EKeychainClient
    ) async throws -> AtlasSelectedVaultID {
        _ = try await harness.start()
        try await performPublicSearch(with: harness)
        await harness.publicShellActions.requestUnlock()
        XCTAssertEqual(
            harness.presentationOwner.flowState.publicShell.vaultStatus,
            .noVault
        )
        let creation = try XCTUnwrap(
            harness.creationContextForTesting
        )
        creation.actions.present()
        creation.actions.createOrResume()
        await creation.owner.waitForCurrentOperationForTesting()
        XCTAssertEqual(
            harness.presentationOwner.flowState.mode,
            .unlockPanel
        )
        XCTAssertNil(
            harness.presentationOwner.flowState
                .unlockPanelState?.selectedMethod
        )
        await harness.unlockActions.select(.localKey)
        let unlocked = await harness.unlockActions.submit(.localKey)
        XCTAssertEqual(unlocked, .unlocked)
        let selected = try await selectedVault(using: keychain)
        return selected
    }

    private func unlockExistingLocally(
        with harness: AtlasVaultProductionCompositionHarness
    ) async throws {
        _ = try await harness.start()
        await harness.publicShellActions.requestUnlock()
        XCTAssertEqual(
            harness.presentationOwner.flowState.mode,
            .unlockPanel
        )
        await harness.unlockActions.select(.localKey)
        let unlocked = await harness.unlockActions.submit(.localKey)
        XCTAssertEqual(unlocked, .unlocked)
        XCTAssertEqual(
            harness.presentationOwner.flowState.mode,
            .unlockedTransition
        )
    }

    private func configureRecovery(
        with harness: AtlasVaultProductionCompositionHarness
    ) async throws -> String {
        let context = try XCTUnwrap(
            harness.recoveryExportContextForTesting
        )
        context.actions.present()
        let generatedHandle = await context.actions.generate()
        let handle = try XCTUnwrap(generatedHandle)
        let displayedCode = await handle.take()
        let recoveryCode = try XCTUnwrap(displayedCode)
        XCTAssertTrue(recoveryCode.hasPrefix("AVRK1-"))
        let document = await context.actions.confirm(recoveryCode)
        XCTAssertNotNil(document)
        await context.actions.exportDidFinish(true)
        XCTAssertEqual(context.owner.presentation, .complete)
        return recoveryCode
    }

    private func performPublicSearch(
        with harness: AtlasVaultProductionCompositionHarness
    ) async throws {
        await harness.publicShellActions.search(query: "public-only")
        XCTAssertEqual(
            harness.presentationOwner.flowState.publicShell.publicJobs,
            []
        )
    }

    private func selectedVault(
        using keychain: PrivateSearchE2EKeychainClient
    ) async throws -> AtlasSelectedVaultID {
        let registry = AtlasKeychainVaultSelectionRegistry(
            client: keychain
        )
        guard case let .selected(selected) =
            try await registry.selectVaultID() else {
            throw AtlasVaultSavedSearchFailure.unavailable
        }
        return selected
    }

    private func makeHarness(
        keychain: PrivateSearchE2EKeychainClient,
        directory: PrivateSearchE2EDirectoryLocator,
        savedSearchTimestamps: PrivateSearchE2ETimestampProvider,
        publicJobs: PrivateSearchE2EPublicJobs =
            PrivateSearchE2EPublicJobs()
    ) throws -> AtlasVaultProductionCompositionHarness {
        let time = PrivateSearchE2ELifecycleTime()
        return try AtlasVaultProductionCompositionFactory
            .makeUnwiredProductionLike(
                configuration: try Self.configuration(),
                lifecycleEvents: PrivateSearchE2ELifecycleSource(),
                directoryLocator: directory,
                keychainClient: keychain,
                atomicFileSystemClient:
                    AtlasFoundationAtomicFileSystemClient(),
                lifecycleClock: time,
                lifecycleSleeper: time,
                publicJobs: publicJobs,
                publicSnapshotRestorer:
                    PrivateSearchE2EPublicSnapshotRestorer(),
                savedSearchTimestamp: {
                    savedSearchTimestamps.next()
                },
                unlockRequestSleep: { _ in
                    throw CancellationError()
                }
            )
    }

    private func temporaryRoot() throws -> URL {
        let root = try AtlasVaultTestFileSystemSupport
            .canonicalTemporaryRoot()
            .appendingPathComponent(
                "phase2d63-\(UUID().uuidString)",
                isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        return root
    }

    private func storeBytes(
        root: URL,
        selected: AtlasSelectedVaultID
    ) throws -> Data {
        try Data(contentsOf: storeURL(root: root, selected: selected))
    }

    private func storeURL(
        root: URL,
        selected: AtlasSelectedVaultID
    ) -> URL {
        root
            .appendingPathComponent("Atlas", isDirectory: true)
            .appendingPathComponent("Vaults", isDirectory: true)
            .appendingPathComponent(
                selected.vaultID,
                isDirectory: true
            )
            .appendingPathComponent("atlasvault-local-store.json")
    }

    private func replaceOnlySavedSearchRequest(
        root: URL,
        selected: AtlasSelectedVaultID,
        keychain: PrivateSearchE2EKeychainClient,
        request: AtlasSearchRequest
    ) throws {
        let key = try XCTUnwrap(
            AtlasKeychainVaultKeyStore(client: keychain)
                .loadVaultKey(for: selected.vaultID)
        )
        let session = try AtlasVaultUnlockedSession(
            vaultID: selected.vaultID,
            vaultKey: key
        )
        let store = try AtlasVaultLocalStoreIO.decode(
            storeBytes(root: root, selected: selected)
        )
        let state = try AtlasVaultRecordHydrator().hydrate(
            records: store.records,
            session: session
        )
        let current = try XCTUnwrap(state.savedSearches.first)
        XCTAssertEqual(state.savedSearches.count, 1)
        let updatedAt = "2026-07-27T04:00:00Z"
        let payload = AtlasSavedSearchVaultPayload(
            name: current.payload.name,
            summary: request.text ?? "All open jobs",
            description: current.payload.description,
            request: request,
            createdAt: current.payload.createdAt,
            updatedAt: updatedAt
        )
        let envelope = AtlasSavedSearchVaultRecordPayload(
            type: .savedSearch,
            payload: payload,
            clientCreatedAt: current.clientCreatedAt,
            clientUpdatedAt: updatedAt
        )
        let updatedRecord = try XCTUnwrap(
            AtlasVaultRecordSaver().save(
                mutations: AtlasVaultMutationSet(
                    updates: [
                        AtlasVaultUpdateMutation(
                            recordID: current.metadata.id,
                            currentRevision: current.metadata.revision,
                            payload: .savedSearch(envelope),
                            keyID: current.metadata.keyID
                        ),
                    ]
                ),
                session: session
            ).first
        )
        let records = store.records.map { record in
            record.id == current.metadata.id ? updatedRecord : record
        }
        let replacement = AtlasVaultLocalStoreEnvelope(
            format: store.format,
            version: store.version,
            storeID: store.storeID,
            createdAt: store.createdAt,
            updatedAt: updatedAt,
            vaultMetadata: store.vaultMetadata,
            records: records
        )
        _ = try AtlasVaultAtomicStoreWriter().write(
            replacement,
            to: storeURL(root: root, selected: selected),
            overwrite: true
        )
    }

    private static func configuration()
        throws -> AtlasVaultProductionCompositionConfiguration
    {
        try AtlasVaultProductionCompositionConfiguration(
            apiBaseURL: URL(string: "https://example.invalid")!,
            publicSearchLimit: 50,
            unlockTimeout: .seconds(30),
            lifecycleLockPolicy: .immediate,
            lockOnInactive: true
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

private final class PrivateSearchE2ETimestampProvider: Sendable {
    private struct State: Sendable {
        var index = 0
    }

    private let values = [
        "2026-07-27T00:00:00Z",
        "2026-07-27T01:00:00Z",
        "2026-07-27T02:00:00Z",
        "2026-07-27T03:00:00Z",
    ]
    private let state = Mutex(State())

    func next() -> String {
        state.withLock { state in
            let value = values[min(state.index, values.count - 1)]
            state.index += 1
            return value
        }
    }
}

private final class PrivateSearchE2EKeychainClient:
    AtlasKeychainClient,
    Sendable
{
    private struct State: Sendable {
        var items: [String: AtlasKeychainItem] = [:]
        var operations = 0
    }

    private let state = Mutex(State())

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        state.withLock {
            $0.operations += 1
            let key = Self.key(item.service, item.account)
            guard $0.items[key] == nil else {
                return errSecDuplicateItem
            }
            $0.items[key] = item
            return errSecSuccess
        }
    }

    func copyMatching(
        _ query: AtlasKeychainQuery
    ) -> AtlasKeychainCopyResult {
        state.withLock {
            $0.operations += 1
            guard let item = $0.items[
                Self.key(query.service, query.account)
            ] else {
                return AtlasKeychainCopyResult(
                    status: errSecItemNotFound,
                    valueData: nil
                )
            }
            return AtlasKeychainCopyResult(
                status: errSecSuccess,
                valueData: item.valueData
            )
        }
    }

    func update(
        _ query: AtlasKeychainQuery,
        with attributes: AtlasKeychainUpdate
    ) -> OSStatus {
        state.withLock {
            $0.operations += 1
            let key = Self.key(query.service, query.account)
            guard let current = $0.items[key] else {
                return errSecItemNotFound
            }
            $0.items[key] = AtlasKeychainItem(
                service: current.service,
                account: current.account,
                valueData: attributes.valueData,
                accessibility: current.accessibility
            )
            return errSecSuccess
        }
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        state.withLock {
            $0.operations += 1
            let removed = $0.items.removeValue(
                forKey: Self.key(query.service, query.account)
            )
            return removed == nil ? errSecItemNotFound : errSecSuccess
        }
    }

    func operationCount() -> Int {
        state.withLock { $0.operations }
    }

    private static func key(
        _ service: String,
        _ account: String
    ) -> String {
        "\(service)\u{0}\(account)"
    }
}

private final class PrivateSearchE2EDirectoryLocator:
    AtlasApplicationSupportDirectoryLocating,
    Sendable
{
    private let root: URL
    private let calls = Mutex(0)

    init(root: URL) {
        self.root = root
    }

    func applicationSupportDirectory() throws -> URL {
        calls.withLock { $0 += 1 }
        return root
    }

    func callCount() -> Int {
        calls.withLock { $0 }
    }
}

private actor PrivateSearchE2EPublicJobs: AtlasPublicJobSearching {
    private var searchRequests: [AtlasPublicJobSearchRequest] = []
    private var shouldFailNextSearch = false

    func health() async throws(AtlasPublicJobServiceError)
        -> AtlasPublicServiceHealth
    {
        do {
            return try AtlasPublicServiceHealth(
                availability: .available,
                openJobCount: 0,
                enabledSourceCount: 1,
                lastSyncAt: nil
            )
        } catch {
            throw .invalidResponse
        }
    }

    func search(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError)
        -> AtlasPublicJobSearchResult
    {
        searchRequests.append(request)
        if shouldFailNextSearch {
            shouldFailNextSearch = false
            throw .unavailable
        }
        do {
            return try AtlasPublicJobSearchResult(
                jobs: [],
                total: 0,
                limit: request.limit,
                offset: request.offset
            )
        } catch {
            throw .invalidResponse
        }
    }

    func searchCallCount() -> Int {
        searchRequests.count
    }

    func lastSearchRequest() -> AtlasPublicJobSearchRequest? {
        searchRequests.last
    }

    func failNextSearch() {
        shouldFailNextSearch = true
    }

    func sources() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicSourceStatus]
    {
        []
    }

    func updates() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicUpdateStatus]
    {
        []
    }

    func detail(
        for reference: AtlasPublicJobReference
    ) async throws(AtlasPublicJobServiceError)
        -> AtlasPublicJobDetailResult
    {
        throw .unavailable
    }
}

private struct PrivateSearchE2EPublicSnapshotRestorer:
    AtlasPublicSnapshotRestoring
{
    func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
    {
        nil
    }
}

private actor PrivateSearchE2ELifecycleSource:
    AtlasVaultPlatformLifecycleEventSourcing
{
    func subscription() async
        -> AtlasVaultPlatformLifecycleEventSubscription
    {
        let pair = AsyncStream<
            AtlasVaultPlatformLifecycleEventDelivery
        >.makeStream(bufferingPolicy: .unbounded)
        return AtlasVaultPlatformLifecycleEventSubscription(
            bootstrapEvents: [
                .protectedDataBecameAvailable,
                .didBecomeActive,
            ],
            events: pair.stream,
            requestReadinessBoundary: { identifier in
                pair.continuation.yield(
                    .readinessBoundary(identifier)
                )
            }
        )
    }
}

private actor PrivateSearchE2ELifecycleTime:
    AtlasVaultLifecycleClock,
    AtlasVaultLifecycleSleeper
{
    func now() async -> Duration {
        .zero
    }

    func sleep(until deadline: Duration) async throws {
        throw CancellationError()
    }
}
