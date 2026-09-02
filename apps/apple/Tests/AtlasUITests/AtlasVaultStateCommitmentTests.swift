import CryptoKit
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultStateCommitmentTests: XCTestCase {
    private let key = Data((0..<32).map(UInt8.init))

    private func vectors() throws -> [String: Any] {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { root.deleteLastPathComponent() }
        let path = root.appendingPathComponent("contracts/sync/test_vectors/atlasvault_state_commitment_vectors_v1.json")
        return try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: path)) as? [String: Any])
    }

    private func tracker(_ url: URL) throws -> AtlasVaultRollbackTracker {
        try AtlasVaultRollbackTracker(fileURL: url, encryptionKey: key, collectionID: "collection_c21", trustedSigner: XCTUnwrap(Data(base64Encoded: vectors()["signing_public_b64"] as! String)))
    }

    private struct HostileServer {
        var commitment: [String: Any]
        var body: Data
        init(_ state: [String: Any], omit: Bool = false) {
            commitment = state["commitment"] as! [String: Any]
            body = Data(base64Encoded: state["opaque_b64"] as! String)!
            if omit { body.removeLast() }
        }
        func serve(_ client: AtlasVaultRollbackTracker) throws -> Bool {
            try client.accept(jsonObject: commitment, opaqueState: body)
        }
    }

    func testSharedRootsAndSignatures() throws {
        let states = try vectors()["states"] as! [[String: Any]]
        let signer = try Curve25519.Signing.PrivateKey(rawRepresentation: key)
        for state in states {
            let server = HostileServer(state)
            let c = server.commitment
            let actual = try AtlasVaultSignedStateCommitment.sign(server.body, collectionID: c["collection_id"] as! String, sequence: c["sequence"] as! Int64, previousRoot: c["previous_root"] as! String, signingKey: signer)
            XCTAssertEqual(NSDictionary(dictionary: actual.jsonObject), NSDictionary(dictionary: c))
        }
    }

    func testHostileServerAttacksAfterDurableObservation() throws {
        let states = try vectors()["states"] as! [[String: Any]]
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        for attack in ["replay", "old_snapshot", "omission", "gap", "non_chaining", "same_sequence"] {
            let path = directory.appendingPathComponent(attack)
            var client = try tracker(path)
            try client.initialize()
            XCTAssertTrue(try HostileServer(states[0]).serve(client))
            if attack != "gap" { XCTAssertTrue(try HostileServer(states[1]).serve(client)) }
            let before = try Data(contentsOf: path)
            client = try tracker(path)
            var server = HostileServer(states[attack == "replay" || attack == "old_snapshot" ? 0 : 2], omit: attack == "omission")
            if attack == "non_chaining" || attack == "same_sequence" {
                server.commitment = try AtlasVaultSignedStateCommitment.sign(server.body, collectionID: "collection_c21", sequence: attack == "non_chaining" ? 3 : 2, previousRoot: String(repeating: "f", count: 64), signingKey: Curve25519.Signing.PrivateKey(rawRepresentation: key)).jsonObject
            }
            XCTAssertThrowsError(try server.serve(client), attack)
            XCTAssertEqual(try Data(contentsOf: path), before, attack)
            XCTAssertEqual(try client.checkpoint()["sequence"] as? Int64, attack == "gap" ? 1 : 2)
        }
    }

    func testDuplicateEncryptedAnchorAndMissingCorruptState() throws {
        let states = try vectors()["states"] as! [[String: Any]]
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("anchor")
        let client = try tracker(path)
        XCTAssertThrowsError(try HostileServer(states[0]).serve(client))
        try client.initialize()
        for state in states { XCTAssertTrue(try HostileServer(state).serve(client)) }
        let before = try Data(contentsOf: path)
        XCTAssertFalse(try HostileServer(states[2]).serve(tracker(path)))
        XCTAssertEqual(try Data(contentsOf: path), before)
        XCTAssertFalse(String(decoding: before, as: UTF8.self).contains("collection_c21"))
        XCTAssertFalse(String(decoding: before, as: UTF8.self).contains(HostileServer(states[2]).commitment["root"] as! String))
        XCTAssertThrowsError(try client.initialize())
        try Data("corrupt".utf8).write(to: path)
        XCTAssertThrowsError(try client.checkpoint())
        try FileManager.default.removeItem(at: path)
        XCTAssertThrowsError(try client.checkpoint())
    }

    func testMalformedWrongSignatureScopeAndKey() throws {
        let states = try vectors()["states"] as! [[String: Any]]
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("anchor")
        let client = try tracker(path)
        try client.initialize()
        let mutations: [[String: Any]] = [
            ["sequence": true], ["sequence": 0], ["sequence": 1.0], ["sequence": Int64(9007199254740992)],
            ["previous_root": String(repeating: "F", count: 64)], ["signature_b64": Data(repeating: 0, count: 64).base64EncodedString()],
            ["collection_id": "other"], ["plaintext": "forbidden"],
        ]
        for mutation in mutations {
            var server = HostileServer(states[0])
            server.commitment.merge(mutation) { _, new in new }
            XCTAssertThrowsError(try server.serve(client))
        }
        let wrongKey = try AtlasVaultRollbackTracker(fileURL: path, encryptionKey: Data(repeating: 0, count: 32), collectionID: "collection_c21", trustedSigner: Data(base64Encoded: vectors()["signing_public_b64"] as! String)!)
        XCTAssertThrowsError(try wrongKey.checkpoint())
        XCTAssertEqual(try client.checkpoint()["sequence"] as? Int64, 0)
    }
}
