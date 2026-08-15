import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultTrustedDeviceRegistryTests: XCTestCase {
    func testRegistryAndReplayMatchStrictSharedVector() throws {
        let root = try loadRoot()
        let registry = try AtlasVaultTrustedDeviceRegistry.decodeStrict(
            try canonicalData(root["trusted_registry"])
        )
        let replay = try AtlasVaultPairingReplayStore.decodeStrict(
            try canonicalData(root["replay_store"])
        )
        XCTAssertEqual(
            try registry.canonicalData(),
            try data(root["trusted_registry_canonical_b64"])
        )
        XCTAssertEqual(
            try replay.canonicalData(),
            try data(root["replay_store_canonical_b64"])
        )
    }

    func testTrustCommitIsCreateOnlyAndIdempotent() throws {
        let root = try loadRoot()
        let empty = try AtlasVaultTrustedDeviceRegistry.decodeStrict(
            try canonicalData(root["empty_trusted_registry"])
        )
        let peer = try JSONDecoder().decode(
            AtlasVaultTrustedDevicePeer.self,
            from: try canonicalData(root["trusted_peer"])
        )
        let committed = try AtlasVaultTrustedDeviceRegistryFoundation.commit(
            peer,
            to: empty,
            revision: try string(root["registry_commit_revision"]),
            updatedAt: try string(root["registry_commit_timestamp"])
        )
        XCTAssertEqual(committed.outcome, .committed)
        let duplicate = try AtlasVaultTrustedDeviceRegistryFoundation.commit(
            peer,
            to: committed.registry,
            revision: try string(root["unused_revision"]),
            updatedAt: try string(root["later_timestamp"])
        )
        XCTAssertEqual(duplicate.outcome, .alreadyTrusted)
        XCTAssertEqual(duplicate.registry, committed.registry)

        let conflict = try AtlasVaultTrustedDevicePeer(
            peerDeviceID: peer.peerDeviceID,
            peerDescriptor: peer.peerDescriptor,
            pairingTranscriptSHA256: peer.pairingTranscriptSHA256,
            linkedAt: peer.linkedAt,
            role: peer.role,
            vaultID: peer.vaultID,
            keyEpoch: peer.keyEpoch,
            deliveryID: try string(root["conflicting_delivery_id"]),
            acknowledgementSHA256: peer.acknowledgementSHA256
        )
        XCTAssertThrowsError(
            try AtlasVaultTrustedDeviceRegistryFoundation.commit(
                conflict,
                to: committed.registry,
                revision: try string(root["unused_revision"]),
                updatedAt: try string(root["later_timestamp"])
            )
        )
    }

    func testReplayDuplicateIgnoresLocalConsumptionTimestamps() throws {
        let root = try loadRoot()
        let replay = try AtlasVaultPairingReplayStore.decodeStrict(
            try canonicalData(root["replay_store"])
        )
        let existing = try XCTUnwrap(replay.entries.first)
        let retried = try AtlasVaultPairingReplayEntry(
            kind: existing.kind,
            objectID: existing.objectID,
            transcriptSHA256: existing.transcriptSHA256,
            consumedAt: "2026-08-15T10:06:00Z",
            expiresAt: "2026-08-15T10:11:00Z"
        )

        let result = try AtlasVaultPairingReplayFoundation.consume(
            retried,
            in: replay,
            revision: try string(root["unused_revision"]),
            updatedAt: "2026-08-15T10:06:00Z",
            currentTime: "2026-08-15T10:06:00Z"
        )

        XCTAssertEqual(result.outcome, .alreadyConsumed)
        XCTAssertEqual(result.store, replay)
    }

    func testReplayRejectsNewEntryExpiredAtCurrentTime() throws {
        let root = try loadRoot()
        let replay = try AtlasVaultPairingReplayStore.decodeStrict(
            try canonicalData(root["replay_store"])
        )
        let expired = try AtlasVaultPairingReplayEntry(
            kind: "offer",
            objectID: "55000000-0000-4000-8000-000000000001",
            transcriptSHA256: String(repeating: "d", count: 64),
            consumedAt: "2026-08-15T10:05:59Z",
            expiresAt: "2026-08-15T10:06:00Z"
        )

        XCTAssertThrowsError(
            try AtlasVaultPairingReplayFoundation.consume(
                expired,
                in: replay,
                revision: try string(root["unused_revision"]),
                updatedAt: "2026-08-15T10:06:00Z",
                currentTime: "2026-08-15T10:06:00Z"
            )
        )
    }

    private func loadRoot() throws -> [String: Any] {
        let url = try vectorURL()
        return try dictionary(JSONSerialization.jsonObject(with: Data(contentsOf: url)))
    }

    private func vectorURL() throws -> URL {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("../../contracts/sync/test_vectors/atlasvault_trusted_pairing_delivery_vectors_v1.json"),
            source.appendingPathComponent("../../../../contracts/sync/test_vectors/atlasvault_trusted_pairing_delivery_vectors_v1.json"),
        ]
        guard let result = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) else {
            throw NSError(domain: "AtlasVaultTests", code: 1)
        }
        return result
    }

    private func canonicalData(_ value: Any?) throws -> Data {
        guard let value else { throw NSError(domain: "AtlasVaultTests", code: 2) }
        return try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes])
    }

    private func dictionary(_ value: Any) throws -> [String: Any] {
        guard let value = value as? [String: Any] else { throw NSError(domain: "AtlasVaultTests", code: 3) }
        return value
    }

    private func string(_ value: Any?) throws -> String {
        guard let value = value as? String else { throw NSError(domain: "AtlasVaultTests", code: 4) }
        return value
    }

    private func data(_ value: Any?) throws -> Data {
        guard let value = value as? String, let data = Data(base64Encoded: value) else {
            throw NSError(domain: "AtlasVaultTests", code: 5)
        }
        return data
    }
}
