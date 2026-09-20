import 'dart:typed_data';

import 'canonical_json.dart';
import 'strict_values.dart';

enum AtlasVaultPayloadType {
  savedSearch('saved_search'),
  savedJob('saved_job'),
  applicationNote('application_note'),
  profileSnippet('profile_snippet'),
  draftMetadata('draft_metadata');

  const AtlasVaultPayloadType(this.wireName);

  final String wireName;

  static AtlasVaultPayloadType parse(Object? value) {
    final text = requireAtlasVaultString(
      value,
      field: 'type',
      allowEmpty: false,
    );
    for (final type in values) {
      if (type.wireName == text) {
        return type;
      }
    }
    throw const AtlasVaultFormatException(
      'Plaintext record type is unsupported.',
    );
  }
}

sealed class AtlasVaultPayload {
  const AtlasVaultPayload();

  Map<String, Object?> toJson();

  @override
  bool operator ==(Object other) {
    return other.runtimeType == runtimeType &&
        other is AtlasVaultPayload &&
        canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());
  }

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;
}

final class AtlasSearchRequest {
  AtlasSearchRequest._({
    required this.text,
    required this.status,
    required this.organizations,
    required this.sourceIds,
    required this.cities,
    required this.countriesIso3,
    required this.nationalInternational,
    required this.gradeCodes,
    required this.ccogFamilies,
    required this.capabilityTags,
    required this.contractGroups,
    required this.seniorityGroups,
    required this.workModalities,
    required this.volunteerKinds,
    required this.unvCategories,
    required this.unvVolunteerTypes,
    required this.closingDateTo,
    required this.includeLowConfidence,
    required this.includeFacets,
    required this.limit,
    required this.offset,
    required this.sort,
  });

  static const _requiredKeys = <String>{
    'status',
    'organizations',
    'source_ids',
    'cities',
    'countries_iso3',
    'national_international',
    'grade_codes',
    'ccog_families',
    'capability_tags',
    'contract_groups',
    'seniority_groups',
    'work_modalities',
    'volunteer_kinds',
    'unv_categories',
    'unv_volunteer_types',
    'include_low_confidence',
    'include_facets',
    'limit',
    'offset',
    'sort',
  };
  static const _optionalKeys = <String>{'text', 'closing_date_to'};

  final String? text;
  final List<String> status;
  final List<String> organizations;
  final List<String> sourceIds;
  final List<String> cities;
  final List<String> countriesIso3;
  final List<String> nationalInternational;
  final List<String> gradeCodes;
  final List<String> ccogFamilies;
  final List<String> capabilityTags;
  final List<String> contractGroups;
  final List<String> seniorityGroups;
  final List<String> workModalities;
  final List<String> volunteerKinds;
  final List<String> unvCategories;
  final List<String> unvVolunteerTypes;
  final String? closingDateTo;
  final bool includeLowConfidence;
  final bool includeFacets;
  final int limit;
  final int offset;
  final String sort;

