import Foundation
@testable import AtlasUI
import XCTest

final class AtlasVaultProtectedStateBoundsTests: XCTestCase {
    func testProtectedStateCategoriesEnforceExactSharedLimits() throws {
        let expected: [AtlasVaultProtectedStateCategory: Int] = [
            .trustedDeviceRegistry: 2 * 1_024 * 1_024,
            .pairingReplayState: 2 * 1_024 * 1_024,
            .pairingTransactionJournal: 64 * 1_024,
            .pairingBootstrap: 128 * 1_024 * 1_024,
            .importedEncryptedState: 128 * 1_024 * 1_024,
        ]

        for (category, limit) in expected {
            XCTAssertEqual(
                AtlasVaultProtectedStateBounds.maximumByteCount(for: category),
                limit
            )
            XCTAssertEqual(
                try AtlasVaultProtectedStateBounds.requireByteCount(
                    limit,
                    for: category
                ),
                limit
            )
            XCTAssertThrowsError(
                try AtlasVaultProtectedStateBounds.requireByteCount(
                    limit + 1,
                    for: category
                )
            )
        }
    }

    func testProtectedStateByteCountsArePositiveIntegers() {
        for invalid in [0, -1] {
            XCTAssertThrowsError(
                try AtlasVaultProtectedStateBounds.requireByteCount(
                    invalid,
                    for: .trustedDeviceRegistry
                )
            )
        }
    }

    func testStagedArtifactAggregateIsOverflowSafeAndBounded() throws {
        let limit = AtlasVaultProtectedStateBounds.maximumStagedArtifactByteCount
        let half = limit / 2
        XCTAssertEqual(
            try AtlasVaultProtectedStateBounds.requireStagedArtifactByteCounts(
                [half, half]
            ),
            limit
        )
        XCTAssertThrowsError(
            try AtlasVaultProtectedStateBounds.requireStagedArtifactByteCounts(
                [half, half + 1]
            )
        )
        XCTAssertThrowsError(
            try AtlasVaultProtectedStateBounds.requireStagedArtifactByteCounts(
                [1, 1, 1, 1, 1]
            )
        )
    }

    func testOversizedRegistryAndReplayFailAtTheirReadBoundary() {
        XCTAssertThrowsError(
            try AtlasVaultTrustedDeviceRegistry.decodeStrict(
                Data(
                    repeating: 0,
                    count: AtlasVaultProtectedStateBounds
                        .maximumTrustedDeviceRegistryByteCount + 1
                )
            )
        )
        XCTAssertThrowsError(
            try AtlasVaultPairingReplayStore.decodeStrict(
                Data(
                    repeating: 0,
                    count: AtlasVaultProtectedStateBounds
                        .maximumPairingReplayStateByteCount + 1
                )
            )
        )
    }

    func testOversizedStagedAggregateNeverReachesPersistence() throws {
        let limit = AtlasVaultProtectedStateBounds.maximumStagedArtifactByteCount
        let half = limit / 2
        XCTAssertNoThrow(
            try transaction(byteCounts: [half, half])
        )

        var persistenceCalled = false
        XCTAssertThrowsError(
            try {
                _ = try transaction(byteCounts: [half, half + 1])
                persistenceCalled = true
            }()
        )
        XCTAssertFalse(persistenceCalled)
    }

    private func transaction(byteCounts: [Int]) throws
        -> AtlasVaultPairingTransaction
    {
        let artifacts = try [
            AtlasVaultStagedPairingArtifact(
                kind: .offer,
                sha256: String(repeating: "d", count: 64),
                byteCount: byteCounts[0]
            ),
            AtlasVaultStagedPairingArtifact(
                kind: .acceptance,
                sha256: String(repeating: "e", count: 64),
                byteCount: byteCounts[1]
            ),
        ]
        return try AtlasVaultPairingTransaction.create(
            transactionID: "42000000-0000-4000-8000-000000000001",
            revision: "42000000-0000-4000-8000-000000000002",
            role: .invitee,
            stage: .acceptanceCreated,
            createdAt: "2026-08-15T10:00:00Z",
            localDeviceID: "avd1-" + String(repeating: "a", count: 64),
            peerDeviceID: "avd1-" + String(repeating: "b", count: 64),
            transcriptSHA256: String(repeating: "c", count: 64),
            offerSHA256: String(repeating: "d", count: 64),
            acceptanceSHA256: String(repeating: "e", count: 64),
            stagedArtifacts: artifacts
        )
    }
}
