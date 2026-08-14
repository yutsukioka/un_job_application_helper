import CryptoKit
import Foundation

public enum AtlasVaultPairingError: Error, Equatable, Sendable, CustomStringConvertible {
    case verificationFailed

    public var description: String {
        "AtlasVault pairing verification failed."
    }
}

enum AtlasVaultPairingValidation {
    static let offerFormat = "atlasvault-pairing-offer"
    static let signedOfferFormat = "atlasvault-signed-pairing-offer"
    static let acceptanceFormat = "atlasvault-pairing-acceptance"
    static let signedAcceptanceFormat = "atlasvault-signed-pairing-acceptance"
    static let version = 1
    static let nonceLength = 32
    static let signatureLength = 64
    static let maximumLifetime: TimeInterval = 600
    static let maximumClockSkew: TimeInterval = 120
    static let offerSignatureDomain = Data("atlasvault-pairing-offer-signature-v1:".utf8)
    static let acceptanceSignatureDomain = Data("atlasvault-pairing-acceptance-signature-v1:".utf8)
    static let transcriptDomain = Data("atlasvault-pairing-transcript-v1:".utf8)
    static let sessionInfo = Data("atlasvault-pairing-session-v1".utf8)
    static let inviterProofDomain = Data("atlasvault-pairing-confirm-inviter-v1:".utf8)
    static let inviteeProofDomain = Data("atlasvault-pairing-confirm-invitee-v1:".utf8)

    static func canonicalUUID(_ value: String) throws -> String {
        guard
            let uuid = UUID(uuidString: value),
            uuid.uuidString.lowercased() == value
        else {
            throw AtlasVaultPairingError.verificationFailed
        }
        return value
    }

    static func lowercaseHex(_ value: String) throws -> String {
        guard
            value.count == 64,
            value.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil
        else {
            throw AtlasVaultPairingError.verificationFailed
        }
        return value
    }

