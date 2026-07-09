import Foundation

public protocol AtlasVaultRecordHydrating: Sendable {
    func hydrate(
        records: [AtlasVaultEncryptedRecordEnvelope],
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultHydratedState
}

public struct AtlasVaultHydratedState: Equatable, Sendable {
    public var savedSearches: [AtlasHydratedSavedSearch]
    public var savedJobs: [AtlasHydratedSavedJob]
    public var applicationNotes: [AtlasHydratedApplicationNote]
    public var profileSnippets: [AtlasHydratedProfileSnippet]
    public var draftMetadata: [AtlasHydratedDraftMetadata]
    public var tombstones: [AtlasHydratedTombstone]

    public init(
        savedSearches: [AtlasHydratedSavedSearch] = [],
        savedJobs: [AtlasHydratedSavedJob] = [],
        applicationNotes: [AtlasHydratedApplicationNote] = [],
        profileSnippets: [AtlasHydratedProfileSnippet] = [],
        draftMetadata: [AtlasHydratedDraftMetadata] = [],
        tombstones: [AtlasHydratedTombstone] = []
    ) {
        self.savedSearches = savedSearches
        self.savedJobs = savedJobs
        self.applicationNotes = applicationNotes
        self.profileSnippets = profileSnippets
        self.draftMetadata = draftMetadata
        self.tombstones = tombstones
    }
}

public struct AtlasHydratedRecordMetadata: Equatable, Sendable {
    public let id: String
    public let revision: String
    public let parentRevision: String?
    public let deleted: Bool
    public let keyID: String

    public init(
        id: String,
        revision: String,
        parentRevision: String?,
        deleted: Bool,
        keyID: String
    ) {
        self.id = id
        self.revision = revision
        self.parentRevision = parentRevision
        self.deleted = deleted
        self.keyID = keyID
    }
}

public struct AtlasHydratedSavedSearch: Equatable, Sendable {
    public let metadata: AtlasHydratedRecordMetadata
    public let payload: AtlasSavedSearchVaultPayload
    public let clientCreatedAt: String
    public let clientUpdatedAt: String

    public init(
        metadata: AtlasHydratedRecordMetadata,
        payload: AtlasSavedSearchVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) {
        self.metadata = metadata
        self.payload = payload
        self.clientCreatedAt = clientCreatedAt
        self.clientUpdatedAt = clientUpdatedAt
    }
}

public struct AtlasHydratedSavedJob: Equatable, Sendable {
    public let metadata: AtlasHydratedRecordMetadata
    public let payload: AtlasSavedJobVaultPayload
    public let clientCreatedAt: String
    public let clientUpdatedAt: String

    public init(
        metadata: AtlasHydratedRecordMetadata,
        payload: AtlasSavedJobVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) {
        self.metadata = metadata
        self.payload = payload
        self.clientCreatedAt = clientCreatedAt
        self.clientUpdatedAt = clientUpdatedAt
    }
}

public struct AtlasHydratedApplicationNote: Equatable, Sendable {
    public let metadata: AtlasHydratedRecordMetadata
    public let payload: AtlasApplicationNoteVaultPayload
    public let clientCreatedAt: String
    public let clientUpdatedAt: String

    public init(
        metadata: AtlasHydratedRecordMetadata,
        payload: AtlasApplicationNoteVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) {
        self.metadata = metadata
        self.payload = payload
        self.clientCreatedAt = clientCreatedAt
        self.clientUpdatedAt = clientUpdatedAt
    }
}

public struct AtlasHydratedProfileSnippet: Equatable, Sendable {
    public let metadata: AtlasHydratedRecordMetadata
    public let payload: AtlasProfileSnippetVaultPayload
    public let clientCreatedAt: String
    public let clientUpdatedAt: String

    public init(
        metadata: AtlasHydratedRecordMetadata,
        payload: AtlasProfileSnippetVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) {
        self.metadata = metadata
        self.payload = payload
        self.clientCreatedAt = clientCreatedAt
        self.clientUpdatedAt = clientUpdatedAt
    }
}

public struct AtlasHydratedDraftMetadata: Equatable, Sendable {
    public let metadata: AtlasHydratedRecordMetadata
    public let payload: AtlasDraftMetadataVaultPayload
    public let clientCreatedAt: String
    public let clientUpdatedAt: String

    public init(
        metadata: AtlasHydratedRecordMetadata,
        payload: AtlasDraftMetadataVaultPayload,
        clientCreatedAt: String,
        clientUpdatedAt: String
    ) {
        self.metadata = metadata
        self.payload = payload
        self.clientCreatedAt = clientCreatedAt
        self.clientUpdatedAt = clientUpdatedAt
    }
}

public struct AtlasHydratedTombstone: Equatable, Sendable {
    public let metadata: AtlasHydratedRecordMetadata

    public init(metadata: AtlasHydratedRecordMetadata) {
        self.metadata = metadata
    }
}

public enum AtlasVaultHydrationError: Error, Equatable, Sendable {
    case authenticationFailed
    case malformedPayload
    case unsupportedPayloadSchema
    case unknownRecordType
    case unsupportedRecordVersion
    case invalidSession
    case corruptRecord
}

public struct AtlasVaultRecordHydrator: AtlasVaultRecordHydrating {
    public init() {}