  factory AtlasSearchRequest.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: _requiredKeys,
      optionalKeys: _optionalKeys,
      context: 'Saved-search request',
    );
    return AtlasSearchRequest._(
      text: requireAtlasVaultOptionalString(
        value,
        key: 'text',
        field: 'request.text',
      ),
      status: requireAtlasVaultStringList(
        value['status'],
        field: 'request.status',
      ),
      organizations: requireAtlasVaultStringList(
        value['organizations'],
        field: 'request.organizations',
      ),
      sourceIds: requireAtlasVaultStringList(
        value['source_ids'],
        field: 'request.source_ids',
      ),
      cities: requireAtlasVaultStringList(
        value['cities'],
        field: 'request.cities',
      ),
      countriesIso3: requireAtlasVaultStringList(
        value['countries_iso3'],
        field: 'request.countries_iso3',
      ),
      nationalInternational: requireAtlasVaultStringList(
        value['national_international'],
        field: 'request.national_international',
      ),
      gradeCodes: requireAtlasVaultStringList(
        value['grade_codes'],
        field: 'request.grade_codes',
      ),
      ccogFamilies: requireAtlasVaultStringList(
        value['ccog_families'],
        field: 'request.ccog_families',
      ),
      capabilityTags: requireAtlasVaultStringList(
        value['capability_tags'],
        field: 'request.capability_tags',
      ),
      contractGroups: requireAtlasVaultStringList(
        value['contract_groups'],
        field: 'request.contract_groups',
      ),
      seniorityGroups: requireAtlasVaultStringList(
        value['seniority_groups'],
        field: 'request.seniority_groups',
      ),
      workModalities: requireAtlasVaultStringList(
        value['work_modalities'],
        field: 'request.work_modalities',
      ),
      volunteerKinds: requireAtlasVaultStringList(
        value['volunteer_kinds'],
        field: 'request.volunteer_kinds',
      ),
      unvCategories: requireAtlasVaultStringList(
        value['unv_categories'],
        field: 'request.unv_categories',
      ),
      unvVolunteerTypes: requireAtlasVaultStringList(
        value['unv_volunteer_types'],
        field: 'request.unv_volunteer_types',
      ),
      closingDateTo: requireAtlasVaultOptionalDate(
        value,
        key: 'closing_date_to',
        field: 'request.closing_date_to',
      ),
      includeLowConfidence: requireAtlasVaultBool(
        value['include_low_confidence'],
        field: 'request.include_low_confidence',
      ),
      includeFacets: requireAtlasVaultBool(
        value['include_facets'],
        field: 'request.include_facets',
      ),
      limit: requireAtlasVaultInt(value['limit'], field: 'request.limit'),
      offset: requireAtlasVaultInt(value['offset'], field: 'request.offset'),
      sort: requireAtlasVaultString(
        value['sort'],
        field: 'request.sort',
        allowEmpty: false,
      ),
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      if (text != null) 'text': text,
      'status': List<String>.from(status),
      'organizations': List<String>.from(organizations),
      'source_ids': List<String>.from(sourceIds),
      'cities': List<String>.from(cities),
      'countries_iso3': List<String>.from(countriesIso3),
      'national_international': List<String>.from(nationalInternational),
      'grade_codes': List<String>.from(gradeCodes),
      'ccog_families': List<String>.from(ccogFamilies),
      'capability_tags': List<String>.from(capabilityTags),
      'contract_groups': List<String>.from(contractGroups),
      'seniority_groups': List<String>.from(seniorityGroups),
      'work_modalities': List<String>.from(workModalities),
      'volunteer_kinds': List<String>.from(volunteerKinds),
      'unv_categories': List<String>.from(unvCategories),
      'unv_volunteer_types': List<String>.from(unvVolunteerTypes),
      if (closingDateTo != null) 'closing_date_to': closingDateTo,
      'include_low_confidence': includeLowConfidence,
      'include_facets': includeFacets,
      'limit': limit,
      'offset': offset,
      'sort': sort,
    };
  }

  @override
  bool operator ==(Object other) {
    return other is AtlasSearchRequest &&
        canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());
  }

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasSearchRequest(<redacted>)';
}

final class AtlasSavedSearchPayload extends AtlasVaultPayload {
  AtlasSavedSearchPayload._({
    required this.name,
    required this.summary,
    required this.description,
    required this.request,
    required this.createdAt,
    required this.updatedAt,
  });

  final String name;
  final String summary;
  final String? description;
  final AtlasSearchRequest request;
  final String? createdAt;
  final String? updatedAt;

  factory AtlasSavedSearchPayload.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{'name', 'summary', 'request'},
      optionalKeys: const <String>{'description', 'created_at', 'updated_at'},
      context: 'Saved-search payload',
    );
    return AtlasSavedSearchPayload._(
      name: requireAtlasVaultString(value['name'], field: 'payload.name'),
      summary: requireAtlasVaultString(
        value['summary'],
        field: 'payload.summary',
      ),
      description: requireAtlasVaultOptionalString(
        value,
        key: 'description',
        field: 'payload.description',
      ),
      request: AtlasSearchRequest.fromJson(
        requireAtlasVaultObject(
          value['request'],
          context: 'Saved-search request',
        ),
      ),
      createdAt: requireAtlasVaultOptionalUtcSeconds(
        value,
        key: 'created_at',
        field: 'payload.created_at',
      ),
      updatedAt: requireAtlasVaultOptionalUtcSeconds(
        value,
        key: 'updated_at',
        field: 'payload.updated_at',
      ),
    );
  }

  @override
  Map<String, Object?> toJson() {
    return <String, Object?>{
      'name': name,
      'summary': summary,
      if (description != null) 'description': description,
      'request': request.toJson(),
      if (createdAt != null) 'created_at': createdAt,
      if (updatedAt != null) 'updated_at': updatedAt,
    };
  }

  @override
  String toString() => 'AtlasSavedSearchPayload(<redacted>)';
}

final class AtlasSavedJobPayload extends AtlasVaultPayload {
  AtlasSavedJobPayload._({
    required this.id,
    required this.jobKey,
    required this.status,
    required this.notes,
    required this.appliedAt,
    required this.updatedAt,
  });

