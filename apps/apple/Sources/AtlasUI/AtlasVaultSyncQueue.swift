import CryptoKit
import CoreFoundation
import Darwin
import Foundation

public enum AtlasVaultEncryptedPatchQueueError: Error, Equatable, Sendable {
    case invalidState
}

private let patchFormat = "atlasvault-encrypted-patch-operation"
private let opaqueEnvelopeFormat = "atlasvault-opaque-ciphertext-envelope"
private let queueEnvelopeFormat = "atlasvault-encrypted-transfer-queue"
private let maximumQueueBytes = 128 * 1024 * 1024
private let maximumQueueOperations = 65_536
private let maximumEnvelopeFieldBytes = 96 * 1024 * 1024
private let maximumInteger = Int64.max

private func invalid() -> AtlasVaultEncryptedPatchQueueError { .invalidState }

private func exactKeys(_ value: [String: Any], _ expected: Set<String>) throws {
    guard Set(value.keys) == expected else { throw invalid() }
}

private func text(_ value: Any?, maximum: Int = 128) throws -> String {
    guard let value = value as? String, !value.isEmpty, value.count <= maximum else {
        throw invalid()
    }
    return value
}

private func identifier(_ value: Any?) throws -> String {
    let value = try text(value)
    let expression = try NSRegularExpression(pattern: "^[A-Za-z0-9._~-]{1,128}$")
    let range = NSRange(value.startIndex..<value.endIndex, in: value)
    guard value != "*", expression.firstMatch(in: value, range: range)?.range == range else {
        throw invalid()
    }
    return value
}

private func positiveInteger(_ value: Any?) throws -> Int64 {
    guard let value = value as? NSNumber,
          CFGetTypeID(value) != CFBooleanGetTypeID()
    else {
        throw invalid()
    }
    let number = value.int64Value
    guard number >= 1, value.doubleValue == Double(number), number <= maximumInteger else {
        throw invalid()
    }
    return number
}

private func canonicalUUID(_ value: Any?) throws -> String {
    let value = try text(value, maximum: 36)
    guard let parsed = UUID(uuidString: value), parsed.uuidString.lowercased() == value else {
        throw invalid()
    }
    return value
}

private func canonicalBase64(
    _ value: Any?,
    exactLength: Int? = nil,
    minimumLength: Int = 1
) throws -> Data {
    let value = try text(value, maximum: maximumEnvelopeFieldBytes * 2)
    guard let decoded = Data(base64Encoded: value),
          decoded.base64EncodedString() == value,
          decoded.count >= minimumLength,
          decoded.count <= maximumEnvelopeFieldBytes,
          exactLength == nil || decoded.count == exactLength
    else {
        throw invalid()
    }
    return decoded
}

private func canonicalJSON(_ value: [String: Any]) throws -> Data {
    guard JSONSerialization.isValidJSONObject(value) else { throw invalid() }
    do {
        return try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    } catch {
        throw invalid()
    }
}

private func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

public struct AtlasVaultOpaqueCiphertextEnvelope: Equatable, Sendable {
    public let version: Int64
    public let objectID: String
    public let revision: String
    public let parentRevision: String?
    public let keyEpoch: Int64
    public let nonceBase64: String
    public let ciphertextBase64: String
    public let aadBase64: String
    public let signatureBase64: String
    public let tombstone: Bool
    public let contentSHA256: String

