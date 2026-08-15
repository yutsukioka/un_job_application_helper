import CryptoKit
import Foundation
import Security
import SwiftUI
import UniformTypeIdentifiers

public enum AtlasVaultPairingTransactionError:
    Error, Equatable, Sendable, CustomStringConvertible
{
    case invalidTransaction
    case unavailable
    case collision
    case stale

    public var description: String {
        "AtlasVault pairing transaction operation failed."
    }
}

public enum AtlasVaultPairingRole: String, Codable, Sendable {
    case inviter
    case invitee
}

public enum AtlasVaultPairingStage: String, Codable, Sendable {
    case offerCreated = "offer_created"
    case offerSaved = "offer_saved"
    case offerImported = "offer_imported"
    case acceptanceCreated = "acceptance_created"
    case acceptanceSaved = "acceptance_saved"
    case acceptanceImported = "acceptance_imported"
    case sasConfirmed = "sas_confirmed"
    case offerConsumed = "offer_consumed"
    case deliveryCreated = "delivery_created"
    case deliverySaved = "delivery_saved"
    case deliveryImported = "delivery_imported"
    case storeCreated = "store_created"
    case keyCreated = "key_created"
    case selectionCommitted = "selection_committed"
    case runtimeActivated = "runtime_activated"
    case acknowledgementCreated = "acknowledgement_created"
    case acknowledgementSaved = "acknowledgement_saved"
    case acknowledgementImported = "acknowledgement_imported"
    case acknowledgementConsumed = "acknowledgement_consumed"
    case trustCommitted = "trust_committed"
}

public struct AtlasVaultStagedPairingArtifact: Codable, Equatable, Sendable {
    public let kind: AtlasVaultPairingArtifactKind
    public let sha256: String
    public let byteCount: Int

    enum CodingKeys: String, CodingKey, CaseIterable {
        case kind
        case sha256
        case byteCount = "byte_count"
    }

    public init(
        kind: AtlasVaultPairingArtifactKind,
        sha256: String,
        byteCount: Int
    ) throws {
        guard byteCount > 0, byteCount <= 128 * 1_024 * 1_024 else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        self.kind = kind
        self.sha256 = try AtlasVaultPairingValidation.lowercaseHex(sha256)
        self.byteCount = byteCount
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultPairingValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let values = try decoder.container(keyedBy: CodingKeys.self)
            let kindText = try values.decode(String.self, forKey: .kind)
            guard let kind = AtlasVaultPairingArtifactKind(rawValue: kindText) else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            try self.init(
                kind: kind,
                sha256: values.decode(String.self, forKey: .sha256),
                byteCount: values.decode(Int.self, forKey: .byteCount)
            )
        } catch {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
    }

    public func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(kind.rawValue, forKey: .kind)
        try values.encode(sha256, forKey: .sha256)
        try values.encode(byteCount, forKey: .byteCount)
    }
}

public struct AtlasVaultPairingTransaction: Codable, Equatable, Sendable {
    public static let maximumByteCount = 64 * 1_024

