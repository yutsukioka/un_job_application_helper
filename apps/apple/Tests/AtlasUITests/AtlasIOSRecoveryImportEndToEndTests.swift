import Foundation
import Security
import Synchronization
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSRecoveryImportEndToEndTests: XCTestCase {
    func testProductionCompositionExposesExplicitRestoreAndRecoveryUnlock()
        throws
    {
        let harness = try phaseSource(
            "AtlasVaultProductionCompositionHarness.swift"
        )
        let root = try phaseSource("AtlasVaultProductionRootView.swift")
        let importCore = try phaseSource("AtlasVaultRecoveryImport.swift")
        let provider = try phaseSource(
            "AtlasVaultRecoveryUnlockProvider.swift"
        )

        for required in [
            "AtlasVaultRecoveryImportCoordinator",
            "recoveryImportContext",
            "AtlasPendingVaultTransactionSelectionGate",
            "AtlasVaultProductionUnlockCapabilitiesResolver",
            "deriveVaultAwareRecoveryVaultKey",
            "Restore Encrypted Backup",
            "createVaultKey",
            "createSelection",
        ] {
            XCTAssertTrue(
                harness.contains(required)
                    || root.contains(required)
                    || importCore.contains(required)
                    || provider.contains(required),
                required
            )
        }
        for forbidden in [
            "AtlasVaultPrivateState",
            "passphraseProvider: Atlas",
            "Task." + "detached",
        ] {
            XCTAssertFalse(harness.contains(forbidden), forbidden)
            XCTAssertFalse(root.contains(forbidden), forbidden)
            XCTAssertFalse(importCore.contains(forbidden), forbidden)
        }
    }

    func testCleanDeviceRestoresThenRecoveryOnlyRelaunchUnlocks()
        async throws
    {
        let sourceRoot = try temporaryRoot("source")
        let restoreRoot = try temporaryRoot("restore")
        defer {
            try? FileManager.default.removeItem(at: sourceRoot)
            try? FileManager.default.removeItem(at: restoreRoot)
        }

        let sourceKeychain = RecoveryImportE2EKeychainClient()
        let sourceHarness = try makeHarness(
            root: sourceRoot,
            keychain: sourceKeychain
        )
        let recoveryCode = try await createSourceExport(
            harness: sourceHarness,
            root: sourceRoot
        )
        let exportURL = sourceRoot.appendingPathComponent(
            AtlasVaultEncryptedExportEnvelope.defaultFilename
        )
        _ = await sourceHarness.stop()

        let restoreKeychain = RecoveryImportE2EKeychainClient()
        let restoreHarness = try makeHarness(
            root: restoreRoot,
            keychain: restoreKeychain
        )
        _ = try await restoreHarness.start()
        await restoreHarness.publicShellActions.search(query: "public")
        await restoreHarness.publicShellActions.requestUnlock()
        XCTAssertEqual(
            restoreHarness.presentationOwner.flowState.publicShell
                .vaultStatus,
            .noVault
        )

        let importContext = try XCTUnwrap(
            restoreHarness.recoveryImportContextForTesting
        )
        importContext.actions.present()
        await importContext.actions.prepareImport(exportURL)
        XCTAssertEqual(
            importContext.owner.presentation,
            .awaitingRecoveryKey
        )
        await importContext.actions.restore(recoveryCode, true)
        XCTAssertEqual(importContext.owner.presentation, .complete)
        XCTAssertNil(restoreKeychain.importJournalData())

        let selected = try await selectedVault(
            keychain: restoreKeychain
        )
        let importedStore = try loadStore(
            root: restoreRoot,
            vaultID: selected.vaultID
        )
        XCTAssertEqual(
            importedStore.vaultMetadata["vault_id"],
            .string(selected.vaultID)
        )
        XCTAssertEqual(
            restoreHarness.presentationOwner.flowState.mode,
            .unlockPanel
        )
        XCTAssertNil(
            restoreHarness.presentationOwner.flowState.unlockPanelState?
                .selectedMethod
        )
        XCTAssertEqual(
            restoreHarness.presentationOwner.flowState.unlockPanelState?
                .availableMethods,
            [.localKey, .recoveryKey]
        )

        await restoreHarness.unlockActions.select(.recoveryKey)
        let firstUnlock = await restoreHarness.unlockActions.submit(
            .recoveryKey(
                AtlasVaultInMemorySecretBuffer(
                    bytes: Data(recoveryCode.utf8)
                )
            )
        )
        XCTAssertEqual(firstUnlock, .unlocked)
        XCTAssertEqual(
            restoreHarness.presentationOwner.flowState.mode,
            .unlockedTransition
        )
        _ = await restoreHarness.stop()

        try AtlasKeychainVaultKeyStore(client: restoreKeychain)
            .deleteVaultKey(for: selected.vaultID)
        let recoveryOnlyHarness = try makeHarness(
            root: restoreRoot,
            keychain: restoreKeychain
        )
        _ = try await recoveryOnlyHarness.start()
        await recoveryOnlyHarness.publicShellActions.requestUnlock()
        let recoveryOnly = recoveryOnlyHarness.presentationOwner.flowState
        XCTAssertEqual(
            recoveryOnly.unlockPanelState?.availableMethods,
            [.recoveryKey]
        )

        await recoveryOnlyHarness.unlockActions.select(.recoveryKey)
        let wrong = await recoveryOnlyHarness.unlockActions.submit(
            .recoveryKey(
                AtlasVaultInMemorySecretBuffer(
                    bytes: Data("AVRK1-INVALID".utf8)
                )
            )
        )
        XCTAssertEqual(wrong, .failed)
        let recovered = await recoveryOnlyHarness.unlockActions.submit(
            .recoveryKey(
                AtlasVaultInMemorySecretBuffer(
                    bytes: Data(recoveryCode.utf8)
                )
            )
        )
        XCTAssertEqual(recovered, .unlocked)
        XCTAssertNil(
            try AtlasKeychainVaultKeyStore(client: restoreKeychain)
                .loadVaultKey(for: selected.vaultID)
        )
        _ = await recoveryOnlyHarness.stop()
    }

    private func createSourceExport(
        harness: AtlasVaultProductionCompositionHarness,
        root: URL
    ) async throws -> String {
        _ = try await harness.start()
        await harness.publicShellActions.requestUnlock()
        let creation = try XCTUnwrap(harness.creationContextForTesting)
        creation.actions.present()
        creation.actions.createOrResume()
        await creation.owner.waitForCurrentOperationForTesting()
        await harness.unlockActions.select(.localKey)
        let unlock = await harness.unlockActions.submit(.localKey)
        XCTAssertEqual(unlock, .unlocked)

        let recovery = try XCTUnwrap(
            harness.recoveryExportContextForTesting
        )
        recovery.actions.present()
        let generatedHandle = await recovery.actions.generate()
        let displayHandle = try XCTUnwrap(generatedHandle)
        let generatedCode = await displayHandle.take()
        let recoveryCode = try XCTUnwrap(generatedCode)
        let generatedDocument = await recovery.actions.confirm(
            recoveryCode
        )
        let document = try XCTUnwrap(generatedDocument)
        try document.encryptedData.write(
            to: root.appendingPathComponent(
                AtlasVaultEncryptedExportEnvelope.defaultFilename
            ),
            options: .atomic
        )
        await recovery.actions.exportDidFinish(true)
        return recoveryCode
    }

    private func selectedVault(
        keychain: RecoveryImportE2EKeychainClient
    ) async throws -> AtlasSelectedVaultID {
        let registry = AtlasKeychainVaultSelectionRegistry(
            client: keychain
        )
        guard case let .selected(selected) =
            try await registry.selectVaultID()
        else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        return selected
    }

    private func loadStore(
        root: URL,
        vaultID: String
    ) throws -> AtlasVaultLocalStoreEnvelope {
        let url = root
            .appendingPathComponent("Atlas", isDirectory: true)
            .appendingPathComponent("Vaults", isDirectory: true)
            .appendingPathComponent(vaultID, isDirectory: true)
            .appendingPathComponent("atlasvault-local-store.json")
        return try AtlasVaultLocalStoreIO.decode(Data(contentsOf: url))
    }

    private func makeHarness(
        root: URL,
        keychain: RecoveryImportE2EKeychainClient
    ) throws -> AtlasVaultProductionCompositionHarness {
        let time = RecoveryImportE2ELifecycleTime()
        return try AtlasVaultProductionCompositionFactory
            .makeUnwiredProductionLike(
                configuration: try Self.configuration(),
                lifecycleEvents: RecoveryImportE2ELifecycleSource(),
                directoryLocator:
                    RecoveryImportE2EDirectoryLocator(root: root),
                keychainClient: keychain,
                atomicFileSystemClient:
                    AtlasFoundationAtomicFileSystemClient(),
                lifecycleClock: time,
                lifecycleSleeper: time,
                publicJobs: RecoveryImportE2EPublicJobs(),
                publicSnapshotRestorer:
                    RecoveryImportE2EPublicSnapshotRestorer(),
                unlockRequestSleep: { _ in
                    throw CancellationError()
                }
            )
    }

    private func temporaryRoot(_ label: String) throws -> URL {
        let root = try AtlasVaultTestFileSystemSupport
            .canonicalTemporaryRoot()
            .appendingPathComponent(
                "phase2d62-\(label)-\(UUID().uuidString)",
                isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        return root
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

    private func phaseSource(_ name: String) throws -> String {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
        return try String(contentsOf: sourceURL, encoding: .utf8)
    }
}

private final class RecoveryImportE2EKeychainClient:
    AtlasKeychainClient,
    Sendable
{
    private struct State: Sendable {
        var items: [String: AtlasKeychainItem] = [:]
    }

    private let state = Mutex(State())

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        state.withLock {
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
            $0.items.removeValue(
                forKey: Self.key(query.service, query.account)
            ) == nil ? errSecItemNotFound : errSecSuccess
        }
    }

    func importJournalData() -> Data? {
        state.withLock {
            $0.items[
                Self.key(
                    AtlasKeychainVaultRecoveryImportJournalStore<
                        RecoveryImportE2EKeychainClient
                    >.service,
                    AtlasKeychainVaultRecoveryImportJournalStore<
                        RecoveryImportE2EKeychainClient
                    >.account
                )
            ]?.valueData
        }
    }

    private static func key(
        _ service: String,
        _ account: String
    ) -> String {
        "\(service)\u{0}\(account)"
    }
}

private struct RecoveryImportE2EDirectoryLocator:
    AtlasApplicationSupportDirectoryLocating,
    Sendable
{
    let root: URL

    func applicationSupportDirectory() throws -> URL {
        root
    }
}

private actor RecoveryImportE2EPublicJobs: AtlasPublicJobSearching {
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
        for _: AtlasPublicJobReference
    ) async throws(AtlasPublicJobServiceError)
        -> AtlasPublicJobDetailResult
    {
        throw .unavailable
    }
}

private struct RecoveryImportE2EPublicSnapshotRestorer:
    AtlasPublicSnapshotRestoring
{
    func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
    {
        nil
    }
}

private actor RecoveryImportE2ELifecycleSource:
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

private actor RecoveryImportE2ELifecycleTime:
    AtlasVaultLifecycleClock,
    AtlasVaultLifecycleSleeper
{
    func now() async -> Duration {
        .zero
    }

    func sleep(until _: Duration) async throws {
        throw CancellationError()
    }
}
