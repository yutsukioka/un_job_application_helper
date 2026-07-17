import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultObservablePresentationAdapterTests: XCTestCase {
    private static let privateSentinel = "FAKE_OBSERVABLE_PRIVATE_SENTINEL"
    private static let fakePath = "/tmp/FAKE_OBSERVABLE_PRIVATE_PATH"
    private static let fakeKey = Data(repeating: 0xD4, count: 32)

    func testConstructionAndCurrentSnapshotInvokeNoSource() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)

        let snapshot = await observer.currentSnapshot()
        let sourceSubscriptions = await source.subscriptionCount()

        XCTAssertEqual(snapshot, lockedSnapshot())
        XCTAssertEqual(sourceSubscriptions, 0)
    }

    func testFirstExplicitSubscriptionStartsObservationAndYieldsLocked() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)

        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        let initial = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)

        XCTAssertEqual(initial, lockedSnapshot())
        XCTAssertTrue(didSubscribe)
        await subscription.cancel()
    }

    func testMultipleSubscribersReceiveOrderedUpdatesFromOneSource() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let first = await observer.subscribe()
        let second = await observer.subscribe()
        var firstIterator = first.snapshots.makeAsyncIterator()
        var secondIterator = second.snapshots.makeAsyncIterator()
        _ = await firstIterator.next()
        _ = await secondIterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let activating = AtlasVaultPresentationSnapshot(
            status: .activating,
            privateState: nil
        )
        let unlocked = privateSnapshot(status: .unlocked, marker: "ordered")
        await source.send(update(1, activating))
        let firstActivating = await firstIterator.next()
        let secondActivating = await secondIterator.next()
        await source.send(update(2, unlocked))
        let firstUnlocked = await firstIterator.next()
        let secondUnlocked = await secondIterator.next()

        XCTAssertEqual(firstActivating, activating)
        XCTAssertEqual(secondActivating, activating)
        XCTAssertEqual(firstUnlocked, unlocked)
        XCTAssertEqual(secondUnlocked, unlocked)
        let sourceSubscriptions = await source.subscriptionCount()
        XCTAssertEqual(sourceSubscriptions, 1)
        await first.cancel()
        await second.cancel()
    }

    func testSubscriberCancellationReleasesOnlyCancelledSubscriber() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let first = await observer.subscribe()
        let second = await observer.subscribe()
        var firstIterator = first.snapshots.makeAsyncIterator()
        var secondIterator = second.snapshots.makeAsyncIterator()
        _ = await firstIterator.next()
        _ = await secondIterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        await first.cancel()
        let firstCompletion = await firstIterator.next()
        let terminationsAfterFirst = await source.terminationCount()
        XCTAssertNil(firstCompletion)
        XCTAssertEqual(terminationsAfterFirst, 0)

        let update = privateSnapshot(status: .unlocked, marker: "remaining")
        await source.send(self.update(1, update))
        let received = await secondIterator.next()
        XCTAssertEqual(received, update)

        await second.cancel()
        let secondCompletion = await secondIterator.next()
        let terminationsAfterSecond = await source.terminationCount()
        XCTAssertNil(secondCompletion)
        XCTAssertEqual(terminationsAfterSecond, 0)
    }

    func testSlowSubscriberUsesBoundedLatestSnapshotBuffer() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        await source.send(update(
            1,
            AtlasVaultPresentationSnapshot(status: .activating, privateState: nil)
        ))
        await source.send(update(
            2,
            privateSnapshot(status: .unlocked, marker: "intermediate")
        ))
        let latest = privateSnapshot(
            status: .saveDurabilityUnconfirmed,
            marker: "latest"
        )
        await source.send(update(3, latest))
        let didInstallLatest = await waitForSnapshot(latest, from: observer)
        XCTAssertTrue(didInstallLatest)

        let received = await iterator.next()
        XCTAssertEqual(received, latest)
        await subscription.cancel()
    }

    func testNonPrivateStatusesDiscardUnsafePrivateProjection() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let statuses: [AtlasVaultPresentationStatus] = [
            .noVault,
            .activating,
            .locking,
            .keyUnavailable,
            .corruptStore,
            .unsupportedVersion,
            .cancelled,
            .failed,
            .locked,
        ]
        for (offset, status) in statuses.enumerated() {
            await source.send(update(
                UInt64(offset + 1),
                privateSnapshot(status: status, marker: "unsafe-\(offset)")
            ))
            let received = await iterator.next()
            XCTAssertEqual(received?.status, status)
            XCTAssertNil(received?.privateState)
        }

        await subscription.cancel()
    }

    func testSaveInProgressRetainsCurrentPrivateProjection() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let saving = privateSnapshot(
            status: .saveInProgress,
            marker: "saving"
        )
        await source.send(update(1, saving))
        let received = await iterator.next()

        XCTAssertEqual(received, saving)
        XCTAssertNotNil(received?.privateState)
        await subscription.cancel()
    }

    func testActivationFailureClearsPriorPrivateProjection() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let unlocked = privateSnapshot(status: .unlocked, marker: "before-failure")
        await source.send(update(1, unlocked))
        let receivedUnlocked = await iterator.next()
        XCTAssertNotNil(receivedUnlocked?.privateState)

        let failed = privateSnapshot(status: .failed, marker: "must-clear")
        await source.send(update(2, failed))
        let receivedFailure = await iterator.next()

        XCTAssertEqual(receivedFailure?.status, .failed)
        XCTAssertNil(receivedFailure?.privateState)
        await subscription.cancel()
    }

    func testRecoverableSaveFailurePreservesCurrentPrivateProjection() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let unlocked = privateSnapshot(status: .unlocked, marker: "recoverable")
        await source.send(update(1, unlocked))
        let acceptedUnlocked = await iterator.next()
        let recoverable = AtlasVaultPresentationSnapshot(
            status: .saveFailed,
            privateState: unlocked.privateState
        )
        await source.send(update(2, recoverable))
        let acceptedRecoverable = await iterator.next()

        XCTAssertEqual(acceptedRecoverable?.status, .saveFailed)
        XCTAssertEqual(
            acceptedRecoverable?.privateState,
            acceptedUnlocked?.privateState
        )
        await subscription.cancel()
    }

    func testCommittedDurabilityWarningInstallsRefreshedProjection() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let initial = privateSnapshot(status: .unlocked, marker: "before-commit")
        await source.send(update(1, initial))
        _ = await iterator.next()
        let committed = privateSnapshot(
            status: .saveDurabilityUnconfirmed,
            marker: "after-commit"
        )
        await source.send(update(2, committed))
        let received = await iterator.next()

        XCTAssertEqual(received, committed)
        XCTAssertNotEqual(received?.privateState, initial.privateState)
        await subscription.cancel()
    }

    func testFatalContainmentAndLockClearPrivateProjection() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        await source.send(update(
            1,
            privateSnapshot(status: .unlocked, marker: "before-fatal")
        ))
        let receivedUnlocked = await iterator.next()
        XCTAssertNotNil(receivedUnlocked?.privateState)

        await source.send(update(
            2,
            privateSnapshot(status: .locking, marker: "fatal-locking")
        ))
        let locking = await iterator.next()
        await source.send(update(
            3,
            privateSnapshot(status: .locked, marker: "fatal-locked")
        ))
        let locked = await iterator.next()

        XCTAssertEqual(locking?.status, .locking)
        XCTAssertNil(locking?.privateState)
        XCTAssertEqual(locked, lockedSnapshot())
        await subscription.cancel()
    }

    func testRepeatedEquivalentLockUpdateIsDeduplicated() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        let initial = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertEqual(initial, lockedSnapshot())
        XCTAssertTrue(didSubscribe)

        await source.send(update(1, lockedSnapshot()))
        let activating = AtlasVaultPresentationSnapshot(
            status: .activating,
            privateState: nil
        )
        await source.send(update(2, activating))

        let received = await iterator.next()
        XCTAssertEqual(received, activating)
        await subscription.cancel()
    }

    func testStaleSequenceCannotReplaceNewerSnapshot() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let current = privateSnapshot(status: .unlocked, marker: "newer")
        await source.send(update(2, current))
        let receivedCurrent = await iterator.next()
        XCTAssertEqual(receivedCurrent, current)
        await source.send(update(
            1,
            privateSnapshot(status: .locked, marker: "stale")
        ))
        let next = privateSnapshot(status: .saveFailed, marker: "next")
        await source.send(update(3, next))

        let receivedNext = await iterator.next()
        let retainedNext = await observer.currentSnapshot()
        XCTAssertEqual(receivedNext, next)
        XCTAssertEqual(retainedNext, next)
        await subscription.cancel()
    }

    func testLastCancellationKeepsObservationCurrentForLaterSubscriber() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let first = await observer.subscribe()
        var firstIterator = first.snapshots.makeAsyncIterator()
        _ = await firstIterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let current = privateSnapshot(status: .unlocked, marker: "restart")
        await source.send(update(5, current))
        let receivedCurrent = await firstIterator.next()
        XCTAssertEqual(receivedCurrent, current)
        await first.cancel()
        let firstCompletion = await firstIterator.next()
        XCTAssertNil(firstCompletion)

        await source.send(update(
            6,
            privateSnapshot(status: .locked, marker: "locked-without-subscriber")
        ))
        let didRetainLocked = await waitForSnapshot(
            lockedSnapshot(),
            from: observer
        )
        XCTAssertTrue(didRetainLocked)

        let second = await observer.subscribe()
        var secondIterator = second.snapshots.makeAsyncIterator()
        let secondInitial = await secondIterator.next()
        let sourceSubscriptions = await source.subscriptionCount()
        XCTAssertEqual(secondInitial, lockedSnapshot())
        XCTAssertEqual(sourceSubscriptions, 1)
        let warning = privateSnapshot(
            status: .saveDurabilityUnconfirmed,
            marker: "fresh-restart"
        )
        await source.send(update(7, warning))

        let receivedWarning = await secondIterator.next()
        XCTAssertEqual(receivedWarning, warning)
        await second.cancel()
    }

    func testUpstreamFinishCompletesSubscribersWithoutPersistingState() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let current = privateSnapshot(status: .unlocked, marker: "finish")
        await source.send(update(1, current))
        let receivedCurrent = await iterator.next()
        XCTAssertEqual(receivedCurrent, current)
        await source.finish()

        let failClosed = await iterator.next()
        let completion = await iterator.next()
        let retained = await observer.currentSnapshot()
        XCTAssertEqual(failClosed, lockedSnapshot())
        XCTAssertNil(completion)
        XCTAssertEqual(retained, lockedSnapshot())
    }

    func testStatelessAdapterOutputFlowsThroughObservableBoundary() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        let snapshot = AtlasVaultPresentationAdapter().makeSnapshot(
            runtimeStatus: .unlocked,
            privateState: hydratedState(),
            generation: AtlasVaultPresentationGeneration(),
            commandState: .saveDurabilityUnconfirmed
        )
        await source.send(update(1, snapshot))

        let received = await iterator.next()
        XCTAssertEqual(received, snapshot)
        XCTAssertEqual(received?.status, .saveDurabilityUnconfirmed)
        XCTAssertNotNil(received?.privateState)
        await subscription.cancel()
    }

    func testDescriptionsAndTypesRemainNonPersistentAndRedacted() async {
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        let snapshot = privateSnapshot(
            status: .unlocked,
            marker: Self.privateSentinel
        )
        let update = self.update(1, snapshot)
        let rendered = [
            String(describing: observer),
            String(reflecting: observer),
            String(describing: subscription),
            String(reflecting: subscription),
            String(describing: update),
            String(reflecting: update),
        ].joined(separator: " ")
        let keyBase64 = Self.fakeKey.base64EncodedString()
        let keyHex = Self.fakeKey.map { String(format: "%02x", $0) }.joined()

        for forbidden in [
            Self.privateSentinel,
            Self.fakePath,
            keyBase64,
            keyHex,
            "record-observable",
            "FAKE_ENCRYPTED_RECORD_ENVELOPE",
            "FAKE_REVISION",
        ] {
            XCTAssertFalse(rendered.contains(forbidden), forbidden)
        }
        XCTAssertFalse(update is any Encodable)
        XCTAssertFalse(subscription is any Encodable)
        await subscription.cancel()
    }

    func testObservationDoesNotMutatePublicSnapshot() async throws {
        let publicSnapshot = try makePublicSnapshot()
        let before = try encodedPublicSnapshot(publicSnapshot)
        let source = ObservablePresentationSource()
        let observer = AtlasVaultObservablePresentationAdapter(source: source)
        let subscription = await observer.subscribe()
        var iterator = subscription.snapshots.makeAsyncIterator()
        _ = await iterator.next()
        let didSubscribe = await source.waitForSubscriptionCount(1)
        XCTAssertTrue(didSubscribe)

        await source.send(update(
            1,
            privateSnapshot(status: .unlocked, marker: "public-isolation")
        ))
        _ = await iterator.next()

        XCTAssertEqual(try encodedPublicSnapshot(publicSnapshot), before)
        await subscription.cancel()
    }

    func testSourceHasNoUIPlatformPersistenceRuntimeOrSecretCoupling() throws {
        let source = try String(contentsOf: sourceFileURL(), encoding: .utf8)
        for forbidden in [
            "SwiftUI",
            "ObservableObject",
            "@Published",
            "@State",
            "@Environment",
            "@AppStorage",
            "@SceneStorage",
            "UserDefaults",
            "FileManager",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "URLSession",
            "Data.write",
            "createFile",
            "Codable",
            "SearchViewModel",
            "AtlasLocalCache",
            "AtlasPublicLocalSnapshot",
            "AtlasVaultRuntimeFacade",
            "AtlasVaultHydratedState",
            "AtlasVaultEncryptedRecordEnvelope",
            "AtlasVaultUnlockedSession",
            "vaultKey",
            "@main",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private func update(
        _ sequence: UInt64,
        _ snapshot: AtlasVaultPresentationSnapshot
    ) -> AtlasVaultPresentationUpdate {
        AtlasVaultPresentationUpdate(sequence: sequence, snapshot: snapshot)
    }

    private func lockedSnapshot() -> AtlasVaultPresentationSnapshot {
        AtlasVaultPresentationSnapshot(status: .locked, privateState: nil)
    }

    private func privateSnapshot(
        status: AtlasVaultPresentationStatus,
        marker: String
    ) -> AtlasVaultPresentationSnapshot {
        let generation = AtlasVaultPresentationGeneration()
        let identifier = AtlasVaultPresentationID(
            recordID: "record-observable-\(marker)",
            generation: generation
        )
        let privateState = AtlasVaultPrivatePresentationState(
            savedSearches: [],
            savedJobs: [AtlasVaultSavedJobPresentation(
                id: identifier,
                applicationID: "FAKE_APPLICATION_\(marker)",
                jobKey: "\(Self.privateSentinel)_JOB_\(marker)",
                status: "FAKE_STATUS_\(marker)",
                notes: "FAKE_NOTES_\(marker)",
                appliedAt: nil,
                updatedAt: "2026-01-01T00:00:00Z"
            )],
            applicationNotes: [],
            profileSnippets: [],
            draftMetadata: []
        )
        return AtlasVaultPresentationSnapshot(
            status: status,
            privateState: privateState
        )
    }

    private func hydratedState() -> AtlasVaultHydratedState {
        AtlasVaultHydratedState(savedJobs: [AtlasHydratedSavedJob(
            metadata: AtlasHydratedRecordMetadata(
                id: "record-observable-integration",
                revision: "FAKE_REVISION",
                parentRevision: nil,
                deleted: false,
                keyID: "FAKE_KEY_ID"
            ),
            payload: AtlasSavedJobVaultPayload(
                id: "FAKE_APPLICATION_ID",
                jobKey: "\(Self.privateSentinel)_JOB",
                status: "FAKE_STATUS",
                notes: "FAKE_NOTES",
                appliedAt: nil,
                updatedAt: "2026-01-01T00:00:00Z"
            ),
            clientCreatedAt: "2026-01-01T00:00:00Z",
            clientUpdatedAt: "2026-01-01T00:00:00Z"
        )])
    }

    private func waitForSnapshot(
        _ expected: AtlasVaultPresentationSnapshot,
        from observer: AtlasVaultObservablePresentationAdapter
    ) async -> Bool {
        for _ in 0..<500 {
            if await observer.currentSnapshot() == expected {
                return true
            }
            await Task.yield()
        }
        return await observer.currentSnapshot() == expected
    }

    private func makePublicSnapshot() throws -> AtlasPublicLocalSnapshot {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(AtlasPublicLocalSnapshot.self, from: Data("""
        {
          "savedAt": "2026-01-01T00:00:00Z",
          "baseURL": "http://127.0.0.1:8765",
          "health": {
            "status": "ok",
            "db_path": null,
            "schema_version": "test",
            "open_jobs": 0,
            "enabled_sources": 0,
            "last_sync_at": null
          },
          "searchResponse": {
            "total": 0,
            "limit": 0,
            "offset": 0,
            "results": [],
            "facets": {},
            "facet_labels": {},
            "unclassified_count": 0
          },
          "sources": [],
          "recentRuns": []
        }
        """.utf8))
    }

    private func encodedPublicSnapshot(
        _ snapshot: AtlasPublicLocalSnapshot
    ) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(snapshot)
    }

    private func sourceFileURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent(
                "Sources/AtlasUI/AtlasVaultObservablePresentationAdapter.swift"
            )
    }
}

