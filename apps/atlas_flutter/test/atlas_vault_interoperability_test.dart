import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas.dart' as app;
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_android_fakes.dart';
import 'support/atlas_vault_vector_loader.dart';

void main() {
  test('construction performs no dependency call', () {
    final keyStore = InteropMemorySecureKeyStore();
    final storeIO = InteropMemoryLocalStoreIO();
    final selected = InteropMemorySelectedVaultStore();
    final migration = InteropMemoryMigrationJournalStore();
    var importPendingCalls = 0;

    AtlasVaultInteroperabilityCoordinator(
      runtime: AtlasVaultPrivateStateRuntime(
        secureKeyStore: keyStore,
        localStoreIO: storeIO,
      ),
      selectedVaultStore: selected,
      migrationJournalStore: migration,
      recoveryImportPending: () async {
        importPendingCalls += 1;
        return false;
      },
      documentTransport: _RecordingDocumentTransport(),
    );

    expect(keyStore.calls, isEmpty);
    expect(storeIO.calls, isEmpty);
    expect(selected.calls, isEmpty);
    expect(migration.calls, isEmpty);
    expect(importPendingCalls, 0);
  });

  test('export requires active matching encrypted authority', () async {
    final fixture = await _Fixture.create(activate: false);

    var availability = await fixture.coordinator.inspectRecoveryExport();
    expect(availability.available, isFalse);
    expect(availability.encryptedRecordCount, 0);

    expect(
      await fixture.runtime.activateExisting(fixture.caseData.vaultId),
      AtlasVaultActivationResult.activated,
    );
    fixture.selected.value = 'different-vault';
    availability = await fixture.coordinator.inspectRecoveryExport();
    expect(availability.available, isFalse);
  });

  test('migration and import pending make export unavailable', () async {
    final migrationFixture = await _Fixture.create(
      migrationJournalBytes: Uint8List.fromList(utf8.encode('{}')),
    );
    expect(
      (await migrationFixture.coordinator.inspectRecoveryExport()).available,
      isFalse,
    );

    final importFixture = await _Fixture.create(importPending: true);
    expect(
      (await importFixture.coordinator.inspectRecoveryExport()).available,
      isFalse,
    );
  });

  test(
    'new recovery setup is one-shot and mutates nothing before confirmation',
    () async {
      final fixture = await _Fixture.create();
      final before = fixture.storeIO.current!;
      final handle = await fixture.coordinator.beginRecoverySetup();

      expect(handle.toString(), 'AtlasVaultRecoveryDisplayHandle(<redacted>)');
      expect(handle.take(), fixture.caseData.recoveryText);
      expect(handle.take(), isNull);
      handle.destroy();
      expect(
        fixture.storeIO.calls.where((value) => value == 'store.replace'),
        isEmpty,
      );
      expect(fixture.storeIO.current, before);
      expect(
        fixture.coordinator.toString(),
        'AtlasVaultInteroperabilityCoordinator(<redacted>)',
      );
      expect(
        fixture.coordinator.toString(),
        isNot(contains(fixture.caseData.recoveryText)),
      );
    },
  );

  test('wrong recovery confirmation creates no wrap', () async {
    final fixture = await _Fixture.create();
    await fixture.coordinator.beginRecoverySetup();

    final result = await fixture.coordinator.confirmRecoverySetup(
      'AVRK1-INVALID',
    );

    expect(result.disposition, AtlasVaultRecoveryExportDisposition.failed);
    expect(fixture.storeIO.current!.vaultMetadata.keyWraps, isEmpty);
    expect(
      fixture.storeIO.calls.where((value) => value == 'store.replace'),
      isEmpty,
    );
  });

  test('discard clears pending recovery setup without persistence', () async {
    final fixture = await _Fixture.create();
    final handle = await fixture.coordinator.beginRecoverySetup();
    final recoveryText = handle.take()!;

    fixture.coordinator.discardPendingRecovery();
    final result = await fixture.coordinator.confirmRecoverySetup(recoveryText);

    expect(result.disposition, AtlasVaultRecoveryExportDisposition.failed);
    expect(fixture.storeIO.current!.vaultMetadata.keyWraps, isEmpty);
    expect(
      fixture.storeIO.calls.where((value) => value == 'store.replace'),
      isEmpty,
    );
  });

  test(
    'confirmed setup preserves records and saves exact Flutter vector bytes',
    () async {
      final fixture = await _Fixture.create();
      final originalRecords = fixture.storeIO.current!.records
          .map((record) => record.toJson())
          .toList(growable: false);
      final handle = await fixture.coordinator.beginRecoverySetup();
      final recoveryText = handle.take()!;

      final prepared = await fixture.coordinator.confirmRecoverySetup(
        recoveryText,
      );

      expect(
        prepared.disposition,
        AtlasVaultRecoveryExportDisposition.exportReady,
      );
      expect(
        prepared.encryptedRecordCount,
        fixture.caseData.expectedRecordCount,
      );
      expect(prepared.recoveryWrapPresent, isTrue);
      expect(fixture.storeIO.lastExpectedSha256, isNotNull);
      final committed = fixture.storeIO.current!;
      expect(
        committed.vaultMetadata.keyWraps
            .whereType<vault.AtlasVaultRecoveryKeyWrapV2>(),
        hasLength(1),
      );
      expect(
        committed.records.map((record) => jsonEncode(record.toJson())),
        orderedEquals(originalRecords.map(jsonEncode)),
      );

      final saved = await fixture.coordinator.savePreparedExport();

      expect(saved.disposition, AtlasVaultRecoveryExportDisposition.saved);
      expect(
        fixture.transport.savedBytes,
        orderedEquals(fixture.caseData.canonicalExportBytes),
      );
      final encoded = utf8.decode(fixture.transport.savedBytes!);
      expect(encoded, isNot(contains('store_id')));
      expect(encoded, isNot(contains(fixture.caseData.recoveryText)));
      for (final sentinel in _privateSentinels) {
        expect(encoded, isNot(contains(sentinel)), reason: sentinel);
      }
      expect(
        await vault.atlasVaultSha256Hex(fixture.transport.savedBytes!),
        fixture.caseData.canonicalExportSha256,
      );
    },
  );

  test('existing recovery wrap requires the matching recovery key', () async {
    final fixture = await _Fixture.create(existingRecoveryWrap: true);

    final wrong = await fixture.coordinator.prepareExistingRecoveryExport(
      'AVRK1-INVALID',
    );
    expect(wrong.disposition, AtlasVaultRecoveryExportDisposition.failed);
    expect(fixture.transport.savedBytes, isNull);

    final prepared = await fixture.coordinator.prepareExistingRecoveryExport(
      fixture.caseData.recoveryText,
    );
    expect(
      prepared.disposition,
      AtlasVaultRecoveryExportDisposition.exportReady,
    );
    await fixture.coordinator.savePreparedExport();
    expect(
      fixture.transport.savedBytes,
      orderedEquals(fixture.caseData.canonicalExportBytes),
    );
    expect(
      fixture.storeIO.calls.where((value) => value == 'store.replace'),
      isEmpty,
    );
  });

  test('new recovery setup preserves an existing passphrase wrap', () async {
    final fixture = await _Fixture.create(existingPassphraseWrap: true);
    final handle = await fixture.coordinator.beginRecoverySetup();

    final result = await fixture.coordinator.confirmRecoverySetup(
      handle.take()!,
    );

    expect(result.disposition, AtlasVaultRecoveryExportDisposition.exportReady);
    final wraps = fixture.storeIO.current!.vaultMetadata.keyWraps;
    expect(
      wraps.whereType<vault.AtlasVaultPassphraseKeyWrapV1>(),
      hasLength(1),
    );
    expect(wraps.whereType<vault.AtlasVaultRecoveryKeyWrapV2>(), hasLength(1));
  });

  test('document cancellation discards prepared encrypted bytes', () async {
    final fixture = await _Fixture.create(
      existingRecoveryWrap: true,
      saveResult: false,
    );
    await fixture.coordinator.prepareExistingRecoveryExport(
      fixture.caseData.recoveryText,
    );

    final result = await fixture.coordinator.savePreparedExport();

    expect(result.disposition, AtlasVaultRecoveryExportDisposition.cancelled);
    await expectLater(
      fixture.coordinator.savePreparedExport(),
      throwsA(isA<AtlasVaultInteroperabilityException>()),
    );
  });

  test('strict metadata rejects duplicate recovery-wrap identifiers', () {
    final caseData = _InteropCase.flutterToIos();
    final export = caseData.export;
    final metadata = export.vaultMetadata.toJson();
    final wraps = List<Object?>.from(metadata['key_wraps']! as List<Object?>);
    wraps.add(Map<String, Object?>.from(wraps.single! as Map<String, Object?>));
    metadata['key_wraps'] = wraps;

    expect(
      () => vault.AtlasVaultMetadata.fromJson(metadata),
      throwsA(isA<vault.AtlasVaultFormatException>()),
    );
  });

  test(
    'Apple-origin import preparation is explicit and side-effect free',
    () async {
      final fixture = await _ImportFixture.create();

      final result = await fixture.coordinator.prepareRecoveryImport();

      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.importPrepared,
      );
      expect(result.encryptedRecordCount, fixture.caseData.expectedRecordCount);
      expect(fixture.transport.pickCalls, 1);
      expect(_persistentImportMutations(fixture.events), isEmpty);
      expect(fixture.importJournal.current, isNull);
    },
  );

  test('wrong import recovery key creates no persistent resource', () async {
    final fixture = await _ImportFixture.create();
    await fixture.coordinator.prepareRecoveryImport();

    final result = await fixture.coordinator.confirmRecoveryImport(
      'AVRK1-INVALID',
    );

    expect(result.disposition, AtlasVaultRecoveryImportDisposition.failed);
    expect(_persistentImportMutations(fixture.events), isEmpty);
    expect(fixture.storeIO.current, isNull);
    expect(
      await fixture.keyStore.containsVaultKey(fixture.caseData.vaultId),
      isFalse,
    );
    expect(await fixture.selected.read(), isNull);
    expect(fixture.importJournal.current, isNull);
  });

  test(
    'protected import journal contains only fixed secret-free metadata',
    () async {
      final fixture = await _ImportFixture.create(
        failAfterEvent: 'import-journal.create',
      );
      await fixture.coordinator.prepareRecoveryImport();

      final interrupted = await fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );

      expect(interrupted.pendingImport, isTrue);
      final journalBytes = fixture.importJournal.current!;
      final journalText = utf8.decode(journalBytes);
      final journal = jsonDecode(journalText)! as Map<String, Object?>;
      expect(journal.keys.toSet(), <String>{
        'format',
        'version',
        'import_id',
        'stage',
        'export_id',
        'vault_id',
        'store_id',
        'created_at',
        'export_sha256',
        'local_store_sha256',
        'vault_key_sha256',
      });
      expect(journalText, isNot(contains(fixture.caseData.recoveryText)));
      expect(
        journalText,
        isNot(contains(base64Encode(fixture.caseData.canonicalExportBytes))),
      );
      for (final sentinel in _privateSentinels) {
        expect(journalText, isNot(contains(sentinel)), reason: sentinel);
      }
      expect(journal, isNot(contains('recovery_key')));
      expect(journal, isNot(contains('vault_key')));
      expect(journal, isNot(contains('export_bytes')));
      expect(journal, isNot(contains('record_ids')));
      expect(journal, isNot(contains('path')));
      expect(journal, isNot(contains('uri')));
    },
  );

  test(
    'import commits store then key then selection and clears journal last',
    () async {
      final fixture = await _ImportFixture.create();
      await fixture.coordinator.prepareRecoveryImport();

      final result = await fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );

      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.importedAndActive,
      );
      expect(
        fixture.events,
        containsAllInOrder(<String>[
          'import-journal.create',
          'store.create',
          'import-journal.replace',
          'key.create',
          'import-journal.replace',
          'selection.create',
          'import-journal.replace',
          'import-journal.delete',
        ]),
      );
      expect(
        _persistentImportMutations(fixture.events).last,
        'import-journal.delete',
      );
      expect(fixture.events.last, 'import-journal.read');
      expect(fixture.runtime.isActive, isTrue);
      final snapshot = await fixture.runtime.read();
      expect(
        snapshot.savedSearches,
        hasLength(fixture.caseData.expectedSavedSearchCount),
      );
      expect(
        snapshot.trackerRecords,
        hasLength(fixture.caseData.expectedTrackerCount),
      );
      final installed = fixture.storeIO.current!;
      expect(installed.storeId, isNot(fixture.caseData.sourceStoreId));
      expect(
        installed.records.map((record) => jsonEncode(record.toJson())),
        orderedEquals(
          fixture.caseData.export.records.map(
            (record) => jsonEncode(record.toJson()),
          ),
        ),
      );
      expect(
        installed.records.where((record) => record.deleted),
        hasLength(fixture.caseData.expectedTombstoneCount),
      );
      final encoded = utf8.decode(installed.canonicalBytes());
      for (final sentinel in _privateSentinels) {
        expect(encoded, isNot(contains(sentinel)), reason: sentinel);
      }
    },
  );

  test('picker cancellation creates no persistent import state', () async {
    final fixture = await _ImportFixture.create(pickerCancelled: true);

    final result = await fixture.coordinator.prepareRecoveryImport();

    expect(result.disposition, AtlasVaultRecoveryImportDisposition.cancelled);
    expect(fixture.transport.pickCalls, 1);
    expect(_persistentImportMutations(fixture.events), isEmpty);
    expect(fixture.importJournal.current, isNull);
  });

  test('noncanonical recovery documents are rejected before import', () async {
    final canonical = _ImportCase.iosToFlutter().canonicalExportBytes;
    final canonicalText = utf8.decode(canonical);
    final decoded = jsonDecode(canonicalText)! as Map<String, dynamic>;
    final reordered = <String, dynamic>{
      for (final key in decoded.keys.toList().reversed) key: decoded[key],
    };
    final candidates = <Uint8List>[
      Uint8List.fromList(<int>[0x20, ...canonical]),
      Uint8List.fromList(utf8.encode(jsonEncode(reordered))),
      Uint8List.fromList(
        utf8.encode(
          '{"format":"atlasvault-export",${canonicalText.substring(1)}',
        ),
      ),
    ];

    for (final candidate in candidates) {
      final fixture = await _ImportFixture.create(pickedBytes: candidate);

      final result = await fixture.coordinator.prepareRecoveryImport();

      expect(result.disposition, AtlasVaultRecoveryImportDisposition.failed);
      expect(_persistentImportMutations(fixture.events), isEmpty);
      expect(fixture.importJournal.current, isNull);
    }
  });

  test('selected vault rejects import before opening the picker', () async {
    final fixture = await _ImportFixture.create(
      selectedVaultId: 'existing-vault',
    );

    final result = await fixture.coordinator.prepareRecoveryImport();

    expect(
      result.disposition,
      AtlasVaultRecoveryImportDisposition.existingVault,
    );
    expect(fixture.transport.pickCalls, 0);
    expect(_persistentImportMutations(fixture.events), isEmpty);
  });

  test('every plaintext authority blocks clean-install import', () async {
    final fixtures = <_ImportFixture>[
      await _ImportFixture.create(inMemoryState: _plaintextState()),
      await _ImportFixture.create(compatibilityState: _plaintextState()),
      await _ImportFixture.create(cacheState: _privateCacheState()),
    ];

    for (final fixture in fixtures) {
      final result = await fixture.coordinator.prepareRecoveryImport();
      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.migrationRequired,
      );
      expect(fixture.transport.pickCalls, 0);
      expect(_persistentImportMutations(fixture.events), isEmpty);
    }
  });

  test('unavailable compatibility authority fails closed', () async {
    final fixture = await _ImportFixture.create(compatibilityFails: true);

    final result = await fixture.coordinator.prepareRecoveryImport();

    expect(result.disposition, AtlasVaultRecoveryImportDisposition.unavailable);
    expect(fixture.transport.pickCalls, 0);
    expect(_persistentImportMutations(fixture.events), isEmpty);
  });

  test('journaled resume does not revisit compatibility endpoints', () async {
    final fixture = await _ImportFixture.create(failAfterEvent: 'store.create');
    await fixture.coordinator.prepareRecoveryImport();
    final interrupted = await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );
    expect(interrupted.pendingImport, isTrue);
    final compatibilityReads = fixture.compatibilitySource.readCalls;
    fixture.compatibilitySource.fails = true;
    fixture.faults.clear();

    final prepared = await fixture.coordinator.prepareRecoveryImport();
    final resumed = await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );

    expect(
      prepared.disposition,
      AtlasVaultRecoveryImportDisposition.importPrepared,
    );
    expect(prepared.pendingImport, isTrue);
    expect(
      resumed.disposition,
      AtlasVaultRecoveryImportDisposition.importedAndActive,
    );
    expect(fixture.compatibilitySource.readCalls, compatibilityReads);
  });

  test(
    'initial import holds legacy mutation admission through journaling',
    () async {
      final admission = _ImportOperationAdmission();
      final fixture = await _ImportFixture.create(
        importOperationAdmission: admission,
      );
      admission.attachEvents(fixture.events);
      await fixture.coordinator.prepareRecoveryImport();
      fixture.events.clear();

      final confirmation = fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );
      await admission.entered;

      expect(admission.blocksNewLegacyMutation, isTrue);
      expect(admission.tryAdmitLegacyMutation(), isFalse);
      expect(fixture.importJournal.current, isNull);
      expect(fixture.events, contains('import-admission.begin'));
      expect(fixture.events, isNot(contains('import-journal.create')));

      admission.releaseAdmittedMutation();
      final result = await confirmation;

      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.importedAndActive,
      );
      expect(admission.blocksNewLegacyMutation, isFalse);
      expect(
        fixture.events.indexOf('import-admission.begin'),
        lessThan(fixture.events.indexOf('import-admission.drained')),
      );
      expect(
        fixture.events.indexOf('import-admission.drained'),
        lessThan(fixture.events.indexOf('import-journal.create')),
      );
      expect(
        fixture.events.indexOf('import-journal.create'),
        lessThan(fixture.events.indexOf('import-admission.end')),
      );
    },
  );

  test(
    'recovered initial journal publishes pending before admission ends',
    () async {
      final pendingChanges = <bool>[];
      final admission = _ImportOperationAdmission();
      final fixture = await _ImportFixture.create(
        importOperationAdmission: admission,
        failAfterEvent: 'import-journal.create',
        recoveryImportPendingChanges: pendingChanges,
      );
      admission.attachEvents(fixture.events);
      await fixture.coordinator.prepareRecoveryImport();
      fixture.events.clear();

      final confirmation = fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );
      await admission.entered;
      admission.releaseAdmittedMutation();
      final result = await confirmation;

      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.recoveryRequired,
      );
      expect(result.pendingImport, isTrue);
      expect(pendingChanges, <bool>[false, true]);
      expect(
        fixture.events.indexOf('recovery-import.pending:true'),
        lessThan(fixture.events.indexOf('import-admission.end')),
      );
      expect(admission.blocksNewLegacyMutation, isFalse);
    },
  );

  test(
    'inconclusive post-create journal read remains pending before admission ends',
    () async {
      final pendingChanges = <bool>[];
      final admission = _ImportOperationAdmission();
      final fixture = await _ImportFixture.create(
        importOperationAdmission: admission,
        recoveryImportPendingChanges: pendingChanges,
      );
      admission.attachEvents(fixture.events);
      await fixture.coordinator.prepareRecoveryImport();
      fixture.events.clear();
      fixture.faults
        ..failAfterEvent = 'import-journal.create'
        ..failBeforeEvent = 'import-journal.read'
        ..remainingBeforeMatches = 2;

      final confirmation = fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );
      await admission.entered;
      admission.releaseAdmittedMutation();
      final result = await confirmation;

      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.recoveryRequired,
      );
      expect(result.pendingImport, isTrue);
      expect(fixture.importJournal.current, isNotNull);
      expect(pendingChanges, <bool>[false, true]);
      expect(
        fixture.events.indexOf('recovery-import.pending:true'),
        lessThan(fixture.events.indexOf('import-admission.end')),
      );
      expect(admission.blocksNewLegacyMutation, isFalse);
    },
  );

  test(
    'new import rejects occupied target resources before journaling',
    () async {
      final occupiedKey = await _ImportFixture.create();
      await occupiedKey.coordinator.prepareRecoveryImport();
      occupiedKey.keyStore.replaceKeyForTest(occupiedKey.caseData.vaultKey);

      final keyResult = await occupiedKey.coordinator.confirmRecoveryImport(
        occupiedKey.caseData.recoveryText,
      );

      expect(keyResult.disposition, AtlasVaultRecoveryImportDisposition.failed);
      expect(occupiedKey.importJournal.current, isNull);
      expect(_persistentImportMutations(occupiedKey.events), isEmpty);
      expect(
        await occupiedKey.keyStore.containsVaultKey(
          occupiedKey.caseData.vaultId,
        ),
        isTrue,
      );

      final occupiedStore = await _ImportFixture.create();
      await occupiedStore.coordinator.prepareRecoveryImport();
      occupiedStore.storeIO.replaceStoreForTest(
        _occupiedStore(occupiedStore.caseData),
      );

      final storeResult = await occupiedStore.coordinator.confirmRecoveryImport(
        occupiedStore.caseData.recoveryText,
      );

      expect(
        storeResult.disposition,
        AtlasVaultRecoveryImportDisposition.failed,
      );
      expect(occupiedStore.importJournal.current, isNull);
      expect(_persistentImportMutations(occupiedStore.events), isEmpty);
      expect(occupiedStore.storeIO.current, isNotNull);
    },
  );

  test('logical projection collisions fail before import journaling', () async {
    for (final type in <vault.AtlasVaultPayloadType>[
      vault.AtlasVaultPayloadType.savedSearch,
      vault.AtlasVaultPayloadType.savedJob,
    ]) {
      final fixture = await _ImportFixture.create(
        pickedBytes: await _appleExportWithDuplicateLogicalRecord(type),
      );
      expect(
        (await fixture.coordinator.prepareRecoveryImport()).disposition,
        AtlasVaultRecoveryImportDisposition.importPrepared,
      );

      final result = await fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );

      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.failed,
        reason: type.wireName,
      );
      expect(fixture.importJournal.current, isNull, reason: type.wireName);
      expect(fixture.storeIO.current, isNull, reason: type.wireName);
      expect(
        await fixture.keyStore.containsVaultKey(fixture.caseData.vaultId),
        isFalse,
        reason: type.wireName,
      );
      expect(await fixture.selected.read(), isNull, reason: type.wireName);
      expect(
        _persistentImportMutations(fixture.events),
        isEmpty,
        reason: type.wireName,
      );
    }
  });

  test('corrupt encrypted record creates no persistent resource', () async {
    final fixture = await _ImportFixture.create(
      pickedBytes: _tamperedAppleExportBytes(),
    );
    expect(
      (await fixture.coordinator.prepareRecoveryImport()).disposition,
      AtlasVaultRecoveryImportDisposition.importPrepared,
    );

    final result = await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );

    expect(result.disposition, AtlasVaultRecoveryImportDisposition.failed);
    expect(_persistentImportMutations(fixture.events), isEmpty);
    expect(fixture.importJournal.current, isNull);
    expect(fixture.storeIO.current, isNull);
  });

  test('valid passphrase wrap may coexist with recovery wrap', () async {
    final fixture = await _ImportFixture.create(
      pickedBytes: _appleExportWithPassphraseWrapBytes(),
    );

    final prepared = await fixture.coordinator.prepareRecoveryImport();
    final imported = await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );

    expect(
      prepared.disposition,
      AtlasVaultRecoveryImportDisposition.importPrepared,
    );
    expect(
      imported.disposition,
      AtlasVaultRecoveryImportDisposition.importedAndActive,
    );
    expect(
      fixture.storeIO.current!.vaultMetadata.keyWraps
          .whereType<vault.AtlasVaultPassphraseKeyWrapV1>(),
      hasLength(1),
    );
  });

  test(
    'every interrupted import stage resumes after explicit re-entry',
    () async {
      final cases =
          <
            ({
              String event,
              int occurrence,
              AtlasVaultRecoveryImportStage stage,
            })
          >[
            (
              event: 'import-journal.create',
              occurrence: 1,
              stage: AtlasVaultRecoveryImportStage.prepared,
            ),
            (
              event: 'store.create',
              occurrence: 1,
              stage: AtlasVaultRecoveryImportStage.prepared,
            ),
            (
              event: 'import-journal.replace',
              occurrence: 1,
              stage: AtlasVaultRecoveryImportStage.storeCreated,
            ),
            (
              event: 'key.create',
              occurrence: 1,
              stage: AtlasVaultRecoveryImportStage.storeCreated,
            ),
            (
              event: 'import-journal.replace',
              occurrence: 2,
              stage: AtlasVaultRecoveryImportStage.keyCreated,
            ),
            (
              event: 'selection.create',
              occurrence: 1,
              stage: AtlasVaultRecoveryImportStage.keyCreated,
            ),
            (
              event: 'import-journal.replace',
              occurrence: 3,
              stage: AtlasVaultRecoveryImportStage.selectionCommitted,
            ),
            (
              event: 'import-journal.replace',
              occurrence: 4,
              stage: AtlasVaultRecoveryImportStage.completionPending,
            ),
          ];

      for (final interruption in cases) {
        final fixture = await _ImportFixture.create(
          failAfterEvent: interruption.event,
          failAfterOccurrence: interruption.occurrence,
        );
        await fixture.coordinator.prepareRecoveryImport();
        final interrupted = await fixture.coordinator.confirmRecoveryImport(
          fixture.caseData.recoveryText,
        );
        expect(interrupted.pendingImport, isTrue, reason: interruption.event);
        final journal = AtlasVaultRecoveryImportJournal.decodeBytes(
          fixture.importJournal.current!,
        );
        expect(journal.stage, interruption.stage, reason: interruption.event);

        fixture.faults.clear();
        final prepared = await fixture.coordinator.prepareRecoveryImport();
        expect(
          prepared.disposition,
          AtlasVaultRecoveryImportDisposition.importPrepared,
          reason: interruption.event,
        );
        expect(prepared.pendingImport, isTrue, reason: interruption.event);
        final resumed = await fixture.coordinator.confirmRecoveryImport(
          fixture.caseData.recoveryText,
        );
        expect(
          resumed.disposition,
          AtlasVaultRecoveryImportDisposition.importedAndActive,
          reason: interruption.event,
        );
        expect(fixture.importJournal.current, isNull);
        expect(fixture.runtime.isActive, isTrue);
      }
    },
  );

  test('journal-clear failure remains explicitly resumable', () async {
    final pendingChanges = <bool>[];
    final fixture = await _ImportFixture.create(
      failBeforeEvent: 'import-journal.delete',
      recoveryImportPendingChanges: pendingChanges,
    );
    await fixture.coordinator.prepareRecoveryImport();

    final interrupted = await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );

    expect(
      interrupted.disposition,
      AtlasVaultRecoveryImportDisposition.completionPending,
    );
    expect(
      AtlasVaultRecoveryImportJournal.decodeBytes(
        fixture.importJournal.current!,
      ).stage,
      AtlasVaultRecoveryImportStage.completionPending,
    );
    expect(pendingChanges, <bool>[false, true]);
    fixture.faults.clear();
    expect(
      (await fixture.coordinator.prepareRecoveryImport()).disposition,
      AtlasVaultRecoveryImportDisposition.importPrepared,
    );
    expect(
      (await fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      )).disposition,
      AtlasVaultRecoveryImportDisposition.importedAndActive,
    );
    expect(pendingChanges, <bool>[false, true, false]);
    expect(
      fixture.events.indexOf('import-journal.delete'),
      lessThan(fixture.events.lastIndexOf('recovery-import.pending:false')),
    );
  });

  test(
    'conclusive absent journal read releases post-delete pending authority',
    () async {
      final pendingChanges = <bool>[];
      final fixture = await _ImportFixture.create(
        failAfterEvent: 'import-journal.delete',
        recoveryImportPendingChanges: pendingChanges,
      );
      await fixture.coordinator.prepareRecoveryImport();

      final interrupted = await fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );

      expect(
        interrupted.disposition,
        AtlasVaultRecoveryImportDisposition.completionPending,
      );
      expect(interrupted.pendingImport, isTrue);
      expect(fixture.importJournal.current, isNull);
      expect(pendingChanges, <bool>[false, true]);
      fixture.faults.clear();

      final inspected = await fixture.coordinator.prepareRecoveryImport();

      expect(
        inspected.disposition,
        AtlasVaultRecoveryImportDisposition.existingVault,
      );
      expect(inspected.pendingImport, isFalse);
      expect(pendingChanges, <bool>[false, true, false]);
      expect(
        fixture.events.lastIndexOf('import-journal.read'),
        lessThan(fixture.events.lastIndexOf('recovery-import.pending:false')),
      );
    },
  );

  test(
    'discard releases pending authority after conclusive journal absence',
    () async {
      final pendingChanges = <bool>[];
      final fixture = await _ImportFixture.create(
        failAfterEvent: 'import-journal.delete',
        recoveryImportPendingChanges: pendingChanges,
      );
      await fixture.coordinator.prepareRecoveryImport();

      final interrupted = await fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );

      expect(
        interrupted.disposition,
        AtlasVaultRecoveryImportDisposition.completionPending,
      );
      expect(interrupted.pendingImport, isTrue);
      expect(fixture.importJournal.current, isNull);
      expect(pendingChanges, <bool>[false, true]);
      fixture.faults.clear();

      final discarded = await fixture.coordinator.discardPendingImport();

      expect(
        discarded.disposition,
        AtlasVaultRecoveryImportDisposition.cancelled,
      );
      expect(discarded.pendingImport, isFalse);
      expect(pendingChanges, <bool>[false, true, false]);
      expect(
        fixture.events.lastIndexOf('import-journal.read'),
        lessThan(fixture.events.lastIndexOf('recovery-import.pending:false')),
      );
    },
  );

  test(
    'cancelled journal backup reselection preserves pending state',
    () async {
      final fixture = await _ImportFixture.create(
        failAfterEvent: 'store.create',
      );
      await fixture.coordinator.prepareRecoveryImport();
      final interrupted = await fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );
      expect(interrupted.pendingImport, isTrue);
      fixture.faults.clear();
      fixture.transport.cancelFuturePicks();

      final cancelled = await fixture.coordinator.prepareRecoveryImport();

      expect(
        cancelled.disposition,
        AtlasVaultRecoveryImportDisposition.cancelled,
      );
      expect(cancelled.pendingImport, isTrue);
      expect(fixture.importJournal.current, isNotNull);
    },
  );

  test('invalid journal backup reselection preserves pending state', () async {
    final canonical = _ImportCase.iosToFlutter().canonicalExportBytes;
    final candidates = <Uint8List>[
      Uint8List.fromList(utf8.encode('{invalid')),
      Uint8List.fromList(<int>[0x20, ...canonical]),
    ];

    for (final candidate in candidates) {
      final fixture = await _ImportFixture.create(
        failAfterEvent: 'store.create',
      );
      await fixture.coordinator.prepareRecoveryImport();
      final interrupted = await fixture.coordinator.confirmRecoveryImport(
        fixture.caseData.recoveryText,
      );
      expect(interrupted.pendingImport, isTrue);
      fixture.faults.clear();
      fixture.transport.replaceFuturePick(candidate);
      final before = _persistentImportMutations(fixture.events).length;

      final failed = await fixture.coordinator.prepareRecoveryImport();

      expect(failed.disposition, AtlasVaultRecoveryImportDisposition.failed);
      expect(failed.pendingImport, isTrue);
      expect(fixture.importJournal.current, isNotNull);
      expect(_persistentImportMutations(fixture.events), hasLength(before));
    }
  });

  test(
    'pre-selection reset removes only hash-bound import resources',
    () async {
      for (final event in <String>['store.create', 'key.create']) {
        final fixture = await _ImportFixture.create(failAfterEvent: event);
        await fixture.coordinator.prepareRecoveryImport();
        await fixture.coordinator.confirmRecoveryImport(
          fixture.caseData.recoveryText,
        );
        fixture.faults.clear();

        final reset = await fixture.coordinator.discardPendingImport();

        expect(
          reset.disposition,
          AtlasVaultRecoveryImportDisposition.cancelled,
          reason: event,
        );
        expect(fixture.storeIO.current, isNull);
        expect(
          await fixture.keyStore.containsVaultKey(fixture.caseData.vaultId),
          isFalse,
        );
        expect(await fixture.selected.read(), isNull);
        expect(fixture.importJournal.current, isNull);
        expect(
          _persistentImportMutations(fixture.events).last,
          'import-journal.delete',
        );
      }
    },
  );

  test('post-selection reset is rejected without deleting resources', () async {
    final fixture = await _ImportFixture.create(
      failAfterEvent: 'selection.create',
    );
    await fixture.coordinator.prepareRecoveryImport();
    await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );
    fixture.faults.clear();
    final before = _persistentImportMutations(fixture.events).length;

    final reset = await fixture.coordinator.discardPendingImport();

    expect(
      reset.disposition,
      AtlasVaultRecoveryImportDisposition.recoveryRequired,
    );
    expect(await fixture.selected.read(), fixture.caseData.vaultId);
    expect(fixture.storeIO.current, isNotNull);
    expect(
      await fixture.keyStore.containsVaultKey(fixture.caseData.vaultId),
      isTrue,
    );
    expect(_persistentImportMutations(fixture.events).length, before);
  });

  test('reset digest mismatch deletes nothing', () async {
    final fixture = await _ImportFixture.create(failAfterEvent: 'store.create');
    await fixture.coordinator.prepareRecoveryImport();
    await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );
    fixture.faults.clear();
    final original = fixture.storeIO.current!;
    fixture.storeIO.replaceStoreForTest(
      vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
        ...original.toJson(),
        'updated_at': '2026-07-29T03:04:06Z',
      }),
    );
    final before = _persistentImportMutations(fixture.events).length;

    final reset = await fixture.coordinator.discardPendingImport();

    expect(
      reset.disposition,
      AtlasVaultRecoveryImportDisposition.recoveryRequired,
    );
    expect(fixture.storeIO.current, isNotNull);
    expect(fixture.importJournal.current, isNotNull);
    expect(_persistentImportMutations(fixture.events).length, before);
  });

  test('reset key digest mismatch deletes nothing', () async {
    final fixture = await _ImportFixture.create(failAfterEvent: 'key.create');
    await fixture.coordinator.prepareRecoveryImport();
    await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );
    fixture.faults.clear();
    fixture.keyStore.replaceKeyForTest(Uint8List(32));
    final before = _persistentImportMutations(fixture.events).length;

    final reset = await fixture.coordinator.discardPendingImport();

    expect(
      reset.disposition,
      AtlasVaultRecoveryImportDisposition.recoveryRequired,
    );
    expect(fixture.storeIO.current, isNotNull);
    expect(fixture.importJournal.current, isNotNull);
    expect(_persistentImportMutations(fixture.events).length, before);
  });

  test('direct Apple artifact imports when exchange mode is enabled', () async {
    final directory = Platform.environment['ATLAS_INTEROP_ARTIFACT_DIR'];
    if (directory == null) {
      return;
    }
    final artifact = File('$directory/ios-to-flutter.atlasvault');
    expect(await artifact.exists(), isTrue);
    final bytes = await artifact.readAsBytes();
    final digest = await vault.atlasVaultSha256Hex(bytes);
    final digestFile = File('$directory/ios-to-flutter.sha256');
    expect(await digestFile.exists(), isTrue);
    expect((await digestFile.readAsString()).trim(), digest);
    final fixture = await _ImportFixture.create(pickedBytes: bytes);

    await fixture.coordinator.prepareRecoveryImport();
    final result = await fixture.coordinator.confirmRecoveryImport(
      fixture.caseData.recoveryText,
    );

    expect(
      result.disposition,
      AtlasVaultRecoveryImportDisposition.importedAndActive,
    );
    expect(digest, fixture.caseData.canonicalExportSha256);
  });
}

