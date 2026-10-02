import 'dart:async';

import 'package:atlas/atlas.dart' as atlas;
import 'package:atlas/features/app_shell/atlas_private_records_panel.dart';
import 'package:atlas/src/atlas_vault/payloads.dart';
import 'package:atlas/src/atlas_vault/private_state_runtime.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _timestamp = '2026-09-01T12:00:00Z';
const _families = <AtlasVaultPayloadType, String>{
  AtlasVaultPayloadType.savedSearch: 'Saved searches',
  AtlasVaultPayloadType.savedJob: 'Saved jobs',
  AtlasVaultPayloadType.applicationNote: 'Application notes',
  AtlasVaultPayloadType.profileSnippet: 'Profile snippets',
  AtlasVaultPayloadType.draftMetadata: 'Draft metadata',
};

void main() {
  testWidgets(
    'failed update stays generic when the parent clears its snapshot',
    (tester) async {
      final harness =
          _Harness(records: [_record(AtlasVaultPayloadType.savedSearch)])
            ..fail = true
            ..clearOnFailure = true;
      await _pump(tester, harness);
      await _tap(tester, find.byTooltip('Edit record'));
      await _tap(tester, find.byKey(const ValueKey('private-record-save')));
      expect(
        find.text('Unable to complete this change. Try again.'),
        findsOneWidget,
      );
      expect(find.text('No records'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('failure is visible even if the runtime remains fenced', (
    tester,
  ) async {
    final harness = _Harness()..pending = Completer<void>();
    await _pump(tester, harness);
    await _tap(tester, find.byTooltip('Create record'));
    await _fillRequired(tester, AtlasVaultPayloadType.savedSearch);
    final finder = find.byKey(const ValueKey('private-record-save'));
    await tester.ensureVisible(finder);
    await tester.tap(finder);
    await tester.pump();
    harness.enabled = false;
    harness.refresh();
    await tester.pump();
    harness.pending!.completeError(StateError('synthetic-error-detail'));
    await tester.pumpAndSettle();
    expect(
      find.text('Unable to complete this change. Try again.'),
      findsOneWidget,
    );
    expect(find.textContaining('synthetic-error-detail'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  for (final type in AtlasVaultPayloadType.values) {
    testWidgets('${type.name}: optional schema fields are editable', (
      tester,
    ) async {
      final harness = _Harness(records: [_record(type)]);
      await _pump(tester, harness);
      await _family(tester, type);
      await _tap(tester, find.byTooltip('Edit record'));
      final edits = switch (type) {
        AtlasVaultPayloadType.savedSearch => {
          'Description': ('description', 'Revised description'),
        },
        AtlasVaultPayloadType.savedJob => {
          'Job record reference': ('id', 'revised-reference'),
          'Notes': ('notes', 'Revised notes'),
          'Applied at (UTC)': ('applied_at', '2026-09-02T13:14:15Z'),
        },
        AtlasVaultPayloadType.applicationNote => {
          'Title': ('title', 'Revised title'),
          'Linked job key': ('linked_job_key', 'example:2'),
          'Linked saved job record': (
            'linked_saved_job_record_id',
            'revised-linked-record',
          ),
        },
        AtlasVaultPayloadType.profileSnippet => {
          'Target system': ('target_system', 'Another custom system'),
          'Field hint': ('field_hint', 'Revised field hint'),
          'Provenance notes': ('provenance_notes', 'Revised provenance'),
        },
        AtlasVaultPayloadType.draftMetadata => {
          'Linked job key': ('linked_job_key', 'example:2'),
          'Linked saved job record': (
            'linked_saved_job_record_id',
            'revised-linked-record',
          ),
          'Reviewed at (UTC)': ('reviewed_at', '2026-09-02T13:14:15Z'),
          'Submitted at (UTC)': ('submitted_at', '2026-09-03T13:14:15Z'),
          'Archived at (UTC)': ('archived_at', '2026-09-04T13:14:15Z'),
          'Personal context reference': (
            'personal_context_reference',
            'example/revised-context',
          ),
          'Context summary': ('context_summary', 'Revised context'),
        },
      };
      for (final edit in edits.entries) {
        await _enter(tester, edit.key, edit.value.$2);
      }
      await _tap(tester, find.byKey(const ValueKey('private-record-save')));
      final payload = harness.updated.single.$2.payload.toJson();
      for (final edit in edits.values) {
        expect(payload[edit.$1], edit.$2);
      }
    });

    testWidgets('${type.name}: create, read, update, confirmed delete', (
      tester,
    ) async {
      final harness = _Harness();
      await _pump(tester, harness);
      await _family(tester, type);
      expect(find.text('No records'), findsOneWidget);
      await _tap(tester, find.byTooltip('Create record'));
      await _fillRequired(tester, type);
      await _tap(tester, find.byKey(const ValueKey('private-record-save')));
      expect(harness.created.length, 1);
      final envelope = harness.created.single;
      expect(envelope.type, type);
      expect(AtlasVaultPayloadEnvelope.fromJson(envelope.toJson()), envelope);
      final original = harness.records.single;

      await _tap(tester, find.byTooltip('Read record'));
      expect(
        tester.widget<TextField>(_field(_mainField(type))).readOnly,
        isTrue,
      );
      await _tap(tester, find.byTooltip('Edit record'));
      await _enter(tester, _mainField(type), 'Updated example');
      await _tap(tester, find.byKey(const ValueKey('private-record-save')));
      expect(harness.updated.length, 1);
      expect(identical(harness.updated.single.$1, original), isTrue);
      expect(
        harness.updated.single.$2.clientCreatedAt,
        envelope.clientCreatedAt,
      );
      expect(
        harness.records.single.envelope.payload.toJson()[_mainKey(type)],
        'Updated example',
      );

      await _tap(tester, find.byTooltip('Delete record'));
      expect(find.text('Delete this record?'), findsOneWidget);
      expect(harness.deleted, isEmpty);
      await _tap(tester, find.text('Cancel'));
      expect(harness.deleted, isEmpty);
      await _tap(tester, find.byTooltip('Delete record'));
      final deleting = harness.records.single;
      await _tap(
        tester,
        find.byKey(const ValueKey('private-record-confirm-delete')),
      );
      expect(identical(harness.deleted.single, deleting), isTrue);
      expect(find.text('No records'), findsOneWidget);
    });

    testWidgets('${type.name}: preserves optional fields on update', (
      tester,
    ) async {
      final original = _record(type);
      final harness = _Harness(records: [original]);
      await _pump(tester, harness);
      await _family(tester, type);
      await _tap(tester, find.byTooltip('Edit record'));
      await _enter(tester, _mainField(type), 'Edited example');
      await _tap(tester, find.byKey(const ValueKey('private-record-save')));
      final actual = harness.updated.single.$2.payload.toJson();
      final expected = original.envelope.payload.toJson()
        ..[_mainKey(type)] = 'Edited example';
      if (expected.containsKey('updated_at')) {
        expected['updated_at'] = actual['updated_at'];
      }
      expect(actual, expected);
      expect(harness.updated.single.$2.clientCreatedAt, _timestamp);
    });
  }

  testWidgets('saved search retains every request field while editing text', (
    tester,
  ) async {
    final original = _record(AtlasVaultPayloadType.savedSearch);
    final harness = _Harness(records: [original]);
    await _pump(tester, harness);
    await _tap(tester, find.byTooltip('Edit record'));
    await _enter(tester, 'Search text', 'Revised query');
    await _tap(tester, find.byKey(const ValueKey('private-record-save')));
    final payload =
        harness.updated.single.$2.payload as AtlasSavedSearchPayload;
    final expected =
        (original.envelope.payload as AtlasSavedSearchPayload).request.toJson()
          ..['text'] = 'Revised query';
    expect(payload.request.toJson(), expected);
    expect(payload.summary, 'Example summary');
  });

  testWidgets(
    'blank required fields block create; clearing optional fields omits them',
    (tester) async {
      final harness = _Harness();
      await _pump(tester, harness);
      await _tap(tester, find.byTooltip('Create record'));
      await _tap(tester, find.byKey(const ValueKey('private-record-save')));
      expect(harness.created, isEmpty);
      expect(find.text('Required.'), findsNWidgets(2));
      await _fillRequired(tester, AtlasVaultPayloadType.savedSearch);
      await _enter(tester, 'Description', 'Example description');
      await _tap(tester, find.byKey(const ValueKey('private-record-save')));
      final original =
          harness.created.single.payload as AtlasSavedSearchPayload;
      await _tap(tester, find.byTooltip('Edit record'));
      await _enter(tester, 'Description', '');
      await _enter(tester, 'Search text', '');
      await _tap(tester, find.byKey(const ValueKey('private-record-save')));
      final payload =
          harness.updated.single.$2.payload as AtlasSavedSearchPayload;
      expect(payload.description, isNull);
      expect(payload.request.text, isNull);
      expect(payload.createdAt, original.createdAt);
      expect(
        payload.request.toJson(),
        const atlas.AtlasSearchRequest().toJson(),
      );
    },
  );

  testWidgets(
    'record metadata stays out of the display and parent owns scrolling',
    (tester) async {
      final harness = _Harness(
        records: [_record(AtlasVaultPayloadType.savedSearch)],
      );
      await _pump(tester, harness);
      expect(find.byType(ListView), findsOneWidget);
      await _tap(tester, find.byTooltip('Read record'));
      for (final metadata in [
        'opaque-example-key',
        'example-revision',
        'payload_schema',
        'saved_search',
      ]) {
        expect(find.text(metadata), findsNothing);
      }
      expect(find.byType(ListView), findsOneWidget);
    },
  );

  testWidgets('validates timestamps and integer fields without echoing input', (
    tester,
  ) async {
    final harness = _Harness(
      records: [_record(AtlasVaultPayloadType.applicationNote)],
    );
    await _pump(tester, harness);
    await _family(tester, AtlasVaultPayloadType.applicationNote);
    await _tap(tester, find.byTooltip('Edit record'));
    await _enter(tester, 'Sort order', '1.5');
    await _tap(tester, find.byKey(const ValueKey('private-record-save')));
    expect(harness.updated, isEmpty);
    expect(find.text('Enter a whole number.'), findsOneWidget);
    await _enter(tester, 'Sort order', '-3');
    await _tap(tester, find.byKey(const ValueKey('private-record-save')));
    expect(
      (harness.updated.single.$2.payload as AtlasApplicationNotePayload)
          .sortOrder,
      -3,
    );

    await _family(tester, AtlasVaultPayloadType.draftMetadata);
    await _tap(tester, find.byTooltip('Create record'));
    await _fillRequired(tester, AtlasVaultPayloadType.draftMetadata);
    await _enter(tester, 'Generated at (UTC)', '2026-02-30T12:00:00Z');
    await _tap(tester, find.byKey(const ValueKey('private-record-save')));
    expect(harness.created, isEmpty);
    expect(find.text('Enter a valid UTC timestamp.'), findsOneWidget);
  });

  testWidgets('tags and pinned use schema-compatible controls', (tester) async {
    final harness = _Harness();
    await _pump(tester, harness);
    await _family(tester, AtlasVaultPayloadType.profileSnippet);
    await _tap(tester, find.byTooltip('Create record'));
    await _fillRequired(tester, AtlasVaultPayloadType.profileSnippet);
    await _tap(tester, find.byTooltip('Add tag'));
    await _enter(tester, 'Tag 1', 'First, intact');
    await _tap(tester, find.byTooltip('Add tag'));
    await _enter(tester, 'Tag 2', 'Second');
    await _tap(tester, find.byTooltip('Remove tag 2'));
    await _tap(tester, find.byKey(const ValueKey('private-record-save')));
    expect(
      (harness.created.single.payload as AtlasProfileSnippetPayload).tags,
      ['First, intact'],
    );

    await _family(tester, AtlasVaultPayloadType.applicationNote);
    await _tap(tester, find.byTooltip('Create record'));
    await _fillRequired(tester, AtlasVaultPayloadType.applicationNote);
    await _tap(tester, find.byType(CheckboxListTile));
    await _tap(tester, find.byKey(const ValueKey('private-record-save')));
    expect(
      (harness.created.last.payload as AtlasApplicationNotePayload).isPinned,
      isTrue,
    );
  });

  testWidgets('fence disables mutations for every family but permits reading', (
    tester,
  ) async {
    final harness = _Harness(
      records: [for (final type in _families.keys) _record(type)],
    )..enabled = false;
    await _pump(tester, harness);
    for (final type in _families.keys) {
      await _family(tester, type);
      for (final tooltip in ['Create record', 'Edit record', 'Delete record']) {
        expect(
          tester
              .widget<IconButton>(
                find.byWidgetPredicate(
                  (widget) => widget is IconButton && widget.tooltip == tooltip,
                ),
              )
              .onPressed,
          isNull,
        );
      }
      await _tap(tester, find.byTooltip('Read record'));
      expect(
        tester.widget<TextField>(_field(_mainField(type))).readOnly,
        isTrue,
      );
      await _tap(tester, find.byTooltip('Back to records'));
    }
    expect(harness.created, isEmpty);
    expect(harness.updated, isEmpty);
    expect(harness.deleted, isEmpty);
  });

  testWidgets(
    'fence clears an open draft and invalidates captured save action',
    (tester) async {
      final harness = _Harness();
      await _pump(tester, harness);
      await _tap(tester, find.byTooltip('Create record'));
      await _fillRequired(tester, AtlasVaultPayloadType.savedSearch);
      final controller = tester.widget<TextField>(_field('Name')).controller!;
      final save = tester
          .widget<FilledButton>(
            find.byKey(const ValueKey('private-record-save')),
          )
          .onPressed!;
      harness.enabled = false;
      harness.refresh();
      await tester.pumpAndSettle();
      save();
      await tester.pumpAndSettle();
      expect(harness.created, isEmpty);
      expect(controller.text, isEmpty);
      harness.enabled = true;
      harness.refresh();
      await tester.pumpAndSettle();
      await _tap(tester, find.byTooltip('Create record'));
      expect(
        tester.widget<TextField>(_field('Name')).controller!.text,
        isEmpty,
      );
    },
  );

  testWidgets('fence invalidates open delete confirmation and stale action', (
    tester,
  ) async {
    final harness = _Harness(
      records: [_record(AtlasVaultPayloadType.savedSearch)],
    );
    await _pump(tester, harness);
    await _tap(tester, find.byTooltip('Delete record'));
    final action = tester
        .widget<FilledButton>(
          find.byKey(const ValueKey('private-record-confirm-delete')),
        )
        .onPressed!;
    harness.enabled = false;
    harness.refresh();
    await tester.pumpAndSettle();
    action();
    await tester.pumpAndSettle();
    expect(harness.deleted, isEmpty);
    expect(find.text('Delete this record?'), findsNothing);
  });

  for (final operation in ['create', 'update', 'delete']) {
    testWidgets(
      '$operation remains fenced while an async callback is pending',
      (tester) async {
        final harness = _Harness(
          records: operation == 'create'
              ? []
              : [_record(AtlasVaultPayloadType.savedSearch)],
        )..pending = Completer<void>();
        await _pump(tester, harness);
        if (operation == 'create') {
          await _tap(tester, find.byTooltip('Create record'));
          await _fillRequired(tester, AtlasVaultPayloadType.savedSearch);
        } else {
          await _tap(
            tester,
            find.byTooltip(
              operation == 'update' ? 'Edit record' : 'Delete record',
            ),
          );
        }
        final finder = find.byKey(
          ValueKey(
            operation == 'delete'
                ? 'private-record-confirm-delete'
                : 'private-record-save',
          ),
        );
        await tester.ensureVisible(finder);
        final action = tester.widget<FilledButton>(finder).onPressed!;
        action();
        await tester.pump();
        expect(tester.widget<FilledButton>(finder).onPressed, isNull);
        expect(harness.calls, 1);
        harness.enabled = false;
        harness.refresh();
        await tester.pump();
        action();
        expect(harness.calls, 1);
        harness.pending!.complete();
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expect(
          tester
              .widget<IconButton>(
                find.byWidgetPredicate(
                  (widget) =>
                      widget is IconButton && widget.tooltip == 'Create record',
                ),
              )
              .onPressed,
          isNull,
        );
      },
    );

    testWidgets('$operation failure is generic and retryable', (tester) async {
      final harness = _Harness(
        records: operation == 'create'
            ? []
            : [_record(AtlasVaultPayloadType.savedSearch)],
      )..fail = true;
      await _pump(tester, harness);
      if (operation == 'create') {
        await _tap(tester, find.byTooltip('Create record'));
        await _fillRequired(tester, AtlasVaultPayloadType.savedSearch);
      } else {
        await _tap(
          tester,
          find.byTooltip(
            operation == 'update' ? 'Edit record' : 'Delete record',
          ),
        );
      }
      final action = find.byKey(
        ValueKey(
          operation == 'delete'
              ? 'private-record-confirm-delete'
              : 'private-record-save',
        ),
      );
      await _tap(tester, action);
      expect(
        find.text('Unable to complete this change. Try again.'),
        findsOneWidget,
      );
      expect(find.textContaining('synthetic-error-detail'), findsNothing);
      expect(tester.takeException(), isNull);
      harness.fail = false;
      await _tap(tester, action);
      expect(
        find.text('Unable to complete this change. Try again.'),
        findsNothing,
      );
    });
  }

  testWidgets('pending mutation blocks duplicates and tolerates disposal', (
    tester,
  ) async {
    final harness = _Harness()..pending = Completer<void>();
    await _pump(tester, harness);
    await _tap(tester, find.byTooltip('Create record'));
    await _fillRequired(tester, AtlasVaultPayloadType.savedSearch);
    final saveFinder = find.byKey(const ValueKey('private-record-save'));
    await tester.ensureVisible(saveFinder);
    final save = tester.widget<FilledButton>(saveFinder).onPressed!;
    save();
    save();
    await tester.pump();
    expect(harness.calls, 1);
    expect(tester.widget<FilledButton>(saveFinder).onPressed, isNull);
    final controller = tester.widget<TextField>(_field('Name')).controller!;
    await tester.pumpWidget(const SizedBox.shrink());
    expect(controller.text, isEmpty);
    harness.pending!.completeError(StateError('synthetic-error-detail'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('cancel, family switch and record replacement discard drafts', (
    tester,
  ) async {
    final harness = _Harness(
      records: [_record(AtlasVaultPayloadType.savedSearch)],
    );
    await _pump(tester, harness);
    await _tap(tester, find.byTooltip('Edit record'));
    await _enter(tester, 'Name', 'Unsaved example');
    final controller = tester.widget<TextField>(_field('Name')).controller!;
    await _tap(tester, find.text('Cancel'));
    expect(controller.text, isEmpty);
    await _tap(tester, find.byTooltip('Edit record'));
    expect(
      tester.widget<TextField>(_field('Name')).controller!.text,
      'Example search',
    );
    await _family(tester, AtlasVaultPayloadType.savedJob);
    expect(find.byType(TextFormField), findsNothing);
    await _family(tester, AtlasVaultPayloadType.savedSearch);
    await _tap(tester, find.byTooltip('Edit record'));
    harness.records = [
      _record(AtlasVaultPayloadType.savedSearch, revision: 'replacement'),
    ];
    harness.refresh();
    await tester.pumpAndSettle();
    expect(find.byType(TextFormField), findsNothing);
    expect(harness.updated, isEmpty);
  });

  testWidgets('compact phone layout handles long content and scaled text', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final harness = _Harness(
      records: [_record(AtlasVaultPayloadType.draftMetadata)],
    );
    await _pump(tester, harness, textScale: 1.6);
    await _family(tester, AtlasVaultPayloadType.draftMetadata);
    expect(tester.takeException(), isNull);
    await _tap(tester, find.byTooltip('Edit record'));
    await _enter(tester, 'Generated document reference', 'example/' * 80);
    await _tap(tester, find.byKey(const ValueKey('private-record-save')));
    expect(harness.updated.length, 1);
    expect(tester.takeException(), isNull);
  });
}

Finder _field(String label) => find.byWidgetPredicate(
  (widget) => widget is TextField && widget.decoration?.labelText == label,
);

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<void> _enter(WidgetTester tester, String label, String value) async {
  await tester.ensureVisible(_field(label));
  await tester.enterText(_field(label), value);
  await tester.pumpAndSettle();
}

Future<void> _family(WidgetTester tester, AtlasVaultPayloadType type) async {
  await _tap(tester, find.byKey(const ValueKey('private-record-family')));
  await _tap(tester, find.text(_families[type]!).last);
}

String _mainField(AtlasVaultPayloadType type) => switch (type) {
  AtlasVaultPayloadType.savedSearch => 'Name',
  AtlasVaultPayloadType.savedJob => 'Job key',
  AtlasVaultPayloadType.applicationNote => 'Body',
  AtlasVaultPayloadType.profileSnippet => 'Title',
  AtlasVaultPayloadType.draftMetadata => 'Document type',
};

String _mainKey(AtlasVaultPayloadType type) => switch (type) {
  AtlasVaultPayloadType.savedSearch => 'name',
  AtlasVaultPayloadType.savedJob => 'job_key',
  AtlasVaultPayloadType.applicationNote => 'body',
  AtlasVaultPayloadType.profileSnippet => 'title',
  AtlasVaultPayloadType.draftMetadata => 'document_type',
};

Future<void> _fillRequired(
  WidgetTester tester,
  AtlasVaultPayloadType type,
) async {
  final fields = switch (type) {
    AtlasVaultPayloadType.savedSearch => {
      'Name': 'Example search',
      'Summary': 'Example summary',
      'Search text': 'Example query',
    },
    AtlasVaultPayloadType.savedJob => {
      'Job key': 'example:1',
      'Status': 'Custom status',
    },
    AtlasVaultPayloadType.applicationNote => {
      'Body': 'Example body',
      'Note kind': 'Custom kind',
    },
    AtlasVaultPayloadType.profileSnippet => {
      'Title': 'Example snippet',
      'Body': 'Example body',
    },
    AtlasVaultPayloadType.draftMetadata => {
      'Target system': 'Custom system',
      'Document type': 'Custom document',
      'Generated document reference': 'example/document',
      'Draft status': 'Custom status',
    },
  };
  for (final entry in fields.entries) {
    await _enter(tester, entry.key, entry.value);
  }
}

Future<void> _pump(
  WidgetTester tester,
  _Harness harness, {
  double textScale = 1,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: MediaQuery(
          data: MediaQueryData(textScaler: TextScaler.linear(textScale)),
          child: ListView(
            children: [
              AnimatedBuilder(
                animation: harness,
                builder: (context, _) => AtlasPrivateRecordsPanel(
                  records: harness.records,
                  enabled: harness.enabled,
                  onCreate: harness.create,
                  onUpdate: harness.update,
                  onDelete: harness.delete,
                ),
              ),
            ],
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

AtlasVaultPrivateRecord _record(
  AtlasVaultPayloadType type, {
  String revision = 'example-revision',
}) {
  final payload = switch (type) {
    AtlasVaultPayloadType.savedSearch => <String, Object?>{
      'name': 'Example search',
      'summary': 'Example summary',
      'description': 'Example description',
      'request': const atlas.AtlasSearchRequest(
        text: 'Example query',
        status: ['custom'],
        organizations: ['Example organization'],
        sourceIDs: ['example'],
        cities: ['Example city'],
        countriesISO3: ['KEN'],
        nationalInternational: ['custom'],
        gradeCodes: ['P-3'],
        ccogFamilies: ['example'],
        capabilityTags: ['example'],
        contractGroups: ['example'],
        seniorityGroups: ['example'],
        workModalities: ['example'],
        volunteerKinds: ['example'],
        unvCategories: ['example'],
        unvVolunteerTypes: ['example'],
        closingDateTo: '2026-12-01',
        includeLowConfidence: true,
        includeFacets: false,
        limit: 17,
        offset: 9,
        sort: 'custom',
      ).toJson(),
      'created_at': _timestamp,
      'updated_at': _timestamp,
    },
    AtlasVaultPayloadType.savedJob => <String, Object?>{
      'id': 'example-id',
      'job_key': 'example:1',
      'status': 'Custom status',
      'notes': 'Example notes',
      'applied_at': _timestamp,
      'updated_at': _timestamp,
    },
    AtlasVaultPayloadType.applicationNote => <String, Object?>{
      'title': 'Example note',
      'body': 'Example body',
      'note_kind': 'Custom kind',
      'linked_job_key': 'example:1',
      'linked_saved_job_record_id': 'example-linked-record',
      'created_at': _timestamp,
      'updated_at': _timestamp,
      'is_pinned': true,
      'sort_order': -2,
    },
    AtlasVaultPayloadType.profileSnippet => <String, Object?>{
      'title': 'Example snippet',
      'body': 'Example body',
      'target_system': 'Custom system',
      'field_hint': 'Example field',
      'tags': ['First, intact', 'Second\nintact', ''],
      'provenance_notes': 'Example provenance',
      'created_at': _timestamp,
      'updated_at': _timestamp,
    },
    AtlasVaultPayloadType.draftMetadata => <String, Object?>{
      'linked_job_key': 'example:1',
      'linked_saved_job_record_id': 'example-linked-record',
      'target_system': 'Custom system',
      'document_type': 'Custom document',
      'generated_document_reference': 'example/document',
      'draft_status': 'Custom status',
      'generated_at': _timestamp,
      'reviewed_at': _timestamp,
      'submitted_at': _timestamp,
      'archived_at': _timestamp,
      'personal_context_reference': 'example/context',
      'context_summary': 'Example context',
    },
  };
  return AtlasVaultPrivateRecord(
    recordId: 'example-${type.name}',
    revision: revision,
    parentRevision: null,
    keyId: 'opaque-example-key',
    envelope: AtlasVaultPayloadEnvelope.fromJson({
      'type': type.wireName,
      'payload_schema': 1,
      'payload': payload,
      'client_created_at': _timestamp,
      'client_updated_at': _timestamp,
    }),
  );
}

class _Harness extends ChangeNotifier {
  _Harness({this.records = const []});
  List<AtlasVaultPrivateRecord> records;
  bool enabled = true;
  bool fail = false;
  bool clearOnFailure = false;
  int calls = 0;
  Completer<void>? pending;
  final created = <AtlasVaultPayloadEnvelope>[];
  final updated = <(AtlasVaultPrivateRecord, AtlasVaultPayloadEnvelope)>[];
  final deleted = <AtlasVaultPrivateRecord>[];

  void refresh() => notifyListeners();

  Future<void> _before() async {
    calls++;
    if (pending != null) await pending!.future;
    if (fail) {
      if (clearOnFailure) {
        records = [];
        refresh();
      }
      throw StateError('synthetic-error-detail');
    }
  }

  Future<void> create(AtlasVaultPayloadEnvelope envelope) async {
    await _before();
    created.add(envelope);
    records = [
      ...records,
      AtlasVaultPrivateRecord(
        recordId: 'created',
        revision: 'created',
        parentRevision: null,
        keyId: 'opaque-example-key',
        envelope: envelope,
      ),
    ];
    refresh();
  }

  Future<void> update(
    AtlasVaultPrivateRecord record,
    AtlasVaultPayloadEnvelope envelope,
  ) async {
    await _before();
    updated.add((record, envelope));
    records = [
      for (final value in records)
        if (identical(value, record))
          AtlasVaultPrivateRecord(
            recordId: record.recordId,
            revision: 'updated',
            parentRevision: record.revision,
            keyId: record.keyId,
            envelope: envelope,
          )
        else
          value,
    ];
    refresh();
  }

  Future<void> delete(AtlasVaultPrivateRecord record) async {
    await _before();
    deleted.add(record);
    records = records.where((value) => !identical(value, record)).toList();
    refresh();
  }
}
