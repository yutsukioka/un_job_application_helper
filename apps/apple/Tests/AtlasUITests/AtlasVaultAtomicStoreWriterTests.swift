import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultAtomicStoreWriterTests: XCTestCase {
    func testRealWriterCreatesCompleteEncryptedStore() throws {
        let rootURL = try temporaryDirectory()
        let destinationURL = rootURL.appendingPathComponent("vault-store.json")
        let store = localStore(storeID: "TEST_ONLY_FIRST_STORE")

        let result = try AtlasVaultAtomicStoreWriter().write(store, to: destinationURL, overwrite: false)

        XCTAssertEqual(result.commitState, .committed)
        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: destinationURL), store)
        XCTAssertEqual(try directoryEntryNames(rootURL), ["vault-store.json"])
        let permissions = try XCTUnwrap(
            FileManager.default.attributesOfItem(atPath: destinationURL.path)[.posixPermissions] as? NSNumber
        )
        XCTAssertEqual(permissions.intValue & 0o777, 0o600)
    }

    func testRealWriterAtomicallyOverwritesExistingStore() throws {
        let rootURL = try temporaryDirectory()
        let destinationURL = rootURL.appendingPathComponent("vault-store.json")
        let original = localStore(storeID: "TEST_ONLY_ORIGINAL_STORE")
        let replacement = localStore(storeID: "TEST_ONLY_REPLACEMENT_STORE", nonceByte: 7)
        let writer = AtlasVaultAtomicStoreWriter()
        _ = try writer.write(original, to: destinationURL, overwrite: false)

        let result = try writer.write(replacement, to: destinationURL, overwrite: true)

        XCTAssertEqual(result.commitState, .committed)
        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: destinationURL), replacement)
        XCTAssertEqual(try directoryEntryNames(rootURL), ["vault-store.json"])
    }

    func testOverwriteFalseRefusesExistingDestinationAndCleansTemporaryFile() throws {
        let rootURL = try temporaryDirectory()
        let destinationURL = rootURL.appendingPathComponent("vault-store.json")
        let original = localStore(storeID: "TEST_ONLY_ORIGINAL_STORE")
        let writer = AtlasVaultAtomicStoreWriter()
        _ = try writer.write(original, to: destinationURL, overwrite: false)

        XCTAssertThrowsError(try writer.write(
            localStore(storeID: "TEST_ONLY_REJECTED_STORE", nonceByte: 8),
            to: destinationURL,
            overwrite: false
        )) { error in
            XCTAssertEqual(error as? AtlasVaultAtomicWriteError, .destinationExists)
        }
        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: destinationURL), original)
        XCTAssertEqual(try directoryEntryNames(rootURL), ["vault-store.json"])
    }

    func testTemporaryFileUsesSameDirectoryAndNonSemanticRandomName() throws {
        let destinationURL = fakeDestinationURL()
        let fileSystem = FakeAtomicFileSystemClient(destinationURL: destinationURL)

        _ = try AtlasVaultAtomicStoreWriter(fileSystem: fileSystem).write(
            localStore(),
            to: destinationURL,
            overwrite: false
        )

        let temporaryURL = try XCTUnwrap(fileSystem.createdTemporaryURLs.first)
        XCTAssertEqual(
            temporaryURL.deletingLastPathComponent().standardizedFileURL,
            destinationURL.deletingLastPathComponent().standardizedFileURL
        )
        XCTAssertNotNil(
            temporaryURL.lastPathComponent.range(
                of: #"^\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.tmp$"#,
                options: .regularExpression
            )
        )
        for forbidden in Self.forbiddenPlaintext {
            XCTAssertFalse(temporaryURL.path.contains(forbidden), forbidden)
        }
    }

    func testTemporaryProtectionOccursBeforeByteWrite() throws {
        let destinationURL = fakeDestinationURL()
        let fileSystem = FakeAtomicFileSystemClient(destinationURL: destinationURL)

        _ = try testWriter(fileSystem: fileSystem).write(localStore(), to: destinationURL, overwrite: false)

        XCTAssertLessThan(
            try XCTUnwrap(fileSystem.calls.firstIndex(of: "protect")),
            try XCTUnwrap(fileSystem.calls.firstIndex(of: "write"))
        )
    }

    func testOldDestinationSurvivesTemporaryCreationFailure() throws {
        try assertPreCommitFailurePreservesDestination(
            failures: [.create],
            expectedError: .temporaryCreationFailed
        )
    }

    func testOldDestinationSurvivesProtectionFailureWithoutWritingBytes() throws {
        let fileSystem = try assertPreCommitFailurePreservesDestination(
            failures: [.protect],
            expectedError: .temporaryProtectionFailed
        )
        XCTAssertEqual(fileSystem.writeCallCount, 0)
    }

    func testOldDestinationSurvivesByteWriteFailure() throws {
        try assertPreCommitFailurePreservesDestination(
            failures: [.write],
            expectedError: .writeFailed
        )
    }

    func testOldDestinationSurvivesValidationFailure() throws {
        try assertPreCommitFailurePreservesDestination(
            failures: [.validation],
            expectedError: .validationFailed
        )
    }

    func testOldDestinationSurvivesTemporaryFileSynchronizationFailure() throws {
        try assertPreCommitFailurePreservesDestination(
            failures: [.synchronizeFile],
            expectedError: .synchronizationFailed
        )
    }

    func testOldDestinationSurvivesReplacementFailure() throws {
        try assertPreCommitFailurePreservesDestination(
            failures: [.commit],
            expectedError: .replacementFailed
        )
    }

    func testCleanupFailureReportsNonSensitivePrimaryStage() throws {
        let destinationURL = fakeDestinationURL()
        let originalData = try AtlasVaultLocalStoreIO.encode(localStore(storeID: "TEST_ONLY_OLD"))
        let fileSystem = FakeAtomicFileSystemClient(
            destinationURL: destinationURL,
            existingDestinationData: originalData,
            failures: [.write, .remove]
        )

        XCTAssertThrowsError(try testWriter(fileSystem: fileSystem).write(
            localStore(storeID: "TEST_ONLY_NEW"),
            to: destinationURL,
            overwrite: true
        )) { error in
            XCTAssertEqual(
                error as? AtlasVaultAtomicWriteError,
                .cleanupFailed(after: .byteWrite)
            )
            assertErrorIsNonSensitive(error)
        }
        XCTAssertEqual(fileSystem.data(at: destinationURL), originalData)
        XCTAssertNotNil(fileSystem.temporaryData)
    }

    func testDirectorySyncFailureReportsCommittedDurabilityUnconfirmedWithoutRollback() throws {
        let destinationURL = fakeDestinationURL()
        let originalData = try AtlasVaultLocalStoreIO.encode(localStore(storeID: "TEST_ONLY_OLD"))
        let replacement = localStore(storeID: "TEST_ONLY_NEW", nonceByte: 9)
        let replacementData = try AtlasVaultLocalStoreIO.encode(replacement)
        let fileSystem = FakeAtomicFileSystemClient(
            destinationURL: destinationURL,
            existingDestinationData: originalData,
            failures: [.synchronizeDirectory]
        )

        let result = try testWriter(fileSystem: fileSystem).write(
            replacement,
            to: destinationURL,
            overwrite: true
        )

        XCTAssertEqual(result.commitState, .committedDurabilityUnconfirmed)
        XCTAssertEqual(fileSystem.data(at: destinationURL), replacementData)
        XCTAssertNil(fileSystem.temporaryData)
        XCTAssertFalse(fileSystem.calls.contains("remove"))
    }

    func testInvalidDestinationFailsBeforeFilesystemMutation() throws {
        let destinationURL = try XCTUnwrap(URL(string: "https://example.invalid/vault-store.json"))
        let fileSystem = FakeAtomicFileSystemClient(destinationURL: fakeDestinationURL())

        XCTAssertThrowsError(try testWriter(fileSystem: fileSystem).write(
            localStore(),
            to: destinationURL,
            overwrite: false
        )) { error in
            XCTAssertEqual(error as? AtlasVaultAtomicWriteError, .invalidDestination)
        }
        XCTAssertTrue(fileSystem.calls.isEmpty)
    }

    func testMissingPreparedParentFailsBeforeTemporaryCreation() throws {
        let destinationURL = fakeDestinationURL()
        let fileSystem = FakeAtomicFileSystemClient(
            destinationURL: destinationURL,
            failures: [.validateParent]
        )

        XCTAssertThrowsError(try testWriter(fileSystem: fileSystem).write(
            localStore(),
            to: destinationURL,
            overwrite: false
        )) { error in
            XCTAssertEqual(error as? AtlasVaultAtomicWriteError, .parentDirectoryUnavailable)
        }
        XCTAssertTrue(fileSystem.createdTemporaryURLs.isEmpty)
    }

    func testTemporaryNameCannotEscapeDestinationParent() throws {
        let destinationURL = fakeDestinationURL()
        let fileSystem = FakeAtomicFileSystemClient(destinationURL: destinationURL)
        let writer = AtlasVaultAtomicStoreWriter(
            fileSystem: fileSystem,
            temporaryNameGenerator: { "../escape" }
        )

        XCTAssertThrowsError(try writer.write(localStore(), to: destinationURL, overwrite: false)) { error in
            XCTAssertEqual(error as? AtlasVaultAtomicWriteError, .invalidTemporaryFileName)
        }
        XCTAssertTrue(fileSystem.createdTemporaryURLs.isEmpty)
    }

    func testTemporaryNameCannotCollideWithDestination() throws {
        let destinationURL = URL(
            fileURLWithPath: "/tmp/atlasvault-atomic-fake-root/.deterministic-test-token.tmp"
        )
        let fileSystem = FakeAtomicFileSystemClient(destinationURL: destinationURL)

        XCTAssertThrowsError(try testWriter(fileSystem: fileSystem).write(
            localStore(),
            to: destinationURL,
            overwrite: true
        )) { error in
            XCTAssertEqual(error as? AtlasVaultAtomicWriteError, .invalidTemporaryFileName)
        }
        XCTAssertTrue(fileSystem.createdTemporaryURLs.isEmpty)
    }

    func testRealWriterRejectsSymbolicLinkDestinationWithoutChangingTarget() throws {
        let rootURL = try temporaryDirectory()
        let outsideURL = try temporaryDirectory().appendingPathComponent("outside.json")
        let originalData = Data("OUTSIDE_TEST_SENTINEL".utf8)
        try originalData.write(to: outsideURL)
        let destinationURL = rootURL.appendingPathComponent("vault-store.json")
        do {
            try FileManager.default.createSymbolicLink(at: destinationURL, withDestinationURL: outsideURL)
        } catch {
            throw XCTSkip("Symbolic links are unavailable in this test environment")
        }

        XCTAssertThrowsError(try AtlasVaultAtomicStoreWriter().write(
            localStore(),
            to: destinationURL,
            overwrite: true
        )) { error in
            XCTAssertEqual(error as? AtlasVaultAtomicWriteError, .unsafePath)
        }
        XCTAssertEqual(try Data(contentsOf: outsideURL), originalData)
    }

    func testEncryptedOutputContainsNoPrivateSentinelsOrPlaintextRecordTypes() throws {
        let rootURL = try temporaryDirectory()
        let destinationURL = rootURL.appendingPathComponent("vault-store.json")
        _ = try AtlasVaultAtomicStoreWriter().write(
            try privateEncryptedStore(),
            to: destinationURL,
            overwrite: false
        )

        let serialized = try XCTUnwrap(String(data: Data(contentsOf: destinationURL), encoding: .utf8))
        for forbidden in Self.forbiddenPlaintext {
            XCTAssertFalse(serialized.contains(forbidden), forbidden)
        }
    }

    func testRealWriterCreatesNoAtlasVaultArtifactOrOutsideRootWrite() throws {
        let rootURL = try temporaryDirectory()
        let destinationURL = rootURL.appendingPathComponent("nested/vault-store.json")
        try FileManager.default.createDirectory(
            at: destinationURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        _ = try AtlasVaultAtomicStoreWriter().write(localStore(), to: destinationURL, overwrite: false)

        let urls = try allURLs(under: rootURL)
        XCTAssertTrue(urls.allSatisfy { $0.standardizedFileURL.path.hasPrefix(rootURL.standardizedFileURL.path) })
        XCTAssertFalse(urls.contains { $0.pathExtension == "atlasvault" })
        XCTAssertEqual(try AtlasVaultLocalStoreIO.read(from: destinationURL), localStore())
    }

    func testSourceAvoidsRuntimePathSelectionKeyRetrievalAndNetworking() throws {
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
            "ApplicationSupport",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private static let createdAt = "2026-01-01T00:00:00Z"
    private static let updatedAt = "2026-01-02T00:00:00Z"
    private static let vaultID = "00000000-0000-4000-8000-000000000224"
    private static let keyID = "phase2d24-test-key"
    private static let vaultKey = Data((0..<AtlasVaultRecordCrypto.vaultKeyByteCount).map { UInt8($0) })
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

    @discardableResult
    private func assertPreCommitFailurePreservesDestination(
        failures: Set<FakeAtomicFileSystemClient.FailurePoint>,
        expectedError: AtlasVaultAtomicWriteError,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws -> FakeAtomicFileSystemClient {
        let destinationURL = fakeDestinationURL()
        let originalData = try AtlasVaultLocalStoreIO.encode(localStore(storeID: "TEST_ONLY_OLD"))
        let fileSystem = FakeAtomicFileSystemClient(
            destinationURL: destinationURL,
            existingDestinationData: originalData,
            failures: failures
        )

        XCTAssertThrowsError(try testWriter(fileSystem: fileSystem).write(
            localStore(storeID: "TEST_ONLY_NEW", nonceByte: 10),
            to: destinationURL,
            overwrite: true
        ), file: file, line: line) { error in
            XCTAssertEqual(error as? AtlasVaultAtomicWriteError, expectedError, file: file, line: line)
            assertErrorIsNonSensitive(error, file: file, line: line)
        }
        XCTAssertEqual(fileSystem.data(at: destinationURL), originalData, file: file, line: line)
        XCTAssertNil(fileSystem.temporaryData, file: file, line: line)
        return fileSystem
    }

    private func localStore(
        storeID: String = "TEST_ONLY_ATOMIC_STORE",
        nonceByte: UInt8 = 1
    ) -> AtlasVaultLocalStoreEnvelope {
        AtlasVaultLocalStoreEnvelope(
            storeID: storeID,
            createdAt: Self.createdAt,
            updatedAt: Self.updatedAt,
            vaultMetadata: [
                "format": .string("atlas-vault"),
                "version": .number(1),
                "vault_id": .string(Self.vaultID),
                "key_wraps": .array([]),
            ],
            records: [encryptedRecord(nonceByte: nonceByte)]
        )
    }

    private func encryptedRecord(nonceByte: UInt8) -> AtlasVaultEncryptedRecordEnvelope {
        AtlasVaultEncryptedRecordEnvelope(
            id: "record-test-only",
            schemaVersion: AtlasVaultRecordCrypto.supportedRecordSchemaVersion,
            revision: "revision-test-only",
            parentRevision: nil,
            deleted: false,
            keyID: Self.keyID,
            nonce: Data(repeating: nonceByte, count: AtlasVaultRecordCrypto.nonceByteCount).base64EncodedString(),
            ciphertext: Data(repeating: nonceByte + 1, count: AtlasVaultRecordCrypto.gcmTagByteCount).base64EncodedString()
        )
    }

    private func privateEncryptedStore() throws -> AtlasVaultLocalStoreEnvelope {
        let session = try AtlasVaultUnlockedSession(vaultID: Self.vaultID, vaultKey: Self.vaultKey)
        let saver = AtlasVaultRecordSaver(
            recordIDGenerator: { "record-private-test" },
            revisionIDGenerator: { "revision-private-test" },
            nonceGenerator: { Data(repeating: 24, count: AtlasVaultRecordCrypto.nonceByteCount) }
        )
        let records = try saver.save(
            mutations: AtlasVaultMutationSet(creates: [
                AtlasVaultCreateMutation(
                    payload: .savedSearch(AtlasSavedSearchVaultRecordPayload(
                        type: .savedSearch,
                        payload: AtlasSavedSearchVaultPayload(
                            name: "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
                            summary: "FAKE_PRIVATE_FILTER_DO_NOT_LEAK",
                            description: nil,
                            request: AtlasSearchRequest(text: "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK"),
                            createdAt: Self.createdAt,
                            updatedAt: Self.updatedAt
                        ),
                        clientCreatedAt: Self.createdAt,
                        clientUpdatedAt: Self.updatedAt
                    )),
                    keyID: Self.keyID
                ),
            ]),
            session: session
        )
        return AtlasVaultLocalStoreEnvelope(
            storeID: "TEST_ONLY_PRIVATE_ENCRYPTED_STORE",
            createdAt: Self.createdAt,
            updatedAt: Self.updatedAt,
            vaultMetadata: [
                "format": .string("atlas-vault"),
                "version": .number(1),
                "vault_id": .string(Self.vaultID),
                "key_wraps": .array([]),
            ],
            records: records
        )
    }

    private func testWriter(
        fileSystem: FakeAtomicFileSystemClient
    ) -> AtlasVaultAtomicStoreWriter {
        AtlasVaultAtomicStoreWriter(
            fileSystem: fileSystem,
            temporaryNameGenerator: { "deterministic-test-token" }
        )
    }

    private func fakeDestinationURL() -> URL {
        URL(fileURLWithPath: "/tmp/atlasvault-atomic-fake-root/vault-store.json")
    }

    private func temporaryDirectory() throws -> URL {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("atlasvault-atomic-writer-tests-\(UUID().uuidString)", isDirectory: true)
            .standardizedFileURL
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: url)
        }
        return url
    }

    private func directoryEntryNames(_ url: URL) throws -> [String] {
        try FileManager.default.contentsOfDirectory(atPath: url.path).sorted()
    }

    private func allURLs(under rootURL: URL) throws -> [URL] {
        guard let enumerator = FileManager.default.enumerator(
            at: rootURL,
            includingPropertiesForKeys: nil
        ) else {
            return []
        }
        return enumerator.compactMap { $0 as? URL }
    }

    private func sourceFileURL() throws -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let candidates = [
            currentDirectory.appendingPathComponent("Sources/AtlasUI/AtlasVaultAtomicStoreWriter.swift"),
            currentDirectory.appendingPathComponent("apps/apple/Sources/AtlasUI/AtlasVaultAtomicStoreWriter.swift"),
            URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .appendingPathComponent("../../Sources/AtlasUI/AtlasVaultAtomicStoreWriter.swift"),
        ].map(\.standardizedFileURL)
        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw NSError(
            domain: "AtlasVaultAtomicStoreWriterTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Could not find atomic writer source"]
        )
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
}

