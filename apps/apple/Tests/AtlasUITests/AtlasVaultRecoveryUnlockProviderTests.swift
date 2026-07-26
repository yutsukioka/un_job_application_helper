import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultRecoveryUnlockProviderTests: XCTestCase {
    func testProviderUnwrapsVectorWithoutWritingOrActivating()
        async throws
    {
        let vector = try RecoveryUnlockVector.load()
        let recorder = RecoveryUnlockRecorder(store: vector.store)
        let provider = AtlasVaultRecoveryUnlockProvider(
            environment: AtlasVaultRecoveryUnlockEnvironment(
                loadStore: { vaultID in
                    try await recorder.loadStore(vaultID: vaultID)
                }
            )
        )

        let key = try await provider.deriveVaultKey(
            vaultID: vector.envelope.vaultMetadata.vaultID,
            recoverySecret: Data(vector.recoveryCode.utf8)
        )

        XCTAssertEqual(key, vector.vaultKey)
        XCTAssertEqual(await recorder.calls(), ["loadStore"])
        XCTAssertFalse(
            String(describing: provider).contains(vector.recoveryCode)
        )
    }

    func testProviderRejectsWrongKeyAndVaultBindingWithFixedErrors()
        async throws
    {
        let vector = try RecoveryUnlockVector.load()
        let provider = AtlasVaultRecoveryUnlockProvider(
            environment: AtlasVaultRecoveryUnlockEnvironment(
                loadStore: { _ in vector.store }
            )
        )

        await XCTAssertThrowsErrorAsync(
            try await provider.deriveVaultKey(
                vaultID: vector.envelope.vaultMetadata.vaultID,
                recoverySecret: Data(Self.wrongRecoveryCode.utf8)
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryUnlockFailure,
                .unlockFailed
            )
            XCTAssertFalse(
                String(describing: error).contains(
                    Self.wrongRecoveryCode
                )
            )
        }

        await XCTAssertThrowsErrorAsync(
            try await provider.deriveVaultKey(
                vaultID: "99999999-8888-4777-8666-555555555555",
                recoverySecret: Data(vector.recoveryCode.utf8)
            )
        ) { error in
            XCTAssertEqual(
                error as? AtlasVaultRecoveryUnlockFailure,
                .unlockFailed
            )
        }
    }

    func testCapabilityResolverReportsBothRecoveryOnlyAndLocalOnly()
        async throws
    {
        let vector = try RecoveryUnlockVector.load()
        let both = AtlasVaultProductionUnlockCapabilitiesResolver(
            environment: AtlasVaultUnlockCapabilitiesResolverEnvironment(
                loadLocalKey: { _ in vector.vaultKey },
                loadStore: { _ in vector.store }
            )
        )
        let selected = try AtlasSelectedVaultID(
            validating: vector.envelope.vaultMetadata.vaultID
        )

        XCTAssertEqual(
            try await both.capabilities(for: selected).availableMethods,
            [.localKey, .recoveryKey]
        )

        let recoveryOnly =
            AtlasVaultProductionUnlockCapabilitiesResolver(
                environment:
                    AtlasVaultUnlockCapabilitiesResolverEnvironment(
                        loadLocalKey: { _ in nil },
                        loadStore: { _ in vector.store }
                    )
            )
        XCTAssertEqual(
            try await recoveryOnly.capabilities(for: selected)
                .availableMethods,
            [.recoveryKey]
        )

        let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
            vaultID: selected.vaultID,
            crypto: vector.envelope.vaultMetadata.crypto,
            keyWraps: []
        )
        let localOnlyStore = AtlasVaultLocalStoreEnvelope(
            storeID: vector.store.storeID,
            createdAt: vector.store.createdAt,
            updatedAt: vector.store.updatedAt,
            vaultMetadata: try metadata.localStoreMetadata(),
            records: []
        )
        let localOnly =
            AtlasVaultProductionUnlockCapabilitiesResolver(
                environment:
                    AtlasVaultUnlockCapabilitiesResolverEnvironment(
                        loadLocalKey: { _ in vector.vaultKey },
                        loadStore: { _ in localOnlyStore }
                    )
            )
        XCTAssertEqual(
            try await localOnly.capabilities(for: selected)
                .availableMethods,
            [.localKey]
        )
    }

    func testRecoveryUnlockSourceContainsNoPersistenceBoundary()
        throws
    {
        let source = try phaseSource(
            "AtlasVaultRecoveryUnlockProvider.swift"
        )
        for forbidden in [
            "saveVaultKey",
            "createVaultKey",
            "storeSelection",
            "createSelection",
            "runtime.activate",
            "URLSession",
            "UserDefaults",
            "Task.detached",
        ] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
        for required in [
            "AtlasVaultRecoveryKeyCodec.parse",
            "AtlasVaultRecoveryWrapCrypto.unwrap",
            "recoveryKeyEnvelope",
            "vaultID",
        ] {
            XCTAssertTrue(source.contains(required), required)
        }
    }

    private func phaseSource(_ name: String) throws -> String {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/AtlasUI")
            .appendingPathComponent(name)
        return try String(contentsOf: sourceURL, encoding: .utf8)
    }

    private static let wrongRecoveryCode =
        "AVRK1-AEBA-GBAF-AYDQ-QCIK-BMGA-2DQP-CAIR-EEYU-CULB-OGAZ-DINR-YHI6-D4QC-5SUP-EHGQ"
}

