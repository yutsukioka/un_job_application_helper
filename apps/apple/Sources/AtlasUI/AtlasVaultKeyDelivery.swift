import CryptoKit
import CoreFoundation
import Foundation

public enum AtlasVaultKeyDeliveryError: Error, Equatable, Sendable, CustomStringConvertible {
    case verificationFailed

    public var description: String {
        "AtlasVault pairing key delivery verification failed."
    }
}

private enum AtlasVaultKeyDeliveryValidation {
    static let requestFormat = "atlasvault-pairing-key-request"
    static let signedRequestFormat = "atlasvault-signed-pairing-key-request"
    static let bootstrapFormat = "atlasvault-pairing-bootstrap"
    static let deliveryFormat = "atlasvault-vault-key-delivery"
    static let signedDeliveryFormat = "atlasvault-signed-vault-key-delivery"
    static let acknowledgementFormat = "atlasvault-pairing-acknowledgement"
    static let signedAcknowledgementFormat = "atlasvault-signed-pairing-acknowledgement"
    static let artifactFormat = "atlasvault-pairing-artifact"
    static let deliveryAADFormat = "atlasvault-vault-key-delivery-aad"
    static let version = 1
    static let keyLength = 32
    static let requestNonceLength = 32
    static let nonceLength = 12
    static let ciphertextLength = 48
    static let signatureLength = 64
    static let maximumRequestLifetime: TimeInterval = 1_800
    static let maximumClockSkew: TimeInterval = 120
    static let sasDomain = Data("atlasvault-pairing-sas-v1:".utf8)
    static let requestSignatureDomain = Data(
        "atlasvault-pairing-key-request-signature-v1:".utf8
    )
    static let deliverySignatureDomain = Data(
        "atlasvault-vault-key-delivery-signature-v1:".utf8
    )
    static let acknowledgementSignatureDomain = Data(
        "atlasvault-pairing-acknowledgement-signature-v1:".utf8
    )
    static let deliveryInfo = Data("atlasvault-vault-key-delivery-v1".utf8)

    static func fail() -> AtlasVaultKeyDeliveryError { .verificationFailed }

    static func exactKeys<K: CodingKey>(
        _ decoder: Decoder,
        expected: Set<String>,
        keyedBy: K.Type
    ) throws {
        do {
            try AtlasVaultPairingValidation.exactKeys(
                decoder,
                expected: expected,
                keyedBy: keyedBy
            )
        } catch {
            throw fail()
        }
    }

    static func canonicalData<T: Encodable>(_ value: T) throws -> Data {
        do {
            return try AtlasVaultPairingValidation.canonicalData(value)
        } catch {
            throw fail()
        }
    }

    static func uuid(_ value: String) throws -> String {
        do { return try AtlasVaultPairingValidation.canonicalUUID(value) }
        catch { throw fail() }
    }

    static func sha256(_ value: String) throws -> String {
        do { return try AtlasVaultPairingValidation.lowercaseHex(value) }
        catch { throw fail() }
    }

    static func deviceID(_ value: String) throws -> String {
        guard value.range(
            of: #"^avd1-[0-9a-f]{64}$"#,
            options: .regularExpression
        ) != nil else { throw fail() }
        return value
    }

    static func timestamp(_ value: String) throws -> String {
        do { return try AtlasVaultPairingValidation.timestamp(value) }
        catch { throw fail() }
    }

    static func date(_ value: String) throws -> Date {
        do { return try AtlasVaultPairingValidation.date(value) }
        catch { throw fail() }
    }

    static func base64(_ value: String, length: Int) throws -> Data {
        do { return try AtlasVaultPairingValidation.canonicalBase64(value, length: length) }
        catch { throw fail() }
    }

    static func vaultID(_ value: String) throws -> String {
        guard (try? AtlasInjectedRootVaultPathLocator.validatedVaultID(value)) == value else {
            throw fail()
        }
        return value
    }

    static func sha256Hex(_ data: Data) -> String {
        AtlasVaultDeviceIdentityValidation.lowercaseHex(Data(SHA256.hash(data: data)))
    }

    static func verify(
        descriptor: AtlasVaultSignedDeviceDescriptor,
        signature: Data,
        domain: Data,
        payload: Data
    ) throws -> AtlasVaultDeviceDescriptor {
        do {
            let verified = try descriptor.verifiedDescriptor()
            let publicKey = try Curve25519.Signing.PublicKey(
                rawRepresentation: verified.signingPublicKey
            )
            guard publicKey.isValidSignature(signature, for: domain + payload) else {
                throw fail()
            }
            return verified
        } catch {
            throw fail()
        }
    }
}

