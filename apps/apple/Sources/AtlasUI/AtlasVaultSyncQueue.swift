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
private let snapshotFormat = "atlasvault-authenticated-collection-snapshot"
private let snapshotPayloadFormat = "atlasvault-authenticated-collection-snapshot-payload"
private let snapshotAuthenticationAlgorithm = "HMAC-SHA256"
private let collectionStateFormat = "atlasvault-encrypted-patch-collection-state"
private let convergentReplicaStateFormat = "atlasvault-encrypted-convergent-replica-state"
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

private func nonnegativeInteger(_ value: Any?) throws -> Int64 {
    guard let value = value as? NSNumber,
          CFGetTypeID(value) != CFBooleanGetTypeID()
    else {
        throw invalid()
    }
    let number = value.int64Value
    guard number >= 0, value.doubleValue == Double(number), number <= maximumInteger else {
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
        return try JSONSerialization.data(
            withJSONObject: value,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
    } catch {
        throw invalid()
    }
}

private func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func constantTimeEqual(_ left: Data, _ right: Data) -> Bool {
    guard left.count == right.count else { return false }
    var difference: UInt8 = 0
    for index in left.indices {
        difference |= left[index] ^ right[index]
    }
    return difference == 0
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

private func syncDirectory(_ directory: URL) throws {
    let descriptor = Darwin.open(directory.path, O_RDONLY | O_DIRECTORY)
    guard descriptor >= 0 else { throw invalid() }
    defer { Darwin.close(descriptor) }
    guard Darwin.fsync(descriptor) == 0 else { throw invalid() }
}

private func ensureDurableDirectory(_ directory: URL) throws {
    var missing: [URL] = []
    var current = directory.standardizedFileURL
    var isDirectory = ObjCBool(false)
    while !FileManager.default.fileExists(atPath: current.path, isDirectory: &isDirectory) {
        let parent = current.deletingLastPathComponent()
        guard parent.path != current.path else { throw invalid() }
        missing.append(current)
        current = parent
    }
    guard isDirectory.boolValue else { throw invalid() }
    for item in missing.reversed() {
        try FileManager.default.createDirectory(
            at: item,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        try syncDirectory(item.deletingLastPathComponent())
    }
}

private func removeAbandonedQueueStages(for fileURL: URL) throws {
    let parent = fileURL.deletingLastPathComponent()
    var isDirectory = ObjCBool(false)
    guard FileManager.default.fileExists(atPath: parent.path, isDirectory: &isDirectory) else {
        return
    }
    guard isDirectory.boolValue else { throw invalid() }
    let prefix = ".\(fileURL.lastPathComponent)."
    let suffix = ".tmp"
    for candidate in try FileManager.default.contentsOfDirectory(
        at: parent,
        includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey]
    ) {
        let name = candidate.lastPathComponent
        guard name.hasPrefix(prefix), name.hasSuffix(suffix) else { continue }
        let start = name.index(name.startIndex, offsetBy: prefix.count)
        let end = name.index(name.endIndex, offsetBy: -suffix.count)
        guard UUID(uuidString: String(name[start..<end])) != nil else { continue }
        let values = try candidate.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        if values.isRegularFile == true || values.isSymbolicLink == true {
            try FileManager.default.removeItem(at: candidate)
        }
    }
}

struct EncryptedQueueFile {
    let fileURL: URL
    private let key: SymmetricKey
    private let aad: Data

    init(fileURL: URL, encryptionKey: Data, kind: String) throws {
        guard fileURL.isFileURL, encryptionKey.count == 32 else { throw invalid() }
        self.fileURL = fileURL.standardizedFileURL
        self.key = SymmetricKey(data: encryptionKey)
        self.aad = Data("\(queueEnvelopeFormat):v1:\(kind)".utf8)
        try removeAbandonedQueueStages(for: self.fileURL)
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

    func write(
        _ state: [String: Any],
        beforeReplace: (() throws -> Void)? = nil
    ) throws {
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
            try ensureDurableDirectory(parent)
            let staged = parent.appendingPathComponent(
                ".\(fileURL.lastPathComponent).\(UUID().uuidString).tmp"
            )
            defer { try? FileManager.default.removeItem(at: staged) }
            try encoded.write(to: staged)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: staged.path
            )
            let handle = try FileHandle(forWritingTo: staged)
            try handle.synchronize()
            try handle.close()
            try beforeReplace?()
            guard Darwin.rename(staged.path, fileURL.path) == 0 else { throw invalid() }
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: fileURL.path
            )
            try syncDirectory(parent)
        } catch let error as AtlasVaultEncryptedPatchQueueError {
            throw error
        } catch {
            throw invalid()
        }
    }
}

public struct AtlasVaultAuthenticatedCollectionSnapshot {
    public let collectionID: String
    public let collectionRevision: Int64
    public let records: [AtlasVaultOpaqueCiphertextEnvelope]
    public let authenticationTagBase64: String
    public let canonicalPayloadSHA256: String

    fileprivate let lastOrder: OrderKey
    fileprivate let appliedFingerprints: [String: String]
    fileprivate let authorSequences: [String: Int64]
    fileprivate let authorSequenceOwners: [String: [Int64: String]]

