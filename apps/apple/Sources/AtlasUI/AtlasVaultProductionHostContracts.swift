import Foundation

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

    public var description: String {
        "invalidVaultID"
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
    func lock() async -> AtlasLockedShellUnlockFlowState
    func handleLifecycleEvent(
        _ event: AtlasVaultLifecycleEvent
    ) async -> AtlasLockedShellUnlockFlowState
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
    public let presentation: any AtlasVaultPresentationObserving
    public let unlockCoordinator: any AtlasVaultUnlockRequestCoordinating
    public let unlockControllerBuilder:
        any AtlasVaultUnlockPresentationControllerBuilding

    public init(
        publicJobs: any AtlasPublicJobSearching,
        publicSnapshotRestorer: any AtlasPublicSnapshotRestoring,
        vaultIDSelector: any AtlasVaultIDSelecting,
        runtime: any AtlasVaultRuntimeFacading,
        lifecycle: any AtlasVaultLifecycleCoordinating,
        presentation: any AtlasVaultPresentationObserving,
        unlockCoordinator: any AtlasVaultUnlockRequestCoordinating,
        unlockControllerBuilder:
            any AtlasVaultUnlockPresentationControllerBuilding
    ) {
        self.publicJobs = publicJobs
        self.publicSnapshotRestorer = publicSnapshotRestorer
        self.vaultIDSelector = vaultIDSelector
        self.runtime = runtime
        self.lifecycle = lifecycle
        self.presentation = presentation
        self.unlockCoordinator = unlockCoordinator
        self.unlockControllerBuilder = unlockControllerBuilder
    }

    public var description: String {
        "AtlasVaultProductionHostDependencies(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}