List<String> _persistentImportMutations(List<String> events) {
  return events
      .where(
        (event) =>
            event.endsWith('.create') ||
            event.endsWith('.replace') ||
            event.endsWith('.delete'),
      )
      .toList(growable: false);
}

const _privateSentinels = <String>[
  'FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK',
  'FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK',
  'FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK',
  'saved_search',
  'saved_job',
];

final class _Fixture {
  _Fixture({
    required this.caseData,
    required this.keyStore,
    required this.storeIO,
    required this.selected,
    required this.migration,
    required this.runtime,
    required this.transport,
    required this.coordinator,
  });

  final _InteropCase caseData;
  final InteropMemorySecureKeyStore keyStore;
  final InteropMemoryLocalStoreIO storeIO;
  final _MutableSelectedVaultStore selected;
  final InteropMemoryMigrationJournalStore migration;
  final AtlasVaultPrivateStateRuntime runtime;
  final _RecordingDocumentTransport transport;
  final AtlasVaultInteroperabilityCoordinator coordinator;

  static Future<_Fixture> create({
    bool activate = true,
    bool existingRecoveryWrap = false,
    bool existingPassphraseWrap = false,
    Uint8List? migrationJournalBytes,
    bool importPending = false,
    bool saveResult = true,
  }) async {
    final caseData = _InteropCase.flutterToIos();
    final export = caseData.export;
    final metadata = export.vaultMetadata.toJson();
    if (!existingRecoveryWrap) {
      metadata['key_wraps'] = <Object?>[];
    }
    if (existingPassphraseWrap) {
      final wraps = List<Object?>.from(metadata['key_wraps']! as List<Object?>);
      wraps.insert(0, _testPassphraseWrap);
      metadata['key_wraps'] = wraps;
    }
    final store = vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
      'format': vault.AtlasVaultLocalStore.format,
      'version': vault.AtlasVaultLocalStore.version,
      'store_id': caseData.sourceStoreId,
      'created_at': caseData.exportTimestamp,
      'updated_at': caseData.exportTimestamp,
      'vault_metadata': metadata,
      'records': <Object?>[
        for (final record in export.records) record.toJson(),
      ],
    });
    final keyStore = InteropMemorySecureKeyStore(key: caseData.vaultKey);
    final storeIO = InteropMemoryLocalStoreIO(store: store);
    final selected = _MutableSelectedVaultStore(caseData.vaultId);
    final migration = InteropMemoryMigrationJournalStore(
      bytes: migrationJournalBytes,
    );
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: storeIO,
    );
    if (activate) {
      expect(
        await runtime.activateExisting(caseData.vaultId),
        AtlasVaultActivationResult.activated,
      );
    }
    keyStore.calls.clear();
    storeIO.calls.clear();
    selected.calls.clear();
    migration.calls.clear();
    final transport = _RecordingDocumentTransport(saveResult: saveResult);
    final coordinator = AtlasVaultInteroperabilityCoordinator(
      runtime: runtime,
      selectedVaultStore: selected,
      migrationJournalStore: migration,
      recoveryImportPending: () async => importPending,
      documentTransport: transport,
      now: () => DateTime.parse(caseData.exportTimestamp),
      uuidProvider: () => caseData.exportId,
      recoveryKeyProvider: () =>
          vault.AtlasVaultRecoveryKey.parse(caseData.recoveryText),
      recoverySaltProvider: () => Uint8List.fromList(caseData.wrapSalt),
      recoveryNonceProvider: () => Uint8List.fromList(caseData.wrapNonce),
    );
    return _Fixture(
      caseData: caseData,
      keyStore: keyStore,
      storeIO: storeIO,
      selected: selected,
      migration: migration,
      runtime: runtime,
      transport: transport,
      coordinator: coordinator,
    );
  }
}