  final String? id;
  final String jobKey;
  final String status;
  final String? notes;
  final String? appliedAt;
  final String? updatedAt;

  factory AtlasSavedJobPayload.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{'job_key', 'status'},
      optionalKeys: const <String>{'id', 'notes', 'applied_at', 'updated_at'},
      context: 'Saved-job payload',
    );
    return AtlasSavedJobPayload._(
      id: requireAtlasVaultOptionalString(
        value,
        key: 'id',
        field: 'payload.id',
      ),
      jobKey: requireAtlasVaultString(
        value['job_key'],
        field: 'payload.job_key',
      ),
      status: requireAtlasVaultString(value['status'], field: 'payload.status'),
      notes: requireAtlasVaultOptionalString(
        value,
        key: 'notes',
        field: 'payload.notes',
      ),
      appliedAt: requireAtlasVaultOptionalUtcSeconds(
        value,
        key: 'applied_at',
        field: 'payload.applied_at',
      ),
      updatedAt: requireAtlasVaultOptionalUtcSeconds(
        value,
        key: 'updated_at',
        field: 'payload.updated_at',
      ),
    );
  }

  @override
  Map<String, Object?> toJson() {
    return <String, Object?>{
      if (id != null) 'id': id,
      'job_key': jobKey,
      'status': status,
      if (notes != null) 'notes': notes,
      if (appliedAt != null) 'applied_at': appliedAt,
      if (updatedAt != null) 'updated_at': updatedAt,
    };
  }

  @override
  String toString() => 'AtlasSavedJobPayload(<redacted>)';
}

final class AtlasApplicationNotePayload extends AtlasVaultPayload {
  AtlasApplicationNotePayload._({
    required this.title,
    required this.body,
    required this.noteKind,
    required this.linkedJobKey,
    required this.linkedSavedJobRecordId,
    required this.createdAt,
    required this.updatedAt,
    required this.isPinned,
    required this.sortOrder,
  });

  final String? title;
  final String body;
  final String noteKind;
  final String? linkedJobKey;
  final String? linkedSavedJobRecordId;
  final String createdAt;
  final String updatedAt;
  final bool? isPinned;
  final int? sortOrder;

  factory AtlasApplicationNotePayload.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'body',
        'note_kind',
        'created_at',
        'updated_at',
      },
      optionalKeys: const <String>{
        'title',
        'linked_job_key',
        'linked_saved_job_record_id',
        'is_pinned',
        'sort_order',
      },
      context: 'Application-note payload',
    );
    return AtlasApplicationNotePayload._(
      title: requireAtlasVaultOptionalString(
        value,
        key: 'title',
        field: 'payload.title',
      ),
      body: requireAtlasVaultString(value['body'], field: 'payload.body'),
      noteKind: requireAtlasVaultString(
        value['note_kind'],
        field: 'payload.note_kind',
      ),
      linkedJobKey: requireAtlasVaultOptionalString(
        value,
        key: 'linked_job_key',
        field: 'payload.linked_job_key',
      ),
      linkedSavedJobRecordId: requireAtlasVaultOptionalString(
        value,
        key: 'linked_saved_job_record_id',
        field: 'payload.linked_saved_job_record_id',
      ),
      createdAt: requireAtlasVaultUtcSeconds(
        value['created_at'],
        field: 'payload.created_at',
      ),
      updatedAt: requireAtlasVaultUtcSeconds(
        value['updated_at'],
        field: 'payload.updated_at',
      ),
      isPinned: value.containsKey('is_pinned')
          ? requireAtlasVaultBool(
              value['is_pinned'],
              field: 'payload.is_pinned',
            )
          : null,
      sortOrder: value.containsKey('sort_order')
          ? requireAtlasVaultInt(
              value['sort_order'],
              field: 'payload.sort_order',
            )
          : null,
    );
  }

  @override
  Map<String, Object?> toJson() {
    return <String, Object?>{
      if (title != null) 'title': title,
      'body': body,
      'note_kind': noteKind,
      if (linkedJobKey != null) 'linked_job_key': linkedJobKey,
      if (linkedSavedJobRecordId != null)
        'linked_saved_job_record_id': linkedSavedJobRecordId,
      'created_at': createdAt,
      'updated_at': updatedAt,
      if (isPinned != null) 'is_pinned': isPinned,
      if (sortOrder != null) 'sort_order': sortOrder,
    };
  }

  @override
  String toString() => 'AtlasApplicationNotePayload(<redacted>)';
}

