import Foundation
import Security

public enum AtlasKeychainDeviceIdentityStoreError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible
{
    case invalidSecret
    case collision
    case unavailable

    public var description: String {
        "AtlasVault device identity Keychain operation failed."
    }
}

public struct AtlasKeychainDeviceIdentityStore<Client: AtlasKeychainClient>:
    Sendable
{
    public static var defaultService: String {
        "com.atlasvault.device-identity.v1"
    }

    public static var defaultAccount: String {
        "primary"
    }

    public static var maximumSecretByteCount: Int {
        16 * 1024
    }

    private let client: Client
    private let service: String
    private let account: String
    private let accessibility: AtlasKeychainAccessibility

    public init(
        client: Client,
        service: String = Self.defaultService,
        account: String = Self.defaultAccount,
        accessibility: AtlasKeychainAccessibility = .afterFirstUnlockThisDeviceOnly
    ) {
        self.client = client
        self.service = service
        self.account = account
        self.accessibility = accessibility
    }

    public func createPrimaryIdentity(_ canonicalSecretBundle: Data) throws {
        try validate(canonicalSecretBundle)
        let status = client.add(
            AtlasKeychainItem(
                service: service,
                account: account,
                valueData: Data(canonicalSecretBundle),
                accessibility: accessibility
            )
        )
        switch status {
        case errSecSuccess:
            guard try loadPrimaryIdentity() == canonicalSecretBundle else {
                throw AtlasKeychainDeviceIdentityStoreError.invalidSecret
            }
        case errSecDuplicateItem:
            throw AtlasKeychainDeviceIdentityStoreError.collision
        default:
            throw AtlasKeychainDeviceIdentityStoreError.unavailable
        }
    }

    public func loadPrimaryIdentity() throws -> Data? {
        let result = client.copyMatching(query)
        switch result.status {
        case errSecSuccess:
            guard let value = result.valueData else {
                throw AtlasKeychainDeviceIdentityStoreError.invalidSecret
            }
            let copy = Data(value)
            try validate(copy)
            return copy
        case errSecItemNotFound:
            return nil
        default:
            throw AtlasKeychainDeviceIdentityStoreError.unavailable
        }
    }

    public func containsPrimaryIdentity() throws -> Bool {
        try loadPrimaryIdentity() != nil
    }

    public func deletePrimaryIdentity() throws {
        switch client.delete(query) {
        case errSecSuccess, errSecItemNotFound:
            return
        default:
            throw AtlasKeychainDeviceIdentityStoreError.unavailable
        }
    }

    private var query: AtlasKeychainQuery {
        AtlasKeychainQuery(service: service, account: account)
    }

    private func validate(_ value: Data) throws {
        guard
            !value.isEmpty,
            value.count <= Self.maximumSecretByteCount
        else {
            throw AtlasKeychainDeviceIdentityStoreError.invalidSecret
        }
        do {
            let secret = try AtlasVaultDeviceIdentitySecret.decodeStrict(value)
            guard try secret.canonicalData() == value else {
                throw AtlasKeychainDeviceIdentityStoreError.invalidSecret
            }
            _ = try secret.loadIdentity()
        } catch {
            throw AtlasKeychainDeviceIdentityStoreError.invalidSecret
        }
    }
}

public extension AtlasKeychainDeviceIdentityStore
where Client == SecItemAtlasKeychainClient {
    init(
        service: String = Self.defaultService,
        account: String = Self.defaultAccount,
        accessibility: AtlasKeychainAccessibility = .afterFirstUnlockThisDeviceOnly
    ) {
        self.init(
            client: SecItemAtlasKeychainClient(),
            service: service,
            account: account,
            accessibility: accessibility
        )
    }
}
