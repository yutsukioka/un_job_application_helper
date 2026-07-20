import Foundation
import Security

public protocol AtlasVaultSelectionRegistering: Sendable {
    func storeSelection(
        _ selection: AtlasSelectedVaultID
    ) async throws(AtlasVaultIDSelectionError)
    func clearSelection() async throws(AtlasVaultIDSelectionError)
}

protocol AtlasVaultSelectionEnvelopeEncoding: Sendable {
    func encode(vaultID: String) throws -> Data
}

public struct AtlasKeychainVaultSelectionRegistry<
    Client: AtlasKeychainClient
>:
    AtlasVaultIDSelecting,
    AtlasVaultSelectionRegistering,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    static var registryService: String {
        "com.atlasvault.selected-vault"
    }

    static var registryAccount: String {
        "current-selection"
    }

    private let client: Client
    private let envelopeEncoder: any AtlasVaultSelectionEnvelopeEncoding

    public init(client: Client) {
        self.client = client
        envelopeEncoder = AtlasJSONVaultSelectionEnvelopeEncoder()
    }

    init(
        client: Client,
        envelopeEncoder: any AtlasVaultSelectionEnvelopeEncoding
    ) {
        self.client = client
        self.envelopeEncoder = envelopeEncoder
    }

    public var description: String {
        "AtlasKeychainVaultSelectionRegistry(<redacted>)"
    }

    public var debugDescription: String {
        description
    }

    public func selectVaultID() async throws(AtlasVaultIDSelectionError)
        -> AtlasVaultIDSelection
    {
        let result = client.copyMatching(Self.query)
        switch result.status {
        case errSecItemNotFound:
            return .none
        case errSecSuccess:
            guard let data = result.valueData else {
                throw .invalidRegistry
            }
            guard Self.hasExpectedEnvelopeKeys(data) else {
                throw .invalidRegistry
            }
            let envelope: RegistryEnvelope
            do {
                envelope = try JSONDecoder().decode(
                    RegistryEnvelope.self,
                    from: data
                )
            } catch {
                throw .invalidRegistry
            }
            guard
                envelope.format == RegistryEnvelope.expectedFormat,
                envelope.version == RegistryEnvelope.currentVersion
            else {
                throw .invalidRegistry
            }
            do {
                return .selected(
                    try AtlasSelectedVaultID(validating: envelope.vaultID)
                )
            } catch {
                throw .invalidRegistry
            }
        default:
            throw .unavailable
        }
    }

    public func storeSelection(
        _ selection: AtlasSelectedVaultID
    ) async throws(AtlasVaultIDSelectionError) {
        let data: Data
        do {
            data = try envelopeEncoder.encode(vaultID: selection.vaultID)
        } catch {
            throw .unavailable
        }

        let item = AtlasKeychainItem(
            service: Self.registryService,
            account: Self.registryAccount,
            valueData: data,
            accessibility: .afterFirstUnlockThisDeviceOnly
        )
        switch client.add(item) {
        case errSecSuccess:
            return
        case errSecDuplicateItem:
            guard client.update(
                Self.query,
                with: AtlasKeychainUpdate(valueData: data)
            ) == errSecSuccess else {
                throw .unavailable
            }
        default:
            throw .unavailable
        }
    }

    public func clearSelection() async throws(AtlasVaultIDSelectionError) {
        switch client.delete(Self.query) {
        case errSecSuccess, errSecItemNotFound:
            return
        default:
            throw .unavailable
        }
    }

    private static var query: AtlasKeychainQuery {
        AtlasKeychainQuery(
            service: registryService,
            account: registryAccount
        )
    }

    private static func hasExpectedEnvelopeKeys(_ data: Data) -> Bool {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dictionary = object as? [String: Any]
        else {
            return false
        }
        return Set(dictionary.keys) == ["format", "version", "vault_id"]
    }
}

public extension AtlasKeychainVaultSelectionRegistry
where Client == SecItemAtlasKeychainClient {
    init() {
        self.init(client: SecItemAtlasKeychainClient())
    }
}

private struct AtlasJSONVaultSelectionEnvelopeEncoder:
    AtlasVaultSelectionEnvelopeEncoding
{
    func encode(vaultID: String) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(
            RegistryEnvelope(vaultID: vaultID)
        )
    }
}

private struct RegistryEnvelope: Codable, Sendable {
    static let expectedFormat = "atlas-vault-selection"
    static let currentVersion = 1

    let format: String
    let version: Int
    let vaultID: String

    init(vaultID: String) {
        format = Self.expectedFormat
        version = Self.currentVersion
        self.vaultID = vaultID
    }

    enum CodingKeys: String, CodingKey {
        case format
        case version
        case vaultID = "vault_id"
    }
}
