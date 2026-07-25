import Foundation
import Security
import Synchronization
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSLocalVaultCreationEndToEndTests: XCTestCase {
    func testFreshInstallCreationUnlockStopRelaunchAndUnlock()
        async throws
    {
        let root = try AtlasVaultTestFileSystemSupport
            .canonicalTemporaryRoot()
            .appendingPathComponent(
                "phase2d60-\(UUID().uuidString)",
                isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        defer {
            try? FileManager.default.removeItem(at: root)
        }

        let keychain = E2EMemoryKeychainClient()
        let publicJobs = E2EPublicJobs()
        let directory = E2EDirectoryLocator(root: root)
        let firstLifecycle = E2ELifecycleSource()
        let firstTime = E2ELifecycleTime()

        let first = try AtlasVaultProductionCompositionFactory
            .makeUnwiredProductionLike(
                configuration: try Self.configuration(),
                lifecycleEvents: firstLifecycle,
                directoryLocator: directory,
                keychainClient: keychain,
                atomicFileSystemClient:
                    AtlasFoundationAtomicFileSystemClient(),
                lifecycleClock: firstTime,
                lifecycleSleeper: firstTime,
                publicJobs: publicJobs,
                publicSnapshotRestorer: E2EPublicSnapshotRestorer(),
                unlockRequestSleep: { _ in
                    throw CancellationError()
                }
            )

        XCTAssertEqual(keychain.operationCount(), 0)
        XCTAssertEqual(directory.callCount(), 0)
        XCTAssertNotNil(first.creationContextForTesting)

        let started = try await first.start()
        XCTAssertEqual(started.mode, .lockedPublic)
        XCTAssertEqual(keychain.operationCount(), 0)

        await first.publicShellActions.search(query: "public")
        let firstSearchCount = await publicJobs.searchCount()
        XCTAssertEqual(firstSearchCount, 1)
        XCTAssertEqual(
            first.presentationOwner.flowState.publicShell.publicJobs.count,
            1
        )

        await first.publicShellActions.requestUnlock()
        XCTAssertEqual(
            first.presentationOwner.flowState.publicShell.vaultStatus,
            .noVault
        )
        XCTAssertFalse(
            first.presentationOwner.flowState.publicShell
                .canRequestUnlock
        )

        let firstContext = try XCTUnwrap(
            first.creationContextForTesting
        )
        firstContext.actions.present()
        XCTAssertEqual(firstContext.owner.presentation, .ready)
        firstContext.actions.createOrResume()
        await firstContext.owner.waitForCurrentOperationForTesting()

        XCTAssertEqual(firstContext.owner.presentation, .hidden)
        let registry = AtlasKeychainVaultSelectionRegistry(
            client: keychain
        )
        let selection = try await registry.selectVaultID()
        guard case let .selected(selected) = selection else {
            XCTFail("Creation did not persist selected-vault readiness")
            return
        }

        let keyStore = AtlasKeychainVaultKeyStore(client: keychain)
        let vaultKey = try XCTUnwrap(
            keyStore.loadVaultKey(for: selected.vaultID)
        )
        XCTAssertEqual(
            vaultKey.count,
            AtlasVaultRecordCrypto.vaultKeyByteCount
        )
        XCTAssertNil(
            keychain.data(
                service:
                    AtlasKeychainLocalVaultCreationJournalStore<
                        E2EMemoryKeychainClient
                    >.service,
                account:
                    AtlasKeychainLocalVaultCreationJournalStore<
                        E2EMemoryKeychainClient
                    >.account
            )
        )

        let storeURL = root
            .appendingPathComponent("Atlas", isDirectory: true)
            .appendingPathComponent("Vaults", isDirectory: true)
            .appendingPathComponent(
                selected.vaultID,
                isDirectory: true
            )
            .appendingPathComponent(
                "atlasvault-local-store.json",
                isDirectory: false
            )
        let storeData = try Data(contentsOf: storeURL)
        let store = try AtlasVaultLocalStoreIO.decode(storeData)
        XCTAssertEqual(store.records, [])
        XCTAssertEqual(
            store.vaultMetadata["key_wraps"],
            .array([])
        )
        XCTAssertNil(storeData.range(of: vaultKey))
        XCTAssertFalse(
            String(decoding: storeData, as: UTF8.self).contains(
                vaultKey.base64EncodedString()
            )
        )
        let attributes = try FileManager.default.attributesOfItem(
            atPath: storeURL.path
        )
        let permissions = try XCTUnwrap(
            attributes[.posixPermissions] as? NSNumber
        )
        XCTAssertEqual(permissions.intValue & 0o777, 0o600)

        let postCreation = first.presentationOwner.flowState
        XCTAssertEqual(postCreation.publicShell.vaultStatus, .locked)
        XCTAssertEqual(postCreation.mode, .unlockPanel)
        XCTAssertNil(postCreation.unlockPanelState?.selectedMethod)

        await first.unlockActions.select(.localKey)
        XCTAssertEqual(
            first.presentationOwner.flowState.unlockPanelState?
                .selectedMethod,
            .localKey
        )
        let firstUnlock = await first.unlockActions.submit(.localKey)
        XCTAssertEqual(firstUnlock, .unlocked)
        XCTAssertEqual(
            first.presentationOwner.flowState.mode,
            .unlockedTransition
        )
        XCTAssertFalse(
            first.presentationOwner.flowState.description.contains(
                selected.vaultID
            )
        )

        _ = await first.stop()
        let journalAddsAfterFirstLaunch = keychain.addCount(
            service:
                AtlasKeychainLocalVaultCreationJournalStore<
                    E2EMemoryKeychainClient
                >.service
        )

        let secondLifecycle = E2ELifecycleSource()
        let secondTime = E2ELifecycleTime()
        let second = try AtlasVaultProductionCompositionFactory
            .makeUnwiredProductionLike(
                configuration: try Self.configuration(),
                lifecycleEvents: secondLifecycle,
                directoryLocator: directory,
                keychainClient: keychain,
                atomicFileSystemClient:
                    AtlasFoundationAtomicFileSystemClient(),
                lifecycleClock: secondTime,
                lifecycleSleeper: secondTime,
                publicJobs: publicJobs,
                publicSnapshotRestorer: E2EPublicSnapshotRestorer(),
                unlockRequestSleep: { _ in
                    throw CancellationError()
                }
            )

        _ = try await second.start()
        await second.publicShellActions.requestUnlock()
        let reopened = second.presentationOwner.flowState
        XCTAssertEqual(reopened.publicShell.vaultStatus, .locked)
        XCTAssertEqual(reopened.mode, .unlockPanel)
        XCTAssertNil(reopened.unlockPanelState?.selectedMethod)
        XCTAssertEqual(
            second.creationContextForTesting?.owner.presentation,
            .hidden
        )
        XCTAssertEqual(
            keychain.addCount(
                service:
                    AtlasKeychainLocalVaultCreationJournalStore<
                        E2EMemoryKeychainClient
                    >.service
            ),
            journalAddsAfterFirstLaunch
        )

        await second.unlockActions.select(.localKey)
        let secondUnlock = await second.unlockActions.submit(.localKey)
        XCTAssertEqual(secondUnlock, .unlocked)
        XCTAssertEqual(
            second.presentationOwner.flowState.mode,
            .unlockedTransition
        )
        _ = await second.stop()
    }

    func testProductionCompositionExposesOneSharedExplicitContext()
        throws
    {
        let harness = try Self.source(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )
        let root = try Self.source(
            named: "AtlasVaultProductionRootView.swift"
        )

        for required in [
            "AtlasLocalVaultCreationCoordinator.production",
            "creationContext",
            "creationOwner.stop()",
            "host.requestUnlockPanel()",
        ] {
            XCTAssertTrue(harness.contains(required), required)
        }
        for required in [
            "Create Local Vault",
            "creationContext",
            "@ObservedObject",
            ".sheet(",
        ] {
            XCTAssertTrue(root.contains(required), required)
        }
        XCTAssertFalse(root.contains("AtlasLocalVaultCreationCoordinator("))
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

    private static func source(named name: String) throws -> String {
        let appleRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try String(
            contentsOf: appleRoot
                .appendingPathComponent("Sources/AtlasUI")
                .appendingPathComponent(name),
            encoding: .utf8
        )
    }
}

private final class E2EMemoryKeychainClient:
    AtlasKeychainClient,
    Sendable
{
    private struct State: Sendable {
        var items: [String: AtlasKeychainItem] = [:]
        var operations: [String] = []
        var addServices: [String] = []
    }

    private let state = Mutex(State())

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        state.withLock {
            $0.operations.append("add:\(item.service)")
            $0.addServices.append(item.service)
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
            $0.operations.append("copy:\(query.service)")
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
            $0.operations.append("update:\(query.service)")
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
            $0.operations.append("delete:\(query.service)")
            let removed = $0.items.removeValue(
                forKey: Self.key(query.service, query.account)
            )
            return removed == nil ? errSecItemNotFound : errSecSuccess
        }
    }

    func operationCount() -> Int {
        state.withLock { $0.operations.count }
    }

    func addCount(service: String) -> Int {
        state.withLock {
            $0.addServices.filter { $0 == service }.count
        }
    }

    func data(service: String, account: String) -> Data? {
        state.withLock {
            $0.items[Self.key(service, account)]?.valueData
        }
    }

    private static func key(_ service: String, _ account: String) -> String {
        "\(service)\u{0}\(account)"
    }
}

private final class E2EDirectoryLocator:
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

private actor E2EPublicJobs: AtlasPublicJobSearching {
    private var searches = 0

    func health() async throws(AtlasPublicJobServiceError)
        -> AtlasPublicServiceHealth
    {
        do {
            return try AtlasPublicServiceHealth(
                availability: .available,
                openJobCount: 1,
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
        searches += 1
        do {
            return try AtlasPublicJobSearchResult(
                jobs: [
                    AtlasLockedPublicJob(
                        id: "PUBLIC_JOB",
                        title: "Public vacancy",
                        organization: "Public organization",
                        location: "Public location"
                    ),
                ],
                total: 1,
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

    func searchCount() -> Int {
        searches
    }
}

private struct E2EPublicSnapshotRestorer:
    AtlasPublicSnapshotRestoring
{
    func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
    {
        nil
    }
}

private actor E2ELifecycleSource:
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

private actor E2ELifecycleTime:
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
