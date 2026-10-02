import 'package:flutter/material.dart';

import '../../atlas.dart' as atlas;
import '../../src/atlas_vault/payloads.dart';
import '../../src/atlas_vault/private_state_runtime.dart';
import '../../src/atlas_vault/strict_values.dart';

/// In-memory private-record UI. The owner supplies committed records and passes
/// `enabled: false` whenever the runtime mutation fence is active.
/// Embed in the owner's scroll view; this panel does not create a viewport.
class AtlasPrivateRecordsPanel extends StatefulWidget {
  const AtlasPrivateRecordsPanel({
    required this.records,
    required this.enabled,
    required this.onCreate,
    required this.onUpdate,
    required this.onDelete,
    super.key,
  });

  final List<AtlasVaultPrivateRecord> records;
  final bool enabled;
  final Future<void> Function(AtlasVaultPayloadEnvelope envelope) onCreate;
  final Future<void> Function(
    AtlasVaultPrivateRecord record,
    AtlasVaultPayloadEnvelope envelope,
  )
  onUpdate;
  final Future<void> Function(AtlasVaultPrivateRecord record) onDelete;

  @override
  State<AtlasPrivateRecordsPanel> createState() =>
      _AtlasPrivateRecordsPanelState();
}

enum _RecordView { list, read, create, edit, delete }

class _AtlasPrivateRecordsPanelState extends State<AtlasPrivateRecordsPanel> {
  AtlasVaultPayloadType _family = AtlasVaultPayloadType.savedSearch;
  _RecordView _view = _RecordView.list;
  AtlasVaultPrivateRecord? _record;
  int _editorVersion = 0;
  bool _busy = false;
  String? _error;

  bool get _canMutate => mounted && widget.enabled && !_busy;

  bool _isCurrent(AtlasVaultPrivateRecord record) => widget.records.any(
    (value) =>
        value.recordId == record.recordId &&
        value.revision == record.revision &&
        value.keyId == record.keyId &&
        value.envelope == record.envelope,
  );

  @override
  void didUpdateWidget(covariant AtlasPrivateRecordsPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if ((!widget.enabled && oldWidget.enabled && _view != _RecordView.read) ||
        (_record != null && !_isCurrent(_record!))) {
      // Parent failure handling may clear records in the same frame. Drop the
      // draft without erasing the non-secret failure notice.
      final error = _error;
      _reset();
      _error = error;
    }
  }

  void _reset() {
    _view = _RecordView.list;
    _record = null;
    _error = null;
    _editorVersion++;
  }

  void _open(_RecordView view, [AtlasVaultPrivateRecord? record]) {
    if (!mounted || _busy) return;
    if (view != _RecordView.read && !_canMutate) return;
    if (record != null && !_isCurrent(record)) return;
    setState(() {
      _view = view;
      _record = record;
      _error = null;
      _editorVersion++;
    });
  }

  Future<void> _save(AtlasVaultPayloadEnvelope envelope) async {
    if (!_canMutate || envelope.type != _family) return;
    final record = _record;
    if (_view == _RecordView.create) {
      await _mutate(() => widget.onCreate(envelope));
    } else if (_view == _RecordView.edit &&
        record != null &&
        _isCurrent(record)) {
      await _mutate(() => widget.onUpdate(record, envelope));
    }
  }

  Future<void> _delete(AtlasVaultPrivateRecord record) async {
    if (!_canMutate ||
        _view != _RecordView.delete ||
        !identical(record, _record) ||
        !_isCurrent(record)) {
      return;
    }
    await _mutate(() => widget.onDelete(record));
  }

