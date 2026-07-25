import Foundation
import Security

public enum AtlasLocalVaultCreationOutcome:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case created
    case resumed
    case alreadyConfigured

    public var description: String {
        switch self {
        case .created:
            "created"
        case .resumed:
            "resumed"
        case .alreadyConfigured:
            "alreadyConfigured"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasLocalVaultCreationFailure:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case unavailable
    case durabilityVerificationRequired
    case recoveryRequired
    case cancelled
    case completionPending

    public var description: String {
        switch self {
        case .unavailable:
            "unavailable"
        case .durabilityVerificationRequired:
            "durabilityVerificationRequired"
        case .recoveryRequired:
            "recoveryRequired"
        case .cancelled:
            "cancelled"
        case .completionPending:
            "completionPending"
        }
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasLocalVaultCreating: Sendable {
    func createOrResume() async throws(AtlasLocalVaultCreationFailure)
        -> AtlasLocalVaultCreationOutcome
    func pause() async
}

struct AtlasLocalVaultCreationJournal: Equatable, Sendable {
    static let expectedFormat = "atlasvault-local-creation"
    static let currentVersion = 1

    let vaultID: String
    let storeID: String
    let createdAt: String

    init(
        vaultID: String,
        storeID: String,
        createdAt: String
    ) throws(AtlasLocalVaultCreationFailure) {
        guard
            (try? AtlasInjectedRootVaultPathLocator.validatedVaultID(
                vaultID
            )) == vaultID,
            !storeID.isEmpty,
            storeID == storeID.trimmingCharacters(
                in: .whitespacesAndNewlines
            ),
            Self.isValidTimestamp(createdAt)
        else {
            throw .recoveryRequired
        }
        self.vaultID = vaultID
        self.storeID = storeID
        self.createdAt = createdAt
    }

    static func isValidTimestamp(_ value: String) -> Bool {
        guard !value.isEmpty else {
            return false
        }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [
            .withInternetDateTime,
            .withFractionalSeconds,
        ]
        if fractional.date(from: value) != nil {
            return true
        }
        return ISO8601DateFormatter().date(from: value) != nil
    }
}

protocol AtlasLocalVaultCreationJournalStoring: Sendable {
    func loadJournal() throws(AtlasLocalVaultCreationFailure)
        -> AtlasLocalVaultCreationJournal?
    func saveJournal(
        _ journal: AtlasLocalVaultCreationJournal
    ) throws(AtlasLocalVaultCreationFailure)
    func clearJournal() throws(AtlasLocalVaultCreationFailure)
}

struct AtlasKeychainLocalVaultCreationJournalStore<
    Client: AtlasKeychainClient
>: AtlasLocalVaultCreationJournalStoring {
    static var service: String {
        "com.atlasvault.vault-creation"
    }

    static var account: String {
        "pending-v1"
    }

    private let client: Client

    init(client: Client) {
        self.client = client
    }

    func loadJournal() throws(AtlasLocalVaultCreationFailure)
        -> AtlasLocalVaultCreationJournal?
    {
        let result = client.copyMatching(Self.query)
        switch result.status {
        case errSecItemNotFound:
            return nil
        case errSecSuccess:
            guard let data = result.valueData else {
                throw .recoveryRequired
            }
            return try Self.decode(data)
        default:
            throw .unavailable
        }
    }

    func saveJournal(
        _ journal: AtlasLocalVaultCreationJournal
    ) throws(AtlasLocalVaultCreationFailure) {
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
            guard client.update(
                Self.query,
                with: AtlasKeychainUpdate(valueData: data)
            ) == errSecSuccess else {
                throw .unavailable
            }
        default:
            throw .unavailable
        }
    }

    func clearJournal() throws(AtlasLocalVaultCreationFailure) {
        switch client.delete(Self.query) {
        case errSecSuccess, errSecItemNotFound:
            return
        default:
            throw .completionPending
        }
    }

    private static var query: AtlasKeychainQuery {
        AtlasKeychainQuery(service: service, account: account)
    }

    private static func encode(
        _ journal: AtlasLocalVaultCreationJournal
    ) throws(AtlasLocalVaultCreationFailure) -> Data {
        let envelope = JournalEnvelope(journal: journal)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        do {
            return try encoder.encode(envelope)
        } catch {
            throw .unavailable
        }
    }

    private static func decode(
        _ data: Data
    ) throws(AtlasLocalVaultCreationFailure)
        -> AtlasLocalVaultCreationJournal
    {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dictionary = object as? [String: Any],
            Set(dictionary.keys) == [
                "format",
                "version",
                "vault_id",
                "store_id",
                "created_at",
            ],
            let envelope = try? JSONDecoder().decode(
                JournalEnvelope.self,
                from: data
            ),
            envelope.format
                == AtlasLocalVaultCreationJournal.expectedFormat,
            envelope.version
                == AtlasLocalVaultCreationJournal.currentVersion
        else {
            throw .recoveryRequired
        }
        return try AtlasLocalVaultCreationJournal(
            vaultID: envelope.vaultID,
            storeID: envelope.storeID,
            createdAt: envelope.createdAt
        )
    }

    private struct JournalEnvelope: Codable, Sendable {
        let format: String
        let version: Int
        let vaultID: String
        let storeID: String
        let createdAt: String

        init(journal: AtlasLocalVaultCreationJournal) {
            format = AtlasLocalVaultCreationJournal.expectedFormat
            version = AtlasLocalVaultCreationJournal.currentVersion
            vaultID = journal.vaultID
            storeID = journal.storeID
            createdAt = journal.createdAt
        }

        enum CodingKeys: String, CodingKey {
            case format
            case version
            case vaultID = "vault_id"
            case storeID = "store_id"
            case createdAt = "created_at"
        }
    }
}

struct AtlasLocalVaultCreationStoreAccess: Sendable {
    let load:
        @Sendable () throws
            -> AtlasVaultLocalStoreEnvelope?
    let save:
        @Sendable (
            AtlasVaultLocalStoreEnvelope,
            Bool
        ) throws
            -> AtlasVaultAtomicWriteResult
}

struct AtlasLocalVaultCreationEnvironment: Sendable {
    let selectVaultID:
        @Sendable () async throws
            -> AtlasVaultIDSelection
    let storeSelection:
        @Sendable (
            AtlasSelectedVaultID
        ) async throws -> Void
    let loadJournal:
        @Sendable () throws
            -> AtlasLocalVaultCreationJournal?
    let saveJournal:
        @Sendable (
            AtlasLocalVaultCreationJournal
        ) throws -> Void
    let clearJournal:
        @Sendable () throws -> Void
    let loadVaultKey:
        @Sendable (String) throws -> Data?
    let saveVaultKey:
        @Sendable (
            Data,
            String
        ) throws -> Void
    let makeStoreAccess:
        @Sendable (
            String,
            Data
        ) throws
            -> AtlasLocalVaultCreationStoreAccess
    let generateVaultID: @Sendable () -> String
    let generateStoreID: @Sendable () -> String
    let generateTimestamp: @Sendable () -> String
    let generateVaultKey:
        @Sendable () throws -> Data
}

struct AtlasLocalVaultCreationSelectionGate<
    Selector: AtlasVaultIDSelecting
>: AtlasVaultIDSelecting {
    let selector: Selector
    let loadJournal:
        @Sendable () throws
            -> AtlasLocalVaultCreationJournal?

    func selectVaultID() async throws(AtlasVaultIDSelectionError)
        -> AtlasVaultIDSelection
    {
        let selection = try await selector.selectVaultID()
        guard case .selected = selection else {
            return selection
        }
        do {
            guard try loadJournal() == nil else {
                return .none
            }
        } catch {
            return .none
        }
        return selection
    }
}

public actor AtlasLocalVaultCreationCoordinator:
    AtlasLocalVaultCreating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private struct Operation {
        let identifier: UUID
        let task: Task<
            Result<
                AtlasLocalVaultCreationOutcome,
                AtlasLocalVaultCreationFailure
            >,
            Never
        >
    }

    private let environment: AtlasLocalVaultCreationEnvironment
    private var operation: Operation?

    init(environment: AtlasLocalVaultCreationEnvironment) {
        self.environment = environment
    }

    public func createOrResume()
        async throws(AtlasLocalVaultCreationFailure)
        -> AtlasLocalVaultCreationOutcome
    {
        if let operation {
            return try await operation.task.value.get()
        }

        let identifier = UUID()
        let environment = environment
        let task = Task {
            await Self.perform(environment: environment)
        }
        operation = Operation(identifier: identifier, task: task)
        let result = await task.value
        if operation?.identifier == identifier {
            operation = nil
        }
        return try result.get()
    }

    public func pause() async {
        guard let operation else {
            return
        }
        operation.task.cancel()
        _ = await operation.task.value
        if self.operation?.identifier == operation.identifier {
            self.operation = nil
        }
    }

    public nonisolated var description: String {
        "AtlasLocalVaultCreationCoordinator(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    func hasRetainedOperationForTesting() -> Bool {
        operation != nil
    }

    static func secureRandomVaultKey()
        throws(AtlasLocalVaultCreationFailure) -> Data
    {
        var key = Data(
            count: AtlasVaultRecordCrypto.vaultKeyByteCount
        )
        let status = key.withUnsafeMutableBytes { bytes in
            SecRandomCopyBytes(
                kSecRandomDefault,
                AtlasVaultRecordCrypto.vaultKeyByteCount,
                bytes.baseAddress!
            )
        }
        guard status == errSecSuccess else {
            if !key.isEmpty {
                key.resetBytes(in: key.startIndex..<key.endIndex)
            }
            key.removeAll(keepingCapacity: false)
            throw .unavailable
        }
        return key
    }

    static func canonicalEmptyStore(
        journal: AtlasLocalVaultCreationJournal
    ) -> AtlasVaultLocalStoreEnvelope {
        AtlasVaultLocalStoreEnvelope(
            storeID: journal.storeID,
            createdAt: journal.createdAt,
            updatedAt: journal.createdAt,
            vaultMetadata: canonicalMetadata(
                vaultID: journal.vaultID
            ),
            records: []
        )
    }

    private static func perform(
        environment: AtlasLocalVaultCreationEnvironment
    ) async -> Result<
        AtlasLocalVaultCreationOutcome,
        AtlasLocalVaultCreationFailure
    > {
        do {
            return .success(try await run(environment: environment))
        } catch let failure as AtlasLocalVaultCreationFailure {
            return .failure(failure)
        } catch is CancellationError {
            return .failure(.cancelled)
        } catch {
            return .failure(.unavailable)
        }
    }

    private static func run(
        environment: AtlasLocalVaultCreationEnvironment
    ) async throws -> AtlasLocalVaultCreationOutcome {
        let selection = try await environment.selectVaultID()
        let existingJournal = try environment.loadJournal()

        switch selection {
        case let .selected(selected):
            if let existingJournal,
               existingJournal.vaultID != selected.vaultID {
                throw AtlasLocalVaultCreationFailure.recoveryRequired
            }
            try verifyConfiguredSelection(
                selected,
                journal: existingJournal,
                environment: environment
            )
            guard existingJournal != nil else {
                return .alreadyConfigured
            }
            try Task.checkCancellation()
            do {
                try environment.clearJournal()
            } catch {
                throw AtlasLocalVaultCreationFailure.completionPending
            }
            return .resumed
        case .none:
            break
        }

        let isNewJournal = existingJournal == nil
        let journal: AtlasLocalVaultCreationJournal
        if let existingJournal {
            journal = existingJournal
        } else {
            journal = try AtlasLocalVaultCreationJournal(
                vaultID: environment.generateVaultID(),
                storeID: environment.generateStoreID(),
                createdAt: environment.generateTimestamp()
            )
            guard journal.vaultID != journal.storeID else {
                throw AtlasLocalVaultCreationFailure.unavailable
            }
            try Task.checkCancellation()
            try environment.saveJournal(journal)
        }

        var vaultKey: Data
        if let existingKey = try environment.loadVaultKey(
            journal.vaultID
        ) {
            guard
                existingKey.count
                    == AtlasVaultRecordCrypto.vaultKeyByteCount
            else {
                throw AtlasLocalVaultCreationFailure.recoveryRequired
            }
            vaultKey = existingKey
        } else {
            vaultKey = try environment.generateVaultKey()
            guard
                vaultKey.count
                    == AtlasVaultRecordCrypto.vaultKeyByteCount
            else {
                throw AtlasLocalVaultCreationFailure.unavailable
            }
            try Task.checkCancellation()
            try environment.saveVaultKey(vaultKey, journal.vaultID)
        }
        defer {
            if !vaultKey.isEmpty {
                vaultKey.resetBytes(
                    in: vaultKey.startIndex..<vaultKey.endIndex
                )
            }
            vaultKey.removeAll(keepingCapacity: false)
        }

        let storeAccess = try environment.makeStoreAccess(
            journal.vaultID,
            vaultKey
        )
        if let existingStore = try storeAccess.load() {
            try validate(
                existingStore,
                journal: journal,
                requireEmptyRecords: true
            )
        } else {
            let store = canonicalEmptyStore(journal: journal)
            try Task.checkCancellation()
            let result = try storeAccess.save(store, false)
            switch result.commitState {
            case .committed:
                break
            case .committedDurabilityUnconfirmed:
                throw AtlasLocalVaultCreationFailure
                    .durabilityVerificationRequired
            }
        }

        try Task.checkCancellation()
        let selected = try AtlasSelectedVaultID(
            validating: journal.vaultID
        )
        try await environment.storeSelection(selected)

        try Task.checkCancellation()
        let verified = try await environment.selectVaultID()
        guard verified == .selected(selected) else {
            throw AtlasLocalVaultCreationFailure.recoveryRequired
        }

        try Task.checkCancellation()
        do {
            try environment.clearJournal()
        } catch {
            throw AtlasLocalVaultCreationFailure.completionPending
        }
        return isNewJournal ? .created : .resumed
    }

    private static func verifyConfiguredSelection(
        _ selected: AtlasSelectedVaultID,
        journal: AtlasLocalVaultCreationJournal?,
        environment: AtlasLocalVaultCreationEnvironment
    ) throws {
        guard
            var workingKey = try environment.loadVaultKey(
                selected.vaultID
            ),
            workingKey.count
                == AtlasVaultRecordCrypto.vaultKeyByteCount
        else {
            throw AtlasLocalVaultCreationFailure.recoveryRequired
        }
        defer {
            if !workingKey.isEmpty {
                workingKey.resetBytes(
                    in: workingKey.startIndex..<workingKey.endIndex
                )
            }
            workingKey.removeAll(keepingCapacity: false)
        }

        let access = try environment.makeStoreAccess(
            selected.vaultID,
            workingKey
        )
        guard let store = try access.load() else {
            throw AtlasLocalVaultCreationFailure.recoveryRequired
        }
        try validateConfiguredStore(
            store,
            selectedVaultID: selected.vaultID,
            journal: journal
        )
    }

    private static func validateConfiguredStore(
        _ store: AtlasVaultLocalStoreEnvelope,
        selectedVaultID: String,
        journal: AtlasLocalVaultCreationJournal?
    ) throws {
        guard
            store.format == AtlasVaultLocalStoreIO.localStoreFormat,
            store.version
                == AtlasVaultLocalStoreIO.supportedLocalStoreVersion,
            !store.storeID.isEmpty,
            AtlasLocalVaultCreationJournal.isValidTimestamp(
                store.createdAt
            ),
            AtlasLocalVaultCreationJournal.isValidTimestamp(
                store.updatedAt
            )
        else {
            throw AtlasLocalVaultCreationFailure.recoveryRequired
        }
        if let journal {
            guard
                store.storeID == journal.storeID,
                store.createdAt == journal.createdAt,
                store.vaultMetadata
                    == canonicalMetadata(vaultID: selectedVaultID)
            else {
                throw AtlasLocalVaultCreationFailure.recoveryRequired
            }
        } else {
            guard validVaultMetadata(
                store.vaultMetadata,
                vaultID: selectedVaultID
            ) else {
                throw AtlasLocalVaultCreationFailure.recoveryRequired
            }
        }
    }

    private static func validate(
        _ store: AtlasVaultLocalStoreEnvelope,
        journal: AtlasLocalVaultCreationJournal,
        requireEmptyRecords: Bool
    ) throws {
        try validateConfiguredStore(
            store,
            selectedVaultID: journal.vaultID,
            journal: journal
        )
        guard
            !requireEmptyRecords || store.records.isEmpty
        else {
            throw AtlasLocalVaultCreationFailure.recoveryRequired
        }
    }

    private static func canonicalMetadata(
        vaultID: String
    ) -> [String: AtlasJSONValue] {
        [
            "format": .string("atlas-vault"),
            "version": .number(1),
            "vault_id": .string(vaultID),
            "crypto": .object([
                "record_aead": .string("AES-256-GCM"),
                "kdf": .string("Argon2id"),
                "subkey_kdf": .string("HKDF-SHA256"),
                "key_wrap_aead": .string("AES-256-GCM"),
            ]),
            "key_wraps": .array([]),
        ]
    }

    private static func validVaultMetadata(
        _ metadata: [String: AtlasJSONValue],
        vaultID: String
    ) -> Bool {
        do {
            let data = try JSONEncoder().encode(
                AtlasJSONValue.object(metadata)
            )
            let decoded = try JSONDecoder().decode(
                AtlasVaultWrappedKeyMetadata.self,
                from: data
            )
            return decoded.vaultID == vaultID
        } catch {
            return false
        }
    }

    static func storeLoadFailure(
        for error: Error
    ) -> AtlasLocalVaultCreationFailure {
        guard let persistenceError =
            error as? AtlasVaultPersistenceError
        else {
            return .unavailable
        }
        switch persistenceError {
        case .invalidSession,
             .corruptStore,
             .unsupportedStoreVersion,
             .cryptoFailed:
            return .recoveryRequired
        case .directoryPreparationFailed,
             .readFailed,
             .writeFailed,
             .fileExists:
            return .unavailable
        }
    }
}

extension AtlasLocalVaultCreationCoordinator {
    static func production<
        DirectoryPreparer: AtlasVaultDirectoryPreparer,
        LocalStoreIO: AtlasVaultLocalStoreProviding,
        Registry: AtlasVaultIDSelecting & AtlasVaultSelectionRegistering,
        Client: AtlasKeychainClient
    >(
        runtimeServices: AtlasVaultRuntimeServices<
            DirectoryPreparer,
            LocalStoreIO
        >,
        selectionRegistry: Registry,
        journalStore:
            AtlasKeychainLocalVaultCreationJournalStore<Client>
    ) -> AtlasLocalVaultCreationCoordinator {
        let environment = AtlasLocalVaultCreationEnvironment(
            selectVaultID: {
                do {
                    return try await selectionRegistry.selectVaultID()
                } catch {
                    throw AtlasLocalVaultCreationFailure.unavailable
                }
            },
            storeSelection: { selected in
                do {
                    try await selectionRegistry.storeSelection(selected)
                } catch {
                    throw AtlasLocalVaultCreationFailure.unavailable
                }
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
            loadVaultKey: { vaultID in
                do {
                    return try runtimeServices.keyStore.loadVaultKey(
                        for: vaultID
                    )
                } catch {
                    throw AtlasLocalVaultCreationFailure.unavailable
                }
            },
            saveVaultKey: { key, vaultID in
                do {
                    try runtimeServices.keyStore.saveVaultKey(
                        key,
                        for: vaultID
                    )
                } catch {
                    throw AtlasLocalVaultCreationFailure.unavailable
                }
            },
            makeStoreAccess: { vaultID, vaultKey in
                let rootURL: URL
                do {
                    rootURL = try runtimeServices.rootDirectoryProvider
                        .rootDirectory()
                } catch {
                    throw AtlasLocalVaultCreationFailure.unavailable
                }
                let services: AtlasVaultPerVaultServices<
                    DirectoryPreparer,
                    LocalStoreIO
                >
                do {
                    services = try runtimeServices.perVaultFactory
                        .makeServices(
                            rootURL: rootURL,
                            vaultID: vaultID
                        )
                } catch {
                    throw AtlasLocalVaultCreationFailure.unavailable
                }
                let session: AtlasVaultUnlockedSession
                do {
                    session = try AtlasVaultUnlockedSession(
                        vaultID: vaultID,
                        vaultKey: vaultKey
                    )
                } catch {
                    throw AtlasLocalVaultCreationFailure.recoveryRequired
                }
                let persistence = services.persistenceCoordinator
                return AtlasLocalVaultCreationStoreAccess(
                    load: {
                        do {
                            return try persistence.loadEncryptedStore(
                                for: session
                            )
                        } catch {
                            throw storeLoadFailure(for: error)
                        }
                    },
                    save: { store, overwrite in
                        do {
                            return try persistence
                                .saveEncryptedStoreAtomically(
                                    store,
                                    for: session,
                                    overwrite: overwrite
                                )
                        } catch let error as AtlasVaultPersistenceError
                            where error == .fileExists {
                            throw AtlasLocalVaultCreationFailure
                                .recoveryRequired
                        } catch {
                            throw AtlasLocalVaultCreationFailure.unavailable
                        }
                    }
                )
            },
            generateVaultID: {
                UUID().uuidString.lowercased()
            },
            generateStoreID: {
                UUID().uuidString.lowercased()
            },
            generateTimestamp: {
                ISO8601DateFormatter().string(from: Date())
            },
            generateVaultKey: {
                try secureRandomVaultKey()
            }
        )
        return AtlasLocalVaultCreationCoordinator(
            environment: environment
        )
    }
}
