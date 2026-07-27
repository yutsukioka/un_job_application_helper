import Foundation

public struct AtlasVaultSavedSearchDraft:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let name: String
    public let searchText: String

    public init(name: String, searchText: String) {
        self.name = name
        self.searchText = searchText
    }

    public var description: String {
        "AtlasVaultSavedSearchDraft(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public struct AtlasVaultSavedSearchSnapshot:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let searches: [AtlasVaultSavedSearchPresentation]

    public init(searches: [AtlasVaultSavedSearchPresentation]) {
        self.searches = searches
    }

    public var description: String {
        "AtlasVaultSavedSearchSnapshot(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultSavedSearchMutationResult:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case committed(AtlasVaultSavedSearchSnapshot)
    case committedDurabilityUnconfirmed(
        AtlasVaultSavedSearchSnapshot
    )
    case failed(AtlasVaultSavedSearchSnapshot)
    case cancelled(AtlasVaultSavedSearchSnapshot)
    case staleItem(AtlasVaultSavedSearchSnapshot)
    case locked

    public var description: String {
        switch self {
        case .committed:
            "committed"
        case .committedDurabilityUnconfirmed:
            "committedDurabilityUnconfirmed"
        case .failed:
            "failed"
        case .cancelled:
            "cancelled"
        case .staleItem:
            "staleItem"
        case .locked:
            "locked"
        }
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasVaultSavedSearchFailure:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case invalidDraft
    case unavailable
    case locked

    public var description: String {
        switch self {
        case .invalidDraft:
            "invalidDraft"
        case .unavailable:
            "unavailable"
        case .locked:
            "locked"
        }
    }

    public var debugDescription: String {
        description
    }
}

public protocol AtlasVaultSavedSearchCoordinating: Sendable {
    func activate(
        selectedVault: String
    ) async throws -> AtlasVaultSavedSearchSnapshot

    func create(
        _ draft: AtlasVaultSavedSearchDraft
    ) async throws -> AtlasVaultSavedSearchMutationResult

    func update(
        presentationID: AtlasVaultPresentationID,
        draft: AtlasVaultSavedSearchDraft
    ) async throws -> AtlasVaultSavedSearchMutationResult

    func delete(
        presentationID: AtlasVaultPresentationID
    ) async throws -> AtlasVaultSavedSearchMutationResult

    func stop() async
}

public struct AtlasVaultSavedSearchEnvironment:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let readPrivateState:
        @Sendable () async throws -> AtlasVaultHydratedState
    public let applyPrivateMutation:
        @Sendable (
            AtlasVaultRuntimeMutationRequest
        ) async -> AtlasVaultPrivateMutationResult
    public let containCommittedPrivateMutationFailure:
        @Sendable () async -> Void
    public let timestamp: @Sendable () -> String

    public init(
        readPrivateState:
            @escaping @Sendable () async throws
                -> AtlasVaultHydratedState,
        applyPrivateMutation:
            @escaping @Sendable (
                AtlasVaultRuntimeMutationRequest
            ) async -> AtlasVaultPrivateMutationResult,
        containCommittedPrivateMutationFailure:
            @escaping @Sendable () async -> Void,
        timestamp: @escaping @Sendable () -> String
    ) {
        self.readPrivateState = readPrivateState
        self.applyPrivateMutation = applyPrivateMutation
        self.containCommittedPrivateMutationFailure =
            containCommittedPrivateMutationFailure
        self.timestamp = timestamp
    }

    public var description: String {
        "AtlasVaultSavedSearchEnvironment(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public actor AtlasVaultSavedSearchCoordinator:
    AtlasVaultSavedSearchCoordinating,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private struct RecordMetadata: Sendable {
        let recordIdentifier: String
        let currentRevision: String
        let encryptionKeyIdentifier: String
        let payload: AtlasSavedSearchVaultPayload
        let clientCreatedAt: String
    }

    private struct Projection {
        let snapshot: AtlasVaultSavedSearchSnapshot
        let mapping: [AtlasVaultPresentationID: RecordMetadata]
        let recordIdentifiers: Set<String>
    }

    private struct MutationOperation {
        let identifier: UUID
        let sessionRevision: UInt64
        let task: Task<AtlasVaultPrivateMutationResult, Never>
    }

    private static let primaryKeyIdentifier = "primary-local-key-v1"

    private let environment: AtlasVaultSavedSearchEnvironment
    private var activeVaultIdentifier: String?
    private var presentationGeneration: AtlasVaultPresentationGeneration?
    private var metadataByPresentationID:
        [AtlasVaultPresentationID: RecordMetadata] = [:]
    private var activeRecordIdentifiers: Set<String> = []
    private var committedSnapshot = AtlasVaultSavedSearchSnapshot(searches: [])
    private var mutationOperation: MutationOperation?
    private var sessionRevision: UInt64 = 0
    private var mutationsDisabled = false

    public init(environment: AtlasVaultSavedSearchEnvironment) {
        self.environment = environment
    }

    public func activate(
        selectedVault: String
    ) async throws -> AtlasVaultSavedSearchSnapshot {
        let validatedVaultIdentifier: String
        do {
            validatedVaultIdentifier =
                try AtlasInjectedRootVaultPathLocator.validatedVaultID(
                    selectedVault
                )
        } catch {
            invalidateSession()
            throw AtlasVaultSavedSearchFailure.unavailable
        }

        await stopMutation()
        invalidateSession()
        let activationRevision = sessionRevision
        let generation = AtlasVaultPresentationGeneration()

        let state: AtlasVaultHydratedState
        do {
            state = try await environment.readPrivateState()
        } catch {
            invalidateSession()
            throw AtlasVaultSavedSearchFailure.locked
        }
        guard
            activationRevision == sessionRevision,
            !Task.isCancelled
        else {
            invalidateSession()
            throw AtlasVaultSavedSearchFailure.locked
        }

        let projection: Projection
        do {
            projection = try Self.project(
                state,
                generation: generation
            )
        } catch {
            invalidateSession()
            throw AtlasVaultSavedSearchFailure.unavailable
        }

        activeVaultIdentifier = validatedVaultIdentifier
        presentationGeneration = generation
        metadataByPresentationID = projection.mapping
        activeRecordIdentifiers = projection.recordIdentifiers
        committedSnapshot = projection.snapshot
        mutationsDisabled = false
        return projection.snapshot
    }

    public func create(
        _ draft: AtlasVaultSavedSearchDraft
    ) async throws -> AtlasVaultSavedSearchMutationResult {
        guard let vaultIdentifier = activeVaultIdentifier,
              presentationGeneration != nil else {
            throw AtlasVaultSavedSearchFailure.locked
        }
        guard !mutationsDisabled, mutationOperation == nil else {
            return .failed(committedSnapshot)
        }

        let normalized = try Self.normalizedDraft(draft)
        let timestamp = environment.timestamp()
        guard Self.isStrictUTCTimestamp(timestamp) else {
            throw AtlasVaultSavedSearchFailure.unavailable
        }
        let request = AtlasSearchRequest(text: normalized.searchText)
        let payload = AtlasSavedSearchVaultPayload(
            name: normalized.name,
            summary: normalized.searchText ?? "All open jobs",
            description: nil,
            request: request,
            createdAt: timestamp,
            updatedAt: timestamp
        )
        let envelope = AtlasSavedSearchVaultRecordPayload(
            type: .savedSearch,
            payload: payload,
            clientCreatedAt: timestamp,
            clientUpdatedAt: timestamp
        )
        let mutation = AtlasVaultCreateMutation(
            payload: .savedSearch(envelope),
            keyID: Self.primaryKeyIdentifier
        )
        let runtimeRequest = AtlasVaultRuntimeMutationRequest(
            expectedVaultID: vaultIdentifier,
            mutations: AtlasVaultMutationSet(creates: [mutation])
        )
        let previousIdentifiers = activeRecordIdentifiers

        return await performMutation(runtimeRequest) { state, projection in
            let created = projection.recordIdentifiers
                .subtracting(previousIdentifiers)
            guard created.count == 1 else {
                return false
            }
            return state.savedSearches.contains {
                created.contains($0.metadata.id)
            }
        }
    }

    public func delete(
        presentationID: AtlasVaultPresentationID
    ) async throws -> AtlasVaultSavedSearchMutationResult {
        guard let vaultIdentifier = activeVaultIdentifier,
              presentationGeneration != nil else {
            throw AtlasVaultSavedSearchFailure.locked
        }
        guard !mutationsDisabled, mutationOperation == nil else {
            return .failed(committedSnapshot)
        }
        guard let metadata = metadataByPresentationID[presentationID] else {
            return .staleItem(committedSnapshot)
        }

        let mutation = AtlasVaultDeleteMutation(
            recordID: metadata.recordIdentifier,
            currentRevision: metadata.currentRevision,
            keyID: metadata.encryptionKeyIdentifier
        )
        let runtimeRequest = AtlasVaultRuntimeMutationRequest(
            expectedVaultID: vaultIdentifier,
            mutations: AtlasVaultMutationSet(deletes: [mutation])
        )

        return await performMutation(runtimeRequest) { state, projection in
            let activeItemIsAbsent = !projection.recordIdentifiers.contains(
                metadata.recordIdentifier
            )
            let matchingTombstone = state.tombstones.contains {
                $0.metadata.id == metadata.recordIdentifier
                    && $0.metadata.deleted
            }
            return activeItemIsAbsent && matchingTombstone
        }
    }

    public func update(
        presentationID: AtlasVaultPresentationID,
        draft: AtlasVaultSavedSearchDraft
    ) async throws -> AtlasVaultSavedSearchMutationResult {
        guard let vaultIdentifier = activeVaultIdentifier,
              presentationGeneration != nil else {
            throw AtlasVaultSavedSearchFailure.locked
        }
        guard !mutationsDisabled, mutationOperation == nil else {
            return .failed(committedSnapshot)
        }
        guard let metadata = metadataByPresentationID[presentationID] else {
            return .staleItem(committedSnapshot)
        }

        let normalized = try Self.normalizedDraft(draft)
        guard metadata.payload.name != normalized.name
                || metadata.payload.request.text != normalized.searchText
        else {
            return .committed(committedSnapshot)
        }

        let timestamp = environment.timestamp()
        guard Self.isStrictUTCTimestamp(timestamp) else {
            throw AtlasVaultSavedSearchFailure.unavailable
        }
        var updatedRequest = metadata.payload.request
        updatedRequest.text = normalized.searchText
        updatedRequest.offset = 0
        let updatedPayload = AtlasSavedSearchVaultPayload(
            name: normalized.name,
            summary: normalized.searchText ?? "All open jobs",
            description: metadata.payload.description,
            request: updatedRequest,
            createdAt: metadata.payload.createdAt,
            updatedAt: timestamp
        )
        let envelope = AtlasSavedSearchVaultRecordPayload(
            type: .savedSearch,
            payload: updatedPayload,
            clientCreatedAt: metadata.clientCreatedAt,
            clientUpdatedAt: timestamp
        )
        let mutation = AtlasVaultUpdateMutation(
            recordID: metadata.recordIdentifier,
            currentRevision: metadata.currentRevision,
            payload: .savedSearch(envelope),
            keyID: metadata.encryptionKeyIdentifier
        )
        let runtimeRequest = AtlasVaultRuntimeMutationRequest(
            expectedVaultID: vaultIdentifier,
            mutations: AtlasVaultMutationSet(updates: [mutation])
        )

        return await performMutation(runtimeRequest) { state, projection in
            guard
                projection.mapping[presentationID]?.recordIdentifier
                    == metadata.recordIdentifier,
                let updatedRecord = state.savedSearches.first(where: {
                    $0.metadata.id == metadata.recordIdentifier
                })
            else {
                return false
            }
            return updatedRecord.metadata.revision
                    != metadata.currentRevision
                && updatedRecord.metadata.parentRevision
                    == metadata.currentRevision
                && updatedRecord.metadata.keyID
                    == metadata.encryptionKeyIdentifier
                && updatedRecord.payload == updatedPayload
                && updatedRecord.clientCreatedAt
                    == metadata.clientCreatedAt
                && updatedRecord.clientUpdatedAt == timestamp
        }
    }

    public func stop() async {
        await stopMutation()
        invalidateSession()
    }

    public nonisolated var description: String {
        "AtlasVaultSavedSearchCoordinator(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private func performMutation(
        _ request: AtlasVaultRuntimeMutationRequest,
        verify: @escaping @Sendable (
            AtlasVaultHydratedState,
            Projection
        ) -> Bool
    ) async -> AtlasVaultSavedSearchMutationResult {
        let operationIdentifier = UUID()
        let operationRevision = sessionRevision
        let apply = environment.applyPrivateMutation
        let task = Task {
            await apply(request)
        }
        mutationOperation = MutationOperation(
            identifier: operationIdentifier,
            sessionRevision: operationRevision,
            task: task
        )
        let hostResult = await task.value
        guard mutationOperation?.identifier == operationIdentifier else {
            return .cancelled(committedSnapshot)
        }
        mutationOperation = nil
        guard
            sessionRevision == operationRevision,
            activeVaultIdentifier != nil,
            presentationGeneration != nil
        else {
            return .locked
        }

        switch hostResult {
        case .failed:
            return .failed(committedSnapshot)
        case .cancelled:
            return .cancelled(committedSnapshot)
        case .locked:
            invalidateSession()
            return .locked
        case .committed, .committedDurabilityUnconfirmed:
            break
        }

        guard let generation = presentationGeneration else {
            invalidateSession()
            return .locked
        }
        let state: AtlasVaultHydratedState
        let projection: Projection
        do {
            state = try await environment.readPrivateState()
            projection = try Self.project(
                state,
                generation: generation
            )
        } catch {
            await containCommittedStateFailure()
            return .locked
        }
        guard
            sessionRevision == operationRevision,
            activeVaultIdentifier != nil,
            presentationGeneration == generation,
            verify(state, projection)
        else {
            await containCommittedStateFailure()
            return .locked
        }

        metadataByPresentationID = projection.mapping
        activeRecordIdentifiers = projection.recordIdentifiers
        committedSnapshot = projection.snapshot

        switch hostResult {
        case .committed:
            return .committed(projection.snapshot)
        case .committedDurabilityUnconfirmed:
            mutationsDisabled = true
            return .committedDurabilityUnconfirmed(projection.snapshot)
        case .failed, .cancelled, .locked:
            return .locked
        }
    }

    private func containCommittedStateFailure() async {
        invalidateSession()
        await environment.containCommittedPrivateMutationFailure()
    }

    private func stopMutation() async {
        guard let operation = mutationOperation else {
            return
        }
        operation.task.cancel()
        _ = await operation.task.value
        if mutationOperation?.identifier == operation.identifier {
            mutationOperation = nil
        }
    }

    private func invalidateSession() {
        sessionRevision &+= 1
        activeVaultIdentifier = nil
        presentationGeneration = nil
        metadataByPresentationID.removeAll(keepingCapacity: false)
        activeRecordIdentifiers.removeAll(keepingCapacity: false)
        committedSnapshot = AtlasVaultSavedSearchSnapshot(searches: [])
        mutationsDisabled = false
    }

    private static func normalizedDraft(
        _ draft: AtlasVaultSavedSearchDraft
    ) throws -> (name: String, searchText: String?) {
        guard
            !containsForbiddenScalar(draft.name),
            !containsForbiddenScalar(draft.searchText)
        else {
            throw AtlasVaultSavedSearchFailure.invalidDraft
        }
        let name = draft.name.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        let searchText = draft.searchText.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        guard (1...120).contains(name.unicodeScalars.count),
              searchText.unicodeScalars.count <= 512 else {
            throw AtlasVaultSavedSearchFailure.invalidDraft
        }
        return (
            name: name,
            searchText: searchText.isEmpty ? nil : searchText
        )
    }

    private static func containsForbiddenScalar(_ value: String) -> Bool {
        value.unicodeScalars.contains {
            CharacterSet.newlines.contains($0)
                || CharacterSet.controlCharacters.contains($0)
        }
    }

    private static func project(
        _ state: AtlasVaultHydratedState,
        generation: AtlasVaultPresentationGeneration
    ) throws -> Projection {
        var recordIdentifiers = Set<String>()
        var presentationIdentifiers = Set<AtlasVaultPresentationID>()
        var mapping: [AtlasVaultPresentationID: RecordMetadata] = [:]
        var searches: [AtlasVaultSavedSearchPresentation] = []
        searches.reserveCapacity(state.savedSearches.count)

        for record in state.savedSearches {
            guard !record.metadata.deleted,
                  recordIdentifiers.insert(record.metadata.id).inserted else {
                throw AtlasVaultSavedSearchFailure.unavailable
            }
            let presentationIdentifier = AtlasVaultPresentationID(
                recordID: record.metadata.id,
                generation: generation
            )
            guard presentationIdentifiers.insert(
                presentationIdentifier
            ).inserted else {
                throw AtlasVaultSavedSearchFailure.unavailable
            }
            mapping[presentationIdentifier] = RecordMetadata(
                recordIdentifier: record.metadata.id,
                currentRevision: record.metadata.revision,
                encryptionKeyIdentifier: record.metadata.keyID,
                payload: record.payload,
                clientCreatedAt: record.clientCreatedAt
            )
            searches.append(
                AtlasVaultSavedSearchPresentation(
                    id: presentationIdentifier,
                    name: record.payload.name,
                    summary: record.payload.summary,
                    details: record.payload.description,
                    request: requestPresentation(
                        record.payload.request
                    ),
                    createdAt: record.payload.createdAt,
                    updatedAt: record.payload.updatedAt
                )
            )
        }

        return Projection(
            snapshot: AtlasVaultSavedSearchSnapshot(searches: searches),
            mapping: mapping,
            recordIdentifiers: recordIdentifiers
        )
    }

    private static func requestPresentation(
        _ request: AtlasSearchRequest
    ) -> AtlasVaultSavedSearchRequestPresentation {
        AtlasVaultSavedSearchRequestPresentation(
            text: request.text,
            status: request.status,
            organizations: request.organizations,
            sourceIDs: request.sourceIDs,
            cities: request.cities,
            countriesISO3: request.countriesISO3,
            nationalInternational: request.nationalInternational,
            gradeCodes: request.gradeCodes,
            ccogFamilies: request.ccogFamilies,
            capabilityTags: request.capabilityTags,
            contractGroups: request.contractGroups,
            seniorityGroups: request.seniorityGroups,
            workModalities: request.workModalities,
            volunteerKinds: request.volunteerKinds,
            unvCategories: request.unvCategories,
            unvVolunteerTypes: request.unvVolunteerTypes,
            closingDateTo: request.closingDateTo,
            includeLowConfidence: request.includeLowConfidence,
            includeFacets: request.includeFacets,
            limit: request.limit,
            offset: request.offset,
            sort: request.sort
        )
    }

    private static func isStrictUTCTimestamp(_ value: String) -> Bool {
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
            } else if !(48...57).contains(bytes[index]) {
                return false
            }
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: value) else {
            return false
        }
        return formatter.string(from: date) == value
    }
}
