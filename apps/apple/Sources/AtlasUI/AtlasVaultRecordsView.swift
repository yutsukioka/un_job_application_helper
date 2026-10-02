import SwiftUI

enum AtlasVaultRecordFamily: String, CaseIterable, Identifiable, Sendable {
  case savedJob = "Saved Jobs"
  case applicationNote = "Application Notes"
  case profileSnippet = "Profile Snippets"
  case draftMetadata = "Draft Metadata"
  var id: String { rawValue }
  var fields: [(String, String)] {
    switch self {
    case .savedJob: [("job_key", "Job key"), ("status", "Status"), ("notes", "Notes")]
    case .applicationNote:
      [("title", "Title"), ("body", "Note"), ("note_kind", "Kind"), ("linked_job_key", "Job key")]
    case .profileSnippet:
      [
        ("title", "Title"), ("body", "Snippet"), ("target_system", "Target system"),
        ("field_hint", "Field"),
      ]
    case .draftMetadata:
      [
        ("target_system", "Target system"), ("document_type", "Document type"),
        ("generated_document_reference", "Document reference"), ("draft_status", "Status"),
        ("linked_job_key", "Job key"),
      ]
    }
  }
  var required: [String] {
    switch self {
    case .savedJob: ["job_key", "status"]
    case .applicationNote: ["body", "note_kind"]
    case .profileSnippet: ["title", "body"]
    case .draftMetadata:
      ["target_system", "document_type", "generated_document_reference", "draft_status"]
    }
  }
}

