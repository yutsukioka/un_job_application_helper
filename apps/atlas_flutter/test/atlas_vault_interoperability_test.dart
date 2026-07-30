import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

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
      expect(fixture.events, isEmpty);
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
    expect(fixture.events, isEmpty);
    expect(fixture.storeIO.current, isNull);
    expect(
      await fixture.keyStore.containsVaultKey(fixture.caseData.vaultId),
      isFalse,
    );
    expect(await fixture.selected.read(), isNull);
    expect(fixture.importJournal.current, isNull);
  });

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
      expect(fixture.events.last, 'import-journal.delete');
      expect(fixture.runtime.isActive, isTrue);
    },
  );
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
  final Uint8List? _pickedBytes;
  Uint8List? savedBytes;
  int pickCalls = 0;

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
      await File(
        '${directory.path}/flutter-to-ios.atlasvault',
      ).writeAsBytes(savedBytes!, flush: true);
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
    required this.runtime,
    required this.transport,
    required this.coordinator,
  });

  final _ImportCase caseData;
  final List<String> events;
  final InteropMemorySecureKeyStore keyStore;
  final InteropMemoryLocalStoreIO storeIO;
  final InteropMemorySelectedVaultStore selected;
  final InteropMemoryRecoveryImportJournalStore importJournal;
  final AtlasVaultPrivateStateRuntime runtime;
  final _RecordingDocumentTransport transport;
  final AtlasVaultInteroperabilityCoordinator coordinator;

  static Future<_ImportFixture> create() async {
    final caseData = _ImportCase.iosToFlutter();
    final events = <String>[];
    final keyStore = InteropMemorySecureKeyStore(events: events);
    final storeIO = InteropMemoryLocalStoreIO(events: events);
    final selected = InteropMemorySelectedVaultStore(events: events);
    final migration = InteropMemoryMigrationJournalStore();
    final importJournal = InteropMemoryRecoveryImportJournalStore(
      events: events,
    );
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: storeIO,
    );
    final transport = _RecordingDocumentTransport(
      pickedBytes: caseData.canonicalExportBytes,
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
      inMemorySource: const InteropEmptyPlaintextStateSource(),
      compatibilitySource: const InteropEmptyCompatibilityPrivateSource(),
      cacheSource: const InteropEmptyCacheMigrationSource(),
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
      runtime: runtime,
      transport: transport,
      coordinator: coordinator,
    );
  }
}

final class _ImportCase {
  _ImportCase({
    required this.recoveryText,
    required this.vaultId,
    required this.canonicalExportBytes,
    required this.expectedRecordCount,
  });

  final String recoveryText;
  final String vaultId;
  final Uint8List canonicalExportBytes;
  final int expectedRecordCount;

  factory _ImportCase.iosToFlutter() {
    final root = loadAtlasVaultVector(
      'atlasvault_ios_flutter_interop_vectors_v1.json',
    );
    final value = atlasVaultObject(root['ios_to_flutter']);
    return _ImportCase(
      recoveryText: value['test_only_recovery_key_text']! as String,
      vaultId: value['vault_id']! as String,
      canonicalExportBytes: Uint8List.fromList(
        base64Decode(value['canonical_encrypted_export_b64']! as String),
      ),
      expectedRecordCount: value['expected_encrypted_record_count']! as int,
    );
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
