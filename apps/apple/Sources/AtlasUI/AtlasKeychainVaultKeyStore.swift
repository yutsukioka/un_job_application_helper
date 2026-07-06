import Foundation
import Security

public enum AtlasKeychainVaultKeyStoreError: Error, Equatable, Sendable {
    case invalidVaultID
    case invalidVaultKeyLength
    case unexpectedItemData
    case keychainError(OSStatus)
}

public enum AtlasKeychainAccessibility: Equatable, Sendable {
    case afterFirstUnlockThisDeviceOnly

    var secAttribute: CFString {
        switch self {
        case .afterFirstUnlockThisDeviceOnly:
            return kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        }
    }
}

public struct AtlasKeychainItem: Equatable, Sendable {
    public let service: String
    public let account: String
    public let valueData: Data
    public let accessibility: AtlasKeychainAccessibility

    public init(
        service: String,
        account: String,
        valueData: Data,
        accessibility: AtlasKeychainAccessibility
    ) {
        self.service = service
        self.account = account
        self.valueData = valueData
        self.accessibility = accessibility
    }
}

public struct AtlasKeychainQuery: Equatable, Sendable {
    public let service: String
    public let account: String

    public init(service: String, account: String) {
        self.service = service
        self.account = account
    }
}

public struct AtlasKeychainUpdate: Equatable, Sendable {
    public let valueData: Data

    public init(valueData: Data) {
        self.valueData = valueData
    }
}

public struct AtlasKeychainCopyResult: Equatable, Sendable {
    public let status: OSStatus
    public let valueData: Data?

    public init(status: OSStatus, valueData: Data?) {
        self.status = status
        self.valueData = valueData
    }
}

public protocol AtlasKeychainClient: Sendable {
    func add(_ item: AtlasKeychainItem) -> OSStatus
    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult
    func update(_ query: AtlasKeychainQuery, with attributes: AtlasKeychainUpdate) -> OSStatus
    func delete(_ query: AtlasKeychainQuery) -> OSStatus
}

public struct SecItemAtlasKeychainClient: AtlasKeychainClient {
    public init() {}

    public func add(_ item: AtlasKeychainItem) -> OSStatus {
        var query = baseQuery(service: item.service, account: item.account)
        query[kSecValueData as String] = item.valueData
        query[kSecAttrAccessible as String] = item.accessibility.secAttribute
        return SecItemAdd(query as CFDictionary, nil)
    }

    public func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        var keychainQuery = baseQuery(service: query.service, account: query.account)
        keychainQuery[kSecReturnData as String] = kCFBooleanTrue
        keychainQuery[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: CFTypeRef?
        let status = SecItemCopyMatching(keychainQuery as CFDictionary, &result)
        return AtlasKeychainCopyResult(status: status, valueData: result as? Data)
    }

    public func update(_ query: AtlasKeychainQuery, with attributes: AtlasKeychainUpdate) -> OSStatus {
        let keychainQuery = baseQuery(service: query.service, account: query.account)
        let updateAttributes: [String: Any] = [
            kSecValueData as String: attributes.valueData,
        ]
        return SecItemUpdate(keychainQuery as CFDictionary, updateAttributes as CFDictionary)
    }

    public func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        let keychainQuery = baseQuery(service: query.service, account: query.account)
        return SecItemDelete(keychainQuery as CFDictionary)
    }

    private func baseQuery(service: String, account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}

public struct AtlasKeychainVaultKeyStore<Client: AtlasKeychainClient>: AtlasVaultKeyStore {
    public static var defaultService: String {
        "com.atlasvault.vault-key"
    }

    private let client: Client
    private let service: String
    private let accessibility: AtlasKeychainAccessibility

    public init(
        client: Client,
        service: String = Self.defaultService,
        accessibility: AtlasKeychainAccessibility = .afterFirstUnlockThisDeviceOnly
    ) {
        self.client = client
        self.service = service
        self.accessibility = accessibility
    }

    public func loadVaultKey(for vaultID: String) throws -> Data? {
        let query = try keychainQuery(for: vaultID)
        let result = client.copyMatching(query)
        switch result.status {
        case errSecSuccess:
            guard let valueData = result.valueData else {
                throw AtlasKeychainVaultKeyStoreError.unexpectedItemData
            }
            try requireValidVaultKey(valueData)
            return valueData
        case errSecItemNotFound:
            return nil
        default:
            throw AtlasKeychainVaultKeyStoreError.keychainError(result.status)
        }
    }

    public func saveVaultKey(_ key: Data, for vaultID: String) throws {
        try requireValidVaultKey(key)
        let item = try keychainItem(vaultID: vaultID, key: key)
        let status = client.add(item)
        switch status {
        case errSecSuccess:
            return
        case errSecDuplicateItem:
            let updateStatus = client.update(
                AtlasKeychainQuery(service: service, account: item.account),
                with: AtlasKeychainUpdate(valueData: key)
            )
            guard updateStatus == errSecSuccess else {
                throw AtlasKeychainVaultKeyStoreError.keychainError(updateStatus)
            }
        default:
            throw AtlasKeychainVaultKeyStoreError.keychainError(status)
        }
    }

    public func deleteVaultKey(for vaultID: String) throws {
        let query = try keychainQuery(for: vaultID)
        let status = client.delete(query)
        switch status {
        case errSecSuccess, errSecItemNotFound:
            return
        default:
            throw AtlasKeychainVaultKeyStoreError.keychainError(status)
        }
    }

    private func keychainItem(vaultID: String, key: Data) throws -> AtlasKeychainItem {
        AtlasKeychainItem(
            service: service,
            account: try account(for: vaultID),
            valueData: key,
            accessibility: accessibility
        )
    }

    private func keychainQuery(for vaultID: String) throws -> AtlasKeychainQuery {
        AtlasKeychainQuery(service: service, account: try account(for: vaultID))
    }

    private func account(for vaultID: String) throws -> String {
        let trimmedVaultID = vaultID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedVaultID.isEmpty else {
            throw AtlasKeychainVaultKeyStoreError.invalidVaultID
        }
        return vaultID
    }

    private func requireValidVaultKey(_ key: Data) throws {
        guard key.count == AtlasVaultRecordCrypto.vaultKeyByteCount else {
            throw AtlasKeychainVaultKeyStoreError.invalidVaultKeyLength
        }
    }
}

public extension AtlasKeychainVaultKeyStore where Client == SecItemAtlasKeychainClient {
    init(
        service: String = Self.defaultService,
        accessibility: AtlasKeychainAccessibility = .afterFirstUnlockThisDeviceOnly
    ) {
        self.init(
            client: SecItemAtlasKeychainClient(),
            service: service,
            accessibility: accessibility
        )
    }
}
