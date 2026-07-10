import Foundation
import Security
import XCTest
@testable import AtlasUI

final class AtlasVaultRuntimeCompositionTests: XCTestCase {
    private static let vaultIDOne = "2d290000-0000-4000-8000-000000000001"
    private static let vaultIDTwo = "2d290000-0000-4000-8000-000000000002"

    func testProductionCompositionInvokesNoRootKeychainOrAtomicFilesystemOperation() throws {
        let recorder = RuntimeCompositionCallRecorder()
        let candidateRoot = temporaryCandidateRoot(
            component: "production-construction-does-not-create"
        )
        XCTAssertFalse(FileManager.default.fileExists(atPath: candidateRoot.path))

        let services = AtlasVaultRuntimeFactory.production(
            directoryLocator: RecordingApplicationSupportDirectoryLocator(
                recorder: recorder,
                rootURL: candidateRoot
            ),
            keychainClient: RecordingKeychainClient(recorder: recorder),
            atomicFileSystemClient: RecordingAtomicFileSystemClient(recorder: recorder)
        )

        XCTAssertTrue(services.rootDirectoryProvider is AtlasApplicationSupportVaultRootProvider)
        XCTAssertTrue(services.keyStore is AtlasKeychainVaultKeyStore<RecordingKeychainClient>)
        XCTAssertTrue(services.perVaultFactory.atomicStoreWriter is AtlasVaultAtomicStoreWriter)
        XCTAssertEqual(recorder.events, [])
        XCTAssertFalse(FileManager.default.fileExists(atPath: candidateRoot.path))
    }

    func testDefaultProductionCompositionConstructsExpectedInactiveGraph() {
        let services = AtlasVaultRuntimeFactory.production()

        XCTAssertTrue(services.rootDirectoryProvider is AtlasApplicationSupportVaultRootProvider)
        XCTAssertTrue(
            services.keyStore is AtlasKeychainVaultKeyStore<SecItemAtlasKeychainClient>
        )
        requireStaticType(
            services.perVaultFactory.directoryPreparer,
            AtlasFileManagerVaultDirectoryPreparer.self
        )
        requireStaticType(
            services.perVaultFactory.localStoreIO,
            AtlasVaultLocalStoreFileIO.self
        )
        XCTAssertTrue(services.perVaultFactory.atomicStoreWriter is AtlasVaultAtomicStoreWriter)
        XCTAssertTrue(services.perVaultFactory.localStoreMerger is AtlasVaultLocalStoreMerger)
        XCTAssertTrue(services.perVaultFactory.recordSaver is AtlasVaultRecordSaver)
        XCTAssertTrue(services.perVaultFactory.recordHydrator is AtlasVaultRecordHydrator)
    }

    func testInjectedCompositionInvokesNoOperationalDependency() {
        let graph = makeInjectedGraph()

        XCTAssertEqual(graph.recorder.events, [])
        XCTAssertTrue(graph.services.rootDirectoryProvider is RecordingRootDirectoryProvider)
        XCTAssertTrue(graph.services.keyStore is RecordingVaultKeyStore)
        XCTAssertTrue(graph.services.perVaultFactory.directoryPreparer === graph.directoryPreparer)
        XCTAssertTrue(graph.services.perVaultFactory.localStoreIO === graph.localStoreIO)
        XCTAssertTrue(
            graph.services.perVaultFactory.atomicStoreWriter as? RecordingAtomicStoreWriter
                === graph.atomicStoreWriter
        )
        XCTAssertTrue(
            graph.services.perVaultFactory.localStoreMerger as? RecordingLocalStoreMerger
                === graph.localStoreMerger
        )
        XCTAssertTrue(
            graph.services.perVaultFactory.recordSaver as? RecordingRecordSaver
                === graph.recordSaver
        )
        XCTAssertTrue(
            graph.services.perVaultFactory.recordHydrator as? RecordingRecordHydrator
                === graph.recordHydrator
        )
    }

