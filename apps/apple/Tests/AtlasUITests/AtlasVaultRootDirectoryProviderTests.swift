import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRootDirectoryProviderTests: XCTestCase {
    func testInjectedLocatorReturnsStandardizedFileURL() throws {
        let expected = try candidateRootURL()
        let provider = AtlasApplicationSupportVaultRootProvider(
            directoryLocator: StubDirectoryLocator(outcome: .url(expected))
        )

        XCTAssertEqual(try provider.rootDirectory(), expected.standardizedFileURL)
    }

    func testUnavailableDirectoryFailsSafely() {
        let provider = AtlasApplicationSupportVaultRootProvider(
            directoryLocator: StubDirectoryLocator(outcome: .unavailable)
        )

        XCTAssertThrowsError(try provider.rootDirectory()) { error in
            XCTAssertEqual(
                error as? AtlasVaultRootDirectoryError,
                .applicationSupportUnavailable
            )
        }
    }

    func testRejectsNonFileURL() throws {
        let url = try XCTUnwrap(URL(string: "https://example.invalid/atlas-root"))
        let provider = provider(returning: url)

        XCTAssertThrowsError(try provider.rootDirectory()) { error in
            XCTAssertEqual(error as? AtlasVaultRootDirectoryError, .invalidFileURL)
        }
    }

    func testRejectsMalformedAndRelativeFileURLs() throws {
        let candidates = [
            try XCTUnwrap(URL(string: "file:relative/root")),
            try XCTUnwrap(URL(string: "file:///")),
            try XCTUnwrap(URL(string: "file://remote.invalid/tmp/atlas-root")),
            try XCTUnwrap(URL(string: "file:///tmp/atlas%2Froot")),
        ]

        for candidate in candidates {
            XCTAssertThrowsError(try provider(returning: candidate).rootDirectory()) { error in
                XCTAssertEqual(
                    error as? AtlasVaultRootDirectoryError,
                    .malformedURL,
                    candidate.absoluteString
                )
            }
        }
    }

    func testSameProviderReturnsStableURL() throws {
        let expected = try candidateRootURL()
        let provider = provider(returning: expected)

        XCTAssertEqual(try provider.rootDirectory(), try provider.rootDirectory())
    }

    func testLookupCreatesNoDirectoryOrFile() throws {
        let candidate = try candidateRootURL()
        XCTAssertFalse(FileManager.default.fileExists(atPath: candidate.path))

        let returned = try provider(returning: candidate).rootDirectory()

        XCTAssertEqual(returned, candidate.standardizedFileURL)
        XCTAssertFalse(FileManager.default.fileExists(atPath: candidate.path))
        XCTAssertNil(try? FileManager.default.contentsOfDirectory(atPath: candidate.path))
    }

    func testRootContainsNoPrivateMetadataAndUsesNoRepositoryPath() throws {
        let candidate = try candidateRootURL()
        let url = try provider(returning: candidate).rootDirectory()
        let currentDirectory = URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath,
            isDirectory: true
        ).standardizedFileURL

        for forbidden in [
            "9f5c7a2e-6e2d-4df5-9f2d-6d5b4a3c2f10",
            "atlasvault-local-store.json",
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
            ".atlasvault",
        ] {
            XCTAssertFalse(url.path.contains(forbidden), forbidden)
        }
        XCTAssertFalse(url.path.hasPrefix(currentDirectory.path))
        XCTAssertFalse(url.path.contains("/private/inputs/"))
        XCTAssertFalse(url.path.contains("/private/jobagg/"))
    }

    func testErrorsDoNotExposeLocatorDetails() {
        let sentinel = "FAKE_PRIVATE_LOCATOR_FAILURE_DO_NOT_LEAK"
        let provider = AtlasApplicationSupportVaultRootProvider(
            directoryLocator: StubDirectoryLocator(outcome: .privateFailure(sentinel))
        )

        XCTAssertThrowsError(try provider.rootDirectory()) { error in
            XCTAssertEqual(
                error as? AtlasVaultRootDirectoryError,
                .applicationSupportUnavailable
            )
            XCTAssertFalse(String(reflecting: error).contains(sentinel))
        }
    }

    func testFoundationLocatorConformsWithoutAccessingHostDirectory() {
        let locator: any AtlasApplicationSupportDirectoryLocating =
            AtlasFoundationApplicationSupportDirectoryLocator()

        XCTAssertTrue(locator is AtlasFoundationApplicationSupportDirectoryLocator)
    }

    func testSourceAvoidsMutationAndRuntimeIntegration() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)

        for forbidden in [
            "createDirectory(",
            "createFile(",
            "removeItem(",
            "moveItem(",
            "replaceItem",
            ".write(",
            "AtlasInjectedRootVaultPathLocator",
            "localStoreURL(",
            "Keychain",
            "SecItem",
            "LAContext",
            "LocalAuthentication",
            "UserDefaults",
            "SwiftUI",
            "AtlasLocalCache",
            "SearchViewModel",
            "URLSession",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private func provider(returning url: URL) -> AtlasApplicationSupportVaultRootProvider {
        AtlasApplicationSupportVaultRootProvider(
            directoryLocator: StubDirectoryLocator(outcome: .url(url))
        )
    }

    private func candidateRootURL() throws -> URL {
        try AtlasVaultTestFileSystemSupport.canonicalTemporaryRoot()
            .appendingPathComponent(
                "atlasvault-root-provider-tests-\(UUID().uuidString)",
                isDirectory: true
            )
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent(
                "../../Sources/AtlasUI/AtlasVaultRootDirectoryProvider.swift"
            ),
            sourceDirectory.appendingPathComponent(
                "../../../../apps/apple/Sources/AtlasUI/AtlasVaultRootDirectoryProvider.swift"
            ),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw NSError(
            domain: "AtlasVaultRootDirectoryProviderTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Could not find provider source"]
        )
    }
}

private struct StubDirectoryLocator: AtlasApplicationSupportDirectoryLocating {
    enum Outcome: Sendable {
        case url(URL)
        case unavailable
        case privateFailure(String)
    }

    let outcome: Outcome

    func applicationSupportDirectory() throws -> URL {
        switch outcome {
        case let .url(url):
            return url
        case .unavailable:
            throw StubDirectoryLocatorError.unavailable
        case let .privateFailure(message):
            throw StubDirectoryLocatorError.privateFailure(message)
        }
    }
}

private enum StubDirectoryLocatorError: Error, Sendable {
    case unavailable
    case privateFailure(String)
}
