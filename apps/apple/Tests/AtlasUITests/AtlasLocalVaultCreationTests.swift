import Foundation
import Security
import Synchronization
import XCTest
@testable import AtlasUI

final class AtlasLocalVaultCreationTests: XCTestCase {
    fileprivate static let vaultID =
        "11111111-1111-4111-8111-111111111111"
    fileprivate static let storeID =
        "22222222-2222-4222-8222-222222222222"
    fileprivate static let timestamp = "2026-07-25T00:00:00Z"
    fileprivate static let key = Data(repeating: 0xA7, count: 32)

    func testConstructionIsSideEffectFreeAndDiagnosticsAreRedacted() {
        let rig = CreationTransactionRig()
        let coordinator = AtlasLocalVaultCreationCoordinator(
            environment: rig.environment()
        )

        XCTAssertEqual(rig.snapshot().events, [])
        XCTAssertFalse(coordinator.description.contains(Self.vaultID))
        XCTAssertFalse(coordinator.debugDescription.contains(Self.storeID))
        XCTAssertEqual(
            AtlasLocalVaultCreationFailure.recoveryRequired.description,
            "recoveryRequired"
        )
    }

    func testFreshCreationOrdersJournalKeyStoreSelectionAndClear()
        async throws
    {
        let rig = CreationTransactionRig()
        let coordinator = AtlasLocalVaultCreationCoordinator(
            environment: rig.environment()
        )

        let outcome = try await coordinator.createOrResume()

        XCTAssertEqual(outcome, .created)
        let snapshot = rig.snapshot()
        XCTAssertEqual(
            snapshot.events,
            [
                "select",
                "loadJournal",
                "generateVaultID",
                "generateStoreID",
                "generateTimestamp",
                "saveJournal",
                "loadKey",
                "generateKey",
                "saveKey",
                "makeStoreAccess",
                "loadStore",
                "saveStore(false)",
                "storeSelection",
                "select",
                "clearJournal",
            ]
        )
        XCTAssertNil(snapshot.journal)
        XCTAssertEqual(snapshot.key, Self.key)
        XCTAssertEqual(
            snapshot.selection,
            .selected(try AtlasSelectedVaultID(validating: Self.vaultID))
        )
        XCTAssertEqual(snapshot.writeCount, 1)
        XCTAssertEqual(snapshot.lastOverwrite, false)

        let store = try XCTUnwrap(snapshot.store)
        XCTAssertEqual(store.format, "atlasvault-local-store")
        XCTAssertEqual(store.version, 1)
        XCTAssertEqual(store.storeID, Self.storeID)
        XCTAssertEqual(store.createdAt, Self.timestamp)
        XCTAssertEqual(store.updatedAt, Self.timestamp)
        XCTAssertEqual(store.records, [])
        XCTAssertEqual(
            store.vaultMetadata,
            AtlasLocalVaultCreationCoordinator.canonicalEmptyStore(
                journal: try Self.journal()
            ).vaultMetadata
        )
        let encoded = try AtlasVaultLocalStoreIO.encode(store)
        XCTAssertNil(encoded.range(of: Self.key))
        XCTAssertFalse(String(decoding: encoded, as: UTF8.self).contains(
            Self.key.base64EncodedString()
        ))
    }

    func testDurabilityUnconfirmedPausesBeforeSelectionAndRetryResumes()
        async throws
    {
        let rig = CreationTransactionRig(
            atomicResult: AtlasVaultAtomicWriteResult(
                commitState: .committedDurabilityUnconfirmed
            )
        )
        let coordinator = AtlasLocalVaultCreationCoordinator(
            environment: rig.environment()
        )

        await XCTAssertThrowsCreationFailure(
            try await coordinator.createOrResume(),
            expected: .durabilityVerificationRequired
        )

        var snapshot = rig.snapshot()
        XCTAssertEqual(snapshot.selection, .none)
        XCTAssertNotNil(snapshot.journal)
        XCTAssertEqual(snapshot.key, Self.key)
        XCTAssertNotNil(snapshot.store)
        XCTAssertEqual(snapshot.writeCount, 1)
        XCTAssertFalse(snapshot.events.contains("storeSelection"))

        rig.setAtomicResult(
            AtlasVaultAtomicWriteResult(commitState: .committed)
        )
        let outcome = try await coordinator.createOrResume()

        XCTAssertEqual(outcome, .resumed)
        snapshot = rig.snapshot()
        XCTAssertEqual(snapshot.writeCount, 1)
        XCTAssertNil(snapshot.journal)
        XCTAssertEqual(
            snapshot.selection,
            .selected(try AtlasSelectedVaultID(validating: Self.vaultID))
        )
    }