private actor ObservablePresentationSource:
    AtlasVaultPresentationUpdateSourcing
{
    private var subscriptions = 0
    private var terminations = 0
    private var continuations: [
        UUID: AsyncStream<AtlasVaultPresentationUpdate>.Continuation
    ] = [:]

    func updates() -> AsyncStream<AtlasVaultPresentationUpdate> {
        subscriptions += 1
        let identifier = UUID()
        let pair = AsyncStream.makeStream(
            of: AtlasVaultPresentationUpdate.self,
            bufferingPolicy: .unbounded
        )
        pair.continuation.onTermination = { [weak self] _ in
            Task {
                await self?.didTerminate(identifier)
            }
        }
        continuations[identifier] = pair.continuation
        return pair.stream
    }

    func send(_ update: AtlasVaultPresentationUpdate) {
        for continuation in continuations.values {
            continuation.yield(update)
        }
    }

    func finish() {
        let current = continuations.values
        continuations.removeAll()
        for continuation in current {
            continuation.finish()
        }
    }

    func subscriptionCount() -> Int {
        subscriptions
    }

    func terminationCount() -> Int {
        terminations
    }

    func waitForSubscriptionCount(_ count: Int) async -> Bool {
        for _ in 0..<500 {
            if subscriptions >= count { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return subscriptions >= count
    }

    private func didTerminate(_ identifier: UUID) {
        guard continuations.removeValue(forKey: identifier) != nil else {
            return
        }
        terminations += 1
    }
}