final class AtlasProfileSnippetPayload extends AtlasVaultPayload {
  AtlasProfileSnippetPayload._({
    required this.title,
    required this.body,
    required this.targetSystem,
    required this.fieldHint,
    required this.tags,
    required this.provenanceNotes,
    required this.createdAt,
    required this.updatedAt,
  });

  final String title;
  final String body;
  final String? targetSystem;
  final String? fieldHint;
  final List<String> tags;
  final String? provenanceNotes;
  final String createdAt;
  final String updatedAt;

  factory AtlasProfileSnippetPayload.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'title',
        'body',
        'tags',
        'created_at',
        'updated_at',
      },
      optionalKeys: const <String>{
        'target_system',
        'field_hint',
        'provenance_notes',
      },
      context: 'Profile-snippet payload',
    );
    return AtlasProfileSnippetPayload._(
      title: requireAtlasVaultString(value['title'], field: 'payload.title'),
      body: requireAtlasVaultString(value['body'], field: 'payload.body'),
      targetSystem: requireAtlasVaultOptionalString(
        value,
        key: 'target_system',
        field: 'payload.target_system',
      ),
      fieldHint: requireAtlasVaultOptionalString(
        value,
        key: 'field_hint',
        field: 'payload.field_hint',
      ),
      tags: requireAtlasVaultStringList(value['tags'], field: 'payload.tags'),
      provenanceNotes: requireAtlasVaultOptionalString(
        value,
        key: 'provenance_notes',
        field: 'payload.provenance_notes',
      ),
      createdAt: requireAtlasVaultUtcSeconds(
        value['created_at'],
        field: 'payload.created_at',
      ),
      updatedAt: requireAtlasVaultUtcSeconds(
        value['updated_at'],
        field: 'payload.updated_at',
      ),
    );
  }

  @override
  Map<String, Object?> toJson() {
    return <String, Object?>{
      'title': title,
      'body': body,
      if (targetSystem != null) 'target_system': targetSystem,
      if (fieldHint != null) 'field_hint': fieldHint,
      'tags': List<String>.from(tags),
      if (provenanceNotes != null) 'provenance_notes': provenanceNotes,
      'created_at': createdAt,
      'updated_at': updatedAt,
    };
  }

  @override
  String toString() => 'AtlasProfileSnippetPayload(<redacted>)';
}

final class AtlasDraftMetadataPayload extends AtlasVaultPayload {
  AtlasDraftMetadataPayload._({
    required this.linkedJobKey,
    required this.linkedSavedJobRecordId,
    required this.targetSystem,
    required this.documentType,
    required this.generatedDocumentReference,
    required this.draftStatus,
    required this.generatedAt,
    required this.reviewedAt,
    required this.submittedAt,
    required this.archivedAt,
    required this.personalContextReference,
    required this.contextSummary,
  });

  final String? linkedJobKey;
  final String? linkedSavedJobRecordId;
  final String targetSystem;
  final String documentType;
  final String generatedDocumentReference;
  final String draftStatus;
  final String generatedAt;
  final String? reviewedAt;
  final String? submittedAt;
  final String? archivedAt;
  final String? personalContextReference;
  final String? contextSummary;

