import CryptoKit
import Foundation

public enum AtlasVaultDeviceIdentityError: Error, Equatable, Sendable, CustomStringConvertible {
    case invalidIdentity

    public var description: String {
        "AtlasVault device identity is invalid."
    }
}

enum AtlasVaultDeviceIdentityValidation {
    static let descriptorFormat = "atlasvault-device-descriptor"
    static let signedDescriptorFormat = "atlasvault-signed-device-descriptor"
    static let secretFormat = "atlasvault-device-identity-secret"
    static let version = 1
    static let keyLength = 32
    static let signatureLength = 64
    static let deviceIDDomain = Data("atlasvault-device-id-v1:".utf8)
    static let descriptorSignatureDomain = Data(
        "atlasvault-device-descriptor-signature-v1:".utf8
    )

    static func exactKeys<K: CodingKey>(_ decoder: Decoder, expected: Set<String>, keyedBy _: K.Type) throws {
        let raw = try decoder.container(keyedBy: AtlasVaultAnyCodingKey.self)
        guard Set(raw.allKeys.map(\.stringValue)) == expected else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    static func canonicalBase64(_ value: String, length: Int) throws -> Data {
        guard
            let data = Data(base64Encoded: value, options: []),
            data.count == length,
            data.base64EncodedString() == value
        else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
        return data
    }

    static func strictUTCTimestamp(_ value: String) throws -> String {
        guard value.range(
            of: #"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"#,
            options: .regularExpression
        ) != nil else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        guard
            let date = formatter.date(from: value),
            formatter.string(from: date) == value
        else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
        return value
    }

    static func timestampDate(_ value: String) throws -> Date {
        _ = try strictUTCTimestamp(value)
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        guard let date = formatter.date(from: value) else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
        return date
    }

    static func canonicalData<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        do {
            return try encoder.encode(value)
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    static func constantTimeEqual(_ lhs: Data, _ rhs: Data) -> Bool {
        guard lhs.count == rhs.count else { return false }
        var difference: UInt8 = 0
        for (left, right) in zip(lhs, rhs) {
            difference |= left ^ right
        }
        return difference == 0
    }

    static func constantTimeEqual(_ lhs: String, _ rhs: String) -> Bool {
        constantTimeEqual(Data(lhs.utf8), Data(rhs.utf8))
    }

    static func lowercaseHex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}

struct AtlasVaultAnyCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init?(stringValue: String) {
        self.stringValue = stringValue
        intValue = nil
    }

    init?(intValue: Int) {
        stringValue = String(intValue)
        self.intValue = intValue
    }
}

public struct AtlasVaultDeviceDescriptor: Codable, Equatable, Sendable, CustomStringConvertible {
    public let format: String
    public let version: Int
    public let deviceID: String
    public let signingPublicKey: Data
    public let agreementPublicKey: Data
    public let createdAt: String
    public let keyEpoch: Int

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case deviceID = "device_id"
        case signingPublicKey = "signing_public_key"
        case agreementPublicKey = "agreement_public_key"
        case createdAt = "created_at"
        case keyEpoch = "key_epoch"
    }