    public init(jsonObject value: [String: Any]) throws {
        try exactKeys(
            value,
            [
                "format", "version", "object_id", "revision", "parent_revision",
                "key_epoch", "nonce_b64", "ciphertext_b64", "aad_b64",
                "signature_b64", "tombstone", "content_sha256",
            ])
        guard value["format"] as? String == opaqueEnvelopeFormat,
              let tombstone = value["tombstone"] as? Bool
        else {
            throw invalid()
        }
        let ciphertext = try canonicalBase64(value["ciphertext_b64"], minimumLength: 16)
        let digest = try text(value["content_sha256"], maximum: 64)
        let digestPattern = try NSRegularExpression(pattern: "^[0-9a-f]{64}$")
        let digestRange = NSRange(digest.startIndex..<digest.endIndex, in: digest)
        guard digestPattern.firstMatch(in: digest, range: digestRange)?.range == digestRange,
              digest == sha256Hex(ciphertext)
        else {
            throw invalid()
        }
        let parentValue = value["parent_revision"]
        let parent = parentValue is NSNull ? nil : try identifier(parentValue)
        self.version = try positiveInteger(value["version"])
        self.objectID = try identifier(value["object_id"])
        self.revision = try identifier(value["revision"])
        self.parentRevision = parent
        self.keyEpoch = try positiveInteger(value["key_epoch"])
        self.nonceBase64 = try canonicalBase64(
            value["nonce_b64"], exactLength: 12
        ).base64EncodedString()
        self.ciphertextBase64 = ciphertext.base64EncodedString()
        self.aadBase64 = try canonicalBase64(value["aad_b64"]).base64EncodedString()
        self.signatureBase64 = try canonicalBase64(
            value["signature_b64"]
        ).base64EncodedString()
        self.tombstone = tombstone
        self.contentSHA256 = digest
    }

    public var jsonObject: [String: Any] {
        [
            "format": opaqueEnvelopeFormat,
            "version": version,
            "object_id": objectID,
            "revision": revision,
            "parent_revision": parentRevision ?? NSNull(),
            "key_epoch": keyEpoch,
            "nonce_b64": nonceBase64,
            "ciphertext_b64": ciphertextBase64,
            "aad_b64": aadBase64,
            "signature_b64": signatureBase64,
            "tombstone": tombstone,
            "content_sha256": contentSHA256,
        ]
    }
}

public struct AtlasVaultEncryptedPatchOperation: Equatable, Comparable, Sendable {
    public let operationID: String
    public let operationType: String
    public let authorDeviceID: String
    public let authorSequence: Int64
    public let lamport: Int64
    public let envelope: AtlasVaultOpaqueCiphertextEnvelope

    public init(jsonObject value: [String: Any]) throws {
        try exactKeys(
            value,
            [
                "format", "version", "operation_id", "operation_type",
                "author_device_id", "author_sequence", "lamport", "envelope",
            ])
        guard value["format"] as? String == patchFormat,
              try positiveInteger(value["version"]) == 1,
              let rawEnvelope = value["envelope"] as? [String: Any]
        else {
            throw invalid()
        }
        let envelope = try AtlasVaultOpaqueCiphertextEnvelope(jsonObject: rawEnvelope)
        let expectedType = envelope.tombstone ? "delete" : "upsert"
        guard value["operation_type"] as? String == expectedType else { throw invalid() }
        self.operationID = try canonicalUUID(value["operation_id"])
        self.operationType = expectedType
        self.authorDeviceID = try identifier(value["author_device_id"])
        self.authorSequence = try positiveInteger(value["author_sequence"])
        self.lamport = try positiveInteger(value["lamport"])
        self.envelope = envelope
    }

    public var idempotencyKey: String { operationID }

    public var jsonObject: [String: Any] {
        [
            "format": patchFormat,
            "version": 1,
            "operation_id": operationID,
            "operation_type": operationType,
            "author_device_id": authorDeviceID,
            "author_sequence": authorSequence,
            "lamport": lamport,
            "envelope": envelope.jsonObject,
        ]
    }

    fileprivate var orderKey: OrderKey {
        OrderKey(
            lamport: lamport,
            authorDeviceID: authorDeviceID,
            authorSequence: authorSequence,
            operationID: operationID
        )
    }

    public static func < (
        lhs: AtlasVaultEncryptedPatchOperation,
        rhs: AtlasVaultEncryptedPatchOperation
    ) -> Bool {
        lhs.orderKey < rhs.orderKey
    }
}

