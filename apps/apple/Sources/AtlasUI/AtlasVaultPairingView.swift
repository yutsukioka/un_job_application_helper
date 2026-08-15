import Foundation
import SwiftUI
import UniformTypeIdentifiers

public protocol AtlasVaultTrustedPairingAuthority: AnyObject, Sendable {
    func clearSensitiveInput() async
    func stopAndDrain() async
}

public enum AtlasVaultTrustedPairingPresentationStatus: Equatable, Sendable {
    case hidden
    case ready
    case identityReady
    case offerReady
    case offerSaved
    case acceptanceReady
    case acceptanceSaved
    case codesReady
    case codesConfirmed
    case deliveryReady
    case deliverySaved
    case acknowledgementReady
    case acknowledgementSaved
    case completed
    case cancelled
    case migrationRequired
    case existingVault
    case unavailable
    case recoveryRequired
    case failed
}

public struct AtlasVaultPairingPresentationClaim: Hashable, Sendable {
    private let identifier = UUID()

    public init() {}
}

public struct AtlasVaultTrustedPairingContext {
    public let owner: AtlasVaultTrustedPairingPresentationOwner

    public init(owner: AtlasVaultTrustedPairingPresentationOwner) {
        self.owner = owner
    }
}

public struct AtlasVaultPairingPendingSave: Identifiable, Sendable {
    public let id: UUID
    public let kind: AtlasVaultPairingArtifactKind
    public let artifact: AtlasVaultPairingArtifact

    init(
        kind: AtlasVaultPairingArtifactKind,
        artifact: AtlasVaultPairingArtifact
    ) {
        id = UUID()
        self.kind = kind
        self.artifact = artifact
    }
}

