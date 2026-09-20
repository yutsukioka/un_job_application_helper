import XCTest
@testable import AtlasUI

final class AtlasVaultLocalStoreMergerTests: XCTestCase {
    func testMergeCreateInsertsNewEncryptedRecord() throws {
        let existing = record(id: "record-existing", revision: "revision-existing")
        let incoming = record(id: "record-created", revision: "revision-created", nonceByte: 3)
        let store = localStore(records: [existing])

        let merged = try merger().merge(records: [incoming], into: store)

        XCTAssertEqual(merged.records, [existing, incoming])
        XCTAssertEqual(merged.updatedAt, Self.mergedAt)
    }

    func testMergeUpdateReplacesExistingRecordWithSameID() throws {
        let existing = record(id: "record-update", revision: "revision-1")
        let incoming = record(
            id: "record-update",
            revision: "revision-2",
            parentRevision: "revision-1",
            nonceByte: 4
        )

        let merged = try merger().merge(records: [incoming], into: localStore(records: [existing]))

        XCTAssertEqual(merged.records, [incoming])
    }

    func testMergeTombstoneReplacesExistingActiveRecord() throws {
        let existing = record(id: "record-delete", revision: "revision-live")
        let tombstone = record(
            id: "record-delete",
            revision: "revision-deleted",
            parentRevision: "revision-live",
            deleted: true,
            nonceByte: 5
        )

        let merged = try merger().merge(records: [tombstone], into: localStore(records: [existing]))

        XCTAssertEqual(merged.records, [tombstone])
        XCTAssertTrue(try XCTUnwrap(merged.records.first).deleted)
    }

    func testUntouchedRecordsArePreserved() throws {
        let updatedOriginal = record(id: "record-a", revision: "revision-a")
        let untouched = record(id: "record-b", revision: "revision-b", nonceByte: 6)
        let update = record(
            id: "record-a",
            revision: "revision-a2",
            parentRevision: "revision-a",
            nonceByte: 7
        )

        let merged = try merger().merge(records: [update], into: localStore(records: [updatedOriginal, untouched]))

        XCTAssertEqual(merged.records, [update, untouched])
    }