struct AtlasVaultRecordDraft: Identifiable, Sendable, CustomStringConvertible,
  CustomDebugStringConvertible
{
  let id = UUID()
  let family: AtlasVaultRecordFamily
  let metadata: AtlasHydratedRecordMetadata?
  private let original: AtlasVaultSavePayload
  var values: [String: String]

  init(
    family: AtlasVaultRecordFamily, metadata: AtlasHydratedRecordMetadata? = nil,
    original: AtlasVaultSavePayload? = nil,
    timestamp: String = AtlasVaultLocalStoreMerger.currentTimestamp()
  ) throws {
    self.family = family
    self.metadata = metadata
    let payload: AtlasVaultSavePayload
    switch family {
    case .savedJob:
      payload = .savedJob(
        .init(
          type: .savedJob, payload: .init(jobKey: "", status: "saved"), clientCreatedAt: timestamp,
          clientUpdatedAt: timestamp))
    case .applicationNote:
      payload = .applicationNote(
        .init(
          type: .applicationNote,
          payload: .init(body: "", noteKind: "general", createdAt: timestamp, updatedAt: timestamp),
          clientCreatedAt: timestamp, clientUpdatedAt: timestamp))
    case .profileSnippet:
      payload = .profileSnippet(
        .init(
          type: .profileSnippet,
          payload: .init(title: "", body: "", createdAt: timestamp, updatedAt: timestamp),
          clientCreatedAt: timestamp, clientUpdatedAt: timestamp))
    case .draftMetadata:
      payload = .draftMetadata(
        .init(
          type: .draftMetadata,
          payload: .init(
            targetSystem: "OTHER", documentType: "cv", generatedDocumentReference: "",
            draftStatus: "draft", generatedAt: timestamp),
          clientCreatedAt: timestamp, clientUpdatedAt: timestamp))
    }
    self.original = original ?? payload
    let envelope = try AtlasVaultDeviceDelivery.map(
      JSONSerialization.jsonObject(with: self.original.encodedPayloadEnvelope()))
    let body = try AtlasVaultDeviceDelivery.map(envelope["payload"])
    values = Dictionary(
      uniqueKeysWithValues: family.fields.map { ($0.0, body[$0.0] as? String ?? "") })
  }

  var isValid: Bool {
    family.required.allSatisfy {
      !(values[$0] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
      && values.values.allSatisfy { $0.utf8.count <= 64 * 1024 }
  }

  func payload(timestamp: String = AtlasVaultLocalStoreMerger.currentTimestamp()) throws
    -> AtlasVaultSavePayload
  {
    guard isValid else { throw AtlasVaultSaveError.invalidMutation }
    var envelope = try AtlasVaultDeviceDelivery.map(
      JSONSerialization.jsonObject(with: original.encodedPayloadEnvelope()))
    var body = try AtlasVaultDeviceDelivery.map(envelope["payload"])
    for (key, _) in family.fields {
      let value = values[key] ?? ""
      body[key] = !family.required.contains(key) && value.isEmpty ? nil : value
    }
    if family != .draftMetadata { body["updated_at"] = timestamp }
    envelope["payload"] = body
    envelope["client_updated_at"] = timestamp
    let data = try AtlasVaultEpochRotation.canonical(envelope)
    let decoder = JSONDecoder()
    switch family {
    case .savedJob:
      return .savedJob(try decoder.decode(AtlasSavedJobVaultRecordPayload.self, from: data))
    case .applicationNote:
      return .applicationNote(
        try decoder.decode(AtlasApplicationNoteVaultRecordPayload.self, from: data))
    case .profileSnippet:
      return .profileSnippet(
        try decoder.decode(AtlasProfileSnippetVaultRecordPayload.self, from: data))
    case .draftMetadata:
      return .draftMetadata(
        try decoder.decode(AtlasDraftMetadataVaultRecordPayload.self, from: data))
    }
  }
  var title: String {
    values["title"] ?? values["job_key"] ?? values["generated_document_reference"]
      ?? family.rawValue
  }
  var description: String { "AtlasVaultRecordDraft(<redacted>)" }
  var debugDescription: String { description }
}

@MainActor
public final class AtlasVaultRecordsOwner: ObservableObject, AtlasVaultPrivateSessionBoundary {
  @Published private(set) var records: [AtlasVaultRecordDraft] = []
  @Published private(set) var busy = false
  @Published private(set) var message: String?
  @Published private(set) var isAvailable = false
  @Published private(set) var isReadOnly = true
  private var vaultID: String?
  private var generation = UUID()
  private var mutationTask: Task<AtlasVaultPrivateMutationResult, Never>?
  private let read: @Sendable () async throws -> AtlasVaultHydratedState
  private let apply:
    @Sendable (AtlasVaultRuntimeMutationRequest) async -> AtlasVaultPrivateMutationResult
  private let contain: @Sendable () async -> Void

  init(
    read: @escaping @Sendable () async throws -> AtlasVaultHydratedState,
    apply:
      @escaping @Sendable (AtlasVaultRuntimeMutationRequest) async ->
      AtlasVaultPrivateMutationResult,
    contain: @escaping @Sendable () async -> Void
  ) {
    self.read = read
    self.apply = apply
    self.contain = contain
  }

  public func activatePrivateSession(selectedVault: String) async -> Bool {
    hidePrivatePresentation()
    vaultID = selectedVault
    isAvailable = true
    await refresh()
    return isAvailable
  }
  public func hidePrivatePresentation() {
    generation = UUID()
    vaultID = nil
    records = []
    message = nil
    busy = false
    isAvailable = false
    isReadOnly = true
    mutationTask?.cancel()
  }
  public func stopAndDrainPrivateSession() async {
    let task = mutationTask
    hidePrivatePresentation()
    _ = await task?.value
    mutationTask = nil
  }
  func refresh() async {
    guard isAvailable else { return }
    let token = generation
    do {
      let s = try await read()
      guard generation == token, isAvailable else { return }
      isReadOnly = s.isReadOnly
      var result: [AtlasVaultRecordDraft] = []
      for r in s.savedJobs {
        result.append(
          try .init(
            family: .savedJob, metadata: r.metadata,
            original: .savedJob(
              .init(
                type: .savedJob, payload: r.payload, clientCreatedAt: r.clientCreatedAt,
                clientUpdatedAt: r.clientUpdatedAt))))
      }
      for r in s.applicationNotes {
        result.append(
          try .init(
            family: .applicationNote, metadata: r.metadata,
            original: .applicationNote(
              .init(
                type: .applicationNote, payload: r.payload, clientCreatedAt: r.clientCreatedAt,
                clientUpdatedAt: r.clientUpdatedAt))))
      }
      for r in s.profileSnippets {
        result.append(
          try .init(
            family: .profileSnippet, metadata: r.metadata,
            original: .profileSnippet(
              .init(
                type: .profileSnippet, payload: r.payload, clientCreatedAt: r.clientCreatedAt,
                clientUpdatedAt: r.clientUpdatedAt))))
      }
      for r in s.draftMetadata {
        result.append(
          try .init(
            family: .draftMetadata, metadata: r.metadata,
            original: .draftMetadata(
              .init(
                type: .draftMetadata, payload: r.payload, clientCreatedAt: r.clientCreatedAt,
                clientUpdatedAt: r.clientUpdatedAt))))
      }
      records = result
    } catch {
      guard generation == token else { return }
      records = []
      message = "Private records are unavailable."
      isReadOnly = true
      isAvailable = false
    }
  }
  func save(_ draft: AtlasVaultRecordDraft) async {
    do {
      let payload = try draft.payload()
      if let m = draft.metadata {
        await mutate(
          .init(updates: [
            .init(recordID: m.id, currentRevision: m.revision, payload: payload, keyID: m.keyID)
          ]))
      } else {
        await mutate(.init(creates: [.init(payload: payload, keyID: "runtime")]))
      }
    } catch { message = "Complete the required fields." }
  }
  func delete(_ draft: AtlasVaultRecordDraft) async {
    guard let m = draft.metadata else { return }
    await mutate(
      .init(deletes: [.init(recordID: m.id, currentRevision: m.revision, keyID: m.keyID)]))
  }
  private func mutate(_ mutations: AtlasVaultMutationSet) async {
    guard !busy, !isReadOnly, isAvailable, let vaultID else { return }
    busy = true
    message = nil
    let token = generation
    let apply = apply
    let task = Task { await apply(.init(expectedVaultID: vaultID, mutations: mutations)) }
    mutationTask = task
    let outcome = await task.value
    guard generation == token else { return }
    mutationTask = nil
    switch outcome {
    case .committed: await refresh()
    case .committedDurabilityUnconfirmed:
      await contain()
      hidePrivatePresentation()
    case .locked: hidePrivatePresentation()
    case .failed: message = "The change could not be saved."
    case .cancelled: message = "The change was cancelled."
    }
    busy = false
  }
}

@MainActor
public struct AtlasVaultRecordsContext {
  let owner: AtlasVaultRecordsOwner
}

@MainActor
final class AtlasVaultCombinedPrivateBoundary: AtlasVaultPrivateSessionBoundary {
  let searches: any AtlasVaultPrivateSessionBoundary
  let records: AtlasVaultRecordsOwner
  init(searches: any AtlasVaultPrivateSessionBoundary, records: AtlasVaultRecordsOwner) {
    self.searches = searches
    self.records = records
  }
  func activatePrivateSession(selectedVault: String) async -> Bool {
    guard await searches.activatePrivateSession(selectedVault: selectedVault) else { return false }
    return await records.activatePrivateSession(selectedVault: selectedVault)
  }
  func hidePrivatePresentation() {
    searches.hidePrivatePresentation()
    records.hidePrivatePresentation()
  }
  func stopAndDrainPrivateSession() async {
    hidePrivatePresentation()
    await searches.stopAndDrainPrivateSession()
    await records.stopAndDrainPrivateSession()
  }
}

@MainActor
struct AtlasVaultRecordsView: View {
  @ObservedObject var owner: AtlasVaultRecordsOwner
  @Environment(\.dismiss) private var dismiss
  @State private var family: AtlasVaultRecordFamily = .savedJob
  @State private var draft: AtlasVaultRecordDraft?
  @State private var deletion: AtlasVaultRecordDraft?

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        Picker("Record family", selection: $family) {
          ForEach(AtlasVaultRecordFamily.allCases) { Text($0.rawValue).tag($0) }
        }.padding()
        List {
          ForEach(owner.records.filter { $0.family == family }) { record in
            HStack {
              Text(record.title.isEmpty ? family.rawValue : record.title).lineLimit(3)
              Spacer()
              Button {
                draft = record
              } label: {
                Image(systemName: "pencil")
              }.help("Edit record").disabled(owner.isReadOnly)
              Button(role: .destructive) {
                deletion = record
              } label: {
                Image(systemName: "trash")
              }.help("Delete record").disabled(owner.isReadOnly)
            }.buttonStyle(.borderless)
          }
        }
        if owner.isReadOnly {
          Text("Read-only legacy records. Runtime enrollment is required to save changes.")
            .foregroundStyle(.secondary).padding()
        }
        if let message = owner.message { Text(message).foregroundStyle(.secondary).padding() }
        if owner.busy { ProgressView().padding(8) }
      }
      .navigationTitle("Private Records")
      .toolbar {
        ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
        ToolbarItem(placement: .primaryAction) {
          Button {
            draft = try? AtlasVaultRecordDraft(family: family)
          } label: {
            Image(systemName: "plus")
          }.help("Add record").disabled(owner.isReadOnly)
        }
      }
      .disabled(owner.busy || !owner.isAvailable)
    }
    .frame(idealWidth: 620, idealHeight: 560)
    .task { await owner.refresh() }
    .sheet(item: $draft) { value in
      AtlasVaultRecordForm(draft: value) { value in await owner.save(value) }
    }
    .confirmationDialog(
      "Delete record?",
      isPresented: Binding(get: { deletion != nil }, set: { if !$0 { deletion = nil } })
    ) {
      Button("Delete", role: .destructive) {
        guard let value = deletion else { return }
        deletion = nil
        Task { await owner.delete(value) }
      }
    }
    .onChange(of: owner.isAvailable) { _, available in
      if !available {
        draft = nil
        deletion = nil
        dismiss()
      }
    }
    .onDisappear {
      draft = nil
      deletion = nil
    }
  }
}

@MainActor
private struct AtlasVaultRecordForm: View {
  @State var draft: AtlasVaultRecordDraft
  let save: @MainActor (AtlasVaultRecordDraft) async -> Void
  @Environment(\.dismiss) private var dismiss
  var body: some View {
    NavigationStack {
      Form {
        ForEach(draft.family.fields, id: \.0) { key, label in
          TextField(
            label, text: Binding(get: { draft.values[key] ?? "" }, set: { draft.values[key] = $0 }),
            axis: .vertical
          )
          .lineLimit((key == "body" || key == "notes") ? 3...8 : 1...3)
        }
      }.formStyle(.grouped)
        .navigationTitle(draft.family.rawValue)
        .toolbar {
          ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
          ToolbarItem(placement: .confirmationAction) {
            Button("Save") {
              let value = draft
              dismiss()
              Task { await save(value) }
            }.disabled(!draft.isValid)
          }
        }
    }.frame(idealWidth: 540, idealHeight: 480)
      .onDisappear { draft.values = [:] }
  }
}