    func testExistingJournalKeyAndEmptyStoreResumeWithoutRewrite()
        async throws
    {
        let pending = try Self.journal()
        let rig = CreationTransactionRig(
            journal: pending,
            key: Self.key,
            store: AtlasLocalVaultCreationCoordinator
                .canonicalEmptyStore(journal: pending)
        )
        let coordinator = AtlasLocalVaultCreationCoordinator(
            environment: rig.environment()
        )

        let outcome = try await coordinator.createOrResume()
        XCTAssertEqual(outcome, .resumed)
        let snapshot = rig.snapshot()
        XCTAssertEqual(snapshot.writeCount, 0)
        XCTAssertFalse(snapshot.events.contains("generateKey"))
        XCTAssertFalse(snapshot.events.contains("saveKey"))
        XCTAssertNil(snapshot.journal)
    }

    func testConfiguredSelectionIsVerifiedWithoutCreationSideEffects()
        async throws
    {
        let pending = try Self.journal()
        let selected = try AtlasSelectedVaultID(
            validating: Self.vaultID
        )
        let rig = CreationTransactionRig(
            selection: .selected(selected),
            key: Self.key,
            store: AtlasLocalVaultCreationCoordinator
                .canonicalEmptyStore(journal: pending)
        )
        let coordinator = AtlasLocalVaultCreationCoordinator(
            environment: rig.environment()
        )

        let outcome = try await coordinator.createOrResume()
        XCTAssertEqual(outcome, .alreadyConfigured)
        let events = rig.snapshot().events
        XCTAssertFalse(events.contains("saveJournal"))
        XCTAssertFalse(events.contains("saveKey"))
        XCTAssertFalse(events.contains("saveStore(false)"))
        XCTAssertFalse(events.contains("storeSelection"))
    }

    func testConflictingJournalOrStoreFailsClosedWithoutDeletion()
        async throws
    {
        let pending = try Self.journal()
        let other = try AtlasSelectedVaultID(
            validating: "33333333-3333-4333-8333-333333333333"
        )
        let conflictingSelection = CreationTransactionRig(
            selection: .selected(other),
            journal: pending,
            key: Self.key,
            store: AtlasLocalVaultCreationCoordinator
                .canonicalEmptyStore(journal: pending)
        )
        let first = AtlasLocalVaultCreationCoordinator(
            environment: conflictingSelection.environment()
        )

        await XCTAssertThrowsCreationFailure(
            try await first.createOrResume(),
            expected: .recoveryRequired
        )
        XCTAssertNotNil(conflictingSelection.snapshot().journal)

        let mismatchedStore = AtlasVaultLocalStoreEnvelope(
            storeID: "unexpected-store",
            createdAt: Self.timestamp,
            updatedAt: Self.timestamp,
            vaultMetadata: AtlasLocalVaultCreationCoordinator
                .canonicalEmptyStore(journal: pending)
                .vaultMetadata,
            records: []
        )
        let conflictingStore = CreationTransactionRig(
            journal: pending,
            key: Self.key,
            store: mismatchedStore
        )
        let second = AtlasLocalVaultCreationCoordinator(
            environment: conflictingStore.environment()
        )

        await XCTAssertThrowsCreationFailure(
            try await second.createOrResume(),
            expected: .recoveryRequired
        )
        let snapshot = conflictingStore.snapshot()
        XCTAssertEqual(snapshot.writeCount, 0)
        XCTAssertEqual(snapshot.selection, .none)
        XCTAssertNotNil(snapshot.journal)
    }

    func testSelectionVerificationAndJournalClearFailuresRemainResumable()
        async throws
    {
        let mismatchedReadback = CreationTransactionRig(
            verificationSelection: .some(.none)
        )
        let first = AtlasLocalVaultCreationCoordinator(
            environment: mismatchedReadback.environment()
        )
        await XCTAssertThrowsCreationFailure(
            try await first.createOrResume(),
            expected: .recoveryRequired
        )
        XCTAssertNotNil(mismatchedReadback.snapshot().journal)

        let clearFailure = CreationTransactionRig(clearFails: true)
        let second = AtlasLocalVaultCreationCoordinator(
            environment: clearFailure.environment()
        )
        await XCTAssertThrowsCreationFailure(
            try await second.createOrResume(),
            expected: .completionPending
        )
        var snapshot = clearFailure.snapshot()
        XCTAssertNotNil(snapshot.journal)
        XCTAssertNotEqual(snapshot.selection, .none)

        clearFailure.setClearFails(false)
        let resumed = try await second.createOrResume()
        XCTAssertEqual(resumed, .resumed)
        snapshot = clearFailure.snapshot()
        XCTAssertNil(snapshot.journal)
        XCTAssertEqual(snapshot.writeCount, 1)
    }