    public let format: String
    public let version: Int
    public let transactionID: String
    public let revision: String
    public let parentRevision: String?
    public let role: AtlasVaultPairingRole
    public let stage: AtlasVaultPairingStage
    public let createdAt: String
    public let updatedAt: String
    public let installedAt: String?
    public let localDeviceID: String
    public let peerDeviceID: String?
    public let transcriptSHA256: String?
    public let offerSHA256: String?
    public let acceptanceSHA256: String?
    public let deliverySHA256: String?
    public let acknowledgementSHA256: String?
    public let bootstrapSHA256: String?
    public let vaultID: String?
    public let keyEpoch: Int?
    private let ephemeralPrivateKey: Data?
    public let storeSHA256: String?
    public let vaultKeySHA256: String?
    public let selectionCommitted: Bool
    public let stagedArtifacts: [AtlasVaultStagedPairingArtifact]

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case transactionID = "transaction_id"
        case revision
        case parentRevision = "parent_revision"
        case role
        case stage
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case installedAt = "installed_at"
        case localDeviceID = "local_device_id"
        case peerDeviceID = "peer_device_id"
        case transcriptSHA256 = "transcript_sha256"
        case offerSHA256 = "offer_sha256"
        case acceptanceSHA256 = "acceptance_sha256"
        case deliverySHA256 = "delivery_sha256"
        case acknowledgementSHA256 = "acknowledgement_sha256"
        case bootstrapSHA256 = "bootstrap_sha256"
        case vaultID = "vault_id"
        case keyEpoch = "key_epoch"
        case ephemeralPrivateKey = "ephemeral_private_key"
        case storeSHA256 = "store_sha256"
        case vaultKeySHA256 = "vault_key_sha256"
        case selectionCommitted = "selection_committed"
        case stagedArtifacts = "staged_artifacts"
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultPairingValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let values = try decoder.container(keyedBy: CodingKeys.self)
            format = try values.decode(String.self, forKey: .format)
            version = try values.decode(Int.self, forKey: .version)
            transactionID = try AtlasVaultPairingValidation.canonicalUUID(
                values.decode(String.self, forKey: .transactionID)
            )
            revision = try AtlasVaultPairingValidation.canonicalUUID(
                values.decode(String.self, forKey: .revision)
            )
            parentRevision = try values.decodeIfPresent(
                String.self, forKey: .parentRevision
            ).map(AtlasVaultPairingValidation.canonicalUUID)
            role = try values.decode(AtlasVaultPairingRole.self, forKey: .role)
            stage = try values.decode(AtlasVaultPairingStage.self, forKey: .stage)
            createdAt = try AtlasVaultPairingValidation.timestamp(
                values.decode(String.self, forKey: .createdAt)
            )
            updatedAt = try AtlasVaultPairingValidation.timestamp(
                values.decode(String.self, forKey: .updatedAt)
            )
            installedAt = try values.decodeIfPresent(
                String.self, forKey: .installedAt
            ).map(AtlasVaultPairingValidation.timestamp)
            localDeviceID = try Self.deviceID(
                values.decode(String.self, forKey: .localDeviceID)
            )
            peerDeviceID = try values.decodeIfPresent(
                String.self, forKey: .peerDeviceID
            ).map(Self.deviceID)
            transcriptSHA256 = try Self.optionalSHA(values, .transcriptSHA256)
            offerSHA256 = try Self.optionalSHA(values, .offerSHA256)
            acceptanceSHA256 = try Self.optionalSHA(values, .acceptanceSHA256)
            deliverySHA256 = try Self.optionalSHA(values, .deliverySHA256)
            acknowledgementSHA256 = try Self.optionalSHA(
                values, .acknowledgementSHA256
            )
            bootstrapSHA256 = try Self.optionalSHA(values, .bootstrapSHA256)
            vaultID = try values.decodeIfPresent(String.self, forKey: .vaultID)
            if let vaultID,
               (try? AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID))
                != vaultID
            {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            keyEpoch = try values.decodeIfPresent(Int.self, forKey: .keyEpoch)
            if let keyEpoch, keyEpoch <= 0 {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            if let encoded = try values.decodeIfPresent(
                String.self, forKey: .ephemeralPrivateKey
            ) {
                ephemeralPrivateKey = try AtlasVaultPairingValidation.canonicalBase64(
                    encoded, length: 32
                )
            } else {
                ephemeralPrivateKey = nil
            }
            storeSHA256 = try Self.optionalSHA(values, .storeSHA256)
            vaultKeySHA256 = try Self.optionalSHA(values, .vaultKeySHA256)
            selectionCommitted = try values.decode(
                Bool.self, forKey: .selectionCommitted
            )
            stagedArtifacts = try values.decode(
                [AtlasVaultStagedPairingArtifact].self,
                forKey: .stagedArtifacts
            )
            let roleStages = Self.stages(for: role)
            let createdDate = try AtlasVaultPairingValidation.date(createdAt)
            let updatedDate = try AtlasVaultPairingValidation.date(updatedAt)
            let installedDate = try installedAt.map(
                AtlasVaultPairingValidation.date
            )
            let runtimeActivated = role == .invitee
                && roleStages.firstIndex(of: stage).map { stageIndex in
                    roleStages.firstIndex(of: .runtimeActivated).map {
                        stageIndex >= $0
                    } ?? false
                } ?? false
            let installedInRange = installedDate.map {
                $0 >= createdDate && $0 <= updatedDate
            } ?? true
            guard
                format == "atlasvault-pairing-transaction",
                version == 1,
                updatedDate >= createdDate,
                roleStages.contains(stage),
                stagedArtifacts.count <= AtlasVaultPairingArtifactKind.allCases.count,
                Set(stagedArtifacts.map(\.kind)).count == stagedArtifacts.count,
                stagedArtifacts.map(\.kind.sortIndex)
                    == stagedArtifacts.map(\.kind.sortIndex).sorted(),
                !selectionCommitted || role == .invitee,
                runtimeActivated == (installedAt != nil),
                installedInRange
            else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
        } catch {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
    }

    public func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(format, forKey: .format)
        try values.encode(version, forKey: .version)
        try values.encode(transactionID, forKey: .transactionID)
        try values.encode(revision, forKey: .revision)
        try values.encode(parentRevision, forKey: .parentRevision)
        try values.encode(role, forKey: .role)
        try values.encode(stage, forKey: .stage)
        try values.encode(createdAt, forKey: .createdAt)
        try values.encode(updatedAt, forKey: .updatedAt)
        try values.encode(installedAt, forKey: .installedAt)
        try values.encode(localDeviceID, forKey: .localDeviceID)
        try values.encode(peerDeviceID, forKey: .peerDeviceID)
        try values.encode(transcriptSHA256, forKey: .transcriptSHA256)
        try values.encode(offerSHA256, forKey: .offerSHA256)
        try values.encode(acceptanceSHA256, forKey: .acceptanceSHA256)
        try values.encode(deliverySHA256, forKey: .deliverySHA256)
        try values.encode(acknowledgementSHA256, forKey: .acknowledgementSHA256)
        try values.encode(bootstrapSHA256, forKey: .bootstrapSHA256)
        try values.encode(vaultID, forKey: .vaultID)
        try values.encode(keyEpoch, forKey: .keyEpoch)
        try values.encode(
            ephemeralPrivateKey?.base64EncodedString(),
            forKey: .ephemeralPrivateKey
        )
        try values.encode(storeSHA256, forKey: .storeSHA256)
        try values.encode(vaultKeySHA256, forKey: .vaultKeySHA256)
        try values.encode(selectionCommitted, forKey: .selectionCommitted)
        try values.encode(stagedArtifacts, forKey: .stagedArtifacts)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            guard !data.isEmpty, data.count <= maximumByteCount else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            let value = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try value.canonicalData(), data
            ) else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            return value
        } catch {
            throw AtlasVaultPairingTransactionError.invalidTransaction
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

    public static func create(
        transactionID: String,
        revision: String,
        role: AtlasVaultPairingRole,
        stage: AtlasVaultPairingStage,
        createdAt: String,
        installedAt: String? = nil,
        localDeviceID: String,
        peerDeviceID: String? = nil,
        transcriptSHA256: String? = nil,
        offerSHA256: String? = nil,
        acceptanceSHA256: String? = nil,
        deliverySHA256: String? = nil,
        acknowledgementSHA256: String? = nil,
        bootstrapSHA256: String? = nil,
        vaultID: String? = nil,
        keyEpoch: Int? = nil,
        ephemeralKeyMaterial: Data? = nil,
        storeSHA256: String? = nil,
        vaultKeySHA256: String? = nil,
        selectionCommitted: Bool = false,
        stagedArtifacts: [AtlasVaultStagedPairingArtifact] = []
    ) throws -> Self {
        try decodeStrict(try canonicalTransactionData([
            "format": "atlasvault-pairing-transaction",
            "version": 1,
            "transaction_id": transactionID,
            "revision": revision,
            "parent_revision": NSNull(),
            "role": role.rawValue,
            "stage": stage.rawValue,
            "created_at": createdAt,
            "updated_at": createdAt,
            "installed_at": installedAt ?? NSNull(),
            "local_device_id": localDeviceID,
            "peer_device_id": peerDeviceID ?? NSNull(),
            "transcript_sha256": transcriptSHA256 ?? NSNull(),
            "offer_sha256": offerSHA256 ?? NSNull(),
            "acceptance_sha256": acceptanceSHA256 ?? NSNull(),
            "delivery_sha256": deliverySHA256 ?? NSNull(),
            "acknowledgement_sha256": acknowledgementSHA256 ?? NSNull(),
            "bootstrap_sha256": bootstrapSHA256 ?? NSNull(),
            "vault_id": vaultID ?? NSNull(),
            "key_epoch": keyEpoch ?? NSNull(),
            "ephemeral_private_key": ephemeralKeyMaterial?
                .base64EncodedString() ?? NSNull(),
            "store_sha256": storeSHA256 ?? NSNull(),
            "vault_key_sha256": vaultKeySHA256 ?? NSNull(),
            "selection_committed": selectionCommitted,
            "staged_artifacts": try stagedArtifacts.map {
                try JSONSerialization.jsonObject(with: JSONEncoder().encode($0))
            },
        ]))
    }

    public func advanced(
        to nextStage: AtlasVaultPairingStage,
        revision nextRevision: String,
        updatedAt: String,
        installedAt: String? = nil,
        peerDeviceID: String? = nil,
        transcriptSHA256: String? = nil,
        offerSHA256: String? = nil,
        acceptanceSHA256: String? = nil,
        deliverySHA256: String? = nil,
        acknowledgementSHA256: String? = nil,
        bootstrapSHA256: String? = nil,
        vaultID: String? = nil,
        keyEpoch: Int? = nil,
        ephemeralKeyMaterial: Data? = nil,
        clearEphemeralKeyMaterial: Bool = false,
        storeSHA256: String? = nil,
        vaultKeySHA256: String? = nil,
        selectionCommitted: Bool? = nil,
        stagedArtifacts: [AtlasVaultStagedPairingArtifact]? = nil
    ) throws -> Self {
        let stages = Self.stages(for: role)
        guard
            let currentIndex = stages.firstIndex(of: stage),
            let nextIndex = stages.firstIndex(of: nextStage),
            nextIndex >= currentIndex
        else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        guard
            let object = try JSONSerialization.jsonObject(
                with: canonicalData()
            ) as? [String: Any]
        else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        var value = object
        value["revision"] = nextRevision
        value["parent_revision"] = revision
        value["stage"] = nextStage.rawValue
        value["updated_at"] = updatedAt
        Self.setIfPresent(installedAt, key: "installed_at", in: &value)
        Self.setIfPresent(peerDeviceID, key: "peer_device_id", in: &value)
        Self.setIfPresent(
            transcriptSHA256,
            key: "transcript_sha256",
            in: &value
        )
        Self.setIfPresent(offerSHA256, key: "offer_sha256", in: &value)
        Self.setIfPresent(
            acceptanceSHA256,
            key: "acceptance_sha256",
            in: &value
        )
        Self.setIfPresent(deliverySHA256, key: "delivery_sha256", in: &value)
        Self.setIfPresent(
            acknowledgementSHA256,
            key: "acknowledgement_sha256",
            in: &value
        )
        Self.setIfPresent(
            bootstrapSHA256,
            key: "bootstrap_sha256",
            in: &value
        )
        Self.setIfPresent(vaultID, key: "vault_id", in: &value)
        Self.setIfPresent(keyEpoch, key: "key_epoch", in: &value)
        Self.setIfPresent(storeSHA256, key: "store_sha256", in: &value)
        Self.setIfPresent(
            vaultKeySHA256,
            key: "vault_key_sha256",
            in: &value
        )
        if clearEphemeralKeyMaterial {
            value["ephemeral_private_key"] = NSNull()
        } else if let ephemeralKeyMaterial {
            value["ephemeral_private_key"] = ephemeralKeyMaterial
                .base64EncodedString()
        }
        if let selectionCommitted {
            value["selection_committed"] = selectionCommitted
        }
        if let stagedArtifacts {
            value["staged_artifacts"] = try stagedArtifacts.map {
                try JSONSerialization.jsonObject(with: JSONEncoder().encode($0))
            }
        }
        let replacement = try Self.decodeStrict(
            try Self.canonicalTransactionData(value)
        )
        guard
            replacement.transactionID == transactionID,
            replacement.role == role,
            replacement.localDeviceID == localDeviceID,
            replacement.createdAt == createdAt,
            installedAt == nil || replacement.installedAt == installedAt,
            replacement.parentRevision == revision,
            replacement.revision != revision,
            (!self.selectionCommitted || replacement.selectionCommitted)
        else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        return replacement
    }

    public func copyEphemeralKeyMaterial() -> Data? {
        ephemeralPrivateKey.map { Data($0) }
    }

    private static func optionalSHA(
        _ values: KeyedDecodingContainer<CodingKeys>,
        _ key: CodingKeys
    ) throws -> String? {
        try values.decodeIfPresent(String.self, forKey: key)
            .map(AtlasVaultPairingValidation.lowercaseHex)
    }

    private static func deviceID(_ value: String) throws -> String {
        guard value.range(
            of: #"^avd1-[0-9a-f]{64}$"#,
            options: .regularExpression
        ) != nil else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        return value
    }

    private static func canonicalTransactionData(
        _ value: [String: Any]
    ) throws -> Data {
        guard JSONSerialization.isValidJSONObject(value) else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        return try JSONSerialization.data(
            withJSONObject: value,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
    }

    private static func setIfPresent<T>(
        _ candidate: T?,
        key: String,
        in value: inout [String: Any]
    ) {
        if let candidate {
            value[key] = candidate
        }
    }

    private static func stages(for role: AtlasVaultPairingRole) -> [AtlasVaultPairingStage] {
        switch role {
        case .inviter:
            return [
                .offerCreated, .offerSaved, .acceptanceImported, .sasConfirmed,
                .deliveryCreated, .deliverySaved, .acknowledgementImported,
                .acknowledgementConsumed, .trustCommitted,
            ]
        case .invitee:
            return [
                .offerImported, .acceptanceCreated, .acceptanceSaved, .sasConfirmed,
                .offerConsumed, .deliveryImported, .storeCreated, .keyCreated,
                .selectionCommitted, .runtimeActivated, .trustCommitted,
                .acknowledgementCreated, .acknowledgementSaved,
            ]
        }
    }
}

public enum AtlasVaultPairingStateStoreError:
    Error, Equatable, Sendable, CustomStringConvertible
{
    case unavailable
    case invalidState
    case collision
    case stale

    public var description: String {
        "AtlasVault pairing state storage failed."
    }
}

private struct AtlasKeychainPairingStateStore<Client: AtlasKeychainClient>:
    Sendable
{
    let client: Client
    let service: String
    let account: String
    let maximumByteCount: Int

    func load() throws -> Data? {
        let result = client.copyMatching(query)
        switch result.status {
        case errSecItemNotFound: return nil
        case errSecSuccess:
            guard
                let data = result.valueData,
                !data.isEmpty,
                data.count <= maximumByteCount
            else {
                throw AtlasVaultPairingStateStoreError.invalidState
            }
            return data
        default: throw AtlasVaultPairingStateStoreError.unavailable
        }
    }

    func create(_ data: Data) throws {
        guard !data.isEmpty, data.count <= maximumByteCount else {
            throw AtlasVaultPairingStateStoreError.invalidState
        }
        let item = AtlasKeychainItem(
            service: service,
            account: account,
            valueData: data,
            accessibility: .afterFirstUnlockThisDeviceOnly
        )
        switch client.add(item) {
        case errSecSuccess:
            guard try load() == data else {
                throw AtlasVaultPairingStateStoreError.unavailable
            }
        case errSecDuplicateItem:
            throw AtlasVaultPairingStateStoreError.collision
        default:
            throw AtlasVaultPairingStateStoreError.unavailable
        }
    }

    func replace(_ data: Data, expectedSHA256: String) throws {
        guard !data.isEmpty, data.count <= maximumByteCount else {
            throw AtlasVaultPairingStateStoreError.invalidState
        }
        let expected = try validatedSHA(expectedSHA256)
        guard let current = try load() else {
            throw AtlasVaultPairingStateStoreError.stale
        }
        let actual = AtlasVaultDeviceIdentityValidation.lowercaseHex(
            Data(SHA256.hash(data: current))
        )
        guard constantTimeEqual(actual, expected) else {
            throw AtlasVaultPairingStateStoreError.stale
        }
        guard client.update(
            query,
            with: AtlasKeychainUpdate(valueData: data)
        ) == errSecSuccess, try load() == data else {
            throw AtlasVaultPairingStateStoreError.unavailable
        }
    }

    func delete(expectedSHA256: String) throws {
        let expected = try validatedSHA(expectedSHA256)
        guard let current = try load() else {
            throw AtlasVaultPairingStateStoreError.stale
        }
        let actual = AtlasVaultDeviceIdentityValidation.lowercaseHex(
            Data(SHA256.hash(data: current))
        )
        guard constantTimeEqual(actual, expected) else {
            throw AtlasVaultPairingStateStoreError.stale
        }
        guard client.delete(query) == errSecSuccess, try load() == nil else {
            throw AtlasVaultPairingStateStoreError.unavailable
        }
    }

    private var query: AtlasKeychainQuery {
        AtlasKeychainQuery(service: service, account: account)
    }

    private func validatedSHA(_ value: String) throws -> String {
        do { return try AtlasVaultPairingValidation.lowercaseHex(value) }
        catch { throw AtlasVaultPairingStateStoreError.invalidState }
    }

    private func constantTimeEqual(_ left: String, _ right: String) -> Bool {
        AtlasVaultDeviceIdentityValidation.constantTimeEqual(
            Data(left.utf8), Data(right.utf8)
        )
    }
}

public struct AtlasKeychainTrustedDeviceRegistryStore<
    Client: AtlasKeychainClient
>: Sendable {
    public static var service: String { "com.atlasvault.trusted-devices.v1" }
    private let store: AtlasKeychainPairingStateStore<Client>

    public init(client: Client) {
        store = AtlasKeychainPairingStateStore(
            client: client,
            service: Self.service,
            account: "state-v1",
            maximumByteCount: 2 * 1_024 * 1_024
        )
    }

    public func load() throws -> AtlasVaultTrustedDeviceRegistry? {
        guard let data = try store.load() else { return nil }
        let registry = try AtlasVaultTrustedDeviceRegistry.decodeStrict(data)
        try Self.verify(registry)
        return registry
    }

    public func create(_ value: AtlasVaultTrustedDeviceRegistry) throws {
        try Self.verify(value)
        try store.create(value.canonicalData())
    }

    public func replace(
        _ value: AtlasVaultTrustedDeviceRegistry,
        expectedSHA256: String
    ) throws {
        try Self.verify(value)
        try store.replace(value.canonicalData(), expectedSHA256: expectedSHA256)
    }

    private static func verify(_ value: AtlasVaultTrustedDeviceRegistry) throws {
        do {
            for peer in value.devices {
                guard try peer.peerDescriptor.verifiedDescriptor().deviceID
                    == peer.peerDeviceID
                else {
                    throw AtlasVaultPairingStateStoreError.invalidState
                }
            }
        } catch {
            throw AtlasVaultPairingStateStoreError.invalidState
        }
    }
}

public struct AtlasKeychainPairingReplayStore<
    Client: AtlasKeychainClient
>: Sendable {
    public static var service: String { "com.atlasvault.pairing-replay.v1" }
    private let store: AtlasKeychainPairingStateStore<Client>

    public init(client: Client) {
        store = AtlasKeychainPairingStateStore(
            client: client,
            service: Self.service,
            account: "state-v1",
            maximumByteCount: 2 * 1_024 * 1_024
        )
    }

    public func load() throws -> AtlasVaultPairingReplayStore? {
        try store.load().map(AtlasVaultPairingReplayStore.decodeStrict)
    }

    public func create(_ value: AtlasVaultPairingReplayStore) throws {
        try store.create(value.canonicalData())
    }

    public func replace(
        _ value: AtlasVaultPairingReplayStore,
        expectedSHA256: String
    ) throws {
        try store.replace(value.canonicalData(), expectedSHA256: expectedSHA256)
    }
}

public struct AtlasKeychainPairingTransactionStore<
    Client: AtlasKeychainClient
>: Sendable {
    public static var service: String { "com.atlasvault.pairing-transaction.v1" }
    private let store: AtlasKeychainPairingStateStore<Client>

    public init(client: Client) {
        store = AtlasKeychainPairingStateStore(
            client: client,
            service: Self.service,
            account: "pending-v1",
            maximumByteCount: AtlasVaultPairingTransaction.maximumByteCount
        )
    }

    public func load() throws -> AtlasVaultPairingTransaction? {
        try store.load().map(AtlasVaultPairingTransaction.decodeStrict)
    }

    public func create(_ value: AtlasVaultPairingTransaction) throws {
        try store.create(value.canonicalData())
    }

    public func replace(
        _ value: AtlasVaultPairingTransaction,
        expectedSHA256: String
    ) throws {
        try store.replace(value.canonicalData(), expectedSHA256: expectedSHA256)
    }

    public func delete(expectedSHA256: String) throws {
        try store.delete(expectedSHA256: expectedSHA256)
    }
}

public extension AtlasKeychainTrustedDeviceRegistryStore
where Client == SecItemAtlasKeychainClient {
    init() { self.init(client: SecItemAtlasKeychainClient()) }
}

public extension AtlasKeychainPairingReplayStore
where Client == SecItemAtlasKeychainClient {
    init() { self.init(client: SecItemAtlasKeychainClient()) }
}

public extension AtlasKeychainPairingTransactionStore
where Client == SecItemAtlasKeychainClient {
    init() { self.init(client: SecItemAtlasKeychainClient()) }
}

public struct AtlasVaultPairingArtifactStageStore: Sendable {
    public static let maximumByteCount = 128 * 1_024 * 1_024
    private let root: URL

    public init(root: URL) throws {
        let resolved = root.resolvingSymlinksInPath().standardizedFileURL
        try FileManager.default.createDirectory(
            at: resolved,
            withIntermediateDirectories: true
        )
        self.root = resolved
    }

    public func create(_ artifact: AtlasVaultPairingArtifact) throws {
        let data = try artifact.canonicalData()
        try validate(data)
        let destination = try url(for: artifact.kind)
        guard !FileManager.default.fileExists(atPath: destination.path) else {
            throw AtlasVaultPairingStateStoreError.collision
        }
        let temporary = root.appendingPathComponent(
            ".pairing-\(UUID().uuidString.lowercased()).tmp",
            isDirectory: false
        )
        guard FileManager.default.createFile(
            atPath: temporary.path,
            contents: nil,
            attributes: [.posixPermissions: 0o600]
        ) else { throw AtlasVaultPairingStateStoreError.unavailable }
        do {
            let handle = try FileHandle(forWritingTo: temporary)
            try handle.write(contentsOf: data)
            try handle.synchronize()
            try handle.close()
            try FileManager.default.moveItem(at: temporary, to: destination)
            guard try Data(contentsOf: destination) == data else {
                throw AtlasVaultPairingStateStoreError.unavailable
            }
        } catch {
            try? FileManager.default.removeItem(at: temporary)
            throw AtlasVaultPairingStateStoreError.unavailable
        }
    }

    public func read(kind: AtlasVaultPairingArtifactKind) throws
        -> AtlasVaultPairingArtifact?
    {
        let location = try url(for: kind)
        guard FileManager.default.fileExists(atPath: location.path) else {
            return nil
        }
        let values = try location.resourceValues(
            forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]
        )
        guard
            values.isRegularFile == true,
            values.isSymbolicLink != true,
            let count = values.fileSize,
            count > 0,
            count <= Self.maximumByteCount
        else { throw AtlasVaultPairingStateStoreError.invalidState }
        let data = try Data(contentsOf: location)
        try validate(data)
        let artifact = try AtlasVaultPairingArtifact.decodeStrict(data)
        guard artifact.kind == kind else {
            throw AtlasVaultPairingStateStoreError.invalidState
        }
        return artifact
    }

    public func delete(
        kind: AtlasVaultPairingArtifactKind,
        expectedSHA256: String
    ) throws {
        guard let artifact = try read(kind: kind) else {
            throw AtlasVaultPairingStateStoreError.stale
        }
        let expected = try AtlasVaultPairingValidation.lowercaseHex(expectedSHA256)
        guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
            Data(try artifact.sha256Hex().utf8), Data(expected.utf8)
        ) else { throw AtlasVaultPairingStateStoreError.stale }
        let location = try url(for: kind)
        try FileManager.default.removeItem(at: location)
        guard !FileManager.default.fileExists(atPath: location.path) else {
            throw AtlasVaultPairingStateStoreError.unavailable
        }
    }

    private func url(for kind: AtlasVaultPairingArtifactKind) throws -> URL {
        let value = root.appendingPathComponent(
            "\(kind.rawValue).atlaspair",
            isDirectory: false
        ).standardizedFileURL
        guard
            value.deletingLastPathComponent().standardizedFileURL.path
                == root.standardizedFileURL.path
        else {
            throw AtlasVaultPairingStateStoreError.invalidState
        }
        return value
    }

    private func validate(_ data: Data) throws {
        guard !data.isEmpty, data.count <= Self.maximumByteCount else {
            throw AtlasVaultPairingStateStoreError.invalidState
        }
    }
}