    func testPerVaultFactoryUsesExplicitRootAndNonSemanticVaultIDWithoutSideEffects() throws {
        let graph = makeInjectedGraph()
        let rootURL = temporaryCandidateRoot(component: "explicit-per-vault-root")

        let perVault = try graph.services.perVaultFactory.makeServices(
            rootURL: rootURL,
            vaultID: Self.vaultIDOne
        )

        XCTAssertEqual(perVault.vaultID, Self.vaultIDOne)
        XCTAssertEqual(graph.recorder.events, [])
        XCTAssertFalse(FileManager.default.fileExists(atPath: rootURL.path))
        XCTAssertEqual(
            try perVault.pathLocator.localStoreURL(vaultID: perVault.vaultID),
            rootURL
                .appendingPathComponent("Atlas", isDirectory: true)
                .appendingPathComponent("Vaults", isDirectory: true)
                .appendingPathComponent(Self.vaultIDOne, isDirectory: true)
                .appendingPathComponent("atlasvault-local-store.json", isDirectory: false)
        )
    }

    func testPerVaultScopesHaveDistinctPathsAndFreshCoordinatorValues() throws {
        let graph = makeInjectedGraph()
        let rootURL = temporaryCandidateRoot(component: "per-vault-isolation")

        let first = try graph.services.perVaultFactory.makeServices(
            rootURL: rootURL,
            vaultID: Self.vaultIDOne
        )
        let second = try graph.services.perVaultFactory.makeServices(
            rootURL: rootURL,
            vaultID: Self.vaultIDTwo
        )

        let firstURL = try first.pathLocator.localStoreURL(vaultID: first.vaultID)
        let secondURL = try second.pathLocator.localStoreURL(vaultID: second.vaultID)
        XCTAssertNotEqual(firstURL, secondURL)
        XCTAssertNotEqual(first.vaultID, second.vaultID)
        XCTAssertEqual(graph.recorder.events, [])
    }

    func testPerVaultScopeRetainsIntentionalInjectedSharing() throws {
        let graph = makeInjectedGraph()
        let perVault = try graph.services.perVaultFactory.makeServices(
            rootURL: temporaryCandidateRoot(component: "intentional-sharing"),
            vaultID: Self.vaultIDOne
        )

        XCTAssertTrue(perVault.directoryPreparer === graph.directoryPreparer)
        XCTAssertTrue(perVault.localStoreIO === graph.localStoreIO)
        XCTAssertTrue(perVault.atomicStoreWriter as? RecordingAtomicStoreWriter === graph.atomicStoreWriter)
        XCTAssertTrue(perVault.localStoreMerger as? RecordingLocalStoreMerger === graph.localStoreMerger)
        XCTAssertTrue(perVault.recordSaver as? RecordingRecordSaver === graph.recordSaver)
        XCTAssertTrue(perVault.recordHydrator as? RecordingRecordHydrator === graph.recordHydrator)
        XCTAssertEqual(graph.recorder.events, [])
    }

    func testPerVaultCoordinatorRejectsMismatchedSessionBeforeAnyOperation() throws {
        let graph = makeInjectedGraph()
        let perVault = try graph.services.perVaultFactory.makeServices(
            rootURL: temporaryCandidateRoot(component: "mismatched-session"),
            vaultID: Self.vaultIDOne
        )
        let mismatchedSession = try AtlasVaultUnlockedSession(
            vaultID: Self.vaultIDTwo,
            vaultKey: Data(repeating: 0x29, count: AtlasVaultRecordCrypto.vaultKeyByteCount)
        )

        XCTAssertThrowsError(
            try perVault.persistenceCoordinator.loadEncryptedStore(for: mismatchedSession)
        ) { error in
            XCTAssertEqual(error as? AtlasVaultPersistenceError, .invalidSession)
        }
        XCTAssertEqual(graph.recorder.events, [])
    }

    func testCompositionValuesAreSendable() throws {
        let graph = makeInjectedGraph()
        let perVault = try graph.services.perVaultFactory.makeServices(
            rootURL: temporaryCandidateRoot(component: "sendable"),
            vaultID: Self.vaultIDOne
        )

        requireSendable(graph.services)
        requireSendable(graph.services.perVaultFactory)
        requireSendable(perVault)
    }

