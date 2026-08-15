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
            guard
                format == "atlasvault-pairing-transaction",
                version == 1,
                try AtlasVaultPairingValidation.date(updatedAt)
                    >= AtlasVaultPairingValidation.date(createdAt),
                Self.stages(for: role).contains(stage),
                stagedArtifacts.count <= AtlasVaultPairingArtifactKind.allCases.count,
                Set(stagedArtifacts.map(\.kind)).count == stagedArtifacts.count,
                stagedArtifacts.map(\.kind.sortIndex)
                    == stagedArtifacts.map(\.kind.sortIndex).sorted(),
                !selectionCommitted || role == .invitee
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

private struct AtlasKeychainPairingStateStore<Client: AtlasKeychainClient> {
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

public struct AtlasKeychainTrustedDeviceRegistryStore<Client: AtlasKeychainClient> {
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

public struct AtlasKeychainPairingReplayStore<Client: AtlasKeychainClient> {
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

public struct AtlasKeychainPairingTransactionStore<Client: AtlasKeychainClient> {
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

private extension AtlasVaultPairingArtifactKind {
    static var allCases: [Self] {
        [.offer, .acceptance, .delivery, .acknowledgement]
    }

    var sortIndex: Int {
        Self.allCases.firstIndex(of: self) ?? Int.max
    }
}