const _testPassphraseWrap = <String, Object?>{
  'id': 'primary-passphrase',
  'type': 'passphrase',
  'kdf': <String, Object?>{
    'algorithm': 'Argon2id',
    'salt': 'IiIiIiIiIiIiIiIiIiIiIg==',
    'memory_kib': 1024,
    'iterations': 2,
    'parallelism': 1,
  },
  'nonce': 'MzMzMzMzMzMzMzMz',
  'ciphertext':
      'JJzE300uvWP/iqioMFTRANtsnhXearJAsujEbtWYY1SyRNBUfZu+5bhYcHZvX87L',
};

final class _MutableSelectedVaultStore implements AtlasVaultSelectedVaultStore {
  _MutableSelectedVaultStore(this.value);

  String? value;
  final List<String> calls = <String>[];

  @override
  Future<String?> read() async {
    calls.add('selection.read');
    return value;
  }

  @override
  Future<void> create(String vaultId) async {
    calls.add('selection.create');
    if (value != null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    value = vaultId;
  }

  @override
  Future<void> clear(String expectedVaultId) async {
    calls.add('selection.clear');
    if (value != expectedVaultId) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    value = null;
  }
}

final class _RecordingDocumentTransport
    implements AtlasVaultEncryptedDocumentTransport {
  _RecordingDocumentTransport({this.saveResult = true, Uint8List? pickedBytes})
    : _pickedBytes = pickedBytes == null
          ? null
          : Uint8List.fromList(pickedBytes);

  final bool saveResult;
  Uint8List? _pickedBytes;
  Uint8List? savedBytes;
  int pickCalls = 0;

  void cancelFuturePicks() {
    _pickedBytes?.fillRange(0, _pickedBytes!.length, 0);
    _pickedBytes = null;
  }

  void replaceFuturePick(Uint8List bytes) {
    _pickedBytes?.fillRange(0, _pickedBytes!.length, 0);
    _pickedBytes = Uint8List.fromList(bytes);
  }

  @override
  Future<Uint8List?> pickEncryptedExport() async {
    pickCalls += 1;
    final bytes = _pickedBytes;
    return bytes == null ? null : Uint8List.fromList(bytes);
  }

  @override
  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes) async {
    savedBytes = Uint8List.fromList(canonicalExportBytes);
    final artifactDirectory =
        Platform.environment['ATLAS_INTEROP_ARTIFACT_DIR'];
    if (saveResult && artifactDirectory != null) {
      final directory = Directory(artifactDirectory);
      await directory.create(recursive: true);
      final digest = await vault.atlasVaultSha256Hex(savedBytes!);
      await File(
        '${directory.path}/flutter-to-ios.atlasvault',
      ).writeAsBytes(savedBytes!, flush: true);
      await File(
        '${directory.path}/flutter-to-ios.sha256',
      ).writeAsString('$digest\n', flush: true);
    }
    return saveResult;
  }
}

