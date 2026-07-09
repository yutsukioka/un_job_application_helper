import Foundation

public protocol AtlasVaultRecordSaving: Sendable {
    func save(
        mutations: AtlasVaultMutationSet,
        session: AtlasVaultUnlockedSession
    ) throws -> [AtlasVaultEncryptedRecordEnvelope]
}

public struct AtlasVaultMutationSet: Equatable, Sendable {
    public let creates: [AtlasVaultCreateMutation]
    public let updates: [AtlasVaultUpdateMutation]
    public let deletes: [AtlasVaultDeleteMutation]

    public init(
        creates: [AtlasVaultCreateMutation] = [],
        updates: [AtlasVaultUpdateMutation] = [],
        deletes: [AtlasVaultDeleteMutation] = []
    ) {
        self.creates = creates
        self.updates = updates
        self.deletes = deletes
    }
}

public struct AtlasVaultCreateMutation: Equatable, Sendable {
    public let payload: AtlasVaultSavePayload
    public let keyID: String

    public init(payload: AtlasVaultSavePayload, keyID: String) {
        self.payload = payload
        self.keyID = keyID
    }
}

public struct AtlasVaultUpdateMutation: Equatable, Sendable {
    public let recordID: String
    public let currentRevision: String
    public let payload: AtlasVaultSavePayload
    public let keyID: String

    public init(
        recordID: String,
        currentRevision: String,
        payload: AtlasVaultSavePayload,
        keyID: String
    ) {
        self.recordID = recordID
        self.currentRevision = currentRevision
        self.payload = payload
        self.keyID = keyID
    }
}

public struct AtlasVaultDeleteMutation: Equatable, Sendable {
    public let recordID: String
    public let currentRevision: String
    public let keyID: String

    public init(recordID: String, currentRevision: String, keyID: String) {
        self.recordID = recordID
        self.currentRevision = currentRevision
        self.keyID = keyID
    }
}

public enum AtlasVaultSavePayload: Equatable, Sendable {
    case savedSearch(AtlasSavedSearchVaultRecordPayload)
    case savedJob(AtlasSavedJobVaultRecordPayload)
    case applicationNote(AtlasApplicationNoteVaultRecordPayload)
    case profileSnippet(AtlasProfileSnippetVaultRecordPayload)
    case draftMetadata(AtlasDraftMetadataVaultRecordPayload)

    var payloadSchema: Int {
        switch self {
        case .savedSearch(let envelope):
            return envelope.payloadSchema
        case .savedJob(let envelope):
            return envelope.payloadSchema
        case .applicationNote(let envelope):
            return envelope.payloadSchema
        case .profileSnippet(let envelope):
            return envelope.payloadSchema
        case .draftMetadata(let envelope):
            return envelope.payloadSchema
        }
    }

    func encodedPayloadEnvelope() throws -> Data {
        guard payloadSchema == AtlasSavedSearchVaultRecordPayload.payloadSchema else {
            throw AtlasVaultSaveError.unsupportedPayloadSchema
        }
        do {
            switch self {
            case .savedSearch(let envelope):
                return try Self.encoder.encode(envelope)
            case .savedJob(let envelope):
                return try Self.encoder.encode(envelope)
            case .applicationNote(let envelope):
                return try Self.encoder.encode(envelope)
            case .profileSnippet(let envelope):
                return try Self.encoder.encode(envelope)
            case .draftMetadata(let envelope):
                return try Self.encoder.encode(envelope)
            }
        } catch {
            throw AtlasVaultSaveError.encodingFailed
        }
    }

    private static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}

public enum AtlasVaultSaveError: Error, Equatable, Sendable {
    case invalidSession
    case unsupportedPayloadType
    case unsupportedPayloadSchema
    case encodingFailed
    case encryptionFailed
    case missingRecordID
    case staleRevision
    case invalidMutation
    case unsupportedRecordVersion
}

public struct AtlasVaultRecordSaver: AtlasVaultRecordSaving {
    private let recordIDGenerator: @Sendable () -> String
    private let revisionIDGenerator: @Sendable () -> String
    private let nonceGenerator: @Sendable () -> Data

    public init() {
        self.init(
            recordIDGenerator: { UUID().uuidString.lowercased() },
            revisionIDGenerator: { UUID().uuidString.lowercased() },
            nonceGenerator: {
                Data((0..<AtlasVaultRecordCrypto.nonceByteCount).map { _ in
                    UInt8.random(in: UInt8.min...UInt8.max)
                })
            }
        )
    }

    init(
        recordIDGenerator: @escaping @Sendable () -> String,
        revisionIDGenerator: @escaping @Sendable () -> String,
        nonceGenerator: @escaping @Sendable () -> Data
    ) {
        self.recordIDGenerator = recordIDGenerator
        self.revisionIDGenerator = revisionIDGenerator
        self.nonceGenerator = nonceGenerator
    }

