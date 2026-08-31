import CryptoKit
import CoreFoundation
import Foundation

public enum AtlasVaultKeyEpochError: Error, Equatable, Sendable {
    case operationFailed
}

public struct AtlasVaultKeyRingMetadata: Equatable, Sendable {
    public static let format = "atlasvault-vault-key-ring"
    public static let version = 1

    public let currentKeyEpoch: Int64
    public let retainedKeyEpochs: [Int64]

    public init(
        currentKeyEpoch: Int64,
        retainedKeyEpochs: [Int64] = []
    ) throws {
        let current = try requireEpoch(currentKeyEpoch)
        let retained = try retainedKeyEpochs.map(requireEpoch)
        guard
            retained.count + 1 <= AtlasVaultKeyEpochRing.maximumEntries,
            retained == Array(Set(retained)).sorted(),
            retained.allSatisfy({ $0 < current })
        else { throw AtlasVaultKeyEpochError.operationFailed }
        self.currentKeyEpoch = current
        self.retainedKeyEpochs = retained
    }

    public init(jsonObject: [String: Any]) throws {
        guard
            Set(jsonObject.keys) == [
                "format", "version", "current_key_epoch", "retained_key_epochs",
            ],
            jsonObject["format"] as? String == Self.format,
            let version = jsonObject["version"],
            try jsonInteger(version) == Self.version,
            let current = jsonObject["current_key_epoch"],
            let retained = jsonObject["retained_key_epochs"] as? [Any]
        else { throw AtlasVaultKeyEpochError.operationFailed }
        try self.init(
            currentKeyEpoch: jsonInteger(current),
            retainedKeyEpochs: retained.map(jsonInteger)
        )
    }

    public var jsonObject: [String: Any] {
        [
            "format": Self.format,
            "version": Self.version,
            "current_key_epoch": currentKeyEpoch,
            "retained_key_epochs": retainedKeyEpochs,
        ]
    }
}

public struct AtlasVaultEpochVaultKey: Equatable, Sendable {
    public let keyEpoch: Int64
    public let vaultKey: Data

    public init(keyEpoch: Int64, vaultKey: Data) throws {
        self.keyEpoch = try requireEpoch(keyEpoch)
        self.vaultKey = try requireVaultKey(vaultKey)
    }
}

public struct AtlasVaultKeyEpochHPKESealedVaultKeyV2: Equatable, Sendable {
    public let keyEpoch: Int64
    public let encapsulatedKey: Data
    public let ciphertext: Data

    public init(keyEpoch: Int64, encapsulatedKey: Data, ciphertext: Data) {
        self.keyEpoch = keyEpoch
        self.encapsulatedKey = encapsulatedKey
        self.ciphertext = ciphertext
    }
}

public struct AtlasVaultKeyEpochRing: Sendable {
    public static let maximumEntries = 32
    public static let maximumContextBytes = 4_058

    public let metadata: AtlasVaultKeyRingMetadata
    private let keys: [Int64: Data]

    public init(
        metadata: AtlasVaultKeyRingMetadata,
        keys: [Int64: Data]
    ) throws {
        let copied = try Dictionary(uniqueKeysWithValues: keys.map {
            (try requireEpoch($0.key), try requireVaultKey($0.value))
        })
        let expected = Set(metadata.retainedKeyEpochs + [metadata.currentKeyEpoch])
        guard
            Set(copied.keys) == expected,
            copied.count <= Self.maximumEntries,
            Set(copied.values).count == copied.count
        else { throw AtlasVaultKeyEpochError.operationFailed }
        self.metadata = metadata
        self.keys = copied
    }

    public static func fromEntries(
        currentKeyEpoch: Int64,
        keys: [Int64: Data]
    ) throws -> AtlasVaultKeyEpochRing {
        let current = try requireEpoch(currentKeyEpoch)
        guard keys[current] != nil else {
            throw AtlasVaultKeyEpochError.operationFailed
        }
        let epochs = try keys.keys.map(requireEpoch).sorted()
        guard epochs.allSatisfy({ $0 == current || $0 < current }) else {
            throw AtlasVaultKeyEpochError.operationFailed
        }
        return try AtlasVaultKeyEpochRing(
            metadata: AtlasVaultKeyRingMetadata(
                currentKeyEpoch: current,
                retainedKeyEpochs: epochs.filter { $0 != current }
            ),
            keys: keys
        )
    }

    public static func fromLegacy(
        _ vaultKey: Data,
        keyEpoch: Int64 = 1
    ) throws -> AtlasVaultKeyEpochRing {
        let epoch = try requireEpoch(keyEpoch)
        guard epoch == 1 else {
            throw AtlasVaultKeyEpochError.operationFailed
        }
        return try fromEntries(currentKeyEpoch: epoch, keys: [epoch: vaultKey])
    }

    public var currentKeyEpoch: Int64 { metadata.currentKeyEpoch }

    public var currentVaultKey: Data { keys[currentKeyEpoch]! }