    public init(jsonObject value: [String: Any], authenticationKey: Data) throws {
        guard authenticationKey.count == 32 else { throw invalid() }
        try exactKeys(value, ["format", "version", "payload", "authentication"])
        guard value["format"] as? String == snapshotFormat,
              try positiveInteger(value["version"]) == 1,
              let payload = value["payload"] as? [String: Any],
              let authentication = value["authentication"] as? [String: Any]
        else {
            throw invalid()
        }
        try exactKeys(authentication, ["algorithm", "tag_b64"])
        guard authentication["algorithm"] as? String == snapshotAuthenticationAlgorithm else {
            throw invalid()
        }
        let tag = try canonicalBase64(authentication["tag_b64"], exactLength: 32)
        let payloadData = try canonicalJSON(payload)
        let expected = Data(HMAC<SHA256>.authenticationCode(
            for: payloadData,
            using: SymmetricKey(data: authenticationKey)
        ))
        guard constantTimeEqual(tag, expected) else { throw invalid() }
        try exactKeys(
            payload,
            [
                "format", "version", "collection_id", "collection_revision",
                "last_order", "records", "applied_fingerprints", "author_sequences",
                "author_sequence_owners",
                "record_count", "live_record_count", "tombstone_count",
            ])
        guard payload["format"] as? String == snapshotPayloadFormat,
              try positiveInteger(payload["version"]) == 1,
              let rawOrder = payload["last_order"] as? [Any],
              let rawRecords = payload["records"] as? [[String: Any]],
              let rawFingerprints = payload["applied_fingerprints"] as? [String: Any],
              let rawSequences = payload["author_sequences"] as? [String: Any],
              let rawOwners = payload["author_sequence_owners"] as? [String: Any]
        else {
            throw invalid()
        }
        let revision = try positiveInteger(payload["collection_revision"])
        guard revision <= Int64(maximumQueueOperations),
              rawRecords.count <= maximumQueueOperations,
              Int64(rawFingerprints.count) == revision,
              !rawSequences.isEmpty,
              rawSequences.count <= maximumQueueOperations
        else {
            throw invalid()
        }
        let order = try OrderKey(jsonArray: rawOrder)
        let records = try rawRecords.map(AtlasVaultOpaqueCiphertextEnvelope.init(jsonObject:))
        guard records.map(\.objectID) == records.map(\.objectID).sorted(),
              Set(records.map(\.objectID)).count == records.count
        else {
            throw invalid()
        }
        var fingerprints: [String: String] = [:]
        for (operationID, value) in rawFingerprints {
            let digest = try text(value, maximum: 64)
            let expression = try NSRegularExpression(pattern: "^[0-9a-f]{64}$")
            let range = NSRange(digest.startIndex..<digest.endIndex, in: digest)
            guard expression.firstMatch(in: digest, range: range)?.range == range else {
                throw invalid()
            }
            fingerprints[try canonicalUUID(operationID)] = digest
        }
        guard fingerprints[order.operationID] != nil else { throw invalid() }
        var sequences: [String: Int64] = [:]
        var sequenceTotal: Int64 = 0
        for (device, value) in rawSequences {
            let sequence = try positiveInteger(value)
            let addition = sequenceTotal.addingReportingOverflow(sequence)
            guard !addition.overflow else { throw invalid() }
            sequenceTotal = addition.partialValue
            sequences[try identifier(device)] = sequence
        }
        guard sequenceTotal == revision else { throw invalid() }
        var sequenceOwners: [String: [Int64: String]] = [:]
        for (rawDevice, rawValue) in rawOwners {
            let device = try identifier(rawDevice)
            guard let rawDeviceOwners = rawValue as? [String: Any] else { throw invalid() }
            var deviceOwners: [Int64: String] = [:]
            for (rawSequence, rawOperationID) in rawDeviceOwners {
                guard let sequence = Int64(rawSequence), sequence > 0,
                      String(sequence) == rawSequence
                else {
                    throw invalid()
                }
                deviceOwners[sequence] = try canonicalUUID(rawOperationID)
            }
            sequenceOwners[device] = deviceOwners
        }
        guard Set(sequenceOwners.keys) == Set(sequences.keys) else { throw invalid() }
        for (device, maximumSequence) in sequences {
            guard let owners = sequenceOwners[device],
                  Int64(owners.count) == maximumSequence
            else {
                throw invalid()
            }
            for sequence in 1...maximumSequence where owners[sequence] == nil {
                throw invalid()
            }
        }
        let ownerIDs = Set(sequenceOwners.values.flatMap(\.values))
        guard Int64(ownerIDs.count) == revision,
              ownerIDs == Set(fingerprints.keys)
        else {
            throw invalid()
        }
        let recordCount = try nonnegativeInteger(payload["record_count"])
        let liveCount = try nonnegativeInteger(payload["live_record_count"])
        let tombstoneCount = try nonnegativeInteger(payload["tombstone_count"])
        guard recordCount == Int64(records.count),
              liveCount == Int64(records.filter({ !$0.tombstone }).count),
              tombstoneCount == Int64(records.filter(\.tombstone).count),
              liveCount + tombstoneCount == recordCount
        else {
            throw invalid()
        }
        self.collectionID = try identifier(payload["collection_id"])
        self.collectionRevision = revision
        self.lastOrder = order
        self.records = records
        self.appliedFingerprints = fingerprints
        self.authorSequences = sequences
        self.authorSequenceOwners = sequenceOwners
        self.authenticationTagBase64 = tag.base64EncodedString()
        self.canonicalPayloadSHA256 = sha256Hex(payloadData)
    }

