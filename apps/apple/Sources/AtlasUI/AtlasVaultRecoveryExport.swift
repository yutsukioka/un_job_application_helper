import Foundation
import Security

public enum AtlasVaultRecoveryExportFailure:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case unavailable
    case unauthorized
    case invalidConfirmation
    case pendingSetupRequiresRecoveryKey
    case alreadyRecoveryPrepared
    case durabilityVerificationRequired
    case recoveryRequired
    case completionPending
    case cancelled

    public var description: String {
        switch self {
        case .unavailable:
            return "Recovery setup is unavailable."
        case .unauthorized:
            return "Unlock the vault to continue recovery setup."
        case .invalidConfirmation:
            return "Recovery key confirmation did not match."
        case .pendingSetupRequiresRecoveryKey:
            return "Enter the saved recovery key to continue."
        case .alreadyRecoveryPrepared:
            return "Recovery setup already exists."
        case .durabilityVerificationRequired:
            return "Recovery setup requires explicit verification."
        case .recoveryRequired:
            return "Recovery setup requires attention."
        case .completionPending:
            return "Encrypted export completion is pending."
        case .cancelled:
            return "Recovery setup is paused."
        }
    }

    public var debugDescription: String {
        description
    }
}

public actor AtlasVaultRecoveryDisplayCodeHandle:
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private var code: String?

    init(code: String) {
        self.code = code
    }

    public func take() -> String? {
        guard let value = code else {
            return nil
        }
        code = String(repeating: " ", count: value.count)
        code = nil
        return value
    }

    public nonisolated var description: String {
        "AtlasVaultRecoveryDisplayCodeHandle(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }
}

public protocol AtlasVaultRecoveryExportCoordinating: Sendable {
    func prepareNewRecovery() async throws
        -> AtlasVaultRecoveryDisplayCodeHandle
    func confirmAndPrepareExport(
        secret: String
    ) async throws -> AtlasVaultEncryptedDocument
    func resumeAndPrepareExport(
        secret: String
    ) async throws -> AtlasVaultEncryptedDocument
    func exportDidSucceed() async throws
    func exportDidFailOrCancel() async
    func resetPendingSetup() async throws
    func hasPendingSetup() async throws -> Bool
    func stop() async
}

struct AtlasVaultRecoveryExportJournal:
    Codable,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    static let expectedFormat = "atlasvault-recovery-export-setup"
    static let currentVersion = 1

    let format: String
    let version: Int
    let vaultID: String
    let storeID: String
    let wrapID: String
    let wrap: AtlasVaultRecoveryWrappedKeyEnvelope
    let exportID: String
    let createdAt: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case format
        case version
        case vaultID = "vault_id"
        case storeID = "store_id"
        case wrapID = "wrap_id"
        case wrap
        case exportID = "export_id"
        case createdAt = "created_at"
    }

    init(
        format: String = Self.expectedFormat,
        version: Int = Self.currentVersion,
        vaultID: String,
        storeID: String,
        wrapID: String =
            AtlasVaultRecoveryWrappedKeyEnvelope.supportedID,
        wrap: AtlasVaultRecoveryWrappedKeyEnvelope,
        exportID: String,
        createdAt: String
    ) throws {
        guard
            format == Self.expectedFormat,
            version == Self.currentVersion,
            (try? AtlasInjectedRootVaultPathLocator.validatedVaultID(
                vaultID
            )) == vaultID,
            Self.isCanonicalLowercaseUUID(storeID),
            wrapID == AtlasVaultRecoveryWrappedKeyEnvelope.supportedID,
            wrap.id == wrapID,
            Self.isCanonicalLowercaseUUID(exportID),
            Self.isStrictUTCTimestamp(createdAt)
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        self.format = format
        self.version = version
        self.vaultID = vaultID
        self.storeID = storeID
        self.wrapID = wrapID
        self.wrap = wrap
        self.exportID = exportID
        self.createdAt = createdAt
    }

    var description: String {
        "AtlasVaultRecoveryExportJournal(<redacted>)"
    }

    var debugDescription: String {
        description
    }

    static func isCanonicalLowercaseUUID(_ value: String) -> Bool {
        guard let uuid = UUID(uuidString: value) else {
            return false
        }
        return uuid.uuidString.lowercased() == value
    }

    static func isStrictUTCTimestamp(_ value: String) -> Bool {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        guard let date = formatter.date(from: value) else {
            return false
        }
        return formatter.string(from: date) == value
    }
}