public struct AtlasVaultPairingKeyRequest: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let requestID: String
    public let transcriptSHA256: String
    public let inviterDeviceID: String
    public let inviteeDeviceID: String
    public let inviteeEphemeralPublicKey: Data
    public let nonce: Data
    public let issuedAt: String
    public let expiresAt: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case requestID = "request_id"
        case transcriptSHA256 = "transcript_sha256"
        case inviterDeviceID = "inviter_device_id"
        case inviteeDeviceID = "invitee_device_id"
        case inviteeEphemeralPublicKey = "invitee_ephemeral_public_key"
        case nonce
        case issuedAt = "issued_at"
        case expiresAt = "expires_at"
    }

    public init(
        format: String = "atlasvault-pairing-key-request",
        version: Int = 1,
        requestID: String,
        transcriptSHA256: String,
        inviterDeviceID: String,
        inviteeDeviceID: String,
        inviteeEphemeralPublicKey: Data,
        nonce: Data,
        issuedAt: String,
        expiresAt: String
    ) throws {
        let issued = try AtlasVaultKeyDeliveryValidation.date(issuedAt)
        let expires = try AtlasVaultKeyDeliveryValidation.date(expiresAt)
        guard
            format == AtlasVaultKeyDeliveryValidation.requestFormat,
            version == AtlasVaultKeyDeliveryValidation.version,
            inviterDeviceID != inviteeDeviceID,
            inviteeEphemeralPublicKey.count == AtlasVaultKeyDeliveryValidation.keyLength,
            nonce.count == AtlasVaultKeyDeliveryValidation.requestNonceLength,
            expires.timeIntervalSince(issued) > 0,
            expires.timeIntervalSince(issued)
                <= AtlasVaultKeyDeliveryValidation.maximumRequestLifetime
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        self.format = format
        self.version = version
        self.requestID = try AtlasVaultKeyDeliveryValidation.uuid(requestID)
        self.transcriptSHA256 = try AtlasVaultKeyDeliveryValidation.sha256(
            transcriptSHA256
        )
        self.inviterDeviceID = try AtlasVaultKeyDeliveryValidation.deviceID(
            inviterDeviceID
        )
        self.inviteeDeviceID = try AtlasVaultKeyDeliveryValidation.deviceID(
            inviteeDeviceID
        )
        self.inviteeEphemeralPublicKey = inviteeEphemeralPublicKey
        self.nonce = nonce
        self.issuedAt = issuedAt
        self.expiresAt = expiresAt
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultKeyDeliveryValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                requestID: container.decode(String.self, forKey: .requestID),
                transcriptSHA256: container.decode(String.self, forKey: .transcriptSHA256),
                inviterDeviceID: container.decode(String.self, forKey: .inviterDeviceID),
                inviteeDeviceID: container.decode(String.self, forKey: .inviteeDeviceID),
                inviteeEphemeralPublicKey: AtlasVaultKeyDeliveryValidation.base64(
                    container.decode(String.self, forKey: .inviteeEphemeralPublicKey),
                    length: AtlasVaultKeyDeliveryValidation.keyLength
                ),
                nonce: AtlasVaultKeyDeliveryValidation.base64(
                    container.decode(String.self, forKey: .nonce),
                    length: AtlasVaultKeyDeliveryValidation.requestNonceLength
                ),
                issuedAt: container.decode(String.self, forKey: .issuedAt),
                expiresAt: container.decode(String.self, forKey: .expiresAt)
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(requestID, forKey: .requestID)
        try container.encode(transcriptSHA256, forKey: .transcriptSHA256)
        try container.encode(inviterDeviceID, forKey: .inviterDeviceID)
        try container.encode(inviteeDeviceID, forKey: .inviteeDeviceID)
        try container.encode(
            inviteeEphemeralPublicKey.base64EncodedString(),
            forKey: .inviteeEphemeralPublicKey
        )
        try container.encode(nonce.base64EncodedString(), forKey: .nonce)
        try container.encode(issuedAt, forKey: .issuedAt)
        try container.encode(expiresAt, forKey: .expiresAt)
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultKeyDeliveryValidation.canonicalData(self)
    }
}

public struct AtlasVaultSignedPairingKeyRequest: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let request: AtlasVaultPairingKeyRequest
    public let invitee: AtlasVaultSignedDeviceDescriptor
    public let signature: Data

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case request
        case invitee
        case signature
    }

    public init(
        format: String = "atlasvault-signed-pairing-key-request",
        version: Int = 1,
        request: AtlasVaultPairingKeyRequest,
        invitee: AtlasVaultSignedDeviceDescriptor,
        signature: Data
    ) throws {
        guard
            format == AtlasVaultKeyDeliveryValidation.signedRequestFormat,
            version == AtlasVaultKeyDeliveryValidation.version,
            signature.count == AtlasVaultKeyDeliveryValidation.signatureLength,
            request.inviteeDeviceID == invitee.descriptor.deviceID
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        self.format = format
        self.version = version
        self.request = request
        self.invitee = invitee
        self.signature = signature
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultKeyDeliveryValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                request: container.decode(AtlasVaultPairingKeyRequest.self, forKey: .request),
                invitee: container.decode(AtlasVaultSignedDeviceDescriptor.self, forKey: .invitee),
                signature: AtlasVaultKeyDeliveryValidation.base64(
                    container.decode(String.self, forKey: .signature),
                    length: AtlasVaultKeyDeliveryValidation.signatureLength
                )
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(request, forKey: .request)
        try container.encode(invitee, forKey: .invitee)
        try container.encode(signature.base64EncodedString(), forKey: .signature)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            let value = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try value.canonicalData(), data
            ) else { throw AtlasVaultKeyDeliveryValidation.fail() }
            return value
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultKeyDeliveryValidation.canonicalData(self)
    }

    public func sha256Hex() throws -> String {
        AtlasVaultKeyDeliveryValidation.sha256Hex(try canonicalData())
    }
}

public struct AtlasVaultPairingBootstrap: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let snapshotID: String
    public let createdAt: String
    public let vaultMetadata: AtlasVaultVersionedWrappedKeyMetadata
    public let records: [AtlasVaultEncryptedRecordEnvelope]

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case snapshotID = "snapshot_id"
        case createdAt = "created_at"
        case vaultMetadata = "vault_metadata"
        case records
    }

    public init(
        format: String = "atlasvault-pairing-bootstrap",
        version: Int = 1,
        snapshotID: String,
        createdAt: String,
        vaultMetadata: AtlasVaultVersionedWrappedKeyMetadata,
        records: [AtlasVaultEncryptedRecordEnvelope]
    ) throws {
        guard
            format == AtlasVaultKeyDeliveryValidation.bootstrapFormat,
            version == AtlasVaultKeyDeliveryValidation.version,
            Set(records.map(\.id)).count == records.count
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        self.format = format
        self.version = version
        self.snapshotID = try AtlasVaultKeyDeliveryValidation.uuid(snapshotID)
        self.createdAt = try AtlasVaultKeyDeliveryValidation.timestamp(createdAt)
        self.vaultMetadata = vaultMetadata
        self.records = records
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultKeyDeliveryValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                snapshotID: container.decode(String.self, forKey: .snapshotID),
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
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            let value = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try value.canonicalData(), data
            ) else { throw AtlasVaultKeyDeliveryValidation.fail() }
            return value
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultKeyDeliveryValidation.canonicalData(self)
    }

    public func sha256Hex() throws -> String {
        AtlasVaultKeyDeliveryValidation.sha256Hex(try canonicalData())
    }
}