    public init(jsonData: Data, authenticationKey: Data) throws {
        do {
            guard let value = try JSONSerialization.jsonObject(with: jsonData) as? [String: Any]
            else {
                throw invalid()
            }
            try self.init(jsonObject: value, authenticationKey: authenticationKey)
        } catch let error as AtlasVaultEncryptedPatchQueueError {
            throw error
        } catch {
            throw invalid()
        }
    }

    fileprivate static func create(
        collectionID: String,
        records: [String: AtlasVaultOpaqueCiphertextEnvelope],
        fingerprints: [String: String],
        authorSequences: [String: Int64],
        authorSequenceOwners: [String: [Int64: String]],
        lastOrder: OrderKey,
        authenticationKey: Data
    ) throws -> AtlasVaultAuthenticatedCollectionSnapshot {
        let orderedRecords = records.keys.sorted().compactMap { records[$0]?.jsonObject }
        let payload: [String: Any] = [
            "format": snapshotPayloadFormat,
            "version": 1,
            "collection_id": collectionID,
            "collection_revision": fingerprints.count,
            "last_order": lastOrder.jsonArray,
            "records": orderedRecords,
            "applied_fingerprints": fingerprints,
            "author_sequences": authorSequences,
            "author_sequence_owners": sequenceOwnersJSON(authorSequenceOwners),
            "record_count": records.count,
            "live_record_count": records.values.filter { !$0.tombstone }.count,
            "tombstone_count": records.values.filter(\.tombstone).count,
        ]
        let tag = Data(HMAC<SHA256>.authenticationCode(
            for: try canonicalJSON(payload),
            using: SymmetricKey(data: authenticationKey)
        ))
        return try AtlasVaultAuthenticatedCollectionSnapshot(
            jsonObject: [
                "format": snapshotFormat,
                "version": 1,
                "payload": payload,
                "authentication": [
                    "algorithm": snapshotAuthenticationAlgorithm,
                    "tag_b64": tag.base64EncodedString(),
                ],
            ],
            authenticationKey: authenticationKey
        )
    }

    public var jsonObject: [String: Any] {
        let payload: [String: Any] = [
            "format": snapshotPayloadFormat,
            "version": 1,
            "collection_id": collectionID,
            "collection_revision": collectionRevision,
            "last_order": lastOrder.jsonArray,
            "records": records.map(\.jsonObject),
            "applied_fingerprints": appliedFingerprints,
            "author_sequences": authorSequences,
            "author_sequence_owners": sequenceOwnersJSON(authorSequenceOwners),
            "record_count": records.count,
            "live_record_count": records.filter { !$0.tombstone }.count,
            "tombstone_count": records.filter(\.tombstone).count,
        ]
        return [
            "format": snapshotFormat,
            "version": 1,
            "payload": payload,
            "authentication": [
                "algorithm": snapshotAuthenticationAlgorithm,
                "tag_b64": authenticationTagBase64,
            ],
        ]
    }
}

private func sequenceOwnersJSON(
    _ owners: [String: [Int64: String]]
) -> [String: [String: String]] {
    owners.mapValues { deviceOwners in
        Dictionary(uniqueKeysWithValues: deviceOwners.map { (String($0.key), $0.value) })
    }
}

private struct CollectionReplay {
    var records: [String: AtlasVaultOpaqueCiphertextEnvelope]
    var fingerprints: [String: String]
    var authorSequences: [String: Int64]
    var authorSequenceOwners: [String: [Int64: String]]
    var objectRevisions: [String: String]
    var lastOrder: OrderKey?

    init(snapshot: AtlasVaultAuthenticatedCollectionSnapshot?) {
        guard let snapshot else {
            self.records = [:]
            self.fingerprints = [:]
            self.authorSequences = [:]
            self.authorSequenceOwners = [:]
            self.objectRevisions = [:]
            self.lastOrder = nil
            return
        }
        self.records = Dictionary(uniqueKeysWithValues: snapshot.records.map {
            ($0.objectID, $0)
        })
        self.fingerprints = snapshot.appliedFingerprints
        self.authorSequences = snapshot.authorSequences
        self.authorSequenceOwners = snapshot.authorSequenceOwners
        self.objectRevisions = Dictionary(uniqueKeysWithValues: snapshot.records.map {
            ($0.objectID, $0.revision)
        })
        self.lastOrder = snapshot.lastOrder
    }