private final class FakeAtomicFileSystemClient: AtlasVaultAtomicFileSystemClient, @unchecked Sendable {
    enum FailurePoint: Hashable {
        case validateParent
        case create
        case protect
        case write
        case validation
        case synchronizeFile
        case commit
        case synchronizeDirectory
        case remove
    }

    private let destinationURL: URL
    private var files: [URL: Data]
    private let failures: Set<FailurePoint>

    private(set) var createdTemporaryURLs: [URL] = []
    private(set) var calls: [String] = []
    private(set) var writeCallCount = 0

    init(
        destinationURL: URL,
        existingDestinationData: Data? = nil,
        failures: Set<FailurePoint> = []
    ) {
        self.destinationURL = destinationURL.standardizedFileURL
        self.failures = failures
        if let existingDestinationData {
            self.files = [self.destinationURL: existingDestinationData]
        } else {
            self.files = [:]
        }
    }

    var temporaryData: Data? {
        guard let temporaryURL = createdTemporaryURLs.last else {
            return nil
        }
        return files[temporaryURL.standardizedFileURL]
    }

    func data(at url: URL) -> Data? {
        files[url.standardizedFileURL]
    }

    func validatePreparedParent(for destinationURL: URL) throws {
        calls.append("validateParent")
        if failures.contains(.validateParent) {
            throw AtlasVaultAtomicFileSystemError.parentUnavailable
        }
        guard destinationURL.deletingLastPathComponent().standardizedFileURL ==
                self.destinationURL.deletingLastPathComponent().standardizedFileURL
        else {
            throw AtlasVaultAtomicFileSystemError.unsafePath
        }
    }