private struct OrderKey: Equatable, Comparable {
    let lamport: Int64
    let authorDeviceID: String
    let authorSequence: Int64
    let operationID: String

    static func < (lhs: OrderKey, rhs: OrderKey) -> Bool {
        if lhs.lamport != rhs.lamport { return lhs.lamport < rhs.lamport }
        if lhs.authorDeviceID != rhs.authorDeviceID {
            return lhs.authorDeviceID < rhs.authorDeviceID
        }
        if lhs.authorSequence != rhs.authorSequence {
            return lhs.authorSequence < rhs.authorSequence
        }
        return lhs.operationID < rhs.operationID
    }

    var jsonArray: [Any] { [lamport, authorDeviceID, authorSequence, operationID] }

    init(
        lamport: Int64,
        authorDeviceID: String,
        authorSequence: Int64,
        operationID: String
    ) {
        self.lamport = lamport
        self.authorDeviceID = authorDeviceID
        self.authorSequence = authorSequence
        self.operationID = operationID
    }

    init(jsonArray: [Any]) throws {
        guard jsonArray.count == 4 else { throw invalid() }
        self.init(
            lamport: try positiveInteger(jsonArray[0]),
            authorDeviceID: try identifier(jsonArray[1]),
            authorSequence: try positiveInteger(jsonArray[2]),
            operationID: try canonicalUUID(jsonArray[3])
        )
    }
}

private func fingerprint(_ operation: AtlasVaultEncryptedPatchOperation) throws -> String {
    sha256Hex(try canonicalJSON(operation.jsonObject))
}

private struct EncryptedQueueFile {
    let fileURL: URL
    private let key: SymmetricKey
    private let aad: Data

    init(fileURL: URL, encryptionKey: Data, kind: String) throws {
        guard fileURL.isFileURL, encryptionKey.count == 32 else { throw invalid() }
        self.fileURL = fileURL.standardizedFileURL
        self.key = SymmetricKey(data: encryptionKey)
        self.aad = Data("\(queueEnvelopeFormat):v1:\(kind)".utf8)
    }

    func read(default fallback: [String: Any]) throws -> [String: Any] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return fallback }
        do {
            let attributes = try FileManager.default.attributesOfItem(atPath: fileURL.path)
            guard let size = attributes[.size] as? NSNumber,
                  size.intValue > 0,
                  size.intValue <= maximumQueueBytes
            else {
                throw invalid()
            }
            let data = try Data(contentsOf: fileURL, options: [.mappedIfSafe])
            guard let outer = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw invalid()
            }
            try exactKeys(outer, ["format", "version", "nonce_b64", "ciphertext_b64"])
            guard outer["format"] as? String == queueEnvelopeFormat,
                  try positiveInteger(outer["version"]) == 1
            else {
                throw invalid()
            }
            let nonce = try canonicalBase64(outer["nonce_b64"], exactLength: 12)
            let combined = try canonicalBase64(outer["ciphertext_b64"], minimumLength: 16)
            let box = try AES.GCM.SealedBox(
                nonce: AES.GCM.Nonce(data: nonce),
                ciphertext: Data(combined.dropLast(16)),
                tag: Data(combined.suffix(16))
            )
            let stateData = try AES.GCM.open(box, using: key, authenticating: aad)
            guard let state = try JSONSerialization.jsonObject(with: stateData) as? [String: Any]
            else {
                throw invalid()
            }
            return state
        } catch let error as AtlasVaultEncryptedPatchQueueError {
            throw error
        } catch {
            throw invalid()
        }
    }

    func write(_ state: [String: Any]) throws {
        do {
            let stateData = try canonicalJSON(state)
            guard !stateData.isEmpty, stateData.count <= maximumQueueBytes else { throw invalid() }
            let nonce = AES.GCM.Nonce()
            let sealed = try AES.GCM.seal(
                stateData,
                using: key,
                nonce: nonce,
                authenticating: aad
            )
            let combined = sealed.ciphertext + sealed.tag
            let outer: [String: Any] = [
                "format": queueEnvelopeFormat,
                "version": 1,
                "nonce_b64": Data(nonce).base64EncodedString(),
                "ciphertext_b64": combined.base64EncodedString(),
            ]
            let encoded = try canonicalJSON(outer)
            guard encoded.count <= maximumQueueBytes else { throw invalid() }
            let parent = fileURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(
                at: parent,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try encoded.write(to: fileURL, options: [.atomic])
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: fileURL.path
            )
            let handle = try FileHandle(forWritingTo: fileURL)
            try handle.synchronize()
            try handle.close()
            let descriptor = Darwin.open(parent.path, O_RDONLY | O_DIRECTORY)
            if descriptor >= 0 {
                defer { Darwin.close(descriptor) }
                guard Darwin.fsync(descriptor) == 0 else { throw invalid() }
            }
        } catch let error as AtlasVaultEncryptedPatchQueueError {
            throw error
        } catch {
            throw invalid()
        }
    }
}