    mutating func apply(_ operation: AtlasVaultEncryptedPatchOperation) throws -> Bool {
        let digest = try fingerprint(operation)
        if let known = fingerprints[operation.operationID] {
            guard known == digest else { throw invalid() }
            return false
        }
        guard fingerprints.count < maximumQueueOperations else { throw invalid() }
        var owners = authorSequenceOwners[operation.authorDeviceID] ?? [:]
        if let knownOwner = owners[operation.authorSequence],
           knownOwner != operation.operationID
        {
            throw invalid()
        }
        lastOrder = try advanceMetadata(
            operation,
            sequences: &authorSequences,
            revisions: &objectRevisions,
            lastOrder: lastOrder
        )
        records[operation.envelope.objectID] = operation.envelope
        fingerprints[operation.operationID] = digest
        owners[operation.authorSequence] = operation.operationID
        authorSequenceOwners[operation.authorDeviceID] = owners
        return true
    }
}

private struct LoadedCollection {
    let snapshot: AtlasVaultAuthenticatedCollectionSnapshot?
    let tail: [AtlasVaultEncryptedPatchOperation]
    var replay: CollectionReplay
}

private func collectionDefault(_ collectionID: String) -> [String: Any] {
    [
        "format": collectionStateFormat,
        "version": 1,
        "collection_id": collectionID,
        "snapshot": NSNull(),
        "tail_operations": [],
    ]
}

private func loadCollection(
    _ store: EncryptedQueueFile,
    collectionID: String,
    authenticationKey: Data
) throws -> LoadedCollection {
    let state = try store.read(default: collectionDefault(collectionID))
    try exactKeys(
        state,
        ["format", "version", "collection_id", "snapshot", "tail_operations"]
    )
    guard state["format"] as? String == collectionStateFormat,
          try positiveInteger(state["version"]) == 1,
          try identifier(state["collection_id"]) == collectionID,
          let rawTail = state["tail_operations"] as? [[String: Any]],
          rawTail.count <= maximumQueueOperations
    else {
        throw invalid()
    }
    let snapshot: AtlasVaultAuthenticatedCollectionSnapshot?
    if state["snapshot"] is NSNull {
        snapshot = nil
    } else {
        guard let rawSnapshot = state["snapshot"] as? [String: Any] else { throw invalid() }
        snapshot = try AtlasVaultAuthenticatedCollectionSnapshot(
            jsonObject: rawSnapshot,
            authenticationKey: authenticationKey
        )
        guard snapshot?.collectionID == collectionID else { throw invalid() }
    }
    let tail = try rawTail.map(AtlasVaultEncryptedPatchOperation.init(jsonObject:))
    guard tail == tail.sorted(), Set(tail.map(\.operationID)).count == tail.count else {
        throw invalid()
    }
    var replay = CollectionReplay(snapshot: snapshot)
    for operation in tail {
        guard try replay.apply(operation) else { throw invalid() }
    }
    return LoadedCollection(snapshot: snapshot, tail: tail, replay: replay)
}

public final class AtlasVaultDurableEncryptedPatchCollection {
    private let store: EncryptedQueueFile
    private let authenticationKey: Data
    private let collectionID: String

    public init(
        fileURL: URL,
        encryptionKey: Data,
        authenticationKey: Data,
        collectionID: String
    ) throws {
        guard authenticationKey.count == 32 else { throw invalid() }
        self.collectionID = try identifier(collectionID)
        self.authenticationKey = authenticationKey
        self.store = try EncryptedQueueFile(
            fileURL: fileURL,
            encryptionKey: encryptionKey,
            kind: "collection"
        )
    }

    private func load() throws -> LoadedCollection {
        try loadCollection(
            store,
            collectionID: collectionID,
            authenticationKey: authenticationKey
        )
    }

    public func append(_ operation: AtlasVaultEncryptedPatchOperation) throws {
        var loaded = try load()
        guard try loaded.replay.apply(operation) else { return }
        try store.write([
            "format": collectionStateFormat,
            "version": 1,
            "collection_id": collectionID,
            "snapshot": loaded.snapshot?.jsonObject ?? NSNull(),
            "tail_operations": (loaded.tail + [operation]).map(\.jsonObject),
        ])
    }

    public func currentRecords() throws -> [AtlasVaultOpaqueCiphertextEnvelope] {
        let replay = try load().replay
        return replay.records.keys.sorted().compactMap { replay.records[$0] }
    }

    public func tailOperations() throws -> [AtlasVaultEncryptedPatchOperation] {
        try load().tail
    }

    public func snapshot() throws -> AtlasVaultAuthenticatedCollectionSnapshot? {
        try load().snapshot
    }

    public func committedOperationCount() throws -> Int {
        try load().replay.fingerprints.count
    }

    public func compact(
        beforeReplace: (() throws -> Void)? = nil
    ) throws -> AtlasVaultAuthenticatedCollectionSnapshot {
        let replay = try load().replay
        guard let lastOrder = replay.lastOrder, !replay.fingerprints.isEmpty else {
            throw invalid()
        }
        let snapshot = try AtlasVaultAuthenticatedCollectionSnapshot.create(
            collectionID: collectionID,
            records: replay.records,
            fingerprints: replay.fingerprints,
            authorSequences: replay.authorSequences,
            authorSequenceOwners: replay.authorSequenceOwners,
            lastOrder: lastOrder,
            authenticationKey: authenticationKey
        )
        try store.write(
            [
                "format": collectionStateFormat,
                "version": 1,
                "collection_id": collectionID,
                "snapshot": snapshot.jsonObject,
                "tail_operations": [],
            ],
            beforeReplace: beforeReplace
        )
        return snapshot
    }
}