  factory AtlasDraftMetadataPayload.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'target_system',
        'document_type',
        'generated_document_reference',
        'draft_status',
        'generated_at',
      },
      optionalKeys: const <String>{
        'linked_job_key',
        'linked_saved_job_record_id',
        'reviewed_at',
        'submitted_at',
        'archived_at',
        'personal_context_reference',
        'context_summary',
      },
      context: 'Draft-metadata payload',
    );
    return AtlasDraftMetadataPayload._(
      linkedJobKey: requireAtlasVaultOptionalString(
        value,
        key: 'linked_job_key',
        field: 'payload.linked_job_key',
      ),
      linkedSavedJobRecordId: requireAtlasVaultOptionalString(
        value,
        key: 'linked_saved_job_record_id',
        field: 'payload.linked_saved_job_record_id',
      ),
      targetSystem: requireAtlasVaultString(
        value['target_system'],
        field: 'payload.target_system',
      ),
      documentType: requireAtlasVaultString(
        value['document_type'],
        field: 'payload.document_type',
      ),
      generatedDocumentReference: requireAtlasVaultString(
        value['generated_document_reference'],
        field: 'payload.generated_document_reference',
      ),
      draftStatus: requireAtlasVaultString(
        value['draft_status'],
        field: 'payload.draft_status',
      ),
      generatedAt: requireAtlasVaultUtcSeconds(
        value['generated_at'],
        field: 'payload.generated_at',
      ),
      reviewedAt: requireAtlasVaultOptionalUtcSeconds(
        value,
        key: 'reviewed_at',
        field: 'payload.reviewed_at',
      ),
      submittedAt: requireAtlasVaultOptionalUtcSeconds(
        value,
        key: 'submitted_at',
        field: 'payload.submitted_at',
      ),
      archivedAt: requireAtlasVaultOptionalUtcSeconds(
        value,
        key: 'archived_at',
        field: 'payload.archived_at',
      ),
      personalContextReference: requireAtlasVaultOptionalString(
        value,
        key: 'personal_context_reference',
        field: 'payload.personal_context_reference',
      ),
      contextSummary: requireAtlasVaultOptionalString(
        value,
        key: 'context_summary',
        field: 'payload.context_summary',
      ),
    );
  }

  @override
  Map<String, Object?> toJson() {
    return <String, Object?>{
      if (linkedJobKey != null) 'linked_job_key': linkedJobKey,
      if (linkedSavedJobRecordId != null)
        'linked_saved_job_record_id': linkedSavedJobRecordId,
      'target_system': targetSystem,
      'document_type': documentType,
      'generated_document_reference': generatedDocumentReference,
      'draft_status': draftStatus,
      'generated_at': generatedAt,
      if (reviewedAt != null) 'reviewed_at': reviewedAt,
      if (submittedAt != null) 'submitted_at': submittedAt,
      if (archivedAt != null) 'archived_at': archivedAt,
      if (personalContextReference != null)
        'personal_context_reference': personalContextReference,
      if (contextSummary != null) 'context_summary': contextSummary,
    };
  }

  @override
  String toString() => 'AtlasDraftMetadataPayload(<redacted>)';
}

final class AtlasVaultPayloadEnvelope {
  AtlasVaultPayloadEnvelope._({
    required this.type,
    required this.payloadSchema,
    required this.payload,
    required this.clientCreatedAt,
    required this.clientUpdatedAt,
  });

  static const supportedPayloadSchema = 1;

  final AtlasVaultPayloadType type;
  final int payloadSchema;
  final AtlasVaultPayload payload;
  final String clientCreatedAt;
  final String clientUpdatedAt;

  factory AtlasVaultPayloadEnvelope.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'type',
        'payload_schema',
        'payload',
        'client_created_at',
        'client_updated_at',
      },
      context: 'Plaintext record envelope',
    );
    final type = AtlasVaultPayloadType.parse(value['type']);
    final schema = requireAtlasVaultInt(
      value['payload_schema'],
      field: 'payload_schema',
    );
    if (schema != supportedPayloadSchema) {
      throw const AtlasVaultFormatException(
        'Plaintext payload schema is unsupported.',
      );
    }
    final payloadJson = requireAtlasVaultObject(
      value['payload'],
      context: 'Plaintext record payload',
    );
    final AtlasVaultPayload payload = switch (type) {
      AtlasVaultPayloadType.savedSearch => AtlasSavedSearchPayload.fromJson(
        payloadJson,
      ),
      AtlasVaultPayloadType.savedJob => AtlasSavedJobPayload.fromJson(
        payloadJson,
      ),
      AtlasVaultPayloadType.applicationNote =>
        AtlasApplicationNotePayload.fromJson(payloadJson),
      AtlasVaultPayloadType.profileSnippet =>
        AtlasProfileSnippetPayload.fromJson(payloadJson),
      AtlasVaultPayloadType.draftMetadata => AtlasDraftMetadataPayload.fromJson(
        payloadJson,
      ),
    };
    return AtlasVaultPayloadEnvelope._(
      type: type,
      payloadSchema: schema,
      payload: payload,
      clientCreatedAt: requireAtlasVaultUtcSeconds(
        value['client_created_at'],
        field: 'client_created_at',
      ),
      clientUpdatedAt: requireAtlasVaultUtcSeconds(
        value['client_updated_at'],
        field: 'client_updated_at',
      ),
    );
  }

  factory AtlasVaultPayloadEnvelope.decodeJson(String source) {
    return AtlasVaultPayloadEnvelope.fromJson(
      decodeAtlasVaultJsonObject(source, context: 'Plaintext record envelope'),
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'type': type.wireName,
      'payload_schema': payloadSchema,
      'payload': payload.toJson(),
      'client_created_at': clientCreatedAt,
      'client_updated_at': clientUpdatedAt,
    };
  }

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  @override
  bool operator ==(Object other) {
    return other is AtlasVaultPayloadEnvelope &&
        canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());
  }

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasVaultPayloadEnvelope(<redacted>)';
}
