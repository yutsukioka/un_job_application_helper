import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/support/atlas_vault_vector_loader.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Android recovery import resumes every protected stage', (
    tester,
  ) async {
    final vector = _RecoveryVector.load();
    final profiles = <_RecoveryProfile>[
      const _RecoveryProfile(
        'journal-created',
        AtlasVaultRecoveryImportStage.prepared,
        _InstalledResources.none,
      ),
      const _RecoveryProfile(
        'store-created-before-transition',
        AtlasVaultRecoveryImportStage.prepared,
        _InstalledResources.store,
      ),
      const _RecoveryProfile(
        'store-created',
        AtlasVaultRecoveryImportStage.storeCreated,
        _InstalledResources.store,
      ),
      const _RecoveryProfile(
        'key-created-before-transition',
        AtlasVaultRecoveryImportStage.storeCreated,
        _InstalledResources.key,
      ),
      const _RecoveryProfile(
        'key-created',
        AtlasVaultRecoveryImportStage.keyCreated,
        _InstalledResources.key,
      ),
      const _RecoveryProfile(
        'selection-created-before-transition',
        AtlasVaultRecoveryImportStage.keyCreated,
        _InstalledResources.selection,
      ),
      const _RecoveryProfile(
        'selection-committed',
        AtlasVaultRecoveryImportStage.selectionCommitted,
        _InstalledResources.selection,
      ),
      const _RecoveryProfile(
        'completion-pending',
        AtlasVaultRecoveryImportStage.completionPending,
        _InstalledResources.selection,
      ),
    ];

    for (final profile in profiles) {
      final fixture = await _RealRecoveryFixture.create(vector);
      addTearDown(fixture.cleanup);
      await fixture.installProfile(profile);

      final prepared = await fixture.coordinator.prepareRecoveryImport();
      expect(
        prepared.disposition,
        AtlasVaultRecoveryImportDisposition.importPrepared,
        reason: profile.label,
      );
      expect(prepared.pendingImport, isTrue, reason: profile.label);
      final result = await fixture.coordinator.confirmRecoveryImport(
        vector.recoveryText,
      );

      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.importedAndActive,
        reason: profile.label,
      );
      expect(await fixture.importJournal.read(), isNull);
      expect(await fixture.selected.read(), vector.vaultId);
      expect(fixture.runtime.isActiveVault(vector.vaultId), isTrue);
      await fixture.cleanup();
    }
    tester.printToConsole(
      'AtlasVault Android recovery-import stage resume matrix passed.',
    );
  });

  testWidgets('Android recovery import reset is pre-selection and hash-bound', (
    tester,
  ) async {
    final vector = _RecoveryVector.load();
    final resetProfiles = <_RecoveryProfile>[
      const _RecoveryProfile(
        'prepared',
        AtlasVaultRecoveryImportStage.prepared,
        _InstalledResources.none,
      ),
      const _RecoveryProfile(
        'store',
        AtlasVaultRecoveryImportStage.prepared,
        _InstalledResources.store,
      ),
      const _RecoveryProfile(
        'key',
        AtlasVaultRecoveryImportStage.storeCreated,
        _InstalledResources.key,
      ),
    ];
    for (final profile in resetProfiles) {
      final fixture = await _RealRecoveryFixture.create(vector);
      addTearDown(fixture.cleanup);
      await fixture.installProfile(profile);

      final result = await fixture.coordinator.discardPendingImport();

      expect(
        result.disposition,
        AtlasVaultRecoveryImportDisposition.cancelled,
        reason: profile.label,
      );
      expect(await fixture.importJournal.read(), isNull);
      expect(await fixture.localStore.read(vector.vaultId), isNull);
      expect(await fixture.keyStore.containsVaultKey(vector.vaultId), isFalse);
      expect(await fixture.selected.read(), isNull);
      await fixture.cleanup();
    }

    final selectedFixture = await _RealRecoveryFixture.create(vector);
    addTearDown(selectedFixture.cleanup);
    await selectedFixture.installProfile(
      const _RecoveryProfile(
        'selection',
        AtlasVaultRecoveryImportStage.keyCreated,
        _InstalledResources.selection,
      ),
    );
    expect(
      (await selectedFixture.coordinator.discardPendingImport()).disposition,
      AtlasVaultRecoveryImportDisposition.recoveryRequired,
    );
    expect(await selectedFixture.selected.read(), vector.vaultId);
    expect(
      await _hasPendingRecoveryImport(selectedFixture.importJournal),
      isTrue,
    );
    await selectedFixture.cleanup();

    final storeMismatch = await _RealRecoveryFixture.create(vector);
    addTearDown(storeMismatch.cleanup);
    await storeMismatch.installProfile(
      const _RecoveryProfile(
        'store-mismatch',
        AtlasVaultRecoveryImportStage.prepared,
        _InstalledResources.none,
      ),
    );
    final mismatchedStore =
        vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
          ...storeMismatch.localStoreValue.toJson(),
          'updated_at': '2026-07-29T03:04:06Z',
        });
    await storeMismatch.localStore.create(vector.vaultId, mismatchedStore);
    expect(
      (await storeMismatch.coordinator.discardPendingImport()).disposition,
      AtlasVaultRecoveryImportDisposition.recoveryRequired,
    );
    expect(await storeMismatch.localStore.read(vector.vaultId), isNotNull);
    expect(
      await _hasPendingRecoveryImport(storeMismatch.importJournal),
      isTrue,
    );
    await storeMismatch.cleanup();

    final keyMismatch = await _RealRecoveryFixture.create(vector);
    addTearDown(keyMismatch.cleanup);
    await keyMismatch.installProfile(
      const _RecoveryProfile(
        'key-mismatch',
        AtlasVaultRecoveryImportStage.storeCreated,
        _InstalledResources.store,
      ),
    );
    await keyMismatch.keyStore.createVaultKey(vector.vaultId, Uint8List(32));
    expect(
      (await keyMismatch.coordinator.discardPendingImport()).disposition,
      AtlasVaultRecoveryImportDisposition.recoveryRequired,
    );
    expect(await keyMismatch.keyStore.containsVaultKey(vector.vaultId), isTrue);
    expect(await _hasPendingRecoveryImport(keyMismatch.importJournal), isTrue);
    await keyMismatch.cleanup();
    tester.printToConsole(
      'AtlasVault Android pre-selection reset and digest fences passed.',
    );
  });

  testWidgets('Android import rejects unsafe and unauthenticated input', (
    tester,
  ) async {
    final vector = _RecoveryVector.load();

    final selectedFixture = await _RealRecoveryFixture.create(vector);
    addTearDown(selectedFixture.cleanup);
    await selectedFixture.selected.create(vector.vaultId);
    expect(
      (await selectedFixture.coordinator.prepareRecoveryImport()).disposition,
      AtlasVaultRecoveryImportDisposition.existingVault,
    );
    await selectedFixture.cleanup();

    final plaintextFixture = await _RealRecoveryFixture.create(
      vector,
      containsPlaintext: true,
    );
    addTearDown(plaintextFixture.cleanup);
    expect(
      (await plaintextFixture.coordinator.prepareRecoveryImport()).disposition,
      AtlasVaultRecoveryImportDisposition.migrationRequired,
    );
    expect(await plaintextFixture.importJournal.read(), isNull);
    await plaintextFixture.cleanup();

    final wrongKeyFixture = await _RealRecoveryFixture.create(vector);
    addTearDown(wrongKeyFixture.cleanup);
    await wrongKeyFixture.coordinator.prepareRecoveryImport();
    expect(
      (await wrongKeyFixture.coordinator.confirmRecoveryImport(
        'AVRK1-INVALID',
      )).disposition,
      AtlasVaultRecoveryImportDisposition.failed,
    );
    expect(await wrongKeyFixture.importJournal.read(), isNull);
    expect(await wrongKeyFixture.localStore.read(vector.vaultId), isNull);
    expect(
      await wrongKeyFixture.keyStore.containsVaultKey(vector.vaultId),
      isFalse,
    );
    await wrongKeyFixture.cleanup();

    final corruptFixture = await _RealRecoveryFixture.create(
      vector,
      pickedBytes: vector.tamperedExportBytes(),
    );
    addTearDown(corruptFixture.cleanup);
    await corruptFixture.coordinator.prepareRecoveryImport();
    expect(
      (await corruptFixture.coordinator.confirmRecoveryImport(
        vector.recoveryText,
      )).disposition,
      AtlasVaultRecoveryImportDisposition.failed,
    );
    expect(await corruptFixture.importJournal.read(), isNull);
    await corruptFixture.cleanup();
    tester.printToConsole(
      'AtlasVault Android import unsafe-input fences passed.',
    );
  });
}