    static func canonicalBase64(_ value: String, length: Int) throws -> Data {
        do {
            return try AtlasVaultDeviceIdentityValidation.canonicalBase64(value, length: length)
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    static func timestamp(_ value: String) throws -> String {
        do {
            return try AtlasVaultDeviceIdentityValidation.strictUTCTimestamp(value)
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    static func date(_ value: String) throws -> Date {
        do {
            return try AtlasVaultDeviceIdentityValidation.timestampDate(value)
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    static func exactKeys<K: CodingKey>(_ decoder: Decoder, expected: Set<String>, keyedBy _: K.Type) throws {
        let raw = try decoder.container(keyedBy: AtlasVaultAnyCodingKey.self)
        guard Set(raw.allKeys.map(\.stringValue)) == expected else {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    static func canonicalData<T: Encodable>(_ value: T) throws -> Data {
        do {
            return try AtlasVaultDeviceIdentityValidation.canonicalData(value)
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    static func uint64BE(_ value: Int) throws -> Data {
        guard value >= 0 else { throw AtlasVaultPairingError.verificationFailed }
        var encoded = UInt64(value).bigEndian
        return withUnsafeBytes(of: &encoded) { Data($0) }
    }
}

public struct AtlasVaultPairingOffer: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let offerID: String
    public let inviter: AtlasVaultSignedDeviceDescriptor
    public let nonce: Data
    public let issuedAt: String
    public let expiresAt: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case offerID = "offer_id"
        case inviter
        case nonce
        case issuedAt = "issued_at"
        case expiresAt = "expires_at"
    }

    public init(
        format: String = "atlasvault-pairing-offer",
        version: Int = 1,
        offerID: String,
        inviter: AtlasVaultSignedDeviceDescriptor,
        nonce: Data,
        issuedAt: String,
        expiresAt: String
    ) throws {
        guard
            format == AtlasVaultPairingValidation.offerFormat,
            version == AtlasVaultPairingValidation.version,
            nonce.count == AtlasVaultPairingValidation.nonceLength
        else {
            throw AtlasVaultPairingError.verificationFailed
        }
        let issued = try AtlasVaultPairingValidation.date(issuedAt)
        let expires = try AtlasVaultPairingValidation.date(expiresAt)
        let lifetime = expires.timeIntervalSince(issued)
        guard lifetime > 0, lifetime <= AtlasVaultPairingValidation.maximumLifetime else {
            throw AtlasVaultPairingError.verificationFailed
        }
        self.format = format
        self.version = version
        self.offerID = try AtlasVaultPairingValidation.canonicalUUID(offerID)
        self.inviter = inviter
        self.nonce = nonce
        self.issuedAt = issuedAt
        self.expiresAt = expiresAt
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultPairingValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                offerID: container.decode(String.self, forKey: .offerID),
                inviter: container.decode(AtlasVaultSignedDeviceDescriptor.self, forKey: .inviter),
                nonce: AtlasVaultPairingValidation.canonicalBase64(
                    container.decode(String.self, forKey: .nonce),
                    length: AtlasVaultPairingValidation.nonceLength
                ),
                issuedAt: container.decode(String.self, forKey: .issuedAt),
                expiresAt: container.decode(String.self, forKey: .expiresAt)
            )
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(offerID, forKey: .offerID)
        try container.encode(inviter, forKey: .inviter)
        try container.encode(nonce.base64EncodedString(), forKey: .nonce)
        try container.encode(issuedAt, forKey: .issuedAt)
        try container.encode(expiresAt, forKey: .expiresAt)
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultPairingValidation.canonicalData(self)
    }
}

public struct AtlasVaultSignedPairingOffer: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let offer: AtlasVaultPairingOffer
    public let signature: Data

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case offer
        case signature
    }

    public init(
        format: String = "atlasvault-signed-pairing-offer",
        version: Int = 1,
        offer: AtlasVaultPairingOffer,
        signature: Data
    ) throws {
        guard
            format == AtlasVaultPairingValidation.signedOfferFormat,
            version == AtlasVaultPairingValidation.version,
            signature.count == AtlasVaultPairingValidation.signatureLength
        else {
            throw AtlasVaultPairingError.verificationFailed
        }
        self.format = format
        self.version = version
        self.offer = offer
        self.signature = signature
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultPairingValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                offer: container.decode(AtlasVaultPairingOffer.self, forKey: .offer),
                signature: AtlasVaultPairingValidation.canonicalBase64(
                    container.decode(String.self, forKey: .signature),
                    length: AtlasVaultPairingValidation.signatureLength
                )
            )
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(offer, forKey: .offer)
        try container.encode(signature.base64EncodedString(), forKey: .signature)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            let decoded = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try decoded.canonicalData(),
                data
            ) else {
                throw AtlasVaultPairingError.verificationFailed
            }
            return decoded
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultPairingValidation.canonicalData(self)
    }

    public func sha256Hex() throws -> String {
        AtlasVaultDeviceIdentityValidation.lowercaseHex(
            Data(SHA256.hash(data: try canonicalData()))
        )
    }
}

public struct AtlasVaultPairingAcceptance: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let offerID: String
    public let offerSHA256: String
    public let invitee: AtlasVaultSignedDeviceDescriptor
    public let nonce: Data
    public let acceptedAt: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case offerID = "offer_id"
        case offerSHA256 = "offer_sha256"
        case invitee
        case nonce
        case acceptedAt = "accepted_at"
    }

    public init(
        format: String = "atlasvault-pairing-acceptance",
        version: Int = 1,
        offerID: String,
        offerSHA256: String,
        invitee: AtlasVaultSignedDeviceDescriptor,
        nonce: Data,
        acceptedAt: String
    ) throws {
        guard
            format == AtlasVaultPairingValidation.acceptanceFormat,
            version == AtlasVaultPairingValidation.version,
            nonce.count == AtlasVaultPairingValidation.nonceLength
        else {
            throw AtlasVaultPairingError.verificationFailed
        }
        self.format = format
        self.version = version
        self.offerID = try AtlasVaultPairingValidation.canonicalUUID(offerID)
        self.offerSHA256 = try AtlasVaultPairingValidation.lowercaseHex(offerSHA256)
        self.invitee = invitee
        self.nonce = nonce
        self.acceptedAt = try AtlasVaultPairingValidation.timestamp(acceptedAt)
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultPairingValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                offerID: container.decode(String.self, forKey: .offerID),
                offerSHA256: container.decode(String.self, forKey: .offerSHA256),
                invitee: container.decode(AtlasVaultSignedDeviceDescriptor.self, forKey: .invitee),
                nonce: AtlasVaultPairingValidation.canonicalBase64(
                    container.decode(String.self, forKey: .nonce),
                    length: AtlasVaultPairingValidation.nonceLength
                ),
                acceptedAt: container.decode(String.self, forKey: .acceptedAt)
            )
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(offerID, forKey: .offerID)
        try container.encode(offerSHA256, forKey: .offerSHA256)
        try container.encode(invitee, forKey: .invitee)
        try container.encode(nonce.base64EncodedString(), forKey: .nonce)
        try container.encode(acceptedAt, forKey: .acceptedAt)
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultPairingValidation.canonicalData(self)
    }
}