    public init(
        format: String = "atlasvault-device-descriptor",
        version: Int = 1,
        deviceID: String,
        signingPublicKey: Data,
        agreementPublicKey: Data,
        createdAt: String,
        keyEpoch: Int
    ) throws {
        guard
            format == AtlasVaultDeviceIdentityValidation.descriptorFormat,
            version == AtlasVaultDeviceIdentityValidation.version,
            signingPublicKey.count == AtlasVaultDeviceIdentityValidation.keyLength,
            agreementPublicKey.count == AtlasVaultDeviceIdentityValidation.keyLength,
            keyEpoch > 0,
            AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                deviceID,
                try AtlasVaultDeviceIdentity.deriveDeviceID(
                    signingPublicKey: signingPublicKey,
                    agreementPublicKey: agreementPublicKey
                )
            )
        else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
        self.format = format
        self.version = version
        self.deviceID = deviceID
        self.signingPublicKey = signingPublicKey
        self.agreementPublicKey = agreementPublicKey
        self.createdAt = try AtlasVaultDeviceIdentityValidation.strictUTCTimestamp(createdAt)
        self.keyEpoch = keyEpoch
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultDeviceIdentityValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                deviceID: container.decode(String.self, forKey: .deviceID),
                signingPublicKey: AtlasVaultDeviceIdentityValidation.canonicalBase64(
                    container.decode(String.self, forKey: .signingPublicKey),
                    length: AtlasVaultDeviceIdentityValidation.keyLength
                ),
                agreementPublicKey: AtlasVaultDeviceIdentityValidation.canonicalBase64(
                    container.decode(String.self, forKey: .agreementPublicKey),
                    length: AtlasVaultDeviceIdentityValidation.keyLength
                ),
                createdAt: container.decode(String.self, forKey: .createdAt),
                keyEpoch: container.decode(Int.self, forKey: .keyEpoch)
            )
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(deviceID, forKey: .deviceID)
        try container.encode(signingPublicKey.base64EncodedString(), forKey: .signingPublicKey)
        try container.encode(agreementPublicKey.base64EncodedString(), forKey: .agreementPublicKey)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(keyEpoch, forKey: .keyEpoch)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            return try JSONDecoder().decode(Self.self, from: data)
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultDeviceIdentityValidation.canonicalData(self)
    }

    public var description: String {
        "AtlasVaultDeviceDescriptor(\(deviceID))"
    }
}

