import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultUnlockPresentationControllerTests: XCTestCase {
    fileprivate static let vaultID = "00000000-0000-4000-8000-000000000550"
    fileprivate static let fakePassphrase = Data("FAKE_PRESENTATION_PASSPHRASE".utf8)
    fileprivate static let fakeRecoveryKey = Data("FAKE_PRESENTATION_RECOVERY_KEY".utf8)
    fileprivate static let fakeVaultKey = Data(repeating: 0x55, count: 32)
    private static let privateSentinel = "FAKE_PRESENTATION_PRIVATE_SENTINEL"

    func testConstructionIsSideEffectFreeAndProjectsExactCapabilities() async {
        let coordinator = ControlledUnlockCoordinator(mode: .immediateSuccess)
        let provider = PresentationNeverCalledUnwrapper()
        let capabilities = AtlasVaultUnlockCapabilities(
            localKeyAvailable: true,
            passphraseProvider: provider,
            recoveryKeyProvider: nil
        )

        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )

        let state = await controller.currentState()
        let dispatchCount = await coordinator.dispatchCount
        let cancelCount = await coordinator.cancelCount
        let providerCalls = await provider.callCount
        XCTAssertEqual(state.capabilities, capabilities)
        XCTAssertEqual(state.selectedMethod, nil)
        XCTAssertEqual(state.status, .locked)
        XCTAssertEqual(dispatchCount, 0)
        XCTAssertEqual(cancelCount, 0)
        XCTAssertEqual(providerCalls, 0)
    }

    func testUnavailableSelectionAndSubmissionRejectBeforeSecretConsumption() async {
        let coordinator = ControlledUnlockCoordinator(mode: .immediateSuccess)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: .currentProduction,
            coordinator: coordinator
        )
        let buffer = TrackingPresentationSecretBuffer(bytes: Self.fakePassphrase)

        let selected = await controller.select(.passphrase)
        let submitted = await controller.submit(.passphrase(buffer))

        let bufferState = await buffer.snapshot()
        let dispatchCount = await coordinator.dispatchCount
        XCTAssertEqual(selected.selectedMethod, nil)
        XCTAssertEqual(selected.status, .methodUnavailable)
        XCTAssertEqual(submitted.status, .methodUnavailable)
        XCTAssertEqual(bufferState.takeCount, 0)
        XCTAssertEqual(bufferState.clearCount, 1)
        XCTAssertTrue(bufferState.isCleared)
        XCTAssertEqual(dispatchCount, 0)
    }

    func testMismatchedSubmissionRejectsBeforeSecretConsumption() async {
        let coordinator = ControlledUnlockCoordinator(mode: .immediateSuccess)
        let capabilities = fakeCapabilities(passphrase: true, recovery: true)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
        let buffer = TrackingPresentationSecretBuffer(bytes: Self.fakeRecoveryKey)
        _ = await controller.select(.passphrase)

        let state = await controller.submit(.recoveryKey(buffer))

        let bufferState = await buffer.snapshot()
        let dispatchCount = await coordinator.dispatchCount
        XCTAssertEqual(state.status, .failed)
        XCTAssertEqual(state.selectedMethod, .passphrase)
        XCTAssertEqual(bufferState.takeCount, 0)
        XCTAssertEqual(bufferState.clearCount, 1)
        XCTAssertEqual(dispatchCount, 0)
    }

    func testLocalKeyDispatchUsesExistingCoordinatorRequest() async {
        let spy = PresentationDispatchSpy()
        let coordinator = functionalCoordinator(spy: spy)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: .currentProduction,
            coordinator: coordinator
        )
        _ = await controller.select(.localKey)

        let state = await controller.submit(.localKey)

        let snapshot = await spy.snapshot()
        XCTAssertEqual(state.status, .unlocked)
        XCTAssertEqual(snapshot.localActivationCount, 1)
        XCTAssertEqual(snapshot.passphraseDerivationCount, 0)
        XCTAssertEqual(snapshot.recoveryDerivationCount, 0)
        XCTAssertTrue(snapshot.vaultIDMatched)
    }

    func testFakePassphraseAndRecoveryDispatchOnlyWhenExplicitlyAvailable() async {
        let spy = PresentationDispatchSpy()
        let provider = PresentationNeverCalledUnwrapper()
        let capabilities = AtlasVaultUnlockCapabilities(
            localKeyAvailable: false,
            passphraseProvider: provider,
            recoveryKeyProvider: provider
        )
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: functionalCoordinator(spy: spy)
        )
        let passphrase = TrackingPresentationSecretBuffer(bytes: Self.fakePassphrase)
        let recovery = TrackingPresentationSecretBuffer(bytes: Self.fakeRecoveryKey)

        _ = await controller.select(.passphrase)
        let passphraseState = await controller.submit(.passphrase(passphrase))
        _ = await controller.hostDidLock()
        _ = await controller.select(.recoveryKey)
        let recoveryState = await controller.submit(.recoveryKey(recovery))

        let dispatch = await spy.snapshot()
        let passphraseBuffer = await passphrase.snapshot()
        let recoveryBuffer = await recovery.snapshot()
        let providerCalls = await provider.callCount
        XCTAssertEqual(passphraseState.status, .unlocked)
        XCTAssertEqual(recoveryState.status, .unlocked)
        XCTAssertEqual(dispatch.passphraseDerivationCount, 1)
        XCTAssertEqual(dispatch.recoveryDerivationCount, 1)
        XCTAssertEqual(dispatch.keyedActivationCount, 2)
        XCTAssertTrue(dispatch.passphraseMatched)
        XCTAssertTrue(dispatch.recoveryMatched)
        XCTAssertEqual(passphraseBuffer.takeCount, 1)
        XCTAssertEqual(recoveryBuffer.takeCount, 1)
        XCTAssertTrue(passphraseBuffer.isCleared)
        XCTAssertTrue(recoveryBuffer.isCleared)
        XCTAssertEqual(providerCalls, 0)
    }

    func testOneInFlightRequestRejectsDuplicateAndClearsRejectedSecret() async {
        let coordinator = ControlledUnlockCoordinator(mode: .gated(cancelSucceeds: true))
        let capabilities = fakeCapabilities(passphrase: true, recovery: true)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
        let firstBuffer = TrackingPresentationSecretBuffer(bytes: Self.fakePassphrase)
        let duplicateBuffer = TrackingPresentationSecretBuffer(bytes: Self.fakeRecoveryKey)
        _ = await controller.select(.passphrase)

        let first = Task {
            await controller.submit(.passphrase(firstBuffer))
        }
        let didDispatch = await waitForDispatch(coordinator)
        XCTAssertTrue(didDispatch)

        let duplicateState = await controller.submit(.recoveryKey(duplicateBuffer))

        let duplicateSnapshot = await duplicateBuffer.snapshot()
        let dispatchCount = await coordinator.dispatchCount
        XCTAssertEqual(duplicateState.status, .activating)
        XCTAssertEqual(duplicateSnapshot.takeCount, 0)
        XCTAssertEqual(duplicateSnapshot.clearCount, 1)
        XCTAssertEqual(dispatchCount, 1)

        await coordinator.resolve(.success)
        let finalState = await first.value
        XCTAssertEqual(finalState.status, .unlocked)
        let firstSnapshot = await firstBuffer.snapshot()
        XCTAssertTrue(firstSnapshot.isCleared)
    }

    func testRejectedSubmissionCannotDowngradeUnlockedState() async {
        let coordinator = ControlledUnlockCoordinator(mode: .immediateSuccess)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: .currentProduction,
            coordinator: coordinator
        )
        _ = await controller.select(.localKey)
        let unlocked = await controller.submit(.localKey)
        let rejectedBuffer = TrackingPresentationSecretBuffer(
            bytes: Self.fakePassphrase
        )

        let retained = await controller.submit(.passphrase(rejectedBuffer))

        let rejectedSnapshot = await rejectedBuffer.snapshot()
        let dispatchCount = await coordinator.dispatchCount
        XCTAssertEqual(unlocked.status, .unlocked)
        XCTAssertEqual(retained.status, .unlocked)
        XCTAssertEqual(retained.selectedMethod, .localKey)
        XCTAssertEqual(rejectedSnapshot.takeCount, 0)
        XCTAssertTrue(rejectedSnapshot.isCleared)
        XCTAssertEqual(dispatchCount, 1)
    }

    func testGenericCoordinatorFailureDoesNotExposeFailureCause() async {
        let coordinator = ControlledUnlockCoordinator(
            mode: .immediateFailure(.unlockFailed)
        )
        let capabilities = fakeCapabilities(passphrase: true, recovery: false)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
        let buffer = TrackingPresentationSecretBuffer(
            bytes: Data(Self.privateSentinel.utf8)
        )
        _ = await controller.select(.passphrase)

        let state = await controller.submit(.passphrase(buffer))

        XCTAssertEqual(state.status, .failed)
        XCTAssertFalse(state.description.contains(Self.privateSentinel))
        XCTAssertFalse(state.debugDescription.contains(Self.privateSentinel))
        let snapshot = await buffer.snapshot()
        XCTAssertTrue(snapshot.isCleared)
    }

    func testCoordinatorTimeoutInvalidatesPresentationAuthorization() async {
        let spy = PresentationDispatchSpy()
        let capabilities = fakeCapabilities(passphrase: true, recovery: false)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: functionalCoordinator(spy: spy)
        )
        let buffer = TrackingPresentationSecretBuffer(bytes: Self.fakePassphrase)
        _ = await controller.select(.passphrase)

        let state = await controller.submit(.passphrase(buffer), timeout: .zero)

        let dispatch = await spy.snapshot()
        let bufferState = await buffer.snapshot()
        XCTAssertEqual(state.status, .timedOut)
        XCTAssertEqual(dispatch.localActivationCount, 0)
        XCTAssertEqual(dispatch.keyedActivationCount, 0)
        XCTAssertEqual(bufferState.takeCount, 0)
        XCTAssertTrue(bufferState.isCleared)
    }

    func testCancellationInvalidatesLateCompletion() async {
        let coordinator = ControlledUnlockCoordinator(mode: .gated(cancelSucceeds: true))
        let capabilities = fakeCapabilities(passphrase: true, recovery: false)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
        let buffer = TrackingPresentationSecretBuffer(bytes: Self.fakePassphrase)
        _ = await controller.select(.passphrase)

        let submission = Task {
            await controller.submit(.passphrase(buffer))
        }
        let didDispatch = await waitForDispatch(coordinator)
        XCTAssertTrue(didDispatch)

        let cancelled = await controller.cancel()
        let terminal = await submission.value
        let retained = await controller.currentState()

        XCTAssertEqual(cancelled.status, .cancelled)
        XCTAssertEqual(terminal.status, .cancelled)
        XCTAssertEqual(retained.status, .cancelled)
        XCTAssertNotEqual(retained.status, .unlocked)
        let bufferState = await buffer.snapshot()
        XCTAssertTrue(bufferState.isCleared)
    }

    func testCancellationLosingToCommittedSuccessRequiresHostReconciliation() async {
        let coordinator = ControlledUnlockCoordinator(mode: .gated(cancelSucceeds: false))
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: .currentProduction,
            coordinator: coordinator
        )
        _ = await controller.select(.localKey)

        let submission = Task {
            await controller.submit(.localKey)
        }
        let didDispatch = await waitForDispatch(coordinator)
        XCTAssertTrue(didDispatch)

        let cancelled = await controller.cancel()
        XCTAssertEqual(cancelled.status, .hostReconciliationRequired)

        await coordinator.resolve(.success)
        let staleCompletion = await submission.value
        let retained = await controller.currentState()
        XCTAssertEqual(staleCompletion.status, .hostReconciliationRequired)
        XCTAssertEqual(retained.status, .hostReconciliationRequired)
        XCTAssertNotEqual(retained.status, .unlocked)
    }

    func testHostReconciliationSurvivesRepeatedCancelAndDisappearance() async {
        let coordinator = ControlledUnlockCoordinator(mode: .gated(cancelSucceeds: false))
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: .currentProduction,
            coordinator: coordinator
        )
        _ = await controller.select(.localKey)
        let submission = Task {
            await controller.submit(.localKey)
        }
        let didDispatch = await waitForDispatch(coordinator)
        XCTAssertTrue(didDispatch)
        let reconciliation = await controller.cancel()
        XCTAssertEqual(reconciliation.status, .hostReconciliationRequired)

        let repeatedCancel = await controller.cancel()
        let disappeared = await controller.didDisappear()

        XCTAssertEqual(repeatedCancel.status, .hostReconciliationRequired)
        XCTAssertEqual(disappeared.status, .hostReconciliationRequired)

        await coordinator.resolve(.success)
        let terminal = await submission.value
        XCTAssertEqual(terminal.status, .hostReconciliationRequired)
    }

    func testMethodChangeCancelsAttemptAndPreservesNewSelection() async {
        let coordinator = ControlledUnlockCoordinator(mode: .gated(cancelSucceeds: true))
        let capabilities = fakeCapabilities(passphrase: true, recovery: true)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
        let buffer = TrackingPresentationSecretBuffer(bytes: Self.fakePassphrase)
        _ = await controller.select(.passphrase)
        let submission = Task {
            await controller.submit(.passphrase(buffer))
        }
        let didDispatch = await waitForDispatch(coordinator)
        XCTAssertTrue(didDispatch)

        let changed = await controller.select(.recoveryKey)
        let terminal = await submission.value

        XCTAssertEqual(changed.status, .ready)
        XCTAssertEqual(changed.selectedMethod, .recoveryKey)
        XCTAssertEqual(terminal, changed)
        let bufferSnapshot = await buffer.snapshot()
        XCTAssertTrue(bufferSnapshot.isCleared)
    }

    func testDisappearanceInvalidatesAttemptWithoutPublishingUnlocked() async {
        let coordinator = ControlledUnlockCoordinator(mode: .gated(cancelSucceeds: false))
        let capabilities = fakeCapabilities(passphrase: true, recovery: false)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
        let buffer = TrackingPresentationSecretBuffer(bytes: Self.fakePassphrase)
        _ = await controller.select(.passphrase)
        let submission = Task {
            await controller.submit(.passphrase(buffer))
        }
        let didDispatch = await waitForDispatch(coordinator)
        XCTAssertTrue(didDispatch)

        let disappeared = await controller.didDisappear()
        await coordinator.resolve(.success)
        let terminal = await submission.value

        XCTAssertEqual(disappeared.status, .hostReconciliationRequired)
        XCTAssertEqual(terminal.status, .hostReconciliationRequired)
        XCTAssertNil(terminal.selectedMethod)
        let bufferState = await buffer.snapshot()
        XCTAssertTrue(bufferState.isCleared)
    }

    func testHostLockInvalidatesAttemptAndOverridesLateSuccess() async {
        let coordinator = ControlledUnlockCoordinator(mode: .gated(cancelSucceeds: false))
        let capabilities = fakeCapabilities(passphrase: true, recovery: false)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
        let buffer = TrackingPresentationSecretBuffer(bytes: Self.fakePassphrase)
        _ = await controller.select(.passphrase)
        let submission = Task {
            await controller.submit(.passphrase(buffer))
        }
        let didDispatch = await waitForDispatch(coordinator)
        XCTAssertTrue(didDispatch)

        let locked = await controller.hostDidLock()
        await coordinator.resolve(.success)
        let terminal = await submission.value

        XCTAssertEqual(locked.status, .locked)
        XCTAssertEqual(terminal.status, .locked)
        XCTAssertNil(terminal.selectedMethod)
        let bufferState = await buffer.snapshot()
        XCTAssertTrue(bufferState.isCleared)
    }

    func testControllerReleasesRequestAndSecretOwnershipAfterCompletion() async {
        let coordinator = ControlledUnlockCoordinator(mode: .immediateSuccess)
        let capabilities = fakeCapabilities(passphrase: true, recovery: false)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: capabilities,
            coordinator: coordinator
        )
        _ = await controller.select(.passphrase)
        var buffer: TrackingPresentationSecretBuffer? = TrackingPresentationSecretBuffer(
            bytes: Self.fakePassphrase
        )
        weak let weakBuffer = buffer

        let state = await controller.submit(.passphrase(buffer!))
        buffer = nil
        for _ in 0..<100 where weakBuffer != nil {
            await Task.yield()
        }

        XCTAssertEqual(state.status, .unlocked)
        XCTAssertNil(weakBuffer)
    }

    func testPublicTypesAndDescriptionsAreSanitizedAndNonCodable() async {
        let coordinator = ControlledUnlockCoordinator(mode: .immediateSuccess)
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: .currentProduction,
            coordinator: coordinator
        )
        let buffer = TrackingPresentationSecretBuffer(
            bytes: Data(Self.privateSentinel.utf8)
        )
        let submission = AtlasVaultUnlockSubmission.passphrase(buffer)
        let state = await controller.currentState()
        let descriptions = [
            state.description,
            state.debugDescription,
            state.status.description,
            state.status.debugDescription,
            submission.description,
            submission.debugDescription,
            controller.description,
            controller.debugDescription,
        ]

        for description in descriptions {
            XCTAssertFalse(description.contains(Self.privateSentinel))
            XCTAssertFalse(description.contains(Self.vaultID))
            XCTAssertFalse(description.contains("/"))
        }
        XCTAssertFalse(AtlasVaultUnlockPresentationState.self is any Encodable.Type)
        XCTAssertFalse(AtlasVaultUnlockPresentationState.self is any Decodable.Type)
        XCTAssertFalse(AtlasVaultUnlockSubmission.self is any Encodable.Type)
        XCTAssertFalse(AtlasVaultUnlockSubmission.self is any Decodable.Type)
        await buffer.clear()
    }

    func testSourceHasNoRawKeyOrForbiddenRuntimeDependencies() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)
        let forbidden = [
            "SwiftUI",
            "ObservableObject",
            "@Published",
            "@State",
            "@Environment",
            "@AppStorage",
            "@SceneStorage",
            "UserDefaults",
            "FileManager",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "URLSession",
            "Data.write",
            "createFile",
            "AtlasVaultRuntimeFacade",
            "AtlasVaultRuntimeActivationRequest",
            "suppliedVaultKey",
            "suppliedTestVaultKey",
            "rawKey",
            "AtlasAPIClient",
            "SearchViewModel",
            "AtlasLocalCache",
            "refreshSidebarData",
            "/api/saved-searches",
            "/api/tracker",
            "AtlasVaultPresentationSnapshot",
            "AtlasVaultMutation",
            "AtlasVaultSave",
        ]

        for token in forbidden {
            XCTAssertFalse(source.contains(token), "Unexpected production dependency: \(token)")
        }
        XCTAssertTrue(source.contains("case localKey"))
        XCTAssertTrue(source.contains("case passphrase"))
        XCTAssertTrue(source.contains("case recoveryKey"))
    }

    func testControllerCannotMutatePublicSnapshotOrCreateVaultArtifacts() async throws {
        let publicSnapshot = AtlasVaultPresentationSnapshot(
            status: .locked,
            privateState: nil
        )
        let controller = AtlasVaultUnlockPresentationController(
            vaultID: Self.vaultID,
            capabilities: .currentProduction,
            coordinator: ControlledUnlockCoordinator(mode: .immediateSuccess)
        )
        _ = await controller.select(.localKey)
        _ = await controller.submit(.localKey)

        XCTAssertEqual(
            publicSnapshot,
            AtlasVaultPresentationSnapshot(status: .locked, privateState: nil)
        )

        let repositoryRoot = sourceURL()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let enumerator = try XCTUnwrap(
            FileManager.default.enumerator(
                at: repositoryRoot,
                includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            )
        )
        var artifacts: [String] = []
        while let url = enumerator.nextObject() as? URL {
            if url.pathExtension == "atlasvault" {
                artifacts.append(url.path)
            }
        }
        XCTAssertEqual(artifacts, [])
    }

    private func fakeCapabilities(
        passphrase: Bool,
        recovery: Bool
    ) -> AtlasVaultUnlockCapabilities {
        AtlasVaultUnlockCapabilities(
            localKeyAvailable: true,
            passphraseProvider: passphrase ? PresentationNeverCalledUnwrapper() : nil,
            recoveryKeyProvider: recovery ? PresentationNeverCalledUnwrapper() : nil
        )
    }

    private func functionalCoordinator(
        spy: PresentationDispatchSpy
    ) -> AtlasVaultUnlockRequestCoordinator {
        AtlasVaultUnlockRequestCoordinator(
            dependencies: AtlasVaultUnlockRequestDependencies(
                derivePassphraseVaultKey: { secret in
                    await spy.derivePassphrase(secret)
                },
                deriveRecoveryVaultKey: { secret in
                    await spy.deriveRecovery(secret)
                },
                activate: { request in
                    await spy.activate(request)
                }
            )
        )
    }

    private func waitForDispatch(
        _ coordinator: ControlledUnlockCoordinator
    ) async -> Bool {
        for _ in 0..<1_000 {
            if await coordinator.dispatchCount > 0 {
                return true
            }
            await Task.yield()
        }
        return false
    }

    private func sourceURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent(
                "Sources/AtlasUI/AtlasVaultUnlockPresentationController.swift"
            )
    }
}