public struct AtlasVaultVaultKeyDelivery: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let deliveryID: String
    public let transcriptSHA256: String
    public let inviterDeviceID: String
    public let inviteeDeviceID: String
    public let requestSHA256: String
    public let vaultID: String
    public let keyEpoch: Int
    public let bootstrapSHA256: String
    public let inviterEphemeralPublicKey: Data
    public let nonce: Data
    public let ciphertext: Data
    public let expiresAt: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case deliveryID = "delivery_id"
        case transcriptSHA256 = "transcript_sha256"
        case inviterDeviceID = "inviter_device_id"
        case inviteeDeviceID = "invitee_device_id"
        case requestSHA256 = "request_sha256"
        case vaultID = "vault_id"
        case keyEpoch = "key_epoch"
        case bootstrapSHA256 = "bootstrap_sha256"
        case inviterEphemeralPublicKey = "inviter_ephemeral_public_key"
        case nonce
        case ciphertext
        case expiresAt = "expires_at"
    }

    public init(
        format: String = "atlasvault-vault-key-delivery",
        version: Int = 1,
        deliveryID: String,
        transcriptSHA256: String,
        inviterDeviceID: String,
        inviteeDeviceID: String,
        requestSHA256: String,
        vaultID: String,
        keyEpoch: Int,
        bootstrapSHA256: String,
        inviterEphemeralPublicKey: Data,
        nonce: Data,
        ciphertext: Data,
        expiresAt: String
    ) throws {
        guard
            format == AtlasVaultKeyDeliveryValidation.deliveryFormat,
            version == AtlasVaultKeyDeliveryValidation.version,
            inviterDeviceID != inviteeDeviceID,
            keyEpoch > 0,
            keyEpoch <= AtlasVaultDeviceIdentityValidation.maximumKeyEpoch,
            inviterEphemeralPublicKey.count == AtlasVaultKeyDeliveryValidation.keyLength,
            nonce.count == AtlasVaultKeyDeliveryValidation.nonceLength,
            ciphertext.count == AtlasVaultKeyDeliveryValidation.ciphertextLength
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        self.format = format
        self.version = version
        self.deliveryID = try AtlasVaultKeyDeliveryValidation.uuid(deliveryID)
        self.transcriptSHA256 = try AtlasVaultKeyDeliveryValidation.sha256(
            transcriptSHA256
        )
        self.inviterDeviceID = try AtlasVaultKeyDeliveryValidation.deviceID(
            inviterDeviceID
        )
        self.inviteeDeviceID = try AtlasVaultKeyDeliveryValidation.deviceID(
            inviteeDeviceID
        )
        self.requestSHA256 = try AtlasVaultKeyDeliveryValidation.sha256(requestSHA256)
        self.vaultID = try AtlasVaultKeyDeliveryValidation.vaultID(vaultID)
        self.keyEpoch = keyEpoch
        self.bootstrapSHA256 = try AtlasVaultKeyDeliveryValidation.sha256(
            bootstrapSHA256
        )
        self.inviterEphemeralPublicKey = inviterEphemeralPublicKey
        self.nonce = nonce
        self.ciphertext = ciphertext
        self.expiresAt = try AtlasVaultKeyDeliveryValidation.timestamp(expiresAt)
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultKeyDeliveryValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                deliveryID: container.decode(String.self, forKey: .deliveryID),
                transcriptSHA256: container.decode(String.self, forKey: .transcriptSHA256),
                inviterDeviceID: container.decode(String.self, forKey: .inviterDeviceID),
                inviteeDeviceID: container.decode(String.self, forKey: .inviteeDeviceID),
                requestSHA256: container.decode(String.self, forKey: .requestSHA256),
                vaultID: container.decode(String.self, forKey: .vaultID),
                keyEpoch: container.decode(Int.self, forKey: .keyEpoch),
                bootstrapSHA256: container.decode(String.self, forKey: .bootstrapSHA256),
                inviterEphemeralPublicKey: AtlasVaultKeyDeliveryValidation.base64(
                    container.decode(String.self, forKey: .inviterEphemeralPublicKey),
                    length: AtlasVaultKeyDeliveryValidation.keyLength
                ),
                nonce: AtlasVaultKeyDeliveryValidation.base64(
                    container.decode(String.self, forKey: .nonce),
                    length: AtlasVaultKeyDeliveryValidation.nonceLength
                ),
                ciphertext: AtlasVaultKeyDeliveryValidation.base64(
                    container.decode(String.self, forKey: .ciphertext),
                    length: AtlasVaultKeyDeliveryValidation.ciphertextLength
                ),
                expiresAt: container.decode(String.self, forKey: .expiresAt)
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(deliveryID, forKey: .deliveryID)
        try container.encode(transcriptSHA256, forKey: .transcriptSHA256)
        try container.encode(inviterDeviceID, forKey: .inviterDeviceID)
        try container.encode(inviteeDeviceID, forKey: .inviteeDeviceID)
        try container.encode(requestSHA256, forKey: .requestSHA256)
        try container.encode(vaultID, forKey: .vaultID)
        try container.encode(keyEpoch, forKey: .keyEpoch)
        try container.encode(bootstrapSHA256, forKey: .bootstrapSHA256)
        try container.encode(
            inviterEphemeralPublicKey.base64EncodedString(),
            forKey: .inviterEphemeralPublicKey
        )
        try container.encode(nonce.base64EncodedString(), forKey: .nonce)
        try container.encode(ciphertext.base64EncodedString(), forKey: .ciphertext)
        try container.encode(expiresAt, forKey: .expiresAt)
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultKeyDeliveryValidation.canonicalData(self)
    }

    public func aad() throws -> Data {
        let value: [String: Any] = [
            "format": AtlasVaultKeyDeliveryValidation.deliveryAADFormat,
            "version": AtlasVaultKeyDeliveryValidation.version,
            "delivery_id": deliveryID,
            "transcript_sha256": transcriptSHA256,
            "inviter_device_id": inviterDeviceID,
            "invitee_device_id": inviteeDeviceID,
            "request_sha256": requestSHA256,
            "vault_id": vaultID,
            "key_epoch": keyEpoch,
            "bootstrap_sha256": bootstrapSHA256,
            "expires_at": expiresAt,
        ]
        guard JSONSerialization.isValidJSONObject(value) else {
            throw AtlasVaultKeyDeliveryValidation.fail()
        }
        return try JSONSerialization.data(
            withJSONObject: value,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
    }
}

