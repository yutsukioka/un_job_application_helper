import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultKeyDeliveryTests: XCTestCase {
    func testPairingBootstrapRejectsNonASCIIAuthenticatedMetadata() throws {
        let root = try loadRoot()
        let source = try XCTUnwrap(root["bootstrap"] as? [String: Any])

        for value in ["record-e\u{0301}", "record-\u{1F512}", "record-\nline"] {
            var bootstrap = source
            var records = try XCTUnwrap(
                bootstrap["records"] as? [[String: Any]]
            )
            records[0]["key_id"] = value
            bootstrap["records"] = records
            let data = try JSONSerialization.data(withJSONObject: bootstrap)

            XCTAssertThrowsError(
                try AtlasVaultPairingBootstrap.decodeStrict(data),
                value
            )
        }
    }

    func testSASBootstrapDeliveryAndAcknowledgementMatchVector() throws {
        let root = try loadRoot()
        let inviter = try identity(root, name: "inviter")
        let invitee = try identity(root, name: "invitee")
        let session = try data(root["pairing_session_key_b64"])
        let transcript = try data(hex: try string(root["transcript_sha256"]))
        XCTAssertEqual(
            try AtlasVaultKeyDelivery.deriveSAS(
                pairingSessionKey: session,
                transcriptSHA256: transcript
            ),
            try string(root["sas"])
        )

        let fixedRequest = try AtlasVaultSignedPairingKeyRequest.decodeStrict(
            try data(root["signed_key_request_canonical_b64"])
        )
        let freshRequest = try AtlasVaultKeyDelivery.createKeyRequest(
            invitee: invitee,
            requestID: try string(root["request_id"]),
            transcriptSHA256: transcript,
            inviterDeviceID: inviter.deviceID,
            inviteeEphemeralPublicKey: try data(
                root["invitee_ephemeral_public_key_b64"]
            ),
            nonce: try data(root["request_nonce_b64"]),
            issuedAt: try string(root["request_issued_at"]),
            expiresAt: try string(root["request_expires_at"])
        )
        XCTAssertEqual(
            try freshRequest.request.canonicalData(),
            try fixedRequest.request.canonicalData()
        )
        _ = try AtlasVaultKeyDelivery.verifyKeyRequest(
            freshRequest,
            transcriptSHA256: transcript,
            inviterDeviceID: inviter.deviceID,
            inviteeDeviceID: invitee.deviceID,
            currentTime: try string(root["verification_time"])
        )
        let bootstrap = try AtlasVaultPairingBootstrap.decodeStrict(
            try data(root["bootstrap_canonical_b64"])
        )
        let fixedDelivery = try AtlasVaultSignedVaultKeyDelivery.decodeStrict(
            try data(root["signed_delivery_canonical_b64"])
        )
        let delivery = try AtlasVaultKeyDelivery.createDelivery(
            inviter: inviter,
            keyRequest: fixedRequest,
            transcriptSHA256: transcript,
            bootstrap: bootstrap,
            vaultKey: try data(root["test_only_vault_key_b64"]),
            inviterEphemeralPrivateKey: try data(
                root["inviter_ephemeral_private_key_b64"]
            ),
            nonce: try data(root["delivery_nonce_b64"]),
            deliveryID: try string(root["delivery_id"]),
            keyEpoch: try integer(root["vault_key_epoch"]),
            expiresAt: try string(root["delivery_expires_at"])
        )
        XCTAssertEqual(try delivery.delivery.canonicalData(), try fixedDelivery.delivery.canonicalData())
        XCTAssertEqual(
            try AtlasVaultKeyDelivery.openDelivery(
                delivery,
                keyRequest: fixedRequest,
                inviteeEphemeralPrivateKey: try data(
                    root["invitee_ephemeral_private_key_b64"]
                ),
                bootstrap: bootstrap,
                transcriptSHA256: transcript,
                currentTime: try string(root["verification_time"])
            ),
            try data(root["test_only_vault_key_b64"])
        )
        let acknowledgement = try AtlasVaultKeyDelivery.createAcknowledgement(
            invitee: invitee,
            acknowledgementID: try string(root["acknowledgement_id"]),
            delivery: delivery,
            installedAt: try string(root["installed_at"])
        )
        let fixedAcknowledgement = try AtlasVaultSignedPairingAcknowledgement.decodeStrict(
            try data(root["signed_acknowledgement_canonical_b64"])
        )
        XCTAssertEqual(
            try acknowledgement.acknowledgement.canonicalData(),
            try fixedAcknowledgement.acknowledgement.canonicalData()
        )
        _ = try AtlasVaultKeyDelivery.verifyAcknowledgement(
            acknowledgement,
            delivery: delivery,
            inviterDeviceID: inviter.deviceID,
            inviteeDeviceID: invitee.deviceID
        )
        XCTAssertEqual(
            try AtlasVaultKeyDelivery.openDelivery(
                fixedDelivery,
                keyRequest: fixedRequest,
                inviteeEphemeralPrivateKey: try data(
                    root["invitee_ephemeral_private_key_b64"]
                ),
                bootstrap: bootstrap,
                transcriptSHA256: transcript,
                currentTime: try string(root["verification_time"])
            ),
            try data(root["test_only_vault_key_b64"])
        )
        _ = try AtlasVaultKeyDelivery.verifyAcknowledgement(
            fixedAcknowledgement,
            delivery: fixedDelivery,
            inviterDeviceID: inviter.deviceID,
            inviteeDeviceID: invitee.deviceID
        )

        XCTAssertEqual(try bootstrap.sha256Hex(), try string(root["bootstrap_sha256"]))
        XCTAssertEqual(bootstrap.records.count, 2)
        XCTAssertFalse(bootstrap.records[0].deleted)
        XCTAssertTrue(bootstrap.records[1].deleted)
        XCTAssertEqual(
            fixedRequest.request.transcriptSHA256,
            try string(root["transcript_sha256"])
        )
        XCTAssertEqual(delivery.delivery.bootstrapSHA256, try string(root["bootstrap_sha256"]))
        XCTAssertEqual(acknowledgement.acknowledgement.deliveryID, delivery.delivery.deliveryID)
    }

    func testWrongEphemeralKeyAndExpiryFailWithRedactedErrors() throws {
        let root = try loadRoot()
        let delivery = try AtlasVaultSignedVaultKeyDelivery.decodeStrict(
            try data(root["signed_delivery_canonical_b64"])
        )
        let request = try AtlasVaultSignedPairingKeyRequest.decodeStrict(
            try data(root["signed_key_request_canonical_b64"])
        )
        let bootstrap = try AtlasVaultPairingBootstrap.decodeStrict(
            try data(root["bootstrap_canonical_b64"])
        )
        let secret = try string(root["test_only_vault_key_b64"])
        XCTAssertThrowsError(
            try AtlasVaultKeyDelivery.openDelivery(
                delivery,
                keyRequest: request,
                inviteeEphemeralPrivateKey: Data(repeating: 0, count: 32),
                bootstrap: bootstrap,
                transcriptSHA256: try data(hex: try string(root["transcript_sha256"])),
                currentTime: try string(root["expired_verification_time"])
            )
        ) { error in
            XCTAssertFalse(String(describing: error).contains(secret))
        }
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

    private func integer(_ value: Any?) throws -> Int {
        guard let value = value as? Int else { throw NSError(domain: "AtlasVaultTests", code: 7) }
        return value
    }

    private func identity(_ root: [String: Any], name: String) throws -> AtlasVaultDeviceIdentity {
        let value = try dictionary(root[name])
        return try AtlasVaultDeviceIdentity(
            signingPrivateSeed: try data(value["signing_private_seed_b64"]),
            agreementPrivateKey: try data(value["agreement_private_key_b64"]),
            createdAt: try string(value["created_at"]),
            keyEpoch: try integer(value["key_epoch"])
        )
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