    func testConcurrentCallersAndCancelledCallerShareRetainedOperation()
        async throws
    {
        let gate = CreationSuspensionGate()
        let rig = CreationTransactionRig(selectionGate: gate)
        let coordinator = AtlasLocalVaultCreationCoordinator(
            environment: rig.environment()
        )
        let first = Task {
            try await coordinator.createOrResume()
        }
        await gate.waitUntilEntered()
        let second = Task {
            try await coordinator.createOrResume()
        }
        first.cancel()
        let retained = await coordinator.hasRetainedOperationForTesting()
        XCTAssertTrue(retained)

        await gate.release()

        let firstOutcome = try await first.value
        let secondOutcome = try await second.value
        XCTAssertEqual(firstOutcome, .created)
        XCTAssertEqual(secondOutcome, .created)
        XCTAssertEqual(
            rig.snapshot().events.filter { $0 == "select" }.count,
            2,
            "One operation selects once before and once after registration"
        )
        XCTAssertEqual(rig.snapshot().writeCount, 1)
    }

    func testPauseCancelsAndDrainsWithoutPersistentRollback()
        async
    {
        let gate = CreationSuspensionGate()
        let rig = CreationTransactionRig(selectionGate: gate)
        let coordinator = AtlasLocalVaultCreationCoordinator(
            environment: rig.environment()
        )
        let creation = Task {
            try await coordinator.createOrResume()
        }
        await gate.waitUntilEntered()

        await coordinator.pause()

        do {
            _ = try await creation.value
            XCTFail("Paused creation unexpectedly succeeded")
        } catch let failure as AtlasLocalVaultCreationFailure {
            XCTAssertEqual(failure, .cancelled)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
        let snapshot = rig.snapshot()
        XCTAssertNil(snapshot.journal)
        XCTAssertNil(snapshot.key)
        XCTAssertNil(snapshot.store)
        XCTAssertEqual(snapshot.selection, .none)
        let retained = await coordinator.hasRetainedOperationForTesting()
        XCTAssertFalse(retained)
    }

    func testJournalStoreIsStrictDeviceOnlyAndUpdatesDuplicates()
        throws
    {
        let client = CreationMemoryKeychainClient()
        let store = AtlasKeychainLocalVaultCreationJournalStore(
            client: client
        )
        let pending = try Self.journal()

        XCTAssertNil(try store.loadJournal())
        try store.saveJournal(pending)
        let item = try XCTUnwrap(client.lastAddedItem())
        XCTAssertEqual(
            item.service,
            "com.atlasvault.vault-creation"
        )
        XCTAssertEqual(item.account, "pending-v1")
        XCTAssertEqual(
            item.accessibility,
            .afterFirstUnlockThisDeviceOnly
        )
        XCTAssertEqual(try store.loadJournal(), pending)

        try store.saveJournal(pending)
        XCTAssertEqual(client.updateCount(), 1)

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: item.valueData)
                as? [String: Any]
        )
        XCTAssertEqual(
            Set(object.keys),
            ["format", "version", "vault_id", "store_id", "created_at"]
        )
        XCTAssertNil(object["vault_key"])

        var corrupt = object
        corrupt["unexpected"] = true
        client.set(
            try JSONSerialization.data(withJSONObject: corrupt),
            service: item.service,
            account: item.account
        )
        XCTAssertThrowsError(try store.loadJournal()) { error in
            XCTAssertEqual(
                error as? AtlasLocalVaultCreationFailure,
                .recoveryRequired
            )
        }

