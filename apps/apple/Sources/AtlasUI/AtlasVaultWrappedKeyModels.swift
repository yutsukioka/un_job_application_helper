import Foundation

private func atlasVaultCanonicalBase64Decoded(_ value: String) -> Data? {
    guard let data = Data(base64Encoded: value, options: []) else {
        return nil
    }
    return data.base64EncodedString() == value ? data : nil
}

private struct AtlasVaultWrappedKeyDynamicCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int? = nil

    init?(stringValue: String) {
        self.stringValue = stringValue
    }

    init?(intValue: Int) {
        return nil
    }
}

private func atlasVaultRequireExactKeys(
    _ decoder: Decoder,
    expected: Set<String>,
    context: String
) throws {
    let container = try decoder.container(
        keyedBy: AtlasVaultWrappedKeyDynamicCodingKey.self
    )
    let actual = Set(container.allKeys.map(\.stringValue))
    guard actual == expected else {
        throw DecodingError.dataCorrupted(
            .init(
                codingPath: decoder.codingPath,
                debugDescription: "\(context) contains invalid fields"
            )
        )
    }
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

    public init(
        recordAEAD: String = "AES-256-GCM",
        kdf: String = "Argon2id",
        subkeyKDF: String = "HKDF-SHA256",
        keyWrapAEAD: String = "AES-256-GCM"
    ) throws {
        guard
            recordAEAD == "AES-256-GCM",
            kdf == "Argon2id",
            subkeyKDF == "HKDF-SHA256",
            keyWrapAEAD == "AES-256-GCM"
        else {
            throw AtlasVaultVersionedWrapModelError.invalidCryptoSuite
        }
        self.recordAEAD = recordAEAD
        self.kdf = kdf
        self.subkeyKDF = subkeyKDF
        self.keyWrapAEAD = keyWrapAEAD
    }

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

public enum AtlasVaultVersionedWrapModelError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible
{
    case invalidRecoveryWrap
    case invalidMetadata
    case invalidCryptoSuite

    public var description: String {
        switch self {
        case .invalidRecoveryWrap:
            return "Recovery key-wrap is invalid."
        case .invalidMetadata:
            return "Vault metadata is invalid."
        case .invalidCryptoSuite:
            return "Vault cryptographic configuration is invalid."
        }
    }
}

public struct AtlasVaultRecoveryWrapKDFParameters:
    Codable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public static let supportedAlgorithm = "HKDF-SHA256"
    public static let supportedInfo = "atlas-vault-recovery-wrap-v2"
    public static let saltByteCount = 32

    public let algorithm: String
    public let salt: Data
    public let info: String

    enum CodingKeys: String, CodingKey {
        case algorithm
        case salt
        case info
    }

    public init(
        algorithm: String = Self.supportedAlgorithm,
        salt: Data,
        info: String = Self.supportedInfo
    ) throws {
        guard
            algorithm == Self.supportedAlgorithm,
            salt.count == Self.saltByteCount,
            info == Self.supportedInfo
        else {
            throw AtlasVaultVersionedWrapModelError.invalidRecoveryWrap
        }
        self.algorithm = algorithm
        self.salt = salt
        self.info = info
    }

    public init(from decoder: Decoder) throws {
        try atlasVaultRequireExactKeys(
            decoder,
            expected: Set(CodingKeys.allCases.map(\.rawValue)),
            context: "Recovery key-wrap KDF"
        )
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let algorithm = try container.decode(String.self, forKey: .algorithm)
        let saltText = try container.decode(String.self, forKey: .salt)
        let info = try container.decode(String.self, forKey: .info)
        guard
            let salt = atlasVaultCanonicalBase64Decoded(saltText)
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .salt,
                in: container,
                debugDescription: "Invalid recovery key-wrap salt"
            )
        }
        try self.init(algorithm: algorithm, salt: salt, info: info)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(algorithm, forKey: .algorithm)
        try container.encode(salt.base64EncodedString(), forKey: .salt)
        try container.encode(info, forKey: .info)
    }

    public var description: String {
        "AtlasVaultRecoveryWrapKDFParameters(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

extension AtlasVaultRecoveryWrapKDFParameters.CodingKeys: CaseIterable {}

public struct AtlasVaultRecoveryWrappedKeyEnvelope:
    Codable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public static let supportedID = "primary-recovery-v2"
    public static let supportedType = "recovery_key"
    public static let supportedWrapVersion = 2
    public static let nonceByteCount = 12
    public static let ciphertextAndTagByteCount = 48

    public let id: String
    public let type: String
    public let wrapVersion: Int
    public let kdf: AtlasVaultRecoveryWrapKDFParameters
    public let nonce: Data
    public let ciphertext: Data

    enum CodingKeys: String, CodingKey, CaseIterable {
        case id
        case type
        case wrapVersion = "wrap_version"
        case kdf
        case nonce
        case ciphertext
    }

    public init(
        id: String = Self.supportedID,
        type: String = Self.supportedType,
        wrapVersion: Int = Self.supportedWrapVersion,
        kdf: AtlasVaultRecoveryWrapKDFParameters,
        nonce: Data,
        ciphertext: Data
    ) throws {
        guard
            id == Self.supportedID,
            type == Self.supportedType,
            wrapVersion == Self.supportedWrapVersion,
            nonce.count == Self.nonceByteCount,
            ciphertext.count == Self.ciphertextAndTagByteCount
        else {
            throw AtlasVaultVersionedWrapModelError.invalidRecoveryWrap
        }
        self.id = id
        self.type = type
        self.wrapVersion = wrapVersion
        self.kdf = kdf
        self.nonce = nonce
        self.ciphertext = ciphertext
    }

    public init(from decoder: Decoder) throws {
        try atlasVaultRequireExactKeys(
            decoder,
            expected: Set(CodingKeys.allCases.map(\.rawValue)),
            context: "Recovery key-wrap"
        )
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let id = try container.decode(String.self, forKey: .id)
        let type = try container.decode(String.self, forKey: .type)
        let wrapVersion = try container.decode(Int.self, forKey: .wrapVersion)
        let kdf = try container.decode(
            AtlasVaultRecoveryWrapKDFParameters.self,
            forKey: .kdf
        )
        let nonceText = try container.decode(String.self, forKey: .nonce)
        let ciphertextText = try container.decode(
            String.self,
            forKey: .ciphertext
        )
        guard
            let nonce = atlasVaultCanonicalBase64Decoded(nonceText),
            let ciphertext = atlasVaultCanonicalBase64Decoded(ciphertextText)
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .ciphertext,
                in: container,
                debugDescription: "Invalid recovery key-wrap data"
            )
        }
        try self.init(
            id: id,
            type: type,
            wrapVersion: wrapVersion,
            kdf: kdf,
            nonce: nonce,
            ciphertext: ciphertext
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(type, forKey: .type)
        try container.encode(wrapVersion, forKey: .wrapVersion)
        try container.encode(kdf, forKey: .kdf)
        try container.encode(nonce.base64EncodedString(), forKey: .nonce)
        try container.encode(
            ciphertext.base64EncodedString(),
            forKey: .ciphertext
        )
    }

    public var description: String {
        "AtlasVaultRecoveryWrappedKeyEnvelope(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultVersionedWrappedKey:
    Codable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case passphrase(AtlasVaultWrappedKeyEnvelope)
    case recoveryKey(AtlasVaultRecoveryWrappedKeyEnvelope)

    private enum ProbeKeys: String, CodingKey {
        case type
        case wrapVersion = "wrap_version"
    }

    private enum V1Keys: String, CodingKey, CaseIterable {
        case id
        case type
        case kdf
        case nonce
        case ciphertext
    }

    private enum V1KDFKeys: String, CodingKey {
        case algorithm
        case salt
        case memoryKiB = "memory_kib"
        case iterations
        case parallelism
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: ProbeKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        switch type {
        case AtlasVaultWrappedKeyEnvelope.supportedType:
            try atlasVaultRequireExactKeys(
                decoder,
                expected: Set(V1Keys.allCases.map(\.rawValue)),
                context: "Passphrase key-wrap"
            )
            self = .passphrase(
                try AtlasVaultWrappedKeyEnvelope(from: decoder)
            )
        case AtlasVaultRecoveryWrappedKeyEnvelope.supportedType:
            let wrapVersion = try container.decode(
                Int.self,
                forKey: .wrapVersion
            )
            let supportedVersion =
                AtlasVaultRecoveryWrappedKeyEnvelope.supportedWrapVersion
            guard wrapVersion == supportedVersion else {
                throw DecodingError.dataCorruptedError(
                    forKey: .wrapVersion,
                    in: container,
                    debugDescription: "Unsupported key-wrap version"
                )
            }
            self = .recoveryKey(
                try AtlasVaultRecoveryWrappedKeyEnvelope(from: decoder)
            )
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type,
                in: container,
                debugDescription: "Unsupported key-wrap type or version"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case let .passphrase(wrapped):
            var container = encoder.container(keyedBy: V1Keys.self)
            try container.encode(wrapped.id, forKey: .id)
            try container.encode(wrapped.type, forKey: .type)
            var kdf = container.nestedContainer(
                keyedBy: V1KDFKeys.self,
                forKey: .kdf
            )
            try kdf.encode(wrapped.kdf.algorithm, forKey: .algorithm)
            try kdf.encode(
                wrapped.kdf.salt.base64EncodedString(),
                forKey: .salt
            )
            try kdf.encode(wrapped.kdf.memoryKiB, forKey: .memoryKiB)
            try kdf.encode(wrapped.kdf.iterations, forKey: .iterations)
            try kdf.encode(wrapped.kdf.parallelism, forKey: .parallelism)
            try container.encode(
                wrapped.nonce.base64EncodedString(),
                forKey: .nonce
            )
            try container.encode(
                wrapped.ciphertext.base64EncodedString(),
                forKey: .ciphertext
            )
        case let .recoveryKey(wrapped):
            try wrapped.encode(to: encoder)
        }
    }

    public var recoveryKeyEnvelope:
        AtlasVaultRecoveryWrappedKeyEnvelope?
    {
        guard case let .recoveryKey(wrapped) = self else {
            return nil
        }
        return wrapped
    }

    public var description: String {
        "AtlasVaultVersionedWrappedKey(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultVersionedWrappedKeyMetadata:
    Codable,
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
    public let keyWraps: [AtlasVaultVersionedWrappedKey]

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case vaultID = "vault_id"
        case crypto
        case keyWraps = "key_wraps"
    }

    private enum CryptoKeys: String, CodingKey, CaseIterable {
        case recordAEAD = "record_aead"
        case kdf
        case subkeyKDF = "subkey_kdf"
        case keyWrapAEAD = "key_wrap_aead"
    }

    public init(
        format: String = Self.supportedFormat,
        version: Int = Self.supportedVersion,
        vaultID: String,
        crypto: AtlasVaultKeyWrapCryptoSuite,
        keyWraps: [AtlasVaultVersionedWrappedKey]
    ) throws {
        let recoveryIDs = keyWraps.compactMap {
            $0.recoveryKeyEnvelope?.id
        }
        guard
            format == Self.supportedFormat,
            version == Self.supportedVersion,
            (try? AtlasInjectedRootVaultPathLocator.validatedVaultID(
                vaultID
            )) == vaultID,
            Set(recoveryIDs).count == recoveryIDs.count
        else {
            throw AtlasVaultVersionedWrapModelError.invalidMetadata
        }
        self.format = format
        self.version = version
        self.vaultID = vaultID
        self.crypto = crypto
        self.keyWraps = keyWraps
    }

    public init(from decoder: Decoder) throws {
        try atlasVaultRequireExactKeys(
            decoder,
            expected: Set(CodingKeys.allCases.map(\.rawValue)),
            context: "Vault metadata"
        )
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let cryptoDecoder = try container.superDecoder(forKey: .crypto)
        try atlasVaultRequireExactKeys(
            cryptoDecoder,
            expected: Set(CryptoKeys.allCases.map(\.rawValue)),
            context: "Vault crypto suite"
        )
        try self.init(
            format: container.decode(String.self, forKey: .format),
            version: container.decode(Int.self, forKey: .version),
            vaultID: container.decode(String.self, forKey: .vaultID),
            crypto: AtlasVaultKeyWrapCryptoSuite(from: cryptoDecoder),
            keyWraps: container.decode(
                [AtlasVaultVersionedWrappedKey].self,
                forKey: .keyWraps
            )
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(vaultID, forKey: .vaultID)
        var cryptoContainer = container.nestedContainer(
            keyedBy: CryptoKeys.self,
            forKey: .crypto
        )
        try cryptoContainer.encode(crypto.recordAEAD, forKey: .recordAEAD)
        try cryptoContainer.encode(crypto.kdf, forKey: .kdf)
        try cryptoContainer.encode(crypto.subkeyKDF, forKey: .subkeyKDF)
        try cryptoContainer.encode(crypto.keyWrapAEAD, forKey: .keyWrapAEAD)
        try container.encode(keyWraps, forKey: .keyWraps)
    }

    public init(
        localStoreMetadata: [String: AtlasJSONValue]
    ) throws {
        let data = try JSONEncoder().encode(localStoreMetadata)
        self = try JSONDecoder().decode(Self.self, from: data)
    }

    public func localStoreMetadata() throws -> [String: AtlasJSONValue] {
        let data = try JSONEncoder().encode(self)
        return try JSONDecoder().decode(
            [String: AtlasJSONValue].self,
            from: data
        )
    }

    public var recoveryKeyWrap:
        AtlasVaultRecoveryWrappedKeyEnvelope?
    {
        keyWraps.compactMap(\.recoveryKeyEnvelope).first
    }

    public var description: String {
        "AtlasVaultVersionedWrappedKeyMetadata(<redacted>)"
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
