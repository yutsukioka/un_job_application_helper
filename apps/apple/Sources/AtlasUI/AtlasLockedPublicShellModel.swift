import Foundation

public enum AtlasLockedPublicVaultStatus:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case locked
    case noVault
    case keyUnavailable

    public var description: String {
        switch self {
        case .locked:
            "locked"
        case .noVault:
            "noVault"
        case .keyUnavailable:
            "keyUnavailable"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasLockedPublicServiceStatus:
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
        case .checking:
            "checking"
        case .available:
            "available"
        case .unavailable:
            "unavailable"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasLockedPublicCacheFreshness:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case unavailable
    case current
    case stale

    public var description: String {
        switch self {
        case .unavailable:
            "unavailable"
        case .current:
            "current"
        case .stale:
            "stale"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasLockedPublicJob:
    Identifiable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let id: String
    public let title: String
    public let organization: String
    public let location: String
    public let closingDateText: String?

    public init(
        id: String,
        title: String,
        organization: String,
        location: String,
        closingDateText: String? = nil
    ) {
        self.id = id
        self.title = title
        self.organization = organization
        self.location = location
        self.closingDateText = closingDateText
    }

    public var description: String {
        "AtlasLockedPublicJob(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasLockedPublicShellModel:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let vaultStatus: AtlasLockedPublicVaultStatus
    public let serviceStatus: AtlasLockedPublicServiceStatus
    public let cacheFreshness: AtlasLockedPublicCacheFreshness
    public let searchQuery: String
    public let publicJobs: [AtlasLockedPublicJob]
    public let isSearching: Bool
    public let canRequestUnlock: Bool
    public let searchOrigin: AtlasPublicJobSearchOrigin
    public let hasAdditionalCriteria: Bool

    public init(
        vaultStatus: AtlasLockedPublicVaultStatus = .locked,
        serviceStatus: AtlasLockedPublicServiceStatus = .checking,
        cacheFreshness: AtlasLockedPublicCacheFreshness = .unavailable,
        searchQuery: String = "",
        publicJobs: [AtlasLockedPublicJob] = [],
        isSearching: Bool = false,
        canRequestUnlock: Bool = true,
        searchOrigin: AtlasPublicJobSearchOrigin = .manual,
        hasAdditionalCriteria: Bool = false
    ) {
        self.vaultStatus = vaultStatus
        self.serviceStatus = serviceStatus
        self.cacheFreshness = cacheFreshness
        self.searchQuery = searchQuery
        self.publicJobs = publicJobs
        self.isSearching = isSearching
        self.canRequestUnlock = canRequestUnlock
        self.searchOrigin = searchOrigin
        self.hasAdditionalCriteria = hasAdditionalCriteria
    }

    public var description: String {
        "AtlasLockedPublicShellModel(status: \(vaultStatus), content: <redacted>)"
    }

    public var debugDescription: String {
        description
    }

    var permitsSearchSubmission: Bool {
        !isSearching
    }
}

public struct AtlasLockedPublicShellActions:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let searchAction: @Sendable (String) async -> Void
    private let unlockAction: @Sendable () async -> Void

    public init(
        search: @escaping @Sendable (String) async -> Void,
        requestUnlock: @escaping @Sendable () async -> Void
    ) {
        self.searchAction = search
        self.unlockAction = requestUnlock
    }

    public func search(query: String) async {
        await searchAction(query)
    }

    public func requestUnlock() async {
        await unlockAction()
    }

    public var description: String {
        "AtlasLockedPublicShellActions(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}