private struct RecoveryUnlockVector {
    let envelope: AtlasVaultEncryptedExportEnvelope
    let store: AtlasVaultLocalStoreEnvelope
    let recoveryCode: String
    let vaultKey: Data

    static func load() throws -> RecoveryUnlockVector {
        let vector = try RecoveryImportVectorForUnlock.load()
        return RecoveryUnlockVector(
            envelope: vector.envelope,
            store: AtlasVaultLocalStoreEnvelope(
                storeID: "cccccccc-dddd-4eee-8fff-aaaaaaaaaaaa",
                createdAt: "2026-07-27T01:02:03Z",
                updatedAt: "2026-07-27T01:02:03Z",
                vaultMetadata: try vector.envelope.vaultMetadata
                    .localStoreMetadata(),
                records: vector.envelope.records
            ),
            recoveryCode: vector.recoveryCode,
            vaultKey: vector.vaultKey
        )
    }
}

private struct RecoveryImportVectorForUnlock {
    let envelope: AtlasVaultEncryptedExportEnvelope
    let recoveryCode: String
    let vaultKey: Data

    static func load() throws -> RecoveryImportVectorForUnlock {
        let testDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        let candidates = [
            testDirectory.appendingPathComponent(
                "../../../../contracts/sync/test_vectors/atlasvault_recovery_export_vectors_v2.json"
            ),
            testDirectory.appendingPathComponent(
                "../../../../../contracts/sync/test_vectors/atlasvault_recovery_export_vectors_v2.json"
            ),
        ].map(\.standardizedFileURL)
        let url = try XCTUnwrap(
            candidates.first {
                FileManager.default.fileExists(atPath: $0.path)
            }
        )
        let root = try XCTUnwrap(
            JSONSerialization.jsonObject(
                with: Data(contentsOf: url)
            ) as? [String: Any]
        )
        let vector = try XCTUnwrap(
            (root["vectors"] as? [[String: Any]])?.first
        )
        let data = try XCTUnwrap(
            Data(
                base64Encoded: XCTUnwrap(
                    vector["canonical_export_json_b64"] as? String
                )
            )
        )
        return try RecoveryImportVectorForUnlock(
            envelope: AtlasVaultEncryptedExportEnvelope.decodeStrict(data),
            recoveryCode: XCTUnwrap(
                vector["canonical_recovery_text"] as? String
            ),
            vaultKey: XCTUnwrap(
                Data(
                    base64Encoded: XCTUnwrap(
                        vector["test_only_vault_key_b64"] as? String
                    )
                )
            )
        )
    }
}

private actor RecoveryUnlockRecorder {
    private let store: AtlasVaultLocalStoreEnvelope
    private var recordedCalls: [String] = []

    init(store: AtlasVaultLocalStoreEnvelope) {
        self.store = store
    }

    func loadStore(vaultID _: String)
        -> AtlasVaultLocalStoreEnvelope?
    {
        recordedCalls.append("loadStore")
        return store
    }

    func calls() -> [String] {
        recordedCalls
    }
}

private func XCTAssertThrowsErrorAsync<T>(
    _ expression: @autoclosure () async throws -> T,
    _ errorHandler: (Error) -> Void = { _ in },
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        _ = try await expression()
        XCTFail("Expected expression to throw", file: file, line: line)
    } catch {
        errorHandler(error)
    }
}
