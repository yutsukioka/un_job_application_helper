import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultWrappedKeyVectorTests: XCTestCase {
    func testRecoveryWrapV2VectorAndVersionedModelSurfaceExists() throws {
        let source = try String(
            contentsOf: Self.appleRoot()
                .appendingPathComponent(
                    "Sources/AtlasUI/AtlasVaultWrappedKeyModels.swift"
                ),
            encoding: .utf8
        )
        let vector = Self.repositoryRoot()
            .appendingPathComponent(
                "contracts/sync/test_vectors/"
                    + "atlasvault_recovery_export_vectors_v2.json"
            )

        XCTAssertTrue(
            source.contains("AtlasVaultRecoveryWrappedKeyEnvelope")
        )
        XCTAssertTrue(
            source.contains("AtlasVaultVersionedWrappedKey")
        )
        XCTAssertTrue(
            source.contains("wrap_version")
        )
        XCTAssertTrue(
            source.contains("recovery_key")
        )
        XCTAssertTrue(FileManager.default.fileExists(atPath: vector.path))
    }

    func testVersionedMetadataDecodesRecoveryV2AndReencodesCanonically()
        throws
    {
        let vector = try loadRecoveryVector()
        let metadataObject = try dictionary(vector["vault_metadata"])
        let data = try JSONSerialization.data(
            withJSONObject: metadataObject,
            options: [.sortedKeys]
        )

        let metadata = try JSONDecoder().decode(
            AtlasVaultVersionedWrappedKeyMetadata.self,
            from: data
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let reencoded = try encoder.encode(metadata)

        XCTAssertEqual(metadata.format, "atlas-vault")
        XCTAssertEqual(metadata.version, 1)
        XCTAssertEqual(metadata.keyWraps.count, 1)
        XCTAssertEqual(
            metadata.recoveryKeyWrap?.id,
            "primary-recovery-v2"
        )
        XCTAssertEqual(
            try JSONSerialization.jsonObject(with: reencoded)
                as? NSDictionary,
            metadataObject as NSDictionary
        )
    }

    func testVersionedMetadataAllowsV1AndV2ToCoexist() throws {
        let v1Root = try loadVectorJSONObject()
        let v1Vector = try dictionary(
            try array(v1Root["vectors"]).first
        )
        let v1Metadata = try dictionary(v1Vector["vault_metadata"])
        let v1 = try dictionary(
            try array(v1Metadata["key_wraps"]).first
        )
        let recovery = try loadRecoveryVector()
        var metadata = try dictionary(recovery["vault_metadata"])
        metadata["key_wraps"] = [
            v1,
            try dictionary(recovery["recovery_wrap"]),
        ]
        let data = try JSONSerialization.data(
            withJSONObject: metadata,
            options: [.sortedKeys]
        )

        let decoded = try JSONDecoder().decode(
            AtlasVaultVersionedWrappedKeyMetadata.self,
            from: data
        )

        XCTAssertEqual(decoded.keyWraps.count, 2)
        guard case .passphrase = decoded.keyWraps[0] else {
            return XCTFail("Expected preserved v1 passphrase wrap")
        }
        guard case .recoveryKey = decoded.keyWraps[1] else {
            return XCTFail("Expected recovery v2 wrap")
        }
    }

    func testVersionedMetadataRejectsDuplicateRecoveryWrapID() throws {
        let vector = try loadRecoveryVector()
        var metadata = try dictionary(vector["vault_metadata"])
        let wrap = try dictionary(vector["recovery_wrap"])
        metadata["key_wraps"] = [wrap, wrap]
        let data = try JSONSerialization.data(
            withJSONObject: metadata,
            options: [.sortedKeys]
        )

        XCTAssertThrowsError(
            try JSONDecoder().decode(
                AtlasVaultVersionedWrappedKeyMetadata.self,
                from: data
            )
        )
    }

    func testSharedVectorDecodesThroughStrictProductionModels() throws {
        let root = try loadVectorRoot()
        let vector = try XCTUnwrap(root.vectors.first)
        let metadata = vector.vaultMetadata
        let wrapped = try XCTUnwrap(metadata.keyWraps.first)

        XCTAssertEqual(root.format, "atlasvault-key-wrap-vectors")
        XCTAssertEqual(root.version, 1)
        XCTAssertTrue(root.description.contains("Fake test-only"))
        XCTAssertTrue(root.warning.contains("TEST ONLY"))
        XCTAssertTrue(root.warning.contains("Not real user data"))
        XCTAssertTrue(root.warning.contains("Not a production vault"))
        XCTAssertTrue(root.warning.contains("Not a production key"))
        XCTAssertTrue(vector.testOnly)

        XCTAssertEqual(metadata.format, "atlas-vault")
        XCTAssertEqual(metadata.version, 1)
        XCTAssertEqual(metadata.vaultID, "00000000-0000-4000-8000-000000000500")
        XCTAssertEqual(metadata.crypto.keyWrapAEAD, "AES-256-GCM")
        XCTAssertEqual(metadata.keyWraps.count, 1)
        XCTAssertEqual(wrapped.id, "primary-passphrase")
        XCTAssertEqual(wrapped.type, "passphrase")
        XCTAssertEqual(wrapped.kdf.algorithm, "Argon2id")
        XCTAssertEqual(wrapped.kdf.salt.count, 16)
        XCTAssertEqual(wrapped.kdf.memoryKiB, 1_024)
        XCTAssertEqual(wrapped.kdf.iterations, 2)
        XCTAssertEqual(wrapped.kdf.parallelism, 1)
        XCTAssertEqual(wrapped.nonce.count, 12)
        XCTAssertEqual(wrapped.ciphertext.count, 48)
        XCTAssertFalse(vector.testOnlyInputUTF8.isEmpty)
        XCTAssertFalse(vector.wrongTestOnlyInputUTF8.isEmpty)
        XCTAssertEqual(try strictBase64(vector.testOnlyVaultKeyBase64).count, 32)
    }

    func testContextKeepsVaultRoutingExplicitButDescriptionsRedacted() throws {
        let metadata = try XCTUnwrap(loadVectorRoot().vectors.first?.vaultMetadata)
        let wrapped = try XCTUnwrap(metadata.keyWraps.first)
        let context = try AtlasVaultKeyUnwrapContext(
            vaultID: metadata.vaultID,
            wrappedKey: wrapped
        )

        XCTAssertEqual(context.vaultID, metadata.vaultID)
        XCTAssertEqual(context.wrappedKey, wrapped)
        XCTAssertEqual(context.description, "AtlasVaultKeyUnwrapContext(<redacted>)")
        XCTAssertEqual(context.debugDescription, context.description)
        XCTAssertFalse(context.description.contains(metadata.vaultID))
        XCTAssertFalse(context.description.contains(wrapped.id))
    }

    func testContextRejectsMissingVaultID() throws {
        let wrapped = try XCTUnwrap(loadVectorRoot().vectors.first?.vaultMetadata.keyWraps.first)

        XCTAssertThrowsError(
            try AtlasVaultKeyUnwrapContext(vaultID: "", wrappedKey: wrapped)
        ) { error in
            XCTAssertEqual(error as? AtlasVaultKeyUnwrapError, .invalidContext)
        }
    }

    func testV1KeyWrapAADExplicitlyExcludesVaultID() throws {
        let root = try loadVectorJSONObject()
        let vector = try dictionary(try array(root["vectors"]).first)
        let metadata = try dictionary(vector["vault_metadata"])
        let aad = try dictionary(vector["key_wrap_aad_json"])
        let vaultID = try string(metadata["vault_id"])
        let aadJSON = try stableJSON(aad)

        XCTAssertNil(aad["vault_id"])
        XCTAssertFalse(aadJSON.contains(vaultID))
        XCTAssertEqual(
            Data(aadJSON.utf8).base64EncodedString(),
            try string(vector["key_wrap_aad_b64"])
        )
    }

    func testWrappedMetadataDoesNotContainFakeSecretOrRawKey() throws {
        let root = try loadVectorJSONObject()
        let vector = try dictionary(try array(root["vectors"]).first)
        let metadata = try dictionary(vector["vault_metadata"])
        let serialized = try stableJSON(metadata)
        let passphrase = try String(
            data: Data(try byteArray(vector["test_only_input_utf8"])),
            encoding: .utf8
        ).unwrap()
        let rawKey = try strictBase64(try string(vector["test_only_vault_key_b64"]))

        XCTAssertFalse(serialized.contains(passphrase))
        XCTAssertFalse(serialized.contains(rawKey.base64EncodedString()))
        XCTAssertFalse(serialized.contains(rawKey.map { String(format: "%02x", $0) }.joined()))
    }

    func testProductionWrappedKeyModelsAreDecodeOnlyAndSendable() {
        assertSendable(AtlasVaultArgon2idParameters.self)
        assertSendable(AtlasVaultKeyWrapCryptoSuite.self)
        assertSendable(AtlasVaultWrappedKeyEnvelope.self)
        assertSendable(AtlasVaultWrappedKeyMetadata.self)
        XCTAssertFalse(AtlasVaultWrappedKeyMetadata.self is any Encodable.Type)
        XCTAssertFalse(AtlasVaultWrappedKeyEnvelope.self is any Encodable.Type)
    }

    func testDescriptionsDoNotExposeMetadataValues() throws {
        let metadata = try XCTUnwrap(loadVectorRoot().vectors.first?.vaultMetadata)
        let wrapped = try XCTUnwrap(metadata.keyWraps.first)
        let sentinel = "FAKE_SECRET_VECTOR_SENTINEL_DO_NOT_LEAK"

        for value in [
            metadata.description,
            metadata.debugDescription,
            wrapped.description,
            wrapped.debugDescription,
            wrapped.kdf.description,
            wrapped.kdf.debugDescription,
        ] {
            XCTAssertFalse(value.contains(sentinel))
            XCTAssertFalse(value.contains(metadata.vaultID))
            XCTAssertFalse(value.contains(wrapped.id))
            XCTAssertFalse(value.contains(wrapped.ciphertext.base64EncodedString()))
        }
    }

    func testRejectsUnsupportedOuterVectorVersion() throws {
        var root = try loadVectorJSONObject()
        root["version"] = 2

        XCTAssertThrowsError(try decodeVectorRoot(root)) { error in
            XCTAssertEqual(error as? KeyWrapVectorTestError, .unsupportedVector)
        }
    }

    func testRejectsUnsupportedVaultVersion() throws {
        try assertMetadataMutationRejected(path: [.key("version")], value: 2)
    }

    func testRejectsUnsupportedKeyWrapType() throws {
        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("type")],
            value: "recovery"
        )
    }

    func testRejectsUnsupportedKDF() throws {
        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("kdf"), .key("algorithm")],
            value: "PBKDF2"
        )
    }

    func testRejectsUnsupportedKeyWrapAEAD() throws {
        try assertMetadataMutationRejected(
            path: [.key("crypto"), .key("key_wrap_aead")],
            value: "AES-CBC"
        )
    }

    func testRejectsMalformedBase64() throws {
        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("kdf"), .key("salt")],
            value: "not-base64"
        )
    }

    func testRejectsNonCanonicalBase64Padding() throws {
        let root = try loadVectorJSONObject()
        let vector = try dictionary(try array(root["vectors"]).first)
        let metadata = try dictionary(vector["vault_metadata"])
        let wrapped = try dictionary(try array(metadata["key_wraps"]).first)
        let kdf = try dictionary(wrapped["kdf"])

        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("kdf"), .key("salt")],
            value: try string(kdf["salt"]) + "="
        )
        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("nonce")],
            value: try string(wrapped["nonce"]) + "="
        )
        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("ciphertext")],
            value: try string(wrapped["ciphertext"]) + "="
        )
    }

    func testRejectsInvalidSaltLength() throws {
        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("kdf"), .key("salt")],
            value: Data(repeating: 1, count: 15).base64EncodedString()
        )
    }

    func testRejectsInvalidNonceLength() throws {
        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("nonce")],
            value: Data(repeating: 1, count: 11).base64EncodedString()
        )
    }

    func testRejectsInvalidCiphertextLayout() throws {
        try assertMetadataMutationRejected(
            path: [.key("key_wraps"), .index(0), .key("ciphertext")],
            value: Data(repeating: 1, count: 47).base64EncodedString()
        )
    }

    private func assertMetadataMutationRejected(path: [JSONPathComponent], value: Any) throws {
        let root = try loadVectorJSONObject()
        let vector = try dictionary(try array(root["vectors"]).first)
        var metadata = try dictionary(vector["vault_metadata"])
        metadata = try replacing(value, at: path[...], in: metadata) as! [String: Any]
        let data = try JSONSerialization.data(withJSONObject: metadata, options: [.sortedKeys])

        XCTAssertThrowsError(try JSONDecoder().decode(AtlasVaultWrappedKeyMetadata.self, from: data))
    }

    private func replacing(
        _ replacement: Any,
        at path: ArraySlice<JSONPathComponent>,
        in value: Any
    ) throws -> Any {
        guard let component = path.first else { return replacement }
        switch component {
        case let .key(key):
            var object = try dictionary(value)
            object[key] = try replacing(
                replacement,
                at: path.dropFirst(),
                in: object[key] as Any
            )
            return object
        case let .index(index):
            var values = try array(value)
            values[index] = try replacing(
                replacement,
                at: path.dropFirst(),
                in: values[index]
            )
            return values
        }
    }

    private func loadVectorRoot() throws -> KeyWrapVectorRoot {
        try decodeVectorRoot(loadVectorJSONObject())
    }

    private func decodeVectorRoot(_ object: [String: Any]) throws -> KeyWrapVectorRoot {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        let root = try JSONDecoder().decode(KeyWrapVectorRoot.self, from: data)
        guard root.format == "atlasvault-key-wrap-vectors", root.version == 1 else {
            throw KeyWrapVectorTestError.unsupportedVector
        }
        guard root.suite.kdf == "Argon2id", root.suite.keyWrapAEAD == "AES-256-GCM" else {
            throw KeyWrapVectorTestError.unsupportedVector
        }
        return root
    }

    private func loadVectorJSONObject() throws -> [String: Any] {
        let data = try Data(contentsOf: vectorFileURL())
        return try dictionary(try JSONSerialization.jsonObject(with: data))
    }

    private func loadRecoveryVector() throws -> [String: Any] {
        let data = try Data(
            contentsOf: recoveryVectorFileURL()
        )
        let root = try dictionary(
            try JSONSerialization.jsonObject(with: data)
        )
        return try dictionary(try array(root["vectors"]).first)
    }

    private func recoveryVectorFileURL() throws -> URL {
        let currentDirectory = URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath
        )
        let sourceDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let fileName =
            "atlasvault_recovery_export_vectors_v2.json"
        let candidates = [
            currentDirectory.appendingPathComponent(
                "../../contracts/sync/test_vectors/\(fileName)"
            ),
            currentDirectory.appendingPathComponent(
                "contracts/sync/test_vectors/\(fileName)"
            ),
            sourceDirectory.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/\(fileName)"
            ),
        ].map(\.standardizedFileURL)
        return try XCTUnwrap(
            candidates.first {
                FileManager.default.fileExists(atPath: $0.path)
            }
        )
    }

    private func vectorFileURL() throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let fileName = "atlasvault_key_wrap_vectors_v1.json"
        let candidates = [
            currentDirectory.appendingPathComponent("../../contracts/sync/test_vectors/\(fileName)"),
            currentDirectory.appendingPathComponent("contracts/sync/test_vectors/\(fileName)"),
            sourceDirectory.appendingPathComponent("../../../../contracts/sync/test_vectors/\(fileName)"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw KeyWrapVectorTestError.vectorMissing
    }

    private static func appleRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private static func repositoryRoot() -> URL {
        appleRoot()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private func stableJSON(_ value: Any) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        return try String(data: data, encoding: .utf8).unwrap()
    }

    private func strictBase64(_ value: String) throws -> Data {
        guard let data = Data(base64Encoded: value, options: []) else {
            throw KeyWrapVectorTestError.invalidFixture
        }
        return data
    }

    private func dictionary(_ value: Any?) throws -> [String: Any] {
        guard let dictionary = value as? [String: Any] else {
            throw KeyWrapVectorTestError.invalidFixture
        }
        return dictionary
    }

    private func array(_ value: Any?) throws -> [Any] {
        guard let array = value as? [Any] else {
            throw KeyWrapVectorTestError.invalidFixture
        }
        return array
    }

    private func string(_ value: Any?) throws -> String {
        guard let string = value as? String else {
            throw KeyWrapVectorTestError.invalidFixture
        }
        return string
    }

    private func byteArray(_ value: Any?) throws -> [UInt8] {
        guard let values = value as? [Any], !values.isEmpty else {
            throw KeyWrapVectorTestError.invalidFixture
        }
        return try values.map { value in
            guard let number = value as? NSNumber else {
                throw KeyWrapVectorTestError.invalidFixture
            }
            let integer = number.intValue
            guard integer >= 0, integer <= 255, NSNumber(value: integer) == number else {
                throw KeyWrapVectorTestError.invalidFixture
            }
            return UInt8(integer)
        }
    }

    private func assertSendable<T: Sendable>(_ type: T.Type) {}
}

