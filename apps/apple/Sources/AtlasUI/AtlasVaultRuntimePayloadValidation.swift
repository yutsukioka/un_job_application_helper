import Foundation

// Runtime admission mirrors the existing Dart payload schema. Legacy P3 decoding stays unchanged.
enum AtlasVaultRuntimePayloadValidation {
  private enum Field {
    case text, nonemptyText, integer, boolean, strings, date, timestamp, request
  }

  static func validate(_ envelope: [String: Any]) throws {
    try object(
      envelope,
      required: [
        "type": .nonemptyText, "payload_schema": .integer,
        "client_created_at": .timestamp, "client_updated_at": .timestamp,
      ], optional: [:], extra: ["payload"])
    guard try viewInteger(envelope["payload_schema"]) == 1,
      let type = envelope["type"] as? String,
      let payload = envelope["payload"] as? [String: Any]
    else { throw AtlasVaultRotationError.rejected }
    switch type {
    case "saved_search":
      try object(
        payload, required: ["name": .text, "summary": .text, "request": .request],
        optional: ["description": .text, "created_at": .timestamp, "updated_at": .timestamp])
    case "saved_job":
      try object(
        payload, required: ["job_key": .text, "status": .text],
        optional: ["id": .text, "notes": .text, "applied_at": .timestamp, "updated_at": .timestamp])
    case "application_note":
      try object(
        payload,
        required: [
          "body": .text, "note_kind": .text,
          "created_at": .timestamp, "updated_at": .timestamp,
        ],
        optional: [
          "title": .text,
          "linked_job_key": .text, "linked_saved_job_record_id": .text, "is_pinned": .boolean,
          "sort_order": .integer,
        ])
    case "profile_snippet":
      try object(
        payload,
        required: [
          "title": .text, "body": .text, "tags": .strings,
          "created_at": .timestamp, "updated_at": .timestamp,
        ],
        optional: [
          "target_system": .text,
          "field_hint": .text, "provenance_notes": .text,
        ])
    case "draft_metadata":
      try object(
        payload,
        required: [
          "target_system": .text, "document_type": .text,
          "generated_document_reference": .text, "draft_status": .text, "generated_at": .timestamp,
        ],
        optional: [
          "linked_job_key": .text, "linked_saved_job_record_id": .text,
          "reviewed_at": .timestamp, "submitted_at": .timestamp, "archived_at": .timestamp,
          "personal_context_reference": .text, "context_summary": .text,
        ])
    default: throw AtlasVaultRotationError.rejected
    }
  }

  private static func object(
    _ value: [String: Any], required: [String: Field], optional: [String: Field],
    extra: Set<String> = []
  ) throws {
    let keys = Set(value.keys)
    guard keys.isSuperset(of: Set(required.keys).union(extra)),
      keys.isSubset(of: Set(required.keys).union(optional.keys).union(extra))
    else {
      throw AtlasVaultRotationError.rejected
    }
    for (key, rule) in required.merging(optional, uniquingKeysWith: { first, _ in first }) {
      if let raw = value[key] { try field(raw, rule) }
    }
  }

  private static func field(_ raw: Any, _ rule: Field) throws {
    switch rule {
    case .text, .nonemptyText:
      guard let text = raw as? String, rule == .text || !text.isEmpty else {
        throw AtlasVaultRotationError.rejected
      }
    case .integer:
      guard let n = raw as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
        ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(String(cString: n.objCType)),
        Int64(n.stringValue) != nil
      else { throw AtlasVaultRotationError.rejected }
    case .boolean:
      guard let n = raw as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() else {
        throw AtlasVaultRotationError.rejected
      }
    case .strings:
      guard raw is [String] else { throw AtlasVaultRotationError.rejected }
    case .date, .timestamp:
      guard let text = raw as? String else { throw AtlasVaultRotationError.rejected }
      try date(text, timestamp: rule == .timestamp)
    case .request:
      guard let value = raw as? [String: Any] else { throw AtlasVaultRotationError.rejected }
      var required: [String: Field] = [
        "include_low_confidence": .boolean, "include_facets": .boolean,
        "limit": .integer, "offset": .integer, "sort": .nonemptyText,
      ]
      for key in [
        "status", "organizations", "source_ids", "cities", "countries_iso3",
        "national_international",
        "grade_codes", "ccog_families", "capability_tags", "contract_groups", "seniority_groups",
        "work_modalities", "volunteer_kinds", "unv_categories", "unv_volunteer_types",
      ] {
        required[key] = .strings
      }
      try object(value, required: required, optional: ["text": .text, "closing_date_to": .date])
    }
  }

  private static func date(_ text: String, timestamp: Bool) throws {
    let pattern =
      timestamp
      ? "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$" : "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
    guard text.utf8.count == (timestamp ? 20 : 10),
      text.range(of: pattern, options: .regularExpression) != nil
    else {
      throw AtlasVaultRotationError.rejected
    }
    let parts = text.split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
    let year = parts[0]
    let month = parts[1]
    let day = parts[2]
    guard year > 0, (1...12).contains(month) else { throw AtlasVaultRotationError.rejected }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
    let days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    guard (1...days[month - 1]).contains(day),
      !timestamp || (parts[3] < 24 && parts[4] < 60 && parts[5] < 60)
    else {
      throw AtlasVaultRotationError.rejected
    }
  }
}
