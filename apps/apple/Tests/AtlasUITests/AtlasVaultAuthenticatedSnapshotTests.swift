import CryptoKit
import Darwin
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultAuthenticatedSnapshotTests: XCTestCase {
    func testSnapshotVectorAuthenticatesAndRejectsTamperOrTruncation() throws {
        let root = try loadJSON(snapshotVectorURL())
        let raw = try XCTUnwrap(root["snapshot"] as? [String: Any])
        let snapshot = try AtlasVaultAuthenticatedCollectionSnapshot(
            jsonObject: raw,
            authenticationKey: authenticationKey()
        )

        XCTAssertTrue(NSDictionary(dictionary: snapshot.jsonObject).isEqual(to: raw))
        XCTAssertEqual(snapshot.collectionRevision, 2)
        XCTAssertTrue(try XCTUnwrap(snapshot.records.first).tombstone)
        let authentication = try XCTUnwrap(raw["authentication"] as? [String: Any])
        XCTAssertEqual(snapshot.authenticationTagBase64, authentication["tag_b64"] as? String)
        XCTAssertEqual(
            snapshot.canonicalPayloadSHA256,
            root["expected_canonical_payload_sha256"] as? String
        )

        var tampered = raw
        var payload = try XCTUnwrap(tampered["payload"] as? [String: Any])
        var records = try XCTUnwrap(payload["records"] as? [[String: Any]])
        records[0]["revision"] = "rev-tampered"
        payload["records"] = records
        tampered["payload"] = payload
        XCTAssertThrowsError(
            try AtlasVaultAuthenticatedCollectionSnapshot(
                jsonObject: tampered,
                authenticationKey: authenticationKey()
            )
        )

        let encoded = try JSONSerialization.data(withJSONObject: raw, options: [.sortedKeys])
        XCTAssertThrowsError(
            try AtlasVaultAuthenticatedCollectionSnapshot(
                jsonData: Data(encoded.dropLast(9)),
                authenticationKey: authenticationKey()
            )
        )
    }

    func testCanonicalSnapshotReceiptsDoNotEscapeSlashes() throws {
        let directory = try temporaryDirectory("canonical-slash")
        defer { try? FileManager.default.removeItem(at: directory) }
        var raw = try XCTUnwrap(loadOperations().first).jsonObject
        var envelope = try XCTUnwrap(raw["envelope"] as? [String: Any])
        envelope["aad_b64"] = "/w=="
        raw["envelope"] = envelope
        let operation = try AtlasVaultEncryptedPatchOperation(jsonObject: raw)
        let value = try collection(directory.appendingPathComponent("slash.collection"))

        try value.append(operation)
        let snapshot = try value.compact()
        let payload = try XCTUnwrap(snapshot.jsonObject["payload"] as? [String: Any])
        let receipts = try XCTUnwrap(payload["applied_fingerprints"] as? [String: String])
        let canonical = try JSONSerialization.data(
            withJSONObject: operation.jsonObject,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        let expected = SHA256.hash(data: canonical)
            .map { String(format: "%02x", $0) }
            .joined()

        XCTAssertEqual(receipts[operation.operationID], expected)
    }

    func testCompactionPreservesReplayBytesTombstonesAndReceipts() throws {
        let directory = try temporaryDirectory("preservation")
        defer { try? FileManager.default.removeItem(at: directory) }
        let operations = try loadOperations()
        let third = try thirdOperation(operations[0])
        let full = try collection(directory.appendingPathComponent("full.collection"))
        let compacted = try collection(directory.appendingPathComponent("compacted.collection"))

        for operation in operations + [third] { try full.append(operation) }
        for operation in operations { try compacted.append(operation) }

        let snapshot = try compacted.compact()
        XCTAssertEqual(snapshot.collectionRevision, 2)
        XCTAssertEqual(snapshot.records, [operations[1].envelope])
        XCTAssertTrue(try XCTUnwrap(snapshot.records.first).tombstone)
        XCTAssertEqual(try compacted.tailOperations(), [])

        try compacted.append(operations[0])
        XCTAssertEqual(try compacted.committedOperationCount(), 2)
        try compacted.append(third)
        XCTAssertEqual(try compacted.currentRecords(), try full.currentRecords())
        XCTAssertEqual(
            try compacted.currentRecords().map(canonicalEnvelope),
            try full.currentRecords().map(canonicalEnvelope)
        )
        XCTAssertEqual(try compacted.committedOperationCount(), 3)
        XCTAssertEqual(try full.committedOperationCount(), 3)

        var changed = operations[0].jsonObject
        changed["lamport"] = 99
        XCTAssertThrowsError(
            try compacted.append(
                AtlasVaultEncryptedPatchOperation(jsonObject: changed)
            )
        )

        let stored = try Data(contentsOf: directory.appendingPathComponent("compacted.collection"))
        let encoded = String(decoding: stored, as: UTF8.self)
        XCTAssertFalse(encoded.contains(operations[0].operationID))
        XCTAssertFalse(encoded.contains(operations[1].envelope.revision))
        XCTAssertFalse(encoded.contains(third.envelope.objectID))
    }

    func testKillMidCompactionRestartsAtValidPreOrPostState() throws {
        let directory = try temporaryDirectory("crash")
        defer { try? FileManager.default.removeItem(at: directory) }
        let fileURL = directory.appendingPathComponent("crash.collection")
        let readyURL = directory.appendingPathComponent("ready")
        let helperURL = directory.appendingPathComponent("helper.swift")
        let binaryURL = directory.appendingPathComponent("helper")
        let before = try collection(fileURL)
        let operations = try loadOperations()
        for operation in operations { try before.append(operation) }
        let expected = try before.currentRecords()
        try processHelperSource.write(to: helperURL, atomically: true, encoding: .utf8)
        try compileHelper(sourceURL: helperURL, binaryURL: binaryURL)

        let process = Process()
        process.executableURL = binaryURL
        process.arguments = [fileURL.path, readyURL.path]
        try process.run()
        let deadline = Date().addingTimeInterval(20)
        while !FileManager.default.fileExists(atPath: readyURL.path), Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: readyURL.path))
        XCTAssertEqual(Darwin.kill(process.processIdentifier, SIGKILL), 0)
        process.waitUntilExit()
        XCTAssertNotEqual(process.terminationStatus, 0)

        let restarted = try collection(fileURL)
        XCTAssertEqual(try restarted.currentRecords(), expected)
        XCTAssertEqual(try restarted.committedOperationCount(), 2)
        let snapshot = try restarted.snapshot()
        let tail = try restarted.tailOperations()
        XCTAssertTrue(
            (snapshot == nil && tail.count == 2) ||
                (snapshot != nil && tail.isEmpty)
        )
        let finalSnapshot = try restarted.compact()
        XCTAssertEqual(finalSnapshot.records, expected)
        XCTAssertEqual(try restarted.tailOperations(), [])
        XCTAssertEqual(try collection(fileURL).currentRecords(), expected)
    }

    func testSnapshotAndJournalFailClosedWithWrongKey() throws {
        let directory = try temporaryDirectory("keys")
        defer { try? FileManager.default.removeItem(at: directory) }
        let fileURL = directory.appendingPathComponent("keys.collection")
        let value = try collection(fileURL)
        try value.append(try loadOperations()[0])
        let raw = try value.compact().jsonObject

        XCTAssertThrowsError(
            try AtlasVaultAuthenticatedCollectionSnapshot(
                jsonObject: raw,
                authenticationKey: Data(repeating: 0x78, count: 32)
            )
        )
        XCTAssertThrowsError(
            try AtlasVaultDurableEncryptedPatchCollection(
                fileURL: fileURL,
                encryptionKey: Data(repeating: 0x79, count: 32),
                authenticationKey: authenticationKey(),
                collectionID: "collection_a"
            ).currentRecords()
        )
    }

    private func loadOperations() throws -> [AtlasVaultEncryptedPatchOperation] {
        let root = try loadJSON(patchVectorURL())
        return try XCTUnwrap(root["operations"] as? [[String: Any]]).map {
            try AtlasVaultEncryptedPatchOperation(jsonObject: $0)
        }
    }

    private func thirdOperation(
        _ first: AtlasVaultEncryptedPatchOperation
    ) throws -> AtlasVaultEncryptedPatchOperation {
        var value = first.jsonObject
        value["operation_id"] = "00000000-0000-4000-8000-000000000003"
        value["author_sequence"] = 3
        value["lamport"] = 9
        var envelope = try XCTUnwrap(value["envelope"] as? [String: Any])
        envelope["object_id"] = "object_b"
        envelope["revision"] = "rev-b-001"
        envelope["parent_revision"] = NSNull()
        value["envelope"] = envelope
        return try AtlasVaultEncryptedPatchOperation(jsonObject: value)
    }

    private func collection(
        _ fileURL: URL
    ) throws -> AtlasVaultDurableEncryptedPatchCollection {
        try AtlasVaultDurableEncryptedPatchCollection(
            fileURL: fileURL,
            encryptionKey: queueKey(),
            authenticationKey: authenticationKey(),
            collectionID: "collection_a"
        )
    }

    private func canonicalEnvelope(
        _ envelope: AtlasVaultOpaqueCiphertextEnvelope
    ) throws -> Data {
        try JSONSerialization.data(withJSONObject: envelope.jsonObject, options: [.sortedKeys])
    }

    private func loadJSON(_ url: URL) throws -> [String: Any] {
        try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
        )
    }

    private func patchVectorURL() throws -> URL {
        try vectorURL("atlasvault_encrypted_patch_queue_vectors_v1.json")
    }

    private func snapshotVectorURL() throws -> URL {
        try vectorURL("atlasvault_authenticated_snapshot_vectors_v1.json")
    }

    private func vectorURL(_ name: String) throws -> URL {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("../../contracts/sync/test_vectors/\(name)")
                .standardizedFileURL,
            source.appendingPathComponent("../../../../contracts/sync/test_vectors/\(name)")
                .standardizedFileURL,
        ]
        return try XCTUnwrap(candidates.first {
            FileManager.default.fileExists(atPath: $0.path)
        })
    }

    private func temporaryDirectory(_ suffix: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("atlasvault-c18-\(suffix)-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func queueKey() -> Data {
        Data(SHA256.hash(data: Data("atlasvault-c18-synthetic-collection-queue".utf8)))
    }

    private func authenticationKey() -> Data {
        Data(SHA256.hash(
            data: Data("atlasvault-c18-synthetic-snapshot-authentication".utf8)
        ))
    }

    private func compileHelper(sourceURL: URL, binaryURL: URL) throws {
        let queueSource = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("../../Sources/AtlasUI/AtlasVaultSyncQueue.swift")
            .standardizedFileURL
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/xcrun")
        process.arguments = ["swiftc", queueSource.path, sourceURL.path, "-o", binaryURL.path]
        let output = Pipe()
        process.standardError = output
        try process.run()
        process.waitUntilExit()
        let error = String(
            decoding: output.fileHandleForReading.readDataToEndOfFile(),
            as: UTF8.self
        )
        XCTAssertEqual(process.terminationStatus, 0, error)
    }

    private var processHelperSource: String {
        #"""
        import CryptoKit
        import Foundation

        @main
        struct Helper {
            static func main() throws {
                let arguments = CommandLine.arguments
                guard arguments.count == 3 else { exit(64) }
                let queueKey = Data(SHA256.hash(
                    data: Data("atlasvault-c18-synthetic-collection-queue".utf8)))
                let authenticationKey = Data(SHA256.hash(
                    data: Data("atlasvault-c18-synthetic-snapshot-authentication".utf8)))
                let collection = try AtlasVaultDurableEncryptedPatchCollection(
                    fileURL: URL(fileURLWithPath: arguments[1]),
                    encryptionKey: queueKey,
                    authenticationKey: authenticationKey,
                    collectionID: "collection_a")
                _ = try collection.compact {
                    try Data("ready\n".utf8).write(
                        to: URL(fileURLWithPath: arguments[2]))
                    while true { Thread.sleep(forTimeInterval: 60) }
                }
            }
        }
        """#
    }
}