@MainActor
public final class AtlasVaultTrustedPairingPresentationOwner:
    ObservableObject,
    AtlasVaultTrustedPairingAuthority
{
    @Published public private(set) var status:
        AtlasVaultTrustedPairingPresentationStatus = .hidden
    @Published public private(set) var role: AtlasVaultPairingRole?
    @Published public private(set) var stage: AtlasVaultPairingStage?
    @Published public private(set) var localFingerprint: String?
    @Published public private(set) var peerFingerprint: String?
    @Published public private(set) var sas: String?
    @Published public private(set) var expiresAt: String?
    @Published public private(set) var trusted = false
    @Published public private(set) var pendingTransaction = false
    @Published public private(set) var pendingSave:
        AtlasVaultPairingPendingSave?

    private let coordinator: any AtlasVaultTrustedPairingCoordinating
    private var presentationClaim: AtlasVaultPairingPresentationClaim?
    private var operationTask: Task<Void, Never>?
    private var generation: UInt64 = 0
    private var terminal = false

    public init(coordinator: any AtlasVaultTrustedPairingCoordinating) {
        self.coordinator = coordinator
    }

    public var isBusy: Bool { operationTask != nil }

    public func present() {
        guard !terminal, status == .hidden else { return }
        status = .ready
    }

    public func dismiss() {
        clearSensitiveInputNow()
        clearPublicDetails()
        status = .hidden
        presentationClaim = nil
    }

    public func claimPresentation(
        _ claim: AtlasVaultPairingPresentationClaim
    ) -> Bool {
        guard presentationClaim == nil || presentationClaim == claim else {
            return false
        }
        presentationClaim = claim
        return true
    }

    public func releasePresentation(
        _ claim: AtlasVaultPairingPresentationClaim
    ) -> Bool {
        guard presentationClaim == claim else { return false }
        presentationClaim = nil
        return true
    }

    public func ownsPresentation(
        _ claim: AtlasVaultPairingPresentationClaim
    ) -> Bool {
        presentationClaim == claim
    }

    public func inspect() {
        run { await self.coordinator.inspect() }
    }

    public func createDeviceIdentity() {
        run { await self.coordinator.createDeviceIdentity() }
    }

    public func createPairingOffer() {
        run { await self.coordinator.createPairingOffer() }
    }

    public func savePairingOffer() {
        prepareSave(.offer)
    }

    public func importPairingOffer(from url: URL) {
        importArtifact(.offer, from: url)
    }

    public func savePairingAcceptance() {
        prepareSave(.acceptance)
    }

    public func importPairingAcceptance(from url: URL) {
        importArtifact(.acceptance, from: url)
    }

    public func confirmCodesMatch() {
        run { await self.coordinator.confirmCodesMatch() }
    }

    public func saveKeyDelivery() {
        prepareSave(.delivery)
    }

    public func importKeyDelivery(from url: URL) {
        importArtifact(.delivery, from: url)
    }

    public func savePairingAcknowledgement() {
        prepareSave(.acknowledgement)
    }

    public func importPairingAcknowledgement(from url: URL) {
        importArtifact(.acknowledgement, from: url)
    }

    public func resumePairing() {
        run { await self.coordinator.resumePairing() }
    }

    public func discardPairing() {
        run { await self.coordinator.discardPairing() }
    }

    public func completePendingSave(committed: Bool) {
        guard let pendingSave else { return }
        self.pendingSave = nil
        run {
            await self.coordinator.pairingArtifactSaveFinished(
                pendingSave.kind,
                committed: committed
            )
        }
    }

    public func clearSensitiveInput() async {
        clearSensitiveInputNow()
        let retained = operationTask
        operationTask?.cancel()
        await retained?.value
        operationTask = nil
    }

    public func stopAndDrain() async {
        terminal = true
        generation &+= 1
        clearSensitiveInputNow()
        let retained = operationTask
        operationTask?.cancel()
        await retained?.value
        operationTask = nil
        await coordinator.stop()
        clearPublicDetails()
        status = .hidden
    }

    private func prepareSave(_ kind: AtlasVaultPairingArtifactKind) {
        runValue {
            let artifact = try await self.coordinator.artifactToSave(kind)
            return AtlasVaultPairingPendingSave(
                kind: kind,
                artifact: artifact
            )
        } publish: { request in
            self.pendingSave = request
        }
    }

    private func importArtifact(
        _ kind: AtlasVaultPairingArtifactKind,
        from url: URL
    ) {
        runValue {
            let artifact = try await Self.readArtifact(from: url)
            guard artifact.kind == kind else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            switch kind {
            case .offer:
                return await self.coordinator.importPairingOffer(artifact)
            case .acceptance:
                return await self.coordinator.importPairingAcceptance(artifact)
            case .delivery:
                return await self.coordinator.importKeyDelivery(artifact)
            case .acknowledgement:
                return await self.coordinator
                    .importPairingAcknowledgement(artifact)
            }
        } publish: { result in
            self.publish(result)
        }
    }

    private func run(
        _ operation: @escaping @MainActor @Sendable () async
            -> AtlasVaultTrustedPairingResult
    ) {
        runValue(operation, publish: publish)
    }

    private func runValue<Value: Sendable>(
        _ operation: @escaping @MainActor @Sendable () async throws -> Value,
        publish: @escaping @MainActor @Sendable (Value) -> Void
    ) {
        guard !terminal, operationTask == nil else { return }
        let operationGeneration = generation
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            defer {
                if self.isCurrent(operationGeneration) {
                    self.operationTask = nil
                }
            }
            do {
                let value = try await operation()
                guard self.isCurrent(operationGeneration) else { return }
                publish(value)
            } catch {
                guard self.isCurrent(operationGeneration) else { return }
                self.clearSensitiveInputNow()
                self.status = .failed
            }
        }
        operationTask = task
    }

    private func publish(_ result: AtlasVaultTrustedPairingResult) {
        role = result.role
        stage = result.stage
        localFingerprint = result.localFingerprint
        peerFingerprint = result.peerFingerprint
        sas = result.sas
        expiresAt = result.expiresAt
        trusted = result.trusted
        pendingTransaction = result.pendingTransaction
        status = Self.presentationStatus(result.disposition)
    }

    private func clearSensitiveInputNow() {
        sas = nil
        pendingSave = nil
        generation &+= 1
    }

    private func clearPublicDetails() {
        role = nil
        stage = nil
        localFingerprint = nil
        peerFingerprint = nil
        sas = nil
        expiresAt = nil
        trusted = false
        pendingTransaction = false
        pendingSave = nil
    }

    private func isCurrent(_ candidate: UInt64) -> Bool {
        !terminal && generation == candidate
    }

    private static func readArtifact(
        from url: URL
    ) async throws -> AtlasVaultPairingArtifact {
        try await Task.detached {
            let hasAccess = url.startAccessingSecurityScopedResource()
            defer {
                if hasAccess { url.stopAccessingSecurityScopedResource() }
            }
            let values = try url.resourceValues(
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
                byteCount
                    <= AtlasVaultPairingArtifactStageStore.maximumByteCount
            else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            let data = try Data(
                contentsOf: url,
                options: [.mappedIfSafe, .uncached]
            )
            guard data.count == byteCount else {
                throw AtlasVaultPairingTransactionError.invalidTransaction
            }
            return try AtlasVaultPairingArtifact.decodeStrict(data)
        }.value
    }

    private static func presentationStatus(
        _ disposition: AtlasVaultTrustedPairingDisposition
    ) -> AtlasVaultTrustedPairingPresentationStatus {
        switch disposition {
        case .ready: .ready
        case .identityReady: .identityReady
        case .offerReady: .offerReady
        case .offerSaved: .offerSaved
        case .acceptanceReady: .acceptanceReady
        case .acceptanceSaved: .acceptanceSaved
        case .codesReady: .codesReady
        case .codesConfirmed: .codesConfirmed
        case .deliveryReady: .deliveryReady
        case .deliverySaved: .deliverySaved
        case .acknowledgementReady: .acknowledgementReady
        case .acknowledgementSaved: .acknowledgementSaved
        case .completed: .completed
        case .cancelled: .cancelled
        case .migrationRequired: .migrationRequired
        case .existingVault: .existingVault
        case .unavailable: .unavailable
        case .recoveryRequired: .recoveryRequired
        case .failed: .failed
        }
    }
}