public struct AtlasVaultSignedDeviceDescriptor: Codable, Equatable, Sendable, CustomStringConvertible {
    public let format: String
    public let version: Int
    public let descriptor: AtlasVaultDeviceDescriptor
    public let signature: Data

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case descriptor
        case signature
    }

    public init(
        format: String = "atlasvault-signed-device-descriptor",
        version: Int = 1,
        descriptor: AtlasVaultDeviceDescriptor,
        signature: Data
    ) throws {
        guard
            format == AtlasVaultDeviceIdentityValidation.signedDescriptorFormat,
            version == AtlasVaultDeviceIdentityValidation.version,
            signature.count == AtlasVaultDeviceIdentityValidation.signatureLength
        else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
        self.format = format
        self.version = version
        self.descriptor = descriptor
        self.signature = signature
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultDeviceIdentityValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                descriptor: container.decode(AtlasVaultDeviceDescriptor.self, forKey: .descriptor),
                signature: AtlasVaultDeviceIdentityValidation.canonicalBase64(
                    container.decode(String.self, forKey: .signature),
                    length: AtlasVaultDeviceIdentityValidation.signatureLength
                )
            )
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(descriptor, forKey: .descriptor)
        try container.encode(signature.base64EncodedString(), forKey: .signature)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            return try JSONDecoder().decode(Self.self, from: data)
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultDeviceIdentityValidation.canonicalData(self)
    }

    public func verifiedDescriptor() throws -> AtlasVaultDeviceDescriptor {
        do {
            let publicKey = try Curve25519.Signing.PublicKey(
                rawRepresentation: descriptor.signingPublicKey
            )
            let input = AtlasVaultDeviceIdentityValidation.descriptorSignatureDomain
                + (try descriptor.canonicalData())
            guard publicKey.isValidSignature(signature, for: input) else {
                throw AtlasVaultDeviceIdentityError.invalidIdentity
            }
            return descriptor
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public var description: String {
        "AtlasVaultSignedDeviceDescriptor(<redacted>)"
    }
}

public struct AtlasVaultDeviceIdentitySecret: Codable, Sendable, CustomStringConvertible {
    public let format: String
    public let version: Int
    public let deviceID: String
    public let createdAt: String
    public let keyEpoch: Int
    private let signingPrivateKey: Data
    private let agreementPrivateKey: Data

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case deviceID = "device_id"
        case createdAt = "created_at"
        case keyEpoch = "key_epoch"
        case signingPrivateKey = "signing_private_key"
        case agreementPrivateKey = "agreement_private_key"
    }

    init(
        format: String = AtlasVaultDeviceIdentityValidation.secretFormat,
        version: Int = AtlasVaultDeviceIdentityValidation.version,
        deviceID: String,
        createdAt: String,
        keyEpoch: Int,
        signingPrivateKey: Data,
        agreementPrivateKey: Data
    ) throws {
        guard
            format == AtlasVaultDeviceIdentityValidation.secretFormat,
            version == AtlasVaultDeviceIdentityValidation.version,
            signingPrivateKey.count == AtlasVaultDeviceIdentityValidation.keyLength,
            agreementPrivateKey.count == AtlasVaultDeviceIdentityValidation.keyLength,
            keyEpoch > 0
        else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
        let identity = try AtlasVaultDeviceIdentity(
            signingPrivateSeed: signingPrivateKey,
            agreementPrivateKey: agreementPrivateKey,
            createdAt: createdAt,
            keyEpoch: keyEpoch,
            expectedDeviceID: deviceID
        )
        self.format = format
        self.version = version
        self.deviceID = identity.deviceID
        self.createdAt = identity.descriptor.createdAt
        self.keyEpoch = identity.descriptor.keyEpoch
        self.signingPrivateKey = signingPrivateKey
        self.agreementPrivateKey = agreementPrivateKey
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultDeviceIdentityValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                deviceID: container.decode(String.self, forKey: .deviceID),
                createdAt: container.decode(String.self, forKey: .createdAt),
                keyEpoch: container.decode(Int.self, forKey: .keyEpoch),
                signingPrivateKey: AtlasVaultDeviceIdentityValidation.canonicalBase64(
                    container.decode(String.self, forKey: .signingPrivateKey),
                    length: AtlasVaultDeviceIdentityValidation.keyLength
                ),
                agreementPrivateKey: AtlasVaultDeviceIdentityValidation.canonicalBase64(
                    container.decode(String.self, forKey: .agreementPrivateKey),
                    length: AtlasVaultDeviceIdentityValidation.keyLength
                )
            )
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(deviceID, forKey: .deviceID)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(keyEpoch, forKey: .keyEpoch)
        try container.encode(signingPrivateKey.base64EncodedString(), forKey: .signingPrivateKey)
        try container.encode(agreementPrivateKey.base64EncodedString(), forKey: .agreementPrivateKey)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            return try JSONDecoder().decode(Self.self, from: data)
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultDeviceIdentityValidation.canonicalData(self)
    }

    public func loadIdentity() throws -> AtlasVaultDeviceIdentity {
        try AtlasVaultDeviceIdentity(
            signingPrivateSeed: signingPrivateKey,
            agreementPrivateKey: agreementPrivateKey,
            createdAt: createdAt,
            keyEpoch: keyEpoch,
            expectedDeviceID: deviceID
        )
    }

    public var description: String {
        "AtlasVaultDeviceIdentitySecret(<redacted>)"
    }
}

public struct AtlasVaultDeviceIdentity: Sendable, CustomStringConvertible {
    private let signingPrivateSeed: Data
    private let agreementPrivateKey: Data
    public let descriptor: AtlasVaultDeviceDescriptor

    public var deviceID: String { descriptor.deviceID }
    public var signingPublicKey: Data { descriptor.signingPublicKey }
    public var agreementPublicKey: Data { descriptor.agreementPublicKey }

