import CryptoKit
import Foundation
import Security

public enum AtlasVaultRecoveryKeyError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible
{
    case invalidRecoveryKey
    case randomUnavailable
    case invalidVault
    case invalidWrap
    case authenticationFailed

    public var description: String {
        switch self {
        case .invalidRecoveryKey:
            return "Recovery key is invalid."
        case .randomUnavailable:
            return "Recovery setup is unavailable."
        case .invalidVault:
            return "Recovery setup cannot use this vault."
        case .invalidWrap:
            return "Recovery key wrap is invalid."
        case .authenticationFailed:
            return "Recovery key verification failed."
        }
    }
}

public enum AtlasVaultRecoveryKeyCodec {
    public static let rawByteCount = 32
    public static let checksumByteCount = 5
    public static let base32SymbolCount = 60
    public static let canonicalPrefix = "AVRK1-"

    private static let checksumDomain =
        Data("atlasvault-recovery-key-v1:".utf8)
    private static let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")

    public static func generate() throws -> Data {
        try secureRandomData(count: rawByteCount)
    }

    public static func canonicalText(for recoveryKey: Data) throws -> String {
        guard recoveryKey.count == rawByteCount else {
            throw AtlasVaultRecoveryKeyError.invalidRecoveryKey
        }
        var payload = recoveryKey
        payload.append(checksum(for: recoveryKey))
        defer {
            bestEffortWipe(&payload)
        }
        let symbols = base32Encode(payload)
        guard symbols.count == base32SymbolCount else {
            throw AtlasVaultRecoveryKeyError.invalidRecoveryKey
        }
        let groups = stride(from: 0, to: symbols.count, by: 4).map {
            String(symbols.dropFirst($0).prefix(4))
        }
        return "AVRK1-" + groups.joined(separator: "-")
    }

    public static func parse(_ text: String) throws -> Data {
        guard text.unicodeScalars.allSatisfy(\.isASCII) else {
            throw AtlasVaultRecoveryKeyError.invalidRecoveryKey
        }
        let trimmed = text.trimmingCharacters(
            in: CharacterSet(charactersIn: " \t\r\n")
        )
        guard !trimmed.contains("=") else {
            throw AtlasVaultRecoveryKeyError.invalidRecoveryKey
        }
        let groups = trimmed.uppercased().split(
            omittingEmptySubsequences: true,
            whereSeparator: { $0 == "-" || $0 == " " }
        )
        guard
            groups.count == 16,
            groups[0] == "AVRK1",
            groups.dropFirst().allSatisfy({ $0.count == 4 })
        else {
            throw AtlasVaultRecoveryKeyError.invalidRecoveryKey
        }
        let symbols = groups.dropFirst().joined()
        guard
            symbols.count == base32SymbolCount,
            symbols.allSatisfy({ alphabet.contains($0) }),
            let decoded = base32Decode(symbols),
            decoded.count == rawByteCount + checksumByteCount
        else {
            throw AtlasVaultRecoveryKeyError.invalidRecoveryKey
        }
        var recoveryKey = Data(decoded.prefix(rawByteCount))
        let suppliedChecksum = Data(decoded.suffix(checksumByteCount))
        let expectedChecksum = checksum(for: recoveryKey)
        guard constantTimeEqual(suppliedChecksum, expectedChecksum) else {
            bestEffortWipe(&recoveryKey)
            throw AtlasVaultRecoveryKeyError.invalidRecoveryKey
        }
        return recoveryKey
    }

    public static func constantTimeEqual(_ lhs: Data, _ rhs: Data) -> Bool {
        guard lhs.count == rhs.count else {
            return false
        }
        var difference: UInt8 = 0
        for (left, right) in zip(lhs, rhs) {
            difference |= left ^ right
        }
        return difference == 0
    }

    public static func bestEffortWipe(_ data: inout Data) {
        data.resetBytes(in: data.startIndex..<data.endIndex)
        data.removeAll(keepingCapacity: false)
    }