final class _ImportFixture {
  _ImportFixture({
    required this.caseData,
    required this.events,
    required this.keyStore,
    required this.storeIO,
    required this.selected,
    required this.importJournal,
    required this.faults,
    required this.runtime,
    required this.transport,
    required this.compatibilitySource,
    required this.coordinator,
  });

  final _ImportCase caseData;
  final List<String> events;
  final InteropMemorySecureKeyStore keyStore;
  final InteropMemoryLocalStoreIO storeIO;
  final InteropMemorySelectedVaultStore selected;
  final InteropMemoryRecoveryImportJournalStore importJournal;
  final InteropOperationFaults faults;
  final AtlasVaultPrivateStateRuntime runtime;
  final _RecordingDocumentTransport transport;
  final _ImportCompatibilitySource compatibilitySource;
  final AtlasVaultInteroperabilityCoordinator coordinator;

  static Future<_ImportFixture> create({
    String? selectedVaultId,
    bool pickerCancelled = false,
    Uint8List? pickedBytes,
    AtlasVaultPlaintextPrivateState? inMemoryState,
    AtlasVaultPlaintextPrivateState? compatibilityState,
    app.AtlasLocalCacheMigrationPrivateState? cacheState,
    bool compatibilityFails = false,
    AtlasVaultRecoveryImportOperationAdmission? importOperationAdmission,
    List<bool>? recoveryImportPendingChanges,
    String? failAfterEvent,
    int failAfterOccurrence = 1,
    String? failBeforeEvent,
  }) async {
    final caseData = _ImportCase.iosToFlutter();
    final events = <String>[];
    final faults = InteropOperationFaults()
      ..failAfterEvent = failAfterEvent
      ..remainingAfterMatches = failAfterOccurrence
      ..failBeforeEvent = failBeforeEvent;
    final keyStore = InteropMemorySecureKeyStore(
      events: events,
      faults: faults,
    );
    final storeIO = InteropMemoryLocalStoreIO(events: events, faults: faults);
    final selected = InteropMemorySelectedVaultStore(
      value: selectedVaultId,
      events: events,
      faults: faults,
    );
    final migration = InteropMemoryMigrationJournalStore();
    final importJournal = InteropMemoryRecoveryImportJournalStore(
      events: events,
      faults: faults,
    );
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: storeIO,
    );
    final transport = _RecordingDocumentTransport(
      pickedBytes: pickerCancelled
          ? null
          : pickedBytes ?? caseData.canonicalExportBytes,
    );
    final compatibilitySource = _ImportCompatibilitySource(
      compatibilityState ?? _emptyPlaintextState(),
      fails: compatibilityFails,
    );
    final coordinator = AtlasVaultInteroperabilityCoordinator(
      runtime: runtime,
      selectedVaultStore: selected,
      migrationJournalStore: migration,
      recoveryImportPending: () async => importJournal.current != null,
      documentTransport: transport,
      recoveryImportJournalStore: importJournal,
      secureKeyStore: keyStore,
      localStoreIO: storeIO,
      inMemorySource: _ImportPlaintextSource(
        inMemoryState ?? _emptyPlaintextState(),
      ),
      compatibilitySource: compatibilitySource,
      cacheSource: _ImportCacheSource(cacheState ?? _emptyCacheState()),
      importOperationAdmission: importOperationAdmission,
      recoveryImportPendingDidChange: recoveryImportPendingChanges == null
          ? null
          : (pending) {
              recoveryImportPendingChanges.add(pending);
              events.add('recovery-import.pending:$pending');
            },
      now: () => DateTime.parse('2026-07-29T03:04:05Z'),
      importIdProvider: () => '30000000-0000-4000-8000-000000000301',
      importStoreIdProvider: () => '30000000-0000-4000-8000-000000000302',
    );
    return _ImportFixture(
      caseData: caseData,
      events: events,
      keyStore: keyStore,
      storeIO: storeIO,
      selected: selected,
      importJournal: importJournal,
      faults: faults,
      runtime: runtime,
      transport: transport,
      compatibilitySource: compatibilitySource,
      coordinator: coordinator,
    );
  }
}