  Future<void> _mutate(Future<void> Function() operation) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await operation();
      if (mounted) setState(_reset);
    } catch (_) {
      if (mounted) {
        setState(() => _error = 'Unable to complete this change. Try again.');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _record = null;
    _error = null;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final records = widget.records
        .where((record) => record.envelope.type == _family)
        .toList();
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'Private records',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: DropdownButton<AtlasVaultPayloadType>(
                  key: const ValueKey('private-record-family'),
                  value: _family,
                  isExpanded: true,
                  items: [
                    for (final type in AtlasVaultPayloadType.values)
                      DropdownMenuItem(
                        value: type,
                        child: Text(_familyLabel(type), maxLines: 2),
                      ),
                  ],
                  onChanged: _busy
                      ? null
                      : (value) {
                          if (value == null || value == _family) return;
                          setState(() {
                            _reset();
                            _family = value;
                          });
                        },
                ),
              ),
              if (_view == _RecordView.list)
                IconButton(
                  tooltip: 'Create record',
                  onPressed: _canMutate
                      ? () => _open(_RecordView.create)
                      : null,
                  icon: const Icon(Icons.add),
                ),
            ],
          ),
          if (!widget.enabled)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 8),
              child: Text('Read only'),
            ),
          if (_busy) const LinearProgressIndicator(),
          if (_error != null &&
              (_view == _RecordView.list || _view == _RecordView.delete))
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text(
                _error!,
                semanticsLabel: _error,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          if (_view == _RecordView.list) ...[
            if (records.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 24),
                child: Text('No records'),
              ),
            for (final record in records) ...[
              ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: Text(
                  _recordTitle(record.envelope.payload),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(
                  _recordSubtitle(record.envelope.payload),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                onTap: _busy ? null : () => _open(_RecordView.read, record),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      tooltip: 'Read record',
                      icon: const Icon(Icons.visibility_outlined),
                      onPressed: _busy
                          ? null
                          : () => _open(_RecordView.read, record),
                    ),
                    IconButton(
                      tooltip: 'Edit record',
                      icon: const Icon(Icons.edit_outlined),
                      onPressed: _canMutate
                          ? () => _open(_RecordView.edit, record)
                          : null,
                    ),
                    IconButton(
                      tooltip: 'Delete record',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: _canMutate
                          ? () => _open(_RecordView.delete, record)
                          : null,
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
            ],
          ] else ...[
            Row(
              children: [
                IconButton(
                  tooltip: 'Back to records',
                  icon: const Icon(Icons.arrow_back),
                  onPressed: _busy ? null : () => setState(_reset),
                ),
                Expanded(
                  child: Text(switch (_view) {
                    _RecordView.create => 'New record',
                    _RecordView.edit => 'Edit record',
                    _RecordView.delete => 'Delete record',
                    _ => 'Record details',
                  }, style: Theme.of(context).textTheme.titleMedium),
                ),
                if (_view == _RecordView.read)
                  IconButton(
                    tooltip: 'Edit record',
                    icon: const Icon(Icons.edit_outlined),
                    onPressed: _canMutate
                        ? () => _open(_RecordView.edit, _record)
                        : null,
                  ),
              ],
            ),
            if (_view == _RecordView.delete) ...[
              const SizedBox(height: 12),
              Text(
                'Delete this record?',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 8),
              Text(_recordTitle(_record!.envelope.payload)),
              const SizedBox(height: 16),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  TextButton(
                    onPressed: _busy ? null : () => setState(_reset),
                    child: const Text('Cancel'),
                  ),
                  Builder(
                    builder: (context) {
                      final record = _record!;
                      return FilledButton.icon(
                        key: const ValueKey('private-record-confirm-delete'),
                        onPressed: _canMutate ? () => _delete(record) : null,
                        icon: const Icon(Icons.delete_outline),
                        label: const Text('Delete'),
                      );
                    },
                  ),
                ],
              ),
            ] else
              _RecordForm(
                key: ValueKey(_editorVersion),
                family: _family,
                original: _record?.envelope,
                readOnly: _view == _RecordView.read,
                enabled: _canMutate,
                busy: _busy,
                operationError: _error,
                onSave: _save,
                onCancel: () => setState(_reset),
              ),
          ],
        ],
      ),
    );
  }
}

String _familyLabel(AtlasVaultPayloadType type) => switch (type) {
  AtlasVaultPayloadType.savedSearch => 'Saved searches',
  AtlasVaultPayloadType.savedJob => 'Saved jobs',
  AtlasVaultPayloadType.applicationNote => 'Application notes',
  AtlasVaultPayloadType.profileSnippet => 'Profile snippets',
  AtlasVaultPayloadType.draftMetadata => 'Draft metadata',
};