enum _InstalledResources { none, store, key, selection }

final class _RecoveryProfile {
  const _RecoveryProfile(this.label, this.journalStage, this.resources);

  final String label;
  final AtlasVaultRecoveryImportStage journalStage;
  final _InstalledResources resources;
}

final class _RealRecoveryFixture {
  _RealRecoveryFixture._({
    required this.vector,
    required this.keyStore,
    required this.localStore,
    required this.selected,
    required this.importJournal,
    required this.runtime,
    required this.localStoreValue,
    required this.preparedJournal,
    required this.coordinator,
  });

  final _RecoveryVector vector;
  final AtlasAndroidVaultSecureKeyStore keyStore;
  final AtlasAndroidVaultLocalStoreIO localStore;
  final AtlasAndroidSelectedVaultStore selected;
  final AtlasAndroidProtectedRecoveryImportJournalStore importJournal;
  final AtlasVaultPrivateStateRuntime runtime;
  final vault.AtlasVaultLocalStore localStoreValue;
  final AtlasVaultRecoveryImportJournal preparedJournal;
  final AtlasVaultInteroperabilityCoordinator coordinator;

  static Future<_RealRecoveryFixture> create(
    _RecoveryVector vector, {
    bool containsPlaintext = false,
    Uint8List? pickedBytes,
  }) async {
    final keyStore = AtlasAndroidVaultSecureKeyStore();
    final localStore = AtlasAndroidVaultLocalStoreIO();
    final selected = AtlasAndroidSelectedVaultStore();
    final importJournal = AtlasAndroidProtectedRecoveryImportJournalStore();
    final migrationJournal = AtlasAndroidProtectedMigrationJournalStore();
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
    );
    await _cleanup(
      vector.vaultId,
      runtime: runtime,
      keyStore: keyStore,
      localStore: localStore,
      selected: selected,
      importJournal: importJournal,
    );
    expect(await migrationJournal.read(), isNull);
    const timestamp = '2026-07-29T03:04:05Z';
    const storeId = '50000000-0000-4000-8000-000000000501';
    final localStoreValue = vault.AtlasVaultLocalStore.fromJson(
      <String, Object?>{
        'format': vault.AtlasVaultLocalStore.format,
        'version': vault.AtlasVaultLocalStore.version,
        'store_id': storeId,
        'created_at': timestamp,
        'updated_at': timestamp,
        'vault_metadata': vector.export.vaultMetadata.toJson(),
        'records': <Object?>[
          for (final record in vector.export.records) record.toJson(),
        ],
      },
    );
    final localBytes = localStoreValue.canonicalBytes();
    final preparedJournal = AtlasVaultRecoveryImportJournal.prepared(
      importId: '50000000-0000-4000-8000-000000000502',
      exportId: vector.export.exportId,
      vaultId: vector.vaultId,
      storeId: storeId,
      createdAt: timestamp,
      exportSha256: await vault.atlasVaultSha256Hex(vector.exportBytes),
      localStoreSha256: await vault.atlasVaultSha256Hex(localBytes),
      vaultKeySha256: await vault.atlasVaultSha256Hex(vector.vaultKey),
    );
    localBytes.fillRange(0, localBytes.length, 0);
    final transport = _InputTransport(pickedBytes ?? vector.exportBytes);
    final coordinator = AtlasVaultInteroperabilityCoordinator(
      runtime: runtime,
      selectedVaultStore: selected,
      migrationJournalStore: migrationJournal,
      recoveryImportPending: () => _hasPendingRecoveryImport(importJournal),
      documentTransport: transport,
      recoveryImportJournalStore: importJournal,
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      inMemorySource: _RecoveryPlaintextSource(containsPlaintext),
      compatibilitySource: const _RecoveryCompatibilitySource(),
      cacheSource: const _RecoveryCacheSource(),
      now: () => DateTime.parse(timestamp),
      importIdProvider: () => preparedJournal.importId,
      importStoreIdProvider: () => storeId,
    );
    return _RealRecoveryFixture._(
      vector: vector,
      keyStore: keyStore,
      localStore: localStore,
      selected: selected,
      importJournal: importJournal,
      runtime: runtime,
      localStoreValue: localStoreValue,
      preparedJournal: preparedJournal,
      coordinator: coordinator,
    );
  }

  Future<void> installProfile(_RecoveryProfile profile) async {
    var journal = preparedJournal;
    while (journal.stage.index < profile.journalStage.index) {
      journal = journal.transitionedTo(
        AtlasVaultRecoveryImportStage.values[journal.stage.index + 1],
      );
    }
    await importJournal.create(journal.canonicalBytes());
    if (profile.resources.index >= _InstalledResources.store.index) {
      await localStore.create(vector.vaultId, localStoreValue);
    }
    if (profile.resources.index >= _InstalledResources.key.index) {
      await keyStore.createVaultKey(vector.vaultId, vector.vaultKey);
    }
    if (profile.resources.index >= _InstalledResources.selection.index) {
      await selected.create(vector.vaultId);
    }
  }

  Future<void> cleanup() {
    return _cleanup(
      vector.vaultId,
      runtime: runtime,
      keyStore: keyStore,
      localStore: localStore,
      selected: selected,
      importJournal: importJournal,
    );
  }
}

