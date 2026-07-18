import Foundation

private func atlasVaultCanonicalBase64Decoded(_ value: String) -> Data? {
    guard let data = Data(base64Encoded: value, options: []) else {
        return nil
    }
    return data.base64EncodedString() == value ? data : nil
}

public struct AtlasVaultArgon2idParameters:
    Decodable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let algorithm: String
    public let salt: Data
    public let memoryKiB: Int
    public let iterations: Int
    public let parallelism: Int

    enum CodingKeys: String, CodingKey {
        case algorithm
        case salt
        case memoryKiB = "memory_kib"
        case iterations
        case parallelism
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let algorithm = try container.decode(String.self, forKey: .algorithm)
        let saltText = try container.decode(String.self, forKey: .salt)
        let memoryKiB = try container.decode(Int.self, forKey: .memoryKiB)
        let iterations = try container.decode(Int.self, forKey: .iterations)
        let parallelism = try container.decode(Int.self, forKey: .parallelism)

        guard algorithm == "Argon2id" else {
            throw DecodingError.dataCorruptedError(
                forKey: .algorithm,
                in: container,
                debugDescription: "Unsupported key-wrap KDF"
            )
        }
        guard
            let salt = atlasVaultCanonicalBase64Decoded(saltText),
            salt.count >= 16
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .salt,
                in: container,
                debugDescription: "Invalid key-wrap salt"
            )
        }
        guard memoryKiB > 0, iterations > 0, parallelism > 0 else {
            throw DecodingError.dataCorruptedError(
                forKey: .memoryKiB,
                in: container,
                debugDescription: "Invalid Argon2id parameters"
            )
        }

        self.algorithm = algorithm
        self.salt = salt
        self.memoryKiB = memoryKiB
        self.iterations = iterations
        self.parallelism = parallelism
    }

    public var description: String {
        "AtlasVaultArgon2idParameters(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultKeyWrapCryptoSuite:
    Decodable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let recordAEAD: String
    public let kdf: String
    public let subkeyKDF: String
    public let keyWrapAEAD: String

    enum CodingKeys: String, CodingKey {
        case recordAEAD = "record_aead"
        case kdf
        case subkeyKDF = "subkey_kdf"
        case keyWrapAEAD = "key_wrap_aead"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let recordAEAD = try container.decode(String.self, forKey: .recordAEAD)
        let kdf = try container.decode(String.self, forKey: .kdf)
        let subkeyKDF = try container.decode(String.self, forKey: .subkeyKDF)
        let keyWrapAEAD = try container.decode(String.self, forKey: .keyWrapAEAD)

        guard
            recordAEAD == "AES-256-GCM",
            kdf == "Argon2id",
            subkeyKDF == "HKDF-SHA256",
            keyWrapAEAD == "AES-256-GCM"
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .keyWrapAEAD,
                in: container,
                debugDescription: "Unsupported vault crypto suite"
            )
        }

        self.recordAEAD = recordAEAD
        self.kdf = kdf
        self.subkeyKDF = subkeyKDF
        self.keyWrapAEAD = keyWrapAEAD
    }

    public var description: String {
        "AtlasVaultKeyWrapCryptoSuite(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultWrappedKeyEnvelope:
    Decodable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public static let supportedType = "passphrase"
    public static let nonceByteCount = 12
    public static let ciphertextAndTagByteCount = 48

    public let id: String
    public let type: String
    public let kdf: AtlasVaultArgon2idParameters
    public let nonce: Data
    public let ciphertext: Data

    enum CodingKeys: String, CodingKey {
        case id
        case type
        case kdf
        case nonce
        case ciphertext
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let id = try container.decode(String.self, forKey: .id)
        let type = try container.decode(String.self, forKey: .type)
        let kdf = try container.decode(AtlasVaultArgon2idParameters.self, forKey: .kdf)
        let nonceText = try container.decode(String.self, forKey: .nonce)
        let ciphertextText = try container.decode(String.self, forKey: .ciphertext)

        guard !id.isEmpty else {
            throw DecodingError.dataCorruptedError(
                forKey: .id,
                in: container,
                debugDescription: "Invalid key-wrap identifier"
            )
        }
        guard type == Self.supportedType else {
            throw DecodingError.dataCorruptedError(
                forKey: .type,
                in: container,
                debugDescription: "Unsupported key-wrap type"
            )
        }
        guard
            let nonce = atlasVaultCanonicalBase64Decoded(nonceText),
            nonce.count == Self.nonceByteCount
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .nonce,
                in: container,
                debugDescription: "Invalid key-wrap nonce"
            )
        }
        guard
            let ciphertext = atlasVaultCanonicalBase64Decoded(ciphertextText),
            ciphertext.count == Self.ciphertextAndTagByteCount
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .ciphertext,
                in: container,
                debugDescription: "Invalid key-wrap ciphertext"
            )
        }

        self.id = id
        self.type = type
        self.kdf = kdf
        self.nonce = nonce
        self.ciphertext = ciphertext
    }

    public var description: String {
        "AtlasVaultWrappedKeyEnvelope(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultWrappedKeyMetadata:
    Decodable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public static let supportedFormat = "atlas-vault"
    public static let supportedVersion = 1

    public let format: String
    public let version: Int
    public let vaultID: String
    public let crypto: AtlasVaultKeyWrapCryptoSuite
    public let keyWraps: [AtlasVaultWrappedKeyEnvelope]

    enum CodingKeys: String, CodingKey {
        case format
        case version
        case vaultID = "vault_id"
        case crypto
        case keyWraps = "key_wraps"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let format = try container.decode(String.self, forKey: .format)
        let version = try container.decode(Int.self, forKey: .version)
        let vaultID = try container.decode(String.self, forKey: .vaultID)
        let crypto = try container.decode(AtlasVaultKeyWrapCryptoSuite.self, forKey: .crypto)
        let keyWraps = try container.decode([AtlasVaultWrappedKeyEnvelope].self, forKey: .keyWraps)

        guard format == Self.supportedFormat else {
            throw DecodingError.dataCorruptedError(
                forKey: .format,
                in: container,
                debugDescription: "Unsupported vault format"
            )
        }
        guard version == Self.supportedVersion else {
            throw DecodingError.dataCorruptedError(
                forKey: .version,
                in: container,
                debugDescription: "Unsupported vault version"
            )
        }
        guard !vaultID.isEmpty else {
            throw DecodingError.dataCorruptedError(
                forKey: .vaultID,
                in: container,
                debugDescription: "Invalid vault identifier"
            )
        }

        self.format = format
        self.version = version
        self.vaultID = vaultID
        self.crypto = crypto
        self.keyWraps = keyWraps
    }

    public var description: String {
        "AtlasVaultWrappedKeyMetadata(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}
