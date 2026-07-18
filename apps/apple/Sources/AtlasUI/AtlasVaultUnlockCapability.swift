import Foundation

public enum AtlasVaultUnlockMethod: String, CaseIterable, Equatable, Sendable {
    case localKey = "local_key"
    case passphrase
    case recoveryKey = "recovery_key"
}

public enum AtlasVaultUnlockCapabilityStatus: Equatable, Sendable {
    case available
    case unavailable
}

public enum AtlasVaultKeyUnwrapError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case invalidContext
    case unsupportedFormat
    case unsupportedMethod
    case providerUnavailable
    case invalidSecret
    case invalidKeyLength
    case unwrapFailed

    public var description: String {
        switch self {
        case .invalidContext: "invalidContext"
        case .unsupportedFormat: "unsupportedFormat"
        case .unsupportedMethod: "unsupportedMethod"
        case .providerUnavailable: "providerUnavailable"
        case .invalidSecret: "invalidSecret"
        case .invalidKeyLength: "invalidKeyLength"
        case .unwrapFailed: "unwrapFailed"
        }
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultKeyUnwrapContext:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let vaultID: String
    public let wrappedKey: AtlasVaultWrappedKeyEnvelope

    public init(vaultID: String, wrappedKey: AtlasVaultWrappedKeyEnvelope) throws {
        do {
            self.vaultID = try AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID)
        } catch {
            throw AtlasVaultKeyUnwrapError.invalidContext
        }
        self.wrappedKey = wrappedKey
    }

    public var description: String {
        "AtlasVaultKeyUnwrapContext(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultKeyUnwrapping: Sendable {
    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data
}

public extension AtlasVaultKeyUnwrapping {
    func validatedVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        let key: Data
        do {
            key = try await unwrapVaultKey(context: context, secret: secret)
        } catch is CancellationError {
            await secret.clear()
            throw CancellationError()
        } catch is AtlasVaultSecretBufferError {
            await secret.clear()
            throw AtlasVaultKeyUnwrapError.invalidSecret
        } catch let error as AtlasVaultKeyUnwrapError {
            await secret.clear()
            throw error
        } catch {
            await secret.clear()
            throw AtlasVaultKeyUnwrapError.unwrapFailed
        }
        await secret.clear()
        guard key.count == 32 else {
            throw AtlasVaultKeyUnwrapError.invalidKeyLength
        }
        return key
    }
}

public struct AtlasVaultUnlockCapabilities:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public static let currentProduction = AtlasVaultUnlockCapabilities(
        localKeyAvailable: true,
        passphraseProvider: nil,
        recoveryKeyProvider: nil
    )

    private let localKeyStatus: AtlasVaultUnlockCapabilityStatus
    private let passphraseStatus: AtlasVaultUnlockCapabilityStatus
    private let recoveryKeyStatus: AtlasVaultUnlockCapabilityStatus

    public init(
        localKeyAvailable: Bool,
        passphraseProvider: (any AtlasVaultKeyUnwrapping)?,
        recoveryKeyProvider: (any AtlasVaultKeyUnwrapping)?
    ) {
        localKeyStatus = localKeyAvailable ? .available : .unavailable
        passphraseStatus = passphraseProvider == nil ? .unavailable : .available
        recoveryKeyStatus = recoveryKeyProvider == nil ? .unavailable : .available
    }

    public func status(for method: AtlasVaultUnlockMethod) -> AtlasVaultUnlockCapabilityStatus {
        switch method {
        case .localKey: localKeyStatus
        case .passphrase: passphraseStatus
        case .recoveryKey: recoveryKeyStatus
        }
    }

    public var availableMethods: [AtlasVaultUnlockMethod] {
        AtlasVaultUnlockMethod.allCases.filter { status(for: $0) == .available }
    }

    public var description: String {
        "AtlasVaultUnlockCapabilities(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}
