import CryptoKit
import Foundation

private let collectionID = "collection_c20"

private func key(_ label: String) -> Data {
    Data(SHA256.hash(data: Data("atlasvault-c20-synthetic-\(label)".utf8)))
}

private func load(_ path: String) throws -> [String: Any] {
    guard let value = try JSONSerialization.jsonObject(
        with: Data(contentsOf: URL(fileURLWithPath: path))
    ) as? [String: Any] else {
        throw ClientError.invalid
    }
    return value
}

private func write(_ path: String, _ value: [String: Any]) throws {
    var data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    data.append(0x0a)
    try data.write(to: URL(fileURLWithPath: path), options: .atomic)
}

private enum ClientError: Error {
    case invalid
}

private struct Parts {
    let replica: AtlasVaultDurableEncryptedConvergentReplica
    let outbox: AtlasVaultDurableEncryptedOutbox
    let inbox: AtlasVaultDurableEncryptedInbox
}

private func parts(_ directory: URL) throws -> Parts {
    try FileManager.default.createDirectory(
        at: directory,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    return try Parts(
        replica: AtlasVaultDurableEncryptedConvergentReplica(
            fileURL: directory.appendingPathComponent("replica.state"),
            encryptionKey: key("replica"),
            authenticationKey: key("snapshot-authentication"),
            collectionID: collectionID
        ),
        outbox: AtlasVaultDurableEncryptedOutbox(
            fileURL: directory.appendingPathComponent("outbox.state"),
            encryptionKey: key("outbox")
        ),
        inbox: AtlasVaultDurableEncryptedInbox(
            fileURL: directory.appendingPathComponent("inbox.state"),
            encryptionKey: key("inbox")
        )
    )
}

private func operations(
    _ path: String
) throws -> [String: AtlasVaultEncryptedPatchOperation] {
    guard let raw = try load(path)["operations"] as? [String: [String: Any]] else {
        throw ClientError.invalid
    }
    return try raw.mapValues(AtlasVaultEncryptedPatchOperation.init(jsonObject:))
}

private func decryptTransport(
    _ operation: AtlasVaultEncryptedPatchOperation
) throws -> AtlasVaultEncryptedPatchOperation {
    guard let nonce = Data(base64Encoded: operation.envelope.nonceBase64),
          let combined = Data(base64Encoded: operation.envelope.ciphertextBase64),
          let aad = Data(base64Encoded: operation.envelope.aadBase64),
          combined.count > 16
    else {
        throw ClientError.invalid
    }
    let sealed = try AES.GCM.SealedBox(
        nonce: AES.GCM.Nonce(data: nonce),
        ciphertext: combined.dropLast(16),
        tag: combined.suffix(16)
    )
    let clear = try AES.GCM.open(sealed, using: SymmetricKey(data: key("transport")), authenticating: aad)
    guard let json = try JSONSerialization.jsonObject(with: clear) as? [String: Any] else {
        throw ClientError.invalid
    }
    return try AtlasVaultEncryptedPatchOperation(jsonObject: json)
}

private func result(_ parts: Parts) throws -> [String: Any] {
    let records = try parts.replica.currentRecords().map(\.jsonObject)
    let canonical = try JSONSerialization.data(
        withJSONObject: records,
        options: [.sortedKeys, .withoutEscapingSlashes]
    )
    return [
        "pid": ProcessInfo.processInfo.processIdentifier,
        "records": records,
        "state_sha256": SHA256.hash(data: canonical)
            .map { String(format: "%02x", $0) }.joined(),
        "accepted_operation_count": try parts.replica.acceptedOperationCount(),
        "pending_replica_count": try parts.replica.pendingOperations().count,
        "pending_outbox_count": try parts.outbox.pendingOperations().count,
        "cursor": try parts.inbox.cursor() ?? NSNull(),
    ]
}

private func prepare(_ arguments: [String]) throws {
    guard arguments.count == 5 else { throw ClientError.invalid }
    let all = try operations(arguments[2])
    let parts = try parts(URL(fileURLWithPath: arguments[1], isDirectory: true))
    for name in arguments[3].split(separator: ",").map(String.init) {
        guard let operation = all[name] else { throw ClientError.invalid }
        _ = try parts.replica.queueLocal(operation)
        try parts.outbox.enqueue(operation)
    }
    var output = try result(parts)
    output["operations"] = try parts.outbox.pendingOperations().map(\.jsonObject)
    try write(arguments[4], output)
}

private func apply(_ arguments: [String]) throws {
    guard arguments.count == 4 else { throw ClientError.invalid }
    let page = try load(arguments[2])
    let parts = try parts(URL(fileURLWithPath: arguments[1], isDirectory: true))
    let pendingReplica = Set(try parts.replica.pendingOperations().map(\.operationID))
    let pendingOutbox = Set(try parts.outbox.pendingOperations().map(\.operationID))
    guard let accepted = page["accepted_operation_ids"] as? [String],
          let rawOperations = page["transport_operations"] as? [[String: Any]]
    else {
        throw ClientError.invalid
    }
    for operationID in accepted {
        if pendingReplica.contains(operationID) {
            try parts.replica.confirmRemoteAcceptance(operationID)
        }
        if pendingOutbox.contains(operationID) {
            try parts.outbox.confirmRemoteAcceptance(operationID)
        }
    }
    try parts.inbox.stagePage(
        expectedCursor: page["expected_cursor"] is NSNull ? nil : page["expected_cursor"] as? String,
        nextCursor: page["next_cursor"] is NSNull ? nil : page["next_cursor"] as? String,
        operations: try rawOperations.map(
            AtlasVaultEncryptedPatchOperation.init(jsonObject:)
        )
    )
    while try parts.inbox.applyNext({ transport in
        _ = try parts.replica.ingestRemote(decryptTransport(transport))
    }) != nil {}
    try write(arguments[3], result(parts))
}

@main
private struct AtlasVaultTwoDeviceConvergenceClient {
    static func main() {
        do {
            let arguments = Array(CommandLine.arguments.dropFirst())
            guard let action = arguments.first else { throw ClientError.invalid }
            if action == "prepare" {
                try prepare(arguments)
            } else if action == "apply" {
                try apply(arguments)
            } else {
                throw ClientError.invalid
            }
        } catch {
            FileHandle.standardError.write(Data("C20 client failed: \(error)\n".utf8))
            exit(1)
        }
    }
}
