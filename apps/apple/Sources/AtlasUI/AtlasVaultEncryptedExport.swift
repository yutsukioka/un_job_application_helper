import CoreFoundation
import Foundation
import SwiftUI
import UniformTypeIdentifiers

public enum AtlasVaultEncryptedExportError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible
{
    case invalidEnvelope
    case unsupportedVersion
    case invalidMetadata
    case invalidRecord

    public var description: String {
        switch self {
        case .invalidEnvelope:
            return "Encrypted export is invalid."
        case .unsupportedVersion:
            return "Encrypted export version is unsupported."
        case .invalidMetadata:
            return "Encrypted export metadata is invalid."
        case .invalidRecord:
            return "Encrypted export record is invalid."
        }
    }
}

public struct AtlasVaultEncryptedExportEnvelope:
    Codable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public static let supportedFormat = "atlasvault-export"
    public static let supportedVersion = 1
    public static let defaultFilename =
        "AtlasVault-Encrypted-Backup.atlasvault"

    public let format: String
    public let version: Int
    public let exportID: String
    public let createdAt: String
    public let vaultMetadata: AtlasVaultVersionedWrappedKeyMetadata
    public let records: [AtlasVaultEncryptedRecordEnvelope]

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case exportID = "export_id"
        case createdAt = "created_at"
        case vaultMetadata = "vault_metadata"
        case records
    }

    public init(
        format: String = Self.supportedFormat,
        version: Int = Self.supportedVersion,
        exportID: String,
        createdAt: String,
        vaultMetadata: AtlasVaultVersionedWrappedKeyMetadata,
        records: [AtlasVaultEncryptedRecordEnvelope]
    ) throws {
        guard format == Self.supportedFormat else {
            throw AtlasVaultEncryptedExportError.invalidEnvelope
        }
        guard version == Self.supportedVersion else {
            throw AtlasVaultEncryptedExportError.unsupportedVersion
        }
        guard
            Self.isCanonicalLowercaseUUID(exportID),
            Self.isStrictUTCTimestamp(createdAt),
            vaultMetadata.recoveryKeyWrap != nil
        else {
            throw AtlasVaultEncryptedExportError.invalidMetadata
        }
        for record in records {
            try Self.validate(record)
        }
        self.format = format
        self.version = version
        self.exportID = exportID
        self.createdAt = createdAt
        self.vaultMetadata = vaultMetadata
        self.records = records
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let actual = Set(container.allKeys.map(\.stringValue))
        guard actual == Set(CodingKeys.allCases.map(\.rawValue)) else {
            throw AtlasVaultEncryptedExportError.invalidEnvelope
        }
        try self.init(
            format: container.decode(String.self, forKey: .format),
            version: container.decode(Int.self, forKey: .version),
            exportID: container.decode(String.self, forKey: .exportID),
            createdAt: container.decode(String.self, forKey: .createdAt),
            vaultMetadata: container.decode(
                AtlasVaultVersionedWrappedKeyMetadata.self,
                forKey: .vaultMetadata
            ),
            records: container.decode(
                [AtlasVaultEncryptedRecordEnvelope].self,
                forKey: .records
            )
        )
    }

    public func canonicalData() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        do {
            let data = try Self.pythonCompatibleASCIIJSON(
                encoder.encode(self)
            )
            _ = try Self.decodeStrict(data)
            return data
        } catch let error as AtlasVaultEncryptedExportError {
            throw error
        } catch {
            throw AtlasVaultEncryptedExportError.invalidEnvelope
        }
    }

    public static func decodeStrict(
        _ data: Data
    ) throws -> AtlasVaultEncryptedExportEnvelope {
        try validateJSONShape(data)
        do {
            return try JSONDecoder().decode(Self.self, from: data)
        } catch let error as AtlasVaultEncryptedExportError {
            throw error
        } catch {
            throw AtlasVaultEncryptedExportError.invalidEnvelope
        }
    }

    public var description: String {
        "AtlasVaultEncryptedExportEnvelope(<redacted>)"
    }

    public var debugDescription: String {
        description
    }

    private static func validateJSONShape(_ data: Data) throws {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let root = object as? [String: Any],
            Set(root.keys) == Set(CodingKeys.allCases.map(\.rawValue)),
            isStrictJSONInteger(
                root["version"],
                equalTo: Self.supportedVersion
            ),
            let metadata = root["vault_metadata"] as? [String: Any],
            Set(metadata.keys) == [
                "format",
                "version",
                "vault_id",
                "crypto",
                "key_wraps",
            ],
            isStrictJSONInteger(
                metadata["version"],
                equalTo: AtlasVaultVersionedWrappedKeyMetadata
                    .supportedVersion
            ),
            let crypto = metadata["crypto"] as? [String: Any],
            Set(crypto.keys) == [
                "record_aead",
                "kdf",
                "subkey_kdf",
                "key_wrap_aead",
            ],
            let wraps = metadata["key_wraps"] as? [[String: Any]]
        else {
            throw AtlasVaultEncryptedExportError.invalidEnvelope
        }
        for wrap in wraps {
            let type = wrap["type"] as? String
            switch type {
            case AtlasVaultWrappedKeyEnvelope.supportedType:
                guard
                    Set(wrap.keys) == [
                        "id",
                        "type",
                        "kdf",
                        "nonce",
                        "ciphertext",
                    ]
                else {
                    throw AtlasVaultEncryptedExportError.invalidMetadata
                }
            case AtlasVaultRecoveryWrappedKeyEnvelope.supportedType:
                guard
                    isStrictJSONInteger(
                        wrap["wrap_version"],
                        equalTo: AtlasVaultRecoveryWrappedKeyEnvelope
                            .supportedWrapVersion
                    ),
                    Set(wrap.keys) == [
                        "id",
                        "type",
                        "wrap_version",
                        "kdf",
                        "nonce",
                        "ciphertext",
                    ]
                else {
                    throw AtlasVaultEncryptedExportError.invalidMetadata
                }
            default:
                throw AtlasVaultEncryptedExportError.invalidMetadata
            }
        }
    }

    private static func isStrictJSONInteger(
        _ value: Any?,
        equalTo expected: Int
    ) -> Bool {
        guard
            let number = value as? NSNumber,
            CFGetTypeID(number) != CFBooleanGetTypeID(),
            !CFNumberIsFloatType(number)
        else {
            return false
        }
        return number.intValue == expected && number == NSNumber(value: expected)
    }

    private static func isCanonicalLowercaseUUID(_ value: String) -> Bool {
        guard let uuid = UUID(uuidString: value) else {
            return false
        }
        return uuid.uuidString.lowercased() == value
    }

    private static func isStrictUTCTimestamp(_ value: String) -> Bool {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        guard let date = formatter.date(from: value) else {
            return false
        }
        return formatter.string(from: date) == value
    }

    private static func validate(
        _ record: AtlasVaultEncryptedRecordEnvelope
    ) throws {
        guard
            record.schemaVersion
                == AtlasVaultRecordCrypto.supportedRecordSchemaVersion,
            !record.id.isEmpty,
            !record.revision.isEmpty,
            !record.keyID.isEmpty,
            let nonce = canonicalBase64(record.nonce),
            nonce.count == AtlasVaultRecordCrypto.nonceByteCount,
            let ciphertext = canonicalBase64(record.ciphertext),
            ciphertext.count >= AtlasVaultRecordCrypto.gcmTagByteCount
        else {
            throw AtlasVaultEncryptedExportError.invalidRecord
        }
    }

    private static func canonicalBase64(_ value: String) -> Data? {
        guard let data = Data(base64Encoded: value, options: []) else {
            return nil
        }
        return data.base64EncodedString() == value ? data : nil
    }

    private static func pythonCompatibleASCIIJSON(
        _ data: Data
    ) throws -> Data {
        guard let text = String(data: data, encoding: .utf8) else {
            throw AtlasVaultEncryptedExportError.invalidEnvelope
        }
        var result = ""
        result.reserveCapacity(text.utf8.count)
        for scalar in text.unicodeScalars {
            switch scalar.value {
            case 0x00...0x7E:
                result.unicodeScalars.append(scalar)
            case 0x7F...0xFFFF:
                result += String(format: "\\u%04x", scalar.value)
            default:
                let value = scalar.value - 0x10000
                let high = 0xD800 + (value >> 10)
                let low = 0xDC00 + (value & 0x3FF)
                result += String(
                    format: "\\u%04x\\u%04x",
                    high,
                    low
                )
            }
        }
        return Data(result.utf8)
    }
}

public struct AtlasVaultEncryptedDocument: FileDocument, Sendable {
    public static var readableContentTypes: [UTType] {
        [UTType(filenameExtension: "atlasvault") ?? .json]
    }

    public let encryptedData: Data

    public init(verifiedEncryptedData: Data) throws {
        let envelope = try AtlasVaultEncryptedExportEnvelope.decodeStrict(
            verifiedEncryptedData
        )
        encryptedData = try envelope.canonicalData()
    }

    public init(configuration: ReadConfiguration) throws {
        guard let data = configuration.file.regularFileContents else {
            throw AtlasVaultEncryptedExportError.invalidEnvelope
        }
        try self.init(verifiedEncryptedData: data)
    }

    public func fileWrapper(
        configuration _: WriteConfiguration
    ) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: encryptedData)
    }
}