final class _ImportOperationAdmission
    implements AtlasVaultRecoveryImportOperationAdmission {
  final Completer<void> _entered = Completer<void>();
  final Completer<void> _release = Completer<void>();
  List<String>? _events;
  bool blocksNewLegacyMutation = false;

  Future<void> get entered => _entered.future;

  void attachEvents(List<String> events) {
    _events = events;
  }

  bool tryAdmitLegacyMutation() => !blocksNewLegacyMutation;

  void releaseAdmittedMutation() {
    if (!_release.isCompleted) {
      _release.complete();
    }
  }

  @override
  Future<void> beginRecoveryImportAdmission() async {
    if (blocksNewLegacyMutation) {
      throw const AtlasVaultInteroperabilityException();
    }
    blocksNewLegacyMutation = true;
    _events?.add('import-admission.begin');
    if (!_entered.isCompleted) {
      _entered.complete();
    }
    await _release.future;
    _events?.add('import-admission.drained');
  }

  @override
  void endRecoveryImportAdmission() {
    _events?.add('import-admission.end');
    blocksNewLegacyMutation = false;
  }
}

final class _ImportCase {
  _ImportCase({
    required this.recoveryText,
    required this.vaultKey,
    required this.vaultId,
    required this.canonicalExportBytes,
    required this.expectedRecordCount,
    required this.sourceStoreId,
    required this.canonicalExportSha256,
    required this.expectedSavedSearchCount,
    required this.expectedTrackerCount,
    required this.expectedTombstoneCount,
  });

