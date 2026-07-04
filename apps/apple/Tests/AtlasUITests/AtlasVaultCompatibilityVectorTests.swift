import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultCompatibilityVectorTests: XCTestCase {
    func testSwiftPayloadModelsMatchSharedVectorJSON() throws {
        let root = try loadVectorRoot()
        let payloads = try dictionary(root["payloads"], context: "payloads")

        for recordType in requiredRecordTypes {
            let vector = try dictionary(payloads[recordType], context: recordType)
            let encodedObject = try encodedSwiftPayloadObject(recordType: recordType, vector: vector)

            try assertJSONObjectsEqual(encodedObject, vector)
        }
    }

    func testVectorRecordTypesAndEnvelopeKeysAreStable() throws {
        let root = try loadVectorRoot()
        let recordTypes = try stringArray(root["record_types"], context: "record_types")
        let commonKeys = Set(try stringArray(root["common_envelope_keys"], context: "common_envelope_keys"))
        let payloads = try dictionary(root["payloads"], context: "payloads")

        XCTAssertEqual(recordTypes, requiredRecordTypes)
        XCTAssertEqual(commonKeys, ["type", "payload_schema", "payload", "client_created_at", "client_updated_at"])

        for recordType in requiredRecordTypes {
            let vector = try dictionary(payloads[recordType], context: recordType)
            XCTAssertEqual(Set(vector.keys), commonKeys)
            XCTAssertEqual(try string(vector["type"], context: "\(recordType).type"), recordType)
            XCTAssertEqual(try int(vector["payload_schema"], context: "\(recordType).payload_schema"), 1)
        }
    }

    func testPayloadKeysAreSnakeCaseAndTimestampsAreUTCStrings() throws {
        let root = try loadVectorRoot()
        let payloads = try dictionary(root["payloads"], context: "payloads")

        for recordType in requiredRecordTypes {
            let vector = try dictionary(payloads[recordType], context: recordType)
            assertSnakeCaseKeys(in: vector, context: recordType)
            assertNoNulls(in: vector, context: recordType)
            for timestamp in timestampValues(in: vector) {
                XCTAssertTrue(timestampRegex.matches(timestamp), timestamp)
            }
        }
    }

    func testPreEncryptionPayloadJSONContainsFakePrivateSentinels() throws {
        // The shared vector file is plaintext-before-encryption test data only.
        // These payloads must never be written to AtlasPublicLocalSnapshot,
        // local vault stores, or .atlasvault exports without encryption.
        let root = try loadVectorRoot()
        let payloads = try dictionary(root["payloads"], context: "payloads")
        let expectations = try dictionary(
            root["encrypted_record_expectations"],
            context: "encrypted_record_expectations"
        )
        let forbiddenStrings = try stringArray(
            expectations["forbidden_plaintext_strings"],
            context: "forbidden_plaintext_strings"
        )
        let encodedPayloads = try requiredRecordTypes.map { recordType in
            let vector = try dictionary(payloads[recordType], context: recordType)
            let encodedObject = try encodedSwiftPayloadObject(recordType: recordType, vector: vector)
            let data = try JSONSerialization.data(withJSONObject: encodedObject, options: [.sortedKeys])
            return try XCTUnwrap(String(data: data, encoding: .utf8))
        }.joined(separator: "\n")

        for privateValue in forbiddenStrings {
            XCTAssertTrue(encodedPayloads.contains(privateValue), privateValue)
        }
    }

    func testPublicSnapshotSerializationDoesNotContainSharedVectorPayloads() throws {
        let root = try loadVectorRoot()
        let expectations = try dictionary(
            root["encrypted_record_expectations"],
            context: "encrypted_record_expectations"
        )
        let forbiddenStrings = try stringArray(
            expectations["forbidden_plaintext_strings"],
            context: "forbidden_plaintext_strings"
        )
        let snapshot = try decoder.decode(AtlasPublicLocalSnapshot.self, from: Data("""
        {
          "savedAt": "2026-01-07T00:00:00Z",
          "baseURL": "http://127.0.0.1:8765",
          "health": {
            "status": "ok",
            "db_path": null,
            "schema_version": "test",
            "open_jobs": 0,
            "enabled_sources": 0,
            "last_sync_at": null
          },
          "searchResponse": {
            "total": 0,
            "limit": 0,
            "offset": 0,
            "results": [],
            "facets": {},
            "facet_labels": {},
            "unclassified_count": 0
          },
          "sources": [],
          "recentRuns": []
        }
        """.utf8))
        let snapshotData = try AtlasLocalCache.encodedSnapshotData(snapshot)
        let snapshotJSON = try XCTUnwrap(String(data: snapshotData, encoding: .utf8))

        for privateValue in forbiddenStrings {
            XCTAssertFalse(snapshotJSON.contains(privateValue), privateValue)
        }
    }

    private func encodedSwiftPayloadObject(recordType: String, vector: [String: Any]) throws -> Any {
        let data = try JSONSerialization.data(withJSONObject: vector, options: [.sortedKeys])

        switch recordType {
        case "saved_search":
            let decoded = try decoder.decode(AtlasSavedSearchVaultRecordPayload.self, from: data)
            XCTAssertEqual(decoded.type.rawValue, recordType)
            return try JSONSerialization.jsonObject(with: encoder.encode(decoded))
        case "saved_job":
            let decoded = try decoder.decode(AtlasSavedJobVaultRecordPayload.self, from: data)
            XCTAssertEqual(decoded.type.rawValue, recordType)
            return try JSONSerialization.jsonObject(with: encoder.encode(decoded))
        case "application_note":
            let decoded = try decoder.decode(AtlasApplicationNoteVaultRecordPayload.self, from: data)
            XCTAssertEqual(decoded.type.rawValue, recordType)
            return try JSONSerialization.jsonObject(with: encoder.encode(decoded))
        case "profile_snippet":
            let decoded = try decoder.decode(AtlasProfileSnippetVaultRecordPayload.self, from: data)
            XCTAssertEqual(decoded.type.rawValue, recordType)
            return try JSONSerialization.jsonObject(with: encoder.encode(decoded))
        case "draft_metadata":
            let decoded = try decoder.decode(AtlasDraftMetadataVaultRecordPayload.self, from: data)
            XCTAssertEqual(decoded.type.rawValue, recordType)
            return try JSONSerialization.jsonObject(with: encoder.encode(decoded))
        default:
            XCTFail("Unsupported record type \(recordType)")
            return [:]
        }
    }

    private func loadVectorRoot() throws -> [String: Any] {
        let data = try Data(contentsOf: vectorFileURL())
        let object = try JSONSerialization.jsonObject(with: data)
        let root = try dictionary(object, context: "vector root")

        XCTAssertEqual(try string(root["format"], context: "format"), "atlasvault-payload-vectors")
        XCTAssertEqual(try int(root["version"], context: "version"), 1)
        XCTAssertEqual(
            try string(root["optional_field_convention"], context: "optional_field_convention"),
            "omit_absent_optional_fields"
        )
        return root
    }

    private func vectorFileURL() throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            currentDirectory.appendingPathComponent(
                "../../contracts/sync/test_vectors/atlasvault_payload_vectors_v1.json"
            ),
            currentDirectory.appendingPathComponent(
                "contracts/sync/test_vectors/atlasvault_payload_vectors_v1.json"
            ),
            sourceDirectory.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/atlasvault_payload_vectors_v1.json"
            ),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw NSError(
            domain: "AtlasVaultCompatibilityVectorTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Could not find shared AtlasVault vector file"]
        )
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

    private func assertSnakeCaseKeys(in value: Any, context: String, file: StaticString = #filePath, line: UInt = #line) {
        if let dictionary = value as? [String: Any] {
            for (key, child) in dictionary {
                XCTAssertTrue(snakeCaseRegex.matches(key), "\(context).\(key)", file: file, line: line)
                assertSnakeCaseKeys(in: child, context: "\(context).\(key)", file: file, line: line)
            }
        } else if let array = value as? [Any] {
            for (index, child) in array.enumerated() {
                assertSnakeCaseKeys(in: child, context: "\(context)[\(index)]", file: file, line: line)
            }
        }
    }

    private func assertNoNulls(in value: Any, context: String, file: StaticString = #filePath, line: UInt = #line) {
        if value is NSNull {
            XCTFail("Optional field should be omitted, not null: \(context)", file: file, line: line)
        } else if let dictionary = value as? [String: Any] {
            for (key, child) in dictionary {
                assertNoNulls(in: child, context: "\(context).\(key)", file: file, line: line)
            }
        } else if let array = value as? [Any] {
            for (index, child) in array.enumerated() {
                assertNoNulls(in: child, context: "\(context)[\(index)]", file: file, line: line)
            }
        }
    }

    private func timestampValues(in value: Any) -> [String] {
        if let dictionary = value as? [String: Any] {
            return dictionary.flatMap { key, child -> [String] in
                var values = timestampValues(in: child)
                if key.hasSuffix("_at"), let timestamp = child as? String {
                    values.append(timestamp)
                }
                return values
            }
        }
        if let array = value as? [Any] {
            return array.flatMap(timestampValues)
        }
        return []
    }

    private func dictionary(_ value: Any?, context: String) throws -> [String: Any] {
        guard let dictionary = value as? [String: Any] else {
            throw NSError(
                domain: "AtlasVaultCompatibilityVectorTests",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey: "\(context) must be an object"]
            )
        }
        return dictionary
    }

    private func stringArray(_ value: Any?, context: String) throws -> [String] {
        guard let array = value as? [String] else {
            throw NSError(
                domain: "AtlasVaultCompatibilityVectorTests",
                code: 3,
                userInfo: [NSLocalizedDescriptionKey: "\(context) must be a string array"]
            )
        }
        return array
    }

    private func string(_ value: Any?, context: String) throws -> String {
        guard let string = value as? String else {
            throw NSError(
                domain: "AtlasVaultCompatibilityVectorTests",
                code: 4,
                userInfo: [NSLocalizedDescriptionKey: "\(context) must be text"]
            )
        }
        return string
    }

    private func int(_ value: Any?, context: String) throws -> Int {
        guard let int = value as? Int else {
            throw NSError(
                domain: "AtlasVaultCompatibilityVectorTests",
                code: 5,
                userInfo: [NSLocalizedDescriptionKey: "\(context) must be an integer"]
            )
        }
        return int
    }

    private var requiredRecordTypes: [String] {
        [
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata",
        ]
    }

    private var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }

    private var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    private var snakeCaseRegex: NSRegularExpression {
        // Allows lower snake_case keys used at the Swift/Python JSON boundary.
        try! NSRegularExpression(pattern: #"^[a-z][a-z0-9_]*$"#)
    }

    private var timestampRegex: NSRegularExpression {
        try! NSRegularExpression(pattern: #"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"#)
    }
}

private extension NSRegularExpression {
    func matches(_ value: String) -> Bool {
        let range = NSRange(value.startIndex..<value.endIndex, in: value)
        return firstMatch(in: value, range: range) != nil
    }
}
