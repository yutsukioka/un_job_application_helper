import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultKeyDeliveryTests: XCTestCase {
    func testSASBootstrapDeliveryAndAcknowledgementMatchVector() throws {
        let root = try loadRoot()
        let session = try data(root["pairing_session_key_b64"])
        let transcript = try data(hex: try string(root["transcript_sha256"]))
        XCTAssertEqual(
            try AtlasVaultKeyDelivery.deriveSAS(
                pairingSessionKey: session,
                transcriptSHA256: transcript
            ),
            try string(root["sas"])
        )

        let request = try AtlasVaultSignedPairingKeyRequest.decodeStrict(
            try data(root["signed_key_request_canonical_b64"])
        )
        let bootstrap = try AtlasVaultPairingBootstrap.decodeStrict(
            try data(root["bootstrap_canonical_b64"])
        )
        let delivery = try AtlasVaultSignedVaultKeyDelivery.decodeStrict(
            try data(root["signed_delivery_canonical_b64"])
        )
        let acknowledgement = try AtlasVaultSignedPairingAcknowledgement.decodeStrict(
            try data(root["signed_acknowledgement_canonical_b64"])
        )

        XCTAssertEqual(try bootstrap.sha256Hex(), try string(root["bootstrap_sha256"]))
        XCTAssertEqual(bootstrap.records.count, 2)
        XCTAssertFalse(bootstrap.records[0].deleted)
        XCTAssertTrue(bootstrap.records[1].deleted)
        XCTAssertEqual(request.request.transcriptSHA256, try string(root["transcript_sha256"]))
        XCTAssertEqual(delivery.delivery.bootstrapSHA256, try string(root["bootstrap_sha256"]))
        XCTAssertEqual(acknowledgement.acknowledgement.deliveryID, delivery.delivery.deliveryID)
    }

    func testAllPairingArtifactBytesAreCanonical() throws {
        let root = try loadRoot()
        let artifacts = try dictionary(root["artifacts"])
        for kind in ["offer", "acceptance", "delivery", "acknowledgement"] {
            let value = try dictionary(artifacts[kind])
            let bytes = try data(value["canonical_b64"])
            let artifact = try AtlasVaultPairingArtifact.decodeStrict(bytes)
            XCTAssertEqual(artifact.kind.rawValue, kind)
            XCTAssertEqual(try artifact.canonicalData(), bytes)
            XCTAssertEqual(try artifact.sha256Hex(), try string(value["sha256"]))
        }
    }

    private func loadRoot() throws -> [String: Any] {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("../../contracts/sync/test_vectors/atlasvault_trusted_pairing_delivery_vectors_v1.json"),
            source.appendingPathComponent("../../../../contracts/sync/test_vectors/atlasvault_trusted_pairing_delivery_vectors_v1.json"),
        ]
        guard let url = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) else {
            throw NSError(domain: "AtlasVaultTests", code: 1)
        }
        return try dictionary(JSONSerialization.jsonObject(with: Data(contentsOf: url)))
    }

    private func dictionary(_ value: Any?) throws -> [String: Any] {
        guard let value = value as? [String: Any] else { throw NSError(domain: "AtlasVaultTests", code: 2) }
        return value
    }

    private func string(_ value: Any?) throws -> String {
        guard let value = value as? String else { throw NSError(domain: "AtlasVaultTests", code: 3) }
        return value
    }

    private func data(_ value: Any?) throws -> Data {
        guard let value = value as? String, let data = Data(base64Encoded: value) else {
            throw NSError(domain: "AtlasVaultTests", code: 4)
        }
        return data
    }

    private func data(hex: String) throws -> Data {
        guard hex.count % 2 == 0 else { throw NSError(domain: "AtlasVaultTests", code: 5) }
        var result = Data()
        var index = hex.startIndex
        while index < hex.endIndex {
            let end = hex.index(index, offsetBy: 2)
            guard let byte = UInt8(hex[index..<end], radix: 16) else {
                throw NSError(domain: "AtlasVaultTests", code: 6)
            }
            result.append(byte)
            index = end
        }
        return result
    }
}