  final String recoveryText;
  final Uint8List vaultKey;
  final String vaultId;
  final Uint8List canonicalExportBytes;
  final int expectedRecordCount;
  final String sourceStoreId;
  final String canonicalExportSha256;
  final int expectedSavedSearchCount;
  final int expectedTrackerCount;
  final int expectedTombstoneCount;

  vault.AtlasVaultEncryptedExport get export =>
      vault.AtlasVaultEncryptedExport.decodeJson(
        utf8.decode(canonicalExportBytes),
      );

  factory _ImportCase.iosToFlutter() {
    final root = loadAtlasVaultVector(
      'atlasvault_ios_flutter_interop_vectors_v1.json',
    );
    final value = atlasVaultObject(root['ios_to_flutter']);
    return _ImportCase(
      recoveryText: value['test_only_recovery_key_text']! as String,
      vaultKey: Uint8List.fromList(
        base64Decode(value['test_only_vault_key_b64']! as String),
      ),
      vaultId: value['vault_id']! as String,
      canonicalExportBytes: Uint8List.fromList(
        base64Decode(value['canonical_encrypted_export_b64']! as String),
      ),
      expectedRecordCount: value['expected_encrypted_record_count']! as int,
      sourceStoreId: value['local_source_store_id']! as String,
      canonicalExportSha256:
          value['canonical_encrypted_export_sha256']! as String,
      expectedSavedSearchCount:
          value['expected_active_saved_search_count']! as int,
      expectedTrackerCount:
          value['expected_active_tracker_saved_job_count']! as int,
      expectedTombstoneCount: value['expected_tombstone_count']! as int,
    );
  }
}