final class _RecoveryVector {
  _RecoveryVector({
    required this.vaultId,
    required this.recoveryText,
    required this.vaultKey,
    required this.exportBytes,
    required this.export,
  });

  final String vaultId;
  final String recoveryText;
  final Uint8List vaultKey;
  final Uint8List exportBytes;
  final vault.AtlasVaultEncryptedExport export;

  factory _RecoveryVector.load() {
    final root = loadAtlasVaultVector(
      'atlasvault_ios_flutter_interop_vectors_v1.json',
    );
    final value = atlasVaultObject(root['ios_to_flutter']);
    final exportBytes = Uint8List.fromList(
      base64Decode(value['canonical_encrypted_export_b64']! as String),
    );
    return _RecoveryVector(
      vaultId: value['vault_id']! as String,
      recoveryText: value['test_only_recovery_key_text']! as String,
      vaultKey: Uint8List.fromList(
        base64Decode(value['test_only_vault_key_b64']! as String),
      ),
      exportBytes: exportBytes,
      export: vault.AtlasVaultEncryptedExport.decodeJson(
        utf8.decode(exportBytes),
      ),
    );
  }

  Uint8List tamperedExportBytes() {
    final source =
        jsonDecode(utf8.decode(exportBytes))! as Map<String, Object?>;
    final records = List<Object?>.from(source['records']! as List<Object?>);
    final record = Map<String, Object?>.from(
      records.first! as Map<String, Object?>,
    );
    final ciphertext = base64Decode(record['ciphertext']! as String);
    ciphertext[0] ^= 1;
    record['ciphertext'] = base64Encode(ciphertext);
    records[0] = record;
    source['records'] = records;
    return vault.AtlasVaultEncryptedExport.fromJson(source).canonicalBytes();
  }
}