protocol AtlasVaultRecoveryExportJournalStoring: Sendable {
    func loadJournal() throws -> AtlasVaultRecoveryExportJournal?
    func saveJournal(_ journal: AtlasVaultRecoveryExportJournal) throws
    func clearJournal() throws
}

struct AtlasKeychainVaultRecoveryExportJournalStore<
    Client: AtlasKeychainClient
>: AtlasVaultRecoveryExportJournalStoring {
    static var service: String {
        "com.atlasvault.recovery-export"
    }

    static var account: String {
        "pending-v2"
    }

    private let client: Client

    init(client: Client) {
        self.client = client
    }

    func loadJournal() throws -> AtlasVaultRecoveryExportJournal? {
        let result = client.copyMatching(Self.query)
        switch result.status {
        case errSecItemNotFound:
            return nil
        case errSecSuccess:
            guard let data = result.valueData else {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            }
            return try Self.decode(data)
        default:
            throw AtlasVaultRecoveryExportFailure.unavailable
        }
    }

    func saveJournal(_ journal: AtlasVaultRecoveryExportJournal) throws {
        let data = try Self.encode(journal)
        let item = AtlasKeychainItem(
            service: Self.service,
            account: Self.account,
            valueData: data,
            accessibility: .afterFirstUnlockThisDeviceOnly
        )
        switch client.add(item) {
        case errSecSuccess:
            return
        case errSecDuplicateItem:
            guard
                client.update(
                    Self.query,
                    with: AtlasKeychainUpdate(valueData: data)
                ) == errSecSuccess
            else {
                throw AtlasVaultRecoveryExportFailure.unavailable
            }
        default:
            throw AtlasVaultRecoveryExportFailure.unavailable
        }
    }

    func clearJournal() throws {
        switch client.delete(Self.query) {
        case errSecSuccess, errSecItemNotFound:
            return
        default:
            throw AtlasVaultRecoveryExportFailure.completionPending
        }
    }

    private static var query: AtlasKeychainQuery {
        AtlasKeychainQuery(service: service, account: account)
    }

    private static func encode(
        _ journal: AtlasVaultRecoveryExportJournal
    ) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        do {
            return try encoder.encode(journal)
        } catch {
            throw AtlasVaultRecoveryExportFailure.unavailable
        }
    }

    private static func decode(
        _ data: Data
    ) throws -> AtlasVaultRecoveryExportJournal {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let root = object as? [String: Any],
            Set(root.keys)
                == Set(
                    AtlasVaultRecoveryExportJournal.CodingKeys
                        .allCases.map(\.rawValue)
                ),
            let wrap = root["wrap"] as? [String: Any],
            Set(wrap.keys) == [
                "id",
                "type",
                "wrap_version",
                "kdf",
                "nonce",
                "ciphertext",
            ],
            let journal = try? JSONDecoder().decode(
                AtlasVaultRecoveryExportJournal.self,
                from: data
            )
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        return try AtlasVaultRecoveryExportJournal(
            format: journal.format,
            version: journal.version,
            vaultID: journal.vaultID,
            storeID: journal.storeID,
            wrapID: journal.wrapID,
            wrap: journal.wrap,
            exportID: journal.exportID,
            createdAt: journal.createdAt
        )
    }
}

struct AtlasVaultRecoveryExportEnvironment: Sendable {
    let authorize: @Sendable () async -> Bool
    let selectVault: @Sendable () async throws -> AtlasSelectedVaultID?
    let loadVaultKey: @Sendable (String) async throws -> Data?
    let loadStore: @Sendable (
        String,
        Data
    ) async throws -> AtlasVaultLocalStoreEnvelope?
    let saveStore: @Sendable (
        AtlasVaultLocalStoreEnvelope,
        String,
        Data
    ) async throws -> AtlasVaultAtomicWriteResult
    let hydrate: @Sendable (
        [AtlasVaultEncryptedRecordEnvelope],
        String,
        Data
    ) async throws -> Void
    let loadJournal:
        @Sendable () async throws -> AtlasVaultRecoveryExportJournal?
    let saveJournal:
        @Sendable (AtlasVaultRecoveryExportJournal) async throws -> Void
    let clearJournal: @Sendable () async throws -> Void
    let generateRecoveryKey: @Sendable () throws -> Data
    let generateSalt: @Sendable () throws -> Data
    let generateNonce: @Sendable () throws -> Data
    let generateID: @Sendable () -> String
    let timestamp: @Sendable () -> String
}