        try store.clearJournal()
        try store.clearJournal()
    }

    func testSecureGeneratorReturnsIndependentExactLengthMaterial()
        throws
    {
        let first = try AtlasLocalVaultCreationCoordinator
            .secureRandomVaultKey()
        let second = try AtlasLocalVaultCreationCoordinator
            .secureRandomVaultKey()

        XCTAssertEqual(
            first.count,
            AtlasVaultRecordCrypto.vaultKeyByteCount
        )
        XCTAssertEqual(second.count, first.count)
        XCTAssertNotEqual(first, second)
    }

    func testProductionStoreLoadSeparatesOperationalAndRecoveryFailures() {
        for failure in [
            AtlasVaultPersistenceError.directoryPreparationFailed,
            .readFailed,
            .writeFailed,
            .fileExists,
        ] {
            XCTAssertEqual(
                AtlasLocalVaultCreationCoordinator.storeLoadFailure(
                    for: failure
                ),
                .unavailable
            )
        }

        for failure in [
            AtlasVaultPersistenceError.invalidSession,
            .corruptStore,
            .unsupportedStoreVersion,
            .cryptoFailed,
        ] {
            XCTAssertEqual(
                AtlasLocalVaultCreationCoordinator.storeLoadFailure(
                    for: failure
                ),
                .recoveryRequired
            )
        }

        XCTAssertEqual(
            AtlasLocalVaultCreationCoordinator.storeLoadFailure(
                for: CancellationError()
            ),
            .unavailable
        )
    }

    func testCreationCoreSourceHasNoWeakGeneratorOrDestructiveRollback()
        throws
    {
        let source = try String(
            contentsOf: Self.sourceURL(
                named: "AtlasLocalVaultCreation.swift"
            ),
            encoding: .utf8
        )
        for required in [
            "SecRandomCopyBytes",
            "committedDurabilityUnconfirmed",
            "saveJournal",
            "saveVaultKey",
            "saveEncryptedStoreAtomically",
            "storeSelection",
            "clearJournal",
            "Task.checkCancellation()",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
        for forbidden in [
            "UInt8.random",
            "deleteVaultKey",
            "clearSelection",
            "removeItem(",
            "Task" + ".detached",
            "UserDefaults",
            "LocalAuthentication",
            "LAContext",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private static func journal()
        throws -> AtlasLocalVaultCreationJournal
    {
        try AtlasLocalVaultCreationJournal(
            vaultID: vaultID,
            storeID: storeID,
            createdAt: timestamp
        )
    }

    private static func sourceURL(named name: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
    }
}

private func XCTAssertThrowsCreationFailure<T>(
    _ expression: @autoclosure () async throws -> T,
    expected: AtlasLocalVaultCreationFailure,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        _ = try await expression()
        XCTFail("Expected creation failure", file: file, line: line)
    } catch let failure as AtlasLocalVaultCreationFailure {
        XCTAssertEqual(failure, expected, file: file, line: line)
    } catch {
        XCTFail("Unexpected error: \(error)", file: file, line: line)
    }
}

private final class CreationTransactionRig: Sendable {
    struct Snapshot: Sendable {
        let selection: AtlasVaultIDSelection
        let journal: AtlasLocalVaultCreationJournal?
        let key: Data?
        let store: AtlasVaultLocalStoreEnvelope?
        let atomicResult: AtlasVaultAtomicWriteResult
        let writeCount: Int
        let lastOverwrite: Bool?
        let clearFails: Bool
        let events: [String]
    }

    private struct State: Sendable {
        var selection: AtlasVaultIDSelection
        var verificationSelection: AtlasVaultIDSelection?
        var journal: AtlasLocalVaultCreationJournal?
        var key: Data?
        var store: AtlasVaultLocalStoreEnvelope?
        var atomicResult: AtlasVaultAtomicWriteResult
        var writeCount = 0
        var lastOverwrite: Bool?
        var clearFails: Bool
        var events: [String] = []
    }

    private let state: Mutex<State>
    private let selectionGate: CreationSuspensionGate?

    init(
        selection: AtlasVaultIDSelection = .none,
        verificationSelection: AtlasVaultIDSelection? = nil,
        journal: AtlasLocalVaultCreationJournal? = nil,
        key: Data? = nil,
        store: AtlasVaultLocalStoreEnvelope? = nil,
        atomicResult: AtlasVaultAtomicWriteResult =
            AtlasVaultAtomicWriteResult(commitState: .committed),
        clearFails: Bool = false,
        selectionGate: CreationSuspensionGate? = nil
    ) {
        state = Mutex(
            State(
                selection: selection,
                verificationSelection: verificationSelection,
                journal: journal,
                key: key,
                store: store,
                atomicResult: atomicResult,
                clearFails: clearFails
            )
        )
        self.selectionGate = selectionGate
    }

    func environment() -> AtlasLocalVaultCreationEnvironment {
        AtlasLocalVaultCreationEnvironment(
            selectVaultID: { [self] in
                state.withLock { $0.events.append("select") }
                if let selectionGate {
                    await selectionGate.wait()
                }
                return state.withLock {
                    $0.verificationSelection ?? $0.selection
                }
            },
            storeSelection: { [self] selected in
                state.withLock {
                    $0.events.append("storeSelection")
                    $0.selection = .selected(selected)
                }
            },
            loadJournal: { [self] in
                state.withLock {
                    $0.events.append("loadJournal")
                    return $0.journal
                }
            },
            saveJournal: { [self] journal in
                state.withLock {
                    $0.events.append("saveJournal")
                    $0.journal = journal
                }
            },
            clearJournal: { [self] in
                try state.withLock {
                    $0.events.append("clearJournal")
                    if $0.clearFails {
                        throw AtlasLocalVaultCreationFailure
                            .completionPending
                    }
                    $0.journal = nil
                }
            },
            loadVaultKey: { [self] _ in
                state.withLock {
                    $0.events.append("loadKey")
                    return $0.key
                }
            },
            saveVaultKey: { [self] key, _ in
                state.withLock {
                    $0.events.append("saveKey")
                    $0.key = key
                }
            },
            makeStoreAccess: { [self] _, _ in
                state.withLock {
                    $0.events.append("makeStoreAccess")
                }
                return AtlasLocalVaultCreationStoreAccess(
                    load: { [self] in
                        state.withLock {
                            $0.events.append("loadStore")
                            return $0.store
                        }
                    },
                    save: { [self] store, overwrite in
                        state.withLock {
                            $0.events.append(
                                "saveStore(\(overwrite))"
                            )
                            $0.store = store
                            $0.writeCount += 1
                            $0.lastOverwrite = overwrite
                            return $0.atomicResult
                        }
                    }
                )
            },
            generateVaultID: { [self] in
                state.withLock {
                    $0.events.append("generateVaultID")
                }
                return AtlasLocalVaultCreationTests.vaultID
            },
            generateStoreID: { [self] in
                state.withLock {
                    $0.events.append("generateStoreID")
                }
                return AtlasLocalVaultCreationTests.storeID
            },
            generateTimestamp: { [self] in
                state.withLock {
                    $0.events.append("generateTimestamp")
                }
                return AtlasLocalVaultCreationTests.timestamp
            },
            generateVaultKey: { [self] in
                state.withLock {
                    $0.events.append("generateKey")
                }
                return AtlasLocalVaultCreationTests.key
            }
        )
    }

    func snapshot() -> Snapshot {
        state.withLock {
            Snapshot(
                selection: $0.selection,
                journal: $0.journal,
                key: $0.key,
                store: $0.store,
                atomicResult: $0.atomicResult,
                writeCount: $0.writeCount,
                lastOverwrite: $0.lastOverwrite,
                clearFails: $0.clearFails,
                events: $0.events
            )
        }
    }

    func setAtomicResult(_ result: AtlasVaultAtomicWriteResult) {
        state.withLock { $0.atomicResult = result }
    }

    func setClearFails(_ value: Bool) {
        state.withLock { $0.clearFails = value }
    }
}

private actor CreationSuspensionGate {
    private var continuation:
        AsyncStream<Void>.Continuation?
    private var stream: AsyncStream<Void>?
    private var entered = false
    private var enteredWaiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if stream == nil {
            var captured: AsyncStream<Void>.Continuation?
            stream = AsyncStream { captured = $0 }
            continuation = captured
        }
        entered = true
        let waiters = enteredWaiters
        enteredWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
        var iterator = stream!.makeAsyncIterator()
        _ = await iterator.next()
    }

    func waitUntilEntered() async {
        guard !entered else {
            return
        }
        await withCheckedContinuation { continuation in
            enteredWaiters.append(continuation)
        }
    }

    func release() {
        continuation?.yield(())
        continuation?.finish()
    }
}