public struct AtlasVaultSignedVaultKeyDelivery: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let delivery: AtlasVaultVaultKeyDelivery
    public let inviter: AtlasVaultSignedDeviceDescriptor
    public let signature: Data

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case delivery
        case inviter
        case signature
    }

    public init(
        format: String = "atlasvault-signed-vault-key-delivery",
        version: Int = 1,
        delivery: AtlasVaultVaultKeyDelivery,
        inviter: AtlasVaultSignedDeviceDescriptor,
        signature: Data
    ) throws {
        guard
            format == AtlasVaultKeyDeliveryValidation.signedDeliveryFormat,
            version == AtlasVaultKeyDeliveryValidation.version,
            signature.count == AtlasVaultKeyDeliveryValidation.signatureLength,
            delivery.inviterDeviceID == inviter.descriptor.deviceID
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        self.format = format
        self.version = version
        self.delivery = delivery
        self.inviter = inviter
        self.signature = signature
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultKeyDeliveryValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                delivery: container.decode(AtlasVaultVaultKeyDelivery.self, forKey: .delivery),
                inviter: container.decode(AtlasVaultSignedDeviceDescriptor.self, forKey: .inviter),
                signature: AtlasVaultKeyDeliveryValidation.base64(
                    container.decode(String.self, forKey: .signature),
                    length: AtlasVaultKeyDeliveryValidation.signatureLength
                )
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(delivery, forKey: .delivery)
        try container.encode(inviter, forKey: .inviter)
        try container.encode(signature.base64EncodedString(), forKey: .signature)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            let value = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try value.canonicalData(), data
            ) else { throw AtlasVaultKeyDeliveryValidation.fail() }
            return value
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultKeyDeliveryValidation.canonicalData(self)
    }
}

public struct AtlasVaultPairingAcknowledgement: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let acknowledgementID: String
    public let deliveryID: String
    public let transcriptSHA256: String
    public let inviterDeviceID: String
    public let inviteeDeviceID: String
    public let vaultID: String
    public let keyEpoch: Int
    public let bootstrapSHA256: String
    public let installedAt: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case acknowledgementID = "acknowledgement_id"
        case deliveryID = "delivery_id"
        case transcriptSHA256 = "transcript_sha256"
        case inviterDeviceID = "inviter_device_id"
        case inviteeDeviceID = "invitee_device_id"
        case vaultID = "vault_id"
        case keyEpoch = "key_epoch"
        case bootstrapSHA256 = "bootstrap_sha256"
        case installedAt = "installed_at"
    }

    public init(
        format: String = "atlasvault-pairing-acknowledgement",
        version: Int = 1,
        acknowledgementID: String,
        deliveryID: String,
        transcriptSHA256: String,
        inviterDeviceID: String,
        inviteeDeviceID: String,
        vaultID: String,
        keyEpoch: Int,
        bootstrapSHA256: String,
        installedAt: String
    ) throws {
        guard
            format == AtlasVaultKeyDeliveryValidation.acknowledgementFormat,
            version == AtlasVaultKeyDeliveryValidation.version,
            inviterDeviceID != inviteeDeviceID,
            keyEpoch > 0,
            keyEpoch <= AtlasVaultDeviceIdentityValidation.maximumKeyEpoch
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        self.format = format
        self.version = version
        self.acknowledgementID = try AtlasVaultKeyDeliveryValidation.uuid(
            acknowledgementID
        )
        self.deliveryID = try AtlasVaultKeyDeliveryValidation.uuid(deliveryID)
        self.transcriptSHA256 = try AtlasVaultKeyDeliveryValidation.sha256(
            transcriptSHA256
        )
        self.inviterDeviceID = try AtlasVaultKeyDeliveryValidation.deviceID(
            inviterDeviceID
        )
        self.inviteeDeviceID = try AtlasVaultKeyDeliveryValidation.deviceID(
            inviteeDeviceID
        )
        self.vaultID = try AtlasVaultKeyDeliveryValidation.vaultID(vaultID)
        self.keyEpoch = keyEpoch
        self.bootstrapSHA256 = try AtlasVaultKeyDeliveryValidation.sha256(
            bootstrapSHA256
        )
        self.installedAt = try AtlasVaultKeyDeliveryValidation.timestamp(installedAt)
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultKeyDeliveryValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                acknowledgementID: container.decode(String.self, forKey: .acknowledgementID),
                deliveryID: container.decode(String.self, forKey: .deliveryID),
                transcriptSHA256: container.decode(String.self, forKey: .transcriptSHA256),
                inviterDeviceID: container.decode(String.self, forKey: .inviterDeviceID),
                inviteeDeviceID: container.decode(String.self, forKey: .inviteeDeviceID),
                vaultID: container.decode(String.self, forKey: .vaultID),
                keyEpoch: container.decode(Int.self, forKey: .keyEpoch),
                bootstrapSHA256: container.decode(String.self, forKey: .bootstrapSHA256),
                installedAt: container.decode(String.self, forKey: .installedAt)
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultKeyDeliveryValidation.canonicalData(self)
    }
}

public struct AtlasVaultSignedPairingAcknowledgement: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let acknowledgement: AtlasVaultPairingAcknowledgement
    public let invitee: AtlasVaultSignedDeviceDescriptor
    public let signature: Data

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case acknowledgement
        case invitee
        case signature
    }

    public init(
        format: String = "atlasvault-signed-pairing-acknowledgement",
        version: Int = 1,
        acknowledgement: AtlasVaultPairingAcknowledgement,
        invitee: AtlasVaultSignedDeviceDescriptor,
        signature: Data
    ) throws {
        guard
            format == AtlasVaultKeyDeliveryValidation.signedAcknowledgementFormat,
            version == AtlasVaultKeyDeliveryValidation.version,
            signature.count == AtlasVaultKeyDeliveryValidation.signatureLength,
            acknowledgement.inviteeDeviceID == invitee.descriptor.deviceID
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        self.format = format
        self.version = version
        self.acknowledgement = acknowledgement
        self.invitee = invitee
        self.signature = signature
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultKeyDeliveryValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                acknowledgement: container.decode(
                    AtlasVaultPairingAcknowledgement.self,
                    forKey: .acknowledgement
                ),
                invitee: container.decode(AtlasVaultSignedDeviceDescriptor.self, forKey: .invitee),
                signature: AtlasVaultKeyDeliveryValidation.base64(
                    container.decode(String.self, forKey: .signature),
                    length: AtlasVaultKeyDeliveryValidation.signatureLength
                )
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(acknowledgement, forKey: .acknowledgement)
        try container.encode(invitee, forKey: .invitee)
        try container.encode(signature.base64EncodedString(), forKey: .signature)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            let value = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try value.canonicalData(), data
            ) else { throw AtlasVaultKeyDeliveryValidation.fail() }
            return value
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultKeyDeliveryValidation.canonicalData(self)
    }

    public func sha256Hex() throws -> String {
        AtlasVaultKeyDeliveryValidation.sha256Hex(try canonicalData())
    }
}