private func outboxDefault() -> [String: Any] {
    [
        "format": "atlasvault-encrypted-outbox-state",
        "version": 1,
        "operations": [],
    ]
}

private func loadOutbox(_ store: EncryptedQueueFile) throws -> [AtlasVaultEncryptedPatchOperation] {
    let state = try store.read(default: outboxDefault())
    try exactKeys(state, ["format", "version", "operations"])
    guard state["format"] as? String == "atlasvault-encrypted-outbox-state",
          try positiveInteger(state["version"]) == 1,
          let raw = state["operations"] as? [[String: Any]],
          raw.count <= maximumQueueOperations
    else {
        throw invalid()
    }
    let operations = try raw.map(AtlasVaultEncryptedPatchOperation.init(jsonObject:))
    guard operations == operations.sorted(), Set(operations.map(\.operationID)).count == operations.count
    else {
        throw invalid()
    }
    return operations
}

public final class AtlasVaultDurableEncryptedOutbox {
    private let store: EncryptedQueueFile

    public init(fileURL: URL, encryptionKey: Data) throws {
        self.store = try EncryptedQueueFile(
            fileURL: fileURL,
            encryptionKey: encryptionKey,
            kind: "outbox"
        )
    }

    public func pendingOperations() throws -> [AtlasVaultEncryptedPatchOperation] {
        try loadOutbox(store)
    }

    public func nextPending() throws -> AtlasVaultEncryptedPatchOperation? {
        try loadOutbox(store).first
    }

    public func enqueue(_ operation: AtlasVaultEncryptedPatchOperation) throws {
        var operations = try loadOutbox(store)
        if let current = operations.first(where: { $0.operationID == operation.operationID }) {
            guard try fingerprint(current) == fingerprint(operation) else { throw invalid() }
            return
        }
        guard operations.count < maximumQueueOperations else { throw invalid() }
        operations.append(operation)
        operations.sort()
        try store.write([
            "format": "atlasvault-encrypted-outbox-state",
            "version": 1,
            "operations": operations.map(\.jsonObject),
        ])
    }

    public func confirmRemoteAcceptance(_ operationID: String) throws {
        let operationID = try canonicalUUID(operationID)
        let operations = try loadOutbox(store)
        let retained = operations.filter { $0.operationID != operationID }
        guard retained.count != operations.count else { throw invalid() }
        try store.write([
            "format": "atlasvault-encrypted-outbox-state",
            "version": 1,
            "operations": retained.map(\.jsonObject),
        ])
    }
}

private struct InboxState {
    var cursor: String?
    var pendingPage: Bool
    var pendingNextCursor: String?
    var pending: [AtlasVaultEncryptedPatchOperation]
    var appliedFingerprints: [String: String]
    var authorSequences: [String: Int64]
    var objectRevisions: [String: String]
    var lastOrder: OrderKey?