public struct AtlasVaultSignedPairingAcceptance: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let acceptance: AtlasVaultPairingAcceptance
    public let signature: Data

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case acceptance
        case signature
    }

    public init(
        format: String = "atlasvault-signed-pairing-acceptance",
        version: Int = 1,
        acceptance: AtlasVaultPairingAcceptance,
        signature: Data
    ) throws {
        guard
            format == AtlasVaultPairingValidation.signedAcceptanceFormat,
            version == AtlasVaultPairingValidation.version,
            signature.count == AtlasVaultPairingValidation.signatureLength
        else {
            throw AtlasVaultPairingError.verificationFailed
        }
        self.format = format
        self.version = version
        self.acceptance = acceptance
        self.signature = signature
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultPairingValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                acceptance: container.decode(AtlasVaultPairingAcceptance.self, forKey: .acceptance),
                signature: AtlasVaultPairingValidation.canonicalBase64(
                    container.decode(String.self, forKey: .signature),
                    length: AtlasVaultPairingValidation.signatureLength
                )
            )
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(acceptance, forKey: .acceptance)
        try container.encode(signature.base64EncodedString(), forKey: .signature)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            let decoded = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try decoded.canonicalData(),
                data
            ) else {
                throw AtlasVaultPairingError.verificationFailed
            }
            return decoded
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultPairingValidation.canonicalData(self)
    }
}

public struct AtlasVaultPairingProofs: Equatable, Sendable, CustomStringConvertible {
    public let inviter: Data
    public let invitee: Data

    public init(inviter: Data, invitee: Data) {
        self.inviter = inviter
        self.invitee = invitee
    }

    public var description: String {
        "AtlasVaultPairingProofs(<redacted>)"
    }
}

public struct AtlasVaultPairingSession: Sendable, CustomStringConvertible {
    public let transcriptSHA256: Data
    public let sessionKey: Data

    public var description: String {
        "AtlasVaultPairingSession(<redacted>)"
    }
}

public enum AtlasVaultPairingReplayOutcome: Sendable {
    case accepted
    case alreadyConsumed
}

public protocol AtlasVaultPairingReplayGuard: Sendable {
    func consume(
        offerID: String,
        transcriptSHA256: Data,
        expiresAt: String
    ) -> AtlasVaultPairingReplayOutcome
}

