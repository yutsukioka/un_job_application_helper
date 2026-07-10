import XCTest
@testable import AtlasUI

final class AtlasVaultDirectoryPreparerTests: XCTestCase {
    func testCreatesParentDirectoriesForValidStoreURLUnderTempRoot() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = localStoreURL(under: rootURL)
        let parentURL = storeURL.deletingLastPathComponent()

        try preparer.prepareParentDirectory(for: storeURL, under: rootURL)

        XCTAssertTrue(FileManager.default.directoryExists(at: parentURL))
    }

    func testDoesNotCreateFinalStoreFile() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = localStoreURL(under: rootURL)

        try preparer.prepareParentDirectory(for: storeURL, under: rootURL)

        XCTAssertFalse(FileManager.default.fileExists(atPath: storeURL.path))
    }

    func testIsIdempotentWhenDirectoriesAlreadyExist() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = localStoreURL(under: rootURL)
        let parentURL = storeURL.deletingLastPathComponent()

        try preparer.prepareParentDirectory(for: storeURL, under: rootURL)
        try preparer.prepareParentDirectory(for: storeURL, under: rootURL)

        XCTAssertTrue(FileManager.default.directoryExists(at: parentURL))
        XCTAssertFalse(FileManager.default.fileExists(atPath: storeURL.path))
    }

    func testRejectsStoreURLOutsideRoot() throws {
        let rootURL = try temporaryDirectory()
        let outsideRoot = try temporaryDirectory(named: "atlasvault-directory-preparer-outside")
        let storeURL = localStoreURL(under: outsideRoot)

        XCTAssertThrowsError(try preparer.prepareParentDirectory(for: storeURL, under: rootURL)) { error in
            XCTAssertEqual(error as? AtlasVaultDirectoryError, .pathEscapesRoot)
        }
    }

    func testRejectsEncodedSlashBeforeCreatingDirectories() throws {
        let rootURL = try temporaryDirectory()
        let rootString = rootURL.absoluteString.hasSuffix("/")
            ? String(rootURL.absoluteString.dropLast())
            : rootURL.absoluteString
        let storeURL = try XCTUnwrap(
            URL(string: "\(rootString)/safe%2Fescape/vault-store.json")
        )

        XCTAssertThrowsError(try preparer.prepareParentDirectory(for: storeURL, under: rootURL)) { error in
            XCTAssertEqual(error as? AtlasVaultDirectoryError, .invalidURL)
        }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: rootURL.path), [])
    }

    func testRejectsRootPathThatExistsAsFile() throws {
        let directory = try temporaryDirectory()
        let rootFileURL = directory.appendingPathComponent("root-file")
        try "TEST_ONLY_ROOT_FILE".write(to: rootFileURL, atomically: true, encoding: .utf8)
        let storeURL = localStoreURL(under: rootFileURL)

        XCTAssertThrowsError(try preparer.prepareParentDirectory(for: storeURL, under: rootFileURL)) { error in
            XCTAssertEqual(error as? AtlasVaultDirectoryError, .rootNotDirectory)
        }
    }

    func testRejectsParentComponentThatExistsAsFile() throws {
        let rootURL = try temporaryDirectory()
        let atlasFileURL = rootURL.appendingPathComponent("Atlas", isDirectory: false)
        try "TEST_ONLY_PARENT_FILE".write(to: atlasFileURL, atomically: true, encoding: .utf8)
        let storeURL = localStoreURL(under: rootURL)

        XCTAssertThrowsError(try preparer.prepareParentDirectory(for: storeURL, under: rootURL)) { error in
            XCTAssertEqual(error as? AtlasVaultDirectoryError, .parentExistsAsFile)
        }
    }

    func testDoesNotDeleteExistingFiles() throws {
        let rootURL = try temporaryDirectory()
        let markerURL = rootURL.appendingPathComponent("existing-marker.txt")
        try "KEEP_THIS_TEST_FILE".write(to: markerURL, atomically: true, encoding: .utf8)

        try preparer.prepareParentDirectory(for: localStoreURL(under: rootURL), under: rootURL)

        XCTAssertTrue(FileManager.default.fileExists(atPath: markerURL.path))
        XCTAssertEqual(try String(contentsOf: markerURL, encoding: .utf8), "KEEP_THIS_TEST_FILE")
    }

    func testDoesNotModifyUnrelatedFilesUnderRoot() throws {
        let rootURL = try temporaryDirectory()
        let unrelatedDirectory = rootURL.appendingPathComponent("Unrelated", isDirectory: true)
        try FileManager.default.createDirectory(at: unrelatedDirectory, withIntermediateDirectories: true)
        let unrelatedFileURL = unrelatedDirectory.appendingPathComponent("keep.txt")
        try "UNCHANGED_TEST_CONTENT".write(to: unrelatedFileURL, atomically: true, encoding: .utf8)

        try preparer.prepareParentDirectory(for: localStoreURL(under: rootURL), under: rootURL)

        XCTAssertEqual(try String(contentsOf: unrelatedFileURL, encoding: .utf8), "UNCHANGED_TEST_CONTENT")
    }

    func testDoesNotWriteUnderRepositoryPrivateFixtures() throws {
        let rootURL = try temporaryDirectory()
        let storeURL = localStoreURL(under: rootURL)

        try preparer.prepareParentDirectory(for: storeURL, under: rootURL)

        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).standardizedFileURL.path
        XCTAssertFalse(storeURL.standardizedFileURL.path.hasPrefix(currentDirectory))
        XCTAssertFalse(storeURL.path.contains("/private/inputs/"))
        XCTAssertFalse(storeURL.path.contains("/private/jobagg/"))
    }

    func testDoesNotCreateAtlasVaultArtifacts() throws {
        let rootURL = try temporaryDirectory()

        try preparer.prepareParentDirectory(for: localStoreURL(under: rootURL), under: rootURL)

        XCTAssertFalse(containsAtlasVaultArtifact(under: rootURL))
    }

    func testSourceAvoidsKeychainDefaultsRuntimeWiringAndNetworking() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)

        for forbidden in [
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "UserDefaults",
            "ApplicationSupport",
            "applicationSupportDirectory",
            "FileManager.default.url",
            "AtlasLocalCache",
            "SearchViewModel",
            "URLSession",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    func testRejectsSymlinkEscapeFromRootWhenSupported() throws {
        let rootURL = try temporaryDirectory()
        let outsideRoot = try temporaryDirectory(named: "atlasvault-directory-preparer-symlink-outside")
        let symlinkURL = rootURL.appendingPathComponent("Atlas", isDirectory: true)
        do {
            try FileManager.default.createSymbolicLink(at: symlinkURL, withDestinationURL: outsideRoot)
        } catch {
            throw XCTSkip("Symlink creation is unavailable in this test environment: \(error)")
        }
        let storeURL = localStoreURL(under: rootURL)

        XCTAssertThrowsError(try preparer.prepareParentDirectory(for: storeURL, under: rootURL)) { error in
            XCTAssertEqual(error as? AtlasVaultDirectoryError, .unsupportedSymlink)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: outsideRoot.appendingPathComponent("Vaults").path))
    }

    func testResolvesSymlinkedRootAncestorBeforeCreatingDirectories() throws {
        let targetContainer = try temporaryDirectory(named: "atlasvault-directory-preparer-real-root")
        let realRootURL = targetContainer.appendingPathComponent("root", isDirectory: true)
        try FileManager.default.createDirectory(at: realRootURL, withIntermediateDirectories: true)
        let linkContainer = try temporaryDirectory(named: "atlasvault-directory-preparer-root-link")
        let linkURL = linkContainer.appendingPathComponent("link", isDirectory: true)
        do {
            try FileManager.default.createSymbolicLink(at: linkURL, withDestinationURL: targetContainer)
        } catch {
            throw XCTSkip("Symlink creation is unavailable in this test environment: \(error)")
        }
        let rootThroughSymlink = linkURL.appendingPathComponent("root", isDirectory: true)
        let storeURL = localStoreURL(under: rootThroughSymlink)
        let expectedParentURL = localStoreURL(under: realRootURL).deletingLastPathComponent()

        try preparer.prepareParentDirectory(for: storeURL, under: rootThroughSymlink)

        XCTAssertTrue(FileManager.default.directoryExists(at: expectedParentURL))
        XCTAssertFalse(FileManager.default.fileExists(atPath: localStoreURL(under: realRootURL).path))
    }

    private let preparer = AtlasFileManagerVaultDirectoryPreparer()

    private func localStoreURL(under rootURL: URL) -> URL {
        rootURL
            .appendingPathComponent("Atlas", isDirectory: true)
            .appendingPathComponent("Vaults", isDirectory: true)
            .appendingPathComponent("TEST_ONLY_VAULT_ID_0123456789", isDirectory: true)
            .appendingPathComponent("atlasvault-local-store.json", isDirectory: false)
            .standardizedFileURL
    }

    private func temporaryDirectory(named prefix: String = "atlasvault-directory-preparer-tests") throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(prefix)-\(UUID().uuidString)", isDirectory: true)
            .standardizedFileURL
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func containsAtlasVaultArtifact(under rootURL: URL) -> Bool {
        guard let enumerator = FileManager.default.enumerator(at: rootURL, includingPropertiesForKeys: nil) else {
            return false
        }
        for case let url as URL in enumerator where url.pathExtension == "atlasvault" {
            return true
        }
        return false
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/AtlasVaultDirectoryPreparer.swift"),
            sourceDirectory.appendingPathComponent("../../../../apps/apple/Sources/AtlasUI/AtlasVaultDirectoryPreparer.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find AtlasVaultDirectoryPreparer.swift")
    }

    private func testError(_ message: String) -> NSError {
        NSError(
            domain: "AtlasVaultDirectoryPreparerTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}

private extension FileManager {
    func directoryExists(at url: URL) -> Bool {
        var isDirectory = ObjCBool(false)
        return fileExists(atPath: url.path, isDirectory: &isDirectory) && isDirectory.boolValue
    }
}