private struct PresentationSecretSnapshot: Equatable, Sendable {
    let takeCount: Int
    let clearCount: Int
    let isCleared: Bool
}

private actor TrackingPresentationSecretBuffer: AtlasVaultSecretBuffer {
    private var bytes: [UInt8]?
    private(set) var takeCount = 0
    private(set) var clearCount = 0

    init(bytes: Data) {
        self.bytes = Array(bytes)
    }

    deinit {
        guard var bytes else { return }
        self.bytes = nil
        Self.wipe(&bytes)
    }

    func takeSecretBytes() async throws -> Data {
        guard var bytes else {
            throw AtlasVaultSecretBufferError.unavailable
        }
        self.bytes = nil
        takeCount += 1
        let result = Data(bytes)
        Self.wipe(&bytes)
        return result
    }

    func clear() async {
        clearCount += 1
        guard var bytes else { return }
        self.bytes = nil
        Self.wipe(&bytes)
    }

    func snapshot() -> PresentationSecretSnapshot {
        PresentationSecretSnapshot(
            takeCount: takeCount,
            clearCount: clearCount,
            isCleared: bytes == nil
        )
    }

    private static func wipe(_ value: inout [UInt8]) {
        for index in value.indices {
            value[index] = 0
        }
        value.removeAll(keepingCapacity: false)
    }
}