public actor AtlasVaultRecoveryExportCoordinator:
    AtlasVaultRecoveryExportCoordinating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum OperationKind: Equatable {
        case prepare
        case confirm
        case resume
        case reset
    }

    private enum OperationValue: Sendable {
        case display(AtlasVaultRecoveryDisplayCodeHandle)
        case document(AtlasVaultEncryptedDocument)
        case complete
    }

    private struct ActiveOperation {
        let identifier: UUID
        let kind: OperationKind
        let task: Task<
            Result<OperationValue, AtlasVaultRecoveryExportFailure>,
            Never
        >
    }

    private struct PreparedState {
        var recoveryKey: Data
        let journal: AtlasVaultRecoveryExportJournal
    }

    private let environment: AtlasVaultRecoveryExportEnvironment
    private var prepared: PreparedState?
    private var activeOperation: ActiveOperation?
    private var terminal = false

    init(environment: AtlasVaultRecoveryExportEnvironment) {
        self.environment = environment
    }

    public func prepareNewRecovery() async throws
        -> AtlasVaultRecoveryDisplayCodeHandle
    {
        let value = try await runRetained(.prepare) { [self] in
            await prepareOperation()
        }
        guard case let .display(handle) = value else {
            throw AtlasVaultRecoveryExportFailure.unavailable
        }
        return handle
    }

    public func confirmAndPrepareExport(
        secret: String
    ) async throws -> AtlasVaultEncryptedDocument {
        let value = try await runRetained(.confirm) { [self] in
            await confirmOperation(secret: secret)
        }
        guard case let .document(document) = value else {
            throw AtlasVaultRecoveryExportFailure.unavailable
        }
        return document
    }

    public func resumeAndPrepareExport(
        secret: String
    ) async throws -> AtlasVaultEncryptedDocument {
        let value = try await runRetained(.resume) { [self] in
            await resumeOperation(secret: secret)
        }
        guard case let .document(document) = value else {
            throw AtlasVaultRecoveryExportFailure.unavailable
        }
        return document
    }

    public func exportDidSucceed() async throws {
        try await requireAuthorization()
        guard !Task.isCancelled, !terminal else {
            throw AtlasVaultRecoveryExportFailure.cancelled
        }
        if try await environment.loadJournal() != nil {
            try await requireAuthorization()
            try await environment.clearJournal()
        }
        wipePrepared()
    }

    public func exportDidFailOrCancel() async {
        let task = activeOperation?.task
        task?.cancel()
        _ = await task?.value
        activeOperation = nil
        wipePrepared()
    }

    public func resetPendingSetup() async throws {
        _ = try await runRetained(.reset) { [self] in
            await resetOperation()
        }
    }

    public func hasPendingSetup() async throws -> Bool {
        try await requireAuthorization()
        return try await environment.loadJournal() != nil
    }

    public func stop() async {
        terminal = true
        let task = activeOperation?.task
        task?.cancel()
        _ = await task?.value
        activeOperation = nil
        wipePrepared()
    }

    public nonisolated var description: String {
        "AtlasVaultRecoveryExportCoordinator(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    static func production<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding,
        Selector: AtlasVaultIDSelecting
    >(
        runtimeServices: AtlasVaultRuntimeServices<
            DirectoryPreparer,
            LocalStoreIO
        >,
        selector: Selector,
        journalStore: any AtlasVaultRecoveryExportJournalStoring,
        authorize: @escaping @Sendable () async -> Bool
    ) -> AtlasVaultRecoveryExportCoordinator {
        let environment = AtlasVaultRecoveryExportEnvironment(
            authorize: authorize,
            selectVault: {
                switch try await selector.selectVaultID() {
                case .none:
                    return nil
                case let .selected(selected):
                    return selected
                }
            },
            loadVaultKey: { vaultID in
                try runtimeServices.keyStore.loadVaultKey(for: vaultID)
            },
            loadStore: { vaultID, vaultKey in
                let root = try runtimeServices.rootDirectoryProvider
                    .rootDirectory()
                let services = try runtimeServices.perVaultFactory
                    .makeServices(rootURL: root, vaultID: vaultID)
                let session = try AtlasVaultUnlockedSession(
                    vaultID: vaultID,
                    vaultKey: vaultKey
                )
                return try services.persistenceCoordinator
                    .loadEncryptedStore(for: session)
            },
            saveStore: { store, vaultID, vaultKey in
                let root = try runtimeServices.rootDirectoryProvider
                    .rootDirectory()
                let services = try runtimeServices.perVaultFactory
                    .makeServices(rootURL: root, vaultID: vaultID)
                let session = try AtlasVaultUnlockedSession(
                    vaultID: vaultID,
                    vaultKey: vaultKey
                )
                return try services.persistenceCoordinator
                    .saveEncryptedStoreAtomically(
                        store,
                        for: session,
                        overwrite: true
                    )
            },
            hydrate: { records, vaultID, vaultKey in
                let root = try runtimeServices.rootDirectoryProvider
                    .rootDirectory()
                let services = try runtimeServices.perVaultFactory
                    .makeServices(rootURL: root, vaultID: vaultID)
                let session = try AtlasVaultUnlockedSession(
                    vaultID: vaultID,
                    vaultKey: vaultKey
                )
                _ = try services.recordHydrator.hydrate(
                    records: records,
                    session: session
                )
            },
            loadJournal: {
                try journalStore.loadJournal()
            },
            saveJournal: { journal in
                try journalStore.saveJournal(journal)
            },
            clearJournal: {
                try journalStore.clearJournal()
            },
            generateRecoveryKey: {
                try AtlasVaultRecoveryKeyCodec.generate()
            },
            generateSalt: {
                try AtlasVaultRecoveryKeyCodec.secureRandomData(count: 32)
            },
            generateNonce: {
                try AtlasVaultRecoveryKeyCodec.secureRandomData(count: 12)
            },
            generateID: {
                UUID().uuidString.lowercased()
            },
            timestamp: {
                Self.currentTimestamp()
            }
        )
        return AtlasVaultRecoveryExportCoordinator(environment: environment)
    }

    private func runRetained(
        _ kind: OperationKind,
        operation: @escaping @Sendable () async
            -> Result<
                OperationValue,
                AtlasVaultRecoveryExportFailure
            >
    ) async throws -> OperationValue {
        guard !terminal else {
            throw AtlasVaultRecoveryExportFailure.cancelled
        }
        if let activeOperation {
            guard activeOperation.kind == kind else {
                throw AtlasVaultRecoveryExportFailure.unavailable
            }
            return try await activeOperation.task.value.get()
        }
        let identifier = UUID()
        let task = Task {
            await operation()
        }
        activeOperation = ActiveOperation(
            identifier: identifier,
            kind: kind,
            task: task
        )
        let result = await task.value
        if activeOperation?.identifier == identifier {
            activeOperation = nil
        }
        return try result.get()
    }

    private func prepareOperation() async
        -> Result<OperationValue, AtlasVaultRecoveryExportFailure>
    {
        do {
            try await requireAuthorization()
            guard try await environment.loadJournal() == nil else {
                throw AtlasVaultRecoveryExportFailure
                    .pendingSetupRequiresRecoveryKey
            }
            var context = try await loadContext()
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(
                    &context.vaultKey
                )
            }
            let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: context.store.vaultMetadata
            )
            guard metadata.recoveryKeyWrap == nil else {
                throw AtlasVaultRecoveryExportFailure
                    .alreadyRecoveryPrepared
            }
            try Task.checkCancellation()
            var recoveryKey = try environment.generateRecoveryKey()
            do {
                let wrap = try AtlasVaultRecoveryWrapCrypto.wrap(
                    vaultKey: context.vaultKey,
                    recoveryKey: recoveryKey,
                    vaultID: context.selection.vaultID,
                    salt: try environment.generateSalt(),
                    nonce: try environment.generateNonce()
                )
                let journal = try AtlasVaultRecoveryExportJournal(
                    vaultID: context.selection.vaultID,
                    storeID: context.store.storeID,
                    wrap: wrap,
                    exportID: environment.generateID(),
                    createdAt: environment.timestamp()
                )
                let text = try AtlasVaultRecoveryKeyCodec.canonicalText(
                    for: recoveryKey
                )
                wipePrepared()
                prepared = PreparedState(
                    recoveryKey: recoveryKey,
                    journal: journal
                )
                recoveryKey = Data()
                return .success(
                    .display(
                        AtlasVaultRecoveryDisplayCodeHandle(code: text)
                    )
                )
            } catch {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&recoveryKey)
                throw error
            }
        } catch {
            return .failure(map(error))
        }
    }

    private func confirmOperation(
        secret: String
    ) async -> Result<OperationValue, AtlasVaultRecoveryExportFailure> {
        do {
            try await requireAuthorization()
            guard var preparedState = prepared else {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            }
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(
                    &preparedState.recoveryKey
                )
            }
            var entered = try AtlasVaultRecoveryKeyCodec.parse(secret)
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&entered)
            }
            guard AtlasVaultRecoveryKeyCodec.constantTimeEqual(
                entered,
                preparedState.recoveryKey
            ) else {
                throw AtlasVaultRecoveryExportFailure.invalidConfirmation
            }
            try Task.checkCancellation()
            try await requireAuthorization()
            try await environment.saveJournal(preparedState.journal)
            let document = try await commitAndVerify(
                journal: preparedState.journal,
                recoveryKey: entered
            )
            wipePrepared()
            return .success(.document(document))
        } catch {
            return .failure(map(error))
        }
    }

    private func resumeOperation(
        secret: String
    ) async -> Result<OperationValue, AtlasVaultRecoveryExportFailure> {
        do {
            try await requireAuthorization()
            var recoveryKey = try AtlasVaultRecoveryKeyCodec.parse(secret)
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&recoveryKey)
            }
            if let journal = try await environment.loadJournal() {
                return .success(
                    .document(
                        try await commitAndVerify(
                            journal: journal,
                            recoveryKey: recoveryKey
                        )
                    )
                )
            }
            var context = try await loadContext()
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(
                    &context.vaultKey
                )
            }
            let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: context.store.vaultMetadata
            )
            guard let wrap = metadata.recoveryKeyWrap else {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            }
            var recovered = try AtlasVaultRecoveryWrapCrypto.unwrap(
                wrap,
                recoveryKey: recoveryKey,
                vaultID: context.selection.vaultID
            )
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&recovered)
            }
            guard AtlasVaultRecoveryWrapCrypto.constantTimeEqual(
                recovered,
                context.vaultKey
            ) else {
                throw AtlasVaultRecoveryExportFailure.invalidConfirmation
            }
            let transient = try AtlasVaultRecoveryExportJournal(
                vaultID: context.selection.vaultID,
                storeID: context.store.storeID,
                wrap: wrap,
                exportID: environment.generateID(),
                createdAt: environment.timestamp()
            )
            return .success(
                .document(
                    try await verifyExport(
                        journal: transient,
                        store: context.store,
                        metadata: metadata,
                        recoveryKey: recoveryKey,
                        localVaultKey: context.vaultKey
                    )
                )
            )
        } catch {
            return .failure(map(error))
        }
    }

    private func resetOperation() async
        -> Result<OperationValue, AtlasVaultRecoveryExportFailure>
    {
        do {
            try await requireAuthorization()
            guard let journal = try await environment.loadJournal() else {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            }
            var context = try await loadContext()
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(
                    &context.vaultKey
                )
            }
            guard
                context.selection.vaultID == journal.vaultID,
                context.store.storeID == journal.storeID
            else {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            }
            let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: context.store.vaultMetadata
            )
            let matches = metadata.keyWraps.filter {
                $0.recoveryKeyEnvelope?.id == journal.wrapID
            }
            guard matches.count <= 1 else {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            }
            if let existing = matches.first?.recoveryKeyEnvelope {
                guard existing == journal.wrap else {
                    throw AtlasVaultRecoveryExportFailure.recoveryRequired
                }
                let retained = metadata.keyWraps.filter {
                    $0.recoveryKeyEnvelope?.id != journal.wrapID
                }
                let updatedMetadata =
                    try AtlasVaultVersionedWrappedKeyMetadata(
                        vaultID: metadata.vaultID,
                        crypto: metadata.crypto,
                        keyWraps: retained
                    )
                let updatedStore = AtlasVaultLocalStoreEnvelope(
                    format: context.store.format,
                    version: context.store.version,
                    storeID: context.store.storeID,
                    createdAt: context.store.createdAt,
                    updatedAt: environment.timestamp(),
                    vaultMetadata: try updatedMetadata.localStoreMetadata(),
                    records: context.store.records
                )
                try Task.checkCancellation()
                try await requireAuthorization()
                let result = try await environment.saveStore(
                    updatedStore,
                    context.selection.vaultID,
                    context.vaultKey
                )
                guard result.commitState == .committed else {
                    throw AtlasVaultRecoveryExportFailure
                        .durabilityVerificationRequired
                }
                try Task.checkCancellation()
                try await requireAuthorization()
                guard
                    let verifiedStore = try await environment.loadStore(
                        context.selection.vaultID,
                        context.vaultKey
                    ),
                    verifiedStore.storeID == context.store.storeID,
                    verifiedStore.records == context.store.records,
                    try AtlasVaultVersionedWrappedKeyMetadata(
                        localStoreMetadata:
                            verifiedStore.vaultMetadata
                    ) == updatedMetadata
                else {
                    throw AtlasVaultRecoveryExportFailure.recoveryRequired
                }
            }
            try Task.checkCancellation()
            try await requireAuthorization()
            try await environment.clearJournal()
            wipePrepared()
            return .success(.complete)
        } catch {
            return .failure(map(error))
        }
    }

    private struct LoadedContext {
        let selection: AtlasSelectedVaultID
        var vaultKey: Data
        let store: AtlasVaultLocalStoreEnvelope
    }

    private func loadContext() async throws -> LoadedContext {
        try await requireAuthorization()
        guard let selection = try await environment.selectVault() else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        guard var vaultKey = try await environment.loadVaultKey(
            selection.vaultID
        ) else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        do {
            guard let store = try await environment.loadStore(
                selection.vaultID,
                vaultKey
            ) else {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            }
            return LoadedContext(
                selection: selection,
                vaultKey: vaultKey,
                store: store
            )
        } catch {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&vaultKey)
            throw error
        }
    }

    private func commitAndVerify(
        journal: AtlasVaultRecoveryExportJournal,
        recoveryKey: Data
    ) async throws -> AtlasVaultEncryptedDocument {
        var context = try await loadContext()
        defer {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&context.vaultKey)
        }
        guard
            context.selection.vaultID == journal.vaultID,
            context.store.storeID == journal.storeID
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        let metadata = try AtlasVaultVersionedWrappedKeyMetadata(
            localStoreMetadata: context.store.vaultMetadata
        )
        let matching = metadata.keyWraps.filter {
            $0.recoveryKeyEnvelope?.id == journal.wrapID
        }
        guard matching.count <= 1 else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        var recovered = try AtlasVaultRecoveryWrapCrypto.unwrap(
            journal.wrap,
            recoveryKey: recoveryKey,
            vaultID: metadata.vaultID
        )
        defer {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&recovered)
        }
        guard AtlasVaultRecoveryWrapCrypto.constantTimeEqual(
            recovered,
            context.vaultKey
        ) else {
            throw AtlasVaultRecoveryExportFailure.invalidConfirmation
        }
        var committedStore = context.store
        if let existing = matching.first?.recoveryKeyEnvelope {
            guard existing == journal.wrap else {
                throw AtlasVaultRecoveryExportFailure.recoveryRequired
            }
        } else {
            let updatedMetadata =
                try AtlasVaultVersionedWrappedKeyMetadata(
                    vaultID: metadata.vaultID,
                    crypto: metadata.crypto,
                    keyWraps: metadata.keyWraps
                        + [.recoveryKey(journal.wrap)]
                )
            committedStore = AtlasVaultLocalStoreEnvelope(
                format: context.store.format,
                version: context.store.version,
                storeID: context.store.storeID,
                createdAt: context.store.createdAt,
                updatedAt: journal.createdAt,
                vaultMetadata: try updatedMetadata.localStoreMetadata(),
                records: context.store.records
            )
            try Task.checkCancellation()
            try await requireAuthorization()
            let result = try await environment.saveStore(
                committedStore,
                context.selection.vaultID,
                context.vaultKey
            )
            guard result.commitState == .committed else {
                throw AtlasVaultRecoveryExportFailure
                    .durabilityVerificationRequired
            }
        }
        try await requireAuthorization()
        guard let reloaded = try await environment.loadStore(
            context.selection.vaultID,
            context.vaultKey
        ) else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        let reloadedMetadata =
            try AtlasVaultVersionedWrappedKeyMetadata(
                localStoreMetadata: reloaded.vaultMetadata
            )
        guard
            reloaded == committedStore,
            reloadedMetadata.recoveryKeyWrap == journal.wrap
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        return try await verifyExport(
            journal: journal,
            store: reloaded,
            metadata: reloadedMetadata,
            recoveryKey: recoveryKey,
            localVaultKey: context.vaultKey
        )
    }

    private func verifyExport(
        journal: AtlasVaultRecoveryExportJournal,
        store: AtlasVaultLocalStoreEnvelope,
        metadata: AtlasVaultVersionedWrappedKeyMetadata,
        recoveryKey: Data,
        localVaultKey: Data
    ) async throws -> AtlasVaultEncryptedDocument {
        guard let wrap = metadata.recoveryKeyWrap else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        var recovered = try AtlasVaultRecoveryWrapCrypto.unwrap(
            wrap,
            recoveryKey: recoveryKey,
            vaultID: metadata.vaultID
        )
        defer {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&recovered)
        }
        guard AtlasVaultRecoveryWrapCrypto.constantTimeEqual(
            recovered,
            localVaultKey
        ) else {
            throw AtlasVaultRecoveryExportFailure.invalidConfirmation
        }
        try await environment.hydrate(
            store.records,
            metadata.vaultID,
            recovered
        )
        let export = try AtlasVaultEncryptedExportEnvelope(
            exportID: journal.exportID,
            createdAt: journal.createdAt,
            vaultMetadata: metadata,
            records: store.records
        )
        let canonical = try export.canonicalData()
        let decoded =
            try AtlasVaultEncryptedExportEnvelope.decodeStrict(canonical)
        guard
            decoded.vaultMetadata.vaultID == metadata.vaultID,
            let decodedWrap = decoded.vaultMetadata.recoveryKeyWrap
        else {
            throw AtlasVaultRecoveryExportFailure.recoveryRequired
        }
        var decodedKey = try AtlasVaultRecoveryWrapCrypto.unwrap(
            decodedWrap,
            recoveryKey: recoveryKey,
            vaultID: decoded.vaultMetadata.vaultID
        )
        defer {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&decodedKey)
        }
        guard AtlasVaultRecoveryWrapCrypto.constantTimeEqual(
            decodedKey,
            localVaultKey
        ) else {
            throw AtlasVaultRecoveryExportFailure.invalidConfirmation
        }
        try await environment.hydrate(
            decoded.records,
            decoded.vaultMetadata.vaultID,
            decodedKey
        )
        return try AtlasVaultEncryptedDocument(
            verifiedEncryptedData: canonical
        )
    }

    private func requireAuthorization() async throws {
        guard
            !terminal,
            !Task.isCancelled,
            await environment.authorize()
        else {
            throw AtlasVaultRecoveryExportFailure.unauthorized
        }
    }

    private func wipePrepared() {
        guard var prepared else {
            return
        }
        AtlasVaultRecoveryKeyCodec.bestEffortWipe(
            &prepared.recoveryKey
        )
        self.prepared = nil
    }

    private func map(_ error: Error) -> AtlasVaultRecoveryExportFailure {
        if error is CancellationError || Task.isCancelled {
            return .cancelled
        }
        if let failure = error as? AtlasVaultRecoveryExportFailure {
            return failure
        }
        if let keyError = error as? AtlasVaultRecoveryKeyError {
            switch keyError {
            case .invalidRecoveryKey, .authenticationFailed:
                return .invalidConfirmation
            case .randomUnavailable:
                return .unavailable
            case .invalidVault, .invalidWrap:
                return .recoveryRequired
            }
        }
        if error is AtlasVaultVersionedWrapModelError
            || error is AtlasVaultEncryptedExportError
        {
            return .recoveryRequired
        }
        return .unavailable
    }

    private static func currentTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return formatter.string(from: Date())
    }
}