    public func save(
        mutations: AtlasVaultMutationSet,
        session: AtlasVaultUnlockedSession
    ) throws -> [AtlasVaultEncryptedRecordEnvelope] {
        try validate(session: session)

        var records: [AtlasVaultEncryptedRecordEnvelope] = []
        records.reserveCapacity(mutations.creates.count + mutations.updates.count + mutations.deletes.count)

        for mutation in mutations.creates {
            records.append(try save(create: mutation, session: session))
        }
        for mutation in mutations.updates {
            records.append(try save(update: mutation, session: session))
        }
        for mutation in mutations.deletes {
            records.append(try save(delete: mutation, session: session))
        }
        return records
    }

    private func save(
        create mutation: AtlasVaultCreateMutation,
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultEncryptedRecordEnvelope {
        guard isValidIdentifier(mutation.keyID) else {
            throw AtlasVaultSaveError.invalidMutation
        }
        let recordID = recordIDGenerator()
        let revisionID = revisionIDGenerator()
        guard isValidIdentifier(recordID), isValidIdentifier(revisionID) else {
            throw AtlasVaultSaveError.invalidMutation
        }
        return try encryptedRecord(
            id: recordID,
            revision: revisionID,
            parentRevision: nil,
            deleted: false,
            keyID: mutation.keyID,
            plaintext: try mutation.payload.encodedPayloadEnvelope(),
            session: session
        )
    }

    private func save(
        update mutation: AtlasVaultUpdateMutation,
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultEncryptedRecordEnvelope {
        try validateExistingRecord(recordID: mutation.recordID, revision: mutation.currentRevision)
        guard isValidIdentifier(mutation.keyID) else {
            throw AtlasVaultSaveError.invalidMutation
        }
        let revisionID = revisionIDGenerator()
        guard isValidIdentifier(revisionID) else {
            throw AtlasVaultSaveError.staleRevision
        }
        return try encryptedRecord(
            id: mutation.recordID,
            revision: revisionID,
            parentRevision: mutation.currentRevision,
            deleted: false,
            keyID: mutation.keyID,
            plaintext: try mutation.payload.encodedPayloadEnvelope(),
            session: session
        )
    }

    private func save(
        delete mutation: AtlasVaultDeleteMutation,
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultEncryptedRecordEnvelope {
        try validateExistingRecord(recordID: mutation.recordID, revision: mutation.currentRevision)
        guard isValidIdentifier(mutation.keyID) else {
            throw AtlasVaultSaveError.invalidMutation
        }
        let revisionID = revisionIDGenerator()
        guard isValidIdentifier(revisionID) else {
            throw AtlasVaultSaveError.staleRevision
        }
        return try encryptedRecord(
            id: mutation.recordID,
            revision: revisionID,
            parentRevision: mutation.currentRevision,
            deleted: true,
            keyID: mutation.keyID,
            plaintext: Data(),
            session: session
        )
    }

    private func encryptedRecord(
        id: String,
        revision: String,
        parentRevision: String?,
        deleted: Bool,
        keyID: String,
        plaintext: Data,
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultEncryptedRecordEnvelope {
        let nonce = nonceGenerator()
        guard nonce.count == AtlasVaultRecordCrypto.nonceByteCount else {
            throw AtlasVaultSaveError.encryptionFailed
        }
        let template = AtlasVaultEncryptedRecordEnvelope(
            id: id,
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion,
            revision: revision,
            parentRevision: parentRevision,
            deleted: deleted,
            keyID: keyID,
            nonce: nonce.base64EncodedString(),
            ciphertext: ""
        )
        do {
            return try session.withVaultKey { vaultKey in
                try AtlasVaultRecordCrypto.seal(
                    plaintext: plaintext,
                    vaultKey: vaultKey,
                    vaultID: session.vaultID,
                    record: template
                )
            }
        } catch let error as AtlasVaultCryptoError {
            throw Self.saveError(for: error)
        } catch {
            throw AtlasVaultSaveError.encryptionFailed
        }
    }

    private func validate(session: AtlasVaultUnlockedSession) throws {
        guard session.vaultKeyByteCount == AtlasVaultRecordCrypto.vaultKeyByteCount else {
            throw AtlasVaultSaveError.invalidSession
        }
        guard isValidIdentifier(session.vaultID) else {
            throw AtlasVaultSaveError.invalidSession
        }
    }

    private func validateExistingRecord(recordID: String, revision: String) throws {
        guard isValidIdentifier(recordID) else {
            throw AtlasVaultSaveError.missingRecordID
        }
        guard isValidIdentifier(revision) else {
            throw AtlasVaultSaveError.staleRevision
        }
    }

    private func isValidIdentifier(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private static func saveError(for error: AtlasVaultCryptoError) -> AtlasVaultSaveError {
        switch error {
        case .invalidVaultKeyLength:
            return .invalidSession
        case .unsupportedRecordVersion:
            return .unsupportedRecordVersion
        case .invalidBase64, .invalidNonceLength, .invalidEnvelope, .authenticationFailed:
            return .encryptionFailed
        }
    }
}
