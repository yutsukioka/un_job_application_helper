import Combine
import Foundation
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultProductionPresentationOwnerTests: XCTestCase {
    private static let fakeQuery = "FAKE_PHASE_2D57_QUERY_DO_NOT_LOG"
    private static let fakeJobID = "FAKE_PHASE_2D57_JOB_DO_NOT_LOG"
    private static let fakeVaultID = "00000000-0000-4000-8000-000000000257"

    func testOwnerIsObservableMainActorResetterWithPrivateFreeInitialState() {
        let owner = AtlasVaultProductionPresentationOwner()

        requireObservableObject(owner)
        requireOwnerResetter(owner)
        XCTAssertEqual(owner.flowState, Self.lockedFlow(canRequestUnlock: false))
        XCTAssertEqual(owner.flowState.mode, .lockedPublic)
        XCTAssertNil(owner.flowState.unlockPanelState)
        XCTAssertFalse(owner.flowState.publicShell.canRequestUnlock)
        XCTAssertEqual(
            AtlasVaultUnlockCapabilities.currentProduction.availableMethods,
            [.localKey]
        )
    }

    func testConstructionInvokesNoResetHookAndDescriptionsAreRedacted() async {
        let hook = OwnerResetHook(suspendingCalls: [])
        let owner = AtlasVaultProductionPresentationOwner(
            beforeResetCommit: { await hook.call() }
        )

        XCTAssertEqual(hook.callCount, 0)
        let rendered = [owner.description, owner.debugDescription]
            .joined(separator: "\n")
        XCTAssertTrue(rendered.contains("<redacted>"))
        XCTAssertFalse(rendered.contains(Self.fakeQuery))
        XCTAssertFalse(rendered.contains(Self.fakeJobID))
        XCTAssertFalse(rendered.contains(Self.fakeVaultID))
    }

    func testOrdinaryResetsEstablishSameAndLaterGenerations() async {
        let owner = AtlasVaultProductionPresentationOwner()
        let first = AtlasVaultProductionHostGeneration()
        let later = AtlasVaultProductionHostGeneration()
        let firstFlow = Self.lockedFlow(canRequestUnlock: true)
        let sameGenerationFlow = Self.searchingFlow()
        let laterFlow = Self.noVaultFlow()

        await ownerExpectTrue(
            await owner.resetPresentation(to: firstFlow, generation: first)
        )
        XCTAssertEqual(owner.flowState, firstFlow)
        await ownerExpectTrue(
            await owner.resetPresentation(
                to: sameGenerationFlow,
                generation: first
            )
        )
        XCTAssertEqual(owner.flowState, sameGenerationFlow)
        await ownerExpectTrue(
            await owner.resetPresentation(to: laterFlow, generation: later)
        )
        XCTAssertEqual(owner.flowState, laterFlow)
    }

    func testSupersedeRequiresExactGenerationUntilItCommits() async {
        let owner = AtlasVaultProductionPresentationOwner()
        let established = AtlasVaultProductionHostGeneration()
        let required = AtlasVaultProductionHostGeneration()
        let rejected = AtlasVaultProductionHostGeneration()
        let original = Self.lockedFlow(canRequestUnlock: true)
        let exact = Self.reconciliationFlow()
        let later = Self.noVaultFlow()

        await ownerExpectTrue(
            await owner.resetPresentation(to: original, generation: established)
        )
        await owner.supersedePresentationGeneration(required)
        XCTAssertEqual(owner.flowState, original)

        await ownerExpectFalse(
            await owner.resetPresentation(to: later, generation: rejected)
        )
        XCTAssertEqual(owner.flowState, original)
        await ownerExpectTrue(
            await owner.resetPresentation(to: exact, generation: required)
        )
        XCTAssertEqual(owner.flowState, exact)

        await ownerExpectTrue(
            await owner.resetPresentation(to: later, generation: rejected)
        )
        XCTAssertEqual(owner.flowState, later)
    }

    func testResetSuspendedBeforeSupersedeCannotCommitAfterward() async {
        let hook = OwnerResetHook(suspendingCalls: [1])
        let owner = AtlasVaultProductionPresentationOwner(
            beforeResetCommit: { await hook.call() }
        )
        let staleGeneration = AtlasVaultProductionHostGeneration()
        let terminalGeneration = AtlasVaultProductionHostGeneration()
        let staleFlow = Self.searchingFlow()
        let terminalFlow = Self.reconciliationFlow()

        let stale = Task { @MainActor in
            await owner.resetPresentation(
                to: staleFlow,
                generation: staleGeneration
            )
        }
        await hook.waitUntilCallCount(1)
        await owner.supersedePresentationGeneration(terminalGeneration)
        XCTAssertEqual(owner.flowState, Self.lockedFlow(canRequestUnlock: false))

        await ownerExpectTrue(
            await owner.resetPresentation(
                to: terminalFlow,
                generation: terminalGeneration
            )
        )
        hook.release(call: 1)
        await ownerExpectFalse(await stale.value)
        XCTAssertEqual(owner.flowState, terminalFlow)
    }

    func testNewerOrdinaryResetInvalidatesOlderSuspendedReset() async {
        let hook = OwnerResetHook(suspendingCalls: [1])
        let owner = AtlasVaultProductionPresentationOwner(
            beforeResetCommit: { await hook.call() }
        )
        let olderGeneration = AtlasVaultProductionHostGeneration()
        let newerGeneration = AtlasVaultProductionHostGeneration()
        let olderFlow = Self.searchingFlow()
        let newerFlow = Self.noVaultFlow()

        let older = Task { @MainActor in
            await owner.resetPresentation(
                to: olderFlow,
                generation: olderGeneration
            )
        }
        await hook.waitUntilCallCount(1)
        await ownerExpectTrue(
            await owner.resetPresentation(
                to: newerFlow,
                generation: newerGeneration
            )
        )
        hook.release(call: 1)

        await ownerExpectFalse(await older.value)
        XCTAssertEqual(owner.flowState, newerFlow)
    }

    func testFailedResetPublishesNothingAndSuccessfulResetPublishesOnce() async {
        let owner = AtlasVaultProductionPresentationOwner()
        let required = AtlasVaultProductionHostGeneration()
        let stale = AtlasVaultProductionHostGeneration()
        var publicationCount = 0
        let cancellable = owner.objectWillChange.sink {
            publicationCount += 1
        }

        await owner.supersedePresentationGeneration(required)
        await ownerExpectFalse(
            await owner.resetPresentation(
                to: Self.searchingFlow(),
                generation: stale
            )
        )
        XCTAssertEqual(publicationCount, 0)

        await ownerExpectTrue(
            await owner.resetPresentation(
                to: Self.reconciliationFlow(),
                generation: required
            )
        )
        XCTAssertEqual(publicationCount, 1)
        withExtendedLifetime(cancellable) {}
    }

    func testMultipleObserversShareOnePublishedFlow() async {
        let owner = AtlasVaultProductionPresentationOwner()
        var firstPublicationCount = 0
        var secondPublicationCount = 0
        let firstCancellable = owner.objectWillChange.sink {
            firstPublicationCount += 1
        }
        let secondCancellable = owner.objectWillChange.sink {
            secondPublicationCount += 1
        }
        let flow = Self.noVaultFlow()

        await ownerExpectTrue(
            await owner.resetPresentation(
                to: flow,
                generation: AtlasVaultProductionHostGeneration()
            )
        )
        XCTAssertEqual(firstPublicationCount, 1)
        XCTAssertEqual(secondPublicationCount, 1)
        XCTAssertEqual(owner.flowState, flow)
        withExtendedLifetime((firstCancellable, secondCancellable)) {}
    }

    func testRealHostStartAndTerminalStopUseStrictConcreteOwner() async throws {
        let graph = Self.makeHostGraph()

        let started = try await graph.host.start()
        XCTAssertEqual(graph.owner.flowState, started)
        XCTAssertEqual(started.mode, .lockedPublic)
        XCTAssertTrue(started.publicShell.canRequestUnlock)

        let stopped = await graph.host.stop()
        XCTAssertEqual(graph.owner.flowState, stopped)
        XCTAssertEqual(stopped.mode, .lockedPublic)
        XCTAssertFalse(stopped.publicShell.canRequestUnlock)
        await ownerExpectEqual(await graph.runtime.lockCallCount(), 1)
        let stoppedSnapshot = await graph.pipeline.currentSnapshot()
        XCTAssertNil(stoppedSnapshot.privateState)
    }

    func testRealHostStopReplacesUnlockedTransitionWithLockedOwnerState()
        async throws
    {
        let graph = Self.makeHostGraph()
        _ = try await graph.host.start()
        await ownerExpectTrue(
            await graph.owner.resetPresentation(
                to: Self.unlockedFlow(),
                generation: AtlasVaultProductionHostGeneration()
            )
        )
        XCTAssertEqual(graph.owner.flowState.mode, .unlockedTransition)

        let stopped = await graph.host.stop()

        XCTAssertEqual(stopped.mode, .lockedPublic)
        XCTAssertEqual(graph.owner.flowState, stopped)
        XCTAssertFalse(graph.owner.flowState.publicShell.canRequestUnlock)
    }

    func testWillTerminateCommitsTerminalOwnerStateAndRemainsPrivateFree()
        async throws
    {
        let graph = Self.makeHostGraph()
        _ = try await graph.host.start()

        let terminated = await graph.host.handleLifecycleEvent(.willTerminate)

        XCTAssertEqual(terminated.mode, .lockedPublic)
        XCTAssertEqual(graph.owner.flowState, terminated)
        XCTAssertFalse(terminated.publicShell.canRequestUnlock)
        let terminalSnapshot = await graph.pipeline.currentSnapshot()
        XCTAssertNil(terminalSnapshot.privateState)
        await ownerExpectEqual(
            await graph.lifecycle.events(),
            [.willTerminate]
        )
    }

    func testSuspendedStaleOwnerResetCannotOverwriteRealHostTerminalState()
        async throws
    {
        let hook = OwnerResetHook(suspendingCalls: [3])
        let owner = AtlasVaultProductionPresentationOwner(
            beforeResetCommit: { await hook.call() }
        )
        let graph = Self.makeHostGraph(owner: owner)
        _ = try await graph.host.start()

        let stale = Task { @MainActor in
            await owner.resetPresentation(
                to: Self.unlockedFlow(),
                generation: AtlasVaultProductionHostGeneration()
            )
        }
        await hook.waitUntilCallCount(3)

        let stopped = await graph.host.stop()
        hook.release(call: 3)

        await ownerExpectFalse(await stale.value)
        XCTAssertEqual(owner.flowState, stopped)
        XCTAssertEqual(owner.flowState.mode, .lockedPublic)
        XCTAssertFalse(owner.flowState.publicShell.canRequestUnlock)
    }

    func testOwnerSourceHasNoPrivatePersistenceServiceOrTaskCoupling() throws {
        let source = try Self.source(
            named: "AtlasVaultProductionPresentationOwner.swift"
        )
        for forbidden in [
            "AtlasVaultPrivateState",
            "AtlasVaultHydratedState",
            "AtlasVaultPrivatePresentationState",
            "vaultID",
            "passphrase",
            "recoveryKey",
            "suppliedTestVaultKey",
            "Keychain",
            "SecItem",
            "FileManager",
            "URLSession",
            "UserDefaults",
            "@AppStorage",
            "@SceneStorage",
            "NotificationCenter",
            "Codable",
            "Task {",
            "Task." + "detached",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        XCTAssertTrue(source.contains("@MainActor"))
        XCTAssertTrue(source.contains("@Published public private(set)"))
        XCTAssertTrue(source.contains("ObservableObject"))
        XCTAssertTrue(source.contains("GenerationAuthority"))
    }

    func testResetHookCopiesWaiterKeysBeforeDictionaryMutation() throws {
        let source = try String(
            contentsOf: URL(fileURLWithPath: #filePath),
            encoding: .utf8
        )
        let hookMarker = try XCTUnwrap(
            source.range(
                of: "private final class OwnerResetHook",
                options: .backwards
            )
        )
        let graphMarker = try XCTUnwrap(
            source.range(
                of: "private struct OwnerHostGraph",
                range: hookMarker.upperBound..<source.endIndex
            )
        )
        let hookBody = String(
            source[hookMarker.lowerBound..<graphMarker.lowerBound]
        )
        XCTAssertTrue(
            hookBody.contains("let readyKeys = callWaiters.keys.filter")
        )
        XCTAssertFalse(
            hookBody.contains("for key in callWaiters." + "keys where")
        )
    }

    private func requireObservableObject<T: ObservableObject>(_ value: T) {}

    private func requireOwnerResetter<Owner>(
        _ value: Owner
    ) where Owner: AtlasVaultProductionPresentationOwnerResetting {}

    private static func lockedFlow(
        canRequestUnlock: Bool
    ) -> AtlasLockedShellUnlockFlowState {
        flow(
            shell: AtlasLockedPublicShellModel(
                serviceStatus: .unavailable,
                canRequestUnlock: canRequestUnlock
            ),
            status: .locked,
            isPanelPresented: false
        )
    }

    private static func searchingFlow() -> AtlasLockedShellUnlockFlowState {
        flow(
            shell: AtlasLockedPublicShellModel(
                serviceStatus: .checking,
                cacheFreshness: .unavailable,
                searchQuery: fakeQuery,
                publicJobs: [],
                isSearching: true,
                canRequestUnlock: false
            ),
            status: .locked,
            isPanelPresented: false
        )
    }

    private static func noVaultFlow() -> AtlasLockedShellUnlockFlowState {
        flow(
            shell: AtlasLockedPublicShellModel(
                vaultStatus: .noVault,
                serviceStatus: .unavailable,
                canRequestUnlock: false
            ),
            status: .locked,
            isPanelPresented: false
        )
    }

    private static func reconciliationFlow()
        -> AtlasLockedShellUnlockFlowState
    {
        flow(
            shell: AtlasLockedPublicShellModel(
                serviceStatus: .unavailable,
                canRequestUnlock: false
            ),
            status: .hostReconciliationRequired,
            isPanelPresented: true
        )
    }

    private static func unlockedFlow() -> AtlasLockedShellUnlockFlowState {
        flow(
            shell: AtlasLockedPublicShellModel(
                serviceStatus: .unavailable,
                canRequestUnlock: false
            ),
            status: .unlocked,
            isPanelPresented: false
        )
    }

    private static func flow(
        shell: AtlasLockedPublicShellModel,
        status: AtlasVaultUnlockPresentationStatus,
        isPanelPresented: Bool
    ) -> AtlasLockedShellUnlockFlowState {
        AtlasLockedShellUnlockFlowState(
            publicShell: shell,
            unlockPresentationState: AtlasVaultUnlockPresentationState(
                capabilities: .currentProduction,
                selectedMethod: nil,
                status: status
            ),
            isUnlockPanelPresented: isPanelPresented
        )
    }

    private static func makeHostGraph(
        owner: AtlasVaultProductionPresentationOwner =
            AtlasVaultProductionPresentationOwner()
    ) -> OwnerHostGraph {
        let runtime = OwnerRuntimeFake()
        let lifecycle = OwnerLifecycleFake()
        let pipeline = AtlasVaultProductionPresentationPipeline()
        let dependencies = AtlasVaultProductionHostDependencies(
            publicJobs: OwnerPublicJobsFake(),
            publicSnapshotRestorer: OwnerSnapshotRestorerFake(),
            vaultIDSelector: OwnerVaultSelectorFake(),
            runtime: runtime,
            lifecycle: lifecycle,
            presentation: pipeline,
            presentationOwner: owner,
            unlockCoordinator: OwnerUnlockCoordinatorFake(),
            unlockControllerBuilder:
                AtlasVaultProductionUnlockPresentationControllerBuilder()
        )
        return OwnerHostGraph(
            host: AtlasVaultProductionHost(dependencies: dependencies),
            owner: owner,
            pipeline: pipeline,
            runtime: runtime,
            lifecycle: lifecycle
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

@MainActor
private final class OwnerResetHook {
    private let suspendingCalls: Set<Int>
    private var entered = 0
    private var callWaiters: [Int: [CheckedContinuation<Void, Never>]] = [:]
    private var releases: [Int: CheckedContinuation<Void, Never>] = [:]

    init(suspendingCalls: Set<Int>) {
        self.suspendingCalls = suspendingCalls
    }

    var callCount: Int {
        entered
    }

    func call() async {
        entered += 1
        let call = entered
        let readyKeys = callWaiters.keys.filter { $0 <= entered }
        let waiters = readyKeys.flatMap {
            callWaiters.removeValue(forKey: $0) ?? []
        }
        for waiter in waiters {
            waiter.resume()
        }
        guard suspendingCalls.contains(call) else {
            return
        }
        await withCheckedContinuation { continuation in
            releases[call] = continuation
        }
    }

    func waitUntilCallCount(_ count: Int) async {
        guard entered < count else {
            return
        }
        await withCheckedContinuation { continuation in
            callWaiters[count, default: []].append(continuation)
        }
    }

    func release(call: Int) {
        releases.removeValue(forKey: call)?.resume()
    }
}

private struct OwnerHostGraph {
    let host: AtlasVaultProductionHost
    let owner: AtlasVaultProductionPresentationOwner
    let pipeline: AtlasVaultProductionPresentationPipeline
    let runtime: OwnerRuntimeFake
    let lifecycle: OwnerLifecycleFake
}

private actor OwnerPublicJobsFake: AtlasPublicJobSearching {
    func health() async throws(AtlasPublicJobServiceError)
        -> AtlasPublicServiceHealth
    {
        throw .unavailable
    }

    func search(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult {
        throw .unavailable
    }

    func sources() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicSourceStatus]
    {
        throw .unavailable
    }

    func updates() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicUpdateStatus]
    {
        throw .unavailable
    }

    func detail(
        for reference: AtlasPublicJobReference
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobDetailResult {
        throw .unavailable
    }
}

private actor OwnerSnapshotRestorerFake: AtlasPublicSnapshotRestoring {
    func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
    {
        nil
    }
}

private actor OwnerVaultSelectorFake: AtlasVaultIDSelecting {
    func selectVaultID() async throws(AtlasVaultIDSelectionError)
        -> AtlasVaultIDSelection
    {
        .none
    }
}

private actor OwnerRuntimeFake: AtlasVaultRuntimeFacading {
    private var runtimeStatus: AtlasVaultRuntimeStatus = .locked
    private var locks = 0

    func status() async -> AtlasVaultRuntimeStatus {
        runtimeStatus
    }

    func activate(_ request: AtlasVaultRuntimeActivationRequest) async throws {
        runtimeStatus = .unlocked
    }

    func lock() async {
        locks += 1
        runtimeStatus = .locked
    }

    func apply(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async throws -> AtlasVaultSaveOutcome {
        .committed
    }

    func lockCallCount() -> Int {
        locks
    }
}

private actor OwnerLifecycleFake: AtlasVaultLifecycleCoordinating {
    private var handled: [AtlasVaultLifecycleEvent] = []

    func handle(_ event: AtlasVaultLifecycleEvent) async {
        handled.append(event)
    }

    func status() async -> AtlasVaultLifecycleStatus {
        AtlasVaultLifecycleStatus(
            lastEvent: handled.last,
            hasPendingGraceLock: false,
            failure: nil
        )
    }

    func events() -> [AtlasVaultLifecycleEvent] {
        handled
    }
}

private actor OwnerUnlockCoordinatorFake: AtlasVaultUnlockRequestCoordinating {
    func dispatch(_ request: AtlasVaultUnlockRequest) async throws {}

    func cancel(_ request: AtlasVaultUnlockRequest) async -> Bool {
        true
    }
}

@MainActor
private func ownerExpectEqual<Value: Equatable & Sendable>(
    _ actual: Value,
    _ expected: Value,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    XCTAssertEqual(actual, expected, file: file, line: line)
}

@MainActor
private func ownerExpectTrue(
    _ expression: Bool,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    XCTAssertTrue(expression, file: file, line: line)
}

@MainActor
private func ownerExpectFalse(
    _ expression: Bool,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    XCTAssertFalse(expression, file: file, line: line)
}