    public init(
        signingPrivateSeed: Data,
        agreementPrivateKey: Data,
        createdAt: String,
        keyEpoch: Int = 1,
        expectedDeviceID: String? = nil
    ) throws {
        do {
            guard
                signingPrivateSeed.count == AtlasVaultDeviceIdentityValidation.keyLength,
                agreementPrivateKey.count == AtlasVaultDeviceIdentityValidation.keyLength,
                keyEpoch > 0
            else {
                throw AtlasVaultDeviceIdentityError.invalidIdentity
            }
            let signing = try Curve25519.Signing.PrivateKey(
                rawRepresentation: signingPrivateSeed
            )
            let agreement = try Curve25519.KeyAgreement.PrivateKey(
                rawRepresentation: agreementPrivateKey
            )
            let derivedID = try Self.deriveDeviceID(
                signingPublicKey: signing.publicKey.rawRepresentation,
                agreementPublicKey: agreement.publicKey.rawRepresentation
            )
            if let expectedDeviceID,
               !AtlasVaultDeviceIdentityValidation.constantTimeEqual(expectedDeviceID, derivedID)
            {
                throw AtlasVaultDeviceIdentityError.invalidIdentity
            }
            descriptor = try AtlasVaultDeviceDescriptor(
                deviceID: derivedID,
                signingPublicKey: signing.publicKey.rawRepresentation,
                agreementPublicKey: agreement.publicKey.rawRepresentation,
                createdAt: createdAt,
                keyEpoch: keyEpoch
            )
            self.signingPrivateSeed = signingPrivateSeed
            self.agreementPrivateKey = agreementPrivateKey
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public static func generate(createdAt: String? = nil, keyEpoch: Int = 1) throws -> Self {
        do {
            let signing = Curve25519.Signing.PrivateKey()
            let agreement = Curve25519.KeyAgreement.PrivateKey()
            return try Self(
                signingPrivateSeed: signing.rawRepresentation,
                agreementPrivateKey: agreement.rawRepresentation,
                createdAt: createdAt ?? utcNowSeconds(),
                keyEpoch: keyEpoch
            )
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public static func deriveDeviceID(
        signingPublicKey: Data,
        agreementPublicKey: Data
    ) throws -> String {
        guard
            signingPublicKey.count == AtlasVaultDeviceIdentityValidation.keyLength,
            agreementPublicKey.count == AtlasVaultDeviceIdentityValidation.keyLength
        else {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
        let digest = SHA256.hash(
            data: AtlasVaultDeviceIdentityValidation.deviceIDDomain
                + signingPublicKey
                + agreementPublicKey
        )
        return "avd1-" + AtlasVaultDeviceIdentityValidation.lowercaseHex(Data(digest))
    }

    func sign(_ data: Data) throws -> Data {
        do {
            return try Curve25519.Signing.PrivateKey(
                rawRepresentation: signingPrivateSeed
            ).signature(for: data)
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public func signDescriptor() throws -> AtlasVaultSignedDeviceDescriptor {
        try AtlasVaultSignedDeviceDescriptor(
            descriptor: descriptor,
            signature: sign(
                AtlasVaultDeviceIdentityValidation.descriptorSignatureDomain
                    + descriptor.canonicalData()
            )
        )
    }

    func sharedSecret(with remotePublicKey: Data) throws -> Data {
        do {
            guard remotePublicKey.count == AtlasVaultDeviceIdentityValidation.keyLength else {
                throw AtlasVaultDeviceIdentityError.invalidIdentity
            }
            let privateKey = try Curve25519.KeyAgreement.PrivateKey(
                rawRepresentation: agreementPrivateKey
            )
            let publicKey = try Curve25519.KeyAgreement.PublicKey(
                rawRepresentation: remotePublicKey
            )
            let shared = try privateKey.sharedSecretFromKeyAgreement(with: publicKey)
            let data = shared.withUnsafeBytes { Data($0) }
            guard
                data.count == AtlasVaultDeviceIdentityValidation.keyLength,
                !AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                    data,
                    Data(repeating: 0, count: AtlasVaultDeviceIdentityValidation.keyLength)
                )
            else {
                throw AtlasVaultDeviceIdentityError.invalidIdentity
            }
            return data
        } catch {
            throw AtlasVaultDeviceIdentityError.invalidIdentity
        }
    }

    public func secretBundle() throws -> AtlasVaultDeviceIdentitySecret {
        try AtlasVaultDeviceIdentitySecret(
            deviceID: deviceID,
            createdAt: descriptor.createdAt,
            keyEpoch: descriptor.keyEpoch,
            signingPrivateKey: signingPrivateSeed,
            agreementPrivateKey: agreementPrivateKey
        )
    }

    public var description: String {
        "AtlasVaultDeviceIdentity(<redacted>)"
    }

    private static func utcNowSeconds() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return formatter.string(from: Date())
    }
}