    func testDescriptionsDoNotReflectDependenciesVaultIDOrPrivateValues() throws {
        let sentinel = "FAKE_PRIVATE_COMPOSITION_SENTINEL_DO_NOT_LEAK"
        let pathSentinel = "FAKE_PRIVATE_PATH_DO_NOT_LEAK"
        let searchSentinel = "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK"
        let jobSentinel = "FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK"
        let graph = makeInjectedGraph(marker: "\(sentinel)-\(searchSentinel)-\(jobSentinel)")
        let rootURL = URL(fileURLWithPath: "/tmp/\(pathSentinel)", isDirectory: true)
        let perVault = try graph.services.perVaultFactory.makeServices(
            rootURL: rootURL,
            vaultID: Self.vaultIDOne
        )
        let rendered = [
            String(describing: graph.services),
            String(reflecting: graph.services),
            String(describing: graph.services.perVaultFactory),
            String(reflecting: graph.services.perVaultFactory),
            String(describing: perVault),
            String(reflecting: perVault),
        ].joined(separator: " ")

        for forbidden in [
            sentinel,
            pathSentinel,
            searchSentinel,
            jobSentinel,
            Self.vaultIDOne,
            rootURL.path,
        ] {
            XCTAssertFalse(rendered.contains(forbidden), forbidden)
        }
        XCTAssertTrue(rendered.contains("<redacted>"))
    }

    func testCompositionDoesNotMutatePublicSnapshot() throws {
        let snapshot = try publicSnapshot()
        let before = try encodedSnapshot(snapshot)
        let graph = makeInjectedGraph()
        _ = try graph.services.perVaultFactory.makeServices(
            rootURL: temporaryCandidateRoot(component: "public-snapshot"),
            vaultID: Self.vaultIDOne
        )

        XCTAssertEqual(try encodedSnapshot(snapshot), before)
        XCTAssertEqual(graph.recorder.events, [])
    }

    func testSourceHasNoRuntimeCallSiteOrOperationalInvocation() throws {
        let source = try String(contentsOf: sourceURL(), encoding: .utf8)

        for forbidden in [
            "import SwiftUI",
            "SearchViewModel",
            "AtlasLocalCache",
            "AtlasPublicLocalSnapshot",
            "URLSession",
            "@main",
            "AtlasIOSHost",
            ".rootDirectory()",
            ".loadVaultKey(",
            ".saveVaultKey(",
            ".deleteVaultKey(",
            ".prepareParentDirectory(",
            ".read(",
            ".write(",
            ".hydrate(",
            "SecItemAdd(",
            "SecItemCopyMatching(",
            "SecItemUpdate(",
            "SecItemDelete(",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }

    private func makeInjectedGraph(
        marker: String = "FAKE_COMPOSITION_DEPENDENCY_DO_NOT_LEAK"
    ) -> InjectedRuntimeGraph {
        let recorder = RuntimeCompositionCallRecorder()
        let rootProvider = RecordingRootDirectoryProvider(recorder: recorder, marker: marker)
        let keyStore = RecordingVaultKeyStore(recorder: recorder, marker: marker)
        let directoryPreparer = RecordingDirectoryPreparer(recorder: recorder)
        let localStoreIO = RecordingLocalStoreIO(recorder: recorder)
        let atomicStoreWriter = RecordingAtomicStoreWriter(recorder: recorder)
        let localStoreMerger = RecordingLocalStoreMerger(recorder: recorder)
        let recordSaver = RecordingRecordSaver(recorder: recorder)
        let recordHydrator = RecordingRecordHydrator(recorder: recorder)
        let services = AtlasVaultRuntimeFactory.makeServices(
            rootDirectoryProvider: rootProvider,
            keyStore: keyStore,
            directoryPreparer: directoryPreparer,
            localStoreIO: localStoreIO,
            atomicStoreWriter: atomicStoreWriter,
            localStoreMerger: localStoreMerger,
            recordSaver: recordSaver,
            recordHydrator: recordHydrator
        )
        return InjectedRuntimeGraph(
            services: services,
            recorder: recorder,
            directoryPreparer: directoryPreparer,
            localStoreIO: localStoreIO,
            atomicStoreWriter: atomicStoreWriter,
            localStoreMerger: localStoreMerger,
            recordSaver: recordSaver,
            recordHydrator: recordHydrator
        )
    }

    private func temporaryCandidateRoot(component: String) -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("atlasvault-runtime-composition-tests", isDirectory: true)
            .appendingPathComponent("\(component)-\(UUID().uuidString)", isDirectory: true)
            .standardizedFileURL
    }

