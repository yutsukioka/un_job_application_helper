import CryptoKit
import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRecordCryptoTests: XCTestCase {
    func testDerivesRecordKeyFromSharedVector() throws {
        for vector in try vectors() {
            let record = try recordEnvelope(vector)
            let vault = try dictionary(vector["vault"], context: "vault")
            let vaultKey = try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64"))
            let derived = try AtlasVaultRecordCrypto.deriveRecordKey(
                vaultKey: vaultKey,
                vaultID: string(vault["vault_id"], context: "vault.vault_id"),
                recordID: record.id
            )

            XCTAssertEqual(symmetricKeyData(derived).base64EncodedString(), try string(
                vector["record_key_b64"],
                context: "record_key_b64"
            ))
        }
    }

    func testCustomVaultFormatFeedsKeyDerivationAndAADConsistently() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let vault = try dictionary(vector["vault"], context: "vault")
        let vaultKey = try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64"))
        let vaultID = try string(vault["vault_id"], context: "vault.vault_id")
        let customVaultFormat = "atlas-vault-test-format"
        let plaintext = Data("test-only plaintext".utf8)
        let sealed = try AtlasVaultRecordCrypto.seal(
            plaintext: plaintext,
            vaultKey: vaultKey,
            vaultID: vaultID,
            record: record,
            vaultFormat: customVaultFormat
        )

        XCTAssertNotEqual(
            symmetricKeyData(try AtlasVaultRecordCrypto.deriveRecordKey(
                vaultKey: vaultKey,
                vaultID: vaultID,
                recordID: record.id
            )),
            symmetricKeyData(try AtlasVaultRecordCrypto.deriveRecordKey(
                vaultKey: vaultKey,
                vaultID: vaultID,
                recordID: record.id,
                vaultFormat: customVaultFormat
            ))
        )
        XCTAssertEqual(
            try AtlasVaultRecordCrypto.open(
                record: sealed,
                vaultKey: vaultKey,
                vaultID: vaultID,
                vaultFormat: customVaultFormat
            ),
            plaintext
        )
        try assertOpenFailsAuthentication(record: sealed, vector: vector)
    }

    func testBuildsStableAADFromSharedVector() throws {
        for vector in try vectors() {
            let record = try recordEnvelope(vector)
            let vault = try dictionary(vector["vault"], context: "vault")
            let aad = try AtlasVaultRecordAAD.data(
                vaultID: string(vault["vault_id"], context: "vault.vault_id"),
                record: record,
                vaultFormat: string(vault["format"], context: "vault.format"),
                vaultVersion: int(vault["version"], context: "vault.version")
            )

            XCTAssertEqual(aad.base64EncodedString(), try string(vector["aad_b64"], context: "aad_b64"))
            try assertJSONObjectsEqual(
                JSONSerialization.jsonObject(with: aad),
                dictionary(vector["aad_json"], context: "aad_json")
            )
        }
    }

    func testStableAADUsesPythonJSONRulesForSlashAndNonASCII() throws {
        let record = AtlasVaultEncryptedRecordEnvelope(
            id: "record/\u{00E9}",
            schemaVersion: 1,
            revision: "revision/\u{00E9}",
            parentRevision: nil,
            deleted: false,
            keyID: "recovery/key-\u{00E9}",
            nonce: Data(repeating: 1, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString(),
            ciphertext: Data(repeating: 2, count: AtlasVaultRecordCrypto.gcmTagByteCount + 1).base64EncodedString()
        )
        let aad = try AtlasVaultRecordAAD.data(vaultID: "vault/\u{00E9}", record: record)
        let aadText = try XCTUnwrap(String(data: aad, encoding: .utf8))

        XCTAssertEqual(
            aadText,
            #"{"deleted":false,"key_id":"recovery/key-\u00e9","parent_revision":null,"record_id":"record/\u00e9","record_schema_version":1,"revision":"revision/\u00e9","vault_format":"atlas-vault","vault_id":"vault/\u00e9","vault_version":1}"#
        )
        XCTAssertFalse(aadText.contains("\\/"))
        XCTAssertFalse(aadText.contains("\u{00E9}"))
    }

    func testOpensPythonGeneratedEncryptedRecord() throws {
        for vector in try vectors() {
            let record = try recordEnvelope(vector)
            let vault = try dictionary(vector["vault"], context: "vault")
            let vaultKey = try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64"))
            let plaintext = try AtlasVaultRecordCrypto.open(
                record: record,
                vaultKey: vaultKey,
                vaultID: string(vault["vault_id"], context: "vault.vault_id"),
                vaultFormat: string(vault["format"], context: "vault.format"),
                vaultVersion: int(vault["version"], context: "vault.version")
            )

            XCTAssertEqual(
                plaintext,
                try data(base64: string(vector["plaintext_json_b64"], context: "plaintext_json_b64"))
            )
        }
    }

    func testSealsPlaintextToPythonCiphertextAndTag() throws {
        for vector in try vectors() {
            let record = try recordEnvelope(vector)
            let vault = try dictionary(vector["vault"], context: "vault")
            let vaultKey = try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64"))
            let plaintext = try data(base64: string(vector["plaintext_json_b64"], context: "plaintext_json_b64"))
            let sealed = try AtlasVaultRecordCrypto.seal(
                plaintext: plaintext,
                vaultKey: vaultKey,
                vaultID: string(vault["vault_id"], context: "vault.vault_id"),
                record: record,
                vaultFormat: string(vault["format"], context: "vault.format"),
                vaultVersion: int(vault["version"], context: "vault.version")
            )

            XCTAssertEqual(sealed.ciphertext, record.ciphertext)
            XCTAssertEqual(sealed.nonce, record.nonce)
            XCTAssertEqual(sealed.id, record.id)
            XCTAssertEqual(sealed.revision, record.revision)
        }
    }

    func testWrongAADFailsAuthentication() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let tamperedAADRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: "\(record.revision)-tampered",
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: record.nonce,
            ciphertext: record.ciphertext
        )

        try assertOpenFailsAuthentication(record: tamperedAADRecord, vector: vector)
    }

    func testWrongNonceFailsAuthentication() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let wrongNonceRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: Data(repeating: 0, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString(),
            ciphertext: record.ciphertext
        )

        try assertOpenFailsAuthentication(record: wrongNonceRecord, vector: vector)
    }

    func testTamperedCiphertextFailsAuthentication() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        var ciphertext = try data(base64: record.ciphertext)
        ciphertext[0] ^= 0x01
        let tamperedRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: record.nonce,
            ciphertext: ciphertext.base64EncodedString()
        )

        try assertOpenFailsAuthentication(record: tamperedRecord, vector: vector)
    }

    func testEmptyPlaintextRoundTripAllowsTagOnlyCiphertext() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let vault = try dictionary(vector["vault"], context: "vault")
        let vaultKey = try data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64"))
        let vaultID = try string(vault["vault_id"], context: "vault.vault_id")
        let sealed = try AtlasVaultRecordCrypto.seal(
            plaintext: Data(),
            vaultKey: vaultKey,
            vaultID: vaultID,
            record: record
        )

        XCTAssertEqual(try data(base64: sealed.ciphertext).count, AtlasVaultRecordCrypto.gcmTagByteCount)
        XCTAssertEqual(
            try AtlasVaultRecordCrypto.open(record: sealed, vaultKey: vaultKey, vaultID: vaultID),
            Data()
        )
    }

    func testInvalidVaultKeyLengthFails() throws {
        XCTAssertThrowsError(try AtlasVaultRecordCrypto.deriveRecordKey(
            vaultKey: Data(repeating: 0, count: AtlasVaultRecordCrypto.vaultKeyByteCount - 1),
            vaultID: "00000000-0000-4000-8000-000000000200",
            recordID: "00000000-0000-4000-8000-000000000201"
        )) { error in
            XCTAssertEqual(error as? AtlasVaultCryptoError, .invalidVaultKeyLength)
        }
    }

    func testInvalidNonceLengthFails() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let invalidNonceRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: Data(repeating: 0, count: AtlasVaultRecordCrypto.nonceByteCount - 1).base64EncodedString(),
            ciphertext: record.ciphertext
        )

        XCTAssertThrowsError(try open(record: invalidNonceRecord, vector: vector)) { error in
            XCTAssertEqual(error as? AtlasVaultCryptoError, .invalidNonceLength)
        }
    }

    func testUnsupportedRecordSchemaVersionFails() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let unsupportedRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion + 1,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: record.nonce,
            ciphertext: record.ciphertext
        )

        XCTAssertThrowsError(try open(record: unsupportedRecord, vector: vector)) { error in
            XCTAssertEqual(error as? AtlasVaultCryptoError, .unsupportedRecordVersion)
        }
    }

    func testInvalidBase64FailsClosed() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let invalidRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: "not base64",
            ciphertext: record.ciphertext
        )

        XCTAssertThrowsError(try open(record: invalidRecord, vector: vector)) { error in
            XCTAssertEqual(error as? AtlasVaultCryptoError, .invalidBase64("nonce"))
        }
    }

    func testInvalidCiphertextBase64FailsClosed() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let invalidRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: record.nonce,
            ciphertext: "not base64"
        )

        XCTAssertThrowsError(try open(record: invalidRecord, vector: vector)) { error in
            XCTAssertEqual(error as? AtlasVaultCryptoError, .invalidBase64("ciphertext"))
        }
    }

    func testTooShortCiphertextAndTagEnvelopeFailsClosed() throws {
        let vector = try firstVector()
        let record = try recordEnvelope(vector)
        let invalidRecord = AtlasVaultEncryptedRecordEnvelope(
            id: record.id,
            schemaVersion: record.schemaVersion,
            revision: record.revision,
            parentRevision: record.parentRevision,
            deleted: record.deleted,
            keyID: record.keyID,
            nonce: record.nonce,
            ciphertext: Data(repeating: 2, count: AtlasVaultRecordCrypto.gcmTagByteCount - 1)
                .base64EncodedString()
        )

        XCTAssertThrowsError(try open(record: invalidRecord, vector: vector)) { error in
            XCTAssertEqual(error as? AtlasVaultCryptoError, .invalidEnvelope)
        }
    }

    func testSerializedEncryptedRecordOmitsRecordTypesAndPrivateSentinels() throws {
        let root = try cryptoVectorRoot()
        let allowlist = Set(try stringArray(
            root["encrypted_record_plaintext_metadata_allowlist"],
            context: "encrypted_record_plaintext_metadata_allowlist"
        ))
        for vector in try vectors(in: root) {
            let record = try recordEnvelope(vector)
            let data = try encoder.encode(record)
            let serialized = try XCTUnwrap(String(data: data, encoding: .utf8))
            let object = try dictionary(JSONSerialization.jsonObject(with: data), context: "encoded record")

            XCTAssertEqual(Set(object.keys), allowlist)
            for forbidden in try stringArray(
                vector["forbidden_plaintext_strings"],
                context: "forbidden_plaintext_strings"
            ) {
                XCTAssertFalse(serialized.contains(forbidden), forbidden)
            }
        }
    }

    func testHelperSourceAvoidsRuntimeSideEffectAPIs() throws {
        let source = try String(contentsOf: helperSourceURL(), encoding: .utf8)

        XCTAssertFalse(source.contains("Keychain"))
        XCTAssertFalse(source.contains("SecItem"))
        XCTAssertFalse(source.contains("UserDefaults"))
        XCTAssertFalse(source.contains("FileManager"))
        XCTAssertFalse(source.contains("Data(contentsOf"))
        XCTAssertFalse(source.contains(".write("))
    }

    private func assertOpenFailsAuthentication(
        record: AtlasVaultEncryptedRecordEnvelope,
        vector: [String: Any],
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        XCTAssertThrowsError(try open(record: record, vector: vector), file: file, line: line) { error in
            XCTAssertEqual(error as? AtlasVaultCryptoError, .authenticationFailed, file: file, line: line)
        }
    }

    private func open(record: AtlasVaultEncryptedRecordEnvelope, vector: [String: Any]) throws -> Data {
        let vault = try dictionary(vector["vault"], context: "vault")
        return try AtlasVaultRecordCrypto.open(
            record: record,
            vaultKey: data(base64: string(vector["test_only_vault_key_b64"], context: "test_only_vault_key_b64")),
            vaultID: string(vault["vault_id"], context: "vault.vault_id"),
            vaultFormat: string(vault["format"], context: "vault.format"),
            vaultVersion: int(vault["version"], context: "vault.version")
        )
    }

    private func recordEnvelope(_ vector: [String: Any]) throws -> AtlasVaultEncryptedRecordEnvelope {
        let record = try dictionary(vector["record"], context: "record")
        return try decoder.decode(
            AtlasVaultEncryptedRecordEnvelope.self,
            from: JSONSerialization.data(withJSONObject: record, options: [.sortedKeys])
        )
    }

    private func cryptoVectorRoot() throws -> [String: Any] {
        let data = try Data(contentsOf: vectorFileURL(fileName: "atlasvault_crypto_vectors_v1.json"))
        let object = try JSONSerialization.jsonObject(with: data)
        return try dictionary(object, context: "atlasvault_crypto_vectors_v1.json")
    }

    private func vectors() throws -> [[String: Any]] {
        try vectors(in: cryptoVectorRoot())
    }

    private func vectors(in root: [String: Any]) throws -> [[String: Any]] {
        guard let vectors = root["vectors"] as? [[String: Any]], !vectors.isEmpty else {
            throw testError("vectors must be a non-empty object array")
        }
        return vectors
    }

    private func firstVector() throws -> [String: Any] {
        try XCTUnwrap(vectors().first)
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

    private func helperSourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/AtlasVaultRecordCrypto.swift"),
            sourceDirectory.appendingPathComponent(
                "../../../../apps/apple/Sources/AtlasUI/AtlasVaultRecordCrypto.swift"
            ),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find AtlasVaultRecordCrypto.swift")
    }

    private func symmetricKeyData(_ key: SymmetricKey) -> Data {
        key.withUnsafeBytes { Data($0) }
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
            domain: "AtlasVaultRecordCryptoTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}

private let encoder: JSONEncoder = {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    return encoder
}()

private let decoder = JSONDecoder()
