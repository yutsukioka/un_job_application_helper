import Foundation

public protocol AtlasVaultPresentationAdapting: Sendable {
    func makeSnapshot(
        runtimeStatus: AtlasVaultRuntimeStatus,
        privateState: AtlasVaultHydratedState?,
        generation: AtlasVaultPresentationGeneration?,
        commandState: AtlasVaultPresentationCommandState
    ) -> AtlasVaultPresentationSnapshot
}

public extension AtlasVaultPresentationAdapting {
    func makeSnapshot(
        runtimeStatus: AtlasVaultRuntimeStatus,
        privateState: AtlasVaultHydratedState?,
        generation: AtlasVaultPresentationGeneration?
    ) -> AtlasVaultPresentationSnapshot {
        makeSnapshot(
            runtimeStatus: runtimeStatus,
            privateState: privateState,
            generation: generation,
            commandState: .none
        )
    }

    func makeSnapshot(
        runtimeStatus: AtlasVaultRuntimeStatus
    ) -> AtlasVaultPresentationSnapshot {
        makeSnapshot(
            runtimeStatus: runtimeStatus,
            privateState: nil,
            generation: nil,
            commandState: .none
        )
    }
}

public enum AtlasVaultPresentationCommandState:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case none
    case cancelled
    case saveFailed

    public var description: String {
        switch self {
        case .none: "none"
        case .cancelled: "cancelled"
        case .saveFailed: "saveFailed"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultPresentationStatus:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case locked
    case noVault
    case activating
    case locking
    case unlocked
    case keyUnavailable
    case corruptStore
    case unsupportedVersion
    case saveInProgress
    case saveFailed
    case cancelled
    case failed

    public var description: String {
        switch self {
        case .locked: "locked"
        case .noVault: "noVault"
        case .activating: "activating"
        case .locking: "locking"
        case .unlocked: "unlocked"
        case .keyUnavailable: "keyUnavailable"
        case .corruptStore: "corruptStore"
        case .unsupportedVersion: "unsupportedVersion"
        case .saveInProgress: "saveInProgress"
        case .saveFailed: "saveFailed"
        case .cancelled: "cancelled"
        case .failed: "failed"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultPresentationGeneration:
    Hashable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let token = UUID()

    public init() {}

    public var description: String {
        "AtlasVaultPresentationGeneration(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultPresentationID:
    Hashable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let processLocalToken: Int

    init(
        recordID: String,
        generation: AtlasVaultPresentationGeneration
    ) {
        var hasher = Hasher()
        hasher.combine(generation)
        hasher.combine(recordID)
        self.processLocalToken = hasher.finalize()
    }

    public var description: String {
        "AtlasVaultPresentationID(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultPresentationSnapshot:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let status: AtlasVaultPresentationStatus
    public let privateState: AtlasVaultPrivatePresentationState?

    public init(
        status: AtlasVaultPresentationStatus,
        privateState: AtlasVaultPrivatePresentationState?
    ) {
        self.status = status
        self.privateState = privateState
    }

    public var description: String {
        "AtlasVaultPresentationSnapshot(status: \(status), private: <redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultPrivatePresentationState:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let savedSearches: [AtlasVaultSavedSearchPresentation]
    public let savedJobs: [AtlasVaultSavedJobPresentation]
    public let applicationNotes: [AtlasVaultApplicationNotePresentation]
    public let profileSnippets: [AtlasVaultProfileSnippetPresentation]
    public let draftMetadata: [AtlasVaultDraftMetadataPresentation]

    init(
        savedSearches: [AtlasVaultSavedSearchPresentation],
        savedJobs: [AtlasVaultSavedJobPresentation],
        applicationNotes: [AtlasVaultApplicationNotePresentation],
        profileSnippets: [AtlasVaultProfileSnippetPresentation],
        draftMetadata: [AtlasVaultDraftMetadataPresentation]
    ) {
        self.savedSearches = savedSearches
        self.savedJobs = savedJobs
        self.applicationNotes = applicationNotes
        self.profileSnippets = profileSnippets
        self.draftMetadata = draftMetadata
    }

    public var description: String {
        "AtlasVaultPrivatePresentationState(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultSavedSearchPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let id: AtlasVaultPresentationID
    public let name: String
    public let summary: String
    public let details: String?
    public let request: AtlasVaultSavedSearchRequestPresentation
    public let createdAt: String?
    public let updatedAt: String?

    public var description: String {
        "AtlasVaultSavedSearchPresentation(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultSavedSearchRequestPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let text: String?
    public let status: [String]
    public let organizations: [String]
    public let sourceIDs: [String]
    public let cities: [String]
    public let countriesISO3: [String]
    public let nationalInternational: [String]
    public let gradeCodes: [String]
    public let ccogFamilies: [String]
    public let capabilityTags: [String]
    public let contractGroups: [String]
    public let seniorityGroups: [String]
    public let workModalities: [String]
    public let volunteerKinds: [String]
    public let unvCategories: [String]
    public let unvVolunteerTypes: [String]
    public let closingDateTo: String?
    public let includeLowConfidence: Bool
    public let includeFacets: Bool
    public let limit: Int
    public let offset: Int
    public let sort: String

    public var description: String {
        "AtlasVaultSavedSearchRequestPresentation(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultSavedJobPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let id: AtlasVaultPresentationID
    public let applicationID: String?
    public let jobKey: String
    public let status: String
    public let notes: String?
    public let appliedAt: String?
    public let updatedAt: String?

    public var description: String {
        "AtlasVaultSavedJobPresentation(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultApplicationNotePresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let id: AtlasVaultPresentationID
    public let title: String?
    public let body: String
    public let noteKind: String
    public let linkedJobKey: String?
    public let linkedSavedJobID: AtlasVaultPresentationID?
    public let createdAt: String
    public let updatedAt: String
    public let isPinned: Bool?
    public let sortOrder: Int?

    public var description: String {
        "AtlasVaultApplicationNotePresentation(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultProfileSnippetPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let id: AtlasVaultPresentationID
    public let title: String
    public let body: String
    public let targetSystem: String?
    public let fieldHint: String?
    public let tags: [String]
    public let provenanceNotes: String?
    public let createdAt: String
    public let updatedAt: String

    public var description: String {
        "AtlasVaultProfileSnippetPresentation(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultDraftMetadataPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let id: AtlasVaultPresentationID
    public let linkedJobKey: String?
    public let linkedSavedJobID: AtlasVaultPresentationID?
    public let targetSystem: String
    public let documentType: String
    public let generatedDocumentReference: String
    public let draftStatus: String
    public let generatedAt: String
    public let reviewedAt: String?
    public let submittedAt: String?
    public let archivedAt: String?
    public let personalContextReference: String?
    public let contextSummary: String?

    public var description: String {
        "AtlasVaultDraftMetadataPresentation(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultPresentationAdapter:
    AtlasVaultPresentationAdapting,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public init() {}

    public func makeSnapshot(
        runtimeStatus: AtlasVaultRuntimeStatus,
        privateState: AtlasVaultHydratedState?,
        generation: AtlasVaultPresentationGeneration?,
        commandState: AtlasVaultPresentationCommandState
    ) -> AtlasVaultPresentationSnapshot {
        AtlasVaultPresentationSnapshot(
            status: presentationStatus(
                runtimeStatus: runtimeStatus,
                commandState: commandState
            ),
            privateState: projectPrivateState(
                privateState,
                generation: generation,
                runtimeStatus: runtimeStatus
            )
        )
    }

    public var description: String {
        "AtlasVaultPresentationAdapter(state: <none>)"
    }

    public var debugDescription: String {
        description
    }

    private func presentationStatus(
        runtimeStatus: AtlasVaultRuntimeStatus,
        commandState: AtlasVaultPresentationCommandState
    ) -> AtlasVaultPresentationStatus {
        switch runtimeStatus {
        case .locked:
            commandState == .cancelled ? .cancelled : .locked
        case .activating:
            .activating
        case .locking:
            .locking
        case .unlocked:
            switch commandState {
            case .none: .unlocked
            case .cancelled: .cancelled
            case .saveFailed: .saveFailed
            }
        case .saving:
            .saveInProgress
        case let .failed(failure):
            presentationStatus(for: failure)
        }
    }

    private func presentationStatus(
        for failure: AtlasVaultRuntimeFailure
    ) -> AtlasVaultPresentationStatus {
        switch failure {
        case let .activation(failure):
            presentationStatus(for: failure)
        }
    }

    private func presentationStatus(
        for failure: AtlasVaultActivationFailure
    ) -> AtlasVaultPresentationStatus {
        switch failure {
        case .keyUnavailable, .keyStoreFailure, .invalidVaultKey:
            .keyUnavailable
        case .storeMissing:
            .noVault
        case .authenticationFailed, .corruptStore:
            .corruptStore
        case .unsupportedVersion:
            .unsupportedVersion
        case .cancelled:
            .cancelled
        case .invalidVaultID, .vaultUnavailable, .activationInProgress, .alreadyUnlocked:
            .failed
        }
    }

    private func shouldProjectPrivateState(
        _ runtimeStatus: AtlasVaultRuntimeStatus
    ) -> Bool {
        switch runtimeStatus {
        case .unlocked, .saving:
            true
        case .locked, .activating, .locking, .failed:
            false
        }
    }

    private func projectPrivateState(
        _ state: AtlasVaultHydratedState?,
        generation: AtlasVaultPresentationGeneration?,
        runtimeStatus: AtlasVaultRuntimeStatus
    ) -> AtlasVaultPrivatePresentationState? {
        guard shouldProjectPrivateState(runtimeStatus),
              let state,
              let generation
        else {
            return nil
        }
        return project(state, generation: generation)
    }

    private func project(
        _ state: AtlasVaultHydratedState,
        generation: AtlasVaultPresentationGeneration
    ) -> AtlasVaultPrivatePresentationState {
        AtlasVaultPrivatePresentationState(
            savedSearches: state.savedSearches.map { record in
                AtlasVaultSavedSearchPresentation(
                    id: AtlasVaultPresentationID(
                        recordID: record.metadata.id,
                        generation: generation
                    ),
                    name: record.payload.name,
                    summary: record.payload.summary,
                    details: record.payload.description,
                    request: AtlasVaultSavedSearchRequestPresentation(
                        text: record.payload.request.text,
                        status: record.payload.request.status,
                        organizations: record.payload.request.organizations,
                        sourceIDs: record.payload.request.sourceIDs,
                        cities: record.payload.request.cities,
                        countriesISO3: record.payload.request.countriesISO3,
                        nationalInternational: record.payload.request.nationalInternational,
                        gradeCodes: record.payload.request.gradeCodes,
                        ccogFamilies: record.payload.request.ccogFamilies,
                        capabilityTags: record.payload.request.capabilityTags,
                        contractGroups: record.payload.request.contractGroups,
                        seniorityGroups: record.payload.request.seniorityGroups,
                        workModalities: record.payload.request.workModalities,
                        volunteerKinds: record.payload.request.volunteerKinds,
                        unvCategories: record.payload.request.unvCategories,
                        unvVolunteerTypes: record.payload.request.unvVolunteerTypes,
                        closingDateTo: record.payload.request.closingDateTo,
                        includeLowConfidence: record.payload.request.includeLowConfidence,
                        includeFacets: record.payload.request.includeFacets,
                        limit: record.payload.request.limit,
                        offset: record.payload.request.offset,
                        sort: record.payload.request.sort
                    ),
                    createdAt: record.payload.createdAt,
                    updatedAt: record.payload.updatedAt
                )
            },
            savedJobs: state.savedJobs.map { record in
                AtlasVaultSavedJobPresentation(
                    id: AtlasVaultPresentationID(
                        recordID: record.metadata.id,
                        generation: generation
                    ),
                    applicationID: record.payload.id,
                    jobKey: record.payload.jobKey,
                    status: record.payload.status,
                    notes: record.payload.notes,
                    appliedAt: record.payload.appliedAt,
                    updatedAt: record.payload.updatedAt
                )
            },
            applicationNotes: state.applicationNotes.map { record in
                AtlasVaultApplicationNotePresentation(
                    id: AtlasVaultPresentationID(
                        recordID: record.metadata.id,
                        generation: generation
                    ),
                    title: record.payload.title,
                    body: record.payload.body,
                    noteKind: record.payload.noteKind,
                    linkedJobKey: record.payload.linkedJobKey,
                    linkedSavedJobID: record.payload.linkedSavedJobRecordID.map {
                        AtlasVaultPresentationID(
                            recordID: $0,
                            generation: generation
                        )
                    },
                    createdAt: record.payload.createdAt,
                    updatedAt: record.payload.updatedAt,
                    isPinned: record.payload.isPinned,
                    sortOrder: record.payload.sortOrder
                )
            },
            profileSnippets: state.profileSnippets.map { record in
                AtlasVaultProfileSnippetPresentation(
                    id: AtlasVaultPresentationID(
                        recordID: record.metadata.id,
                        generation: generation
                    ),
                    title: record.payload.title,
                    body: record.payload.body,
                    targetSystem: record.payload.targetSystem,
                    fieldHint: record.payload.fieldHint,
                    tags: record.payload.tags,
                    provenanceNotes: record.payload.provenanceNotes,
                    createdAt: record.payload.createdAt,
                    updatedAt: record.payload.updatedAt
                )
            },
            draftMetadata: state.draftMetadata.map { record in
                AtlasVaultDraftMetadataPresentation(
                    id: AtlasVaultPresentationID(
                        recordID: record.metadata.id,
                        generation: generation
                    ),
                    linkedJobKey: record.payload.linkedJobKey,
                    linkedSavedJobID: record.payload.linkedSavedJobRecordID.map {
                        AtlasVaultPresentationID(
                            recordID: $0,
                            generation: generation
                        )
                    },
                    targetSystem: record.payload.targetSystem,
                    documentType: record.payload.documentType,
                    generatedDocumentReference: record.payload.generatedDocumentReference,
                    draftStatus: record.payload.draftStatus,
                    generatedAt: record.payload.generatedAt,
                    reviewedAt: record.payload.reviewedAt,
                    submittedAt: record.payload.submittedAt,
                    archivedAt: record.payload.archivedAt,
                    personalContextReference: record.payload.personalContextReference,
                    contextSummary: record.payload.contextSummary
                )
            }
        )
    }
}