    private func publicSnapshot() throws -> AtlasPublicLocalSnapshot {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(AtlasPublicLocalSnapshot.self, from: Data("""
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
    }

    private func encodedSnapshot(_ snapshot: AtlasPublicLocalSnapshot) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(snapshot)
    }

    private func sourceURL() throws -> URL {
        let sourceDirectory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let candidates = [
            sourceDirectory.appendingPathComponent(
                "../../Sources/AtlasUI/AtlasVaultRuntimeComposition.swift"
            ),
            sourceDirectory.appendingPathComponent(
                "../../../../apps/apple/Sources/AtlasUI/AtlasVaultRuntimeComposition.swift"
            ),
        ].map(\.standardizedFileURL)

        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        throw NSError(
            domain: "AtlasVaultRuntimeCompositionTests",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Could not find composition source"]
        )
    }
}

private typealias InjectedRuntimeServices = AtlasVaultRuntimeServices<
    RecordingDirectoryPreparer,
    RecordingLocalStoreIO
>

private struct InjectedRuntimeGraph {
    let services: InjectedRuntimeServices
    let recorder: RuntimeCompositionCallRecorder
    let directoryPreparer: RecordingDirectoryPreparer
    let localStoreIO: RecordingLocalStoreIO
    let atomicStoreWriter: RecordingAtomicStoreWriter
    let localStoreMerger: RecordingLocalStoreMerger
    let recordSaver: RecordingRecordSaver
    let recordHydrator: RecordingRecordHydrator
}

private final class RuntimeCompositionCallRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var recordedEvents: [String] = []

    var events: [String] {
        lock.withLock { recordedEvents }
    }

    func record(_ event: String) {
        lock.withLock {
            recordedEvents.append(event)
        }
    }
}

private final class RecordingApplicationSupportDirectoryLocator:
    AtlasApplicationSupportDirectoryLocating,
    @unchecked Sendable
{
    private let recorder: RuntimeCompositionCallRecorder
    private let rootURL: URL

    init(recorder: RuntimeCompositionCallRecorder, rootURL: URL) {
        self.recorder = recorder
        self.rootURL = rootURL
    }

    func applicationSupportDirectory() throws -> URL {
        recorder.record("applicationSupportDirectory")
        return rootURL
    }
}

private final class RecordingRootDirectoryProvider:
    AtlasVaultRootDirectoryProviding,
    CustomStringConvertible,
    @unchecked Sendable
{
    private let recorder: RuntimeCompositionCallRecorder
    private let marker: String

    init(recorder: RuntimeCompositionCallRecorder, marker: String) {
        self.recorder = recorder
        self.marker = marker
    }

    var description: String { marker }

    func rootDirectory() throws -> URL {
        recorder.record("rootDirectory")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }
}

private final class RecordingKeychainClient: AtlasKeychainClient, @unchecked Sendable {
    private let recorder: RuntimeCompositionCallRecorder

    init(recorder: RuntimeCompositionCallRecorder) {
        self.recorder = recorder
    }

    func add(_ item: AtlasKeychainItem) -> OSStatus {
        recorder.record("keychain.add")
        return errSecSuccess
    }

    func copyMatching(_ query: AtlasKeychainQuery) -> AtlasKeychainCopyResult {
        recorder.record("keychain.copyMatching")
        return AtlasKeychainCopyResult(status: errSecItemNotFound, valueData: nil)
    }

    func update(_ query: AtlasKeychainQuery, with attributes: AtlasKeychainUpdate) -> OSStatus {
        recorder.record("keychain.update")
        return errSecSuccess
    }

    func delete(_ query: AtlasKeychainQuery) -> OSStatus {
        recorder.record("keychain.delete")
        return errSecSuccess
    }
}

private final class RecordingVaultKeyStore:
    AtlasVaultKeyStore,
    CustomStringConvertible,
    @unchecked Sendable
{
    private let recorder: RuntimeCompositionCallRecorder
    private let marker: String

    init(recorder: RuntimeCompositionCallRecorder, marker: String) {
        self.recorder = recorder
        self.marker = marker
    }

    var description: String { marker }

    func loadVaultKey(for vaultID: String) throws -> Data? {
        recorder.record("keyStore.load")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }

    func saveVaultKey(_ key: Data, for vaultID: String) throws {
        recorder.record("keyStore.save")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }

    func deleteVaultKey(for vaultID: String) throws {
        recorder.record("keyStore.delete")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }
}

private final class RecordingDirectoryPreparer: AtlasVaultDirectoryPreparer, @unchecked Sendable {
    private let recorder: RuntimeCompositionCallRecorder

    init(recorder: RuntimeCompositionCallRecorder) {
        self.recorder = recorder
    }

    func prepareParentDirectory(for storeURL: URL, under rootDirectory: URL) throws {
        recorder.record("directory.prepare")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }
}

private final class RecordingLocalStoreIO: AtlasVaultLocalStoreProviding, @unchecked Sendable {
    private let recorder: RuntimeCompositionCallRecorder

    init(recorder: RuntimeCompositionCallRecorder) {
        self.recorder = recorder
    }

    func read(from url: URL) throws -> AtlasVaultLocalStoreEnvelope {
        recorder.record("localStore.read")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }

    func write(_ store: AtlasVaultLocalStoreEnvelope, to url: URL, overwrite: Bool) throws {
        recorder.record("localStore.write")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }
}

private final class RecordingAtomicStoreWriter: AtlasVaultAtomicStoreWriting, @unchecked Sendable {
    private let recorder: RuntimeCompositionCallRecorder

    init(recorder: RuntimeCompositionCallRecorder) {
        self.recorder = recorder
    }

    func write(
        _ store: AtlasVaultLocalStoreEnvelope,
        to destinationURL: URL,
        overwrite: Bool
    ) throws -> AtlasVaultAtomicWriteResult {
        recorder.record("atomicStore.write")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }
}

private final class RecordingAtomicFileSystemClient:
    AtlasVaultAtomicFileSystemClient,
    @unchecked Sendable
{
    private let recorder: RuntimeCompositionCallRecorder

    init(recorder: RuntimeCompositionCallRecorder) {
        self.recorder = recorder
    }

    func validatePreparedParent(for destinationURL: URL) throws {
        recorder.record("atomicFileSystem.validateParent")
    }

    func createTemporaryFile(at url: URL) throws {
        recorder.record("atomicFileSystem.create")
    }

    func protectTemporaryFile(at url: URL) throws {
        recorder.record("atomicFileSystem.protect")
    }

    func write(_ data: Data, to url: URL) throws {
        recorder.record("atomicFileSystem.write")
    }

    func read(from url: URL) throws -> Data {
        recorder.record("atomicFileSystem.read")
        return Data()
    }

    func synchronizeFile(at url: URL) throws {
        recorder.record("atomicFileSystem.synchronizeFile")
    }

    func commitTemporaryFile(at temporaryURL: URL, to destinationURL: URL, overwrite: Bool) throws {
        recorder.record("atomicFileSystem.commit")
    }

    func synchronizeDirectory(at url: URL) throws {
        recorder.record("atomicFileSystem.synchronizeDirectory")
    }

    func removeItemIfExists(at url: URL) throws {
        recorder.record("atomicFileSystem.remove")
    }
}

private final class RecordingLocalStoreMerger: AtlasVaultLocalStoreMerging, @unchecked Sendable {
    private let recorder: RuntimeCompositionCallRecorder

    init(recorder: RuntimeCompositionCallRecorder) {
        self.recorder = recorder
    }

    func merge(
        records incoming: [AtlasVaultEncryptedRecordEnvelope],
        into store: AtlasVaultLocalStoreEnvelope
    ) throws -> AtlasVaultLocalStoreEnvelope {
        recorder.record("merger.merge")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }
}

private final class RecordingRecordSaver: AtlasVaultRecordSaving, @unchecked Sendable {
    private let recorder: RuntimeCompositionCallRecorder

    init(recorder: RuntimeCompositionCallRecorder) {
        self.recorder = recorder
    }

    func save(
        mutations: AtlasVaultMutationSet,
        session: AtlasVaultUnlockedSession
    ) throws -> [AtlasVaultEncryptedRecordEnvelope] {
        recorder.record("recordSaver.save")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }
}

private final class RecordingRecordHydrator: AtlasVaultRecordHydrating, @unchecked Sendable {
    private let recorder: RuntimeCompositionCallRecorder

    init(recorder: RuntimeCompositionCallRecorder) {
        self.recorder = recorder
    }

    func hydrate(
        records: [AtlasVaultEncryptedRecordEnvelope],
        session: AtlasVaultUnlockedSession
    ) throws -> AtlasVaultHydratedState {
        recorder.record("recordHydrator.hydrate")
        throw RecordingRuntimeCompositionError.unexpectedInvocation
    }
}

private enum RecordingRuntimeCompositionError: Error {
    case unexpectedInvocation
}

private func requireSendable<Value: Sendable>(_ value: Value) {}

private func requireStaticType<Value>(_ value: Value, _: Value.Type) {}