private actor PresentationNeverCalledUnwrapper: AtlasVaultKeyUnwrapping {
    private(set) var callCount = 0

    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        callCount += 1
        throw AtlasVaultKeyUnwrapError.providerUnavailable
    }
}

private struct PresentationDispatchSnapshot: Equatable, Sendable {
    var passphraseDerivationCount = 0
    var recoveryDerivationCount = 0
    var localActivationCount = 0
    var keyedActivationCount = 0
    var passphraseMatched = false
    var recoveryMatched = false
    var vaultIDMatched = false
}

private actor PresentationDispatchSpy {
    private var value = PresentationDispatchSnapshot()

    func derivePassphrase(_ secret: Data) -> Data {
        value.passphraseDerivationCount += 1
        value.passphraseMatched =
            secret == AtlasVaultUnlockPresentationControllerTests.fakePassphrase
        return AtlasVaultUnlockPresentationControllerTests.fakeVaultKey
    }

    func deriveRecovery(_ secret: Data) -> Data {
        value.recoveryDerivationCount += 1
        value.recoveryMatched =
            secret == AtlasVaultUnlockPresentationControllerTests.fakeRecoveryKey
        return AtlasVaultUnlockPresentationControllerTests.fakeVaultKey
    }

    func activate(_ request: AtlasVaultRuntimeActivationRequest) {
        value.vaultIDMatched =
            request.vaultID == AtlasVaultUnlockPresentationControllerTests.vaultID
        if let key = request.suppliedVaultKeyValue {
            value.keyedActivationCount += 1
            precondition(key == AtlasVaultUnlockPresentationControllerTests.fakeVaultKey)
        } else {
            value.localActivationCount += 1
        }
    }

    func snapshot() -> PresentationDispatchSnapshot {
        value
    }
}