String _recordTitle(AtlasVaultPayload payload) {
  final title = switch (payload) {
    AtlasSavedSearchPayload() => payload.name,
    AtlasSavedJobPayload() => payload.jobKey,
    AtlasApplicationNotePayload() => payload.title ?? payload.body,
    AtlasProfileSnippetPayload() => payload.title,
    AtlasDraftMetadataPayload() => payload.documentType,
  };
  return title.trim().isEmpty ? 'Untitled record' : title;
}

String _recordSubtitle(AtlasVaultPayload payload) => switch (payload) {
  AtlasSavedSearchPayload() => payload.summary,
  AtlasSavedJobPayload() => payload.status,
  AtlasApplicationNotePayload() => payload.noteKind,
  AtlasProfileSnippetPayload() => payload.body,
  AtlasDraftMetadataPayload() => payload.draftStatus,
};

class _Field {
  const _Field(
    this.key,
    this.label, {
    this.required = false,
    this.multiline = false,
    this.timestamp = false,
    this.managed = false,
    this.integer = false,
  });
  final String key;
  final String label;
  final bool required;
  final bool multiline;
  final bool timestamp;
  final bool managed;
  final bool integer;
}

const _created = _Field(
  'created_at',
  'Created at (UTC)',
  timestamp: true,
  managed: true,
);
const _updated = _Field(
  'updated_at',
  'Updated at (UTC)',
  timestamp: true,
  managed: true,
);
const _linkedJob = _Field('linked_job_key', 'Linked job key');
const _linkedSavedJob = _Field(
  'linked_saved_job_record_id',
  'Linked saved job record',
);

List<_Field> _fields(AtlasVaultPayloadType type) => switch (type) {
  AtlasVaultPayloadType.savedSearch => const [
    _Field('name', 'Name', required: true),
    _Field('summary', 'Summary', required: true, multiline: true),
    _Field('description', 'Description', multiline: true),
    _Field('text', 'Search text', multiline: true),
    _created,
    _updated,
  ],
  AtlasVaultPayloadType.savedJob => const [
    _Field('job_key', 'Job key', required: true),
    _Field('status', 'Status', required: true),
    _Field('id', 'Job record reference'),
    _Field('notes', 'Notes', multiline: true),
    _Field('applied_at', 'Applied at (UTC)', timestamp: true),
    _updated,
  ],
  AtlasVaultPayloadType.applicationNote => const [
    _Field('title', 'Title'),
    _Field('body', 'Body', required: true, multiline: true),
    _Field('note_kind', 'Note kind', required: true),
    _linkedJob,
    _linkedSavedJob,
    _Field('sort_order', 'Sort order', integer: true),
    _created,
    _updated,
  ],
  AtlasVaultPayloadType.profileSnippet => const [
    _Field('title', 'Title', required: true),
    _Field('body', 'Body', required: true, multiline: true),
    _Field('target_system', 'Target system'),
    _Field('field_hint', 'Field hint'),
    _Field('provenance_notes', 'Provenance notes', multiline: true),
    _created,
    _updated,
  ],
  AtlasVaultPayloadType.draftMetadata => const [
    _Field('target_system', 'Target system', required: true),
    _Field('document_type', 'Document type', required: true),
    _Field(
      'generated_document_reference',
      'Generated document reference',
      required: true,
    ),
    _Field('draft_status', 'Draft status', required: true),
    _Field(
      'generated_at',
      'Generated at (UTC)',
      required: true,
      timestamp: true,
    ),
    _linkedJob,
    _linkedSavedJob,
    _Field('reviewed_at', 'Reviewed at (UTC)', timestamp: true),
    _Field('submitted_at', 'Submitted at (UTC)', timestamp: true),
    _Field('archived_at', 'Archived at (UTC)', timestamp: true),
    _Field('personal_context_reference', 'Personal context reference'),
    _Field('context_summary', 'Context summary', multiline: true),
  ],
};

class _RecordForm extends StatefulWidget {
  const _RecordForm({
    required this.family,
    required this.original,
    required this.readOnly,
    required this.enabled,
    required this.busy,
    required this.operationError,
    required this.onSave,
    required this.onCancel,
    super.key,
  });
  final AtlasVaultPayloadType family;
  final AtlasVaultPayloadEnvelope? original;
  final bool readOnly;
  final bool enabled;
  final bool busy;
  final String? operationError;
  final Future<void> Function(AtlasVaultPayloadEnvelope envelope) onSave;
  final VoidCallback onCancel;