public enum AtlasVaultPairingFoundation {
    public static func createOffer(
        inviter: AtlasVaultDeviceIdentity,
        offerID: String,
        nonce: Data,
        issuedAt: String,
        expiresAt: String
    ) throws -> AtlasVaultSignedPairingOffer {
        do {
            let offer = try AtlasVaultPairingOffer(
                offerID: offerID,
                inviter: inviter.signDescriptor(),
                nonce: nonce,
                issuedAt: issuedAt,
                expiresAt: expiresAt
            )
            return try AtlasVaultSignedPairingOffer(
                offer: offer,
                signature: inviter.sign(
                    AtlasVaultPairingValidation.offerSignatureDomain
                        + offer.canonicalData()
                )
            )
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public static func verifyOffer(
        _ signed: AtlasVaultSignedPairingOffer,
        currentTime: String
    ) throws -> AtlasVaultPairingOffer {
        do {
            try verifyOfferSignature(signed)
            let now = try AtlasVaultPairingValidation.date(currentTime)
            let issued = try AtlasVaultPairingValidation.date(signed.offer.issuedAt)
            let expires = try AtlasVaultPairingValidation.date(signed.offer.expiresAt)
            guard
                now < expires,
                issued <= now.addingTimeInterval(AtlasVaultPairingValidation.maximumClockSkew)
            else {
                throw AtlasVaultPairingError.verificationFailed
            }
            return signed.offer
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public static func createAcceptance(
        invitee: AtlasVaultDeviceIdentity,
        signedOffer: AtlasVaultSignedPairingOffer,
        nonce: Data,
        acceptedAt: String,
        currentTime: String
    ) throws -> AtlasVaultSignedPairingAcceptance {
        do {
            let offer = try verifyOffer(signedOffer, currentTime: currentTime)
            let acceptance = try AtlasVaultPairingAcceptance(
                offerID: offer.offerID,
                offerSHA256: signedOffer.sha256Hex(),
                invitee: invitee.signDescriptor(),
                nonce: nonce,
                acceptedAt: acceptedAt
            )
            let signed = try AtlasVaultSignedPairingAcceptance(
                acceptance: acceptance,
                signature: invitee.sign(
                    AtlasVaultPairingValidation.acceptanceSignatureDomain
                        + acceptance.canonicalData()
                )
            )
            try verifyAcceptanceRelation(offer: signedOffer, acceptance: signed)
            return signed
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public static func transcriptSHA256(
        offer: AtlasVaultSignedPairingOffer,
        acceptance: AtlasVaultSignedPairingAcceptance
    ) throws -> Data {
        do {
            let offerData = try offer.canonicalData()
            let acceptanceData = try acceptance.canonicalData()
            return Data(SHA256.hash(
                data: AtlasVaultPairingValidation.transcriptDomain
                    + (try AtlasVaultPairingValidation.uint64BE(offerData.count))
                    + offerData
                    + (try AtlasVaultPairingValidation.uint64BE(acceptanceData.count))
                    + acceptanceData
            ))
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public static func deriveSessionKey(
        sharedSecret: Data,
        transcriptSHA256: Data
    ) throws -> Data {
        guard
            sharedSecret.count == 32,
            transcriptSHA256.count == 32,
            !AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                sharedSecret,
                Data(repeating: 0, count: 32)
            )
        else {
            throw AtlasVaultPairingError.verificationFailed
        }
        let key = HKDF<SHA256>.deriveKey(
            inputKeyMaterial: SymmetricKey(data: sharedSecret),
            salt: transcriptSHA256,
            info: AtlasVaultPairingValidation.sessionInfo,
            outputByteCount: 32
        )
        return key.withUnsafeBytes { Data($0) }
    }

    public static func deriveSessionKey(
        localIdentity: AtlasVaultDeviceIdentity,
        offer: AtlasVaultSignedPairingOffer,
        acceptance: AtlasVaultSignedPairingAcceptance
    ) throws -> Data {
        do {
            try verifyOfferSignature(offer)
            try verifyAcceptanceSignature(acceptance)
            try verifyAcceptanceRelation(offer: offer, acceptance: acceptance)
            let inviter = offer.offer.inviter.descriptor
            let invitee = acceptance.acceptance.invitee.descriptor
            let remotePublicKey: Data
            if localIdentity.deviceID == inviter.deviceID {
                remotePublicKey = invitee.agreementPublicKey
            } else if localIdentity.deviceID == invitee.deviceID {
                remotePublicKey = inviter.agreementPublicKey
            } else {
                throw AtlasVaultPairingError.verificationFailed
            }
            return try deriveSessionKey(
                sharedSecret: localIdentity.sharedSecret(with: remotePublicKey),
                transcriptSHA256: transcriptSHA256(offer: offer, acceptance: acceptance)
            )
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    public static func deriveProofs(
        sessionKey: Data,
        transcriptSHA256: Data
    ) throws -> AtlasVaultPairingProofs {
        guard sessionKey.count == 32, transcriptSHA256.count == 32 else {
            throw AtlasVaultPairingError.verificationFailed
        }
        let key = SymmetricKey(data: sessionKey)
        return AtlasVaultPairingProofs(
            inviter: Data(HMAC<SHA256>.authenticationCode(
                for: AtlasVaultPairingValidation.inviterProofDomain + transcriptSHA256,
                using: key
            )),
            invitee: Data(HMAC<SHA256>.authenticationCode(
                for: AtlasVaultPairingValidation.inviteeProofDomain + transcriptSHA256,
                using: key
            ))
        )
    }

    public static func verify(
        localIdentity: AtlasVaultDeviceIdentity,
        offer: AtlasVaultSignedPairingOffer,
        acceptance: AtlasVaultSignedPairingAcceptance,
        proofs: AtlasVaultPairingProofs,
        currentTime: String,
        replayGuard: any AtlasVaultPairingReplayGuard
    ) throws -> AtlasVaultPairingSession {
        do {
            _ = try verifyOffer(offer, currentTime: currentTime)
            try verifyAcceptanceSignature(acceptance)
            try verifyAcceptanceRelation(offer: offer, acceptance: acceptance)
            let transcript = try transcriptSHA256(offer: offer, acceptance: acceptance)
            let sessionKey = try deriveSessionKey(
                localIdentity: localIdentity,
                offer: offer,
                acceptance: acceptance
            )
            let expected = try deriveProofs(
                sessionKey: sessionKey,
                transcriptSHA256: transcript
            )
            guard
                AtlasVaultDeviceIdentityValidation.constantTimeEqual(expected.inviter, proofs.inviter),
                AtlasVaultDeviceIdentityValidation.constantTimeEqual(expected.invitee, proofs.invitee),
                replayGuard.consume(
                    offerID: offer.offer.offerID,
                    transcriptSHA256: transcript,
                    expiresAt: offer.offer.expiresAt
                ) == .accepted
            else {
                throw AtlasVaultPairingError.verificationFailed
            }
            return AtlasVaultPairingSession(
                transcriptSHA256: transcript,
                sessionKey: sessionKey
            )
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    private static func verifyOfferSignature(_ signed: AtlasVaultSignedPairingOffer) throws {
        do {
            let descriptor = try signed.offer.inviter.verifiedDescriptor()
            let publicKey = try Curve25519.Signing.PublicKey(
                rawRepresentation: descriptor.signingPublicKey
            )
            guard publicKey.isValidSignature(
                signed.signature,
                for: AtlasVaultPairingValidation.offerSignatureDomain
                    + (try signed.offer.canonicalData())
            ) else {
                throw AtlasVaultPairingError.verificationFailed
            }
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    private static func verifyAcceptanceSignature(
        _ signed: AtlasVaultSignedPairingAcceptance
    ) throws {
        do {
            let descriptor = try signed.acceptance.invitee.verifiedDescriptor()
            let publicKey = try Curve25519.Signing.PublicKey(
                rawRepresentation: descriptor.signingPublicKey
            )
            guard publicKey.isValidSignature(
                signed.signature,
                for: AtlasVaultPairingValidation.acceptanceSignatureDomain
                    + (try signed.acceptance.canonicalData())
            ) else {
                throw AtlasVaultPairingError.verificationFailed
            }
        } catch {
            throw AtlasVaultPairingError.verificationFailed
        }
    }

    private static func verifyAcceptanceRelation(
        offer: AtlasVaultSignedPairingOffer,
        acceptance: AtlasVaultSignedPairingAcceptance
    ) throws {
        let value = acceptance.acceptance
        guard
            value.offerID == offer.offer.offerID,
            AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                value.offerSHA256,
                try offer.sha256Hex()
            ),
            value.invitee.descriptor.deviceID != offer.offer.inviter.descriptor.deviceID
        else {
            throw AtlasVaultPairingError.verificationFailed
        }
        let accepted = try AtlasVaultPairingValidation.date(value.acceptedAt)
        let lower = try AtlasVaultPairingValidation.date(offer.offer.issuedAt)
            .addingTimeInterval(-AtlasVaultPairingValidation.maximumClockSkew)
        let upper = try AtlasVaultPairingValidation.date(offer.offer.expiresAt)
            .addingTimeInterval(AtlasVaultPairingValidation.maximumClockSkew)
        guard accepted >= lower, accepted <= upper else {
            throw AtlasVaultPairingError.verificationFailed
        }
    }
}