    var jsonObject: [String: Any] {
        [
            "format": "atlasvault-encrypted-inbox-state",
            "version": 1,
            "cursor": cursor ?? NSNull(),
            "pending_page": pendingPage,
            "pending_next_cursor": pendingNextCursor ?? NSNull(),
            "pending_operations": pending.map(\.jsonObject),
            "applied_fingerprints": appliedFingerprints,
            "author_sequences": authorSequences,
            "object_revisions": objectRevisions,
            "last_order": lastOrder?.jsonArray ?? NSNull(),
        ]
    }
}

private func inboxDefault() -> [String: Any] {
    InboxState(
        cursor: nil,
        pendingPage: false,
        pendingNextCursor: nil,
        pending: [],
        appliedFingerprints: [:],
        authorSequences: [:],
        objectRevisions: [:],
        lastOrder: nil
    ).jsonObject
}

private func validatedCursor(_ value: Any?) throws -> String? {
    value is NSNull ? nil : try text(value, maximum: 2_048)
}

private func loadInbox(_ store: EncryptedQueueFile) throws -> InboxState {
    let state = try store.read(default: inboxDefault())
    try exactKeys(
        state,
        [
            "format", "version", "cursor", "pending_page", "pending_next_cursor",
            "pending_operations", "applied_fingerprints", "author_sequences",
            "object_revisions", "last_order",
        ])
    guard state["format"] as? String == "atlasvault-encrypted-inbox-state",
          try positiveInteger(state["version"]) == 1,
          let rawPending = state["pending_operations"] as? [[String: Any]],
          rawPending.count <= maximumQueueOperations,
          let rawFingerprints = state["applied_fingerprints"] as? [String: Any],
          let rawSequences = state["author_sequences"] as? [String: Any],
          let rawRevisions = state["object_revisions"] as? [String: Any]
    else {
        throw invalid()
    }
    let pending = try rawPending.map(AtlasVaultEncryptedPatchOperation.init(jsonObject:))
    guard pending == pending.sorted(), Set(pending.map(\.operationID)).count == pending.count,
          rawFingerprints.count <= maximumQueueOperations,
          rawSequences.count <= maximumQueueOperations,
          rawRevisions.count <= maximumQueueOperations
    else {
        throw invalid()
    }
    var fingerprints: [String: String] = [:]
    for (key, value) in rawFingerprints {
        let digest = try text(value, maximum: 64)
        let pattern = try NSRegularExpression(pattern: "^[0-9a-f]{64}$")
        let range = NSRange(digest.startIndex..<digest.endIndex, in: digest)
        guard pattern.firstMatch(in: digest, range: range)?.range == range else { throw invalid() }
        fingerprints[try canonicalUUID(key)] = digest
    }
    var sequences: [String: Int64] = [:]
    for (key, value) in rawSequences {
        sequences[try identifier(key)] = try positiveInteger(value)
    }
    var revisions: [String: String] = [:]
    for (key, value) in rawRevisions {
        revisions[try identifier(key)] = try identifier(value)
    }
    let rawLastOrder = state["last_order"]
    let lastOrder: OrderKey?
    if rawLastOrder is NSNull {
        lastOrder = nil
    } else {
        guard let rawLastOrder = rawLastOrder as? [Any] else { throw invalid() }
        lastOrder = try OrderKey(jsonArray: rawLastOrder)
    }
    guard let pendingPageNumber = state["pending_page"] as? NSNumber,
          CFGetTypeID(pendingPageNumber) == CFBooleanGetTypeID()
    else {
        throw invalid()
    }
    let pendingPage = pendingPageNumber.boolValue
    let pendingCursor = try validatedCursor(state["pending_next_cursor"])
    guard pending.isEmpty != pendingPage else { throw invalid() }
    return InboxState(
        cursor: try validatedCursor(state["cursor"]),
        pendingPage: pendingPage,
        pendingNextCursor: pendingCursor,
        pending: pending,
        appliedFingerprints: fingerprints,
        authorSequences: sequences,
        objectRevisions: revisions,
        lastOrder: lastOrder
    )
}