private struct ConvergentReplicaState {
    let operations: [AtlasVaultEncryptedPatchOperation]
    let snapshots: [AtlasVaultAuthenticatedCollectionSnapshot]
    let pendingOperationIDs: [String]
    let receipts: [String: String]
}

private struct ConvergentRevisionKey: Hashable {
    let objectID: String
    let revision: String
}

private struct ConvergentSequenceKey: Hashable {
    let deviceID: String
    let sequence: Int64
}

private func convergentDefault(_ collectionID: String) -> [String: Any] {
    [
        "format": convergentReplicaStateFormat,
        "version": 1,
        "collection_id": collectionID,
        "operations": [],
        "snapshots": [],
        "pending_operation_ids": [],
    ]
}

private func validateConvergentHistory(
    _ operations: [AtlasVaultEncryptedPatchOperation],
    snapshots: [AtlasVaultAuthenticatedCollectionSnapshot]
) throws -> [String: String] {
    var receipts: [String: String] = [:]
    var snapshotSequences: [String: Int64] = [:]
    var sequenceOwners: [ConvergentSequenceKey: String] = [:]
    var operationSequences: [String: ConvergentSequenceKey] = [:]
    var revisionValues: [ConvergentRevisionKey: Data] = [:]
    var revisionParents: [ConvergentRevisionKey: String?] = [:]

    func addReceipt(_ operationID: String, _ digest: String) throws {
        if let known = receipts[operationID], known != digest { throw invalid() }
        receipts[operationID] = digest
    }

    func addEnvelope(_ envelope: AtlasVaultOpaqueCiphertextEnvelope) throws {
        guard envelope.parentRevision != envelope.revision else { throw invalid() }
        let key = ConvergentRevisionKey(
            objectID: envelope.objectID,
            revision: envelope.revision
        )
        let encoded = try canonicalJSON(envelope.jsonObject)
        if let known = revisionValues[key], known != encoded { throw invalid() }
        revisionValues[key] = encoded
        revisionParents[key] = envelope.parentRevision
    }

    func addSequenceOwner(_ key: ConvergentSequenceKey, _ operationID: String) throws {
        if let knownOwner = sequenceOwners[key], knownOwner != operationID {
            throw invalid()
        }
        if let knownSequence = operationSequences[operationID], knownSequence != key {
            throw invalid()
        }
        sequenceOwners[key] = operationID
        operationSequences[operationID] = key
    }

    for snapshot in snapshots {
        for (operationID, digest) in snapshot.appliedFingerprints {
            try addReceipt(operationID, digest)
        }
        for (deviceID, sequence) in snapshot.authorSequences {
            snapshotSequences[deviceID] = max(snapshotSequences[deviceID] ?? 0, sequence)
        }
        for (deviceID, owners) in snapshot.authorSequenceOwners {
            for (sequence, operationID) in owners {
                let key = ConvergentSequenceKey(deviceID: deviceID, sequence: sequence)
                try addSequenceOwner(key, operationID)
            }
        }
        for envelope in snapshot.records { try addEnvelope(envelope) }
    }

    for operation in operations {
        let digest = try fingerprint(operation)
        let knownReceipt = receipts[operation.operationID]
        try addReceipt(operation.operationID, digest)
        let sequenceKey = ConvergentSequenceKey(
            deviceID: operation.authorDeviceID,
            sequence: operation.authorSequence
        )
        try addSequenceOwner(sequenceKey, operation.operationID)
        if knownReceipt == nil,
           operation.authorSequence <= (snapshotSequences[operation.authorDeviceID] ?? 0)
        {
            throw invalid()
        }
        try addEnvelope(operation.envelope)
    }
    guard receipts.count <= maximumQueueOperations else { throw invalid() }

    for start in revisionParents.keys {
        var seen: Set<ConvergentRevisionKey> = []
        var current: ConvergentRevisionKey? = start
        while let value = current, let parentValue = revisionParents[value] {
            guard seen.insert(value).inserted else { throw invalid() }
            current = parentValue.map {
                ConvergentRevisionKey(objectID: value.objectID, revision: $0)
            }
        }
    }
    return receipts
}

