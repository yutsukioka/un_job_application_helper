import CoreFoundation
import CryptoKit
import Foundation
import Security

public enum AtlasVaultRecoveryImportOutcome:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case committed
    case resumed

    public var description: String {
        switch self {
        case .committed:
            "committed"
        case .resumed:
            "resumed"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultRecoveryImportFailure:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case unavailable
    case invalidFile
    case invalidExport
    case invalidRecoveryKey
    case existingVault
    case restoreUnavailable
    case pendingImportRequiresResume
    case durabilityVerificationRequired
    case recoveryRequired
    case completionPending
    case cancelled

    public var description: String {
        switch self {
        case .unavailable:
            "unavailable"
        case .invalidFile:
            "invalidFile"
        case .invalidExport:
            "invalidExport"
        case .invalidRecoveryKey:
            "invalidRecoveryKey"
        case .existingVault:
            "existingVault"
        case .restoreUnavailable:
            "restoreUnavailable"
        case .pendingImportRequiresResume:
            "pendingImportRequiresResume"
        case .durabilityVerificationRequired:
            "durabilityVerificationRequired"
        case .recoveryRequired:
            "recoveryRequired"
        case .completionPending:
            "completionPending"
        case .cancelled:
            "cancelled"
        }
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultRecoveryImportCoordinating: Sendable {
    func prepareImport(from url: URL) async throws
    func confirmAndImport(
        recoverySecret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome
    func resumeImport(
        from url: URL,
        recoverySecret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome
    func finishCommittedImport(
        from url: URL,
        recoverySecret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome
    func resetPendingImport() async throws
    func hasPendingImport() async throws -> Bool
    func pause() async
    func stop() async
}

struct AtlasVaultRecoveryImportJournal:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    static let expectedFormat = "atlasvault-recovery-import"
    static let currentVersion = 1

    let importID: String
    let exportID: String
    let vaultID: String
    let storeID: String
    let createdAt: String
    let exportSHA256: String
    let localStoreSHA256: String
    let vaultKeySHA256: String

    init(
        importID: String,
        exportID: String,
        vaultID: String,
        storeID: String,
        createdAt: String,
        exportSHA256: String,
        localStoreSHA256: String,
        vaultKeySHA256: String
    ) throws {
        guard
            Self.isCanonicalLowercaseUUID(importID),
            Self.isCanonicalLowercaseUUID(exportID),
            Self.isCanonicalLowercaseUUID(storeID),
            importID != exportID,
            importID != storeID,
            exportID != storeID,
            (try? AtlasInjectedRootVaultPathLocator.validatedVaultID(
                vaultID
            )) == vaultID,
            Self.isStrictUTCTimestamp(createdAt),
            Self.isLowercaseSHA256(exportSHA256),
            Self.isLowercaseSHA256(localStoreSHA256),
            Self.isLowercaseSHA256(vaultKeySHA256)
        else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        self.importID = importID
        self.exportID = exportID
        self.vaultID = vaultID
        self.storeID = storeID
        self.createdAt = createdAt
        self.exportSHA256 = exportSHA256
        self.localStoreSHA256 = localStoreSHA256
        self.vaultKeySHA256 = vaultKeySHA256
    }

    func encodedData() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        do {
            return try encoder.encode(Envelope(journal: self))
        } catch {
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
    }

    static func decode(_ data: Data) throws
        -> AtlasVaultRecoveryImportJournal
    {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dictionary = object as? [String: Any],
            Set(dictionary.keys) == Set(Envelope.CodingKeys.allCases.map(
                \.rawValue
            )),
            isStrictInteger(
                dictionary["version"],
                equalTo: currentVersion
            )
        else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        do {
            let envelope = try JSONDecoder().decode(
                Envelope.self,
                from: data
            )
            guard
                envelope.format == expectedFormat,
                envelope.version == currentVersion
            else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            return try envelope.journal()
        } catch let failure as AtlasVaultRecoveryImportFailure {
            throw failure
        } catch {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
    }

    var description: String {
        "AtlasVaultRecoveryImportJournal(<redacted>)"
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
        let bytes = Array(value.utf8)
        guard bytes.count == 20 else {
            return false
        }
        let separators: [Int: UInt8] = [
            4: 45,
            7: 45,
            10: 84,
            13: 58,
            16: 58,
            19: 90,
        ]
        for index in bytes.indices {
            if let separator = separators[index] {
                guard bytes[index] == separator else {
                    return false
                }
            } else {
                guard (48...57).contains(bytes[index]) else {
                    return false
                }
            }
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: value) else {
            return false
        }
        return formatter.string(from: date) == value
    }

    static func isLowercaseSHA256(_ value: String) -> Bool {
        value.utf8.count == 64
            && value.utf8.allSatisfy {
                (48...57).contains($0) || (97...102).contains($0)
            }
    }

    private static func isStrictInteger(
        _ value: Any?,
        equalTo expected: Int
    ) -> Bool {
        guard let number = value as? NSNumber else {
            return false
        }
        return CFGetTypeID(number) != CFBooleanGetTypeID()
            && !CFNumberIsFloatType(number)
            && number.doubleValue == Double(expected)
            && number.intValue == expected
    }

    private struct Envelope: Codable {
        let format: String
        let version: Int
        let importID: String
        let exportID: String
        let vaultID: String
        let storeID: String
        let createdAt: String
        let exportSHA256: String
        let localStoreSHA256: String
        let vaultKeySHA256: String

        enum CodingKeys: String, CodingKey, CaseIterable {
            case format
            case version
            case importID = "import_id"
            case exportID = "export_id"
            case vaultID = "vault_id"
            case storeID = "store_id"
            case createdAt = "created_at"
            case exportSHA256 = "export_sha256"
            case localStoreSHA256 = "local_store_sha256"
            case vaultKeySHA256 = "vault_key_sha256"
        }

        init(journal: AtlasVaultRecoveryImportJournal) {
            format = AtlasVaultRecoveryImportJournal.expectedFormat
            version = AtlasVaultRecoveryImportJournal.currentVersion
            importID = journal.importID
            exportID = journal.exportID
            vaultID = journal.vaultID
            storeID = journal.storeID
            createdAt = journal.createdAt
            exportSHA256 = journal.exportSHA256
            localStoreSHA256 = journal.localStoreSHA256
            vaultKeySHA256 = journal.vaultKeySHA256
        }

        func journal() throws -> AtlasVaultRecoveryImportJournal {
            try AtlasVaultRecoveryImportJournal(
                importID: importID,
                exportID: exportID,
                vaultID: vaultID,
                storeID: storeID,
                createdAt: createdAt,
                exportSHA256: exportSHA256,
                localStoreSHA256: localStoreSHA256,
                vaultKeySHA256: vaultKeySHA256
            )
        }
    }
}

protocol AtlasVaultRecoveryImportJournalStoring: Sendable {
    func loadJournal() throws -> AtlasVaultRecoveryImportJournal?
    func saveJournal(_ journal: AtlasVaultRecoveryImportJournal) throws
    func clearJournal() throws
}

struct AtlasKeychainVaultRecoveryImportJournalStore<
    Client: AtlasKeychainClient
>: AtlasVaultRecoveryImportJournalStoring {
    static var service: String {
        "com.atlasvault.recovery-import"
    }

    static var account: String {
        "pending-v1"
    }

    private let client: Client

    init(client: Client) {
        self.client = client
    }

    func loadJournal() throws -> AtlasVaultRecoveryImportJournal? {
        let result = client.copyMatching(Self.query)
        switch result.status {
        case errSecItemNotFound:
            return nil
        case errSecSuccess:
            guard let data = result.valueData else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            return try AtlasVaultRecoveryImportJournal.decode(data)
        default:
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
    }

    func saveJournal(
        _ journal: AtlasVaultRecoveryImportJournal
    ) throws {
        let data = try journal.encodedData()
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
            let current = client.copyMatching(Self.query)
            guard
                current.status == errSecSuccess,
                let currentData = current.valueData,
                try AtlasVaultRecoveryImportJournal.decode(currentData)
                    == journal
            else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            return
        default:
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
    }

    func clearJournal() throws {
        switch client.delete(Self.query) {
        case errSecSuccess, errSecItemNotFound:
            return
        default:
            throw AtlasVaultRecoveryImportFailure.completionPending
        }
    }

    private static var query: AtlasKeychainQuery {
        AtlasKeychainQuery(service: service, account: account)
    }
}

protocol AtlasVaultRecoveryImportFileReading: Sendable {
    func read(from url: URL) throws -> Data
}

struct AtlasVaultRecoveryImportFileReader:
    AtlasVaultRecoveryImportFileReading,
    Sendable
{
    static let maximumByteCount = AtlasVaultProtectedStateBounds
        .maximumImportedEncryptedStateByteCount

    func read(from url: URL) throws -> Data {
        guard
            url.isFileURL,
            AtlasVaultFileURLPolicy.isSafeAbsoluteLocalFileURL(url)
        else {
            throw AtlasVaultRecoveryImportFailure.invalidFile
        }
        let standardizedURL = url.standardizedFileURL
        let didStartSecurityScope =
            standardizedURL.startAccessingSecurityScopedResource()
        defer {
            if didStartSecurityScope {
                standardizedURL.stopAccessingSecurityScopedResource()
            }
        }
        do {
            let values = try standardizedURL.resourceValues(
                forKeys: [
                    .isRegularFileKey,
                    .isSymbolicLinkKey,
                    .fileSizeKey,
                ]
            )
            guard
                values.isRegularFile == true,
                values.isSymbolicLink != true,
                let byteCount = values.fileSize,
                byteCount > 0,
                byteCount <= Self.maximumByteCount
            else {
                throw AtlasVaultRecoveryImportFailure.invalidFile
            }
            let data = try Data(contentsOf: standardizedURL)
            guard
                !data.isEmpty,
                data.count <= Self.maximumByteCount
            else {
                throw AtlasVaultRecoveryImportFailure.invalidFile
            }
            return data
        } catch let failure as AtlasVaultRecoveryImportFailure {
            throw failure
        } catch {
            throw AtlasVaultRecoveryImportFailure.invalidFile
        }
    }
}

struct AtlasVaultRecoveryImportEnvironment: Sendable {
    let authorize: @Sendable () async -> Bool
    let selectVault:
        @Sendable () async throws -> AtlasVaultIDSelection
    let hasPendingCreation: @Sendable () async throws -> Bool
    let readFile: @Sendable (URL) async throws -> Data
    let loadJournal:
        @Sendable () async throws
            -> AtlasVaultRecoveryImportJournal?
    let saveJournal:
        @Sendable (AtlasVaultRecoveryImportJournal) async throws -> Void
    let clearJournal: @Sendable () async throws -> Void
    let loadStore:
        @Sendable (String) async throws
            -> AtlasVaultLocalStoreEnvelope?
    let saveStore:
        @Sendable (
            AtlasVaultLocalStoreEnvelope,
            String,
            Data,
            Bool
        ) async throws -> AtlasVaultAtomicWriteResult
    let confirmStoreDurability:
        @Sendable (String) async throws -> Void
    let confirmStoreDeletionDurability:
        @Sendable (String) async throws -> Void
    let deleteStore: @Sendable (String) async throws -> Void
    let loadVaultKey: @Sendable (String) async throws -> Data?
    let createVaultKey:
        @Sendable (Data, String) async throws -> Void
    let deleteVaultKey: @Sendable (String) async throws -> Void
    let createSelection:
        @Sendable (AtlasSelectedVaultID) async throws -> Void
    let hydrate:
        @Sendable (
            [AtlasVaultEncryptedRecordEnvelope],
            String,
            Data
        ) async throws -> Void
    let generateImportID: @Sendable () -> String
    let generateStoreID: @Sendable () -> String
    let timestamp: @Sendable () -> String
    let pendingImportDidChange: @Sendable (Bool) async -> Void
}

struct AtlasPendingVaultTransactionSelectionGate<
    Selector: AtlasVaultIDSelecting
>: AtlasVaultIDSelecting {
    let selector: Selector
    let hasPendingCreation: @Sendable () throws -> Bool
    let hasPendingImport: @Sendable () throws -> Bool
    let pendingImportDidChange: @Sendable (Bool) async -> Void

    init(
        selector: Selector,
        hasPendingCreation: @escaping @Sendable () throws -> Bool,
        hasPendingImport: @escaping @Sendable () throws -> Bool,
        pendingImportDidChange:
            @escaping @Sendable (Bool) async -> Void = { _ in }
    ) {
        self.selector = selector
        self.hasPendingCreation = hasPendingCreation
        self.hasPendingImport = hasPendingImport
        self.pendingImportDidChange = pendingImportDidChange
    }

    func selectVaultID() async throws(AtlasVaultIDSelectionError)
        -> AtlasVaultIDSelection
    {
        let selection = try await selector.selectVaultID()
        let pendingImport: Bool
        do {
            pendingImport = try hasPendingImport()
        } catch {
            await pendingImportDidChange(true)
            return .none
        }
        await pendingImportDidChange(pendingImport)
        guard !pendingImport else {
            return .none
        }
        do {
            guard try !hasPendingCreation() else {
                return .none
            }
        } catch {
            return .none
        }
        return selection
    }
}

actor AtlasVaultPendingTransactionAuthority {
    private struct Lease: Sendable {
        let identifier: UUID
    }

    private struct Waiter {
        let identifier: UUID
        let continuation: CheckedContinuation<Lease?, Never>
    }

    private var holderIdentifier: UUID?
    private var waiters: [Waiter] = []

    var waitingOperationCount: Int {
        waiters.count
    }

    func perform<Value: Sendable>(
        _ operation: @escaping @Sendable () async throws -> Value
    ) async throws -> Value {
        guard let lease = await acquire() else {
            throw CancellationError()
        }
        do {
            try Task.checkCancellation()
            let value = try await operation()
            release(lease)
            return value
        } catch {
            release(lease)
            throw error
        }
    }

    private func acquire() async -> Lease? {
        guard !Task.isCancelled else {
            return nil
        }
        let identifier = UUID()
        guard holderIdentifier != nil else {
            holderIdentifier = identifier
            return Lease(identifier: identifier)
        }
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                guard !Task.isCancelled else {
                    continuation.resume(returning: nil)
                    return
                }
                waiters.append(
                    Waiter(
                        identifier: identifier,
                        continuation: continuation
                    )
                )
            }
        } onCancel: {
            Task {
                await self.cancelWaiter(identifier)
            }
        }
    }

    private func cancelWaiter(_ identifier: UUID) {
        guard let index = waiters.firstIndex(where: {
            $0.identifier == identifier
        }) else {
            return
        }
        let waiter = waiters.remove(at: index)
        waiter.continuation.resume(returning: nil)
    }

    private func release(_ lease: Lease) {
        guard holderIdentifier == lease.identifier else {
            return
        }
        guard !waiters.isEmpty else {
            holderIdentifier = nil
            return
        }
        let waiter = waiters.removeFirst()
        holderIdentifier = waiter.identifier
        waiter.continuation.resume(
            returning: Lease(identifier: waiter.identifier)
        )
    }
}

struct AtlasPendingRecoveryImportCreationGate<
    Creator: AtlasLocalVaultCreating
>: AtlasLocalVaultCreating {
    let creator: Creator
    let hasPendingImport: @Sendable () throws -> Bool
    let transactionAuthority: AtlasVaultPendingTransactionAuthority

    init(
        creator: Creator,
        hasPendingImport: @escaping @Sendable () throws -> Bool,
        transactionAuthority: AtlasVaultPendingTransactionAuthority =
            AtlasVaultPendingTransactionAuthority()
    ) {
        self.creator = creator
        self.hasPendingImport = hasPendingImport
        self.transactionAuthority = transactionAuthority
    }

    func createOrResume()
        async throws(AtlasLocalVaultCreationFailure)
        -> AtlasLocalVaultCreationOutcome
    {
        do {
            return try await transactionAuthority.perform {
                guard !Task.isCancelled else {
                    throw AtlasLocalVaultCreationFailure.cancelled
                }
                guard try !hasPendingImport() else {
                    throw AtlasLocalVaultCreationFailure.recoveryRequired
                }
                guard !Task.isCancelled else {
                    throw AtlasLocalVaultCreationFailure.cancelled
                }
                return try await creator.createOrResume()
            }
        } catch let failure as AtlasLocalVaultCreationFailure {
            throw failure
        } catch is CancellationError {
            throw .cancelled
        } catch {
            throw .recoveryRequired
        }
    }

    func pause() async {
        await creator.pause()
    }
}

public actor AtlasVaultRecoveryImportCoordinator:
    AtlasVaultRecoveryImportCoordinating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum OperationKind: Equatable {
        case prepare(URL)
        case confirm
        case resume(URL)
        case finish(URL)
        case reset
    }

    private enum OperationValue: Sendable {
        case prepared
        case outcome(AtlasVaultRecoveryImportOutcome)
        case reset
    }

    private struct ActiveOperation {
        let identifier: UUID
        let kind: OperationKind
        let task: Task<
            Result<OperationValue, AtlasVaultRecoveryImportFailure>,
            Never
        >
    }

    private struct PreparedImport: Sendable {
        let envelope: AtlasVaultEncryptedExportEnvelope
        let canonicalData: Data
        let exportSHA256: String
    }

    private let environment: AtlasVaultRecoveryImportEnvironment
    private let transactionAuthority: AtlasVaultPendingTransactionAuthority
    private var preparedImport: PreparedImport?
    private var activeOperation: ActiveOperation?
    private var terminal = false

    init(
        environment: AtlasVaultRecoveryImportEnvironment,
        transactionAuthority: AtlasVaultPendingTransactionAuthority =
            AtlasVaultPendingTransactionAuthority()
    ) {
        self.environment = environment
        self.transactionAuthority = transactionAuthority
    }

    public func prepareImport(from url: URL) async throws {
        let operationURL = url.standardizedFileURL
        let value = try await runRetained(.prepare(operationURL)) {
            [self] in
            await prepareOperation(from: operationURL)
        }
        guard case .prepared = value else {
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
    }

    public func confirmAndImport(
        recoverySecret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome {
        let value = try await runRetained(
            .confirm,
            discardUnusedInput: {
                await recoverySecret.clear()
            }
        ) { [self] in
            await runPendingTransaction(
                discardUnusedInput: {
                    await recoverySecret.clear()
                }
            ) { [self] in
                await confirmOperation(recoverySecret: recoverySecret)
            }
        }
        guard case let .outcome(outcome) = value else {
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
        return outcome
    }

    public func resumeImport(
        from url: URL,
        recoverySecret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome {
        let operationURL = url.standardizedFileURL
        let value = try await runRetained(
            .resume(operationURL),
            discardUnusedInput: {
                await recoverySecret.clear()
            }
        ) { [self] in
            await runPendingTransaction(
                discardUnusedInput: {
                    await recoverySecret.clear()
                }
            ) { [self] in
                await resumeOperation(
                    from: operationURL,
                    recoverySecret: recoverySecret,
                    outcome: .resumed
                )
            }
        }
        guard case let .outcome(outcome) = value else {
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
        return outcome
    }

    public func finishCommittedImport(
        from url: URL,
        recoverySecret: any AtlasVaultSecretBuffer
    ) async throws -> AtlasVaultRecoveryImportOutcome {
        let operationURL = url.standardizedFileURL
        let value = try await runRetained(
            .finish(operationURL),
            discardUnusedInput: {
                await recoverySecret.clear()
            }
        ) { [self] in
            await runPendingTransaction(
                discardUnusedInput: {
                    await recoverySecret.clear()
                }
            ) { [self] in
                await resumeOperation(
                    from: operationURL,
                    recoverySecret: recoverySecret,
                    outcome: .resumed
                )
            }
        }
        guard case let .outcome(outcome) = value else {
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
        return outcome
    }

    public func resetPendingImport() async throws {
        await cancelRetainedOperation()
        let value = try await runRetained(.reset) { [self] in
            await runPendingTransaction { [self] in
                await resetOperation()
            }
        }
        guard case .reset = value else {
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
    }

    public func hasPendingImport() async throws -> Bool {
        guard !terminal else {
            throw AtlasVaultRecoveryImportFailure.cancelled
        }
        let pending = try await environment.loadJournal() != nil
        await environment.pendingImportDidChange(pending)
        return pending
    }

    public func pause() async {
        await cancelRetainedOperation()
        preparedImport = nil
    }

    public func stop() async {
        terminal = true
        await cancelRetainedOperation()
        preparedImport = nil
    }

    public nonisolated var description: String {
        "AtlasVaultRecoveryImportCoordinator(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    static func production<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding,
        Selector: AtlasVaultIDSelecting,
        KeyCreator: AtlasVaultKeyCreating,
        SelectionCreator: AtlasVaultSelectionCreating,
        AtomicFileSystem: AtlasVaultAtomicFileSystemClient
    >(
        runtimeServices: AtlasVaultRuntimeServices<
            DirectoryPreparer,
            LocalStoreIO
        >,
        selector: Selector,
        keyCreator: KeyCreator,
        selectionCreator: SelectionCreator,
        journalStore: any AtlasVaultRecoveryImportJournalStoring,
        hasPendingCreation: @escaping @Sendable () throws -> Bool,
        transactionAuthority: AtlasVaultPendingTransactionAuthority =
            AtlasVaultPendingTransactionAuthority(),
        fileReader: any AtlasVaultRecoveryImportFileReading,
        atomicFileSystem: AtomicFileSystem,
        authorize: @escaping @Sendable () async -> Bool,
        pendingImportDidChange:
            @escaping @Sendable (Bool) async -> Void = { _ in }
    ) -> AtlasVaultRecoveryImportCoordinator {
        let environment = AtlasVaultRecoveryImportEnvironment(
            authorize: authorize,
            selectVault: {
                try await selector.selectVaultID()
            },
            hasPendingCreation: {
                try hasPendingCreation()
            },
            readFile: { url in
                try fileReader.read(from: url)
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
            loadStore: { vaultID in
                try loadProductionStore(
                    runtimeServices: runtimeServices,
                    vaultID: vaultID
                )
            },
            saveStore: { store, vaultID, vaultKey, overwrite in
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
                        overwrite: overwrite
                    )
            },
            confirmStoreDurability: { vaultID in
                do {
                    let root = try runtimeServices
                        .rootDirectoryProvider.rootDirectory()
                    let services = try runtimeServices.perVaultFactory
                        .makeServices(
                            rootURL: root,
                            vaultID: vaultID
                        )
                    let url = try services.pathLocator.localStoreURL(
                        vaultID: vaultID
                    )
                    try atomicFileSystem.synchronizeDirectory(
                        at: url.deletingLastPathComponent()
                    )
                } catch {
                    throw AtlasVaultRecoveryImportFailure
                        .durabilityVerificationRequired
                }
            },
            confirmStoreDeletionDurability: { vaultID in
                do {
                    let root = try runtimeServices
                        .rootDirectoryProvider.rootDirectory()
                    let services = try runtimeServices.perVaultFactory
                        .makeServices(
                            rootURL: root,
                            vaultID: vaultID
                        )
                    let url = try services.pathLocator.localStoreURL(
                        vaultID: vaultID
                    )
                    let fileManager = FileManager.default
                    let rootComponents = root.standardizedFileURL
                        .pathComponents
                    var directory = url.deletingLastPathComponent()
                        .standardizedFileURL
                    while directory.pathComponents.count
                        >= rootComponents.count
                    {
                        var isDirectory = ObjCBool(false)
                        if fileManager.fileExists(
                            atPath: directory.path,
                            isDirectory: &isDirectory
                        ) {
                            guard isDirectory.boolValue else {
                                throw AtlasVaultRecoveryImportFailure
                                    .recoveryRequired
                            }
                            try atomicFileSystem.synchronizeDirectory(
                                at: directory
                            )
                            return
                        }
                        guard directory.pathComponents != rootComponents
                        else {
                            break
                        }
                        directory = directory.deletingLastPathComponent()
                    }
                    throw AtlasVaultRecoveryImportFailure
                        .durabilityVerificationRequired
                } catch {
                    throw AtlasVaultRecoveryImportFailure
                        .durabilityVerificationRequired
                }
            },
            deleteStore: { vaultID in
                let root = try runtimeServices.rootDirectoryProvider
                    .rootDirectory()
                let services = try runtimeServices.perVaultFactory
                    .makeServices(rootURL: root, vaultID: vaultID)
                let url = try services.pathLocator.localStoreURL(
                    vaultID: vaultID
                )
                try atomicFileSystem.removeItemIfExists(at: url)
                try atomicFileSystem.synchronizeDirectory(
                    at: url.deletingLastPathComponent()
                )
            },
            loadVaultKey: { vaultID in
                try runtimeServices.keyStore.loadVaultKey(for: vaultID)
            },
            createVaultKey: { key, vaultID in
                try keyCreator.createVaultKey(key, for: vaultID)
            },
            deleteVaultKey: { vaultID in
                try runtimeServices.keyStore.deleteVaultKey(for: vaultID)
            },
            createSelection: { selected in
                try await selectionCreator.createSelection(selected)
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
            generateImportID: {
                UUID().uuidString.lowercased()
            },
            generateStoreID: {
                UUID().uuidString.lowercased()
            },
            timestamp: {
                currentTimestamp()
            },
            pendingImportDidChange: pendingImportDidChange
        )
        return AtlasVaultRecoveryImportCoordinator(
            environment: environment,
            transactionAuthority: transactionAuthority
        )
    }

    private func runRetained(
        _ kind: OperationKind,
        discardUnusedInput: (@Sendable () async -> Void)? = nil,
        operation: @escaping @Sendable () async
            -> Result<OperationValue, AtlasVaultRecoveryImportFailure>
    ) async throws -> OperationValue {
        guard !terminal else {
            await discardUnusedInput?()
            throw AtlasVaultRecoveryImportFailure.cancelled
        }
        if let activeOperation {
            guard activeOperation.kind == kind else {
                await discardUnusedInput?()
                throw AtlasVaultRecoveryImportFailure.unavailable
            }
            await discardUnusedInput?()
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

    private func cancelRetainedOperation() async {
        let retained = activeOperation
        retained?.task.cancel()
        _ = await retained?.task.value
        if activeOperation?.identifier == retained?.identifier {
            activeOperation = nil
        }
    }

    private func runPendingTransaction(
        discardUnusedInput: (@Sendable () async -> Void)? = nil,
        operation: @escaping @Sendable () async
            -> Result<OperationValue, AtlasVaultRecoveryImportFailure>
    ) async -> Result<
        OperationValue,
        AtlasVaultRecoveryImportFailure
    > {
        do {
            return try await transactionAuthority.perform(operation)
        } catch {
            await discardUnusedInput?()
            return .failure(.cancelled)
        }
    }

    private func prepareOperation(
        from url: URL
    ) async -> Result<
        OperationValue,
        AtlasVaultRecoveryImportFailure
    > {
        do {
            try await requireCleanInstall()
            guard try await environment.loadJournal() == nil else {
                throw AtlasVaultRecoveryImportFailure
                    .pendingImportRequiresResume
            }
            let prepared = try await readAndValidateExport(from: url)
            try Task.checkCancellation()
            preparedImport = prepared
            return .success(.prepared)
        } catch {
            return .failure(map(error))
        }
    }

    private func confirmOperation(
        recoverySecret: any AtlasVaultSecretBuffer
    ) async -> Result<
        OperationValue,
        AtlasVaultRecoveryImportFailure
    > {
        do {
            guard let preparedImport else {
                throw AtlasVaultRecoveryImportFailure.restoreUnavailable
            }
            try await requireCleanInstall()
            guard try await environment.loadJournal() == nil else {
                throw AtlasVaultRecoveryImportFailure
                    .pendingImportRequiresResume
            }
            var vaultKey = try await verifiedVaultKey(
                prepared: preparedImport,
                recoverySecret: recoverySecret
            )
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&vaultKey)
            }
            try await requireCleanInstall()
            let storeID = environment.generateStoreID()
            let createdAt = environment.timestamp()
            let store = try importedStore(
                prepared: preparedImport,
                storeID: storeID,
                createdAt: createdAt
            )
            let journal = try AtlasVaultRecoveryImportJournal(
                importID: environment.generateImportID(),
                exportID: preparedImport.envelope.exportID,
                vaultID: preparedImport.envelope.vaultMetadata.vaultID,
                storeID: storeID,
                createdAt: createdAt,
                exportSHA256: preparedImport.exportSHA256,
                localStoreSHA256: try Self.sha256Hex(
                    AtlasVaultLocalStoreIO.encode(store)
                ),
                vaultKeySHA256: Self.sha256Hex(vaultKey)
            )
            try await requireCleanInstall()
            try await environment.saveJournal(journal)
            await environment.pendingImportDidChange(true)
            let outcome = try await install(
                journal: journal,
                store: store,
                vaultKey: vaultKey,
                outcome: .committed
            )
            self.preparedImport = nil
            return .success(.outcome(outcome))
        } catch {
            await recoverySecret.clear()
            return .failure(map(error))
        }
    }

    private func resumeOperation(
        from url: URL,
        recoverySecret: any AtlasVaultSecretBuffer,
        outcome: AtlasVaultRecoveryImportOutcome
    ) async -> Result<
        OperationValue,
        AtlasVaultRecoveryImportFailure
    > {
        do {
            try await requireBaseAuthorization()
            let journal = try await environment.loadJournal()
            guard let journal else {
                throw AtlasVaultRecoveryImportFailure.restoreUnavailable
            }
            await environment.pendingImportDidChange(true)
            let prepared = try await readAndValidateExport(from: url)
            guard
                prepared.exportSHA256 == journal.exportSHA256,
                prepared.envelope.exportID == journal.exportID,
                prepared.envelope.vaultMetadata.vaultID
                    == journal.vaultID
            else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            var vaultKey = try await verifiedVaultKey(
                prepared: prepared,
                recoverySecret: recoverySecret
            )
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&vaultKey)
            }
            guard Self.sha256Hex(vaultKey) == journal.vaultKeySHA256 else {
                throw AtlasVaultRecoveryImportFailure.invalidRecoveryKey
            }
            let store = try importedStore(
                prepared: prepared,
                storeID: journal.storeID,
                createdAt: journal.createdAt
            )
            guard
                try Self.sha256Hex(
                    AtlasVaultLocalStoreIO.encode(store)
                ) == journal.localStoreSHA256
            else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            let result = try await install(
                journal: journal,
                store: store,
                vaultKey: vaultKey,
                outcome: outcome
            )
            preparedImport = nil
            return .success(.outcome(result))
        } catch {
            await recoverySecret.clear()
            return .failure(map(error))
        }
    }

    private func install(
        journal: AtlasVaultRecoveryImportJournal,
        store: AtlasVaultLocalStoreEnvelope,
        vaultKey: Data,
        outcome: AtlasVaultRecoveryImportOutcome
    ) async throws -> AtlasVaultRecoveryImportOutcome {
        try await requireTransactionAuthorization(
            journal: journal,
            allowMatchingSelection: true
        )
        if let existingStore = try await environment.loadStore(
            journal.vaultID
        ) {
            guard
                try Self.sha256Hex(
                    AtlasVaultLocalStoreIO.encode(existingStore)
                ) == journal.localStoreSHA256,
                existingStore == store
            else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            try await requireTransactionAuthorization(
                journal: journal,
                allowMatchingSelection: true
            )
            do {
                try await environment.confirmStoreDurability(
                    journal.vaultID
                )
            } catch {
                throw AtlasVaultRecoveryImportFailure
                    .durabilityVerificationRequired
            }
        } else {
            try await requireTransactionAuthorization(
                journal: journal,
                allowMatchingSelection: false
            )
            let result = try await environment.saveStore(
                store,
                journal.vaultID,
                vaultKey,
                // Restore creation is always overwrite: false.
                false
            )
            switch result.commitState {
            case .committed:
                break
            case .committedDurabilityUnconfirmed:
                throw AtlasVaultRecoveryImportFailure
                    .durabilityVerificationRequired
            }
        }
        try Task.checkCancellation()
        guard let readBackStore = try await environment.loadStore(
            journal.vaultID
        ), readBackStore == store,
        try Self.sha256Hex(
            AtlasVaultLocalStoreIO.encode(readBackStore)
        ) == journal.localStoreSHA256
        else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }

        let selected = try AtlasSelectedVaultID(
            validating: journal.vaultID
        )
        let selectionAlreadyCommitted: Bool
        switch try await environment.selectVault() {
        case .none:
            selectionAlreadyCommitted = false
        case let .selected(existing):
            guard existing == selected else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            selectionAlreadyCommitted = true
        }

        var readBackKey = try await environment.loadVaultKey(
            journal.vaultID
        )
        if readBackKey == nil {
            guard !selectionAlreadyCommitted else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            try await requireTransactionAuthorization(
                journal: journal,
                allowMatchingSelection: false
            )
            do {
                try await environment.createVaultKey(
                    vaultKey,
                    journal.vaultID
                )
            } catch {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            readBackKey = try await environment.loadVaultKey(
                journal.vaultID
            )
        }
        guard var verifiedReadBackKey = readBackKey else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        readBackKey = nil
        defer {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(
                &verifiedReadBackKey
            )
        }
        guard
            Self.sha256Hex(verifiedReadBackKey)
                == journal.vaultKeySHA256,
            AtlasVaultRecoveryWrapCrypto.constantTimeEqual(
                verifiedReadBackKey,
                vaultKey
            )
        else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }

        if !selectionAlreadyCommitted {
            try await requireTransactionAuthorization(
                journal: journal,
                allowMatchingSelection: false
            )
            do {
                try await environment.createSelection(selected)
            } catch {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
        }
        guard
            try await environment.selectVault() == .selected(selected)
        else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        try await requireTransactionAuthorization(
            journal: journal,
            allowMatchingSelection: true
        )
        do {
            try await environment.clearJournal()
        } catch {
            throw AtlasVaultRecoveryImportFailure.completionPending
        }
        await environment.pendingImportDidChange(false)
        return outcome
    }

    private func resetOperation() async -> Result<
        OperationValue,
        AtlasVaultRecoveryImportFailure
    > {
        do {
            try await requireBaseAuthorization()
            let journal = try await environment.loadJournal()
            guard let journal else {
                throw AtlasVaultRecoveryImportFailure.restoreUnavailable
            }
            await environment.pendingImportDidChange(true)
            guard try await environment.selectVault() == .none else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
            let store = try await environment.loadStore(
                journal.vaultID
            )
            if let store {
                guard
                    try Self.sha256Hex(
                        AtlasVaultLocalStoreIO.encode(store)
                    ) == journal.localStoreSHA256
                else {
                    throw AtlasVaultRecoveryImportFailure.recoveryRequired
                }
            }
            let hasKey: Bool
            if var loadedKey = try await environment.loadVaultKey(
                journal.vaultID
            ) {
                defer {
                    AtlasVaultRecoveryKeyCodec.bestEffortWipe(&loadedKey)
                }
                guard
                    Self.sha256Hex(loadedKey)
                        == journal.vaultKeySHA256
                else {
                    throw AtlasVaultRecoveryImportFailure.recoveryRequired
                }
                hasKey = true
            } else {
                hasKey = false
            }
            if store != nil {
                try await requireTransactionAuthorization(
                    journal: journal,
                    allowMatchingSelection: false
                )
                guard
                    let currentStore = try await environment.loadStore(
                        journal.vaultID
                    ),
                    try Self.sha256Hex(
                        AtlasVaultLocalStoreIO.encode(currentStore)
                    ) == journal.localStoreSHA256
                else {
                    throw AtlasVaultRecoveryImportFailure.recoveryRequired
                }
                try await environment.deleteStore(journal.vaultID)
                guard try await environment.loadStore(
                    journal.vaultID
                ) == nil else {
                    throw AtlasVaultRecoveryImportFailure.recoveryRequired
                }
            } else {
                try await requireTransactionAuthorization(
                    journal: journal,
                    allowMatchingSelection: false
                )
                guard try await environment.loadStore(
                    journal.vaultID
                ) == nil else {
                    throw AtlasVaultRecoveryImportFailure.recoveryRequired
                }
                do {
                    try await environment
                        .confirmStoreDeletionDurability(
                            journal.vaultID
                        )
                } catch {
                    throw AtlasVaultRecoveryImportFailure
                        .durabilityVerificationRequired
                }
            }
            if hasKey {
                try await requireTransactionAuthorization(
                    journal: journal,
                    allowMatchingSelection: false
                )
                guard var currentKey =
                    try await environment.loadVaultKey(journal.vaultID)
                else {
                    throw AtlasVaultRecoveryImportFailure.recoveryRequired
                }
                guard
                    Self.sha256Hex(currentKey)
                        == journal.vaultKeySHA256
                else {
                    AtlasVaultRecoveryKeyCodec.bestEffortWipe(&currentKey)
                    throw AtlasVaultRecoveryImportFailure.recoveryRequired
                }
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&currentKey)
                try await environment.deleteVaultKey(journal.vaultID)
                if var remainingKey = try await environment.loadVaultKey(
                    journal.vaultID
                ) {
                    defer {
                        AtlasVaultRecoveryKeyCodec.bestEffortWipe(
                            &remainingKey
                        )
                    }
                    throw AtlasVaultRecoveryImportFailure.recoveryRequired
                }
            }
            try await requireTransactionAuthorization(
                journal: journal,
                allowMatchingSelection: false
            )
            try await environment.clearJournal()
            await environment.pendingImportDidChange(false)
            preparedImport = nil
            return .success(.reset)
        } catch {
            return .failure(map(error))
        }
    }

    private func readAndValidateExport(
        from url: URL
    ) async throws -> PreparedImport {
        try Task.checkCancellation()
        let data = try await environment.readFile(url)
        try Task.checkCancellation()
        let envelope: AtlasVaultEncryptedExportEnvelope
        let canonicalData: Data
        do {
            envelope = try AtlasVaultEncryptedExportEnvelope.decodeStrict(
                data
            )
            canonicalData = try envelope.canonicalData()
        } catch {
            throw AtlasVaultRecoveryImportFailure.invalidExport
        }
        guard AtlasVaultRecoveryUnlockProvider.onlyRecoveryWrap(
            in: envelope.vaultMetadata
        ) != nil else {
            throw AtlasVaultRecoveryImportFailure.invalidExport
        }
        var recordIDs = Set<String>()
        for record in envelope.records {
            guard recordIDs.insert(record.id).inserted else {
                throw AtlasVaultRecoveryImportFailure.invalidExport
            }
        }
        return PreparedImport(
            envelope: envelope,
            canonicalData: canonicalData,
            exportSHA256: Self.sha256Hex(canonicalData)
        )
    }

    private func verifiedVaultKey(
        prepared: PreparedImport,
        recoverySecret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        var secret: Data
        do {
            secret = try await recoverySecret.takeSecretBytes()
        } catch {
            await recoverySecret.clear()
            throw AtlasVaultRecoveryImportFailure.invalidRecoveryKey
        }
        do {
            guard
                let text = String(data: secret, encoding: .utf8),
                Data(text.utf8) == secret,
                let wrap = AtlasVaultRecoveryUnlockProvider.onlyRecoveryWrap(
                    in: prepared.envelope.vaultMetadata
                )
            else {
                throw AtlasVaultRecoveryImportFailure.invalidRecoveryKey
            }
            var recoveryKey: Data
            do {
                recoveryKey = try AtlasVaultRecoveryKeyCodec.parse(text)
            } catch {
                throw AtlasVaultRecoveryImportFailure.invalidRecoveryKey
            }
            defer {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&recoveryKey)
            }
            var vaultKey: Data
            do {
                vaultKey = try AtlasVaultRecoveryWrapCrypto.unwrap(
                    wrap,
                    recoveryKey: recoveryKey,
                    vaultID: prepared.envelope.vaultMetadata.vaultID
                )
            } catch {
                throw AtlasVaultRecoveryImportFailure.invalidRecoveryKey
            }
            guard
                vaultKey.count == AtlasVaultRecordCrypto.vaultKeyByteCount
            else {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&vaultKey)
                throw AtlasVaultRecoveryImportFailure.invalidRecoveryKey
            }
            do {
                try await environment.hydrate(
                    prepared.envelope.records,
                    prepared.envelope.vaultMetadata.vaultID,
                    vaultKey
                )
            } catch {
                AtlasVaultRecoveryKeyCodec.bestEffortWipe(&vaultKey)
                throw AtlasVaultRecoveryImportFailure.invalidExport
            }
            try Task.checkCancellation()
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&secret)
            await recoverySecret.clear()
            return vaultKey
        } catch {
            AtlasVaultRecoveryKeyCodec.bestEffortWipe(&secret)
            await recoverySecret.clear()
            throw error
        }
    }

    private func importedStore(
        prepared: PreparedImport,
        storeID: String,
        createdAt: String
    ) throws -> AtlasVaultLocalStoreEnvelope {
        guard
            AtlasVaultRecoveryImportJournal
                .isCanonicalLowercaseUUID(storeID),
            AtlasVaultRecoveryImportJournal
                .isStrictUTCTimestamp(createdAt),
            storeID != prepared.envelope.exportID
        else {
            throw AtlasVaultRecoveryImportFailure.unavailable
        }
        return AtlasVaultLocalStoreEnvelope(
            storeID: storeID,
            createdAt: createdAt,
            updatedAt: createdAt,
            vaultMetadata: try prepared.envelope.vaultMetadata
                .localStoreMetadata(),
            records: prepared.envelope.records
        )
    }

    private func requireBaseAuthorization() async throws {
        try Task.checkCancellation()
        guard !terminal, await environment.authorize() else {
            throw AtlasVaultRecoveryImportFailure.restoreUnavailable
        }
        guard try await environment.hasPendingCreation() == false else {
            throw AtlasVaultRecoveryImportFailure.restoreUnavailable
        }
    }

    private func requireCleanInstall() async throws {
        try await requireBaseAuthorization()
        guard try await environment.selectVault() == .none else {
            throw AtlasVaultRecoveryImportFailure.existingVault
        }
    }

    private func requireTransactionAuthorization(
        journal: AtlasVaultRecoveryImportJournal,
        allowMatchingSelection: Bool
    ) async throws {
        try await requireBaseAuthorization()
        guard try await environment.loadJournal() == journal else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        switch try await environment.selectVault() {
        case .none:
            return
        case let .selected(selected):
            guard
                allowMatchingSelection,
                selected.vaultID == journal.vaultID
            else {
                throw AtlasVaultRecoveryImportFailure.recoveryRequired
            }
        }
    }

    private static func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map {
            String(format: "%02x", $0)
        }.joined()
    }

    private static func currentTimestamp() -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: Date())
    }

    private static func loadProductionStore<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding
    >(
        runtimeServices: AtlasVaultRuntimeServices<
            DirectoryPreparer,
            LocalStoreIO
        >,
        vaultID: String
    ) throws -> AtlasVaultLocalStoreEnvelope? {
        let root = try runtimeServices.rootDirectoryProvider
            .rootDirectory()
        let services = try runtimeServices.perVaultFactory.makeServices(
            rootURL: root,
            vaultID: vaultID
        )
        let url = try services.pathLocator.localStoreURL(
            vaultID: vaultID
        )
        guard FileManager.default.fileExists(atPath: url.path) else {
            return nil
        }
        let values = try url.resourceValues(
            forKeys: [.isRegularFileKey, .isSymbolicLinkKey]
        )
        guard
            values.isRegularFile == true,
            values.isSymbolicLink != true
        else {
            throw AtlasVaultRecoveryImportFailure.recoveryRequired
        }
        return try services.localStoreIO.read(from: url)
    }

    private func map(_ error: Error) -> AtlasVaultRecoveryImportFailure {
        if let failure = error as? AtlasVaultRecoveryImportFailure {
            return failure
        }
        if error is CancellationError {
            return .cancelled
        }
        return .unavailable
    }
}
