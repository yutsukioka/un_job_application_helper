import Foundation

public enum AtlasVaultStoreError: Error, Equatable, Sendable {
    case unsupportedStoreVersion
    case invalidStoreFormat
    case invalidEnvelope
    case invalidRecord
    case fileExists
    case readFailed
    case writeFailed
    case invalidJSON
}

public indirect enum AtlasJSONValue: Codable, Equatable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null
    case array([AtlasJSONValue])
    case object([String: AtlasJSONValue])

    public init(from decoder: Decoder) throws {
        if let container = try? decoder.container(keyedBy: DynamicCodingKey.self) {
            var object: [String: AtlasJSONValue] = [:]
            for key in container.allKeys {
                object[key.stringValue] = try container.decode(AtlasJSONValue.self, forKey: key)
            }
            self = .object(object)
            return
        }
        if var container = try? decoder.unkeyedContainer() {
            var values: [AtlasJSONValue] = []
            while !container.isAtEnd {
                values.append(try container.decode(AtlasJSONValue.self))
            }
            self = .array(values)
            return
        }

        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case let .string(value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case let .number(value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case let .bool(value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case .null:
            var container = encoder.singleValueContainer()
            try container.encodeNil()
        case let .array(values):
            var container = encoder.unkeyedContainer()
            for value in values {
                try container.encode(value)
            }
        case let .object(values):
            var container = encoder.container(keyedBy: DynamicCodingKey.self)
            for key in values.keys.sorted() {
                try container.encode(values[key], forKey: DynamicCodingKey(stringValue: key))
            }
        }
    }
}

public struct AtlasVaultLocalStoreEnvelope: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let storeID: String
    public let createdAt: String
    public let updatedAt: String
    public let vaultMetadata: [String: AtlasJSONValue]
    public let records: [AtlasVaultEncryptedRecordEnvelope]

    public init(
        format: String = AtlasVaultLocalStoreIO.localStoreFormat,
        version: Int = AtlasVaultLocalStoreIO.supportedLocalStoreVersion,
        storeID: String,
        createdAt: String,
        updatedAt: String,
        vaultMetadata: [String: AtlasJSONValue],
        records: [AtlasVaultEncryptedRecordEnvelope]
    ) {
        self.format = format
        self.version = version
        self.storeID = storeID
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.vaultMetadata = vaultMetadata
        self.records = records
    }

    enum CodingKeys: String, CodingKey {
        case format
        case version
        case storeID = "store_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case vaultMetadata = "vault_metadata"
        case records
    }
}

public enum AtlasVaultLocalStoreIO {
    public static let localStoreFormat = "atlasvault-local-store"
    public static let supportedLocalStoreVersion = 1

    public static func decode(_ data: Data) throws -> AtlasVaultLocalStoreEnvelope {
        try rejectLegacyPrivateFields(in: data)
        let store: AtlasVaultLocalStoreEnvelope
        do {
            store = try decoder.decode(AtlasVaultLocalStoreEnvelope.self, from: data)
        } catch {
            throw AtlasVaultStoreError.invalidJSON
        }
        try validate(store)
        return store
    }

    public static func encode(_ store: AtlasVaultLocalStoreEnvelope) throws -> Data {
        try validate(store)
        do {
            return try encoder.encode(store)
        } catch {
            throw AtlasVaultStoreError.invalidEnvelope
        }
    }

    public static func read(from url: URL) throws -> AtlasVaultLocalStoreEnvelope {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw AtlasVaultStoreError.readFailed
        }
        return try decode(data)
    }

    public static func write(
        _ store: AtlasVaultLocalStoreEnvelope,
        to url: URL,
        overwrite: Bool = false
    ) throws {
        if !overwrite && fileExists(at: url) {
            throw AtlasVaultStoreError.fileExists
        }
        let data = try encode(store)
        do {
            try data.write(to: url, options: [.atomic])
        } catch {
            throw AtlasVaultStoreError.writeFailed
        }
    }

    private static func validate(_ store: AtlasVaultLocalStoreEnvelope) throws {
        guard store.format == localStoreFormat else {
            throw AtlasVaultStoreError.invalidStoreFormat
        }
        guard store.version == supportedLocalStoreVersion else {
            throw AtlasVaultStoreError.unsupportedStoreVersion
        }
        guard !store.storeID.isEmpty, !store.createdAt.isEmpty, !store.updatedAt.isEmpty else {
            throw AtlasVaultStoreError.invalidEnvelope
        }
        for record in store.records {
            try validate(record)
        }
    }

    private static func validate(_ record: AtlasVaultEncryptedRecordEnvelope) throws {
        guard record.schemaVersion == AtlasVaultRecordCrypto.supportedRecordSchemaVersion else {
            throw AtlasVaultStoreError.invalidRecord
        }
        guard
            !record.id.isEmpty,
            !record.revision.isEmpty,
            !record.keyID.isEmpty,
            let nonce = Data(base64Encoded: record.nonce),
            nonce.count == AtlasVaultRecordCrypto.nonceByteCount,
            let ciphertext = Data(base64Encoded: record.ciphertext),
            ciphertext.count >= AtlasVaultRecordCrypto.gcmTagByteCount
        else {
            throw AtlasVaultStoreError.invalidRecord
        }
    }

    private static func rejectLegacyPrivateFields(in data: Data) throws {
        let object: Any
        do {
            object = try JSONSerialization.jsonObject(with: data)
        } catch {
            throw AtlasVaultStoreError.invalidJSON
        }
        guard let dictionary = object as? [String: Any] else {
            throw AtlasVaultStoreError.invalidJSON
        }
        for legacyPrivateField in ["savedSearches", "savedJobs", "saved_searches", "saved_jobs"] {
            if dictionary.keys.contains(legacyPrivateField) {
                throw AtlasVaultStoreError.invalidEnvelope
            }
        }
    }

    private static func fileExists(at url: URL) -> Bool {
        (try? url.checkResourceIsReachable()) == true
    }

    private static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }()

    private static let decoder = JSONDecoder()
}

private struct DynamicCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    init(intValue: Int) {
        self.stringValue = String(intValue)
        self.intValue = intValue
    }
}