private func loadConvergentReplica(
    _ store: EncryptedQueueFile,
    collectionID: String,
    authenticationKey: Data
) throws -> ConvergentReplicaState {
    let state = try store.read(default: convergentDefault(collectionID))
    try exactKeys(
        state,
        [
            "format", "version", "collection_id", "operations", "snapshots",
            "pending_operation_ids",
        ]
    )
    guard state["format"] as? String == convergentReplicaStateFormat,
          try positiveInteger(state["version"]) == 1,
          try identifier(state["collection_id"]) == collectionID,
          let rawOperations = state["operations"] as? [[String: Any]],
          let rawSnapshots = state["snapshots"] as? [[String: Any]],
          let rawPending = state["pending_operation_ids"] as? [String],
          rawOperations.count <= maximumQueueOperations,
          rawSnapshots.count <= maximumQueueOperations,
          rawPending.count <= maximumQueueOperations
    else {
        throw invalid()
    }
    let operations = try rawOperations.map(
        AtlasVaultEncryptedPatchOperation.init(jsonObject:)
    )
    guard operations == operations.sorted(by: { $0.operationID < $1.operationID }),
          Set(operations.map(\.operationID)).count == operations.count
    else {
        throw invalid()
    }
    let snapshots = try rawSnapshots.map {
        try AtlasVaultAuthenticatedCollectionSnapshot(
            jsonObject: $0,
            authenticationKey: authenticationKey
        )
    }
    guard snapshots.allSatisfy({ $0.collectionID == collectionID }),
          snapshots.map(\.canonicalPayloadSHA256)
            == snapshots.map(\.canonicalPayloadSHA256).sorted(),
          Set(snapshots.map(\.canonicalPayloadSHA256)).count == snapshots.count
    else {
        throw invalid()
    }
    let pending = try rawPending.map(canonicalUUID)
    guard pending == pending.sorted(),
          Set(pending).count == pending.count,
          Set(operations.map(\.operationID)).isSuperset(of: pending)
    else {
        throw invalid()
    }
    return try ConvergentReplicaState(
        operations: operations,
        snapshots: snapshots,
        pendingOperationIDs: pending,
        receipts: validateConvergentHistory(operations, snapshots: snapshots)
    )
}

private struct ConvergentCandidate {
    let envelope: AtlasVaultOpaqueCiphertextEnvelope
    let operation: AtlasVaultEncryptedPatchOperation?
    let snapshot: AtlasVaultAuthenticatedCollectionSnapshot?
}

private func snapshotDominates(
    _ newer: AtlasVaultAuthenticatedCollectionSnapshot,
    _ older: AtlasVaultAuthenticatedCollectionSnapshot
) -> Bool {
    guard newer.collectionRevision > older.collectionRevision else { return false }
    for (deviceID, owners) in older.authorSequenceOwners {
        guard let newerOwners = newer.authorSequenceOwners[deviceID] else { return false }
        for (sequence, operationID) in owners where newerOwners[sequence] != operationID {
            return false
        }
    }
    return true
}

public final class AtlasVaultDurableEncryptedConvergentReplica {
    private let store: EncryptedQueueFile
    private let authenticationKey: Data
    private let collectionID: String

    public init(
        fileURL: URL,
        encryptionKey: Data,
        authenticationKey: Data,
        collectionID: String
    ) throws {
        guard authenticationKey.count == 32 else { throw invalid() }
        self.collectionID = try identifier(collectionID)
        self.authenticationKey = authenticationKey
        self.store = try EncryptedQueueFile(
            fileURL: fileURL,
            encryptionKey: encryptionKey,
            kind: "convergent-replica"
        )
    }

    private func load() throws -> ConvergentReplicaState {
        try loadConvergentReplica(
            store,
            collectionID: collectionID,
            authenticationKey: authenticationKey
        )
    }

    private func write(
        operations: [AtlasVaultEncryptedPatchOperation],
        snapshots: [AtlasVaultAuthenticatedCollectionSnapshot],
        pendingOperationIDs: [String]
    ) throws {
        let orderedOperations = operations.sorted { $0.operationID < $1.operationID }
        let orderedSnapshots = snapshots.sorted {
            $0.canonicalPayloadSHA256 < $1.canonicalPayloadSHA256
        }
        let orderedPending = pendingOperationIDs.sorted()
        _ = try validateConvergentHistory(orderedOperations, snapshots: orderedSnapshots)
        guard Set(orderedOperations.map(\.operationID)).isSuperset(of: orderedPending) else {
            throw invalid()
        }
        try store.write([
            "format": convergentReplicaStateFormat,
            "version": 1,
            "collection_id": collectionID,
            "operations": orderedOperations.map(\.jsonObject),
            "snapshots": orderedSnapshots.map(\.jsonObject),
            "pending_operation_ids": orderedPending,
        ])
    }

    @discardableResult
    public func ingestRemote(_ operation: AtlasVaultEncryptedPatchOperation) throws -> Bool {
        let state = try load()
        let digest = try fingerprint(operation)
        if let known = state.receipts[operation.operationID] {
            guard known == digest else { throw invalid() }
            return false
        }
        try write(
            operations: state.operations + [operation],
            snapshots: state.snapshots,
            pendingOperationIDs: state.pendingOperationIDs
        )
        return true
    }

    @discardableResult
    public func queueLocal(_ operation: AtlasVaultEncryptedPatchOperation) throws -> Bool {
        let state = try load()
        let digest = try fingerprint(operation)
        if let known = state.receipts[operation.operationID] {
            guard known == digest else { throw invalid() }
            return false
        }
        try write(
            operations: state.operations + [operation],
            snapshots: state.snapshots,
            pendingOperationIDs: state.pendingOperationIDs + [operation.operationID]
        )
        return true
    }

