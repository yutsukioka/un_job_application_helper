import CryptoKit
import Darwin
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultEncryptedPatchConvergenceTests: XCTestCase {
    func testConcurrentConflictsAreCommutativeAndVectorFixed() throws {
        let directory = try temporaryDirectory("order")
        defer { try? FileManager.default.removeItem(at: directory) }
        let operations = try loadOperations()
        let root = try loadJSON(vectorURL())
        let left = try replica(directory.appendingPathComponent("left"))
        let right = try replica(directory.appendingPathComponent("right"))

        for name in ["base", "edit_a", "edit_b"] {
            _ = try left.ingestRemote(try XCTUnwrap(operations[name]))
        }
        for name in ["base", "edit_b", "edit_a"] {
            _ = try right.ingestRemote(try XCTUnwrap(operations[name]))
        }

        XCTAssertEqual(try left.currentRecords(), try right.currentRecords())
        XCTAssertEqual(
            try XCTUnwrap(left.currentRecords().first).revision,
            root["expected_concurrent_winner_revision"] as? String
        )
        XCTAssertEqual(try left.acceptedOperationCount(), 3)
        XCTAssertEqual(try right.acceptedOperationCount(), 3)
    }

    func testDeleteWinsOverStalePatchAndAuthenticatedSnapshot() throws {
        let directory = try temporaryDirectory("delete")
        defer { try? FileManager.default.removeItem(at: directory) }
        let operations = try loadOperations()
        let root = try loadJSON(vectorURL())
        let snapshotSource = try AtlasVaultDurableEncryptedPatchCollection(
            fileURL: directory.appendingPathComponent("snapshot-source"),
            encryptionKey: queueKey(),
            authenticationKey: authenticationKey(),
            collectionID: "collection_a"
        )
        try snapshotSource.append(try XCTUnwrap(operations["base"]))
        let oldSnapshot = try snapshotSource.compact()
        let left = try replica(directory.appendingPathComponent("left"))
        let right = try replica(directory.appendingPathComponent("right"))

        for name in ["base", "edit_a", "delete", "stale_edit"] {
            _ = try left.ingestRemote(try XCTUnwrap(operations[name]))
        }
        _ = try left.mergeSnapshot(oldSnapshot)
        _ = try right.mergeSnapshot(oldSnapshot)
        for name in ["stale_edit", "delete", "edit_a", "base"] {
            _ = try right.ingestRemote(try XCTUnwrap(operations[name]))
        }

        XCTAssertEqual(try left.currentRecords(), try right.currentRecords())
        XCTAssertTrue(try XCTUnwrap(left.currentRecords().first).tombstone)
        XCTAssertEqual(
            try XCTUnwrap(left.currentRecords().first).revision,
            root["expected_delete_winner_revision"] as? String
        )
    }

    func testOfflineQueueSurvivesKillReconnectsAndRetriesExactlyOnce() throws {
        let directory = try temporaryDirectory("offline")
        defer { try? FileManager.default.removeItem(at: directory) }
        let stateURL = directory.appendingPathComponent("offline")
        let readyURL = directory.appendingPathComponent("ready")
        let helperURL = directory.appendingPathComponent("helper.swift")
        let binaryURL = directory.appendingPathComponent("helper")
        let operations = try loadOperations()
        try helperSource.write(to: helperURL, atomically: true, encoding: .utf8)
        try compileHelper(sourceURL: helperURL, binaryURL: binaryURL)

        let process = Process()
        process.executableURL = binaryURL
        process.arguments = [stateURL.path, readyURL.path, try vectorURL().path]
        try process.run()
        let deadline = Date().addingTimeInterval(20)
        while !FileManager.default.fileExists(atPath: readyURL.path), Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: readyURL.path))
        XCTAssertEqual(Darwin.kill(process.processIdentifier, SIGKILL), 0)
        process.waitUntilExit()
        XCTAssertNotEqual(process.terminationStatus, 0)

        let restarted = try replica(stateURL)
        XCTAssertEqual(
            try restarted.pendingOperations().map(\.operationID),
            [try XCTUnwrap(operations["base"]).operationID,
             try XCTUnwrap(operations["edit_a"]).operationID]
        )
        let remote = try replica(directory.appendingPathComponent("remote"))
        XCTAssertEqual(try restarted.synchronize(to: remote), 2)
        XCTAssertEqual(try restarted.pendingOperations(), [])
        XCTAssertEqual(try remote.acceptedOperationCount(), 2)
        XCTAssertFalse(try remote.ingestRemote(try XCTUnwrap(operations["edit_a"])))
        XCTAssertEqual(try remote.acceptedOperationCount(), 2)

        let stored = String(decoding: try Data(contentsOf: stateURL), as: UTF8.self)
        XCTAssertFalse(stored.contains(try XCTUnwrap(operations["edit_a"]).operationID))
        XCTAssertFalse(stored.contains(try XCTUnwrap(operations["edit_a"]).envelope.revision))
    }

    func testDivergentOfflineEditsConvergeAndAliasesFailClosed() throws {
        let directory = try temporaryDirectory("reconnect")
        defer { try? FileManager.default.removeItem(at: directory) }
        let operations = try loadOperations()
        let left = try replica(directory.appendingPathComponent("left"))
        let right = try replica(directory.appendingPathComponent("right"))
        _ = try left.queueLocal(try XCTUnwrap(operations["base"]))
        _ = try right.ingestRemote(try XCTUnwrap(operations["base"]))
        _ = try left.queueLocal(try XCTUnwrap(operations["edit_a"]))
        _ = try right.queueLocal(try XCTUnwrap(operations["edit_b"]))

        XCTAssertEqual(try left.synchronize(to: right), 2)
        XCTAssertEqual(try right.synchronize(to: left), 1)
        XCTAssertEqual(try left.currentRecords(), try right.currentRecords())
        XCTAssertEqual(try XCTUnwrap(left.currentRecords().first).revision, "rev-edit-b")
        XCTAssertEqual(try left.acceptedOperationCount(), 3)
        XCTAssertEqual(try right.acceptedOperationCount(), 3)

        var changed = try XCTUnwrap(operations["edit_a"]).jsonObject
        changed["lamport"] = 99
        XCTAssertThrowsError(
            try left.ingestRemote(
                AtlasVaultEncryptedPatchOperation(jsonObject: changed)
            )
        )
    }

    private func loadOperations() throws -> [String: AtlasVaultEncryptedPatchOperation] {
        let root = try loadJSON(vectorURL())
        let raw = try XCTUnwrap(root["operations"] as? [String: [String: Any]])
        return try raw.mapValues(AtlasVaultEncryptedPatchOperation.init(jsonObject:))
    }

    private func replica(_ fileURL: URL) throws -> AtlasVaultDurableEncryptedConvergentReplica {
        try AtlasVaultDurableEncryptedConvergentReplica(
            fileURL: fileURL,
            encryptionKey: queueKey(),
            authenticationKey: authenticationKey(),
            collectionID: "collection_a"
        )
    }

    private func queueKey() -> Data {
        Data(SHA256.hash(data: Data("atlasvault-c19-synthetic-replica-queue".utf8)))
    }

    private func authenticationKey() -> Data {
        Data(SHA256.hash(
            data: Data("atlasvault-c19-synthetic-snapshot-authentication".utf8)
        ))
    }

    private func vectorURL() throws -> URL {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent(
                    "../../contracts/sync/test_vectors/atlasvault_encrypted_patch_convergence_vectors_v1.json"
                ).standardizedFileURL,
            source.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/atlasvault_encrypted_patch_convergence_vectors_v1.json"
            ).standardizedFileURL,
        ]
        return try XCTUnwrap(candidates.first {
            FileManager.default.fileExists(atPath: $0.path)
        })
    }

    private func loadJSON(_ url: URL) throws -> [String: Any] {
        try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
        )
    }

    private func temporaryDirectory(_ suffix: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("atlasvault-c19-\(suffix)-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
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

    private var helperSource: String {
        #"""
        import CryptoKit
        import Foundation

        @main
        struct Helper {
            static func main() throws {
                guard CommandLine.arguments.count == 4 else { exit(64) }
                let queueKey = Data(SHA256.hash(
                    data: Data("atlasvault-c19-synthetic-replica-queue".utf8)))
                let authenticationKey = Data(SHA256.hash(
                    data: Data("atlasvault-c19-synthetic-snapshot-authentication".utf8)))
                let root = try JSONSerialization.jsonObject(
                    with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[3])))
                    as! [String: Any]
                let raw = root["operations"] as! [String: [String: Any]]
                let replica = try AtlasVaultDurableEncryptedConvergentReplica(
                    fileURL: URL(fileURLWithPath: CommandLine.arguments[1]),
                    encryptionKey: queueKey,
                    authenticationKey: authenticationKey,
                    collectionID: "collection_a")
                _ = try replica.queueLocal(
                    AtlasVaultEncryptedPatchOperation(jsonObject: raw["base"]!))
                _ = try replica.queueLocal(
                    AtlasVaultEncryptedPatchOperation(jsonObject: raw["edit_a"]!))
                try Data("ready\n".utf8).write(
                    to: URL(fileURLWithPath: CommandLine.arguments[2]))
                while true { Thread.sleep(forTimeInterval: 60) }
            }
        }
        """#
    }
}