public struct AtlasVaultPairingDocument: FileDocument, Sendable {
    public static var readableContentTypes: [UTType] {
        [UTType(filenameExtension: "atlaspair") ?? .json]
    }

    public let artifact: AtlasVaultPairingArtifact

    public init(artifact: AtlasVaultPairingArtifact) {
        self.artifact = artifact
    }

    public init(configuration: ReadConfiguration) throws {
        guard
            let data = configuration.file.regularFileContents,
            !data.isEmpty,
            data.count <= AtlasVaultPairingArtifactStageStore.maximumByteCount
        else {
            throw AtlasVaultPairingStateStoreError.invalidState
        }
        artifact = try AtlasVaultPairingArtifact.decodeStrict(data)
    }

    public func fileWrapper(configuration _: WriteConfiguration) throws
        -> FileWrapper
    {
        let data = try artifact.canonicalData()
        guard
            !data.isEmpty,
            data.count <= AtlasVaultPairingArtifactStageStore.maximumByteCount
        else {
            throw AtlasVaultPairingStateStoreError.invalidState
        }
        return FileWrapper(regularFileWithContents: data)
    }
}

public enum AtlasVaultPairingCleanInstallDisposition: Sendable {
    case clean
    case migrationRequired
    case existingVault
    case unavailable
    case recoveryRequired
}

public enum AtlasVaultTrustedPairingDisposition: Sendable {
    case ready
    case identityReady
    case offerReady
    case offerSaved
    case acceptanceReady
    case acceptanceSaved
    case codesReady
    case codesConfirmed
    case deliveryReady
    case deliverySaved
    case acknowledgementReady
    case acknowledgementSaved
    case completed
    case cancelled
    case migrationRequired
    case existingVault
    case unavailable
    case recoveryRequired
    case failed
}

public struct AtlasVaultTrustedPairingResult: Sendable {
    public let disposition: AtlasVaultTrustedPairingDisposition
    public let role: AtlasVaultPairingRole?
    public let stage: AtlasVaultPairingStage?
    public let localFingerprint: String?
    public let peerFingerprint: String?
    public let sas: String?
    public let expiresAt: String?
    public let trusted: Bool
    public let pendingTransaction: Bool

    public init(
        disposition: AtlasVaultTrustedPairingDisposition,
        role: AtlasVaultPairingRole? = nil,
        stage: AtlasVaultPairingStage? = nil,
        localFingerprint: String? = nil,
        peerFingerprint: String? = nil,
        sas: String? = nil,
        expiresAt: String? = nil,
        trusted: Bool = false,
        pendingTransaction: Bool = false
    ) {
        self.disposition = disposition
        self.role = role
        self.stage = stage
        self.localFingerprint = localFingerprint
        self.peerFingerprint = peerFingerprint
        self.sas = sas
        self.expiresAt = expiresAt
        self.trusted = trusted
        self.pendingTransaction = pendingTransaction
    }
}