    public func hydrate(
        records: [AtlasVaultEncryptedRecordEnvelope],
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultHydratedState {
        var state = AtlasVaultHydratedState()
        for record in records {
            try hydrate(record: record, session: session, into: &state)
        }
        return state
    }

    private func hydrate(
        record: AtlasVaultEncryptedRecordEnvelope,
        session: AtlasVaultUnlockedSession,
        into state: inout AtlasVaultHydratedState
    ) throws {
        guard record.schemaVersion == AtlasVaultRecordCrypto.supportedRecordSchemaVersion else {
            throw AtlasVaultHydrationError.unsupportedRecordVersion
        }

        let metadata = AtlasHydratedRecordMetadata(record: record)
        let plaintext = try open(record: record, session: session)
        guard !record.deleted else {
            state.tombstones.append(AtlasHydratedTombstone(metadata: metadata))
            return
        }

        let header = try decodeHeader(from: plaintext)
        guard header.payloadSchema == AtlasSavedSearchVaultRecordPayload.payloadSchema else {
            throw AtlasVaultHydrationError.unsupportedPayloadSchema
        }
        guard let type = AtlasVaultPayloadType(rawValue: header.type) else {
            throw AtlasVaultHydrationError.unknownRecordType
        }

        switch type {
        case .savedSearch:
            let envelope: AtlasSavedSearchVaultRecordPayload = try decodeEnvelope(from: plaintext)
            state.savedSearches.append(AtlasHydratedSavedSearch(
                metadata: metadata,
                payload: envelope.payload,
                clientCreatedAt: envelope.clientCreatedAt,
                clientUpdatedAt: envelope.clientUpdatedAt
            ))
        case .savedJob:
            let envelope: AtlasSavedJobVaultRecordPayload = try decodeEnvelope(from: plaintext)
            state.savedJobs.append(AtlasHydratedSavedJob(
                metadata: metadata,
                payload: envelope.payload,
                clientCreatedAt: envelope.clientCreatedAt,
                clientUpdatedAt: envelope.clientUpdatedAt
            ))
        case .applicationNote:
            let envelope: AtlasApplicationNoteVaultRecordPayload = try decodeEnvelope(from: plaintext)
            state.applicationNotes.append(AtlasHydratedApplicationNote(
                metadata: metadata,
                payload: envelope.payload,
                clientCreatedAt: envelope.clientCreatedAt,
                clientUpdatedAt: envelope.clientUpdatedAt
            ))
        case .profileSnippet:
            let envelope: AtlasProfileSnippetVaultRecordPayload = try decodeEnvelope(from: plaintext)
            state.profileSnippets.append(AtlasHydratedProfileSnippet(
                metadata: metadata,
                payload: envelope.payload,
                clientCreatedAt: envelope.clientCreatedAt,
                clientUpdatedAt: envelope.clientUpdatedAt
            ))
        case .draftMetadata:
            let envelope: AtlasDraftMetadataVaultRecordPayload = try decodeEnvelope(from: plaintext)
            state.draftMetadata.append(AtlasHydratedDraftMetadata(
                metadata: metadata,
                payload: envelope.payload,
                clientCreatedAt: envelope.clientCreatedAt,
                clientUpdatedAt: envelope.clientUpdatedAt
            ))
        }
    }

    private func open(
        record: AtlasVaultEncryptedRecordEnvelope,
        session: AtlasVaultUnlockedSession
    ) throws -> Data {
        do {
            return try session.withVaultKey { vaultKey in
                try AtlasVaultRecordCrypto.open(
                    record: record,
                    vaultKey: vaultKey,
                    vaultID: session.vaultID
                )
            }
        } catch let error as AtlasVaultCryptoError {
            throw Self.hydrationError(for: error)
        } catch {
            throw AtlasVaultHydrationError.corruptRecord
        }
    }

    private func decodeHeader(from data: Data) throws -> PayloadHeader {
        do {
            return try JSONDecoder().decode(PayloadHeader.self, from: data)
        } catch {
            throw AtlasVaultHydrationError.malformedPayload
        }
    }

    private func decodeEnvelope<Payload>(
        from data: Data
    ) throws -> AtlasVaultPayloadEnvelope<Payload> where Payload: Codable & Equatable & Sendable {
        do {
            return try JSONDecoder().decode(AtlasVaultPayloadEnvelope<Payload>.self, from: data)
        } catch {
            throw AtlasVaultHydrationError.malformedPayload
        }
    }

    private static func hydrationError(for error: AtlasVaultCryptoError) -> AtlasVaultHydrationError {
        switch error {
        case .invalidVaultKeyLength:
            return .invalidSession
        case .unsupportedRecordVersion:
            return .unsupportedRecordVersion
        case .authenticationFailed:
            return .authenticationFailed
        case .invalidBase64, .invalidNonceLength, .invalidEnvelope:
            return .corruptRecord
        }
    }
}

private struct PayloadHeader: Decodable {
    let type: String
    let payloadSchema: Int

    enum CodingKeys: String, CodingKey {
        case type
        case payloadSchema = "payload_schema"
    }
}

private extension AtlasHydratedRecordMetadata {
    init(record: AtlasVaultEncryptedRecordEnvelope) {
        self.init(
            id: record.id,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID
        )
    }
}