    @discardableResult
    public func mergeSnapshot(
        _ snapshot: AtlasVaultAuthenticatedCollectionSnapshot
    ) throws -> Bool {
        let verified = try AtlasVaultAuthenticatedCollectionSnapshot(
            jsonObject: snapshot.jsonObject,
            authenticationKey: authenticationKey
        )
        guard verified.collectionID == collectionID else { throw invalid() }
        let state = try load()
        guard !state.snapshots.contains(where: {
            $0.canonicalPayloadSHA256 == verified.canonicalPayloadSHA256
        }) else {
            return false
        }
        try write(
            operations: state.operations,
            snapshots: state.snapshots + [verified],
            pendingOperationIDs: state.pendingOperationIDs
        )
        return true
    }

    public func currentRecords() throws -> [AtlasVaultOpaqueCiphertextEnvelope] {
        let state = try load()
        var candidates: [String: [ConvergentCandidate]] = [:]
        func consider(_ candidate: ConvergentCandidate) {
            let objectID = candidate.envelope.objectID
            candidates[objectID, default: []].append(candidate)
        }
        for snapshot in state.snapshots {
            for envelope in snapshot.records {
                consider(ConvergentCandidate(
                    envelope: envelope,
                    operation: nil,
                    snapshot: snapshot
                ))
            }
        }
        for operation in state.operations {
            consider(ConvergentCandidate(
                envelope: operation.envelope,
                operation: operation,
                snapshot: nil
            ))
        }
        var winners: [String: AtlasVaultOpaqueCiphertextEnvelope] = [:]
        for (objectID, objectCandidates) in candidates {
            var eligible = objectCandidates
            if eligible.contains(where: { $0.envelope.tombstone }) {
                eligible = eligible.filter { $0.envelope.tombstone }
            }
            let operationCandidates = eligible.filter { $0.operation != nil }
            if let winner = operationCandidates.max(by: {
                $0.operation! < $1.operation!
            }) {
                winners[objectID] = winner.envelope
                continue
            }
            let snapshotCandidates = eligible.filter { $0.snapshot != nil }
            let undominated = snapshotCandidates.filter { candidate in
                !snapshotCandidates.contains { other in
                    other.snapshot!.canonicalPayloadSHA256
                        != candidate.snapshot!.canonicalPayloadSHA256
                        && snapshotDominates(other.snapshot!, candidate.snapshot!)
                }
            }
            guard let winner = undominated.max(by: {
                $0.snapshot!.canonicalPayloadSHA256
                    < $1.snapshot!.canonicalPayloadSHA256
            }) else {
                throw invalid()
            }
            winners[objectID] = winner.envelope
        }
        return winners.keys.sorted().compactMap { winners[$0] }
    }

    public func acceptedOperationCount() throws -> Int { try load().receipts.count }

    public func pendingOperations() throws -> [AtlasVaultEncryptedPatchOperation] {
        let state = try load()
        let pending = Set(state.pendingOperationIDs)
        return state.operations.filter { pending.contains($0.operationID) }.sorted()
    }

    public func confirmRemoteAcceptance(_ operationID: String) throws {
        let operationID = try canonicalUUID(operationID)
        let state = try load()
        guard state.pendingOperationIDs.contains(operationID) else { throw invalid() }
        try write(
            operations: state.operations,
            snapshots: state.snapshots,
            pendingOperationIDs: state.pendingOperationIDs.filter { $0 != operationID }
        )
    }

    public func synchronize(
        to remote: AtlasVaultDurableEncryptedConvergentReplica
    ) throws -> Int {
        guard remote.collectionID == collectionID else { throw invalid() }
        var accepted = 0
        for operation in try pendingOperations() {
            _ = try remote.ingestRemote(operation)
            try confirmRemoteAcceptance(operation.operationID)
            accepted += 1
        }
        return accepted
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

private let commitmentFormat = "atlasvault-state-commitment"
private let zeroCommitmentRoot = String(repeating: "0", count: 64)
private let maximumCommitmentSequence: Int64 = 9_007_199_254_740_991

private func commitmentHex(_ value: Any?) throws -> String {
    guard let value = value as? String, value.utf8.count == 64,
          value.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) })
    else { throw invalid() }
    return value
}

private func commitmentSequence(_ value: Any?, allowZero: Bool = false) throws -> Int64 {
    guard let number = value as? NSNumber,
          CFGetTypeID(number) != CFBooleanGetTypeID(),
          ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(String(cString: number.objCType)),
          let sequence = Int64(number.stringValue),
          sequence >= (allowZero ? 0 : 1), sequence <= maximumCommitmentSequence
    else { throw invalid() }
    return sequence
}

private func commitmentStateDigest(_ bytes: Data) throws -> String {
    guard bytes.count >= 16, bytes.count <= maximumQueueBytes else { throw invalid() }
    return sha256Hex(bytes)
}

private func commitmentRoot(_ collection: String, _ sequence: Int64, _ previous: String, _ digest: String) -> String {
    sha256Hex(Data("atlasvault-state-commitment-v1\n\(collection)\n\(sequence)\n\(previous)\n\(digest)\n".utf8))
}