@MainActor
public struct AtlasVaultPairingView: View {
    @ObservedObject private var owner:
        AtlasVaultTrustedPairingPresentationOwner
    @State private var importKind: AtlasVaultPairingArtifactKind?
    @State private var importerPresented = false
    @State private var exporterPresented = false

    public init(owner: AtlasVaultTrustedPairingPresentationOwner) {
        self.owner = owner
    }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text(statusText).font(.headline)
                safeDetails
                Divider()
                actions
                Divider()
                Text("Pairing files are transferred manually.")
                Text("Compare codes on both recognized devices.")
                Text("Ongoing synchronization is not available.")
                Text("Device revocation and key rotation are not available.")
                Text("Pair only a recognized device.")
            }
            .padding()
        }
        .fileImporter(
            isPresented: $importerPresented,
            allowedContentTypes: [pairingType],
            allowsMultipleSelection: false
        ) { result in
            guard let kind = importKind else { return }
            importKind = nil
            switch result {
            case let .success(urls):
                guard let url = urls.first else { return }
                importArtifact(kind, from: url)
            case .failure:
                break
            }
        }
        .fileExporter(
            isPresented: $exporterPresented,
            document: owner.pendingSave.map {
                AtlasVaultPairingDocument(artifact: $0.artifact)
            },
            contentType: pairingType,
            defaultFilename: "AtlasVault-Pairing.atlaspair"
        ) { result in
            owner.completePendingSave(
                committed: (try? result.get()) != nil
            )
        }
        .onChange(of: owner.pendingSave?.id) { _, identifier in
            exporterPresented = identifier != nil
        }
    }

    @ViewBuilder
    private var safeDetails: some View {
        if let value = owner.localFingerprint {
            LabeledContent("This device", value: value)
        }
        if let value = owner.peerFingerprint {
            LabeledContent("Other device", value: value)
        }
        if let value = owner.sas {
            LabeledContent("Comparison code", value: value)
        }
        if let value = owner.stage {
            LabeledContent("Stage", value: value.rawValue)
        }
        if let value = owner.expiresAt {
            LabeledContent("Expires", value: value)
        }
        if owner.trusted {
            LabeledContent("Status", value: "Trusted")
        }
    }

    private var actions: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button("Create Device Identity") {
                owner.createDeviceIdentity()
            }
            Button("Create Pairing Offer") {
                owner.createPairingOffer()
            }
            Button("Save Pairing Offer") {
                owner.savePairingOffer()
            }
            Button("Import Pairing Offer") { beginImport(.offer) }
            Button("Save Pairing Acceptance") {
                owner.savePairingAcceptance()
            }
            Button("Import Pairing Acceptance") {
                beginImport(.acceptance)
            }
            Button("Codes Match") { owner.confirmCodesMatch() }
            Button("Save Key Delivery") { owner.saveKeyDelivery() }
            Button("Import Key Delivery") { beginImport(.delivery) }
            Button("Save Pairing Acknowledgement") {
                owner.savePairingAcknowledgement()
            }
            Button("Import Pairing Acknowledgement") {
                beginImport(.acknowledgement)
            }
            Button("Resume Pairing") { owner.resumePairing() }
            Button("Discard Pairing") { owner.discardPairing() }
        }
        .disabled(owner.isBusy)
    }

    private func beginImport(_ kind: AtlasVaultPairingArtifactKind) {
        importKind = kind
        importerPresented = true
    }

    private func importArtifact(
        _ kind: AtlasVaultPairingArtifactKind,
        from url: URL
    ) {
        switch kind {
        case .offer: owner.importPairingOffer(from: url)
        case .acceptance: owner.importPairingAcceptance(from: url)
        case .delivery: owner.importKeyDelivery(from: url)
        case .acknowledgement:
            owner.importPairingAcknowledgement(from: url)
        }
    }

    private var pairingType: UTType {
        UTType(filenameExtension: "atlaspair") ?? .json
    }

    private var statusText: String {
        switch owner.status {
        case .hidden, .ready: "Trusted-device pairing is ready."
        case .identityReady: "This device identity is ready."
        case .offerReady: "The pairing offer is ready to save."
        case .offerSaved: "The pairing offer was saved."
        case .acceptanceReady: "The pairing acceptance is ready to save."
        case .acceptanceSaved, .codesReady:
            "Compare the code on both devices."
        case .codesConfirmed: "The comparison code was confirmed."
        case .deliveryReady: "Encrypted key delivery is ready to save."
        case .deliverySaved: "Encrypted key delivery was saved."
        case .acknowledgementReady:
            "The signed acknowledgement is ready to save."
        case .acknowledgementSaved:
            "The signed acknowledgement was saved."
        case .completed: "Trusted-device pairing completed."
        case .cancelled: "The pairing file operation was cancelled."
        case .migrationRequired:
            "Plaintext private data must be migrated before pairing."
        case .existingVault: "This device already has an AtlasVault."
        case .unavailable: "Trusted-device pairing is unavailable."
        case .recoveryRequired:
            "Trusted-device pairing requires recovery."
        case .failed: "Trusted-device pairing failed."
        }
    }
}

private enum AtlasVaultTrustedPairingContractSurface {
    typealias Artifact = AtlasVaultPairingArtifact
    typealias Transaction = AtlasVaultPairingTransaction
    typealias Registry = AtlasVaultTrustedDeviceRegistry
    typealias Replay = AtlasVaultPairingReplayStore
    typealias Bootstrap = AtlasVaultPairingBootstrap
    typealias Delivery = AtlasVaultSignedVaultKeyDelivery
    typealias Acknowledgement = AtlasVaultSignedPairingAcknowledgement
}