private func advanceMetadata(
    _ operation: AtlasVaultEncryptedPatchOperation,
    sequences: inout [String: Int64],
    revisions: inout [String: String],
    lastOrder: OrderKey?
) throws -> OrderKey {
    guard lastOrder == nil || operation.orderKey > lastOrder! else { throw invalid() }
    guard operation.authorSequence == (sequences[operation.authorDeviceID] ?? 0) + 1 else {
        throw invalid()
    }
    guard operation.envelope.parentRevision == revisions[operation.envelope.objectID] else {
        throw invalid()
    }
    sequences[operation.authorDeviceID] = operation.authorSequence
    revisions[operation.envelope.objectID] = operation.envelope.revision
    return operation.orderKey
}

public final class AtlasVaultDurableEncryptedInbox {
    private let store: EncryptedQueueFile

    public init(fileURL: URL, encryptionKey: Data) throws {
        self.store = try EncryptedQueueFile(
            fileURL: fileURL,
            encryptionKey: encryptionKey,
            kind: "inbox"
        )
    }

    public func cursor() throws -> String? { try loadInbox(store).cursor }

    public func pendingOperations() throws -> [AtlasVaultEncryptedPatchOperation] {
        try loadInbox(store).pending
    }

    public func stagePage(
        expectedCursor: String?,
        nextCursor: String?,
        operations: [AtlasVaultEncryptedPatchOperation]
    ) throws {
        let expectedCursor = try validatedCursor(expectedCursor ?? NSNull())
        let nextCursor = try validatedCursor(nextCursor ?? NSNull())
        var state = try loadInbox(store)
        guard state.pending.isEmpty, state.cursor == expectedCursor,
              operations.count <= maximumQueueOperations,
              operations == operations.sorted(),
              Set(operations.map(\.operationID)).count == operations.count
        else {
            throw invalid()
        }
        var sequences = state.authorSequences
        var revisions = state.objectRevisions
        var lastOrder = state.lastOrder
        var fresh: [AtlasVaultEncryptedPatchOperation] = []
        for operation in operations {
            let digest = try fingerprint(operation)
            if let known = state.appliedFingerprints[operation.operationID] {
                guard known == digest else { throw invalid() }
                continue
            }
            lastOrder = try advanceMetadata(
                operation,
                sequences: &sequences,
                revisions: &revisions,
                lastOrder: lastOrder
            )
            fresh.append(operation)
        }
        guard state.appliedFingerprints.count + fresh.count <= maximumQueueOperations else {
            throw invalid()
        }
        state.cursor = fresh.isEmpty ? nextCursor : state.cursor
        state.pendingPage = !fresh.isEmpty
        state.pendingNextCursor = nextCursor
        state.pending = fresh
        try store.write(state.jsonObject)
    }

    @discardableResult
    public func applyNext(
        _ apply: (AtlasVaultEncryptedPatchOperation) throws -> Void
    ) throws -> AtlasVaultEncryptedPatchOperation? {
        var state = try loadInbox(store)
        guard let operation = state.pending.first else { return nil }
        try apply(operation)
        var sequences = state.authorSequences
        var revisions = state.objectRevisions
        let lastOrder = try advanceMetadata(
            operation,
            sequences: &sequences,
            revisions: &revisions,
            lastOrder: state.lastOrder
        )
        state.appliedFingerprints[operation.operationID] = try fingerprint(operation)
        state.pending.removeFirst()
        state.pendingPage = !state.pending.isEmpty
        state.authorSequences = sequences
        state.objectRevisions = revisions
        state.lastOrder = lastOrder
        if state.pending.isEmpty {
            state.cursor = state.pendingNextCursor
            state.pendingNextCursor = nil
        }
        try store.write(state.jsonObject)
        return operation
    }
}
