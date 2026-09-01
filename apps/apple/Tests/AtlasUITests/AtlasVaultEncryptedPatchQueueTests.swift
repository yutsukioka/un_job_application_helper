import CryptoKit
import Darwin
import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultEncryptedPatchQueueTests: XCTestCase {
    func testSharedPatchContractIsStrictAndDeterministicallyOrdered() throws {
        let root = try loadRoot()
        let operations = try loadOperations(root)

        XCTAssertEqual(
            operations.sorted().map(\.operationID),
            root["expected_transport_order"] as? [String]
        )
        XCTAssertEqual(operations[0].idempotencyKey, operations[0].operationID)
        XCTAssertNil(operations[0].envelope.parentRevision)
        XCTAssertEqual(operations[1].envelope.parentRevision, operations[0].envelope.revision)

        var malformed = operations[0].jsonObject
        malformed["plaintext"] = "forbidden"
        XCTAssertThrowsError(try AtlasVaultEncryptedPatchOperation(jsonObject: malformed))
        var inconsistent = operations[1].jsonObject
        inconsistent["operation_type"] = "upsert"
        XCTAssertThrowsError(try AtlasVaultEncryptedPatchOperation(jsonObject: inconsistent))
    }

    func testOutboxIsEncryptedOrderedDurableAndAcknowledgementGated() throws {
        let directory = try temporaryDirectory("outbox")
        defer { try? FileManager.default.removeItem(at: directory) }
        let fileURL = directory.appendingPathComponent("outbox.queue")
        let operations = try loadOperations(loadRoot())
        let outbox = try AtlasVaultDurableEncryptedOutbox(
            fileURL: fileURL,
            encryptionKey: queueKey()
        )

        try outbox.enqueue(operations[1])
        try outbox.enqueue(operations[0])
        try outbox.enqueue(operations[0])

        XCTAssertEqual(try outbox.nextPending(), operations[0])
        XCTAssertEqual(try outbox.pendingOperations(), operations)
        XCTAssertEqual(try outbox.nextPending(), operations[0])
        let stored = try Data(contentsOf: fileURL)
        for forbidden in [
            operations[0].operationID,
            operations[0].envelope.objectID,
            operations[0].envelope.revision,
            operations[0].envelope.ciphertextBase64,
        ] {
            XCTAssertNil(stored.range(of: Data(forbidden.utf8)))
        }
        let outer = try XCTUnwrap(
            JSONSerialization.jsonObject(with: stored) as? [String: Any]
        )
        XCTAssertEqual(
            Set(outer.keys),
            Set(["format", "version", "nonce_b64", "ciphertext_b64"])
        )

        let restarted = try AtlasVaultDurableEncryptedOutbox(
            fileURL: fileURL,
            encryptionKey: queueKey()
        )
        XCTAssertEqual(try restarted.pendingOperations(), operations)
        try restarted.confirmRemoteAcceptance(operations[0].operationID)
        XCTAssertEqual(try restarted.pendingOperations(), [operations[1]])
        XCTAssertThrowsError(
            try restarted.confirmRemoteAcceptance(operations[0].operationID)
        )
        XCTAssertThrowsError(
            try AtlasVaultDurableEncryptedOutbox(
                fileURL: fileURL,
                encryptionKey: Data(repeating: 0x78, count: 32)
            ).pendingOperations()
        )
    }

    func testInboxCursorWaitsForDurableApplyAndDeduplicates() throws {
        let directory = try temporaryDirectory("inbox")
        defer { try? FileManager.default.removeItem(at: directory) }
        let fileURL = directory.appendingPathComponent("inbox.queue")
        let operations = try loadOperations(loadRoot())
        let inbox = try AtlasVaultDurableEncryptedInbox(
            fileURL: fileURL,
            encryptionKey: queueKey()
        )
        try inbox.stagePage(
            expectedCursor: nil,
            nextCursor: "cursor-after-two",
            operations: operations
        )

        XCTAssertNil(try inbox.cursor())
        XCTAssertEqual(try inbox.pendingOperations(), operations)
        var applied: [String] = []
        XCTAssertEqual(
            try inbox.applyNext { applied.append($0.operationID) },
            operations[0]
        )
        XCTAssertNil(try inbox.cursor())
        XCTAssertEqual(
            try AtlasVaultDurableEncryptedInbox(
                fileURL: fileURL,
                encryptionKey: queueKey()
            ).pendingOperations(),
            [operations[1]]
        )
        XCTAssertEqual(
            try inbox.applyNext { applied.append($0.operationID) },
            operations[1]
        )
        XCTAssertEqual(try inbox.cursor(), "cursor-after-two")
        XCTAssertEqual(applied, operations.map(\.operationID))

        try inbox.stagePage(
            expectedCursor: "cursor-after-two",
            nextCursor: "cursor-after-replay",
            operations: operations
        )
        XCTAssertEqual(try inbox.pendingOperations(), [])
        XCTAssertEqual(try inbox.cursor(), "cursor-after-replay")
        XCTAssertNil(try inbox.applyNext { applied.append($0.operationID) })
        XCTAssertEqual(applied, operations.map(\.operationID))

        var changed = operations[0].jsonObject
        changed["lamport"] = 99
        XCTAssertThrowsError(
            try inbox.stagePage(
                expectedCursor: "cursor-after-replay",
                nextCursor: "cursor-invalid",
                operations: [try AtlasVaultEncryptedPatchOperation(jsonObject: changed)]
            )
        )
    }

    func testInboxRejectsOrderingAndParentRegressionsBeforePersistence() throws {
        let directory = try temporaryDirectory("invalid")
        defer { try? FileManager.default.removeItem(at: directory) }
        let fileURL = directory.appendingPathComponent("inbox.queue")
        let operations = try loadOperations(loadRoot())
        let inbox = try AtlasVaultDurableEncryptedInbox(
            fileURL: fileURL,
            encryptionKey: queueKey()
        )

        XCTAssertThrowsError(
            try inbox.stagePage(
                expectedCursor: nil,
                nextCursor: "cursor-invalid",
                operations: Array(operations.reversed())
            )
        )
        XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path))

        var invalid = operations[1].jsonObject
        var envelope = try XCTUnwrap(invalid["envelope"] as? [String: Any])
        envelope["parent_revision"] = "wrong-parent"
        invalid["envelope"] = envelope
        XCTAssertThrowsError(
            try inbox.stagePage(
                expectedCursor: nil,
                nextCursor: "cursor-invalid",
                operations: [
                    operations[0],
                    try AtlasVaultEncryptedPatchOperation(jsonObject: invalid),
                ]
            )
        )
        XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path))
    }

    func testOutboxAndInboxSurviveProcessKillAndRestart() throws {
        let directory = try temporaryDirectory("process")
        defer { try? FileManager.default.removeItem(at: directory) }
        let outboxURL = directory.appendingPathComponent("outbox.queue")
        let inboxURL = directory.appendingPathComponent("inbox.queue")
        let readyURL = directory.appendingPathComponent("ready")
        let helperURL = directory.appendingPathComponent("helper.swift")
        let binaryURL = directory.appendingPathComponent("helper")
        try processHelperSource.write(to: helperURL, atomically: true, encoding: .utf8)
        try compileHelper(sourceURL: helperURL, binaryURL: binaryURL)

        let process = Process()
        process.executableURL = binaryURL
        process.arguments = [
            outboxURL.path,
            inboxURL.path,
            readyURL.path,
            try vectorURL().path,
        ]
        try process.run()
        let deadline = Date().addingTimeInterval(20)
        while !FileManager.default.fileExists(atPath: readyURL.path), Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: readyURL.path))
        XCTAssertEqual(Darwin.kill(process.processIdentifier, SIGKILL), 0)
        process.waitUntilExit()
        XCTAssertNotEqual(process.terminationStatus, 0)

        let operations = try loadOperations(loadRoot())
        XCTAssertEqual(
            try AtlasVaultDurableEncryptedOutbox(
                fileURL: outboxURL,
                encryptionKey: queueKey()
            ).pendingOperations(),
            operations
        )
        let inbox = try AtlasVaultDurableEncryptedInbox(
            fileURL: inboxURL,
            encryptionKey: queueKey()
        )
        XCTAssertNil(try inbox.cursor())
        XCTAssertEqual(try inbox.pendingOperations(), operations)
    }

    private func loadRoot() throws -> [String: Any] {
        let data = try Data(contentsOf: vectorURL())
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(root["format"] as? String, "atlasvault-encrypted-patch-queue-vectors")
        XCTAssertEqual(root["version"] as? Int, 1)
        return root
    }

    private func loadOperations(_ root: [String: Any]) throws -> [AtlasVaultEncryptedPatchOperation] {
        try XCTUnwrap(root["operations"] as? [[String: Any]]).map {
            try AtlasVaultEncryptedPatchOperation(jsonObject: $0)
        }
    }

    private func vectorURL() throws -> URL {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("../../contracts/sync/test_vectors/atlasvault_encrypted_patch_queue_vectors_v1.json")
                .standardizedFileURL,
            source.appendingPathComponent("../../../../contracts/sync/test_vectors/atlasvault_encrypted_patch_queue_vectors_v1.json")
                .standardizedFileURL,
        ]
        return try XCTUnwrap(candidates.first { FileManager.default.fileExists(atPath: $0.path) })
    }

    private func temporaryDirectory(_ suffix: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("atlasvault-c17-\(suffix)-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func queueKey() -> Data {
        Data(SHA256.hash(data: Data("atlasvault-c17-synthetic-queue-key".utf8)))
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
        let error = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
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
                guard arguments.count == 5 else { exit(64) }
                let root = try JSONSerialization.jsonObject(
                    with: Data(contentsOf: URL(fileURLWithPath: arguments[4]))) as! [String: Any]
                let operations = try (root["operations"] as! [[String: Any]]).map {
                    try AtlasVaultEncryptedPatchOperation(jsonObject: $0)
                }
                let key = Data(SHA256.hash(
                    data: Data("atlasvault-c17-synthetic-queue-key".utf8)))
                let outbox = try AtlasVaultDurableEncryptedOutbox(
                    fileURL: URL(fileURLWithPath: arguments[1]), encryptionKey: key)
                try outbox.enqueue(operations[1])
                try outbox.enqueue(operations[0])
                let inbox = try AtlasVaultDurableEncryptedInbox(
                    fileURL: URL(fileURLWithPath: arguments[2]), encryptionKey: key)
                try inbox.stagePage(
                    expectedCursor: nil,
                    nextCursor: "cursor-after-two",
                    operations: operations)
                try Data("ready\n".utf8).write(to: URL(fileURLWithPath: arguments[3]))
                while true { Thread.sleep(forTimeInterval: 60) }
            }
        }
        """#
    }
}