    func testDuplicateIncomingIDsFailSafely() throws {
        let incomingA = record(id: "record-duplicate", revision: "revision-a")
        let incomingB = record(id: "record-duplicate", revision: "revision-b", nonceByte: 8)

        XCTAssertThrowsError(try merger().merge(records: [incomingA, incomingB], into: localStore(records: []))) { error in
            XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .duplicateIncomingRecordID)
            assertErrorIsNonSensitive(error)
        }
    }

    func testDuplicateExistingIDsFailBeforeMapping() throws {
        let existingA = record(id: "record-existing-duplicate", revision: "revision-a")
        let existingB = record(id: "record-existing-duplicate", revision: "revision-b", nonceByte: 9)

        XCTAssertThrowsError(try merger().merge(records: [], into: localStore(records: [existingA, existingB]))) { error in
            XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .duplicateExistingRecordID)
            assertErrorIsNonSensitive(error)
        }
    }

    func testStaleParentRevisionFailsSafely() throws {
        let existing = record(id: "record-stale", revision: "revision-current")
        let incoming = record(
            id: "record-stale",
            revision: "revision-next",
            parentRevision: "revision-old",
            nonceByte: 10
        )

        XCTAssertThrowsError(try merger().merge(records: [incoming], into: localStore(records: [existing]))) { error in
            XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .staleParentRevision)
            assertErrorIsNonSensitive(error)
        }
    }

    func testMatchingParentRevisionSucceeds() throws {
        let existing = record(id: "record-parent", revision: "revision-current")
        let incoming = record(
            id: "record-parent",
            revision: "revision-next",
            parentRevision: "revision-current",
            nonceByte: 11
        )

        let merged = try merger().merge(records: [incoming], into: localStore(records: [existing]))

        XCTAssertEqual(merged.records, [incoming])
    }

    func testMissingParentRevisionPolicy() throws {
        let existing = record(id: "record-existing", revision: "revision-current")
        let conflictingCreate = record(id: "record-existing", revision: "revision-conflict", nonceByte: 12)
        let newCreate = record(id: "record-new", revision: "revision-new", nonceByte: 13)

        XCTAssertThrowsError(try merger().merge(
            records: [conflictingCreate],
            into: localStore(records: [existing])
        )) { error in
            XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .conflictDetected)
        }

        let merged = try merger().merge(records: [newCreate], into: localStore(records: [existing]))
        XCTAssertEqual(merged.records, [existing, newCreate])
    }

    func testVaultMetadataIsPreservedAndUpdatedAtChangesDeterministically() throws {
        let store = localStore(records: [record()])

        let merged = try merger().merge(
            records: [record(id: "record-new", revision: "revision-new", nonceByte: 14)],
            into: store
        )

        XCTAssertEqual(merged.format, store.format)
        XCTAssertEqual(merged.version, store.version)
        XCTAssertEqual(merged.storeID, store.storeID)
        XCTAssertEqual(merged.createdAt, store.createdAt)
        XCTAssertEqual(merged.updatedAt, Self.mergedAt)
        XCTAssertEqual(merged.vaultMetadata, store.vaultMetadata)
    }

    func testSerializedMergedStoreDoesNotContainPrivateSentinelsOrRecordTypes() throws {
        let merged = try merger().merge(
            records: [record(id: "record-new", revision: "revision-new", nonceByte: 15)],
            into: localStore(records: [record()])
        )
        let serialized = try XCTUnwrap(String(data: AtlasVaultLocalStoreIO.encode(merged), encoding: .utf8))

        for forbidden in Self.forbiddenPlaintext {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testMergerAcceptsOpaqueEncryptedRecordsWithoutOpeningThem() throws {
        let opaqueRecord = record(
            id: "record-opaque",
            revision: "revision-opaque",
            ciphertextByte: 255
        )

        let merged = try merger().merge(records: [opaqueRecord], into: localStore(records: []))

        XCTAssertEqual(merged.records, [opaqueRecord])
    }

    func testUnsupportedRecordVersionFailsSafely() throws {
        let unsupported = record(
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion + 1
        )

        XCTAssertThrowsError(try merger().merge(records: [unsupported], into: localStore(records: []))) { error in
            XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .unsupportedRecordVersion)
            assertErrorIsNonSensitive(error)
        }
    }

    func testInvalidStoreFailsSafely() throws {
        let store = AtlasVaultLocalStoreEnvelope(
            format: "wrong-format",
            version: AtlasVaultLocalStoreIO.supportedLocalStoreVersion,
            storeID: Self.storeID,
            createdAt: Self.createdAt,
            updatedAt: Self.updatedAt,
            vaultMetadata: [:],
            records: []
        )

        XCTAssertThrowsError(try merger().merge(records: [], into: store)) { error in
            XCTAssertEqual(error as? AtlasVaultLocalStoreMergeError, .invalidStore)
            assertErrorIsNonSensitive(error)
        }
    }

    func testSourceAvoidsRuntimeFileIOAndRecordOpening() throws {
        let source = try String(contentsOf: sourceFileURL(), encoding: .utf8)
        for forbidden in [
            "SwiftUI",
            "SearchViewModel",
            "AtlasLocalCache",
            "UserDefaults",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "URLSession",
            "Data.write",
            "createFile",
            "FileManager.default",
            "decrypt",
            "open(",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private static let storeID = "TEST_ONLY_MERGER_STORE"
    private static let createdAt = "2026-01-01T00:00:00Z"
    private static let updatedAt = "2026-01-02T00:00:00Z"
    private static let mergedAt = "2026-01-03T00:00:00Z"
    private static let forbiddenPlaintext = [
        "saved_search",
        "saved_job",
        "application_note",
        "profile_snippet",
        "draft_metadata",
        "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
        "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
        "FAKE_PRIVATE_FILTER_DO_NOT_LEAK",
        "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
        "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
        "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
        "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK",
        "TOP_SECRET_SENTINEL_DO_NOT_LEAK",
    ]

    private func merger() -> AtlasVaultLocalStoreMerger {
        AtlasVaultLocalStoreMerger(updatedAtProvider: { Self.mergedAt })
    }

    private func localStore(records: [AtlasVaultEncryptedRecordEnvelope]) -> AtlasVaultLocalStoreEnvelope {
        AtlasVaultLocalStoreEnvelope(
            storeID: Self.storeID,
            createdAt: Self.createdAt,
            updatedAt: Self.updatedAt,
            vaultMetadata: [
                "format": .string("atlas-vault"),
                "version": .number(1),
                "vault_id": .string("TEST_ONLY_VAULT_ID"),
                "key_wraps": .array([]),
            ],
            records: records
        )
    }

    private func record(
        id: String = "record-existing",
        schemaVersion: Int = AtlasVaultRecordCrypto.supportedRecordSchemaVersion,
        revision: String = "revision-existing",
        parentRevision: String? = nil,
        deleted: Bool = false,
        keyID: String = "test-key",
        nonceByte: UInt8 = 1,
        ciphertextByte: UInt8 = 2
    ) -> AtlasVaultEncryptedRecordEnvelope {
        AtlasVaultEncryptedRecordEnvelope(
            id: id,
            schemaVersion: schemaVersion,
            revision: revision,
            parentRevision: parentRevision,
            deleted: deleted,
            keyID: keyID,
            nonce: Data(repeating: nonceByte, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString(),
            ciphertext: Data(repeating: ciphertextByte, count: AtlasVaultRecordCrypto.gcmTagByteCount).base64EncodedString()
        )
    }

    private func sourceFileURL() throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let candidates = [
            currentDirectory.appendingPathComponent("Sources/AtlasUI/AtlasVaultLocalStoreMerger.swift"),
            currentDirectory.appendingPathComponent("apps/apple/Sources/AtlasUI/AtlasVaultLocalStoreMerger.swift"),
            URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .appendingPathComponent("../../Sources/AtlasUI/AtlasVaultLocalStoreMerger.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find AtlasVaultLocalStoreMerger.swift")
    }

    private func assertErrorIsNonSensitive(
        _ error: Error,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let rendered = "\(String(describing: error)) \(String(reflecting: error))"
        for forbidden in Self.forbiddenPlaintext {
            XCTAssertFalse(rendered.contains(forbidden), forbidden, file: file, line: line)
        }
    }

    private func testError(_ message: String) -> NSError {
        NSError(
            domain: "AtlasVaultLocalStoreMergerTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}