private func rootSignatureMessage(_ root: String) -> Data {
    let characters = Array(root)
    let bytes = stride(from: 0, to: characters.count, by: 2).map {
        UInt8(String(characters[$0...($0 + 1)]), radix: 16)!
    }
    return Data("atlasvault-state-root-signature-v1\0".utf8) + Data(bytes)
}

public struct AtlasVaultSignedStateCommitment {
    public let collectionID: String
    public let sequence: Int64
    public let previousRoot: String
    public let stateSHA256: String
    public let root: String
    private let signature: Data

    public init(jsonObject value: [String: Any]) throws {
        try exactKeys(value, ["format", "version", "collection_id", "sequence", "previous_root", "state_sha256", "root", "signature_b64"])
        guard value["format"] as? String == commitmentFormat,
              try commitmentSequence(value["version"]) == 1 else { throw invalid() }
        collectionID = try identifier(value["collection_id"])
        sequence = try commitmentSequence(value["sequence"])
        previousRoot = try commitmentHex(value["previous_root"])
        stateSHA256 = try commitmentHex(value["state_sha256"])
        root = try commitmentHex(value["root"])
        guard let encodedSignature = value["signature_b64"] as? String,
              encodedSignature.utf8.count == 88 else { throw invalid() }
        signature = try canonicalBase64(value["signature_b64"], exactLength: 64)
        guard (sequence == 1) == (previousRoot == zeroCommitmentRoot),
              root == commitmentRoot(collectionID, sequence, previousRoot, stateSHA256)
        else { throw invalid() }
    }

    public static func sign(_ opaqueState: Data, collectionID: String, sequence: Int64,
                            previousRoot: String, signingKey: Curve25519.Signing.PrivateKey) throws -> Self {
        let collection = try identifier(collectionID)
        let sequence = try commitmentSequence(sequence)
        let previous = try commitmentHex(previousRoot)
        let digest = try commitmentStateDigest(opaqueState)
        let root = commitmentRoot(collection, sequence, previous, digest)
        let signature = try signingKey.signature(for: rootSignatureMessage(root))
        return try Self(jsonObject: [
            "format": commitmentFormat, "version": 1, "collection_id": collection,
            "sequence": sequence, "previous_root": previous, "state_sha256": digest,
            "root": root, "signature_b64": signature.base64EncodedString(),
        ])
    }

    public var jsonObject: [String: Any] {
        ["format": commitmentFormat, "version": 1, "collection_id": collectionID,
         "sequence": sequence, "previous_root": previousRoot, "state_sha256": stateSHA256,
         "root": root, "signature_b64": signature.base64EncodedString()]
    }

    func verify(publicKey: Data) throws -> Bool {
        try Curve25519.Signing.PublicKey(rawRepresentation: publicKey)
            .isValidSignature(signature, for: rootSignatureMessage(root))
    }
}

/// Durable observation anchor. Each checkpoint path has one owning client.
public final class AtlasVaultRollbackTracker {
    private let store: EncryptedQueueFile
    private let collection: String
    private let publicKey: Data
    private let lock = NSLock()

    public init(fileURL: URL, encryptionKey: Data, collectionID: String, trustedSigner: Data) throws {
        guard trustedSigner.count == 32 else { throw invalid() }
        collection = try identifier(collectionID)
        publicKey = trustedSigner
        store = try EncryptedQueueFile(fileURL: fileURL, encryptionKey: encryptionKey, kind: "rollback-anchor")
    }

    private func anchor(_ sequence: Int64, _ root: String) -> [String: Any] {
        ["collection_id": collection, "signing_public_b64": publicKey.base64EncodedString(), "sequence": sequence, "root": root]
    }

    public func initialize() throws {
        lock.lock()
        defer { lock.unlock() }
        guard !FileManager.default.fileExists(atPath: store.fileURL.path) else { throw invalid() }
        try store.write(anchor(0, zeroCommitmentRoot))
    }

    private func load() throws -> [String: Any] {
        let state = try store.read(default: [:])
        try exactKeys(state, ["collection_id", "signing_public_b64", "sequence", "root"])
        let sequence = try commitmentSequence(state["sequence"], allowZero: true)
        let root = try commitmentHex(state["root"])
        guard state["collection_id"] as? String == collection,
              state["signing_public_b64"] as? String == publicKey.base64EncodedString(),
              (sequence == 0) == (root == zeroCommitmentRoot)
        else { throw invalid() }
        return anchor(sequence, root)
    }

    public func checkpoint() throws -> [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        return try load()
    }

    public func accept(jsonObject served: [String: Any], opaqueState: Data) throws -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let state = try load()
        let commitment = try AtlasVaultSignedStateCommitment(jsonObject: served)
        guard commitment.collectionID == collection,
              commitment.stateSHA256 == (try commitmentStateDigest(opaqueState)),
              try commitment.verify(publicKey: publicKey)
        else { throw invalid() }
        let sequence = try commitmentSequence(state["sequence"], allowZero: true)
        let root = try commitmentHex(state["root"])
        if commitment.sequence == sequence, commitment.root == root { return false }
        guard commitment.sequence == sequence + 1, commitment.previousRoot == root else { throw invalid() }
        try store.write(anchor(commitment.sequence, commitment.root))
        return true
    }
}
