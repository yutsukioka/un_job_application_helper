import Foundation

public enum AtlasVaultProtectedStateCategory: Hashable, Sendable {
    case trustedDeviceRegistry
    case pairingReplayState
    case pairingTransactionJournal
    case pairingBootstrap
    case importedEncryptedState
}

public enum AtlasVaultProtectedStateBoundsError:
    Error, Equatable, Sendable
{
    case invalidByteCount
}

public enum AtlasVaultProtectedStateBounds {
    public static let maximumTrustedDeviceRegistryByteCount = 2 * 1_024 * 1_024
    public static let maximumPairingReplayStateByteCount = 2 * 1_024 * 1_024
    public static let maximumPairingTransactionJournalByteCount = 64 * 1_024
    public static let maximumPairingBootstrapByteCount = 128 * 1_024 * 1_024
    public static let maximumImportedEncryptedStateByteCount = 128 * 1_024 * 1_024
    public static let maximumStagedArtifactByteCount = 128 * 1_024 * 1_024
    public static let maximumStagedArtifactCount = 4

    public static func maximumByteCount(
        for category: AtlasVaultProtectedStateCategory
    ) -> Int {
        switch category {
        case .trustedDeviceRegistry: maximumTrustedDeviceRegistryByteCount
        case .pairingReplayState: maximumPairingReplayStateByteCount
        case .pairingTransactionJournal:
            maximumPairingTransactionJournalByteCount
        case .pairingBootstrap: maximumPairingBootstrapByteCount
        case .importedEncryptedState: maximumImportedEncryptedStateByteCount
        }
    }

    @discardableResult
    public static func requireByteCount(
        _ byteCount: Int,
        for category: AtlasVaultProtectedStateCategory
    ) throws -> Int {
        guard
            byteCount > 0,
            byteCount <= maximumByteCount(for: category)
        else {
            throw AtlasVaultProtectedStateBoundsError.invalidByteCount
        }
        return byteCount
    }

    @discardableResult
    public static func requireStagedArtifactByteCounts<S: Sequence>(
        _ byteCounts: S
    ) throws -> Int where S.Element == Int {
        var total = 0
        var count = 0
        for byteCount in byteCounts {
            count += 1
            guard
                count <= maximumStagedArtifactCount,
                byteCount > 0,
                byteCount <= maximumStagedArtifactByteCount,
                total <= maximumStagedArtifactByteCount - byteCount
            else {
                throw AtlasVaultProtectedStateBoundsError.invalidByteCount
            }
            total += byteCount
        }
        return total
    }
}
