import Foundation

public protocol AtlasVaultKeyStore: Sendable {
    func loadVaultKey(for vaultID: String) throws -> Data?
    func saveVaultKey(_ key: Data, for vaultID: String) throws
    func deleteVaultKey(for vaultID: String) throws
}

public enum AtlasVaultUnlockError: Error, Equatable, Sendable {
    case invalidVaultKeyLength
}

public enum AtlasVaultUnlockState: Equatable, Sendable {
    case locked
    case unlocking
    case unlocked
    case keyUnavailable
    case invalidKey
    case corruptVault
}

public struct AtlasVaultSession: Sendable {
    public let vaultID: String
    private var vaultKey: Data

    public init(vaultID: String, vaultKey: Data) throws {
        guard vaultKey.count == AtlasVaultRecordCrypto.vaultKeyByteCount else {
            throw AtlasVaultUnlockError.invalidVaultKeyLength
        }
        self.vaultID = vaultID
        self.vaultKey = vaultKey
    }

    public var vaultKeyByteCount: Int {
        vaultKey.count
    }

    public func withVaultKey<Result>(_ operation: (Data) throws -> Result) rethrows -> Result {
        try operation(vaultKey)
    }

    public mutating func wipeVaultKey() {
        guard !vaultKey.isEmpty else { return }
        vaultKey.resetBytes(in: vaultKey.startIndex..<vaultKey.endIndex)
        vaultKey.removeAll(keepingCapacity: false)
    }
}

public struct AtlasVaultUnlockService<KeyStore: AtlasVaultKeyStore>: Sendable {
    private let keyStore: KeyStore
    public private(set) var state: AtlasVaultUnlockState
    public private(set) var session: AtlasVaultSession?

    public init(keyStore: KeyStore, initialState: AtlasVaultUnlockState = .locked) {
        self.keyStore = keyStore
        self.state = initialState
        self.session = nil
    }

    public func saveVaultKey(_ key: Data, for vaultID: String) throws {
        try Self.requireValidVaultKey(key)
        try keyStore.saveVaultKey(key, for: vaultID)
    }

    public mutating func deleteVaultKey(for vaultID: String) throws {
        try keyStore.deleteVaultKey(for: vaultID)
        if session?.vaultID == vaultID {
            clearSession(state: .locked)
        }
    }

    @discardableResult
    public mutating func unlockWithStoredKey(for vaultID: String) -> AtlasVaultUnlockState {
        state = .unlocking
        do {
            guard let key = try keyStore.loadVaultKey(for: vaultID) else {
                clearSession(state: .keyUnavailable)
                return state
            }
            return unlock(vaultID: vaultID, vaultKey: key)
        } catch {
            clearSession(state: .keyUnavailable)
            return state
        }
    }

    @discardableResult
    public mutating func unlock(vaultID: String, vaultKey: Data) -> AtlasVaultUnlockState {
        state = .unlocking
        guard Self.isValidVaultKey(vaultKey) else {
            clearSession(state: .invalidKey)
            return state
        }
        do {
            session = try AtlasVaultSession(vaultID: vaultID, vaultKey: vaultKey)
            state = .unlocked
        } catch {
            clearSession(state: .invalidKey)
        }
        return state
    }

    public mutating func lock() {
        clearSession(state: .locked)
    }

    public static func isValidVaultKey(_ key: Data) -> Bool {
        key.count == AtlasVaultRecordCrypto.vaultKeyByteCount
    }

    private static func requireValidVaultKey(_ key: Data) throws {
        guard isValidVaultKey(key) else {
            throw AtlasVaultUnlockError.invalidVaultKeyLength
        }
    }

    private mutating func clearSession(state: AtlasVaultUnlockState) {
        session?.wipeVaultKey()
        session = nil
        self.state = state
    }
}
