import Foundation

public enum AtlasVaultPayloadType: String, Codable, CaseIterable, Equatable, Sendable {
    case savedSearch = "saved_search"
    case savedJob = "saved_job"
    case applicationNote = "application_note"
    case profileSnippet = "profile_snippet"
    case draftMetadata = "draft_metadata"
}

public struct AtlasVaultPayloadEnvelope<Payload>: Codable, Equatable, Sendable
where Payload: Codable & Equatable & Sendable {
    public static var payloadSchema: Int { 1 }

    public let type: AtlasVaultPayloadType
    public let payloadSchema: Int
    public let payload: Payload
    public let clientCreatedAt: String
    public let clientUpdatedAt: String

    public init(
        type: AtlasVaultPayloadType,
        payloadSchema: Int = Self.payloadSchema,
        payload: Payload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) {
        self.type = type
        self.payloadSchema = payloadSchema
        self.payload = payload
        self.clientCreatedAt = clientCreatedAt
        self.clientUpdatedAt = clientUpdatedAt
    }

    enum CodingKeys: String, CodingKey {
        case type
        case payloadSchema = "payload_schema"
        case payload
        case clientCreatedAt = "client_created_at"
        case clientUpdatedAt = "client_updated_at"
    }
}

public typealias AtlasSavedSearchVaultRecordPayload = AtlasVaultPayloadEnvelope<AtlasSavedSearchVaultPayload>
public typealias AtlasSavedJobVaultRecordPayload = AtlasVaultPayloadEnvelope<AtlasSavedJobVaultPayload>
public typealias AtlasApplicationNoteVaultRecordPayload = AtlasVaultPayloadEnvelope<AtlasApplicationNoteVaultPayload>
public typealias AtlasProfileSnippetVaultRecordPayload = AtlasVaultPayloadEnvelope<AtlasProfileSnippetVaultPayload>
public typealias AtlasDraftMetadataVaultRecordPayload = AtlasVaultPayloadEnvelope<AtlasDraftMetadataVaultPayload>

public struct AtlasSavedSearchVaultPayload: Codable, Equatable, Sendable {
    public let name: String
    public let summary: String
    public let description: String?
    public let request: AtlasSearchRequest
    public let createdAt: String?
    public let updatedAt: String?