private enum JSONPathComponent {
    case key(String)
    case index(Int)
}

private struct KeyWrapVectorRoot: Decodable {
    let format: String
    let version: Int
    let description: String
    let warning: String
    let suite: KeyWrapVectorSuite
    let vectors: [KeyWrapVector]
}

private struct KeyWrapVectorSuite: Decodable {
    let kdf: String
    let keyWrapAEAD: String

    enum CodingKeys: String, CodingKey {
        case kdf
        case keyWrapAEAD = "key_wrap_aead"
    }
}

private struct KeyWrapVector: Decodable {
    let name: String
    let testOnly: Bool
    let testOnlyInputUTF8: [UInt8]
    let wrongTestOnlyInputUTF8: [UInt8]
    let testOnlyVaultKeyBase64: String
    let vaultMetadata: AtlasVaultWrappedKeyMetadata
    let keyWrapAADBase64: String

    enum CodingKeys: String, CodingKey {
        case name
        case testOnly = "test_only"
        case testOnlyInputUTF8 = "test_only_input_utf8"
        case wrongTestOnlyInputUTF8 = "wrong_test_only_input_utf8"
        case testOnlyVaultKeyBase64 = "test_only_vault_key_b64"
        case vaultMetadata = "vault_metadata"
        case keyWrapAADBase64 = "key_wrap_aad_b64"
    }
}

private enum KeyWrapVectorTestError: Error, Equatable {
    case unsupportedVector
    case vectorMissing
    case invalidFixture
}

private extension Optional {
    func unwrap() throws -> Wrapped {
        guard let value = self else {
            throw KeyWrapVectorTestError.invalidFixture
        }
        return value
    }
}