  @override
  State<_RecordForm> createState() => _RecordFormState();
}

class _RecordFormState extends State<_RecordForm> {
  final _formKey = GlobalKey<FormState>();
  final _controllers = <String, TextEditingController>{};
  final _tags = <TextEditingController>[];
  late final List<_Field> _definitions;
  bool? _pinned;
  String? _error;

  @override
  void initState() {
    super.initState();
    _definitions = _fields(widget.family);
    final payload = widget.original?.payload.toJson() ?? <String, Object?>{};
    final now = _utcNow();
    for (final field in _definitions) {
      Object? value = payload[field.key];
      if (widget.family == AtlasVaultPayloadType.savedSearch &&
          field.key == 'text') {
        value = (widget.original?.payload as AtlasSavedSearchPayload?)
            ?.request
            .text;
      }
      if (widget.original == null &&
          (field.managed || field.key == 'generated_at')) {
        value ??= now;
      }
      _controllers[field.key] = TextEditingController(
        text: value?.toString() ?? '',
      );
    }
    final original = widget.original?.payload;
    if (original is AtlasProfileSnippetPayload) {
      for (final tag in original.tags) {
        _tags.add(TextEditingController(text: tag));
      }
    }
    _pinned = original is AtlasApplicationNotePayload
        ? original.isPinned
        : false;
  }

  @override
  void dispose() {
    // Remove draft references before releasing the controllers. Dart strings
    // cannot be securely zeroed; no draft is persisted or restored by this UI.
    for (final controller in [..._controllers.values, ..._tags]) {
      controller.clear();
      controller.dispose();
    }
    _controllers.clear();
    _tags.clear();
    _pinned = null;
    _error = null;
    super.dispose();
  }

  String? _validate(_Field field, String? value) {
    final text = value ?? '';
    if (text.isEmpty) return field.required ? 'Required.' : null;
    if (field.integer && int.tryParse(text) == null) {
      return 'Enter a whole number.';
    }
    try {
      if (field.timestamp) {
        requireAtlasVaultUtcSeconds(text, field: 'timestamp');
      } else {
        requireAtlasVaultString(text, field: 'text');
      }
    } catch (_) {
      return field.timestamp
          ? 'Enter a valid UTC timestamp.'
          : 'Enter valid text.';
    }
    return null;
  }

  void _save() {
    if (!mounted || !widget.enabled || widget.readOnly || widget.busy) return;
    final invalid = _formKey.currentState!.validateGranularly();
    if (invalid.isNotEmpty) {
      Scrollable.ensureVisible(
        invalid.first.context,
        duration: const Duration(milliseconds: 200),
      );
      return;
    }
    final AtlasVaultPayloadEnvelope envelope;
    try {
      final payload = widget.original?.payload.toJson() ?? <String, Object?>{};
      for (final field in _definitions) {
        if (widget.family == AtlasVaultPayloadType.savedSearch &&
            field.key == 'text') {
          continue;
        }
        final text = _controllers[field.key]!.text;
        if (text.isEmpty && !field.required) {
          // Preserve an existing empty string, but allow clearing populated
          // optional fields without producing nulls forbidden by the schema.
          if (payload[field.key] != '') payload.remove(field.key);
        } else {
          payload[field.key] = field.integer ? int.parse(text) : text;
        }
      }
      final now = _utcNow();
      if (_definitions.any((field) => field.key == 'updated_at')) {
        payload['updated_at'] = now;
      }
      switch (widget.family) {
        case AtlasVaultPayloadType.savedSearch:
          final previous = widget.original?.payload as AtlasSavedSearchPayload?;
          final request =
              previous?.request.toJson() ??
              const atlas.AtlasSearchRequest().toJson();
          final text = _controllers['text']!.text;
          if (text.isEmpty) {
            if (request['text'] != '') request.remove('text');
          } else {
            request['text'] = text;
          }
          payload['request'] = AtlasSearchRequest.fromJson(request).toJson();
        case AtlasVaultPayloadType.profileSnippet:
          payload['tags'] = [for (final tag in _tags) tag.text];
        case AtlasVaultPayloadType.applicationNote:
          if (_pinned == null) {
            payload.remove('is_pinned');
          } else {
            payload['is_pinned'] = _pinned;
          }
        case AtlasVaultPayloadType.savedJob:
        case AtlasVaultPayloadType.draftMetadata:
          break;
      }
      envelope = AtlasVaultPayloadEnvelope.fromJson({
        'type': widget.family.wireName,
        'payload_schema': AtlasVaultPayloadEnvelope.supportedPayloadSchema,
        'payload': payload,
        'client_created_at': widget.original?.clientCreatedAt ?? now,
        'client_updated_at': now,
      });
    } catch (_) {
      setState(
        () => _error = 'Unable to prepare this change. Check the fields.',
      );
      return;
    }
    setState(() => _error = null);
    widget.onSave(envelope);
  }