    public init(
        name: String,
        summary: String,
        description: String? = nil,
        request: AtlasSearchRequest,
        createdAt: String? = nil,
        updatedAt: String? = nil
    ) {
        self.name = name
        self.summary = summary
        self.description = description
        self.request = request
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    public init(savedSearch: AtlasSavedSearch, summary: String? = nil) {
        self.init(
            name: savedSearch.name,
            summary: summary ?? savedSearch.description ?? "",
            description: savedSearch.description,
            request: savedSearch.request,
            createdAt: savedSearch.createdAt,
            updatedAt: savedSearch.updatedAt
        )
    }

    enum CodingKeys: String, CodingKey {
        case name
        case summary
        case description
        case request
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct AtlasSavedJobVaultPayload: Codable, Equatable, Sendable {
    public let id: String?
    public let jobKey: String
    public let status: String
    public let notes: String?
    public let appliedAt: String?
    public let updatedAt: String?

    public init(
        id: String? = nil,
        jobKey: String,
        status: String,
        notes: String? = nil,
        appliedAt: String? = nil,
        updatedAt: String? = nil
    ) {
        self.id = id
        self.jobKey = jobKey
        self.status = status
        self.notes = notes
        self.appliedAt = appliedAt
        self.updatedAt = updatedAt
    }

    public init(applicationRecord: AtlasApplicationRecord) {
        self.init(
            id: applicationRecord.id,
            jobKey: applicationRecord.jobKey,
            status: applicationRecord.status,
            notes: applicationRecord.notes,
            appliedAt: applicationRecord.appliedAt,
            updatedAt: applicationRecord.updatedAt
        )
    }

    enum CodingKeys: String, CodingKey {
        case id
        case jobKey = "job_key"
        case status
        case notes
        case appliedAt = "applied_at"
        case updatedAt = "updated_at"
    }
}

public struct AtlasApplicationNoteVaultPayload: Codable, Equatable, Sendable {
    public let title: String?
    public let body: String
    public let noteKind: String
    public let linkedJobKey: String?
    public let linkedSavedJobRecordID: String?
    public let createdAt: String
    public let updatedAt: String
    public let isPinned: Bool?
    public let sortOrder: Int?

    public init(
        title: String? = nil,
        body: String,
        noteKind: String,
        linkedJobKey: String? = nil,
        linkedSavedJobRecordID: String? = nil,
        createdAt: String,
        updatedAt: String,
        isPinned: Bool? = nil,
        sortOrder: Int? = nil
    ) {
        self.title = title
        self.body = body
        self.noteKind = noteKind
        self.linkedJobKey = linkedJobKey
        self.linkedSavedJobRecordID = linkedSavedJobRecordID
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.isPinned = isPinned
        self.sortOrder = sortOrder
    }

    enum CodingKeys: String, CodingKey {
        case title
        case body
        case noteKind = "note_kind"
        case linkedJobKey = "linked_job_key"
        case linkedSavedJobRecordID = "linked_saved_job_record_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case isPinned = "is_pinned"
        case sortOrder = "sort_order"
    }
}

public struct AtlasProfileSnippetVaultPayload: Codable, Equatable, Sendable {
    public let title: String
    public let body: String
    public let targetSystem: String?
    public let fieldHint: String?
    public let tags: [String]
    public let provenanceNotes: String?
    public let createdAt: String
    public let updatedAt: String

    public init(
        title: String,
        body: String,
        targetSystem: String? = nil,
        fieldHint: String? = nil,
        tags: [String] = [],
        provenanceNotes: String? = nil,
        createdAt: String,
        updatedAt: String
    ) {
        self.title = title
        self.body = body
        self.targetSystem = targetSystem
        self.fieldHint = fieldHint
        self.tags = tags
        self.provenanceNotes = provenanceNotes
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case title
        case body
        case targetSystem = "target_system"
        case fieldHint = "field_hint"
        case tags
        case provenanceNotes = "provenance_notes"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct AtlasDraftMetadataVaultPayload: Codable, Equatable, Sendable {
    public let linkedJobKey: String?
    public let linkedSavedJobRecordID: String?
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

    public init(
        linkedJobKey: String? = nil,
        linkedSavedJobRecordID: String? = nil,
        targetSystem: String,
        documentType: String,
        generatedDocumentReference: String,
        draftStatus: String,
        generatedAt: String,
        reviewedAt: String? = nil,
        submittedAt: String? = nil,
        archivedAt: String? = nil,
        personalContextReference: String? = nil,
        contextSummary: String? = nil
    ) {
        self.linkedJobKey = linkedJobKey
        self.linkedSavedJobRecordID = linkedSavedJobRecordID
        self.targetSystem = targetSystem
        self.documentType = documentType
        self.generatedDocumentReference = generatedDocumentReference
        self.draftStatus = draftStatus
        self.generatedAt = generatedAt
        self.reviewedAt = reviewedAt
        self.submittedAt = submittedAt
        self.archivedAt = archivedAt
        self.personalContextReference = personalContextReference
        self.contextSummary = contextSummary
    }

    enum CodingKeys: String, CodingKey {
        case linkedJobKey = "linked_job_key"
        case linkedSavedJobRecordID = "linked_saved_job_record_id"
        case targetSystem = "target_system"
        case documentType = "document_type"
        case generatedDocumentReference = "generated_document_reference"
        case draftStatus = "draft_status"
        case generatedAt = "generated_at"
        case reviewedAt = "reviewed_at"
        case submittedAt = "submitted_at"
        case archivedAt = "archived_at"
        case personalContextReference = "personal_context_reference"
        case contextSummary = "context_summary"
    }
}

public extension AtlasVaultPayloadEnvelope where Payload == AtlasSavedSearchVaultPayload {
    static func savedSearch(
        _ payload: AtlasSavedSearchVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) -> Self {
        Self(
            type: .savedSearch,
            payload: payload,
            clientCreatedAt: clientCreatedAt,
            clientUpdatedAt: clientUpdatedAt
        )
    }
}

public extension AtlasVaultPayloadEnvelope where Payload == AtlasSavedJobVaultPayload {
    static func savedJob(
        _ payload: AtlasSavedJobVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) -> Self {
        Self(
            type: .savedJob,
            payload: payload,
            clientCreatedAt: clientCreatedAt,
            clientUpdatedAt: clientUpdatedAt
        )
    }
}

public extension AtlasVaultPayloadEnvelope where Payload == AtlasApplicationNoteVaultPayload {
    static func applicationNote(
        _ payload: AtlasApplicationNoteVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) -> Self {
        Self(
            type: .applicationNote,
            payload: payload,
            clientCreatedAt: clientCreatedAt,
            clientUpdatedAt: clientUpdatedAt
        )
    }
}

public extension AtlasVaultPayloadEnvelope where Payload == AtlasProfileSnippetVaultPayload {
    static func profileSnippet(
        _ payload: AtlasProfileSnippetVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) -> Self {
        Self(
            type: .profileSnippet,
            payload: payload,
            clientCreatedAt: clientCreatedAt,
            clientUpdatedAt: clientUpdatedAt
        )
    }
}

public extension AtlasVaultPayloadEnvelope where Payload == AtlasDraftMetadataVaultPayload {
    static func draftMetadata(
        _ payload: AtlasDraftMetadataVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) -> Self {
        Self(
            type: .draftMetadata,
            payload: payload,
            clientCreatedAt: clientCreatedAt,
            clientUpdatedAt: clientUpdatedAt
        )
    }
}