Uint8List _tamperedAppleExportBytes() {
  final source =
      jsonDecode(utf8.decode(_ImportCase.iosToFlutter().canonicalExportBytes))!
          as Map<String, Object?>;
  final records = List<Object?>.from(source['records']! as List<Object?>);
  final record = Map<String, Object?>.from(
    records.first! as Map<String, Object?>,
  );
  final ciphertext = base64Decode(record['ciphertext']! as String);
  ciphertext[0] ^= 0x01;
  record['ciphertext'] = base64Encode(ciphertext);
  records[0] = record;
  source['records'] = records;
  return vault.AtlasVaultEncryptedExport.fromJson(source).canonicalBytes();
}

vault.AtlasVaultLocalStore _occupiedStore(_ImportCase caseData) {
  return vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': vault.AtlasVaultLocalStore.format,
    'version': vault.AtlasVaultLocalStore.version,
    'store_id': '30000000-0000-4000-8000-000000000399',
    'created_at': '2026-07-29T03:04:05Z',
    'updated_at': '2026-07-29T03:04:05Z',
    'vault_metadata': caseData.export.vaultMetadata.toJson(),
    'records': <Object?>[
      for (final record in caseData.export.records) record.toJson(),
    ],
  });
}

Future<Uint8List> _appleExportWithDuplicateLogicalRecord(
  vault.AtlasVaultPayloadType type,
) async {
  final caseData = _ImportCase.iosToFlutter();
  final export = caseData.export;
  vault.AtlasVaultEncryptedRecord? source;
  Uint8List? plaintext;
  for (final record in export.records) {
    final candidate = await vault.openAtlasVaultRecord(
      vaultKey: caseData.vaultKey,
      vaultId: caseData.vaultId,
      record: record,
    );
    if (!record.deleted) {
      final envelope = vault.AtlasVaultPayloadEnvelope.decodeJson(
        utf8.decode(candidate, allowMalformed: false),
      );
      if (envelope.type == type) {
        source = record;
        plaintext = candidate;
        break;
      }
    }
    candidate.fillRange(0, candidate.length, 0);
  }
  if (source == null || plaintext == null) {
    throw StateError('Missing deterministic interoperability record.');
  }
  try {
    final savedSearch = type == vault.AtlasVaultPayloadType.savedSearch;
    final template = vault.AtlasVaultEncryptedRecord.fromJson(<String, Object?>{
      ...source.toJson(),
      'id': savedSearch
          ? '30000000-0000-4000-8000-000000000310'
          : '30000000-0000-4000-8000-000000000320',
      'revision': savedSearch
          ? '30000000-0000-4000-8000-000000000311'
          : '30000000-0000-4000-8000-000000000321',
      'parent_revision': null,
      'nonce': base64Encode(
        Uint8List.fromList(
          savedSearch
              ? const <int>[1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23]
              : const <int>[2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24],
        ),
      ),
      'ciphertext': base64Encode(Uint8List(16)),
    });
    final duplicate = await vault.sealAtlasVaultRecord(
      plaintext: plaintext,
      vaultKey: caseData.vaultKey,
      vaultId: caseData.vaultId,
      record: template,
    );
    return vault.AtlasVaultEncryptedExport.fromJson(<String, Object?>{
      ...export.toJson(),
      'records': <Object?>[
        for (final record in export.records) record.toJson(),
        duplicate.toJson(),
      ],
    }).canonicalBytes();
  } finally {
    plaintext.fillRange(0, plaintext.length, 0);
    caseData.vaultKey.fillRange(0, caseData.vaultKey.length, 0);
  }
}