public enum AtlasVaultKeyDelivery {
    public static func deriveSAS(
        pairingSessionKey: Data,
        transcriptSHA256: Data
    ) throws -> String {
        guard
            pairingSessionKey.count == AtlasVaultKeyDeliveryValidation.keyLength,
            transcriptSHA256.count == AtlasVaultKeyDeliveryValidation.keyLength
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        let code = Data(HMAC<SHA256>.authenticationCode(
            for: AtlasVaultKeyDeliveryValidation.sasDomain + transcriptSHA256,
            using: SymmetricKey(data: pairingSessionKey)
        ).prefix(6))
        let rendered = code.map { String(format: "%02X", $0) }.joined()
        return "\(rendered.prefix(4))-\(rendered.dropFirst(4).prefix(4))-\(rendered.suffix(4))"
    }

    public static func createKeyRequest(
        invitee: AtlasVaultDeviceIdentity,
        requestID: String,
        transcriptSHA256: Data,
        inviterDeviceID: String,
        inviteeEphemeralPublicKey: Data,
        nonce: Data,
        issuedAt: String,
        expiresAt: String
    ) throws -> AtlasVaultSignedPairingKeyRequest {
        do {
            guard transcriptSHA256.count == AtlasVaultKeyDeliveryValidation.keyLength else {
                throw AtlasVaultKeyDeliveryValidation.fail()
            }
            let request = try AtlasVaultPairingKeyRequest(
                requestID: requestID,
                transcriptSHA256: AtlasVaultDeviceIdentityValidation.lowercaseHex(
                    transcriptSHA256
                ),
                inviterDeviceID: inviterDeviceID,
                inviteeDeviceID: invitee.deviceID,
                inviteeEphemeralPublicKey: inviteeEphemeralPublicKey,
                nonce: nonce,
                issuedAt: issuedAt,
                expiresAt: expiresAt
            )
            return try AtlasVaultSignedPairingKeyRequest(
                request: request,
                invitee: invitee.signDescriptor(),
                signature: invitee.sign(
                    AtlasVaultKeyDeliveryValidation.requestSignatureDomain
                        + request.canonicalData()
                )
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    @discardableResult
    public static func verifyKeyRequest(
        _ signed: AtlasVaultSignedPairingKeyRequest,
        transcriptSHA256: Data,
        inviterDeviceID: String,
        inviteeDeviceID: String,
        currentTime: String
    ) throws -> AtlasVaultPairingKeyRequest {
        do {
            guard transcriptSHA256.count == AtlasVaultKeyDeliveryValidation.keyLength else {
                throw AtlasVaultKeyDeliveryValidation.fail()
            }
            let descriptor = try AtlasVaultKeyDeliveryValidation.verify(
                descriptor: signed.invitee,
                signature: signed.signature,
                domain: AtlasVaultKeyDeliveryValidation.requestSignatureDomain,
                payload: signed.request.canonicalData()
            )
            let request = signed.request
            let now = try AtlasVaultKeyDeliveryValidation.date(currentTime)
            let expectedInviter = try AtlasVaultKeyDeliveryValidation.deviceID(
                inviterDeviceID
            )
            let expectedInvitee = try AtlasVaultKeyDeliveryValidation.deviceID(
                inviteeDeviceID
            )
            let requestExpiry = try AtlasVaultKeyDeliveryValidation.date(
                request.expiresAt
            )
            let requestIssue = try AtlasVaultKeyDeliveryValidation.date(
                request.issuedAt
            )
            guard
                descriptor.deviceID == request.inviteeDeviceID,
                request.transcriptSHA256
                    == AtlasVaultDeviceIdentityValidation.lowercaseHex(transcriptSHA256),
                request.inviterDeviceID == expectedInviter,
                request.inviteeDeviceID == expectedInvitee,
                now < requestExpiry,
                requestIssue
                    <= now.addingTimeInterval(
                        AtlasVaultKeyDeliveryValidation.maximumClockSkew
                    )
            else { throw AtlasVaultKeyDeliveryValidation.fail() }
            return request
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public static func createDelivery(
        inviter: AtlasVaultDeviceIdentity,
        keyRequest: AtlasVaultSignedPairingKeyRequest,
        transcriptSHA256: Data,
        bootstrap: AtlasVaultPairingBootstrap,
        vaultKey: Data,
        inviterEphemeralPrivateKey: Data,
        nonce: Data,
        deliveryID: String,
        keyEpoch: Int,
        expiresAt: String
    ) throws -> AtlasVaultSignedVaultKeyDelivery {
        do {
            guard
                transcriptSHA256.count == AtlasVaultKeyDeliveryValidation.keyLength,
                vaultKey.count == AtlasVaultKeyDeliveryValidation.keyLength,
                inviterEphemeralPrivateKey.count == AtlasVaultKeyDeliveryValidation.keyLength,
                keyRequest.request.transcriptSHA256
                    == AtlasVaultDeviceIdentityValidation.lowercaseHex(transcriptSHA256),
                keyRequest.request.inviterDeviceID == inviter.deviceID,
                expiresAt == keyRequest.request.expiresAt
            else { throw AtlasVaultKeyDeliveryValidation.fail() }
            _ = try AtlasVaultKeyDeliveryValidation.verify(
                descriptor: keyRequest.invitee,
                signature: keyRequest.signature,
                domain: AtlasVaultKeyDeliveryValidation.requestSignatureDomain,
                payload: keyRequest.request.canonicalData()
            )
            let privateKey = try Curve25519.KeyAgreement.PrivateKey(
                rawRepresentation: inviterEphemeralPrivateKey
            )
            let remote = try Curve25519.KeyAgreement.PublicKey(
                rawRepresentation: keyRequest.request.inviteeEphemeralPublicKey
            )
            let shared = try privateKey.sharedSecretFromKeyAgreement(with: remote)
            let key = try deliveryKey(shared: shared, transcriptSHA256: transcriptSHA256)
            let shell = try AtlasVaultVaultKeyDelivery(
                deliveryID: deliveryID,
                transcriptSHA256: AtlasVaultDeviceIdentityValidation.lowercaseHex(
                    transcriptSHA256
                ),
                inviterDeviceID: inviter.deviceID,
                inviteeDeviceID: keyRequest.request.inviteeDeviceID,
                requestSHA256: keyRequest.sha256Hex(),
                vaultID: bootstrap.vaultMetadata.vaultID,
                keyEpoch: keyEpoch,
                bootstrapSHA256: bootstrap.sha256Hex(),
                inviterEphemeralPublicKey: privateKey.publicKey.rawRepresentation,
                nonce: nonce,
                ciphertext: Data(
                    repeating: 0,
                    count: AtlasVaultKeyDeliveryValidation.ciphertextLength
                ),
                expiresAt: expiresAt
            )
            let sealed = try AES.GCM.seal(
                vaultKey,
                using: key,
                nonce: AES.GCM.Nonce(data: nonce),
                authenticating: shell.aad()
            )
            let delivery = try AtlasVaultVaultKeyDelivery(
                deliveryID: shell.deliveryID,
                transcriptSHA256: shell.transcriptSHA256,
                inviterDeviceID: shell.inviterDeviceID,
                inviteeDeviceID: shell.inviteeDeviceID,
                requestSHA256: shell.requestSHA256,
                vaultID: shell.vaultID,
                keyEpoch: shell.keyEpoch,
                bootstrapSHA256: shell.bootstrapSHA256,
                inviterEphemeralPublicKey: shell.inviterEphemeralPublicKey,
                nonce: shell.nonce,
                ciphertext: sealed.ciphertext + sealed.tag,
                expiresAt: shell.expiresAt
            )
            return try AtlasVaultSignedVaultKeyDelivery(
                delivery: delivery,
                inviter: inviter.signDescriptor(),
                signature: inviter.sign(
                    AtlasVaultKeyDeliveryValidation.deliverySignatureDomain
                        + delivery.canonicalData()
                )
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public static func openDelivery(
        _ signed: AtlasVaultSignedVaultKeyDelivery,
        keyRequest: AtlasVaultSignedPairingKeyRequest,
        inviteeEphemeralPrivateKey: Data,
        bootstrap: AtlasVaultPairingBootstrap,
        transcriptSHA256: Data,
        currentTime: String
    ) throws -> Data {
        do {
            guard
                inviteeEphemeralPrivateKey.count
                    == AtlasVaultKeyDeliveryValidation.keyLength,
                transcriptSHA256.count == AtlasVaultKeyDeliveryValidation.keyLength
            else { throw AtlasVaultKeyDeliveryValidation.fail() }
            let value = try verifyDeliverySignature(signed)
            let request = try verifyKeyRequest(
                keyRequest,
                transcriptSHA256: transcriptSHA256,
                inviterDeviceID: value.inviterDeviceID,
                inviteeDeviceID: value.inviteeDeviceID,
                currentTime: currentTime
            )
            let current = try AtlasVaultKeyDeliveryValidation.date(currentTime)
            let expiry = try AtlasVaultKeyDeliveryValidation.date(value.expiresAt)
            let requestHash = try keyRequest.sha256Hex()
            let bootstrapHash = try bootstrap.sha256Hex()
            guard
                current < expiry,
                value.expiresAt == request.expiresAt,
                value.transcriptSHA256
                    == AtlasVaultDeviceIdentityValidation.lowercaseHex(transcriptSHA256),
                value.requestSHA256 == requestHash,
                value.vaultID == bootstrap.vaultMetadata.vaultID,
                value.bootstrapSHA256 == bootstrapHash
            else { throw AtlasVaultKeyDeliveryValidation.fail() }
            let privateKey = try Curve25519.KeyAgreement.PrivateKey(
                rawRepresentation: inviteeEphemeralPrivateKey
            )
            let remote = try Curve25519.KeyAgreement.PublicKey(
                rawRepresentation: value.inviterEphemeralPublicKey
            )
            let shared = try privateKey.sharedSecretFromKeyAgreement(with: remote)
            let key = try deliveryKey(shared: shared, transcriptSHA256: transcriptSHA256)
            let split = value.ciphertext.count - 16
            let box = try AES.GCM.SealedBox(
                nonce: AES.GCM.Nonce(data: value.nonce),
                ciphertext: value.ciphertext.prefix(split),
                tag: value.ciphertext.suffix(16)
            )
            let opened = try AES.GCM.open(box, using: key, authenticating: value.aad())
            guard opened.count == AtlasVaultKeyDeliveryValidation.keyLength else {
                throw AtlasVaultKeyDeliveryValidation.fail()
            }
            return opened
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    @discardableResult
    public static func verifyDeliverySignature(
        _ signed: AtlasVaultSignedVaultKeyDelivery
    ) throws -> AtlasVaultVaultKeyDelivery {
        do {
            let inviter = try AtlasVaultKeyDeliveryValidation.verify(
                descriptor: signed.inviter,
                signature: signed.signature,
                domain: AtlasVaultKeyDeliveryValidation.deliverySignatureDomain,
                payload: signed.delivery.canonicalData()
            )
            guard inviter.deviceID == signed.delivery.inviterDeviceID else {
                throw AtlasVaultKeyDeliveryValidation.fail()
            }
            return signed.delivery
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public static func createAcknowledgement(
        invitee: AtlasVaultDeviceIdentity,
        acknowledgementID: String,
        delivery: AtlasVaultSignedVaultKeyDelivery,
        installedAt: String
    ) throws -> AtlasVaultSignedPairingAcknowledgement {
        do {
            let value = delivery.delivery
            guard invitee.deviceID == value.inviteeDeviceID else {
                throw AtlasVaultKeyDeliveryValidation.fail()
            }
            let acknowledgement = try AtlasVaultPairingAcknowledgement(
                acknowledgementID: acknowledgementID,
                deliveryID: value.deliveryID,
                transcriptSHA256: value.transcriptSHA256,
                inviterDeviceID: value.inviterDeviceID,
                inviteeDeviceID: value.inviteeDeviceID,
                vaultID: value.vaultID,
                keyEpoch: value.keyEpoch,
                bootstrapSHA256: value.bootstrapSHA256,
                installedAt: installedAt
            )
            return try AtlasVaultSignedPairingAcknowledgement(
                acknowledgement: acknowledgement,
                invitee: invitee.signDescriptor(),
                signature: invitee.sign(
                    AtlasVaultKeyDeliveryValidation.acknowledgementSignatureDomain
                        + acknowledgement.canonicalData()
                )
            )
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    @discardableResult
    public static func verifyAcknowledgement(
        _ signed: AtlasVaultSignedPairingAcknowledgement,
        delivery: AtlasVaultSignedVaultKeyDelivery,
        inviterDeviceID: String,
        inviteeDeviceID: String
    ) throws -> AtlasVaultPairingAcknowledgement {
        do {
            let descriptor = try AtlasVaultKeyDeliveryValidation.verify(
                descriptor: signed.invitee,
                signature: signed.signature,
                domain: AtlasVaultKeyDeliveryValidation.acknowledgementSignatureDomain,
                payload: signed.acknowledgement.canonicalData()
            )
            let acknowledgement = signed.acknowledgement
            let value = delivery.delivery
            let expectedInviter = try AtlasVaultKeyDeliveryValidation.deviceID(
                inviterDeviceID
            )
            let expectedInvitee = try AtlasVaultKeyDeliveryValidation.deviceID(
                inviteeDeviceID
            )
            guard
                descriptor.deviceID == acknowledgement.inviteeDeviceID,
                acknowledgement.deliveryID == value.deliveryID,
                acknowledgement.transcriptSHA256 == value.transcriptSHA256,
                acknowledgement.inviterDeviceID == expectedInviter,
                acknowledgement.inviteeDeviceID == expectedInvitee,
                acknowledgement.vaultID == value.vaultID,
                acknowledgement.keyEpoch == value.keyEpoch,
                acknowledgement.bootstrapSHA256 == value.bootstrapSHA256
            else { throw AtlasVaultKeyDeliveryValidation.fail() }
            return acknowledgement
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    private static func deliveryKey(
        shared: SharedSecret,
        transcriptSHA256: Data
    ) throws -> SymmetricKey {
        let bytes = shared.withUnsafeBytes { Data($0) }
        guard
            bytes.count == AtlasVaultKeyDeliveryValidation.keyLength,
            !AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                bytes,
                Data(repeating: 0, count: AtlasVaultKeyDeliveryValidation.keyLength)
            )
        else { throw AtlasVaultKeyDeliveryValidation.fail() }
        return HKDF<SHA256>.deriveKey(
            inputKeyMaterial: SymmetricKey(data: bytes),
            salt: transcriptSHA256,
            info: AtlasVaultKeyDeliveryValidation.deliveryInfo,
            outputByteCount: AtlasVaultKeyDeliveryValidation.keyLength
        )
    }
}

public enum AtlasVaultPairingArtifactKind: String, Sendable {
    case offer
    case acceptance
    case delivery
    case acknowledgement
}

public struct AtlasVaultPairingArtifact: Sendable {
    public let kind: AtlasVaultPairingArtifactKind
    private let encoded: Data

    private init(kind: AtlasVaultPairingArtifactKind, encoded: Data) {
        self.kind = kind
        self.encoded = encoded
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            guard
                let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                Set(root.keys) == ["format", "version", "kind", "payload"],
                root["format"] as? String == AtlasVaultKeyDeliveryValidation.artifactFormat,
                Self.strictInteger(root["version"], equals: AtlasVaultKeyDeliveryValidation.version),
                let kindText = root["kind"] as? String,
                let kind = AtlasVaultPairingArtifactKind(rawValue: kindText),
                let payload = root["payload"] as? [String: Any]
            else { throw AtlasVaultKeyDeliveryValidation.fail() }
            try validate(kind: kind, payload: payload)
            let canonical = try JSONSerialization.data(
                withJSONObject: root,
                options: [.sortedKeys, .withoutEscapingSlashes]
            )
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(canonical, data) else {
                throw AtlasVaultKeyDeliveryValidation.fail()
            }
            return Self(kind: kind, encoded: canonical)
        } catch { throw AtlasVaultKeyDeliveryValidation.fail() }
    }

    public static func offer(
        _ signedOffer: AtlasVaultSignedPairingOffer
    ) throws -> Self {
        try make(kind: .offer, payload: [
            "signed_offer": try object(signedOffer.canonicalData()),
        ])
    }

    public static func acceptance(
        _ signedAcceptance: AtlasVaultSignedPairingAcceptance,
        keyRequest: AtlasVaultSignedPairingKeyRequest,
        inviteeProof: Data
    ) throws -> Self {
        guard inviteeProof.count == 32 else { throw AtlasVaultKeyDeliveryValidation.fail() }
        return try make(kind: .acceptance, payload: [
            "signed_acceptance": try object(signedAcceptance.canonicalData()),
            "signed_key_request": try object(keyRequest.canonicalData()),
            "invitee_proof": inviteeProof.base64EncodedString(),
        ])
    }

    public static func delivery(
        _ signedDelivery: AtlasVaultSignedVaultKeyDelivery,
        bootstrap: AtlasVaultPairingBootstrap,
        inviterProof: Data
    ) throws -> Self {
        guard inviterProof.count == 32 else { throw AtlasVaultKeyDeliveryValidation.fail() }
        return try make(kind: .delivery, payload: [
            "signed_delivery": try object(signedDelivery.canonicalData()),
            "bootstrap": try object(bootstrap.canonicalData()),
            "inviter_proof": inviterProof.base64EncodedString(),
        ])
    }

    public static func acknowledgement(
        _ signedAcknowledgement: AtlasVaultSignedPairingAcknowledgement
    ) throws -> Self {
        try make(kind: .acknowledgement, payload: [
            "signed_acknowledgement": try object(signedAcknowledgement.canonicalData()),
        ])
    }

    public func canonicalData() throws -> Data { encoded }

    public func sha256Hex() throws -> String {
        AtlasVaultKeyDeliveryValidation.sha256Hex(encoded)
    }

    public func signedOffer() throws -> AtlasVaultSignedPairingOffer {
        let payload = try payload(for: .offer)
        return try AtlasVaultSignedPairingOffer.decodeStrict(
            Self.canonicalObjectData(payload["signed_offer"])
        )
    }

    public func acceptancePayload() throws
        -> AtlasVaultPairingAcceptanceArtifactPayload
    {
        let payload = try payload(for: .acceptance)
        guard let proofText = payload["invitee_proof"] as? String else {
            throw AtlasVaultKeyDeliveryValidation.fail()
        }
        return try AtlasVaultPairingAcceptanceArtifactPayload(
            signedAcceptance: AtlasVaultSignedPairingAcceptance.decodeStrict(
                Self.canonicalObjectData(payload["signed_acceptance"])
            ),
            signedKeyRequest: AtlasVaultSignedPairingKeyRequest.decodeStrict(
                Self.canonicalObjectData(payload["signed_key_request"])
            ),
            inviteeProof: AtlasVaultKeyDeliveryValidation.base64(
                proofText,
                length: AtlasVaultKeyDeliveryValidation.keyLength
            )
        )
    }

    public func deliveryPayload() throws
        -> AtlasVaultPairingDeliveryArtifactPayload
    {
        let payload = try payload(for: .delivery)
        guard let proofText = payload["inviter_proof"] as? String else {
            throw AtlasVaultKeyDeliveryValidation.fail()
        }
        return try AtlasVaultPairingDeliveryArtifactPayload(
            signedDelivery: AtlasVaultSignedVaultKeyDelivery.decodeStrict(
                Self.canonicalObjectData(payload["signed_delivery"])
            ),
            bootstrap: AtlasVaultPairingBootstrap.decodeStrict(
                Self.canonicalObjectData(payload["bootstrap"])
            ),
            inviterProof: AtlasVaultKeyDeliveryValidation.base64(
                proofText,
                length: AtlasVaultKeyDeliveryValidation.keyLength
            )
        )
    }

    public func signedAcknowledgement() throws
        -> AtlasVaultSignedPairingAcknowledgement
    {
        let payload = try payload(for: .acknowledgement)
        return try AtlasVaultSignedPairingAcknowledgement.decodeStrict(
            Self.canonicalObjectData(payload["signed_acknowledgement"])
        )
    }

    private static func make(
        kind: AtlasVaultPairingArtifactKind,
        payload: [String: Any]
    ) throws -> Self {
        let root: [String: Any] = [
            "format": AtlasVaultKeyDeliveryValidation.artifactFormat,
            "version": AtlasVaultKeyDeliveryValidation.version,
            "kind": kind.rawValue,
            "payload": payload,
        ]
        let data = try JSONSerialization.data(
            withJSONObject: root,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        return try decodeStrict(data)
    }

    private static func object(_ data: Data) throws -> Any {
        try JSONSerialization.jsonObject(with: data)
    }

    private func payload(
        for expectedKind: AtlasVaultPairingArtifactKind
    ) throws -> [String: Any] {
        guard
            kind == expectedKind,
            let root = try JSONSerialization.jsonObject(with: encoded)
                as? [String: Any],
            let payload = root["payload"] as? [String: Any]
        else {
            throw AtlasVaultKeyDeliveryValidation.fail()
        }
        return payload
    }

    private static func canonicalObjectData(_ value: Any?) throws -> Data {
        guard let value else {
            throw AtlasVaultKeyDeliveryValidation.fail()
        }
        return try JSONSerialization.data(
            withJSONObject: value,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
    }

    private static func validate(
        kind: AtlasVaultPairingArtifactKind,
        payload: [String: Any]
    ) throws {
        switch kind {
        case .offer:
            guard Set(payload.keys) == ["signed_offer"] else {
                throw AtlasVaultKeyDeliveryValidation.fail()
            }
            _ = try AtlasVaultSignedPairingOffer.decodeStrict(
                try data(payload["signed_offer"])
            )
        case .acceptance:
            guard Set(payload.keys) == [
                "signed_acceptance", "signed_key_request", "invitee_proof",
            ] else { throw AtlasVaultKeyDeliveryValidation.fail() }
            _ = try AtlasVaultSignedPairingAcceptance.decodeStrict(
                try data(payload["signed_acceptance"])
            )
            _ = try AtlasVaultSignedPairingKeyRequest.decodeStrict(
                try data(payload["signed_key_request"])
            )
            _ = try proof(payload["invitee_proof"])
        case .delivery:
            guard Set(payload.keys) == [
                "signed_delivery", "bootstrap", "inviter_proof",
            ] else { throw AtlasVaultKeyDeliveryValidation.fail() }
            _ = try AtlasVaultSignedVaultKeyDelivery.decodeStrict(
                try data(payload["signed_delivery"])
            )
            _ = try AtlasVaultPairingBootstrap.decodeStrict(
                try data(payload["bootstrap"])
            )
            _ = try proof(payload["inviter_proof"])
        case .acknowledgement:
            guard Set(payload.keys) == ["signed_acknowledgement"] else {
                throw AtlasVaultKeyDeliveryValidation.fail()
            }
            _ = try AtlasVaultSignedPairingAcknowledgement.decodeStrict(
                try data(payload["signed_acknowledgement"])
            )
        }
    }

    private static func data(_ value: Any?) throws -> Data {
        guard let value else { throw AtlasVaultKeyDeliveryValidation.fail() }
        return try JSONSerialization.data(
            withJSONObject: value,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
    }

    private static func proof(_ value: Any?) throws -> Data {
        guard let text = value as? String else {
            throw AtlasVaultKeyDeliveryValidation.fail()
        }
        return try AtlasVaultKeyDeliveryValidation.base64(text, length: 32)
    }

    private static func strictInteger(_ value: Any?, equals expected: Int) -> Bool {
        guard let number = value as? NSNumber else { return false }
        if CFGetTypeID(number) == CFBooleanGetTypeID() { return false }
        return number.intValue == expected && number.doubleValue == Double(expected)
    }
}

public struct AtlasVaultPairingAcceptanceArtifactPayload: Sendable {
    public let signedAcceptance: AtlasVaultSignedPairingAcceptance
    public let signedKeyRequest: AtlasVaultSignedPairingKeyRequest
    public let inviteeProof: Data

    public init(
        signedAcceptance: AtlasVaultSignedPairingAcceptance,
        signedKeyRequest: AtlasVaultSignedPairingKeyRequest,
        inviteeProof: Data
    ) throws {
        guard inviteeProof.count == 32 else {
            throw AtlasVaultKeyDeliveryError.verificationFailed
        }
        self.signedAcceptance = signedAcceptance
        self.signedKeyRequest = signedKeyRequest
        self.inviteeProof = Data(inviteeProof)
    }
}

public struct AtlasVaultPairingDeliveryArtifactPayload: Sendable {
    public let signedDelivery: AtlasVaultSignedVaultKeyDelivery
    public let bootstrap: AtlasVaultPairingBootstrap
    public let inviterProof: Data

    public init(
        signedDelivery: AtlasVaultSignedVaultKeyDelivery,
        bootstrap: AtlasVaultPairingBootstrap,
        inviterProof: Data
    ) throws {
        guard inviterProof.count == 32 else {
            throw AtlasVaultKeyDeliveryError.verificationFailed
        }
        self.signedDelivery = signedDelivery
        self.bootstrap = bootstrap
        self.inviterProof = Data(inviterProof)
    }
}
