import CryptoKit
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultCryptoVectorTests: XCTestCase {
    func testSwiftCryptoKitDerivesPythonRecordKey() throws {
        let root = try loadCryptoVectorRoot()

        for vector in try vectors(in: root) {
            let vaultKey = try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64"))
            let vault = try dictionary(vector["vault"], context: "vault")
            let record = try dictionary(vector["record"], context: "record")
            let vaultID = try string(vault["vault_id"], context: "vault.vault_id")
            let recordID = try string(record["id"], context: "record.id")
            let derived = deriveRecordKey(vaultKey: vaultKey, vaultID: vaultID, recordID: recordID)

            XCTAssertEqual(derived.base64EncodedString(), try string(vector["record_key_b64"], context: "record_key_b64"))
        }
    }

    func testSwiftCryptoKitDecryptsPythonGeneratedCiphertext() throws {
        let cryptoRoot = try loadCryptoVectorRoot()
        let payloadRoot = try loadPayloadVectorRoot()
        let payloads = try dictionary(payloadRoot["payloads"], context: "payloads")

        for vector in try vectors(in: cryptoRoot) {
            let vaultKey = try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64"))
            let vault = try dictionary(vector["vault"], context: "vault")
            let record = try dictionary(vector["record"], context: "record")
            let recordKey = deriveRecordKey(
                vaultKey: vaultKey,
                vaultID: try string(vault["vault_id"], context: "vault.vault_id"),
                recordID: try string(record["id"], context: "record.id")
            )
            let aad = try aadData(vault: vault, record: record)
            let sealedBox = try sealedBox(record: record)

            let plaintext = try AES.GCM.open(sealedBox, using: SymmetricKey(data: recordKey), authenticating: aad)
            let decryptedObject = try JSONSerialization.jsonObject(with: plaintext)
            let sourcePayloadName = try string(vector["source_payload_vector"], context: "source_payload_vector")
            let expectedObject = try XCTUnwrap(payloads[sourcePayloadName], sourcePayloadName)

            try assertJSONObjectsEqual(decryptedObject, expectedObject)
        }
    }

    func testSwiftCryptoKitSealsCanonicalPlaintextToPythonCiphertext() throws {
        let root = try loadCryptoVectorRoot()

        for vector in try vectors(in: root) {
            let vaultKey = try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64"))
            let vault = try dictionary(vector["vault"], context: "vault")
            let record = try dictionary(vector["record"], context: "record")
            let recordKey = deriveRecordKey(
                vaultKey: vaultKey,
                vaultID: try string(vault["vault_id"], context: "vault.vault_id"),
                recordID: try string(record["id"], context: "record.id")
            )
            let nonce = try AES.GCM.Nonce(data: data(base64: string(record["nonce"], context: "record.nonce")))
            let plaintext = try data(
                base64: string(vector["plaintext_json_b64"], context: "plaintext_json_b64")
            )
            let sealed = try AES.GCM.seal(
                plaintext,
                using: SymmetricKey(data: recordKey),
                nonce: nonce,
                authenticating: aadData(vault: vault, record: record)
            )
            let combinedCiphertextAndTag = sealed.ciphertext + sealed.tag

            XCTAssertEqual(
                combinedCiphertextAndTag.base64EncodedString(),
                try string(record["ciphertext"], context: "record.ciphertext")
            )
        }
    }

    func testCanonicalPlaintextBytesMatchSourcePayloadVector() throws {
        let cryptoRoot = try loadCryptoVectorRoot()
        let payloadRoot = try loadPayloadVectorRoot()
        let payloads = try dictionary(payloadRoot["payloads"], context: "payloads")

        for vector in try vectors(in: cryptoRoot) {
            let sourcePayloadName = try string(vector["source_payload_vector"], context: "source_payload_vector")
            let expectedPayload = try XCTUnwrap(payloads[sourcePayloadName], sourcePayloadName)
            let plaintext = try data(
                base64: string(vector["plaintext_json_b64"], context: "plaintext_json_b64")
            )
            let plaintextObject = try JSONSerialization.jsonObject(with: plaintext)

            try assertJSONObjectsEqual(plaintextObject, expectedPayload)
        }
    }

    func testAADBytesMatchStableJSON() throws {
        let root = try loadCryptoVectorRoot()

        for vector in try vectors(in: root) {
            let vault = try dictionary(vector["vault"], context: "vault")
            let record = try dictionary(vector["record"], context: "record")
            let aadJSON = try aadJSONObject(vault: vault, record: record)
            let aadData = try aadData(vault: vault, record: record)

            try assertJSONObjectsEqual(aadJSON, try dictionary(vector["aad_json"], context: "aad_json"))
            XCTAssertEqual(aadData.base64EncodedString(), try string(vector["aad_b64"], context: "aad_b64"))
        }
    }

    func testEncryptedRecordMetadataDoesNotExposeRecordTypeOrPrivateSentinels() throws {
        let root = try loadCryptoVectorRoot()
        let allowlist = Set(try stringArray(
            root["encrypted_record_plaintext_metadata_allowlist"],
            context: "encrypted_record_plaintext_metadata_allowlist"
        ))

        for vector in try vectors(in: root) {
            let record = try dictionary(vector["record"], context: "record")
            let serialized = try String(
                data: JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]),
                encoding: .utf8
            ).unwrap(context: "serialized record")

            XCTAssertEqual(Set(record.keys), allowlist)
            XCTAssertFalse(record.keys.contains("type"))
            XCTAssertFalse(record.keys.contains("payload"))
            for forbidden in try stringArray(
                vector["forbidden_plaintext_strings"],
                context: "forbidden_plaintext_strings"
            ) {
                XCTAssertFalse(serialized.contains(forbidden), forbidden)
            }
        }
    }

    func testCryptoVectorsAreFakeAndTestOnly() throws {
        let root = try loadCryptoVectorRoot()

        XCTAssertEqual(try string(root["format"], context: "format"), "atlasvault-crypto-vectors")
        XCTAssertEqual(try int(root["version"], context: "version"), 1)
        XCTAssertTrue(try string(root["description"], context: "description").contains("Fake test-only"))
        XCTAssertTrue(try string(root["warning"], context: "warning").contains("TEST ONLY"))
        for vector in try vectors(in: root) {
            XCTAssertEqual(
                try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64")).count,
                32
            )
            XCTAssertFalse(try data(
                base64: string(vector["plaintext_json_b64"], context: "plaintext_json_b64")
            ).isEmpty)
            let record = try dictionary(vector["record"], context: "record")
            XCTAssertEqual(try data(base64: string(record["nonce"], context: "record.nonce")).count, 12)
        }
    }

    private func deriveRecordKey(vaultKey: Data, vaultID: String, recordID: String) -> Data {
        // Test-only compatibility derivation. Production vault access belongs to a later phase.
        let inputKey = SymmetricKey(data: vaultKey)
        let salt = Data("atlas-vault:v1:\(vaultID)".utf8)
        let info = Data("record:\(recordID)".utf8)
        let derived = HKDF<SHA256>.deriveKey(
            inputKeyMaterial: inputKey,
            salt: salt,
            info: info,
            outputByteCount: 32
        )
        return derived.withUnsafeBytes { Data($0) }
    }

    private func aadJSONObject(vault: [String: Any], record: [String: Any]) throws -> [String: Any] {
        [
            "deleted": try bool(record["deleted"], context: "record.deleted"),
            "key_id": try string(record["key_id"], context: "record.key_id"),
            "parent_revision": try nullableString(record["parent_revision"], context: "record.parent_revision"),
            "record_id": try string(record["id"], context: "record.id"),
            "record_schema_version": try int(record["schema_version"], context: "record.schema_version"),
            "revision": try string(record["revision"], context: "record.revision"),
            "vault_format": try string(vault["format"], context: "vault.format"),
            "vault_id": try string(vault["vault_id"], context: "vault.vault_id"),
            "vault_version": try int(vault["version"], context: "vault.version"),
        ]
    }

    private func aadData(vault: [String: Any], record: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: aadJSONObject(vault: vault, record: record), options: [.sortedKeys])
    }

    private func sealedBox(record: [String: Any]) throws -> AES.GCM.SealedBox {
        let nonceData = try data(base64: string(record["nonce"], context: "record.nonce"))
        let combinedCiphertextAndTag = try data(base64: string(record["ciphertext"], context: "record.ciphertext"))
        guard combinedCiphertextAndTag.count > 16 else {
            throw testError("record.ciphertext must contain ciphertext plus a 16-byte tag")
        }
        let ciphertext = Data(combinedCiphertextAndTag.dropLast(16))
        let tag = Data(combinedCiphertextAndTag.suffix(16))
        return try AES.GCM.SealedBox(
            nonce: AES.GCM.Nonce(data: nonceData),
            ciphertext: ciphertext,
            tag: tag
        )
    }

    private func loadCryptoVectorRoot() throws -> [String: Any] {
        try loadJSONRoot(fileName: "atlasvault_crypto_vectors_v1.json")
    }

    private func loadPayloadVectorRoot() throws -> [String: Any] {
        try loadJSONRoot(fileName: "atlasvault_payload_vectors_v1.json")
    }

    private func loadJSONRoot(fileName: String) throws -> [String: Any] {
        let data = try Data(contentsOf: vectorFileURL(fileName: fileName))
        let object = try JSONSerialization.jsonObject(with: data)
        return try dictionary(object, context: fileName)
    }

    private func vectorFileURL(fileName: String) throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            currentDirectory.appendingPathComponent("../../contracts/sync/test_vectors/\(fileName)"),
            currentDirectory.appendingPathComponent("contracts/sync/test_vectors/\(fileName)"),
            sourceDirectory.appendingPathComponent("../../../../contracts/sync/test_vectors/\(fileName)"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find shared AtlasVault vector file \(fileName)")
    }

    private func vectors(in root: [String: Any]) throws -> [[String: Any]] {
        guard let vectors = root["vectors"] as? [[String: Any]], !vectors.isEmpty else {
            throw testError("vectors must be a non-empty object array")
        }
        return vectors
    }

    private func dictionary(_ value: Any?, context: String) throws -> [String: Any] {
        guard let dictionary = value as? [String: Any] else {
            throw testError("\(context) must be an object")
        }
        return dictionary
    }

    private func stringArray(_ value: Any?, context: String) throws -> [String] {
        guard let array = value as? [String] else {
            throw testError("\(context) must be a string array")
        }
        return array
    }

    private func string(_ value: Any?, context: String) throws -> String {
        guard let string = value as? String else {
            throw testError("\(context) must be text")
        }
        return string
    }

    private func int(_ value: Any?, context: String) throws -> Int {
        guard let int = value as? Int else {
            throw testError("\(context) must be an integer")
        }
        return int
    }

    private func bool(_ value: Any?, context: String) throws -> Bool {
        guard let bool = value as? Bool else {
            throw testError("\(context) must be a boolean")
        }
        return bool
    }

    private func nullableString(_ value: Any?, context: String) throws -> Any {
        if value == nil || value is NSNull {
            return NSNull()
        }
        return try string(value, context: context)
    }

    private func data(base64 value: String) throws -> Data {
        guard let data = Data(base64Encoded: value) else {
            throw testError("value must be base64")
        }
        return data
    }

    private func assertJSONObjectsEqual(
        _ lhs: Any,
        _ rhs: Any,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        let lhsData = try JSONSerialization.data(withJSONObject: lhs, options: [.sortedKeys])
        let rhsData = try JSONSerialization.data(withJSONObject: rhs, options: [.sortedKeys])
        XCTAssertEqual(
            String(data: lhsData, encoding: .utf8),
            String(data: rhsData, encoding: .utf8),
            file: file,
            line: line
        )
    }

    private func testError(_ message: String) -> NSError {
        NSError(
            domain: "AtlasVaultCryptoVectorTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}

private extension Optional where Wrapped == String {
    func unwrap(context: String) throws -> String {
        guard let value = self else {
            throw NSError(
                domain: "AtlasVaultCryptoVectorTests",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey: "\(context) must be UTF-8 text"]
            )
        }
        return value
    }
}
