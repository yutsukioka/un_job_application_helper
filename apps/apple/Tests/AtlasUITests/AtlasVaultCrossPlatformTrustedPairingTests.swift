import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultCrossPlatformTrustedPairingTests: XCTestCase {
    func testProductionPairingJourneyPreservesManualArtifactRing() throws {
        let pairing = try Self.source(named: "AtlasVaultPairingView.swift")
        let harness = try Self.source(
            named: "AtlasVaultProductionCompositionHarness.swift"
        )

        for required in [
            "AtlasVaultPairingArtifact",
            "AtlasVaultPairingTransaction",
            "AtlasVaultTrustedDeviceRegistry",
            "AtlasVaultPairingReplayStore",
            "AtlasVaultPairingBootstrap",
            "AtlasVaultSignedVaultKeyDelivery",
            "AtlasVaultSignedPairingAcknowledgement",
        ] {
            XCTAssertTrue(pairing.contains(required), required)
        }
        for required in [
            "pairingContext",
            "pairingOwner.stopAndDrain",
            "pairingOwner.clearSensitiveInput",
        ] {
            XCTAssertTrue(harness.contains(required), required)
        }
    }

    func testExternalAppleAndroidWindowsPairingRingUsesCanonicalArtifacts() throws {
        guard let directoryPath = ProcessInfo.processInfo.environment[
            "ATLAS_PAIRING_ARTIFACT_DIR"
        ], !directoryPath.isEmpty else { return }
        let stage = ProcessInfo.processInfo.environment[
            "ATLAS_PAIRING_RING_STAGE"
        ] ?? ""
        guard stage == "produce" || stage == "verify" else { return }

        let vectorData = try Data(contentsOf: Self.vectorURL())
        let root = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: vectorData) as? [String: Any]
        )
        let artifacts = try XCTUnwrap(root["artifacts"] as? [String: Any])
        let expectedPayloads = try XCTUnwrap(
            root["expected_payloads"] as? [String: Any]
        )
        let sentinel = try XCTUnwrap(
            expectedPayloads["unsupported_private_sentinel"] as? String
        )
        let directory = URL(fileURLWithPath: directoryPath, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )

        for kind in ["offer", "acceptance", "delivery", "acknowledgement"] {
            let entry = try XCTUnwrap(artifacts[kind] as? [String: Any])
            let encoded = try XCTUnwrap(entry["canonical_b64"] as? String)
            let expected = try XCTUnwrap(Data(base64Encoded: encoded))
            let digest = try XCTUnwrap(entry["sha256"] as? String)
            let prefix = stage == "produce"
                ? "apple-to-android"
                : "windows-to-apple"
            let artifactURL = directory.appendingPathComponent(
                "\(prefix)-\(kind).atlaspair"
            )
            let digestURL = directory.appendingPathComponent(
                "\(prefix)-\(kind).sha256"
            )
            if stage == "produce" {
                try expected.write(to: artifactURL, options: .atomic)
                try Data("\(digest)\n".utf8).write(
                    to: digestURL,
                    options: .atomic
                )
            }

            let exchanged = try Data(contentsOf: artifactURL)
            let artifact = try AtlasVaultPairingArtifact.decodeStrict(exchanged)
            XCTAssertEqual(try artifact.canonicalData(), expected)
            XCTAssertEqual(try artifact.sha256Hex(), digest)
            XCTAssertEqual(
                try String(contentsOf: digestURL, encoding: .utf8)
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                digest
            )
            let text = try XCTUnwrap(String(data: exchanged, encoding: .utf8))
            XCTAssertFalse(text.contains(sentinel))
            XCTAssertFalse(text.contains(#""vault_key""#))
            XCTAssertFalse(text.contains(#""private_key""#))
        }
    }

    private static func source(named name: String) throws -> String {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let url = root
            .appendingPathComponent("apps/apple/Sources/AtlasUI")
            .appendingPathComponent(name)
        return try String(contentsOf: url, encoding: .utf8)
    }

    private static func vectorURL() throws -> URL {
        let current = URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath,
            isDirectory: true
        )
        let source = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let candidates = [
            current.appendingPathComponent(
                "../../contracts/sync/test_vectors/atlasvault_trusted_pairing_delivery_vectors_v1.json"
            ),
            current.appendingPathComponent(
                "contracts/sync/test_vectors/atlasvault_trusted_pairing_delivery_vectors_v1.json"
            ),
            source.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/atlasvault_trusted_pairing_delivery_vectors_v1.json"
            ),
        ]
        guard let candidate = candidates.first(where: {
            FileManager.default.fileExists(atPath: $0.standardizedFileURL.path)
        }) else {
            throw CocoaError(.fileNoSuchFile)
        }
        return candidate.standardizedFileURL
    }
}
