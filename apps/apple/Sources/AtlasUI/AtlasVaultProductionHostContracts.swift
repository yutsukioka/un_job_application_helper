// Phase 2D-56 repository boundary.
import Foundation
import Synchronization

public enum AtlasPublicServiceAvailability:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case checking
    case available
    case unavailable

    public var description: String {
        switch self {
        case .checking: "checking"
        case .available: "available"
        case .unavailable: "unavailable"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasPublicJobServiceError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case invalidRequest
    case unavailable
    case invalidResponse

    public var description: String {
        switch self {
        case .invalidRequest: "invalidRequest"
        case .unavailable: "unavailable"
        case .invalidResponse: "invalidResponse"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasPublicJobSearchRequest:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public static let maximumLimit = 200

    public let query: String
    public let limit: Int
    public let offset: Int

    public init(
        query: String,
        limit: Int,
        offset: Int
    ) throws {
        guard
            (1...Self.maximumLimit).contains(limit),
            offset >= 0
        else {
            throw AtlasPublicJobServiceError.invalidRequest
        }
        self.query = query
        self.limit = limit
        self.offset = offset
    }

    public var description: String {
        "AtlasPublicJobSearchRequest(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasPublicJobSearchResult:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let jobs: [AtlasLockedPublicJob]
    public let total: Int
    public let limit: Int
    public let offset: Int

    public init(
        jobs: [AtlasLockedPublicJob],
        total: Int,
        limit: Int,
        offset: Int
    ) throws {
        guard
            total >= 0,
            total >= jobs.count,
            (1...AtlasPublicJobSearchRequest.maximumLimit).contains(limit),
            offset >= 0,
            offset <= total,
            jobs.count <= limit,
            jobs.count <= total - offset
        else {
            throw AtlasPublicJobServiceError.invalidResponse
        }
        self.jobs = jobs
        self.total = total
        self.limit = limit
        self.offset = offset
    }

    public var description: String {
        "AtlasPublicJobSearchResult(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasPublicServiceHealth:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let availability: AtlasPublicServiceAvailability
    public let openJobCount: Int?
    public let enabledSourceCount: Int?
    public let lastSyncAt: Date?

    public init(
        availability: AtlasPublicServiceAvailability,
        openJobCount: Int?,
        enabledSourceCount: Int?,
        lastSyncAt: Date?
    ) throws {
        guard
            openJobCount.map({ $0 >= 0 }) ?? true,
            enabledSourceCount.map({ $0 >= 0 }) ?? true
        else {
            throw AtlasPublicJobServiceError.invalidResponse
        }
        self.availability = availability
        self.openJobCount = openJobCount
        self.enabledSourceCount = enabledSourceCount
        self.lastSyncAt = lastSyncAt
    }

    public var description: String {
        "AtlasPublicServiceHealth(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasPublicSourceStatus:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let sourceID: String
    public let displayName: String
    public let availability: AtlasPublicServiceAvailability
    public let openJobCount: Int?

    public init(
        sourceID: String,
        displayName: String,
        availability: AtlasPublicServiceAvailability,
        openJobCount: Int?
    ) throws {
        guard
            !sourceID.isEmpty,
            !displayName.isEmpty,
            openJobCount.map({ $0 >= 0 }) ?? true
        else {
            throw AtlasPublicJobServiceError.invalidResponse
        }
        self.sourceID = sourceID
        self.displayName = displayName
        self.availability = availability
        self.openJobCount = openJobCount
    }

    public var description: String {
        "AtlasPublicSourceStatus(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasPublicUpdateStatus:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let sourceID: String
    public let observedAt: Date?
    public let fetchedJobCount: Int
    public let changedJobCount: Int
    public let closedJobCount: Int

    public init(
        sourceID: String,
        observedAt: Date?,
        fetchedJobCount: Int,
        changedJobCount: Int,
        closedJobCount: Int
    ) throws {
        guard
            !sourceID.isEmpty,
            fetchedJobCount >= 0,
            changedJobCount >= 0,
            closedJobCount >= 0
        else {
            throw AtlasPublicJobServiceError.invalidResponse
        }
        self.sourceID = sourceID
        self.observedAt = observedAt
        self.fetchedJobCount = fetchedJobCount
        self.changedJobCount = changedJobCount
        self.closedJobCount = closedJobCount
    }

    public var description: String {
        "AtlasPublicUpdateStatus(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasPublicJobReference:
    Equatable,
    Hashable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let publicJobID: String

    public init(publicJobID: String) throws {
        guard !publicJobID.isEmpty else {
            throw AtlasPublicJobServiceError.invalidRequest
        }
        self.publicJobID = publicJobID
    }

    public var description: String {
        "AtlasPublicJobReference(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasPublicJobDetailResult:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let reference: AtlasPublicJobReference
    public let job: AtlasLockedPublicJob
    public let detailText: String

    public init(
        reference: AtlasPublicJobReference,
        job: AtlasLockedPublicJob,
        detailText: String
    ) throws {
        guard reference.publicJobID == job.id else {
            throw AtlasPublicJobServiceError.invalidResponse
        }
        self.reference = reference
        self.job = job
        self.detailText = detailText
    }

    public var description: String {
        "AtlasPublicJobDetailResult(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasPublicJobSearching: Sendable {
    func health() async throws(AtlasPublicJobServiceError)
        -> AtlasPublicServiceHealth
    func search(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult
    func sources() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicSourceStatus]
    func updates() async throws(AtlasPublicJobServiceError)
        -> [AtlasPublicUpdateStatus]
    func detail(
        for reference: AtlasPublicJobReference
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobDetailResult
}

public enum AtlasPublicSnapshotRestoreError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case unavailable
    case invalidSnapshot

    public var description: String {
        switch self {
        case .unavailable: "unavailable"
        case .invalidSnapshot: "invalidSnapshot"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasProductionPublicSnapshot:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let savedAt: Date
    public let health: AtlasPublicServiceHealth
    public let jobs: [AtlasLockedPublicJob]
    public let sources: [AtlasPublicSourceStatus]
    public let updates: [AtlasPublicUpdateStatus]

    public init(
        savedAt: Date,
        health: AtlasPublicServiceHealth,
        jobs: [AtlasLockedPublicJob],
        sources: [AtlasPublicSourceStatus],
        updates: [AtlasPublicUpdateStatus]
    ) {
        self.savedAt = savedAt
        self.health = health
        self.jobs = jobs
        self.sources = sources
        self.updates = updates
    }

    public var description: String {
        "AtlasProductionPublicSnapshot(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasPublicSnapshotRestoring: Sendable {
    func restore() async throws(AtlasPublicSnapshotRestoreError)
        -> AtlasProductionPublicSnapshot?
}

public enum AtlasVaultIDSelectionError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case invalidVaultID
    case unavailable
    case invalidRegistry
    case existingSelection

    public var description: String {
        switch self {
        case .invalidVaultID:
            "invalidVaultID"
        case .unavailable:
            "unavailable"
        case .invalidRegistry:
            "invalidRegistry"
        case .existingSelection:
            "existingSelection"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasSelectedVaultID:
    Equatable,
    Hashable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let vaultID: String

    public init(validating candidate: String) throws {
        do {
            vaultID = try AtlasInjectedRootVaultPathLocator.validatedVaultID(
                candidate
            )
        } catch {
            throw AtlasVaultIDSelectionError.invalidVaultID
        }
    }

    public var description: String {
        "AtlasSelectedVaultID(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultIDSelection:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case none
    case selected(AtlasSelectedVaultID)

    public var description: String {
        switch self {
        case .none:
            "AtlasVaultIDSelection(none)"
        case .selected:
            "AtlasVaultIDSelection(selected: <redacted>)"
        }
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultIDSelecting: Sendable {
    func selectVaultID() async throws(AtlasVaultIDSelectionError)
        -> AtlasVaultIDSelection
}

public enum AtlasVaultProductionHostError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case stopped
    case presentationUnavailable

    public var description: String {
        switch self {
        case .stopped:
            "stopped"
        case .presentationUnavailable:
            "presentationUnavailable"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultProductionHostGeneration:
    Hashable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let token = UUID()

    public init() {}

    public var description: String {
        "AtlasVaultProductionHostGeneration(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultPrivateFreePresentationSnapshot:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let snapshot: AtlasVaultPresentationSnapshot

    public init(
        validating snapshot: AtlasVaultPresentationSnapshot
    ) throws(AtlasVaultProductionHostError) {
        guard snapshot.privateState == nil else {
            throw AtlasVaultProductionHostError.presentationUnavailable
        }
        self.snapshot = snapshot
    }

    public var description: String {
        "AtlasVaultPrivateFreePresentationSnapshot(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultProductionPresentationCoordinating:
    AtlasVaultPresentationObserving
{
    func start() async -> Bool
    func publish(
        _ value: AtlasVaultPrivateFreePresentationSnapshot
    ) async -> Bool
    func finish() async -> Bool
}

public protocol AtlasVaultProductionPresentationOwnerResetting: Sendable {
    /// Invalidates owner work associated with superseded host generations.
    /// A reset may establish its supplied generation and may commit only while
    /// that generation remains current after each suspension.
    @MainActor
    func supersedePresentationGeneration(
        _ generation: AtlasVaultProductionHostGeneration
    ) async

    @MainActor
    func resetPresentation(
        to state: AtlasLockedShellUnlockFlowState,
        generation: AtlasVaultProductionHostGeneration
    ) async -> Bool
}

public protocol AtlasVaultProductionHosting: Sendable {
    func start() async throws -> AtlasLockedShellUnlockFlowState
    func stop() async -> AtlasLockedShellUnlockFlowState
    func currentFlowState() async -> AtlasLockedShellUnlockFlowState
    func searchPublicJobs(
        _ request: AtlasPublicJobSearchRequest
    ) async throws(AtlasPublicJobServiceError) -> AtlasPublicJobSearchResult
    func requestUnlockPanel() async -> AtlasLockedShellUnlockFlowState
    func selectUnlockMethod(
        _ method: AtlasVaultUnlockMethod?
    ) async -> AtlasLockedShellUnlockFlowState
    func submitUnlock(
        _ submission: AtlasVaultUnlockSubmission,
        timeout: Duration?
    ) async -> AtlasLockedShellUnlockFlowState
    func cancelUnlock() async -> AtlasLockedShellUnlockFlowState
    func unlockPanelDidDisappear() async -> AtlasLockedShellUnlockFlowState
    func lock() async -> AtlasLockedShellUnlockFlowState
    func handleLifecycleEvent(
        _ event: AtlasVaultLifecycleEvent
    ) async -> AtlasLockedShellUnlockFlowState
}

public enum AtlasVaultPrivateMutationResult:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case committed
    case committedDurabilityUnconfirmed
    case failed
    case cancelled
    case locked

    public var description: String {
        switch self {
        case .committed:
            "committed"
        case .committedDurabilityUnconfirmed:
            "committedDurabilityUnconfirmed"
        case .failed:
            "failed"
        case .cancelled:
            "cancelled"
        case .locked:
            "locked"
        }
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultPrivateMutationHosting: Sendable {
    func applyPrivateMutation(
        _ request: AtlasVaultRuntimeMutationRequest
    ) async -> AtlasVaultPrivateMutationResult
}

public protocol AtlasVaultPrivateSessionBoundary: Sendable {
    func activatePrivateSession(selectedVault: String) async -> Bool

    @MainActor
    func hidePrivatePresentation()

    func stopAndDrainPrivateSession() async
}

public struct AtlasNoopVaultPrivateSessionBoundary:
    AtlasVaultPrivateSessionBoundary,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public init() {}

    public func activatePrivateSession(selectedVault: String) async -> Bool {
        true
    }

    @MainActor
    public func hidePrivatePresentation() {}

    public func stopAndDrainPrivateSession() async {}

    public var description: String {
        "AtlasNoopVaultPrivateSessionBoundary(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public final class AtlasVaultPrivateSessionBoundaryBridge:
    AtlasVaultPrivateSessionBoundary,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let target = Mutex<
        (any AtlasVaultPrivateSessionBoundary)?
    >(nil)

    public init() {}

    @discardableResult
    public func attach(
        _ candidate: any AtlasVaultPrivateSessionBoundary
    ) -> Bool {
        target.withLock { target in
            guard target == nil else {
                return false
            }
            target = candidate
            return true
        }
    }

    public func activatePrivateSession(selectedVault: String) async -> Bool {
        guard let target = target.withLock({ $0 }) else {
            return false
        }
        return await target.activatePrivateSession(
            selectedVault: selectedVault
        )
    }

    @MainActor
    public func hidePrivatePresentation() {
        target.withLock { $0 }?.hidePrivatePresentation()
    }

    public func stopAndDrainPrivateSession() async {
        guard let target = target.withLock({ $0 }) else {
            return
        }
        await target.stopAndDrainPrivateSession()
    }

    public var description: String {
        "AtlasVaultPrivateSessionBoundaryBridge(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultUnlockPresentationControllerBuilding: Sendable {
    func makeController(
        selectedVaultID: AtlasSelectedVaultID,
        capabilities: AtlasVaultUnlockCapabilities,
        coordinator: any AtlasVaultUnlockRequestCoordinating
    ) -> any AtlasVaultUnlockPresentationControlling
}

public struct AtlasVaultProductionHostDependencies:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let publicJobs: any AtlasPublicJobSearching
    public let publicSnapshotRestorer: any AtlasPublicSnapshotRestoring
    public let vaultIDSelector: any AtlasVaultIDSelecting
    public let runtime: any AtlasVaultRuntimeFacading
    public let lifecycle: any AtlasVaultLifecycleCoordinating
    public let presentation:
        any AtlasVaultProductionPresentationCoordinating
    public let presentationOwner:
        any AtlasVaultProductionPresentationOwnerResetting
    public let unlockCoordinator: any AtlasVaultUnlockRequestCoordinating
    public let unlockControllerBuilder:
        any AtlasVaultUnlockPresentationControllerBuilding
    public let unlockCapabilitiesResolver:
        any AtlasVaultUnlockCapabilitiesResolving
    public let privateSessionBoundary:
        any AtlasVaultPrivateSessionBoundary

    public init(
        publicJobs: any AtlasPublicJobSearching,
        publicSnapshotRestorer: any AtlasPublicSnapshotRestoring,
        vaultIDSelector: any AtlasVaultIDSelecting,
        runtime: any AtlasVaultRuntimeFacading,
        lifecycle: any AtlasVaultLifecycleCoordinating,
        presentation: any AtlasVaultProductionPresentationCoordinating,
        presentationOwner:
            any AtlasVaultProductionPresentationOwnerResetting,
        unlockCoordinator: any AtlasVaultUnlockRequestCoordinating,
        unlockControllerBuilder:
            any AtlasVaultUnlockPresentationControllerBuilding,
        unlockCapabilitiesResolver:
            any AtlasVaultUnlockCapabilitiesResolving =
                AtlasFixedVaultUnlockCapabilitiesResolver(
                    capabilities: .currentProduction
                ),
        privateSessionBoundary:
            any AtlasVaultPrivateSessionBoundary =
                AtlasNoopVaultPrivateSessionBoundary()
    ) {
        self.publicJobs = publicJobs
        self.publicSnapshotRestorer = publicSnapshotRestorer
        self.vaultIDSelector = vaultIDSelector
        self.runtime = runtime
        self.lifecycle = lifecycle
        self.presentation = presentation
        self.presentationOwner = presentationOwner
        self.unlockCoordinator = unlockCoordinator
        self.unlockControllerBuilder = unlockControllerBuilder
        self.unlockCapabilitiesResolver = unlockCapabilitiesResolver
        self.privateSessionBoundary = privateSessionBoundary
    }

    public var description: String {
        "AtlasVaultProductionHostDependencies(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}