    func createTemporaryFile(at url: URL) throws {
        calls.append("create")
        if failures.contains(.create) {
            throw AtlasVaultAtomicFileSystemError.temporaryCreationFailed
        }
        let url = url.standardizedFileURL
        guard files[url] == nil else {
            throw AtlasVaultAtomicFileSystemError.temporaryCreationFailed
        }
        createdTemporaryURLs.append(url)
        files[url] = Data()
    }

    func protectTemporaryFile(at url: URL) throws {
        calls.append("protect")
        if failures.contains(.protect) {
            throw AtlasVaultAtomicFileSystemError.protectionFailed
        }
    }

    func write(_ data: Data, to url: URL) throws {
        calls.append("write")
        writeCallCount += 1
        let url = url.standardizedFileURL
        if failures.contains(.write) {
            files[url] = Data(data.prefix(max(1, data.count / 2)))
            throw AtlasVaultAtomicFileSystemError.writeFailed
        }
        files[url] = data
    }

    func read(from url: URL) throws -> Data {
        calls.append("read")
        if failures.contains(.validation) {
            return Data(#"{"invalid":true}"#.utf8)
        }
        guard let data = files[url.standardizedFileURL] else {
            throw AtlasVaultAtomicFileSystemError.readFailed
        }
        return data
    }

    func synchronizeFile(at url: URL) throws {
        calls.append("synchronizeFile")
        if failures.contains(.synchronizeFile) {
            throw AtlasVaultAtomicFileSystemError.synchronizationFailed
        }
    }

    func commitTemporaryFile(
        at temporaryURL: URL,
        to destinationURL: URL,
        overwrite: Bool
    ) throws {
        calls.append("commit")
        if failures.contains(.commit) {
            throw AtlasVaultAtomicFileSystemError.replacementFailed
        }
        let temporaryURL = temporaryURL.standardizedFileURL
        let destinationURL = destinationURL.standardizedFileURL
        if !overwrite, files[destinationURL] != nil {
            throw AtlasVaultAtomicFileSystemError.destinationExists
        }
        guard let data = files.removeValue(forKey: temporaryURL) else {
            throw AtlasVaultAtomicFileSystemError.replacementFailed
        }
        files[destinationURL] = data
    }

    func synchronizeDirectory(at url: URL) throws {
        calls.append("synchronizeDirectory")
        if failures.contains(.synchronizeDirectory) {
            throw AtlasVaultAtomicFileSystemError.synchronizationFailed
        }
    }

    func removeItemIfExists(at url: URL) throws {
        calls.append("remove")
        if failures.contains(.remove) {
            throw AtlasVaultAtomicFileSystemError.cleanupFailed
        }
        files.removeValue(forKey: url.standardizedFileURL)
    }
}
