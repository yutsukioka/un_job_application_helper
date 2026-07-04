import CryptoKit
import Foundation

public enum AtlasVaultCryptoError: Error, Equatable, Sendable {
    case invalidBase64(String)
    case invalidVaultKeyLength
    case invalidNonceLength
    case unsupportedRecordVersion
    case authenticationFailed
    case invalidEnvelope
}

public struct AtlasVaultEncryptedRecordEnvelope: Codable, Equatable, Sendable {
    public let id: String
    public let schemaVersion: Int
    public let revision: String
    public let parentRevision: String?
    public let deleted: Bool
    public let keyID: String
    public let nonce: String
    public let ciphertext: String

    public init(
        id: String,
        schemaVersion: Int,
        revision: String,
        parentRevision: String?,
        deleted: Bool,
        keyID: String,
        nonce: String,
        ciphertext: String
    ) {
        self.id = id
        self.schemaVersion = schemaVersion
        self.revision = revision
        self.parentRevision = parentRevision
        self.deleted = deleted
        self.keyID = keyID
        self.nonce = nonce
        self.ciphertext = ciphertext
    }

    enum CodingKeys: String, CodingKey {
        case id
        case schemaVersion = "schema_version"
        case revision
        case parentRevision = "parent_revision"
        case deleted
        case keyID = "key_id"
        case nonce
        case ciphertext
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        self.revision = try container.decode(String.self, forKey: .revision)
        self.parentRevision = try container.decodeIfPresent(String.self, forKey: .parentRevision)
        self.deleted = try container.decode(Bool.self, forKey: .deleted)
        self.keyID = try container.decode(String.self, forKey: .keyID)
        self.nonce = try container.decode(String.self, forKey: .nonce)
        self.ciphertext = try container.decode(String.self, forKey: .ciphertext)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(revision, forKey: .revision)
        if let parentRevision {
            try container.encode(parentRevision, forKey: .parentRevision)
        } else {
            try container.encodeNil(forKey: .parentRevision)
        }
        try container.encode(deleted, forKey: .deleted)
        try container.encode(keyID, forKey: .keyID)
        try container.encode(nonce, forKey: .nonce)
        try container.encode(ciphertext, forKey: .ciphertext)
    }
}

public enum AtlasVaultRecordAAD {
    public static func jsonObject(
        vaultID: String,
        record: AtlasVaultEncryptedRecordEnvelope,
        vaultFormat: String = AtlasVaultRecordCrypto.vaultFormat,
        vaultVersion: Int = AtlasVaultRecordCrypto.vaultVersion
    ) -> [String: Any] {
        [
            "deleted": record.deleted,
            "key_id": record.keyID,
            "parent_revision": record.parentRevision ?? NSNull(),
            "record_id": record.id,
            "record_schema_version": record.schemaVersion,
            "revision": record.revision,
            "vault_format": vaultFormat,
            "vault_id": vaultID,
            "vault_version": vaultVersion,
        ]
    }

    public static func data(
        vaultID: String,
        record: AtlasVaultEncryptedRecordEnvelope,
        vaultFormat: String = AtlasVaultRecordCrypto.vaultFormat,
        vaultVersion: Int = AtlasVaultRecordCrypto.vaultVersion
    ) throws -> Data {
        try Data(
            stableJSONString(
                jsonObject(
                    vaultID: vaultID,
                    record: record,
                    vaultFormat: vaultFormat,
                    vaultVersion: vaultVersion
                )
            ).utf8
        )
    }

    private static func stableJSONString(_ value: Any) throws -> String {
        if value is NSNull {
            return "null"
        }
        if let bool = value as? Bool {
            return bool ? "true" : "false"
        }
        if let int = value as? Int {
            return String(int)
        }
        if let string = value as? String {
            return stableJSONStringLiteral(string)
        }
        if let dictionary = value as? [String: Any] {
            let fields = try dictionary.keys.sorted().map { key in
                try "\(stableJSONStringLiteral(key)):\(stableJSONString(dictionary[key]!))"
            }
            return "{\(fields.joined(separator: ","))}"
        }
        throw AtlasVaultCryptoError.invalidEnvelope
    }

    private static func stableJSONStringLiteral(_ value: String) -> String {
        var result = "\""
        for scalar in value.unicodeScalars {
            switch scalar.value {
            case 0x08:
                result += "\\b"
            case 0x09:
                result += "\\t"
            case 0x0A:
                result += "\\n"
            case 0x0C:
                result += "\\f"
            case 0x0D:
                result += "\\r"
            case 0x22:
                result += "\\\""
            case 0x5C:
                result += "\\\\"
            case 0x00..<0x20:
                result += String(format: "\\u%04x", scalar.value)
            case 0x20...0x7E:
                result.unicodeScalars.append(scalar)
            case 0x7F...0xFFFF:
                result += String(format: "\\u%04x", scalar.value)
            default:
                let value = scalar.value - 0x10000
                let high = 0xD800 + (value >> 10)
                let low = 0xDC00 + (value & 0x3FF)
                result += String(format: "\\u%04x\\u%04x", high, low)
            }
        }
        result += "\""
        return result
    }
}

