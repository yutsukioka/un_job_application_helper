import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultPairingFoundationTests: XCTestCase {
    func testOfferAndAcceptanceMatchExactVectorBytesAndSignatures() throws {
        let root = try loadRoot()
        let pairing = try dictionary(root["pairing"], context: "pairing")
        let inviter = try identity(root, name: "device_a")
        let invitee = try identity(root, name: "device_b")

        let offer = try AtlasVaultPairingFoundation.createOffer(
            inviter: inviter,
            offerID: try string(pairing["offer_id"], context: "offer_id"),
            nonce: try data(pairing["offer_nonce"], context: "offer_nonce"),
            issuedAt: try string(pairing["issued_at"], context: "issued_at"),
            expiresAt: try string(pairing["expires_at"], context: "expires_at")
        )
        let acceptance = try AtlasVaultPairingFoundation.createAcceptance(
            invitee: invitee,
            signedOffer: offer,
            nonce: try data(pairing["acceptance_nonce"], context: "acceptance_nonce"),
            acceptedAt: try string(pairing["accepted_at"], context: "accepted_at"),
            currentTime: try string(pairing["verification_time"], context: "verification_time")
        )

        XCTAssertEqual(offer.signature, try data(pairing["offer_signature"], context: "offer signature"))
        XCTAssertEqual(try offer.canonicalData(), try data(pairing["signed_offer_canonical_json_b64"], context: "offer canonical"))
        XCTAssertEqual(try offer.sha256Hex(), try string(pairing["offer_sha256"], context: "offer hash"))
        XCTAssertEqual(acceptance.signature, try data(pairing["acceptance_signature"], context: "acceptance signature"))
        XCTAssertEqual(
            try acceptance.canonicalData(),
            try data(pairing["signed_acceptance_canonical_json_b64"], context: "acceptance canonical")
        )
    }

    func testTranscriptSessionAndDirectionalProofsMatchVector() throws {
        let root = try loadRoot()
        let pairing = try dictionary(root["pairing"], context: "pairing")
        let inviter = try identity(root, name: "device_a")
        let invitee = try identity(root, name: "device_b")
        let offer = try signedOffer(pairing)
        let acceptance = try signedAcceptance(pairing)

        let transcript = try AtlasVaultPairingFoundation.transcriptSHA256(offer: offer, acceptance: acceptance)
        let inviterSession = try AtlasVaultPairingFoundation.deriveSessionKey(
            localIdentity: inviter,
            offer: offer,
            acceptance: acceptance
        )
        let inviteeSession = try AtlasVaultPairingFoundation.deriveSessionKey(
            localIdentity: invitee,
            offer: offer,
            acceptance: acceptance
        )
        let proofs = try AtlasVaultPairingFoundation.deriveProofs(
            sessionKey: inviterSession,
            transcriptSHA256: transcript
        )

        XCTAssertEqual(transcript.hexString, try string(pairing["transcript_sha256"], context: "transcript"))
        XCTAssertEqual(inviterSession, inviteeSession)
        XCTAssertEqual(inviterSession, try data(pairing["hkdf_session_key"], context: "session key"))
        XCTAssertEqual(proofs.inviter, try data(pairing["inviter_proof"], context: "inviter proof"))
        XCTAssertEqual(proofs.invitee, try data(pairing["invitee_proof"], context: "invitee proof"))
    }

    func testReplayGuardConsumesOnlyAfterProofValidation() throws {
        let root = try loadRoot()
        let pairing = try dictionary(root["pairing"], context: "pairing")
        let inviter = try identity(root, name: "device_a")
        let offer = try signedOffer(pairing)
        let acceptance = try signedAcceptance(pairing)
        let proofs = AtlasVaultPairingProofs(
            inviter: try data(pairing["inviter_proof"], context: "inviter proof"),
            invitee: try data(pairing["invitee_proof"], context: "invitee proof")
        )
        let guardStore = TestReplayGuard()

        let verified = try AtlasVaultPairingFoundation.verify(
            localIdentity: inviter,
            offer: offer,
            acceptance: acceptance,
            proofs: proofs,
            currentTime: try string(pairing["verification_time"], context: "verification_time"),
            replayGuard: guardStore
        )
        XCTAssertEqual(verified.transcriptSHA256.hexString, try string(pairing["transcript_sha256"], context: "transcript"))
        XCTAssertEqual(guardStore.consumedCount, 1)

        XCTAssertThrowsError(
            try AtlasVaultPairingFoundation.verify(
                localIdentity: inviter,
                offer: offer,
                acceptance: acceptance,
                proofs: proofs,
                currentTime: try string(pairing["verification_time"], context: "verification_time"),
                replayGuard: guardStore
            )
        )

        let freshGuard = TestReplayGuard()
        XCTAssertThrowsError(
            try AtlasVaultPairingFoundation.verify(
                localIdentity: inviter,
                offer: offer,
                acceptance: acceptance,
                proofs: AtlasVaultPairingProofs(inviter: proofs.invitee, invitee: proofs.inviter),
                currentTime: try string(pairing["verification_time"], context: "verification_time"),
                replayGuard: freshGuard
            )
        )
        XCTAssertEqual(freshGuard.consumedCount, 0)
    }

    func testLifetimeExpiryFutureIssueAndAllZeroSecretFail() throws {
        let root = try loadRoot()
        let pairing = try dictionary(root["pairing"], context: "pairing")
        let inviter = try identity(root, name: "device_a")
        let invitee = try identity(root, name: "device_b")
        let cases = [
            ("2026-01-15T12:05:00Z", "2026-01-15T12:15:01Z", "2026-01-15T12:07:00Z"),
            ("2026-01-15T12:05:00Z", "2026-01-15T12:15:00Z", "2026-01-15T12:15:00Z"),
            ("2026-01-15T12:09:01Z", "2026-01-15T12:15:00Z", "2026-01-15T12:07:00Z"),
        ]

        for (issuedAt, expiresAt, currentTime) in cases {
            let offer = try AtlasVaultPairingFoundation.createOffer(
                inviter: inviter,
                offerID: try string(pairing["offer_id"], context: "offer_id"),
                nonce: try data(pairing["offer_nonce"], context: "offer_nonce"),
                issuedAt: issuedAt,
                expiresAt: expiresAt
            )
            XCTAssertThrowsError(
                try AtlasVaultPairingFoundation.createAcceptance(
                    invitee: invitee,
                    signedOffer: offer,
                    nonce: try data(pairing["acceptance_nonce"], context: "acceptance_nonce"),
                    acceptedAt: try string(pairing["accepted_at"], context: "accepted_at"),
                    currentTime: currentTime
                )
            )
        }

        XCTAssertThrowsError(
            try AtlasVaultPairingFoundation.deriveSessionKey(
                sharedSecret: Data(repeating: 0, count: 32),
                transcriptSHA256: try hexData(string(pairing["transcript_sha256"], context: "transcript"))
            )
        )
    }

    func testTamperAndOfferHashMismatchFailBeforeReplayConsumption() throws {
        let root = try loadRoot()
        let pairing = try dictionary(root["pairing"], context: "pairing")
        let inviter = try identity(root, name: "device_a")
        let proofs = AtlasVaultPairingProofs(
            inviter: try data(pairing["inviter_proof"], context: "inviter proof"),
            invitee: try data(pairing["invitee_proof"], context: "invitee proof")
        )

        var offerObject = try dictionary(
            JSONSerialization.jsonObject(with: try data(pairing["signed_offer_canonical_json_b64"], context: "offer")),
            context: "offer"
        )
        var signature = try data(offerObject["signature"], context: "signature")
        signature[0] ^= 1
        offerObject["signature"] = signature.base64EncodedString()

        var acceptanceObject = try dictionary(
            JSONSerialization.jsonObject(with: try data(pairing["signed_acceptance_canonical_json_b64"], context: "acceptance")),
            context: "acceptance"
        )
        var acceptancePayload = try dictionary(acceptanceObject["acceptance"], context: "acceptance payload")
        acceptancePayload["offer_sha256"] = String(repeating: "0", count: 64)
        acceptanceObject["acceptance"] = acceptancePayload

        let invalidPairs = [
            (offerObject, try jsonObject(pairing["signed_acceptance"])),
            (try jsonObject(pairing["signed_offer"]), acceptanceObject),
        ]
        for (offerJSON, acceptanceJSON) in invalidPairs {
            let guardStore = TestReplayGuard()
            XCTAssertThrowsError(
                try AtlasVaultPairingFoundation.verify(
                    localIdentity: inviter,
                    offer: try AtlasVaultSignedPairingOffer.decodeStrict(
                        JSONSerialization.data(withJSONObject: offerJSON, options: [.sortedKeys])
                    ),
                    acceptance: try AtlasVaultSignedPairingAcceptance.decodeStrict(
                        JSONSerialization.data(withJSONObject: acceptanceJSON, options: [.sortedKeys])
                    ),
                    proofs: proofs,
                    currentTime: try string(pairing["verification_time"], context: "verification_time"),
                    replayGuard: guardStore
                )
            )
            XCTAssertEqual(guardStore.consumedCount, 0)
        }
    }

    func testInvalidCaseManifestIsComplete() throws {
        let root = try loadRoot()
        let cases = try array(root["invalid_cases"], context: "invalid_cases")
        let actual = try Set(cases.map {
            try string(try dictionary($0, context: "case")["case_id"], context: "case_id")
        })
        XCTAssertEqual(actual, [
            "descriptor_signature_tamper", "descriptor_device_id_mismatch",
            "signing_key_substitution", "agreement_key_substitution",
            "offer_signature_tamper", "offer_nonce_tamper", "offer_id_tamper",
            "expired_offer", "excessive_lifetime", "future_issue_time",
            "acceptance_offer_hash_mismatch", "acceptance_signature_tamper",
            "same_inviter_invitee_identity", "all_zero_shared_secret",
            "transcript_tamper", "swapped_proof", "replay_consumption_duplicate",
        ])
    }

    private func identity(_ root: [String: Any], name: String) throws -> AtlasVaultDeviceIdentity {
        let vector = try dictionary(root[name], context: name)
        let descriptor = try dictionary(vector["descriptor"], context: "descriptor")
        return try AtlasVaultDeviceIdentity(
            signingPrivateSeed: try data(vector["signing_private_seed"], context: "signing seed"),
            agreementPrivateKey: try data(vector["agreement_private_key"], context: "agreement key"),
            createdAt: try string(descriptor["created_at"], context: "created_at"),
            keyEpoch: try int(descriptor["key_epoch"], context: "key_epoch")
        )
    }

    private func signedOffer(_ pairing: [String: Any]) throws -> AtlasVaultSignedPairingOffer {
        try AtlasVaultSignedPairingOffer.decodeStrict(
            try data(pairing["signed_offer_canonical_json_b64"], context: "offer")
        )
    }

    private func signedAcceptance(_ pairing: [String: Any]) throws -> AtlasVaultSignedPairingAcceptance {
        try AtlasVaultSignedPairingAcceptance.decodeStrict(
            try data(pairing["signed_acceptance_canonical_json_b64"], context: "acceptance")
        )
    }

    private func loadRoot() throws -> [String: Any] {
        let current = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            current.appendingPathComponent("../../contracts/sync/test_vectors/atlasvault_device_identity_pairing_vectors_v1.json"),
            current.appendingPathComponent("contracts/sync/test_vectors/atlasvault_device_identity_pairing_vectors_v1.json"),
            source.appendingPathComponent("../../../../contracts/sync/test_vectors/atlasvault_device_identity_pairing_vectors_v1.json"),
        ].map(\.standardizedFileURL)
        guard let url = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) else {
            throw NSError(domain: "AtlasVaultPairingFoundationTests", code: 1)
        }
        return try dictionary(JSONSerialization.jsonObject(with: Data(contentsOf: url)), context: "root")
    }

    private func dictionary(_ value: Any?, context: String) throws -> [String: Any] {
        guard let value = value as? [String: Any] else { throw NSError(domain: context, code: 1) }
        return value
    }

    private func jsonObject(_ value: Any?) throws -> [String: Any] {
        let data = try JSONSerialization.data(withJSONObject: value as Any, options: [.sortedKeys])
        return try dictionary(JSONSerialization.jsonObject(with: data), context: "json object")
    }

    private func array(_ value: Any?, context: String) throws -> [Any] {
        guard let value = value as? [Any] else { throw NSError(domain: context, code: 1) }
        return value
    }

    private func string(_ value: Any?, context: String) throws -> String {
        guard let value = value as? String else { throw NSError(domain: context, code: 1) }
        return value
    }

    private func int(_ value: Any?, context: String) throws -> Int {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else {
            throw NSError(domain: context, code: 1)
        }
        return number.intValue
    }

    private func data(_ value: Any?, context: String) throws -> Data {
        guard let text = value as? String, let result = Data(base64Encoded: text), result.base64EncodedString() == text else {
            throw NSError(domain: context, code: 1)
        }
        return result
    }

    private func hexData(_ value: String) throws -> Data {
        guard value.count % 2 == 0 else { throw NSError(domain: "hex", code: 1) }
        return try Data(stride(from: 0, to: value.count, by: 2).map { offset in
            let start = value.index(value.startIndex, offsetBy: offset)
            let end = value.index(start, offsetBy: 2)
            guard let byte = UInt8(value[start..<end], radix: 16) else { throw NSError(domain: "hex", code: 1) }
            return byte
        })
    }
}

private final class TestReplayGuard: AtlasVaultPairingReplayGuard, @unchecked Sendable {
    private var consumed = Set<String>()

    var consumedCount: Int { consumed.count }

    func consume(offerID: String, transcriptSHA256: Data, expiresAt: String) -> AtlasVaultPairingReplayOutcome {
        let key = "\(offerID):\(transcriptSHA256.base64EncodedString()):\(expiresAt)"
        return consumed.insert(key).inserted ? .accepted : .alreadyConsumed
    }
}

private extension Data {
    var hexString: String { map { String(format: "%02x", $0) }.joined() }
}