    public func vaultKey(for keyEpoch: Int64) throws -> Data {
        guard let key = keys[try requireEpoch(keyEpoch)] else {
            throw AtlasVaultKeyEpochError.operationFailed
        }
        return key
    }

    public func deriveRecordKey(
        keyEpoch: Int64,
        vaultID: String,
        recordID: String
    ) throws -> Data {
        let epoch = try requireEpoch(keyEpoch)
        if epoch == 1 {
            return try AtlasVaultRecordCrypto.deriveRecordKey(
                vaultKey: vaultKey(for: epoch),
                vaultID: identifier(vaultID),
                recordID: legacyRecordIdentifier(recordID)
            ).withUnsafeBytes { Data($0) }
        }
        let key = SymmetricKey(data: try vaultKey(for: epoch))
        let salt = Data(
            "atlasvault-record-key-epoch-v1:\(try identifier(vaultID))".utf8
        )
        let info = Data(
            "epoch:\(epoch):record:\(try identifier(recordID))".utf8
        )
        let derived = HKDF<SHA256>.deriveKey(
            inputKeyMaterial: key,
            salt: salt,
            info: info,
            outputByteCount: 32
        )
        return derived.withUnsafeBytes { Data($0) }
    }

    public func sealCurrentHPKEV2(
        recipientPublicKey: Data,
        context: Data
    ) throws -> AtlasVaultKeyEpochHPKESealedVaultKeyV2 {
        do {
            let sealed = try AtlasVaultHPKEKeyDelivery.sealVaultKeyV2(
                recipientPublicKey: recipientPublicKey,
                vaultKey: currentVaultKey,
                context: try epochContext(currentKeyEpoch, context)
            )
            return AtlasVaultKeyEpochHPKESealedVaultKeyV2(
                keyEpoch: currentKeyEpoch,
                encapsulatedKey: sealed.encapsulatedKey,
                ciphertext: sealed.ciphertext
            )
        } catch {
            throw AtlasVaultKeyEpochError.operationFailed
        }
    }
}

public enum AtlasVaultKeyEpochHPKE {
    public static func open(
        recipientPrivateKey: Data,
        sealed: AtlasVaultKeyEpochHPKESealedVaultKeyV2,
        context: Data,
        minimumKeyEpoch: Int64
    ) throws -> AtlasVaultEpochVaultKey {
        do {
            let epoch = try requireEpoch(sealed.keyEpoch)
            guard epoch >= (try requireEpoch(minimumKeyEpoch)) else {
                throw AtlasVaultKeyEpochError.operationFailed
            }
            let key = try AtlasVaultHPKEKeyDelivery.openVaultKeyV2(
                recipientPrivateKey: recipientPrivateKey,
                sealed: AtlasVaultHPKESealedVaultKeyV2(
                    encapsulatedKey: sealed.encapsulatedKey,
                    ciphertext: sealed.ciphertext
                ),
                context: try epochContext(epoch, context)
            )
            return try AtlasVaultEpochVaultKey(keyEpoch: epoch, vaultKey: key)
        } catch {
            throw AtlasVaultKeyEpochError.operationFailed
        }
    }
}

private let maximumKeyEpoch = Int64.max
private let maximumIdentifierBytes = 1_024

private func jsonInteger(_ value: Any) throws -> Int64 {
    guard
        let number = value as? NSNumber,
        CFGetTypeID(number) != CFBooleanGetTypeID(),
        ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(
            String(cString: number.objCType)
        ),
        let result = Int64(number.stringValue)
    else { throw AtlasVaultKeyEpochError.operationFailed }
    return result
}

private func requireEpoch(_ value: Int64) throws -> Int64 {
    guard value >= 1, value <= maximumKeyEpoch else {
        throw AtlasVaultKeyEpochError.operationFailed
    }
    return value
}

private func requireVaultKey(_ value: Data) throws -> Data {
    guard value.count == 32 else {
        throw AtlasVaultKeyEpochError.operationFailed
    }
    return Data(value)
}

private func identifier(_ value: String) throws -> String {
    guard
        !value.isEmpty,
        let bytes = value.data(using: .utf8),
        bytes.count <= maximumIdentifierBytes
    else { throw AtlasVaultKeyEpochError.operationFailed }
    return value
}

private func legacyRecordIdentifier(_ value: String) throws -> String {
    guard !value.isEmpty else { throw AtlasVaultKeyEpochError.operationFailed }
    return value
}

private func epochContext(_ keyEpoch: Int64, _ context: Data) throws -> Data {
    guard
        !context.isEmpty,
        context.count <= AtlasVaultKeyEpochRing.maximumContextBytes
    else { throw AtlasVaultKeyEpochError.operationFailed }
    var bigEndian = UInt64(try requireEpoch(keyEpoch)).bigEndian
    let epoch = withUnsafeBytes(of: &bigEndian) { Data($0) }
    return Data("atlasvault-key-epoch-hpke-v1:".utf8)
        + epoch
        + Data([0x3a])
        + context
}