    static func secureRandomData(count: Int) throws -> Data {
        var data = Data(count: count)
        let status = data.withUnsafeMutableBytes { bytes in
            guard let baseAddress = bytes.baseAddress else {
                return errSecParam
            }
            return SecRandomCopyBytes(kSecRandomDefault, count, baseAddress)
        }
        guard status == errSecSuccess else {
            bestEffortWipe(&data)
            throw AtlasVaultRecoveryKeyError.randomUnavailable
        }
        return data
    }

    private static func checksum(for recoveryKey: Data) -> Data {
        var input = checksumDomain
        input.append(recoveryKey)
        defer {
            bestEffortWipe(&input)
        }
        return Data(SHA256.hash(data: input).prefix(checksumByteCount))
    }

    private static func base32Encode(_ data: Data) -> String {
        var result = ""
        var buffer: UInt64 = 0
        var bitCount = 0
        for byte in data {
            buffer = (buffer << 8) | UInt64(byte)
            bitCount += 8
            while bitCount >= 5 {
                bitCount -= 5
                let index = Int((buffer >> UInt64(bitCount)) & 0x1f)
                result.append(alphabet[index])
            }
            if bitCount > 0 {
                buffer &= (1 << UInt64(bitCount)) - 1
            } else {
                buffer = 0
            }
        }
        if bitCount > 0 {
            let index = Int((buffer << UInt64(5 - bitCount)) & 0x1f)
            result.append(alphabet[index])
        }
        return result
    }

    private static func base32Decode(_ symbols: String) -> Data? {
        var result = Data()
        var buffer: UInt64 = 0
        var bitCount = 0
        for symbol in symbols {
            guard let index = alphabet.firstIndex(of: symbol) else {
                return nil
            }
            buffer = (buffer << 5) | UInt64(index)
            bitCount += 5
            while bitCount >= 8 {
                bitCount -= 8
                result.append(UInt8((buffer >> UInt64(bitCount)) & 0xff))
            }
            if bitCount > 0 {
                buffer &= (1 << UInt64(bitCount)) - 1
            } else {
                buffer = 0
            }
        }
        guard bitCount == 4, buffer == 0 else {
            return nil
        }
        return result
    }
}

public enum AtlasVaultRecoveryWrapCrypto {
    public static let wrappingKeyByteCount = 32
    public static let saltByteCount = 32
    public static let nonceByteCount = 12
    public static let authenticationTagByteCount = 16
    public static let keyWrapAEAD = "AES-256-GCM"

    public static func wrap(
        vaultKey: Data,
        recoveryKey: Data,
        vaultID: String,
        salt: Data? = nil,
        nonce: Data? = nil
    ) throws -> AtlasVaultRecoveryWrappedKeyEnvelope {
        guard
            vaultKey.count == AtlasVaultRecordCrypto.vaultKeyByteCount,
            recoveryKey.count == AtlasVaultRecoveryKeyCodec.rawByteCount
        else {
            throw AtlasVaultRecoveryKeyError.invalidWrap
        }
        try validateVaultID(vaultID)
        let salt = try salt ?? AtlasVaultRecoveryKeyCodec.secureRandomData(
            count: saltByteCount
        )
        let nonce = try nonce ?? AtlasVaultRecoveryKeyCodec.secureRandomData(
            count: nonceByteCount
        )
        let kdf = try AtlasVaultRecoveryWrapKDFParameters(salt: salt)
        let placeholder = try AtlasVaultRecoveryWrappedKeyEnvelope(
            kdf: kdf,
            nonce: nonce,
            ciphertext: Data(
                repeating: 0,
                count: AtlasVaultRecoveryWrappedKeyEnvelope
                    .ciphertextAndTagByteCount
            )
        )
        let key = deriveWrappingKey(
            recoveryKey: recoveryKey,
            parameters: kdf
        )
        let gcmNonce: AES.GCM.Nonce
        do {
            gcmNonce = try AES.GCM.Nonce(data: nonce)
        } catch {
            throw AtlasVaultRecoveryKeyError.invalidWrap
        }
        let sealed: AES.GCM.SealedBox
        do {
            sealed = try AES.GCM.seal(
                vaultKey,
                using: key,
                nonce: gcmNonce,
                authenticating: try associatedData(
                    vaultID: vaultID,
                    wrap: placeholder
                )
            )
        } catch let error as AtlasVaultRecoveryKeyError {
            throw error
        } catch {
            throw AtlasVaultRecoveryKeyError.invalidWrap
        }
        var ciphertext = sealed.ciphertext
        ciphertext.append(sealed.tag)
        return try AtlasVaultRecoveryWrappedKeyEnvelope(
            kdf: kdf,
            nonce: nonce,
            ciphertext: ciphertext
        )
    }

