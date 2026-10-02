import CryptoKit
import Foundation
import Security

#if os(macOS)
import FlutterMacOS
#else
import Flutter
#endif

final class AtlasVaultAppleStoragePlugin {
    static let channelName = "atlas/vault/apple"

    static func register(with messenger: FlutterBinaryMessenger) {
        let channel = FlutterMethodChannel(
            name: channelName,
            binaryMessenger: messenger
        )
        let instance = AtlasVaultAppleStoragePlugin()
        channel.setMethodCallHandler { call, result in
            instance.handle(call, result: result)
        }
    }

    private let fileManager = FileManager.default
    private let service =
        (Bundle.main.bundleIdentifier ?? "atlas") + ".atlasvault.flutter"

    private func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
        do {
            switch call.method {
            case "createVaultKey":
                let arguments = try requiredArguments(call)
                let vaultID = try requiredVaultID(arguments["vault_id"])
                var key = try requiredData(arguments["vault_key"])
                defer { key.resetBytes(in: key.startIndex..<key.endIndex) }
                guard key.count == 32 else { throw StorageError.invalidInput }
                try createKeychainValue(key, account: keyAccount(vaultID))
                result(nil)
            case "loadVaultKey":
                let vaultID = try requiredVaultID(
                    requiredArguments(call)["vault_id"]
                )
                guard let value = try readKeychainValue(account: keyAccount(vaultID))
                else {
                    result(nil)
                    return
                }
                guard value.count == 32 else { throw StorageError.corruptState }
                result(FlutterStandardTypedData(bytes: value))
            case "containsVaultKey":
                let vaultID = try requiredVaultID(
                    requiredArguments(call)["vault_id"]
                )
                result(try readKeychainValue(account: keyAccount(vaultID)) != nil)
            case "deleteVaultKey":
                let vaultID = try requiredVaultID(
                    requiredArguments(call)["vault_id"]
                )
                try deleteKeychainValue(account: keyAccount(vaultID))
                result(nil)
            case "readLocalStore":
                let vaultID = try requiredVaultID(
                    requiredArguments(call)["vault_id"]
                )
                let url = try localStoreURL(vaultID)
                guard fileManager.fileExists(atPath: url.path) else {
                    result(nil)
                    return
                }
                let bytes = try Data(contentsOf: url, options: .mappedIfSafe)
                try validateStoreBytes(bytes)
                result(FlutterStandardTypedData(bytes: bytes))
            case "createLocalStore":
                let arguments = try requiredArguments(call)
                let vaultID = try requiredVaultID(arguments["vault_id"])
                let bytes = try requiredData(arguments["store_bytes"])
                try validateStoreBytes(bytes)
                let url = try localStoreURL(vaultID)
                guard !fileManager.fileExists(atPath: url.path) else {
                    throw StorageError.conflict
                }
                try bytes.write(to: url, options: [.atomic])
                result(nil)
            case "replaceLocalStore":
                let arguments = try requiredArguments(call)
                let vaultID = try requiredVaultID(arguments["vault_id"])
                let bytes = try requiredData(arguments["store_bytes"])
                let expected = try requiredDigest(arguments["expected_sha256"])
                try validateStoreBytes(bytes)
                let url = try localStoreURL(vaultID)
                guard fileManager.fileExists(atPath: url.path) else {
                    throw StorageError.conflict
                }
                let current = try Data(contentsOf: url, options: .mappedIfSafe)
                guard sha256(current) == expected else { throw StorageError.conflict }
                try bytes.write(to: url, options: [.atomic])
                result(nil)
            case "deleteLocalStore":
                let vaultID = try requiredVaultID(
                    requiredArguments(call)["vault_id"]
                )
                let url = try localStoreURL(vaultID)
                if fileManager.fileExists(atPath: url.path) {
                    try fileManager.removeItem(at: url)
                }
                result(nil)
            case "readSelectedVault":
                guard let bytes = try readKeychainValue(account: selectedAccount)
                else {
                    result(nil)
                    return
                }
                guard let value = String(data: bytes, encoding: .utf8) else {
                    throw StorageError.corruptState
                }
                result(try requiredVaultID(value))
            case "createSelectedVault":
                let vaultID = try requiredVaultID(
                    requiredArguments(call)["vault_id"]
                )
                guard let bytes = vaultID.data(using: .utf8) else {
                    throw StorageError.invalidInput
                }
                try createKeychainValue(bytes, account: selectedAccount)
                result(nil)
            case "clearSelectedVault":
                let expected = try requiredVaultID(
                    requiredArguments(call)["expected_vault_id"]
                )
                guard let bytes = try readKeychainValue(account: selectedAccount),
                      String(data: bytes, encoding: .utf8) == expected else {
                    throw StorageError.conflict
                }
                try deleteKeychainValue(account: selectedAccount)
                result(nil)
            default:
                result(FlutterMethodNotImplemented)
            }
        } catch {
            result(
                FlutterError(
                    code: "ATLAS_VAULT_STORAGE_FAILED",
                    message: "Encrypted storage operation failed.",
                    details: nil
                )
            )
        }
    }

    private var selectedAccount: String { "selected-vault" }

    private func keyAccount(_ vaultID: String) -> String {
        "vault-key:\(vaultID)"
    }

    private func requiredArguments(_ call: FlutterMethodCall) throws -> [String: Any] {
        guard let arguments = call.arguments as? [String: Any] else {
            throw StorageError.invalidInput
        }
        return arguments
    }

    private func requiredVaultID(_ value: Any?) throws -> String {
        guard let value = value as? String,
              !value.isEmpty,
              value.count <= 96,
              value.rangeOfCharacter(from: Self.allowedID.inverted) == nil,
              !Self.reservedIDs.contains(value.lowercased()) else {
            throw StorageError.invalidInput
        }
        return value
    }

    private func requiredData(_ value: Any?) throws -> Data {
        if let value = value as? FlutterStandardTypedData {
            return value.data
        }
        if let value = value as? Data { return value }
        throw StorageError.invalidInput
    }

    private func requiredDigest(_ value: Any?) throws -> String {
        guard let value = value as? String,
              value.count == 64,
              value.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else {
            throw StorageError.invalidInput
        }
        return value
    }

    private func localStoreURL(_ vaultID: String) throws -> URL {
        guard let support = fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            throw StorageError.unavailable
        }
        let directory = support
            .appendingPathComponent(service, isDirectory: true)
            .appendingPathComponent("vaults", isDirectory: true)
        try fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        return directory.appendingPathComponent("\(vaultID).atlasvault")
    }

    private func validateStoreBytes(_ value: Data) throws {
        guard !value.isEmpty, value.count <= 128 * 1024 * 1024 else {
            throw StorageError.invalidInput
        }
    }

    private func sha256(_ value: Data) -> String {
        SHA256.hash(data: value).map { String(format: "%02x", $0) }.joined()
    }

    private func createKeychainValue(_ value: Data, account: String) throws {
        let status = SecItemAdd([
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecAttrAccessible: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            kSecValueData: value,
        ] as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw status == errSecDuplicateItem ? StorageError.conflict : .unavailable
        }
    }

    private func readKeychainValue(account: String) throws -> Data? {
        var result: CFTypeRef?
        let status = SecItemCopyMatching([
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecReturnData: true,
            kSecMatchLimit: kSecMatchLimitOne,
        ] as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let value = result as? Data else {
            throw StorageError.unavailable
        }
        return value
    }

    private func deleteKeychainValue(account: String) throws {
        let status = SecItemDelete([
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
        ] as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw StorageError.unavailable
        }
    }

    private static let allowedID = CharacterSet(
        charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    )
    private static let reservedIDs: Set<String> = [
        "saved_search",
        "saved_job",
        "application_note",
        "profile_snippet",
        "draft_metadata",
    ]
}

private enum StorageError: Error {
    case invalidInput
    case conflict
    case corruptState
    case unavailable
}
