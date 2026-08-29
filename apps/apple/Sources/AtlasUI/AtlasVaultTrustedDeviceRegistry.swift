import Foundation

public enum AtlasVaultTrustedDeviceStateError: Error, Equatable, Sendable, CustomStringConvertible {
    case invalidState

    public var description: String {
        "AtlasVault trusted-device state is invalid."
    }
}

private enum AtlasVaultTrustedDeviceValidation {
    static let registryFormat = "atlasvault-trusted-device-registry"
    static let replayFormat = "atlasvault-pairing-replay"
    static let version = 1
    static let maximumPeers = 64
    static let maximumReplayEntries = 2_048

    static func deviceID(_ value: String) throws -> String {
        guard value.range(
            of: #"^avd1-[0-9a-f]{64}$"#,
            options: .regularExpression
        ) != nil else {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
        return value
    }

    static func sha256(_ value: String) throws -> String {
        do {
            return try AtlasVaultPairingValidation.lowercaseHex(value)
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

    static func uuid(_ value: String) throws -> String {
        do {
            return try AtlasVaultPairingValidation.canonicalUUID(value)
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

    static func timestamp(_ value: String) throws -> String {
        do {
            return try AtlasVaultPairingValidation.timestamp(value)
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

    static func date(_ value: String) throws -> Date {
        do {
            return try AtlasVaultPairingValidation.date(value)
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

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
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

    static func canonicalData<T: Encodable>(_ value: T) throws -> Data {
        do {
            return try AtlasVaultPairingValidation.canonicalData(value)
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }
}

public struct AtlasVaultTrustedDevicePeer: Codable, Equatable, Sendable {
    public let peerDeviceID: String
    public let peerDescriptor: AtlasVaultSignedDeviceDescriptor
    public let pairingTranscriptSHA256: String
    public let linkedAt: String
    public let role: String
    public let vaultID: String
    public let keyEpoch: Int
    public let deliveryID: String
    public let acknowledgementSHA256: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case peerDeviceID = "peer_device_id"
        case peerDescriptor = "peer_descriptor"
        case pairingTranscriptSHA256 = "pairing_transcript_sha256"
        case linkedAt = "linked_at"
        case role
        case vaultID = "vault_id"
        case keyEpoch = "key_epoch"
        case deliveryID = "delivery_id"
        case acknowledgementSHA256 = "acknowledgement_sha256"
    }

    public init(
        peerDeviceID: String,
        peerDescriptor: AtlasVaultSignedDeviceDescriptor,
        pairingTranscriptSHA256: String,
        linkedAt: String,
        role: String,
        vaultID: String,
        keyEpoch: Int,
        deliveryID: String,
        acknowledgementSHA256: String
    ) throws {
        guard
            peerDescriptor.descriptor.deviceID == peerDeviceID,
            role == "inviter" || role == "invitee",
            keyEpoch > 0,
            keyEpoch <= AtlasVaultDeviceIdentityValidation.maximumKeyEpoch,
            (try? AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID)) == vaultID
        else {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
        self.peerDeviceID = try AtlasVaultTrustedDeviceValidation.deviceID(peerDeviceID)
        self.peerDescriptor = peerDescriptor
        self.pairingTranscriptSHA256 = try AtlasVaultTrustedDeviceValidation.sha256(
            pairingTranscriptSHA256
        )
        self.linkedAt = try AtlasVaultTrustedDeviceValidation.timestamp(linkedAt)
        self.role = role
        self.vaultID = vaultID
        self.keyEpoch = keyEpoch
        self.deliveryID = try AtlasVaultTrustedDeviceValidation.uuid(deliveryID)
        self.acknowledgementSHA256 = try AtlasVaultTrustedDeviceValidation.sha256(
            acknowledgementSHA256
        )
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultTrustedDeviceValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                peerDeviceID: container.decode(String.self, forKey: .peerDeviceID),
                peerDescriptor: container.decode(
                    AtlasVaultSignedDeviceDescriptor.self,
                    forKey: .peerDescriptor
                ),
                pairingTranscriptSHA256: container.decode(
                    String.self,
                    forKey: .pairingTranscriptSHA256
                ),
                linkedAt: container.decode(String.self, forKey: .linkedAt),
                role: container.decode(String.self, forKey: .role),
                vaultID: container.decode(String.self, forKey: .vaultID),
                keyEpoch: container.decode(Int.self, forKey: .keyEpoch),
                deliveryID: container.decode(String.self, forKey: .deliveryID),
                acknowledgementSHA256: container.decode(
                    String.self,
                    forKey: .acknowledgementSHA256
                )
            )
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

}

public struct AtlasVaultTrustedDeviceRegistry: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let localDeviceID: String
    public let revision: String
    public let parentRevision: String?
    public let createdAt: String
    public let updatedAt: String
    public let devices: [AtlasVaultTrustedDevicePeer]

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case localDeviceID = "local_device_id"
        case revision
        case parentRevision = "parent_revision"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case devices
    }

    public init(
        format: String = "atlasvault-trusted-device-registry",
        version: Int = 1,
        localDeviceID: String,
        revision: String,
        parentRevision: String?,
        createdAt: String,
        updatedAt: String,
        devices: [AtlasVaultTrustedDevicePeer]
    ) throws {
        let ids = devices.map(\.peerDeviceID)
        guard
            format == AtlasVaultTrustedDeviceValidation.registryFormat,
            version == AtlasVaultTrustedDeviceValidation.version,
            devices.count <= AtlasVaultTrustedDeviceValidation.maximumPeers,
            Set(ids).count == ids.count,
            !ids.contains(localDeviceID),
            ids == ids.sorted(),
            try AtlasVaultTrustedDeviceValidation.date(updatedAt)
                >= AtlasVaultTrustedDeviceValidation.date(createdAt)
        else {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
        self.format = format
        self.version = version
        self.localDeviceID = try AtlasVaultTrustedDeviceValidation.deviceID(localDeviceID)
        self.revision = try AtlasVaultTrustedDeviceValidation.uuid(revision)
        self.parentRevision = try parentRevision.map(AtlasVaultTrustedDeviceValidation.uuid)
        self.createdAt = try AtlasVaultTrustedDeviceValidation.timestamp(createdAt)
        self.updatedAt = try AtlasVaultTrustedDeviceValidation.timestamp(updatedAt)
        self.devices = devices
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultTrustedDeviceValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                localDeviceID: container.decode(String.self, forKey: .localDeviceID),
                revision: container.decode(String.self, forKey: .revision),
                parentRevision: container.decodeIfPresent(
                    String.self,
                    forKey: .parentRevision
                ),
                createdAt: container.decode(String.self, forKey: .createdAt),
                updatedAt: container.decode(String.self, forKey: .updatedAt),
                devices: container.decode(
                    [AtlasVaultTrustedDevicePeer].self,
                    forKey: .devices
                )
            )
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(localDeviceID, forKey: .localDeviceID)
        try container.encode(revision, forKey: .revision)
        if let parentRevision {
            try container.encode(parentRevision, forKey: .parentRevision)
        } else {
            try container.encodeNil(forKey: .parentRevision)
        }
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(updatedAt, forKey: .updatedAt)
        try container.encode(devices, forKey: .devices)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            try AtlasVaultProtectedStateBounds.requireByteCount(
                data.count,
                for: .trustedDeviceRegistry
            )
            let value = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try value.canonicalData(),
                data
            ) else {
                throw AtlasVaultTrustedDeviceStateError.invalidState
            }
            return value
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultTrustedDeviceValidation.canonicalData(self)
    }
}

public enum AtlasVaultTrustedDeviceCommitOutcome: Equatable, Sendable {
    case committed
    case alreadyTrusted
}

public struct AtlasVaultTrustedDeviceCommitResult: Equatable, Sendable {
    public let registry: AtlasVaultTrustedDeviceRegistry
    public let outcome: AtlasVaultTrustedDeviceCommitOutcome
}

public enum AtlasVaultTrustedDeviceRegistryFoundation {
    public static func commit(
        _ peer: AtlasVaultTrustedDevicePeer,
        to registry: AtlasVaultTrustedDeviceRegistry,
        revision: String,
        updatedAt: String
    ) throws -> AtlasVaultTrustedDeviceCommitResult {
        do {
            let descriptor = try peer.peerDescriptor.verifiedDescriptor()
            guard descriptor.deviceID == peer.peerDeviceID else {
                throw AtlasVaultTrustedDeviceStateError.invalidState
            }
            if let current = registry.devices.first(where: {
                $0.peerDeviceID == peer.peerDeviceID
            }) {
                guard current == peer else {
                    throw AtlasVaultTrustedDeviceStateError.invalidState
                }
                return AtlasVaultTrustedDeviceCommitResult(
                    registry: registry,
                    outcome: .alreadyTrusted
                )
            }
            guard
                registry.devices.count < AtlasVaultTrustedDeviceValidation.maximumPeers,
                revision != registry.revision,
                revision != registry.parentRevision,
                try AtlasVaultTrustedDeviceValidation.date(updatedAt)
                    >= AtlasVaultTrustedDeviceValidation.date(registry.updatedAt)
            else {
                throw AtlasVaultTrustedDeviceStateError.invalidState
            }
            let next = try AtlasVaultTrustedDeviceRegistry(
                localDeviceID: registry.localDeviceID,
                revision: revision,
                parentRevision: registry.revision,
                createdAt: registry.createdAt,
                updatedAt: updatedAt,
                devices: (registry.devices + [peer]).sorted {
                    $0.peerDeviceID < $1.peerDeviceID
                }
            )
            return AtlasVaultTrustedDeviceCommitResult(
                registry: next,
                outcome: .committed
            )
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }
}

public struct AtlasVaultPairingReplayEntry: Codable, Equatable, Sendable {
    public let kind: String
    public let objectID: String
    public let transcriptSHA256: String
    public let consumedAt: String
    public let expiresAt: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case kind
        case objectID = "object_id"
        case transcriptSHA256 = "transcript_sha256"
        case consumedAt = "consumed_at"
        case expiresAt = "expires_at"
    }

    public init(
        kind: String,
        objectID: String,
        transcriptSHA256: String,
        consumedAt: String,
        expiresAt: String
    ) throws {
        guard
            kind == "offer" || kind == "acknowledgement",
            try AtlasVaultTrustedDeviceValidation.date(expiresAt)
                > AtlasVaultTrustedDeviceValidation.date(consumedAt)
        else {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
        self.kind = kind
        self.objectID = try AtlasVaultTrustedDeviceValidation.uuid(objectID)
        self.transcriptSHA256 = try AtlasVaultTrustedDeviceValidation.sha256(
            transcriptSHA256
        )
        self.consumedAt = try AtlasVaultTrustedDeviceValidation.timestamp(consumedAt)
        self.expiresAt = try AtlasVaultTrustedDeviceValidation.timestamp(expiresAt)
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultTrustedDeviceValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                kind: container.decode(String.self, forKey: .kind),
                objectID: container.decode(String.self, forKey: .objectID),
                transcriptSHA256: container.decode(
                    String.self,
                    forKey: .transcriptSHA256
                ),
                consumedAt: container.decode(String.self, forKey: .consumedAt),
                expiresAt: container.decode(String.self, forKey: .expiresAt)
            )
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }
}

public struct AtlasVaultPairingReplayStore: Codable, Equatable, Sendable {
    public let format: String
    public let version: Int
    public let localDeviceID: String
    public let revision: String
    public let parentRevision: String?
    public let createdAt: String
    public let updatedAt: String
    public let entries: [AtlasVaultPairingReplayEntry]

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case localDeviceID = "local_device_id"
        case revision
        case parentRevision = "parent_revision"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case entries
    }

    public init(
        format: String = "atlasvault-pairing-replay",
        version: Int = 1,
        localDeviceID: String,
        revision: String,
        parentRevision: String?,
        createdAt: String,
        updatedAt: String,
        entries: [AtlasVaultPairingReplayEntry]
    ) throws {
        let keys = entries.map { "\($0.kind):\($0.objectID)" }
        let sorted = entries.sorted(by: Self.lessThan)
        guard
            format == AtlasVaultTrustedDeviceValidation.replayFormat,
            version == AtlasVaultTrustedDeviceValidation.version,
            entries.count <= AtlasVaultTrustedDeviceValidation.maximumReplayEntries,
            Set(keys).count == keys.count,
            entries == sorted,
            try AtlasVaultTrustedDeviceValidation.date(updatedAt)
                >= AtlasVaultTrustedDeviceValidation.date(createdAt)
        else {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
        self.format = format
        self.version = version
        self.localDeviceID = try AtlasVaultTrustedDeviceValidation.deviceID(localDeviceID)
        self.revision = try AtlasVaultTrustedDeviceValidation.uuid(revision)
        self.parentRevision = try parentRevision.map(AtlasVaultTrustedDeviceValidation.uuid)
        self.createdAt = try AtlasVaultTrustedDeviceValidation.timestamp(createdAt)
        self.updatedAt = try AtlasVaultTrustedDeviceValidation.timestamp(updatedAt)
        self.entries = entries
    }

    public init(from decoder: Decoder) throws {
        do {
            try AtlasVaultTrustedDeviceValidation.exactKeys(
                decoder,
                expected: Set(CodingKeys.allCases.map(\.rawValue)),
                keyedBy: CodingKeys.self
            )
            let container = try decoder.container(keyedBy: CodingKeys.self)
            try self.init(
                format: container.decode(String.self, forKey: .format),
                version: container.decode(Int.self, forKey: .version),
                localDeviceID: container.decode(String.self, forKey: .localDeviceID),
                revision: container.decode(String.self, forKey: .revision),
                parentRevision: container.decodeIfPresent(
                    String.self,
                    forKey: .parentRevision
                ),
                createdAt: container.decode(String.self, forKey: .createdAt),
                updatedAt: container.decode(String.self, forKey: .updatedAt),
                entries: container.decode(
                    [AtlasVaultPairingReplayEntry].self,
                    forKey: .entries
                )
            )
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(format, forKey: .format)
        try container.encode(version, forKey: .version)
        try container.encode(localDeviceID, forKey: .localDeviceID)
        try container.encode(revision, forKey: .revision)
        if let parentRevision {
            try container.encode(parentRevision, forKey: .parentRevision)
        } else {
            try container.encodeNil(forKey: .parentRevision)
        }
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(updatedAt, forKey: .updatedAt)
        try container.encode(entries, forKey: .entries)
    }

    public static func decodeStrict(_ data: Data) throws -> Self {
        do {
            try AtlasVaultProtectedStateBounds.requireByteCount(
                data.count,
                for: .pairingReplayState
            )
            let value = try JSONDecoder().decode(Self.self, from: data)
            guard AtlasVaultDeviceIdentityValidation.constantTimeEqual(
                try value.canonicalData(),
                data
            ) else {
                throw AtlasVaultTrustedDeviceStateError.invalidState
            }
            return value
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }

    public func canonicalData() throws -> Data {
        try AtlasVaultTrustedDeviceValidation.canonicalData(self)
    }

    fileprivate static func lessThan(
        _ left: AtlasVaultPairingReplayEntry,
        _ right: AtlasVaultPairingReplayEntry
    ) -> Bool {
        if left.expiresAt != right.expiresAt { return left.expiresAt < right.expiresAt }
        if left.kind != right.kind { return left.kind < right.kind }
        return left.objectID < right.objectID
    }
}

public enum AtlasVaultReplayConsumeOutcome: Equatable, Sendable {
    case consumed
    case alreadyConsumed
}

public struct AtlasVaultReplayConsumeResult: Equatable, Sendable {
    public let store: AtlasVaultPairingReplayStore
    public let outcome: AtlasVaultReplayConsumeOutcome
}

public enum AtlasVaultPairingReplayFoundation {
    public static func consume(
        _ entry: AtlasVaultPairingReplayEntry,
        in store: AtlasVaultPairingReplayStore,
        revision: String,
        updatedAt: String,
        currentTime: String
    ) throws -> AtlasVaultReplayConsumeResult {
        do {
            if let current = store.entries.first(where: {
                $0.kind == entry.kind && $0.objectID == entry.objectID
            }) {
                guard current.transcriptSHA256 == entry.transcriptSHA256 else {
                    throw AtlasVaultTrustedDeviceStateError.invalidState
                }
                return AtlasVaultReplayConsumeResult(
                    store: store,
                    outcome: .alreadyConsumed
                )
            }
            let now = try AtlasVaultTrustedDeviceValidation.date(currentTime)
            guard try AtlasVaultTrustedDeviceValidation.date(entry.expiresAt)
                > now else {
                throw AtlasVaultTrustedDeviceStateError.invalidState
            }
            var entries = store.entries.filter {
                guard let expiry = try? AtlasVaultTrustedDeviceValidation.date($0.expiresAt)
                else { return false }
                return expiry > now
            }
            guard entries.count < AtlasVaultTrustedDeviceValidation.maximumReplayEntries
            else {
                throw AtlasVaultTrustedDeviceStateError.invalidState
            }
            entries.append(entry)
            entries.sort(by: AtlasVaultPairingReplayStore.lessThan)
            let next = try AtlasVaultPairingReplayStore(
                localDeviceID: store.localDeviceID,
                revision: revision,
                parentRevision: store.revision,
                createdAt: store.createdAt,
                updatedAt: updatedAt,
                entries: entries
            )
            return AtlasVaultReplayConsumeResult(store: next, outcome: .consumed)
        } catch {
            throw AtlasVaultTrustedDeviceStateError.invalidState
        }
    }
}
