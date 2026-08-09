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

  testWidgets(
    'Android installs and re-exports an Apple-origin encrypted vault',
    (tester) async {
      await _runImportAndReexport(tester, _AndroidInteropVector.loadApple());
    },
  );

  testWidgets(
    'Android installs and re-exports a Windows-origin encrypted vault',
    (tester) async {
      await _runImportAndReexport(tester, _AndroidInteropVector.loadWindows());
    },
  );
}

Future<void> _runImportAndReexport(
  WidgetTester tester,
  _AndroidInteropVector vector,
) async {
  final keyStore = AtlasAndroidVaultSecureKeyStore();
  final localStore = AtlasAndroidVaultLocalStoreIO();
  final selected = AtlasAndroidSelectedVaultStore();
  final migrationJournal = AtlasAndroidProtectedMigrationJournalStore();
  final importJournal = AtlasAndroidProtectedRecoveryImportJournalStore();
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
  addTearDown(() async {
    await _cleanup(
      vector.vaultId,
      runtime: runtime,
      keyStore: keyStore,
      localStore: localStore,
      selected: selected,
      importJournal: importJournal,
    );
  });
  expect(await migrationJournal.read(), isNull);
  final compatibility = _EmptyCompatibilitySource();
  final transport = _MemoryDocumentTransport(vector.exportBytes);
  final coordinator = AtlasVaultInteroperabilityCoordinator(
    runtime: runtime,
    selectedVaultStore: selected,
    migrationJournalStore: migrationJournal,
    recoveryImportPending: () => _hasPendingRecoveryImport(importJournal),
    documentTransport: transport,
    recoveryImportJournalStore: importJournal,
    secureKeyStore: keyStore,
    localStoreIO: localStore,
    inMemorySource: const _EmptyPlaintextSource(),
    compatibilitySource: compatibility,
    cacheSource: const _EmptyCacheSource(),
    now: () => DateTime.parse('2026-07-29T03:04:05Z'),
    uuidProvider: () => '40000000-0000-4000-8000-000000000401',
    importIdProvider: () => '40000000-0000-4000-8000-000000000402',
    importStoreIdProvider: () => '40000000-0000-4000-8000-000000000403',
  );

  expect(await selected.read(), isNull);
  expect(await importJournal.read(), isNull);
  final prepared = await coordinator.prepareRecoveryImport();
  expect(
    prepared.disposition,
    AtlasVaultRecoveryImportDisposition.importPrepared,
  );
  final imported = await coordinator.confirmRecoveryImport(vector.recoveryText);

  expect(
    imported.disposition,
    AtlasVaultRecoveryImportDisposition.importedAndActive,
  );
  expect(await selected.read(), vector.vaultId);
  expect(await importJournal.read(), isNull);
  expect(await keyStore.containsVaultKey(vector.vaultId), isTrue);
  final installed = (await localStore.read(vector.vaultId))!;
  expect(installed.records, hasLength(vector.expectedRecordCount));
  expect(
    installed.records.map((record) => jsonEncode(record.toJson())),
    orderedEquals(
      vector.export.records.map((record) => jsonEncode(record.toJson())),
    ),
  );
  expect(
    installed.records.where((record) => record.deleted),
    hasLength(vector.expectedTombstoneCount),
  );
  final snapshot = await runtime.read();
  expect(snapshot.savedSearches, hasLength(vector.expectedSavedSearchCount));
  expect(snapshot.trackerRecords, hasLength(vector.expectedTrackerCount));
  expect(
    vector.expectedRecordCount -
        vector.expectedTombstoneCount -
        snapshot.savedSearches.length -
        snapshot.trackerRecords.length,
    vector.expectedHiddenRecordCount,
  );
  final storeText = utf8.decode(installed.canonicalBytes());
  for (final sentinel in vector.privateSentinels) {
    expect(storeText, isNot(contains(sentinel)), reason: sentinel);
  }
  expect(compatibility.deleteCalls, 0);

  final exportPrepared = await coordinator.prepareExistingRecoveryExport(
    vector.recoveryText,
  );
  expect(
    exportPrepared.disposition,
    AtlasVaultRecoveryExportDisposition.exportReady,
  );
  expect(
    (await coordinator.savePreparedExport()).disposition,
    AtlasVaultRecoveryExportDisposition.saved,
  );
  final reexported = vault.AtlasVaultEncryptedExport.decodeJson(
    utf8.decode(transport.savedBytes!),
  );
  expect(reexported.vaultMetadata.vaultId, vector.vaultId);
  expect(reexported.records, installed.records);
  expect(reexported.canonicalBytes(), orderedEquals(transport.savedBytes!));
  expect(compatibility.deleteCalls, 0);
  tester.printToConsole('AtlasVault Android ${vector.caseId} passed.');
}

