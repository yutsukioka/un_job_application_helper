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

        let recoveryOnly = try makeHarness(
            keychain: keychain,
            directory: directory,
            savedSearchTimestamps: savedSearchTimestamps
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
        XCTAssertTrue(feature.contains("AtlasVaultDeleteMutation"))
        XCTAssertTrue(feature.contains("tombstones"))
        XCTAssertTrue(host.contains("activatePrivateSession"))
        XCTAssertTrue(host.contains("applyPrivateMutation"))
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
        savedSearchTimestamps: PrivateSearchE2ETimestampProvider
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
                publicJobs: PrivateSearchE2EPublicJobs(),
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
        try Data(
            contentsOf: root
                .appendingPathComponent("Atlas", isDirectory: true)
                .appendingPathComponent("Vaults", isDirectory: true)
                .appendingPathComponent(
                    selected.vaultID,
                    isDirectory: true
                )
                .appendingPathComponent(
                    "atlasvault-local-store.json"
                )
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
