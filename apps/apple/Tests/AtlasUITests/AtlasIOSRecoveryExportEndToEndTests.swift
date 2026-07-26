import Foundation
import Security
import Synchronization
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasIOSRecoveryExportEndToEndTests: XCTestCase {
    func testCreatedVaultRecoveryExportAndRelaunchReExport()
        async throws
    {
        let root = try temporaryRoot("journey")
        defer { try? FileManager.default.removeItem(at: root) }

        let keychain = RecoveryE2EKeychainClient()
        let directory = RecoveryE2EDirectoryLocator(root: root)
        let first = try makeHarness(
            root: root,
            keychain: keychain,
            directory: directory
        )

        XCTAssertEqual(keychain.operationCount(), 0)
        XCTAssertEqual(directory.callCount(), 0)
        XCTAssertNotNil(first.recoveryExportContextForTesting)

        let selected = try await createAndUnlock(
            with: first,
            keychain: keychain
        )
        let keyStore = AtlasKeychainVaultKeyStore(client: keychain)
        let localVaultKey = try XCTUnwrap(
            keyStore.loadVaultKey(for: selected.vaultID)
        )
        let recoveryContext = try XCTUnwrap(
            first.recoveryExportContextForTesting
        )

        recoveryContext.actions.present()
        let generatedHandle = await recoveryContext.actions.generate()
        let displayHandle = try XCTUnwrap(generatedHandle)
        let displayedCode = await displayHandle.take()
        let recoveryCode = try XCTUnwrap(displayedCode)
        XCTAssertTrue(recoveryCode.hasPrefix("AVRK1-"))

        let preparedDocument = await recoveryContext.actions.confirm(
            recoveryCode
        )
        let document = try XCTUnwrap(preparedDocument)
        let exportURL = root.appendingPathComponent(
            AtlasVaultEncryptedExportEnvelope.defaultFilename
        )
        try document.encryptedData.write(to: exportURL, options: .atomic)
        await recoveryContext.actions.exportDidFinish(true)
        XCTAssertEqual(recoveryContext.owner.presentation, .complete)
        XCTAssertNil(keychain.recoveryJournalData())

        let store = try loadStore(root: root, vaultID: selected.vaultID)
        let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
            localStoreMetadata: store.vaultMetadata
        )
        let recoveryWrap = try XCTUnwrap(metadata.recoveryKeyWrap)
        XCTAssertEqual(metadata.keyWraps.count, 1)
        XCTAssertEqual(store.records, [])

        let exportBytes = try Data(contentsOf: exportURL)
        XCTAssertEqual(exportBytes, document.encryptedData)
        XCTAssertNil(exportBytes.range(of: localVaultKey))
        XCTAssertFalse(
            String(decoding: exportBytes, as: UTF8.self).contains(
                recoveryCode
            )
        )
        let firstExport =
            try AtlasVaultEncryptedExportEnvelope.decodeStrict(
                exportBytes
            )
        XCTAssertEqual(firstExport.vaultMetadata, metadata)
        XCTAssertEqual(firstExport.records, store.records)
        var rawRecovery = try AtlasVaultRecoveryKeyCodec.parse(
            recoveryCode
        )
        defer {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&rawRecovery)
        }
        var unwrapped = try AtlasVaultRecoveryWrapCrypto.unwrap(
            recoveryWrap,
            recoveryKey: rawRecovery,
            vaultID: selected.vaultID
        )
        defer {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&unwrapped)
        }
        XCTAssertTrue(
            AtlasVaultRecoveryWrapCrypto.constantTimeEqual(
                unwrapped,
                localVaultKey
            )
        )

        _ = await first.stop()

        let second = try makeHarness(
            root: root,
            keychain: keychain,
            directory: directory
        )
        try await unlockExisting(with: second)
        let secondContext = try XCTUnwrap(
            second.recoveryExportContextForTesting
        )
        secondContext.actions.present()

        let repeatedGeneration = await secondContext.actions.generate()
        XCTAssertNil(repeatedGeneration)
        XCTAssertEqual(
            secondContext.owner.presentation,
            .reexportRequired
        )
        let wrongResume = await secondContext.actions.resume(
            wrongRecoveryCode(recoveryCode)
        )
        XCTAssertNil(wrongResume)
        XCTAssertEqual(
            secondContext.owner.presentation,
            .reexportRequired
        )

        let resumedDocument = await secondContext.actions.resume(
            recoveryCode
        )
        let secondDocument = try XCTUnwrap(resumedDocument)
        let secondExport =
            try AtlasVaultEncryptedExportEnvelope.decodeStrict(
                secondDocument.encryptedData
            )
        XCTAssertEqual(
            secondExport.vaultMetadata,
            firstExport.vaultMetadata
        )
        XCTAssertEqual(secondExport.records, firstExport.records)
        await secondContext.actions.exportDidFinish(true)
        XCTAssertNil(keychain.recoveryJournalData())
        XCTAssertEqual(
            AtlasVaultUnlockCapabilities.currentProduction
                .availableMethods,
            [.localKey]
        )
        _ = await second.stop()
    }

    func testInterruptedCommittedSetupCanBeExplicitlyReset()
        async throws
    {
        let root = try temporaryRoot("reset")
        defer { try? FileManager.default.removeItem(at: root) }

        let keychain = RecoveryE2EKeychainClient()
        let directory = RecoveryE2EDirectoryLocator(root: root)
        let first = try makeHarness(
            root: root,
            keychain: keychain,
            directory: directory
        )
        let selected = try await createAndUnlock(
            with: first,
            keychain: keychain
        )
        let initialSelection = try await selection(using: keychain)
        let keyStore = AtlasKeychainVaultKeyStore(client: keychain)
        let initialKey = try XCTUnwrap(
            keyStore.loadVaultKey(for: selected.vaultID)
        )

        let context = try XCTUnwrap(
            first.recoveryExportContextForTesting
        )
        context.actions.present()
        let generatedHandle = await context.actions.generate()
        let handle = try XCTUnwrap(generatedHandle)
        let displayedCode = await handle.take()
        let recoveryCode = try XCTUnwrap(displayedCode)
        let preparedDocument = await context.actions.confirm(recoveryCode)
        XCTAssertNotNil(preparedDocument)
        XCTAssertNotNil(keychain.recoveryJournalData())

        let committed = try loadStore(
            root: root,
            vaultID: selected.vaultID
        )
        let committedMetadata =
            try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: committed.vaultMetadata
            )
        XCTAssertNotNil(committedMetadata.recoveryKeyWrap)
        let committedRecords = committed.records
        _ = await first.stop()

        let second = try makeHarness(
            root: root,
            keychain: keychain,
            directory: directory
        )
        try await unlockExisting(with: second)
        let secondContext = try XCTUnwrap(
            second.recoveryExportContextForTesting
        )
        secondContext.actions.present()
        let repeatedGeneration = await secondContext.actions.generate()
        XCTAssertNil(repeatedGeneration)
        XCTAssertEqual(
            secondContext.owner.presentation,
            .resumeRequired
        )

        await secondContext.actions.resetPendingSetup(confirmed: false)
        XCTAssertNotNil(keychain.recoveryJournalData())
        await secondContext.actions.resetPendingSetup(confirmed: true)
        XCTAssertEqual(secondContext.owner.presentation, .ready)
        XCTAssertNil(keychain.recoveryJournalData())

        let resetStore = try loadStore(
            root: root,
            vaultID: selected.vaultID
        )
        let resetMetadata = try AtlasVaultVersionedWrappedKeyMetadata(
            localStoreMetadata: resetStore.vaultMetadata
        )
        XCTAssertNil(resetMetadata.recoveryKeyWrap)
        XCTAssertEqual(resetStore.records, committedRecords)
        let retainedSelection = try await selection(using: keychain)
        XCTAssertEqual(retainedSelection, initialSelection)
        let retainedKey = try keyStore.loadVaultKey(
            for: selected.vaultID
        )
        XCTAssertEqual(retainedKey, initialKey)
        _ = await second.stop()
    }

    func testProductionHarnessExposesRecoveryExportJourneyWithoutRecoveryUnlock()
        throws
    {
        let harness = try phaseSource(
            "AtlasVaultProductionCompositionHarness.swift"
        )
        let recovery = try phaseSource("AtlasVaultRecoveryExport.swift")
        let root = try phaseSource("AtlasVaultProductionRootView.swift")

        for required in [
            "AtlasVaultRecoveryExportCoordinator",
            "recoveryExportContext",
            "recoveryOwner.stop",
            "resumeAndPrepareExport",
            "resetPendingSetup",
            "Recovery & Encrypted Export",
            "productionCapabilities.availableMethods == [.localKey]",
        ] {
            XCTAssertTrue(
                harness.contains(required)
                    || recovery.contains(required)
                    || root.contains(required),
                required
            )
        }
        XCTAssertFalse(harness.contains("recoveryKeyProvider: Atlas"))
    }

    private func createAndUnlock(
        with harness: AtlasVaultProductionCompositionHarness,
        keychain: RecoveryE2EKeychainClient
    ) async throws -> AtlasSelectedVaultID {
        _ = try await harness.start()
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
            harness.presentationOwner.flowState.unlockPanelState?
                .selectedMethod
        )
        await harness.unlockActions.select(.localKey)
        let unlockResult = await harness.unlockActions.submit(.localKey)
        XCTAssertEqual(unlockResult, .unlocked)
        XCTAssertEqual(
            harness.presentationOwner.flowState.mode,
            .unlockedTransition
        )
        return try await selection(using: keychain)
    }

    private func unlockExisting(
        with harness: AtlasVaultProductionCompositionHarness
    ) async throws {
        _ = try await harness.start()
        await harness.publicShellActions.requestUnlock()
        XCTAssertEqual(
            harness.presentationOwner.flowState.mode,
            .unlockPanel
        )
        await harness.unlockActions.select(.localKey)
        let unlockResult = await harness.unlockActions.submit(.localKey)
        XCTAssertEqual(unlockResult, .unlocked)
        XCTAssertEqual(
            harness.presentationOwner.flowState.mode,
            .unlockedTransition
        )
    }

    private func selection(
        using keychain: RecoveryE2EKeychainClient
    ) async throws -> AtlasSelectedVaultID {
        let registry = AtlasKeychainVaultSelectionRegistry(
            client: keychain
        )
        guard case let .selected(selected) =
            try await registry.selectVaultID()
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        return selected
    }

    private func makeHarness(
        root _: URL,
        keychain: RecoveryE2EKeychainClient,
        directory: RecoveryE2EDirectoryLocator
    ) throws -> AtlasVaultProductionCompositionHarness {
        let time = RecoveryE2ELifecycleTime()
        return try AtlasVaultProductionCompositionFactory
            .makeUnwiredProductionLike(
                configuration: try Self.configuration(),
                lifecycleEvents: RecoveryE2ELifecycleSource(),
                directoryLocator: directory,
                keychainClient: keychain,
                atomicFileSystemClient:
                    AtlasFoundationAtomicFileSystemClient(),
                lifecycleClock: time,
                lifecycleSleeper: time,
                publicJobs: RecoveryE2EPublicJobs(),
                publicSnapshotRestorer:
                    RecoveryE2EPublicSnapshotRestorer(),
                unlockRequestSleep: { _ in
                    throw CancellationError()
                }
            )
    }

    private func temporaryRoot(_ label: String) throws -> URL {
        let root = try AtlasVaultTestFileSystemSupport
            .canonicalTemporaryRoot()
            .appendingPathComponent(
                "phase2d61-\(label)-\(UUID().uuidString)",
                isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        return root
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
        return try AtlasVaultLocalStoreIO.decode(
            Data(contentsOf: url)
        )
    }

    private func wrongRecoveryCode(_ code: String) -> String {
        var characters = Array(code)
        guard let index = characters.lastIndex(where: { $0 != "-" }) else {
            return "AVRK1-INVALID"
        }
        characters[index] = characters[index] == "A" ? "B" : "A"
        return String(characters)
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

private final class RecoveryE2EKeychainClient:
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

    func recoveryJournalData() -> Data? {
        state.withLock {
            $0.items[
                Self.key(
                    AtlasKeychainVaultRecoveryExportJournalStore<
                        RecoveryE2EKeychainClient
                    >.service,
                    AtlasKeychainVaultRecoveryExportJournalStore<
                        RecoveryE2EKeychainClient
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

private final class RecoveryE2EDirectoryLocator:
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

private actor RecoveryE2EPublicJobs: AtlasPublicJobSearching {
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

private struct RecoveryE2EPublicSnapshotRestorer:
    AtlasPublicSnapshotRestoring
{
    func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
    {
        nil
    }
}

private actor RecoveryE2ELifecycleSource:
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

private actor RecoveryE2ELifecycleTime:
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