final class _AndroidInteropVector {
  _AndroidInteropVector({
    required this.caseId,
    required this.vaultId,
    required this.recoveryText,
    required this.exportBytes,
    required this.export,
    required this.expectedRecordCount,
    required this.expectedSavedSearchCount,
    required this.expectedTrackerCount,
    required this.expectedTombstoneCount,
    required this.expectedHiddenRecordCount,
    required this.privateSentinels,
  });

  final String caseId;
  final String vaultId;
  final String recoveryText;
  final Uint8List exportBytes;
  final vault.AtlasVaultEncryptedExport export;
  final int expectedRecordCount;
  final int expectedSavedSearchCount;
  final int expectedTrackerCount;
  final int expectedTombstoneCount;
  final int expectedHiddenRecordCount;
  final List<String> privateSentinels;

  factory _AndroidInteropVector.loadApple() => _AndroidInteropVector._load(
    fileName: 'atlasvault_ios_flutter_interop_vectors_v1.json',
    caseName: 'ios_to_flutter',
    privateSentinels: const <String>[
      'FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK',
      'FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK',
      'FAKE_SAVED_ONLY_JOB_KEY_DO_NOT_LEAK',
      'saved_search',
      'saved_job',
    ],
  );

  factory _AndroidInteropVector.loadWindows() => _AndroidInteropVector._load(
    fileName: 'atlasvault_windows_interop_vectors_v1.json',
    caseName: 'windows_to_apple_android',
  );

  factory _AndroidInteropVector._load({
    required String fileName,
    required String caseName,
    List<String>? privateSentinels,
  }) {
    final root = loadAtlasVaultVector(fileName);
    final value = atlasVaultObject(root[caseName]);
    final bytes = Uint8List.fromList(
      base64Decode(value['canonical_encrypted_export_b64']! as String),
    );
    final expectedPayloadValues = value['fake_expected_payload_values'];
    return _AndroidInteropVector(
      caseId: value['case_id'] as String? ?? caseName,
      vaultId: value['vault_id']! as String,
      recoveryText: value['test_only_recovery_key_text']! as String,
      exportBytes: bytes,
      export: vault.AtlasVaultEncryptedExport.decodeJson(utf8.decode(bytes)),
      expectedRecordCount: value['expected_encrypted_record_count']! as int,
      expectedSavedSearchCount:
          value['expected_active_saved_search_count']! as int,
      expectedTrackerCount:
          value['expected_active_tracker_saved_job_count']! as int,
      expectedTombstoneCount: value['expected_tombstone_count']! as int,
      expectedHiddenRecordCount:
          value['expected_preserved_other_private_record_count']! as int,
      privateSentinels:
          privateSentinels ??
          (expectedPayloadValues is List<dynamic>
              ? expectedPayloadValues.cast<String>()
              : const <String>[]),
    );
  }
}

final class _MemoryDocumentTransport
    implements AtlasVaultEncryptedDocumentTransport {
  _MemoryDocumentTransport(this.pickedBytes);

  final Uint8List pickedBytes;
  Uint8List? savedBytes;

  @override
  Future<Uint8List?> pickEncryptedExport() async =>
      Uint8List.fromList(pickedBytes);

  @override
  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes) async {
    savedBytes = Uint8List.fromList(canonicalExportBytes);
    return true;
  }
}

final class _EmptyCompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  int deleteCalls = 0;

  @override
  Uri get authorityBaseURL => Uri.parse('https://example.invalid/');

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async => _emptyPlaintextState();

  @override
  Future<bool> deleteSavedSearch(String name) async {
    deleteCalls += 1;
    return false;
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) async {
    deleteCalls += 1;
    return false;
  }
}

final class _EmptyPlaintextSource implements AtlasVaultPlaintextStateSource {
  const _EmptyPlaintextSource();

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async =>
      _emptyPlaintextState();
}

final class _EmptyCacheSource implements AtlasLocalCacheMigrationSource {
  const _EmptyCacheSource();

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
    throw StateError('Import must not mutate plaintext cache.');
  }
}

AtlasVaultPlaintextPrivateState _emptyPlaintextState() {
  return AtlasVaultPlaintextPrivateState(
    savedSearches: const <AtlasSavedSearch>[],
    trackerRecords: const <AtlasApplicationRecord>[],
  );
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