private final class CreationMemoryKeychainClient:
    AtlasKeychainClient,
    Sendable
{
    private struct State: Sendable {
        var items: [String: AtlasKeychainItem] = [:]
        var lastAdded: AtlasKeychainItem?
        var updates = 0
    }

    private let state = Mutex(State())

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        state.withLock {
            let key = Self.key(item.service, item.account)
            guard $0.items[key] == nil else {
                return errSecDuplicateItem
            }
            $0.items[key] = item
            $0.lastAdded = item
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
            $0.updates += 1
            return errSecSuccess
        }
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        state.withLock {
            let removed = $0.items.removeValue(
                forKey: Self.key(query.service, query.account)
            )
            return removed == nil ? errSecItemNotFound : errSecSuccess
        }
    }

    func lastAddedItem() -> AtlasKeychainItem? {
        state.withLock { $0.lastAdded }
    }

    func updateCount() -> Int {
        state.withLock { $0.updates }
    }

    func set(
        _ data: Data,
        service: String,
        account: String
    ) {
        state.withLock {
            $0.items[Self.key(service, account)] = AtlasKeychainItem(
                service: service,
                account: account,
                valueData: data,
                accessibility: .afterFirstUnlockThisDeviceOnly
            )
        }
    }

    private static func key(_ service: String, _ account: String) -> String {
        "\(service)\u{0}\(account)"
    }
}