  @override
  Widget build(BuildContext context) {
    final editable = widget.enabled && !widget.readOnly && !widget.busy;
    return Form(
      key: _formKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final field in _definitions)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: TextFormField(
                controller: _controllers[field.key],
                decoration: InputDecoration(
                  labelText: field.label,
                  border: const OutlineInputBorder(),
                  isDense: true,
                  hintText: field.timestamp ? 'YYYY-MM-DDTHH:MM:SSZ' : null,
                ),
                readOnly: !editable || field.managed,
                enabled: !widget.busy,
                enableSuggestions: false,
                autocorrect: false,
                keyboardType: field.integer
                    ? const TextInputType.numberWithOptions(signed: true)
                    : field.multiline
                    ? TextInputType.multiline
                    : TextInputType.text,
                minLines: field.multiline ? 2 : 1,
                maxLines: field.multiline ? 5 : 1,
                validator: (value) => _validate(field, value),
              ),
            ),
          if (widget.family == AtlasVaultPayloadType.applicationNote)
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Pinned'),
              tristate: true,
              value: _pinned,
              onChanged: editable
                  ? (value) => setState(() => _pinned = value)
                  : null,
            ),
          if (widget.family == AtlasVaultPayloadType.profileSnippet) ...[
            Row(
              children: [
                const Expanded(child: Text('Tags')),
                if (!widget.readOnly)
                  IconButton(
                    tooltip: 'Add tag',
                    icon: const Icon(Icons.add),
                    onPressed: editable
                        ? () =>
                              setState(() => _tags.add(TextEditingController()))
                        : null,
                  ),
              ],
            ),
            for (var index = 0; index < _tags.length; index++)
              Padding(
                key: ObjectKey(_tags[index]),
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(
                  children: [
                    Expanded(
                      child: TextFormField(
                        controller: _tags[index],
                        readOnly: !editable,
                        enabled: !widget.busy,
                        enableSuggestions: false,
                        autocorrect: false,
                        minLines: 1,
                        maxLines: 3,
                        decoration: InputDecoration(
                          labelText: 'Tag ${index + 1}',
                          border: const OutlineInputBorder(),
                          isDense: true,
                        ),
                      ),
                    ),
                    if (!widget.readOnly)
                      IconButton(
                        tooltip: 'Remove tag ${index + 1}',
                        icon: const Icon(Icons.close),
                        onPressed: editable
                            ? () {
                                final controller = _tags[index];
                                setState(() => _tags.removeAt(index));
                                // Keep the controller alive until its TextField unmounts.
                                WidgetsBinding.instance.addPostFrameCallback((
                                  _,
                                ) {
                                  controller.clear();
                                  controller.dispose();
                                });
                              }
                            : null,
                      ),
                  ],
                ),
              ),
          ],
          const SizedBox(height: 12),
          if (widget.operationError != null || _error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Semantics(
                liveRegion: true,
                child: Text(
                  widget.operationError ?? _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            ),
          if (!widget.readOnly)
            Wrap(
              spacing: 8,
              runSpacing: 8,
              alignment: WrapAlignment.end,
              children: [
                TextButton(
                  onPressed: widget.busy ? null : widget.onCancel,
                  child: const Text('Cancel'),
                ),
                FilledButton.icon(
                  key: const ValueKey('private-record-save'),
                  onPressed: editable ? _save : null,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('Save'),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

String _utcNow() =>
    '${DateTime.now().toUtc().toIso8601String().split('.').first}Z';