private actor ControlledUnlockCoordinator: AtlasVaultUnlockRequestCoordinating {
    enum Resolution: Sendable {
        case success
        case failure(AtlasVaultUnlockRequestError)
    }

    enum Mode: Sendable {
        case immediateSuccess
        case immediateFailure(AtlasVaultUnlockRequestError)
        case gated(cancelSucceeds: Bool)
    }

    private let mode: Mode
    private var continuation: CheckedContinuation<Void, Error>?
    private(set) var dispatchCount = 0
    private(set) var cancelCount = 0

    init(mode: Mode) {
        self.mode = mode
    }

    func dispatch(_ request: AtlasVaultUnlockRequest) async throws {
        dispatchCount += 1
        switch mode {
        case .immediateSuccess:
            return
        case let .immediateFailure(error):
            throw error
        case .gated:
            try await withCheckedThrowingContinuation { continuation in
                self.continuation = continuation
            }
        }
    }

    func cancel(_ request: AtlasVaultUnlockRequest) async -> Bool {
        cancelCount += 1
        guard case let .gated(cancelSucceeds) = mode else {
            return false
        }
        guard cancelSucceeds else {
            return false
        }
        continuation?.resume(throwing: AtlasVaultUnlockRequestError.cancelled)
        continuation = nil
        return true
    }

    func resolve(_ resolution: Resolution) {
        guard let continuation else { return }
        self.continuation = nil
        switch resolution {
        case .success:
            continuation.resume()
        case let .failure(error):
            continuation.resume(throwing: error)
        }
    }
}