    public static func unwrap(
        _ wrap: AtlasVaultRecoveryWrappedKeyEnvelope,
        recoveryKey: Data,
        vaultID: String
    ) throws -> Data {
        guard recoveryKey.count == AtlasVaultRecoveryKeyCodec.rawByteCount else {
            throw AtlasVaultRecoveryKeyError.authenticationFailed
        }
        do {
            try validateVaultID(vaultID)
            let nonce = try AES.GCM.Nonce(data: wrap.nonce)
            let ciphertext = wrap.ciphertext.dropLast(
                authenticationTagByteCount
            )
            let tag = wrap.ciphertext.suffix(authenticationTagByteCount)
            let box = try AES.GCM.SealedBox(
                nonce: nonce,
                ciphertext: ciphertext,
                tag: tag
            )
            let result = try AES.GCM.open(
                box,
                using: deriveWrappingKey(
                    recoveryKey: recoveryKey,
                    parameters: wrap.kdf
                ),
                authenticating: associatedData(
                    vaultID: vaultID,
                    wrap: wrap
                )
            )
            guard result.count == AtlasVaultRecordCrypto.vaultKeyByteCount else {
                throw AtlasVaultRecoveryKeyError.authenticationFailed
            }
            return result
        } catch {
            throw AtlasVaultRecoveryKeyError.authenticationFailed
        }
    }

    public static func associatedData(
        vaultID: String,
        wrap: AtlasVaultRecoveryWrappedKeyEnvelope
    ) throws -> Data {
        try validateVaultID(vaultID)
        let object: [String: Any] = [
            "format": "atlas-vault-key-wrap",
            "version": wrap.wrapVersion,
            "vault_id": vaultID,
            "id": wrap.id,
            "type": wrap.type,
            "key_wrap_aead": keyWrapAEAD,
            "kdf": [
                "algorithm": wrap.kdf.algorithm,
                "salt": wrap.kdf.salt.base64EncodedString(),
                "info": wrap.kdf.info,
            ],
        ]
        guard JSONSerialization.isValidJSONObject(object) else {
            throw AtlasVaultRecoveryKeyError.invalidWrap
        }
        do {
            return try JSONSerialization.data(
                withJSONObject: object,
                options: [.sortedKeys, .withoutEscapingSlashes]
            )
        } catch {
            throw AtlasVaultRecoveryKeyError.invalidWrap
        }
    }

    public static func constantTimeEqual(_ lhs: Data, _ rhs: Data) -> Bool {
        AtlasVaultRecoveryKeyCodec.constantTimeEqual(lhs, rhs)
    }

    private static func deriveWrappingKey(
        recoveryKey: Data,
        parameters: AtlasVaultRecoveryWrapKDFParameters
    ) -> SymmetricKey {
        HKDF<SHA256>.deriveKey(
            inputKeyMaterial: SymmetricKey(data: recoveryKey),
            salt: parameters.salt,
            info: Data(
                "atlas-vault-recovery-wrap-v2".utf8
            ),
            outputByteCount: wrappingKeyByteCount
        )
    }

    private static func validateVaultID(_ vaultID: String) throws {
        do {
            _ = try AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID)
        } catch {
            throw AtlasVaultRecoveryKeyError.invalidVault
        }
    }
}