final class _InputTransport implements AtlasVaultEncryptedDocumentTransport {
  const _InputTransport(this.bytes);

  final Uint8List bytes;

  @override
  Future<Uint8List?> pickEncryptedExport() async => Uint8List.fromList(bytes);

  @override
  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes) {
    throw StateError('Recovery integration does not export.');
  }
}

final class _RecoveryPlaintextSource implements AtlasVaultPlaintextStateSource {
  const _RecoveryPlaintextSource(this.containsPrivateState);

  final bool containsPrivateState;

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async {
    return AtlasVaultPlaintextPrivateState(
      savedSearches: containsPrivateState
          ? <AtlasSavedSearch>[
              AtlasSavedSearch(
                name: 'FAKE_MIGRATION_REQUIRED',
                request: const AtlasSearchRequest(text: 'FAKE_PRIVATE'),
              ),
            ]
          : const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
    );
  }
}

final class _RecoveryCompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  const _RecoveryCompatibilitySource();

  @override
  Uri get authorityBaseURL => Uri.parse('https://example.invalid/');

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async {
    return AtlasVaultPlaintextPrivateState(
      savedSearches: const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
    );
  }

  @override
  Future<bool> deleteSavedSearch(String name) {
    throw StateError('Import must not mutate compatibility state.');
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) {
    throw StateError('Import must not mutate compatibility state.');
  }
}

final class _RecoveryCacheSource implements AtlasLocalCacheMigrationSource {
  const _RecoveryCacheSource();

  @override
  Future<AtlasLocalCacheMigrationPrivateState>
  readPrivateStateForMigration() async {
    return AtlasLocalCacheMigrationPrivateState(
      savedSearches: const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
      privateSha256: null,
      cachePresent: false,
    );
  }

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) {
    throw StateError('Import must not mutate cache.');
  }
}

Future<void> _cleanup(
  String vaultId, {
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasAndroidVaultSecureKeyStore keyStore,
  required AtlasAndroidVaultLocalStoreIO localStore,
  required AtlasAndroidSelectedVaultStore selected,
  required AtlasAndroidProtectedRecoveryImportJournalStore importJournal,
}) async {
  if (runtime.isActive) {
    await runtime.deactivate();
  }
  final selectedVault = await selected.read();
  if (selectedVault == vaultId) {
    await selected.clear(vaultId);
  } else if (selectedVault != null) {
    throw StateError('Unexpected selected vault in test storage.');
  }
  if (await localStore.read(vaultId) != null) {
    await localStore.delete(vaultId);
  }
  if (await keyStore.containsVaultKey(vaultId)) {
    await keyStore.deleteVaultKey(vaultId);
  }
  final journalBytes = await importJournal.read();
  if (journalBytes != null) {
    try {
      await importJournal.delete(
        expectedSha256: await vault.atlasVaultSha256Hex(journalBytes),
      );
    } finally {
      journalBytes.fillRange(0, journalBytes.length, 0);
    }
  }
}

Future<bool> _hasPendingRecoveryImport(
  AtlasAndroidProtectedRecoveryImportJournalStore store,
) async {
  final bytes = await store.read();
  try {
    return bytes != null;
  } finally {
    bytes?.fillRange(0, bytes.length, 0);
  }
}