public protocol AtlasVaultTrustedPairingCoordinating: Sendable {
    func inspect() async -> AtlasVaultTrustedPairingResult
    func createDeviceIdentity() async -> AtlasVaultTrustedPairingResult
    func createPairingOffer() async -> AtlasVaultTrustedPairingResult
    func artifactToSave(
        _ kind: AtlasVaultPairingArtifactKind
    ) async throws -> AtlasVaultPairingArtifact
    func pairingArtifactSaveFinished(
        _ kind: AtlasVaultPairingArtifactKind,
        committed: Bool
    ) async -> AtlasVaultTrustedPairingResult
    func importPairingOffer(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult
    func importPairingAcceptance(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult
    func confirmCodesMatch() async -> AtlasVaultTrustedPairingResult
    func importKeyDelivery(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult
    func importPairingAcknowledgement(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult
    func resumePairing() async -> AtlasVaultTrustedPairingResult
    func discardPairing() async -> AtlasVaultTrustedPairingResult
    func stop() async
}

public struct AtlasVaultPairingActiveVault: Sendable {
    public let vaultID: String
    public let store: AtlasVaultLocalStoreEnvelope
    private let keyMaterial: Data

    public init(
        vaultID: String,
        store: AtlasVaultLocalStoreEnvelope,
        keyMaterial: Data
    ) throws {
        guard
            (try? AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID))
                == vaultID,
            keyMaterial.count == AtlasVaultRecordCrypto.vaultKeyByteCount
        else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        self.vaultID = vaultID
        self.store = store
        self.keyMaterial = Data(keyMaterial)
    }

    public func withKeyMaterial<Result>(
        _ operation: (Data) throws -> Result
    ) rethrows -> Result {
        try operation(Data(keyMaterial))
    }
}

public struct AtlasVaultTrustedPairingEnvironment: Sendable {
    public let loadIdentity:
        @Sendable () async throws -> AtlasVaultDeviceIdentity?
    public let createIdentity:
        @Sendable () async throws -> AtlasVaultDeviceIdentity
    public let loadTransaction:
        @Sendable () async throws -> AtlasVaultPairingTransaction?
    public let createTransaction:
        @Sendable (AtlasVaultPairingTransaction) async throws -> Void
    public let replaceTransaction:
        @Sendable (
            AtlasVaultPairingTransaction,
            String
        ) async throws -> Void
    public let deleteTransaction:
        @Sendable (String) async throws -> Void
    public let loadArtifact:
        @Sendable (AtlasVaultPairingArtifactKind) async throws
            -> AtlasVaultPairingArtifact?
    public let createArtifact:
        @Sendable (AtlasVaultPairingArtifact) async throws -> Void
    public let deleteArtifact:
        @Sendable (AtlasVaultPairingArtifactKind, String) async throws -> Void
    public let loadRegistry:
        @Sendable () async throws -> AtlasVaultTrustedDeviceRegistry?
    public let createRegistry:
        @Sendable (AtlasVaultTrustedDeviceRegistry) async throws -> Void
    public let replaceRegistry:
        @Sendable (AtlasVaultTrustedDeviceRegistry, String) async throws -> Void
    public let loadReplay:
        @Sendable () async throws -> AtlasVaultPairingReplayStore?
    public let createReplay:
        @Sendable (AtlasVaultPairingReplayStore) async throws -> Void
    public let replaceReplay:
        @Sendable (AtlasVaultPairingReplayStore, String) async throws -> Void
    public let activeVault:
        @Sendable () async throws -> AtlasVaultPairingActiveVault?
    public let cleanInstall:
        @Sendable () async -> AtlasVaultPairingCleanInstallDisposition
    public let loadStore:
        @Sendable (String, Data) async throws -> AtlasVaultLocalStoreEnvelope?
    public let createStore:
        @Sendable (
            AtlasVaultLocalStoreEnvelope,
            String,
            Data
        ) async throws -> Void
    public let deleteStore: @Sendable (String) async throws -> Void
    public let loadStoredKey: @Sendable (String) async throws -> Data?
    public let createStoredKey:
        @Sendable (Data, String) async throws -> Void
    public let deleteStoredKey: @Sendable (String) async throws -> Void
    public let selectedVault: @Sendable () async throws -> String?
    public let createSelection: @Sendable (String) async throws -> Void
    public let activate: @Sendable (String, Data) async throws -> Bool
    public let validateProjection:
        @Sendable (
            AtlasVaultLocalStoreEnvelope,
            String,
            Data,
            Bool
        ) async throws -> Bool
    public let uuid: @Sendable () -> String
    public let timestamp: @Sendable () -> String
    public let randomBytes: @Sendable (Int) throws -> Data

    public init(
        loadIdentity: @escaping @Sendable () async throws
            -> AtlasVaultDeviceIdentity?,
        createIdentity: @escaping @Sendable () async throws
            -> AtlasVaultDeviceIdentity,
        loadTransaction: @escaping @Sendable () async throws
            -> AtlasVaultPairingTransaction?,
        createTransaction: @escaping @Sendable (
            AtlasVaultPairingTransaction
        ) async throws -> Void,
        replaceTransaction: @escaping @Sendable (
            AtlasVaultPairingTransaction,
            String
        ) async throws -> Void,
        deleteTransaction: @escaping @Sendable (String) async throws -> Void,
        loadArtifact: @escaping @Sendable (
            AtlasVaultPairingArtifactKind
        ) async throws -> AtlasVaultPairingArtifact?,
        createArtifact: @escaping @Sendable (
            AtlasVaultPairingArtifact
        ) async throws -> Void,
        deleteArtifact: @escaping @Sendable (
            AtlasVaultPairingArtifactKind,
            String
        ) async throws -> Void,
        loadRegistry: @escaping @Sendable () async throws
            -> AtlasVaultTrustedDeviceRegistry?,
        createRegistry: @escaping @Sendable (
            AtlasVaultTrustedDeviceRegistry
        ) async throws -> Void,
        replaceRegistry: @escaping @Sendable (
            AtlasVaultTrustedDeviceRegistry,
            String
        ) async throws -> Void,
        loadReplay: @escaping @Sendable () async throws
            -> AtlasVaultPairingReplayStore?,
        createReplay: @escaping @Sendable (
            AtlasVaultPairingReplayStore
        ) async throws -> Void,
        replaceReplay: @escaping @Sendable (
            AtlasVaultPairingReplayStore,
            String
        ) async throws -> Void,
        activeVault: @escaping @Sendable () async throws
            -> AtlasVaultPairingActiveVault?,
        cleanInstall: @escaping @Sendable () async
            -> AtlasVaultPairingCleanInstallDisposition,
        loadStore: @escaping @Sendable (
            String,
            Data
        ) async throws -> AtlasVaultLocalStoreEnvelope?,
        createStore: @escaping @Sendable (
            AtlasVaultLocalStoreEnvelope,
            String,
            Data
        ) async throws -> Void,
        deleteStore: @escaping @Sendable (String) async throws -> Void,
        loadStoredKey: @escaping @Sendable (String) async throws -> Data?,
        createStoredKey: @escaping @Sendable (
            Data,
            String
        ) async throws -> Void,
        deleteStoredKey: @escaping @Sendable (String) async throws -> Void,
        selectedVault: @escaping @Sendable () async throws -> String?,
        createSelection: @escaping @Sendable (String) async throws -> Void,
        activate: @escaping @Sendable (String, Data) async throws -> Bool,
        validateProjection: @escaping @Sendable (
            AtlasVaultLocalStoreEnvelope,
            String,
            Data,
            Bool
        ) async throws -> Bool,
        uuid: @escaping @Sendable () -> String = {
            UUID().uuidString.lowercased()
        },
        timestamp: @escaping @Sendable () -> String = {
            AtlasVaultTrustedPairingCoordinator.currentTimestamp()
        },
        randomBytes: @escaping @Sendable (Int) throws -> Data = { count in
            guard count > 0 else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            var bytes = Data(count: count)
            let status = bytes.withUnsafeMutableBytes { buffer in
                SecRandomCopyBytes(
                    kSecRandomDefault,
                    count,
                    buffer.baseAddress!
                )
            }
            guard status == errSecSuccess else {
                throw AtlasVaultPairingTransactionError.unavailable
            }
            return bytes
        }
    ) {
        self.loadIdentity = loadIdentity
        self.createIdentity = createIdentity
        self.loadTransaction = loadTransaction
        self.createTransaction = createTransaction
        self.replaceTransaction = replaceTransaction
        self.deleteTransaction = deleteTransaction
        self.loadArtifact = loadArtifact
        self.createArtifact = createArtifact
        self.deleteArtifact = deleteArtifact
        self.loadRegistry = loadRegistry
        self.createRegistry = createRegistry
        self.replaceRegistry = replaceRegistry
        self.loadReplay = loadReplay
        self.createReplay = createReplay
        self.replaceReplay = replaceReplay
        self.activeVault = activeVault
        self.cleanInstall = cleanInstall
        self.loadStore = loadStore
        self.createStore = createStore
        self.deleteStore = deleteStore
        self.loadStoredKey = loadStoredKey
        self.createStoredKey = createStoredKey
        self.deleteStoredKey = deleteStoredKey
        self.selectedVault = selectedVault
        self.createSelection = createSelection
        self.activate = activate
        self.validateProjection = validateProjection
        self.uuid = uuid
        self.timestamp = timestamp
        self.randomBytes = randomBytes
    }
}

public actor AtlasVaultTrustedPairingCoordinator:
    AtlasVaultTrustedPairingCoordinating
{
    private let environment: AtlasVaultTrustedPairingEnvironment
    private var operationInProgress = false
    private var stopped = false

    public init(environment: AtlasVaultTrustedPairingEnvironment) {
        self.environment = environment
    }

    public func inspect() async -> AtlasVaultTrustedPairingResult {
        await run {
            let transaction = try await self.environment.loadTransaction()
            let identity = try await self.environment.loadIdentity()
            guard let transaction else {
                return self.fixed(
                    identity == nil ? .ready : .identityReady,
                    local: identity
                )
            }
            return try await self.result(for: transaction, identity: identity)
        }
    }

    public func createDeviceIdentity() async
        -> AtlasVaultTrustedPairingResult
    {
        await run {
            let identity: AtlasVaultDeviceIdentity
            if let existing = try await self.environment.loadIdentity() {
                identity = existing
            } else {
                identity = try await self.environment.createIdentity()
            }
            return self.fixed(.identityReady, local: identity)
        }
    }

    public func createPairingOffer() async
        -> AtlasVaultTrustedPairingResult
    {
        await run {
            guard try await self.environment.loadTransaction() == nil,
                  let identity = try await self.environment.loadIdentity(),
                  let active = try await self.environment.activeVault()
            else {
                return self.fixed(.unavailable)
            }
            let issued = self.environment.timestamp()
            let expires = try Self.timestamp(
                adding: 600,
                to: issued
            )
            let offer = try AtlasVaultPairingFoundation.createOffer(
                inviter: identity,
                offerID: self.environment.uuid(),
                nonce: self.environment.randomBytes(32),
                issuedAt: issued,
                expiresAt: expires
            )
            let artifact = try AtlasVaultPairingArtifact.offer(offer)
            try await self.stage(artifact)
            let transaction = try AtlasVaultPairingTransaction.create(
                transactionID: self.environment.uuid(),
                revision: self.environment.uuid(),
                role: .inviter,
                stage: .offerCreated,
                createdAt: issued,
                localDeviceID: identity.deviceID,
                offerSHA256: artifact.sha256Hex(),
                vaultID: active.vaultID,
                keyEpoch: identity.descriptor.keyEpoch,
                stagedArtifacts: try self.metadata(for: [artifact])
            )
            try await self.environment.createTransaction(transaction)
            try await self.requireTransaction(transaction)
            return self.result(
                .offerReady,
                transaction,
                identity: identity,
                expiresAt: expires
            )
        }
    }

    public func artifactToSave(
        _ kind: AtlasVaultPairingArtifactKind
    ) async throws -> AtlasVaultPairingArtifact {
        guard !stopped, !operationInProgress,
              let transaction = try await environment.loadTransaction()
        else {
            throw AtlasVaultPairingTransactionError.unavailable
        }
        return try await requireArtifact(kind, transaction: transaction)
    }

    public func pairingArtifactSaveFinished(
        _ kind: AtlasVaultPairingArtifactKind,
        committed: Bool
    ) async -> AtlasVaultTrustedPairingResult {
        await run {
            guard committed else { return self.fixed(.cancelled) }
            let expected: (
                AtlasVaultPairingRole,
                AtlasVaultPairingStage,
                AtlasVaultPairingStage,
                AtlasVaultTrustedPairingDisposition
            )
            switch kind {
            case .offer:
                expected = (.inviter, .offerCreated, .offerSaved, .offerSaved)
            case .acceptance:
                expected = (
                    .invitee,
                    .acceptanceCreated,
                    .acceptanceSaved,
                    .acceptanceSaved
                )
            case .delivery:
                expected = (
                    .inviter,
                    .deliveryCreated,
                    .deliverySaved,
                    .deliverySaved
                )
            case .acknowledgement:
                expected = (
                    .invitee,
                    .acknowledgementCreated,
                    .acknowledgementSaved,
                    .acknowledgementSaved
                )
            }
            let transaction = try await self.requireStage(
                expected.0,
                expected.1
            )
            _ = try await self.requireArtifact(
                kind,
                transaction: transaction
            )
            let updated = try await self.advance(
                transaction,
                to: expected.2
            )
            if kind == .acknowledgement {
                try await self.clearTransaction(updated)
                return self.fixed(.completed, trusted: true)
            }
            return self.result(
                expected.3,
                updated,
                identity: try await self.environment.loadIdentity()
            )
        }
    }

    public func importPairingOffer(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult {
        await run {
            if let blocked = self.cleanInstallResult(
                await self.environment.cleanInstall()
            ) {
                return blocked
            }
            guard artifact.kind == .offer,
                  try await self.environment.loadTransaction() == nil,
                  let identity = try await self.environment.loadIdentity()
            else {
                return self.fixed(.recoveryRequired)
            }
            let now = self.environment.timestamp()
            let signedOffer = try artifact.signedOffer()
            let offer = try AtlasVaultPairingFoundation.verifyOffer(
                signedOffer,
                currentTime: now
            )
            guard offer.inviter.descriptor.deviceID != identity.deviceID else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            let acceptance = try AtlasVaultPairingFoundation.createAcceptance(
                invitee: identity,
                signedOffer: signedOffer,
                nonce: self.environment.randomBytes(32),
                acceptedAt: now,
                currentTime: now
            )
            let transcript = try AtlasVaultPairingFoundation.transcriptSHA256(
                offer: signedOffer,
                acceptance: acceptance
            )
            var transient = try self.environment.randomBytes(32)
            defer { Self.wipe(&transient) }
            let transientKey = try Curve25519.KeyAgreement.PrivateKey(
                rawRepresentation: transient
            )
            let request = try AtlasVaultKeyDelivery.createKeyRequest(
                invitee: identity,
                requestID: self.environment.uuid(),
                transcriptSHA256: transcript,
                inviterDeviceID: offer.inviter.descriptor.deviceID,
                inviteeEphemeralPublicKey:
                    transientKey.publicKey.rawRepresentation,
                nonce: self.environment.randomBytes(32),
                issuedAt: now,
                expiresAt: offer.expiresAt
            )
            var session = try AtlasVaultPairingFoundation.deriveSessionKey(
                localIdentity: identity,
                offer: signedOffer,
                acceptance: acceptance
            )
            defer { Self.wipe(&session) }
            let proofs = try AtlasVaultPairingFoundation.deriveProofs(
                sessionKey: session,
                transcriptSHA256: transcript
            )
            let sas = try AtlasVaultKeyDelivery.deriveSAS(
                pairingSessionKey: session,
                transcriptSHA256: transcript
            )
            let acceptanceArtifact = try AtlasVaultPairingArtifact.acceptance(
                acceptance,
                keyRequest: request,
                inviteeProof: proofs.invitee
            )
            try await self.stage(artifact)
            try await self.stage(acceptanceArtifact)
            let transaction = try AtlasVaultPairingTransaction.create(
                transactionID: self.environment.uuid(),
                revision: self.environment.uuid(),
                role: .invitee,
                stage: .acceptanceCreated,
                createdAt: now,
                localDeviceID: identity.deviceID,
                peerDeviceID: offer.inviter.descriptor.deviceID,
                transcriptSHA256: Self.hex(transcript),
                offerSHA256: artifact.sha256Hex(),
                acceptanceSHA256: acceptanceArtifact.sha256Hex(),
                ephemeralKeyMaterial: transient,
                stagedArtifacts: try self.metadata(
                    for: [artifact, acceptanceArtifact]
                )
            )
            try await self.environment.createTransaction(transaction)
            try await self.requireTransaction(transaction)
            return self.result(
                .acceptanceReady,
                transaction,
                identity: identity,
                peerDeviceID: offer.inviter.descriptor.deviceID,
                sas: sas,
                expiresAt: offer.expiresAt
            )
        }
    }

    public func importPairingAcceptance(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult {
        await run {
            guard artifact.kind == .acceptance,
                  let identity = try await self.environment.loadIdentity()
            else { return self.fixed(.recoveryRequired) }
            let transaction = try await self.requireStage(.inviter, .offerSaved)
            let offerArtifact = try await self.requireArtifact(
                .offer,
                transaction: transaction
            )
            let offer = try offerArtifact.signedOffer()
            let payload = try artifact.acceptancePayload()
            let transcript = try AtlasVaultPairingFoundation.transcriptSHA256(
                offer: offer,
                acceptance: payload.signedAcceptance
            )
            let inviteeID = payload.signedAcceptance.acceptance
                .invitee.descriptor.deviceID
            _ = try AtlasVaultKeyDelivery.verifyKeyRequest(
                payload.signedKeyRequest,
                transcriptSHA256: transcript,
                inviterDeviceID: identity.deviceID,
                inviteeDeviceID: inviteeID,
                currentTime: self.environment.timestamp()
            )
            var session = try AtlasVaultPairingFoundation.deriveSessionKey(
                localIdentity: identity,
                offer: offer,
                acceptance: payload.signedAcceptance
            )
            defer { Self.wipe(&session) }
            let proofs = try AtlasVaultPairingFoundation.deriveProofs(
                sessionKey: session,
                transcriptSHA256: transcript
            )
            guard Self.equal(proofs.invitee, payload.inviteeProof) else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            try await self.stage(artifact)
            let updated = try await self.advance(
                transaction,
                to: .acceptanceImported,
                peerDeviceID: inviteeID,
                transcriptSHA256: Self.hex(transcript),
                acceptanceSHA256: artifact.sha256Hex(),
                stagedArtifacts: try self.metadata(for: [offerArtifact, artifact])
            )
            return self.result(
                .codesReady,
                updated,
                identity: identity,
                peerDeviceID: inviteeID,
                sas: try AtlasVaultKeyDelivery.deriveSAS(
                    pairingSessionKey: session,
                    transcriptSHA256: transcript
                ),
                expiresAt: offer.offer.expiresAt
            )
        }
    }

    public func confirmCodesMatch() async
        -> AtlasVaultTrustedPairingResult
    {
        await run {
            guard let identity = try await self.environment.loadIdentity(),
                  let transaction = try await self.environment.loadTransaction()
            else { return self.fixed(.failed) }
            if transaction.role == .invitee {
                guard transaction.stage == .acceptanceSaved else {
                    return self.fixed(.failed, pending: true)
                }
                let confirmed = try await self.advance(
                    transaction,
                    to: .sasConfirmed
                )
                let offerArtifact = try await self.requireArtifact(
                    .offer,
                    transaction: confirmed
                )
                let offer = try offerArtifact.signedOffer()
                guard let transcriptHash = confirmed.transcriptSHA256 else {
                    throw AtlasVaultPairingTransactionError.invalidTransaction
                }
                try await self.consumeReplay(
                    AtlasVaultPairingReplayEntry(
                        kind: "offer",
                        objectID: offer.offer.offerID,
                        transcriptSHA256: transcriptHash,
                        consumedAt: self.environment.timestamp(),
                        expiresAt: offer.offer.expiresAt
                    ),
                    localDeviceID: identity.deviceID,
                    permitExactReplay: false
                )
                let consumed = try await self.advance(
                    confirmed,
                    to: .offerConsumed
                )
                return try await self.resultWithSAS(
                    .codesConfirmed,
                    transaction: consumed,
                    identity: identity
                )
            }

            guard transaction.stage == .acceptanceImported,
                  let active = try await self.environment.activeVault(),
                  active.vaultID == transaction.vaultID
            else { return self.fixed(.unavailable, pending: true) }
            let confirmed = try await self.advance(
                transaction,
                to: .sasConfirmed
            )
            let acceptanceArtifact = try await self.requireArtifact(
                .acceptance,
                transaction: confirmed
            )
            let acceptance = try acceptanceArtifact.acceptancePayload()
            guard let transcriptText = confirmed.transcriptSHA256,
                  let transcript = Self.data(hex: transcriptText)
            else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: active.store.vaultMetadata
            )
            let bootstrap = try AtlasVaultPairingBootstrap(
                snapshotID: self.environment.uuid(),
                createdAt: self.environment.timestamp(),
                vaultMetadata: metadata,
                records: active.store.records
            )
            var deliverySecret = try self.environment.randomBytes(32)
            defer { Self.wipe(&deliverySecret) }
            let delivery = try active.withKeyMaterial { material in
                try AtlasVaultKeyDelivery.createDelivery(
                    inviter: identity,
                    keyRequest: acceptance.signedKeyRequest,
                    transcriptSHA256: transcript,
                    bootstrap: bootstrap,
                    vaultKey: material,
                    inviterEphemeralPrivateKey: deliverySecret,
                    nonce: try self.environment.randomBytes(12),
                    deliveryID: self.environment.uuid(),
                    keyEpoch: confirmed.keyEpoch ?? 1,
                    expiresAt: acceptance.signedKeyRequest.request.expiresAt
                )
            }
            var session = try await self.sessionKey(
                transaction: confirmed,
                identity: identity
            )
            defer { Self.wipe(&session) }
            let proofs = try AtlasVaultPairingFoundation.deriveProofs(
                sessionKey: session,
                transcriptSHA256: transcript
            )
            let artifact = try AtlasVaultPairingArtifact.delivery(
                delivery,
                bootstrap: bootstrap,
                inviterProof: proofs.inviter
            )
            try await self.stage(artifact)
            let updated = try await self.advance(
                confirmed,
                to: .deliveryCreated,
                deliverySHA256: artifact.sha256Hex(),
                bootstrapSHA256: bootstrap.sha256Hex(),
                vaultID: active.vaultID,
                stagedArtifacts: try self.mergedMetadata(
                    transaction: confirmed,
                    artifact: artifact
                )
            )
            return self.result(
                .deliveryReady,
                updated,
                identity: identity,
                peerDeviceID: updated.peerDeviceID,
                sas: try AtlasVaultKeyDelivery.deriveSAS(
                    pairingSessionKey: session,
                    transcriptSHA256: transcript
                ),
                expiresAt: acceptance.signedKeyRequest.request.expiresAt
            )
        }
    }

    public func importKeyDelivery(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult {
        await run {
            guard artifact.kind == .delivery else {
                return self.fixed(.recoveryRequired)
            }
            let transaction = try await self.requireStage(
                .invitee,
                .offerConsumed
            )
            let payload = try artifact.deliveryPayload()
            try await self.stage(artifact)
            let imported = try await self.advance(
                transaction,
                to: .deliveryImported,
                deliverySHA256: artifact.sha256Hex(),
                bootstrapSHA256: payload.bootstrap.sha256Hex(),
                vaultID: payload.signedDelivery.delivery.vaultID,
                keyEpoch: payload.signedDelivery.delivery.keyEpoch,
                stagedArtifacts: try self.mergedMetadata(
                    transaction: transaction,
                    artifact: artifact
                )
            )
            return try await self.installInvitee(imported)
        }
    }

    public func importPairingAcknowledgement(
        _ artifact: AtlasVaultPairingArtifact
    ) async -> AtlasVaultTrustedPairingResult {
        await run {
            guard artifact.kind == .acknowledgement,
                  let identity = try await self.environment.loadIdentity()
            else { return self.fixed(.recoveryRequired) }
            let transaction = try await self.requireStage(
                .inviter,
                .deliverySaved
            )
            try await self.stage(artifact)
            let imported = try await self.advance(
                transaction,
                to: .acknowledgementImported,
                acknowledgementSHA256: artifact.sha256Hex(),
                stagedArtifacts: try self.mergedMetadata(
                    transaction: transaction,
                    artifact: artifact
                )
            )
            return try await self.completeInviterAcknowledgement(
                imported,
                identity: identity
            )
        }
    }

    public func resumePairing() async -> AtlasVaultTrustedPairingResult {
        await run {
            guard let transaction = try await self.environment.loadTransaction()
            else { return self.fixed(.identityReady) }
            if transaction.role == .invitee,
               Self.isAtLeast(transaction, .deliveryImported) {
                return try await self.installInvitee(transaction)
            }
            if transaction.role == .inviter,
               Self.isAtLeast(transaction, .acknowledgementImported),
               let identity = try await self.environment.loadIdentity() {
                return try await self.completeInviterAcknowledgement(
                    transaction,
                    identity: identity
                )
            }
            return try await self.result(for: transaction, identity: nil)
        }
    }

    public func discardPairing() async -> AtlasVaultTrustedPairingResult {
        await run {
            guard let transaction = try await self.environment.loadTransaction(),
                  !transaction.selectionCommitted,
                  transaction.stage != .selectionCommitted,
                  transaction.stage != .runtimeActivated,
                  transaction.stage != .trustCommitted,
                  transaction.stage != .acknowledgementCreated,
                  transaction.stage != .acknowledgementSaved,
                  !(transaction.role == .inviter
                    && Self.isAtLeast(transaction, .deliverySaved)),
                  try await self.environment.selectedVault() == nil
            else {
                return self.fixed(.recoveryRequired, pending: true)
            }
            if transaction.storeSHA256 != nil
                || transaction.vaultKeySHA256 != nil
            {
                var context = try await self.inviteeContext(transaction)
                defer { Self.wipe(&context.recoveredKey) }
                let vaultID = context.delivery.signedDelivery.delivery.vaultID
                if let expectedStoreHash = transaction.storeSHA256 {
                    guard
                        let stored = try await self.environment.loadStore(
                            vaultID,
                            context.recoveredKey
                        ),
                        Self.sha256(try AtlasVaultLocalStoreIO.encode(stored))
                            == expectedStoreHash
                    else {
                        throw AtlasVaultPairingTransactionError.stale
                    }
                    try await self.environment.deleteStore(vaultID)
                    guard try await self.environment.loadStore(
                        vaultID,
                        context.recoveredKey
                    ) == nil else {
                        throw AtlasVaultPairingTransactionError.unavailable
                    }
                }
                if let expectedKeyHash = transaction.vaultKeySHA256 {
                    var storedKey = try await self.environment.loadStoredKey(
                        vaultID
                    )
                    defer { if storedKey != nil { Self.wipe(&storedKey!) } }
                    guard let key = storedKey,
                          Self.sha256(key) == expectedKeyHash,
                          Self.equal(key, context.recoveredKey)
                    else {
                        throw AtlasVaultPairingTransactionError.stale
                    }
                    try await self.environment.deleteStoredKey(vaultID)
                    guard try await self.environment.loadStoredKey(vaultID)
                        == nil else {
                        throw AtlasVaultPairingTransactionError.unavailable
                    }
                }
            }
            try await self.clearTransaction(transaction)
            return self.fixed(.identityReady)
        }
    }

    public func stop() async {
        stopped = true
    }

    private func installInvitee(
        _ starting: AtlasVaultPairingTransaction
    ) async throws -> AtlasVaultTrustedPairingResult {
        if !Self.isAtLeast(starting, .storeCreated),
           let blocked = cleanInstallResult(await environment.cleanInstall()) {
            return blocked
        }
        guard let identity = try await environment.loadIdentity() else {
            throw AtlasVaultPairingTransactionError.unavailable
        }
        var context = try await inviteeContext(starting)
        defer { Self.wipe(&context.recoveredKey) }
        var transaction = starting
        let delivery = context.delivery.signedDelivery.delivery
        let vaultID = delivery.vaultID
        let store = AtlasVaultLocalStoreEnvelope(
            storeID: transaction.transactionID,
            createdAt: transaction.createdAt,
            updatedAt: transaction.createdAt,
            vaultMetadata: try context.delivery.bootstrap.vaultMetadata
                .localStoreMetadata(),
            records: context.delivery.bootstrap.records
        )
        guard try await environment.validateProjection(
            store,
            vaultID,
            context.recoveredKey,
            false
        ) else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        let storeHash = Self.sha256(try AtlasVaultLocalStoreIO.encode(store))
        let keyHash = Self.sha256(context.recoveredKey)

        if !Self.isAtLeast(transaction, .storeCreated) {
            try await environment.createStore(
                store,
                vaultID,
                context.recoveredKey
            )
            guard let readBack = try await environment.loadStore(
                vaultID,
                context.recoveredKey
            ), readBack == store,
            Self.sha256(try AtlasVaultLocalStoreIO.encode(readBack)) == storeHash
            else { throw AtlasVaultPairingTransactionError.unavailable }
            transaction = try await advance(
                transaction,
                to: .storeCreated,
                storeSHA256: storeHash
            )
        } else {
            guard transaction.storeSHA256 == storeHash,
                  let readBack = try await environment.loadStore(
                    vaultID,
                    context.recoveredKey
                  ),
                  readBack == store
            else { throw AtlasVaultPairingTransactionError.stale }
        }

        if !Self.isAtLeast(transaction, .keyCreated) {
            try await environment.createStoredKey(context.recoveredKey, vaultID)
            var readBack = try await environment.loadStoredKey(vaultID)
            defer { if readBack != nil { Self.wipe(&readBack!) } }
            guard let readBack,
                  Self.equal(readBack, context.recoveredKey),
                  Self.sha256(readBack) == keyHash
            else { throw AtlasVaultPairingTransactionError.unavailable }
            transaction = try await advance(
                transaction,
                to: .keyCreated,
                vaultKeySHA256: keyHash
            )
        } else {
            var readBack = try await environment.loadStoredKey(vaultID)
            defer { if readBack != nil { Self.wipe(&readBack!) } }
            guard transaction.vaultKeySHA256 == keyHash,
                  let readBack,
                  Self.equal(readBack, context.recoveredKey)
            else { throw AtlasVaultPairingTransactionError.stale }
        }

        if !Self.isAtLeast(transaction, .selectionCommitted) {
            guard try await environment.selectedVault() == nil else {
                throw AtlasVaultPairingTransactionError.collision
            }
            try await environment.createSelection(vaultID)
            guard try await environment.selectedVault() == vaultID else {
                throw AtlasVaultPairingTransactionError.unavailable
            }
            transaction = try await advance(
                transaction,
                to: .selectionCommitted,
                clearEphemeralKeyMaterial: true,
                selectionCommitted: true
            )
        } else if try await environment.selectedVault() != vaultID {
            throw AtlasVaultPairingTransactionError.stale
        }

        if !Self.isAtLeast(transaction, .runtimeActivated) {
            guard try await environment.activate(vaultID, context.recoveredKey),
                  try await environment.validateProjection(
                    store,
                    vaultID,
                    context.recoveredKey,
                    true
                  )
            else { throw AtlasVaultPairingTransactionError.unavailable }
            transaction = try await advance(
                transaction,
                to: .runtimeActivated,
                installedAt: environment.timestamp()
            )
        }

        guard let installedAt = transaction.installedAt else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        let acknowledgement = try AtlasVaultKeyDelivery.createAcknowledgement(
            invitee: identity,
            acknowledgementID: transaction.transactionID,
            delivery: context.delivery.signedDelivery,
            installedAt: installedAt
        )
        let acknowledgementArtifact = try AtlasVaultPairingArtifact
            .acknowledgement(acknowledgement)
        if !Self.isAtLeast(transaction, .trustCommitted) {
            let peer = try AtlasVaultTrustedDevicePeer(
                peerDeviceID: delivery.inviterDeviceID,
                peerDescriptor: context.delivery.signedDelivery.inviter,
                pairingTranscriptSHA256: delivery.transcriptSHA256,
                linkedAt: environment.timestamp(),
                role: "invitee",
                vaultID: vaultID,
                keyEpoch: delivery.keyEpoch,
                deliveryID: delivery.deliveryID,
                acknowledgementSHA256: acknowledgement.sha256Hex()
            )
            try await commitTrust(peer, localDeviceID: identity.deviceID)
            transaction = try await advance(
                transaction,
                to: .trustCommitted,
                acknowledgementSHA256: acknowledgementArtifact.sha256Hex()
            )
        } else if transaction.acknowledgementSHA256
            != (try acknowledgementArtifact.sha256Hex())
        {
            throw AtlasVaultPairingTransactionError.stale
        }
        if transaction.stage == .trustCommitted {
            try await stage(acknowledgementArtifact)
            transaction = try await advance(
                transaction,
                to: .acknowledgementCreated,
                stagedArtifacts: try mergedMetadata(
                    transaction: transaction,
                    artifact: acknowledgementArtifact
                )
            )
        } else {
            let restored = try await requireArtifact(
                .acknowledgement,
                transaction: transaction
            )
            guard Self.equal(
                try restored.canonicalData(),
                try acknowledgementArtifact.canonicalData()
            ) else { throw AtlasVaultPairingTransactionError.stale }
        }
        return result(
            .acknowledgementReady,
            transaction,
            identity: identity,
            peerDeviceID: delivery.inviterDeviceID,
            trusted: true
        )
    }

    private func completeInviterAcknowledgement(
        _ starting: AtlasVaultPairingTransaction,
        identity: AtlasVaultDeviceIdentity
    ) async throws -> AtlasVaultTrustedPairingResult {
        var transaction = starting
        let acknowledgementArtifact = try await requireArtifact(
            .acknowledgement,
            transaction: transaction
        )
        let deliveryArtifact = try await requireArtifact(
            .delivery,
            transaction: transaction
        )
        let acknowledgement = try acknowledgementArtifact
            .signedAcknowledgement()
        let delivery = try deliveryArtifact.deliveryPayload().signedDelivery
        _ = try AtlasVaultKeyDelivery.verifyAcknowledgement(
            acknowledgement,
            delivery: delivery,
            inviterDeviceID: identity.deviceID,
            inviteeDeviceID: acknowledgement.invitee.descriptor.deviceID
        )
        if transaction.stage == .acknowledgementImported {
            try await consumeReplay(
                AtlasVaultPairingReplayEntry(
                    kind: "acknowledgement",
                    objectID: acknowledgement.acknowledgement
                        .acknowledgementID,
                    transcriptSHA256: acknowledgement.acknowledgement
                        .transcriptSHA256,
                    consumedAt: environment.timestamp(),
                    expiresAt: delivery.delivery.expiresAt
                ),
                localDeviceID: identity.deviceID,
                permitExactReplay: true
            )
            transaction = try await advance(
                transaction,
                to: .acknowledgementConsumed
            )
        }
        if transaction.stage == .acknowledgementConsumed {
            let value = acknowledgement.acknowledgement
            let peer = try AtlasVaultTrustedDevicePeer(
                peerDeviceID: value.inviteeDeviceID,
                peerDescriptor: acknowledgement.invitee,
                pairingTranscriptSHA256: value.transcriptSHA256,
                linkedAt: transaction.createdAt,
                role: "inviter",
                vaultID: value.vaultID,
                keyEpoch: value.keyEpoch,
                deliveryID: value.deliveryID,
                acknowledgementSHA256: acknowledgement.sha256Hex()
            )
            try await commitTrust(peer, localDeviceID: identity.deviceID)
            transaction = try await advance(transaction, to: .trustCommitted)
        }
        guard transaction.stage == .trustCommitted else {
            throw AtlasVaultPairingTransactionError.stale
        }
        try await clearTransaction(transaction)
        return fixed(.completed, local: identity, trusted: true)
    }

    private struct InviteeContext {
        let delivery: AtlasVaultPairingDeliveryArtifactPayload
        var recoveredKey: Data
    }

    private func inviteeContext(
        _ transaction: AtlasVaultPairingTransaction
    ) async throws -> InviteeContext {
        guard let identity = try await environment.loadIdentity(),
              let transcriptText = transaction.transcriptSHA256,
              let transcript = Self.data(hex: transcriptText)
        else { throw AtlasVaultPairingTransactionError.invalidTransaction }
        let offer = try await requireArtifact(
            .offer,
            transaction: transaction
        ).signedOffer()
        let acceptance = try await requireArtifact(
            .acceptance,
            transaction: transaction
        ).acceptancePayload()
        let delivery = try await requireArtifact(
            .delivery,
            transaction: transaction
        ).deliveryPayload()
        guard
            delivery.signedDelivery.delivery.inviteeDeviceID
                == identity.deviceID,
            delivery.signedDelivery.delivery.inviterDeviceID
                == transaction.peerDeviceID,
            try AtlasVaultPairingFoundation.transcriptSHA256(
                offer: offer,
                acceptance: acceptance.signedAcceptance
            ) == transcript
        else { throw AtlasVaultPairingTransactionError.invalidTransaction }
        var session = try AtlasVaultPairingFoundation.deriveSessionKey(
            localIdentity: identity,
            offer: offer,
            acceptance: acceptance.signedAcceptance
        )
        defer { Self.wipe(&session) }
        let proofs = try AtlasVaultPairingFoundation.deriveProofs(
            sessionKey: session,
            transcriptSHA256: transcript
        )
        guard Self.equal(proofs.inviter, delivery.inviterProof) else {
            throw AtlasVaultPairingTransactionError.invalidTransaction
        }
        let recovered: Data
        if var transient = transaction.copyEphemeralKeyMaterial() {
            defer { Self.wipe(&transient) }
            recovered = try AtlasVaultKeyDelivery.openDelivery(
                delivery.signedDelivery,
                keyRequest: acceptance.signedKeyRequest,
                inviteeEphemeralPrivateKey: transient,
                bootstrap: delivery.bootstrap,
                transcriptSHA256: transcript,
                currentTime: environment.timestamp()
            )
        } else {
            guard Self.isAtLeast(transaction, .keyCreated),
                  let expectedHash = transaction.vaultKeySHA256,
                  let stored = try await environment.loadStoredKey(
                    delivery.signedDelivery.delivery.vaultID
                  ),
                  Self.sha256(stored) == expectedHash
            else { throw AtlasVaultPairingTransactionError.stale }
            recovered = stored
        }
        return InviteeContext(delivery: delivery, recoveredKey: recovered)
    }

    private func run(
        _ operation: () async throws -> AtlasVaultTrustedPairingResult
    ) async -> AtlasVaultTrustedPairingResult {
        guard !stopped, !operationInProgress else {
            return fixed(.unavailable)
        }
        operationInProgress = true
        defer { operationInProgress = false }
        do { return try await operation() }
        catch { return fixed(.recoveryRequired, pending: true) }
    }

    private func requireStage(
        _ role: AtlasVaultPairingRole,
        _ stage: AtlasVaultPairingStage
    ) async throws -> AtlasVaultPairingTransaction {
        guard let transaction = try await environment.loadTransaction(),
              transaction.role == role,
              transaction.stage == stage
        else { throw AtlasVaultPairingTransactionError.stale }
        return transaction
    }

    private func advance(
        _ transaction: AtlasVaultPairingTransaction,
        to stage: AtlasVaultPairingStage,
        installedAt: String? = nil,
        peerDeviceID: String? = nil,
        transcriptSHA256: String? = nil,
        acceptanceSHA256: String? = nil,
        deliverySHA256: String? = nil,
        acknowledgementSHA256: String? = nil,
        bootstrapSHA256: String? = nil,
        vaultID: String? = nil,
        keyEpoch: Int? = nil,
        clearEphemeralKeyMaterial: Bool = false,
        storeSHA256: String? = nil,
        vaultKeySHA256: String? = nil,
        selectionCommitted: Bool? = nil,
        stagedArtifacts: [AtlasVaultStagedPairingArtifact]? = nil
    ) async throws -> AtlasVaultPairingTransaction {
        let replacement = try transaction.advanced(
            to: stage,
            revision: environment.uuid(),
            updatedAt: environment.timestamp(),
            installedAt: installedAt,
            peerDeviceID: peerDeviceID,
            transcriptSHA256: transcriptSHA256,
            acceptanceSHA256: acceptanceSHA256,
            deliverySHA256: deliverySHA256,
            acknowledgementSHA256: acknowledgementSHA256,
            bootstrapSHA256: bootstrapSHA256,
            vaultID: vaultID,
            keyEpoch: keyEpoch,
            clearEphemeralKeyMaterial: clearEphemeralKeyMaterial,
            storeSHA256: storeSHA256,
            vaultKeySHA256: vaultKeySHA256,
            selectionCommitted: selectionCommitted,
            stagedArtifacts: stagedArtifacts
        )
        try await environment.replaceTransaction(
            replacement,
            transaction.sha256Hex()
        )
        try await requireTransaction(replacement)
        return replacement
    }

    private func requireTransaction(
        _ expected: AtlasVaultPairingTransaction
    ) async throws {
        guard let current = try await environment.loadTransaction(),
              Self.equal(
                try current.canonicalData(),
                try expected.canonicalData()
              )
        else { throw AtlasVaultPairingTransactionError.stale }
    }

    private func stage(_ artifact: AtlasVaultPairingArtifact) async throws {
        if let existing = try await environment.loadArtifact(artifact.kind) {
            guard Self.equal(
                try existing.canonicalData(),
                try artifact.canonicalData()
            ) else { throw AtlasVaultPairingTransactionError.collision }
        } else {
            try await environment.createArtifact(artifact)
        }
        guard let readBack = try await environment.loadArtifact(artifact.kind),
              Self.equal(
                try readBack.canonicalData(),
                try artifact.canonicalData()
              )
        else { throw AtlasVaultPairingTransactionError.unavailable }
    }

    private func requireArtifact(
        _ kind: AtlasVaultPairingArtifactKind,
        transaction: AtlasVaultPairingTransaction
    ) async throws -> AtlasVaultPairingArtifact {
        guard let metadata = transaction.stagedArtifacts.first(where: {
            $0.kind == kind
        }),
        let artifact = try await environment.loadArtifact(kind),
        try artifact.canonicalData().count == metadata.byteCount,
        try artifact.sha256Hex() == metadata.sha256
        else { throw AtlasVaultPairingTransactionError.stale }
        return artifact
    }

    private func metadata(
        for artifacts: [AtlasVaultPairingArtifact]
    ) throws -> [AtlasVaultStagedPairingArtifact] {
        try artifacts.sorted { $0.kind.sortIndex < $1.kind.sortIndex }.map {
            let data = try $0.canonicalData()
            return try AtlasVaultStagedPairingArtifact(
                kind: $0.kind,
                sha256: $0.sha256Hex(),
                byteCount: data.count
            )
        }
    }

    private func mergedMetadata(
        transaction: AtlasVaultPairingTransaction,
        artifact: AtlasVaultPairingArtifact
    ) throws -> [AtlasVaultStagedPairingArtifact] {
        var values = transaction.stagedArtifacts.filter {
            $0.kind != artifact.kind
        }
        values.append(contentsOf: try metadata(for: [artifact]))
        return values.sorted { $0.kind.sortIndex < $1.kind.sortIndex }
    }

    private func clearTransaction(
        _ transaction: AtlasVaultPairingTransaction
    ) async throws {
        for metadata in transaction.stagedArtifacts.reversed() {
            try await environment.deleteArtifact(
                metadata.kind,
                metadata.sha256
            )
            guard try await environment.loadArtifact(metadata.kind) == nil else {
                throw AtlasVaultPairingTransactionError.unavailable
            }
        }
        try await environment.deleteTransaction(transaction.sha256Hex())
        guard try await environment.loadTransaction() == nil else {
            throw AtlasVaultPairingTransactionError.unavailable
        }
    }

    private func consumeReplay(
        _ entry: AtlasVaultPairingReplayEntry,
        localDeviceID: String,
        permitExactReplay: Bool
    ) async throws {
        let now = environment.timestamp()
        if let current = try await environment.loadReplay() {
            let result = try AtlasVaultPairingReplayFoundation.consume(
                entry,
                in: current,
                revision: environment.uuid(),
                updatedAt: now,
                currentTime: now
            )
            guard result.outcome == .consumed || permitExactReplay else {
                throw AtlasVaultPairingTransactionError.collision
            }
            if result.outcome == .consumed {
                try await environment.replaceReplay(
                    result.store,
                    try Self.sha256(current.canonicalData())
                )
            }
            return
        }
        let empty = try AtlasVaultPairingReplayStore(
            localDeviceID: localDeviceID,
            revision: environment.uuid(),
            parentRevision: nil,
            createdAt: now,
            updatedAt: now,
            entries: []
        )
        let result = try AtlasVaultPairingReplayFoundation.consume(
            entry,
            in: empty,
            revision: environment.uuid(),
            updatedAt: now,
            currentTime: now
        )
        try await environment.createReplay(result.store)
    }

    private func commitTrust(
        _ peer: AtlasVaultTrustedDevicePeer,
        localDeviceID: String
    ) async throws {
        let now = environment.timestamp()
        if let current = try await environment.loadRegistry() {
            let result = try AtlasVaultTrustedDeviceRegistryFoundation.commit(
                peer,
                to: current,
                revision: environment.uuid(),
                updatedAt: now
            )
            if result.outcome == .committed {
                try await environment.replaceRegistry(
                    result.registry,
                    try Self.sha256(current.canonicalData())
                )
            }
            return
        }
        let empty = try AtlasVaultTrustedDeviceRegistry(
            localDeviceID: localDeviceID,
            revision: environment.uuid(),
            parentRevision: nil,
            createdAt: now,
            updatedAt: now,
            devices: []
        )
        let result = try AtlasVaultTrustedDeviceRegistryFoundation.commit(
            peer,
            to: empty,
            revision: environment.uuid(),
            updatedAt: now
        )
        try await environment.createRegistry(result.registry)
    }

    private func sessionKey(
        transaction: AtlasVaultPairingTransaction,
        identity: AtlasVaultDeviceIdentity
    ) async throws -> Data {
        let offer = try await requireArtifact(
            .offer,
            transaction: transaction
        ).signedOffer()
        let acceptance = try await requireArtifact(
            .acceptance,
            transaction: transaction
        ).acceptancePayload().signedAcceptance
        return try AtlasVaultPairingFoundation.deriveSessionKey(
            localIdentity: identity,
            offer: offer,
            acceptance: acceptance
        )
    }

    private func resultWithSAS(
        _ disposition: AtlasVaultTrustedPairingDisposition,
        transaction: AtlasVaultPairingTransaction,
        identity: AtlasVaultDeviceIdentity
    ) async throws -> AtlasVaultTrustedPairingResult {
        guard let transcriptText = transaction.transcriptSHA256,
              let transcript = Self.data(hex: transcriptText)
        else { throw AtlasVaultPairingTransactionError.invalidTransaction }
        var session = try await sessionKey(
            transaction: transaction,
            identity: identity
        )
        defer { Self.wipe(&session) }
        let offer = try await requireArtifact(
            .offer,
            transaction: transaction
        ).signedOffer()
        return result(
            disposition,
            transaction,
            identity: identity,
            peerDeviceID: transaction.peerDeviceID,
            sas: try AtlasVaultKeyDelivery.deriveSAS(
                pairingSessionKey: session,
                transcriptSHA256: transcript
            ),
            expiresAt: offer.offer.expiresAt
        )
    }

    private func result(
        for transaction: AtlasVaultPairingTransaction,
        identity: AtlasVaultDeviceIdentity?
    ) async throws -> AtlasVaultTrustedPairingResult {
        let disposition: AtlasVaultTrustedPairingDisposition
        switch transaction.stage {
        case .offerCreated: disposition = .offerReady
        case .offerSaved: disposition = .offerSaved
        case .offerImported, .acceptanceCreated: disposition = .acceptanceReady
        case .acceptanceSaved: disposition = .codesReady
        case .acceptanceImported: disposition = .codesReady
        case .sasConfirmed, .offerConsumed: disposition = .codesConfirmed
        case .deliveryCreated: disposition = .deliveryReady
        case .deliverySaved: disposition = .deliverySaved
        case .deliveryImported, .storeCreated, .keyCreated,
             .selectionCommitted, .runtimeActivated,
             .acknowledgementImported, .acknowledgementConsumed:
            disposition = .recoveryRequired
        case .trustCommitted, .acknowledgementCreated:
            disposition = .acknowledgementReady
        case .acknowledgementSaved:
            disposition = .acknowledgementSaved
        }
        if let identity,
           transaction.transcriptSHA256 != nil,
           transaction.acceptanceSHA256 != nil,
           [.acceptanceSaved, .acceptanceImported, .sasConfirmed,
            .offerConsumed, .deliveryCreated, .deliverySaved]
            .contains(transaction.stage) {
            return try await resultWithSAS(
                disposition,
                transaction: transaction,
                identity: identity
            )
        }
        return result(
            disposition,
            transaction,
            identity: identity,
            peerDeviceID: transaction.peerDeviceID,
            trusted: Self.isAtLeast(transaction, .trustCommitted)
        )
    }

    private func result(
        _ disposition: AtlasVaultTrustedPairingDisposition,
        _ transaction: AtlasVaultPairingTransaction,
        identity: AtlasVaultDeviceIdentity?,
        peerDeviceID: String? = nil,
        sas: String? = nil,
        expiresAt: String? = nil,
        trusted: Bool = false
    ) -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(
            disposition: disposition,
            role: transaction.role,
            stage: transaction.stage,
            localFingerprint: identity.flatMap {
                AtlasVaultPairingFoundation.deviceFingerprint($0.deviceID)
            },
            peerFingerprint: peerDeviceID.flatMap(
                AtlasVaultPairingFoundation.deviceFingerprint
            ),
            sas: sas,
            expiresAt: expiresAt,
            trusted: trusted,
            pendingTransaction: true
        )
    }

    private func fixed(
        _ disposition: AtlasVaultTrustedPairingDisposition,
        local: AtlasVaultDeviceIdentity? = nil,
        trusted: Bool = false,
        pending: Bool = false
    ) -> AtlasVaultTrustedPairingResult {
        AtlasVaultTrustedPairingResult(
            disposition: disposition,
            localFingerprint: local.flatMap {
                AtlasVaultPairingFoundation.deviceFingerprint($0.deviceID)
            },
            trusted: trusted,
            pendingTransaction: pending
        )
    }

    private func cleanInstallResult(
        _ disposition: AtlasVaultPairingCleanInstallDisposition
    ) -> AtlasVaultTrustedPairingResult? {
        switch disposition {
        case .clean: nil
        case .migrationRequired: fixed(.migrationRequired)
        case .existingVault: fixed(.existingVault)
        case .unavailable: fixed(.unavailable)
        case .recoveryRequired: fixed(.recoveryRequired)
        }
    }

    public static func currentTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return formatter.string(from: Date())
    }

    private static func timestamp(
        adding interval: TimeInterval,
        to value: String
    ) throws -> String {
        let date = try AtlasVaultPairingValidation.date(value)
            .addingTimeInterval(interval)
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return formatter.string(from: date)
    }

    private static func isAtLeast(
        _ transaction: AtlasVaultPairingTransaction,
        _ stage: AtlasVaultPairingStage
    ) -> Bool {
        let stages: [AtlasVaultPairingStage] = transaction.role == .inviter
            ? [
                .offerCreated, .offerSaved, .acceptanceImported,
                .sasConfirmed, .deliveryCreated, .deliverySaved,
                .acknowledgementImported, .acknowledgementConsumed,
                .trustCommitted,
            ]
            : [
                .offerImported, .acceptanceCreated, .acceptanceSaved,
                .sasConfirmed, .offerConsumed, .deliveryImported,
                .storeCreated, .keyCreated, .selectionCommitted,
                .runtimeActivated, .trustCommitted,
                .acknowledgementCreated, .acknowledgementSaved,
            ]
        guard let current = stages.firstIndex(of: transaction.stage),
              let expected = stages.firstIndex(of: stage) else { return false }
        return current >= expected
    }

    private static func sha256(_ data: Data) -> String {
        hex(Data(SHA256.hash(data: data)))
    }

    private static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    private static func data(hex: String) -> Data? {
        guard hex.count == 64,
              hex.range(
                of: #"^[0-9a-f]{64}$"#,
                options: .regularExpression
              ) != nil else { return nil }
        var result = Data()
        result.reserveCapacity(32)
        var index = hex.startIndex
        while index < hex.endIndex {
            let next = hex.index(index, offsetBy: 2)
            guard let byte = UInt8(hex[index..<next], radix: 16) else {
                return nil
            }
            result.append(byte)
            index = next
        }
        return result
    }

    private static func equal(_ left: Data, _ right: Data) -> Bool {
        AtlasVaultDeviceIdentityValidation.constantTimeEqual(left, right)
    }

    private static func wipe(_ data: inout Data) {
        data.resetBytes(in: 0..<data.count)
        data.removeAll(keepingCapacity: false)
    }
}

private extension AtlasVaultPairingArtifactKind {
    static var allCases: [Self] {
        [.offer, .acceptance, .delivery, .acknowledgement]
    }

    var sortIndex: Int {
        Self.allCases.firstIndex(of: self) ?? Int.max
    }
}