Uint8List _appleExportWithPassphraseWrapBytes() {
  final source =
      jsonDecode(utf8.decode(_ImportCase.iosToFlutter().canonicalExportBytes))!
          as Map<String, Object?>;
  final metadata = Map<String, Object?>.from(
    source['vault_metadata']! as Map<String, Object?>,
  );
  final wraps = List<Object?>.from(metadata['key_wraps']! as List<Object?>);
  final keyWrapVectors = loadAtlasVaultVector(
    'atlasvault_key_wrap_vectors_v1.json',
  );
  final vectors = keyWrapVectors['vectors']! as List<Object?>;
  final passphraseMetadata =
      (vectors.single! as Map<String, Object?>)['vault_metadata']!
          as Map<String, Object?>;
  wraps.add(
    Map<String, Object?>.from(
      (passphraseMetadata['key_wraps']! as List<Object?>).single!
          as Map<String, Object?>,
    ),
  );
  metadata['key_wraps'] = wraps;
  source['vault_metadata'] = metadata;
  return vault.AtlasVaultEncryptedExport.fromJson(source).canonicalBytes();
}

AtlasVaultPlaintextPrivateState _emptyPlaintextState() {
  return AtlasVaultPlaintextPrivateState(
    savedSearches: const <app.AtlasSavedSearch>[],
    trackerRecords: const <app.AtlasApplicationRecord>[],
  );
}

AtlasVaultPlaintextPrivateState _plaintextState() {
  return AtlasVaultPlaintextPrivateState(
    savedSearches: <app.AtlasSavedSearch>[
      app.AtlasSavedSearch(
        name: 'FAKE_IMPORT_GATE_SEARCH',
        request: app.AtlasSearchRequest(text: 'FAKE_IMPORT_GATE_QUERY'),
      ),
    ],
    trackerRecords: const <app.AtlasApplicationRecord>[],
  );
}

app.AtlasLocalCacheMigrationPrivateState _emptyCacheState() {
  return app.AtlasLocalCacheMigrationPrivateState(
    savedSearches: const <app.AtlasSavedSearch>[],
    trackerRecords: const <app.AtlasApplicationRecord>[],
    privateSha256: null,
    cachePresent: false,
  );
}

app.AtlasLocalCacheMigrationPrivateState _privateCacheState() {
  return app.AtlasLocalCacheMigrationPrivateState(
    savedSearches: _plaintextState().savedSearches,
    trackerRecords: const <app.AtlasApplicationRecord>[],
    privateSha256: '1' * 64,
  );
}

final class _ImportPlaintextSource implements AtlasVaultPlaintextStateSource {
  const _ImportPlaintextSource(this.state);

  final AtlasVaultPlaintextPrivateState state;

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async =>
      state;
}

final class _ImportCompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  _ImportCompatibilitySource(this.state, {required this.fails});

  final AtlasVaultPlaintextPrivateState state;
  bool fails;
  int readCalls = 0;

  @override
  Uri get authorityBaseURL => Uri.parse('https://example.invalid/');

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async {
    readCalls += 1;
    if (fails) {
      throw StateError('deterministic compatibility failure');
    }
    return state;
  }

  @override
  Future<bool> deleteSavedSearch(String name) {
    throw StateError('Import must not mutate compatibility private state.');
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) {
    throw StateError('Import must not mutate compatibility private state.');
  }
}

final class _ImportCacheSource implements AtlasLocalCacheMigrationSource {
  const _ImportCacheSource(this.state);

  final app.AtlasLocalCacheMigrationPrivateState state;

  @override
  Future<app.AtlasLocalCacheMigrationPrivateState>
  readPrivateStateForMigration() async => state;

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) {
    throw StateError('Import must not mutate the plaintext cache.');
  }
}

final class _InteropCase {
  _InteropCase({
    required this.recoveryText,
    required this.vaultKey,
    required this.vaultId,
    required this.exportId,
    required this.exportTimestamp,
    required this.sourceStoreId,
    required this.wrapSalt,
    required this.wrapNonce,
    required this.canonicalExportBytes,
    required this.canonicalExportSha256,
    required this.expectedRecordCount,
  }) : export = vault.AtlasVaultEncryptedExport.decodeJson(
         utf8.decode(canonicalExportBytes),
       );

  final String recoveryText;
  final Uint8List vaultKey;
  final String vaultId;
  final String exportId;
  final String exportTimestamp;
  final String sourceStoreId;
  final Uint8List wrapSalt;
  final Uint8List wrapNonce;
  final Uint8List canonicalExportBytes;
  final String canonicalExportSha256;
  final int expectedRecordCount;
  final vault.AtlasVaultEncryptedExport export;

  factory _InteropCase.flutterToIos() {
    final root = loadAtlasVaultVector(
      'atlasvault_ios_flutter_interop_vectors_v1.json',
    );
    final value = atlasVaultObject(root['flutter_to_ios']);
    return _InteropCase(
      recoveryText: value['test_only_recovery_key_text']! as String,
      vaultKey: Uint8List.fromList(
        base64Decode(value['test_only_vault_key_b64']! as String),
      ),
      vaultId: value['vault_id']! as String,
      exportId: value['export_id']! as String,
      exportTimestamp: value['export_timestamp']! as String,
      sourceStoreId: value['local_source_store_id']! as String,
      wrapSalt: Uint8List.fromList(
        base64Decode(value['wrap_salt_b64']! as String),
      ),
      wrapNonce: Uint8List.fromList(
        base64Decode(value['wrap_nonce_b64']! as String),
      ),
      canonicalExportBytes: Uint8List.fromList(
        base64Decode(value['canonical_encrypted_export_b64']! as String),
      ),
      canonicalExportSha256:
          value['canonical_encrypted_export_sha256']! as String,
      expectedRecordCount: value['expected_encrypted_record_count']! as int,
    );
  }
}