public enum AtlasVaultRecordCrypto {
    public static let vaultFormat = "atlas-vault"
    public static let vaultVersion = 1
    public static let supportedRecordSchemaVersion = 1
    public static let vaultKeyByteCount = 32
    public static let nonceByteCount = 12
    public static let gcmTagByteCount = 16

    public static func deriveRecordKey(
        vaultKey: Data,
        vaultID: String,
        recordID: String
    ) throws -> SymmetricKey {
        guard vaultKey.count == vaultKeyByteCount else {
            throw AtlasVaultCryptoError.invalidVaultKeyLength
        }
        let inputKey = SymmetricKey(data: vaultKey)
        let salt = Data("\(vaultFormat):v1:\(vaultID)".utf8)
        let info = Data("record:\(recordID)".utf8)
        return HKDF<SHA256>.deriveKey(
            inputKeyMaterial: inputKey,
            salt: salt,
            info: info,
            outputByteCount: vaultKeyByteCount
        )
    }

    public static func open(
        record: AtlasVaultEncryptedRecordEnvelope,
        vaultKey: Data,
        vaultID: String,
        vaultFormat: String = AtlasVaultRecordCrypto.vaultFormat,
        vaultVersion: Int = AtlasVaultRecordCrypto.vaultVersion
    ) throws -> Data {
        try requireSupported(record)
        let recordKey = try deriveRecordKey(vaultKey: vaultKey, vaultID: vaultID, recordID: record.id)
        let sealedBox = try sealedBox(record)
        let aad = try AtlasVaultRecordAAD.data(
            vaultID: vaultID,
            record: record,
            vaultFormat: vaultFormat,
            vaultVersion: vaultVersion
        )
        do {
            return try AES.GCM.open(sealedBox, using: recordKey, authenticating: aad)
        } catch {
            throw AtlasVaultCryptoError.authenticationFailed
        }
    }

    public static func seal(
        plaintext: Data,
        vaultKey: Data,
        vaultID: String,
        record: AtlasVaultEncryptedRecordEnvelope,
        vaultFormat: String = AtlasVaultRecordCrypto.vaultFormat,
        vaultVersion: Int = AtlasVaultRecordCrypto.vaultVersion
    ) throws -> AtlasVaultEncryptedRecordEnvelope {
        try requireSupported(record)
        let recordKey = try deriveRecordKey(vaultKey: vaultKey, vaultID: vaultID, recordID: record.id)
        let nonce = try nonceData(record.nonce)
        let aad = try AtlasVaultRecordAAD.data(
            vaultID: vaultID,
            record: record,
            vaultFormat: vaultFormat,
            vaultVersion: vaultVersion
        )
        do {
            let sealed = try AES.GCM.seal(
                plaintext,
                using: recordKey,
                nonce: AES.GCM.Nonce(data: nonce),
                authenticating: aad
            )
            return AtlasVaultEncryptedRecordEnvelope(
                id: record.id,
                schemaVersion: record.schemaVersion,
                revision: record.revision,
                parentRevision: record.parentRevision,
                deleted: record.deleted,
                keyID: record.keyID,
                nonce: record.nonce,
                ciphertext: (sealed.ciphertext + sealed.tag).base64EncodedString()
            )
        } catch {
            throw AtlasVaultCryptoError.authenticationFailed
        }
    }

    private static func requireSupported(_ record: AtlasVaultEncryptedRecordEnvelope) throws {
        guard record.schemaVersion == supportedRecordSchemaVersion else {
            throw AtlasVaultCryptoError.unsupportedRecordVersion
        }
    }

    private static func sealedBox(_ record: AtlasVaultEncryptedRecordEnvelope) throws -> AES.GCM.SealedBox {
        let nonce = try nonceData(record.nonce)
        let combinedCiphertextAndTag = try base64Data(record.ciphertext, fieldName: "ciphertext")
        guard combinedCiphertextAndTag.count > gcmTagByteCount else {
            throw AtlasVaultCryptoError.invalidEnvelope
        }
        return try AES.GCM.SealedBox(
            nonce: AES.GCM.Nonce(data: nonce),
            ciphertext: Data(combinedCiphertextAndTag.dropLast(gcmTagByteCount)),
            tag: Data(combinedCiphertextAndTag.suffix(gcmTagByteCount))
        )
    }

    private static func nonceData(_ base64: String) throws -> Data {
        let data = try base64Data(base64, fieldName: "nonce")
        guard data.count == nonceByteCount else {
            throw AtlasVaultCryptoError.invalidNonceLength
        }
        return data
    }

    private static func base64Data(_ base64: String, fieldName: String) throws -> Data {
        guard let data = Data(base64Encoded: base64) else {
            throw AtlasVaultCryptoError.invalidBase64(fieldName)
        }
        return data
    }
}
