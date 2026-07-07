import XCTest
@testable import AtlasUI

final class AtlasVaultPathLocatorTests: XCTestCase {
    func testLocalStoreURLUsesInjectedRootAndGenericPathComponents() throws {
        let rootURL = temporaryRootURL()
        let locator = try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)
        let vaultID = "9f5c7a2e-6e2d-4df5-9f2d-6d5b4a3c2f10"

        let url = try locator.localStoreURL(vaultID: vaultID)

        XCTAssertEqual(
            url.path,
            rootURL.standardizedFileURL
                .appendingPathComponent("Atlas", isDirectory: true)
                .appendingPathComponent("Vaults", isDirectory: true)
                .appendingPathComponent(vaultID, isDirectory: true)
                .appendingPathComponent("atlasvault-local-store.json", isDirectory: false)
                .path
        )
    }

    func testLocalStoreURLIsStableForSameVaultID() throws {
        let locator = try AtlasInjectedRootVaultPathLocator(rootURL: temporaryRootURL())
        let vaultID = "ABCdef0123456789_-"

        let firstURL = try locator.localStoreURL(vaultID: vaultID)
        let secondURL = try locator.localStoreURL(vaultID: vaultID)

        XCTAssertEqual(firstURL, secondURL)
    }

    func testAllowsFilesystemSafeRandomIDShapes() throws {
        for vaultID in [
            "0123456789abcdef0123456789abcdef",
            "9f5c7a2e-6e2d-4df5-9f2d-6d5b4a3c2f10",
            "ABCdef0123456789_-",
        ] {
            XCTAssertEqual(try AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID), vaultID)
        }
    }

    func testRejectsInvalidVaultIDs() {
        let invalidVaultIDs = [
            "",
            " ",
            " vault",
            "vault ",
            ".",
            "..",
            "vault/id",
            "vault\\id",
            "vault.id",
            "vault+id",
            "vault=id",
            "vault id",
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata",
            String(repeating: "a", count: AtlasInjectedRootVaultPathLocator.maxVaultIDLength + 1),
        ]

        for vaultID in invalidVaultIDs {
            XCTAssertThrowsError(try AtlasInjectedRootVaultPathLocator.validatedVaultID(vaultID), vaultID) { error in
                XCTAssertEqual(error as? AtlasVaultPathLocatorError, .invalidVaultID)
            }
        }
    }

    func testRejectsNonFileRootURL() throws {
        let rootURL = try XCTUnwrap(URL(string: "https://example.invalid/atlas"))

        XCTAssertThrowsError(try AtlasInjectedRootVaultPathLocator(rootURL: rootURL)) { error in
            XCTAssertEqual(error as? AtlasVaultPathLocatorError, .invalidRootURL)
        }
    }

    func testPathDoesNotContainPrivateSentinelsOrRecordTypes() throws {
        let locator = try AtlasInjectedRootVaultPathLocator(rootURL: temporaryRootURL())
        let url = try locator.localStoreURL(vaultID: "9f5c7a2e-6e2d-4df5-9f2d-6d5b4a3c2f10")
        let path = url.path

        for forbidden in [
            "FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK",
            "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK",
            "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK",
            "FAKE_PRIVATE_NOTE_DO_NOT_LEAK",
            "FAKE_PROFILE_SNIPPET_DO_NOT_LEAK",
            "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK",
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata",
        ] {
            XCTAssertFalse(path.contains(forbidden), forbidden)
        }
    }

    func testTempRootKeepsPathOutsideRepositoryPrivateFixtures() throws {
        let locator = try AtlasInjectedRootVaultPathLocator(rootURL: temporaryRootURL())
        let url = try locator.localStoreURL(vaultID: "9f5c7a2e-6e2d-4df5-9f2d-6d5b4a3c2f10")
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).standardizedFileURL.path

        XCTAssertFalse(url.path.hasPrefix(currentDirectory))
        XCTAssertFalse(url.path.contains("/private/inputs/"))
        XCTAssertFalse(url.path.contains("/private/jobagg/"))
    }

    func testSourceAvoidsRuntimeStorageWiringAndSideEffectAPIs() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)

        for forbidden in [
            "ApplicationSupport",
            "applicationSupportDirectory",
            "FileManager.default.url",
            "createDirectory",
            "Data(",
            "write(",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "UserDefaults",
            "AtlasLocalCache",
            "SearchViewModel",
            "URLSession",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private func temporaryRootURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("atlasvault-path-locator-tests-\(UUID().uuidString)", isDirectory: true)
            .standardizedFileURL
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent("../../Sources/AtlasUI/AtlasVaultPathLocator.swift"),
            sourceDirectory.appendingPathComponent("../../../../apps/apple/Sources/AtlasUI/AtlasVaultPathLocator.swift"),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw testError("Could not find AtlasVaultPathLocator.swift")
    }

    private func testError(_ message: String) -> NSError {
        NSError(
            domain: "AtlasVaultPathLocatorTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}
