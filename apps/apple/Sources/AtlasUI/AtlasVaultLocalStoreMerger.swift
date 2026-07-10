import Foundation

public protocol AtlasVaultLocalStoreMerging: Sendable {
    func merge(
        records incoming: [AtlasVaultEncryptedRecordEnvelope],
        into store: AtlasVaultLocalStoreEnvelope
    ) throws -> AtlasVaultLocalStoreEnvelope
}

public enum AtlasVaultLocalStoreMergeError: Error, Equatable, Sendable {
    case duplicateExistingRecordID
    case duplicateIncomingRecordID
    case staleParentRevision
    case unsupportedRecordVersion
    case invalidRecord
    case invalidStore
    case conflictDetected
}

public struct AtlasVaultLocalStoreMerger: AtlasVaultLocalStoreMerging {
    private let updatedAtProvider: @Sendable () -> String

    public init(updatedAtProvider: @escaping @Sendable () -> String = AtlasVaultLocalStoreMerger.currentTimestamp) {
        self.updatedAtProvider = updatedAtProvider
    }

    public func merge(
        records incoming: [AtlasVaultEncryptedRecordEnvelope],
        into store: AtlasVaultLocalStoreEnvelope
    ) throws -> AtlasVaultLocalStoreEnvelope {
        try validate(store: store)
        try validateNoDuplicateExistingRecords(store.records)
        try validateNoDuplicateIncomingRecords(incoming)

        var mergedRecords = store.records
        var recordIndexByID = Dictionary(uniqueKeysWithValues: store.records.enumerated().map { index, record in
            (record.id, index)
        })

        for record in incoming {
            try validate(record: record)
            if let parentRevision = record.parentRevision {
                guard let existingIndex = recordIndexByID[record.id] else {
                    throw AtlasVaultLocalStoreMergeError.staleParentRevision
                }
                guard mergedRecords[existingIndex].revision == parentRevision else {
                    throw AtlasVaultLocalStoreMergeError.staleParentRevision
                }
                mergedRecords[existingIndex] = record
            } else if recordIndexByID[record.id] != nil {
                throw AtlasVaultLocalStoreMergeError.conflictDetected
            } else {
                recordIndexByID[record.id] = mergedRecords.count
                mergedRecords.append(record)
            }
        }

        return AtlasVaultLocalStoreEnvelope(
            format: store.format,
            version: store.version,
            storeID: store.storeID,
            createdAt: store.createdAt,
            updatedAt: updatedAtProvider(),
            vaultMetadata: store.vaultMetadata,
            records: mergedRecords
        )
    }

    private func validate(store: AtlasVaultLocalStoreEnvelope) throws {
        guard store.format == AtlasVaultLocalStoreIO.localStoreFormat,
              store.version == AtlasVaultLocalStoreIO.supportedLocalStoreVersion,
              !store.storeID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !store.createdAt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !store.updatedAt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            throw AtlasVaultLocalStoreMergeError.invalidStore
        }
        for record in store.records {
            try validate(record: record)
        }
    }

    private func validateNoDuplicateExistingRecords(_ records: [AtlasVaultEncryptedRecordEnvelope]) throws {
        var ids = Set<String>()
        for record in records where !ids.insert(record.id).inserted {
            throw AtlasVaultLocalStoreMergeError.duplicateExistingRecordID
        }
    }

    private func validateNoDuplicateIncomingRecords(_ records: [AtlasVaultEncryptedRecordEnvelope]) throws {
        var ids = Set<String>()
        for record in records where !ids.insert(record.id).inserted {
            throw AtlasVaultLocalStoreMergeError.duplicateIncomingRecordID
        }
    }

    private func validate(record: AtlasVaultEncryptedRecordEnvelope) throws {
        guard record.schemaVersion == AtlasVaultRecordCrypto.supportedRecordSchemaVersion else {
            throw AtlasVaultLocalStoreMergeError.unsupportedRecordVersion
        }
        guard
            !record.id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            !record.revision.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            !record.keyID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            !record.nonce.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            !record.ciphertext.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            throw AtlasVaultLocalStoreMergeError.invalidRecord
        }
    }

    public static func currentTimestamp() -> String {
        ISO8601DateFormatter().string(from: Date())
    }
}
