import CryptoKit
import Foundation

public enum AtlasVaultHPKEKeyDeliveryError: Error, Equatable, Sendable {
    case operationFailed
}

public struct AtlasVaultHPKESealedVaultKeyV2: Equatable, Sendable {
    public let encapsulatedKey: Data
    public let ciphertext: Data

    public init(encapsulatedKey: Data, ciphertext: Data) {
        self.encapsulatedKey = encapsulatedKey
        self.ciphertext = ciphertext
    }
}

public enum AtlasVaultHPKEKeyDelivery {
    public static let version = 2

    private static let keyLength = 32
    private static let encapsulatedKeyLength = 32
    private static let ciphertextLength = 48
    private static let maximumContextLength = 4_096
    private static let infoPrefix = Data(
        "atlasvault-vault-key-delivery-hpke-v2:".utf8
    )
    private static let ciphersuite = HPKE.Ciphersuite(
        kem: .Curve25519_HKDF_SHA256,
        kdf: .HKDF_SHA256,
        aead: .AES_GCM_256
    )

    public static func sealVaultKeyV2(
        recipientPublicKey: Data,
        vaultKey: Data,
        context: Data
    ) throws -> AtlasVaultHPKESealedVaultKeyV2 {
        do {
            guard
                recipientPublicKey.count == keyLength,
                vaultKey.count == keyLength
            else { throw AtlasVaultHPKEKeyDeliveryError.operationFailed }
            let recipient = try Curve25519.KeyAgreement.PublicKey(
                rawRepresentation: recipientPublicKey
            )
            var sender = try HPKE.Sender(
                recipientKey: recipient,
                ciphersuite: ciphersuite,
                info: try info(context)
            )
            let ciphertext = try sender.seal(vaultKey)
            guard
                sender.encapsulatedKey.count == encapsulatedKeyLength,
                ciphertext.count == ciphertextLength
            else { throw AtlasVaultHPKEKeyDeliveryError.operationFailed }
            return AtlasVaultHPKESealedVaultKeyV2(
                encapsulatedKey: sender.encapsulatedKey,
                ciphertext: ciphertext
            )
        } catch {
            throw AtlasVaultHPKEKeyDeliveryError.operationFailed
        }
    }

    public static func openVaultKeyV2(
        recipientPrivateKey: Data,
        sealed: AtlasVaultHPKESealedVaultKeyV2,
        context: Data
    ) throws -> Data {
        do {
            guard
                recipientPrivateKey.count == keyLength,
                sealed.encapsulatedKey.count == encapsulatedKeyLength,
                sealed.ciphertext.count == ciphertextLength
            else { throw AtlasVaultHPKEKeyDeliveryError.operationFailed }
            let privateKey = try Curve25519.KeyAgreement.PrivateKey(
                rawRepresentation: recipientPrivateKey
            )
            var recipient = try HPKE.Recipient(
                privateKey: privateKey,
                ciphersuite: ciphersuite,
                info: try info(context),
                encapsulatedKey: sealed.encapsulatedKey
            )
            let plaintext = try recipient.open(sealed.ciphertext)
            guard plaintext.count == keyLength else {
                throw AtlasVaultHPKEKeyDeliveryError.operationFailed
            }
            return plaintext
        } catch {
            throw AtlasVaultHPKEKeyDeliveryError.operationFailed
        }
    }

    private static func info(_ context: Data) throws -> Data {
        guard
            !context.isEmpty,
            context.count <= maximumContextLength
        else { throw AtlasVaultHPKEKeyDeliveryError.operationFailed }
        return infoPrefix + context
    }
}
