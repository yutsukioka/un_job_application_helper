import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:atlas/features/app_shell/atlas_cache_location.dart';
import 'package:atlas/src/atlas_vault/plaintext_migration.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/support/atlas_vault_vector_loader.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Windows installs Apple and Android encrypted artifacts', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    for (final caseName in <String>['apple_to_windows', 'android_to_windows']) {
      final vector = _WindowsInteropVector.load(caseName);
      final cacheRoot = await Directory.systemTemp.createTemp(
        'atlas_windows_interop_${vector.caseId}_',
      );
      final location = _cacheLocation(cacheRoot);
      final keyStore = AtlasWindowsVaultSecureKeyStore();
      final localStore = AtlasWindowsVaultLocalStoreIO();
      final selected = AtlasWindowsSelectedVaultStore();
      final migrationJournal = AtlasWindowsProtectedMigrationJournalStore();
      final importJournal = AtlasWindowsProtectedRecoveryImportJournalStore();
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: keyStore,
        localStoreIO: localStore,
      );
      final compatibility = _EmptyCompatibilitySource();
      final transport = _ArtifactDocumentTransport(
        pickedBytes: await _directArtifactOrVector(vector),
      );
      final authorityAdmission = AtlasWindowsPlaintextAuthorityAdmission(
        locationProvider: () async => location,
        journalStore: migrationJournal,
        recoveryImportJournalStore: importJournal,
        selectedVaultStore: selected,
      );

      await _cleanup(
        vector.vaultId,
        runtime: runtime,
        keyStore: keyStore,
        localStore: localStore,
        selected: selected,
        importJournal: importJournal,
      );
      try {
        final coordinator = AtlasVaultInteroperabilityCoordinator(
          runtime: runtime,
          selectedVaultStore: selected,
          migrationJournalStore: migrationJournal,
          recoveryImportPending: () => _journalExists(importJournal),
          documentTransport: transport,
          recoveryImportJournalStore: importJournal,
          secureKeyStore: keyStore,
          localStoreIO: localStore,
          inMemorySource: const _EmptyPlaintextSource(),
          compatibilitySource: compatibility,
          cacheSource: AtlasWindowsDesktopCacheMigrationSource(location),
          importTransactionAdmission: authorityAdmission,
          recoveryImportProfile: AtlasVaultRecoveryImportProfile.windows,
          now: () => DateTime.utc(2026, 8, 9, 3, 4, 5),
          importIdProvider: () => vector.importId,
          importStoreIdProvider: () => vector.importStoreId,
        );

        final prepared = await coordinator.prepareRecoveryImport();
        expect(
          prepared.disposition,
          AtlasVaultRecoveryImportDisposition.importPrepared,
          reason: caseName,
        );
        expect(prepared.encryptedRecordCount, vector.expectedRecordCount);
        expect(await importJournal.read(), isNull);

        final installed = await coordinator.confirmRecoveryImport(
          vector.recoveryText,
        );
        expect(
          installed.disposition,
          AtlasVaultRecoveryImportDisposition.importedAndActive,
          reason: caseName,
        );
        expect(await importJournal.read(), isNull);
        expect(await selected.read(), vector.vaultId);
        expect(runtime.isActiveVault(vector.vaultId), isTrue);

        final restoredStore = await localStore.read(vector.vaultId);
        expect(restoredStore, isNotNull);
        expect(restoredStore!.storeId, vector.importStoreId);
        expect(restoredStore.storeId, isNot(vector.sourceStoreId));
        expect(
          restoredStore.records.map((record) => record.toJson()).toList(),
          vector.export.records.map((record) => record.toJson()).toList(),
        );
        expect(
          restoredStore.records.where((record) => record.deleted),
          hasLength(vector.expectedTombstoneCount),
        );

        final restoredKey = await keyStore.loadVaultKey(vector.vaultId);
        expect(restoredKey, orderedEquals(vector.vaultKey));
        restoredKey?.fillRange(0, restoredKey.length, 0);
        final snapshot = await runtime.read();
        expect(
          snapshot.savedSearches,
          hasLength(vector.expectedSavedSearchCount),
        );
        expect(snapshot.trackerRecords, hasLength(vector.expectedTrackerCount));
        expect(
          vector.expectedHiddenRecordCount,
          restoredStore.records.where((record) => !record.deleted).length -
              snapshot.savedSearches.length -
              snapshot.trackerRecords.length,
        );
        final localBytes = restoredStore.canonicalBytes();
        try {
          final encoded = utf8.decode(localBytes);
          for (final sentinel in vector.privateSentinels) {
            expect(encoded, isNot(contains(sentinel)), reason: caseName);
          }
        } finally {
          localBytes.fillRange(0, localBytes.length, 0);
        }
        expect(compatibility.deleteCalls, 0);
        expect(await location.cacheFile.exists(), isFalse);
        expect(await location.legacyFile.exists(), isFalse);
        await coordinator.stop();
      } finally {
        await _cleanup(
          vector.vaultId,
          runtime: runtime,
          keyStore: keyStore,
          localStore: localStore,
          selected: selected,
          importJournal: importJournal,
        );
        vector.destroy();
        if (await cacheRoot.exists()) {
          await cacheRoot.delete(recursive: true);
        }
      }
      tester.printToConsole(
        'Windows installed the exact $caseName encrypted artifact.',
      );
    }
  });

  testWidgets('Windows exports the canonical three-platform artifact', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    final vector = _WindowsInteropVector.load('windows_to_apple_android');
    final keyStore = AtlasWindowsVaultSecureKeyStore();
    final localStore = AtlasWindowsVaultLocalStoreIO();
    final selected = AtlasWindowsSelectedVaultStore();
    final migrationJournal = AtlasWindowsProtectedMigrationJournalStore();
    final importJournal = AtlasWindowsProtectedRecoveryImportJournalStore();
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
    );
    final transport = _ArtifactDocumentTransport();

    await _cleanup(
      vector.vaultId,
      runtime: runtime,
      keyStore: keyStore,
      localStore: localStore,
      selected: selected,
      importJournal: importJournal,
    );
    try {
      expect(await migrationJournal.read(), isNull);
      await keyStore.createVaultKey(vector.vaultId, vector.vaultKey);
      await localStore.create(vector.vaultId, vector.sourceLocalStore);
      await selected.create(vector.vaultId);
      expect(
        await runtime.activateExisting(vector.vaultId),
        AtlasVaultActivationResult.activated,
      );

      final coordinator = AtlasVaultInteroperabilityCoordinator(
        runtime: runtime,
        selectedVaultStore: selected,
        migrationJournalStore: migrationJournal,
        recoveryImportPending: () => _journalExists(importJournal),
        documentTransport: transport,
        now: () => DateTime.parse(vector.export.createdAt),
        uuidProvider: () => vector.export.exportId,
      );
      final prepared = await coordinator.prepareExistingRecoveryExport(
        vector.recoveryText,
      );
      expect(
        prepared.disposition,
        AtlasVaultRecoveryExportDisposition.exportReady,
      );
      final saved = await coordinator.savePreparedExport();
      expect(saved.disposition, AtlasVaultRecoveryExportDisposition.saved);
      expect(transport.savedBytes, orderedEquals(vector.exportBytes));
      expect(
        await atlasVaultSha256Hex(transport.savedBytes!),
        vector.exportSha256,
      );
      for (final sentinel in vector.privateSentinels) {
        expect(utf8.decode(transport.savedBytes!), isNot(contains(sentinel)));
      }
      await coordinator.stop();
      tester.printToConsole(
        'Windows AtlasVault canonical recovery export passed.',
      );
    } finally {
      await _cleanup(
        vector.vaultId,
        runtime: runtime,
        keyStore: keyStore,
        localStore: localStore,
        selected: selected,
        importJournal: importJournal,
      );
      vector.destroy();
    }
  });
}

final class _WindowsInteropVector {
  _WindowsInteropVector({
    required this.caseId,
    required this.vaultId,
    required this.recoveryText,
    required this.vaultKey,
    required this.exportBytes,
    required this.exportSha256,
    required this.export,
    required this.sourceStoreId,
    required this.importId,
    required this.importStoreId,
    required this.expectedRecordCount,
    required this.expectedSavedSearchCount,
    required this.expectedTrackerCount,
    required this.expectedHiddenRecordCount,
    required this.expectedTombstoneCount,
    required this.privateSentinels,
  });

  final String caseId;
  final String vaultId;
  final String recoveryText;
  final Uint8List vaultKey;
  final Uint8List exportBytes;
  final String exportSha256;
  final AtlasVaultEncryptedExport export;
  final String sourceStoreId;
  final String importId;
  final String importStoreId;
  final int expectedRecordCount;
  final int expectedSavedSearchCount;
  final int expectedTrackerCount;
  final int expectedHiddenRecordCount;
  final int expectedTombstoneCount;
  final List<String> privateSentinels;

  AtlasVaultLocalStore get sourceLocalStore =>
      AtlasVaultLocalStore.fromJson(<String, Object?>{
        'format': 'atlasvault-local-store',
        'version': 1,
        'store_id': sourceStoreId,
        'created_at': export.createdAt,
        'updated_at': export.createdAt,
        'vault_metadata': export.vaultMetadata.toJson(),
        'records': <Object?>[
          for (final record in export.records) record.toJson(),
        ],
      });

  factory _WindowsInteropVector.load(String caseName) {
    final root = loadAtlasVaultVector(
      'atlasvault_windows_interop_vectors_v1.json',
    );
    final value = atlasVaultObject(root[caseName]);
    final exportBytes = Uint8List.fromList(
      base64Decode(value['canonical_encrypted_export_b64']! as String),
    );
    final export = AtlasVaultEncryptedExport.decodeJson(
      utf8.decode(exportBytes),
    );
    final payloadValues = atlasVaultObject(value['expected_payload_values']);
    final caseIndex = switch (caseName) {
      'apple_to_windows' => 1,
      'android_to_windows' => 2,
      _ => 3,
    };
    return _WindowsInteropVector(
      caseId: value['case_id']! as String,
      vaultId: value['vault_id']! as String,
      recoveryText: value['test_only_recovery_key_text']! as String,
      vaultKey: Uint8List.fromList(
        base64Decode(value['test_only_vault_key_b64']! as String),
      ),
      exportBytes: exportBytes,
      exportSha256: value['canonical_encrypted_export_sha256']! as String,
      export: export,
      sourceStoreId: value['local_source_store_id']! as String,
      importId: '71000000-0000-4000-8000-00000000000$caseIndex',
      importStoreId: '72000000-0000-4000-8000-00000000000$caseIndex',
      expectedRecordCount: value['expected_encrypted_record_count']! as int,
      expectedSavedSearchCount:
          value['expected_active_saved_search_count']! as int,
      expectedTrackerCount:
          value['expected_active_tracker_saved_job_count']! as int,
      expectedHiddenRecordCount:
          value['expected_preserved_other_private_record_count']! as int,
      expectedTombstoneCount: value['expected_tombstone_count']! as int,
      privateSentinels: <String>[
        for (final entry in payloadValues.entries)
          if (!entry.key.endsWith('_record_id') && entry.value is String)
            entry.value! as String,
      ],
    );
  }

  void destroy() {
    vaultKey.fillRange(0, vaultKey.length, 0);
    exportBytes.fillRange(0, exportBytes.length, 0);
  }
}

final class _ArtifactDocumentTransport
    implements AtlasVaultEncryptedDocumentTransport {
  _ArtifactDocumentTransport({this.pickedBytes});

  final Uint8List? pickedBytes;
  Uint8List? savedBytes;

  @override
  Future<Uint8List?> pickEncryptedExport() async =>
      pickedBytes == null ? null : Uint8List.fromList(pickedBytes!);

  @override
  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes) async {
    savedBytes = Uint8List.fromList(canonicalExportBytes);
    final artifactDirectory =
        Platform.environment['ATLAS_INTEROP_ARTIFACT_DIR'];
    if (artifactDirectory != null && artifactDirectory.isNotEmpty) {
      final directory = Directory(artifactDirectory);
      await directory.create(recursive: true);
      final artifact = File(
        '${directory.path}${Platform.pathSeparator}'
        'windows-to-apple-android.atlasvault',
      );
      await artifact.writeAsBytes(savedBytes!, flush: true);
      final digest = await atlasVaultSha256Hex(savedBytes!);
      await File(
        '${directory.path}${Platform.pathSeparator}'
        'windows-to-apple-android.sha256',
      ).writeAsString('$digest\n', flush: true);
    }
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

AtlasVaultPlaintextPrivateState _emptyPlaintextState() {
  return AtlasVaultPlaintextPrivateState(
    savedSearches: const <AtlasSavedSearch>[],
    trackerRecords: const <AtlasApplicationRecord>[],
  );
}

AtlasPersistentCacheLocation _cacheLocation(Directory root) {
  return AtlasPersistentCacheLocation(
    cacheFile: File(
      '${root.path}${Platform.pathSeparator}durable'
      '${Platform.pathSeparator}$atlasLocalCacheFileName',
    ),
    legacyFile: File(
      '${root.path}${Platform.pathSeparator}legacy'
      '${Platform.pathSeparator}$atlasLocalCacheFileName',
    ),
    legacyImportRetiredFile: File(
      '${root.path}${Platform.pathSeparator}durable'
      '${Platform.pathSeparator}$atlasLegacyImportRetiredFileName',
    ),
  );
}

Future<Uint8List> _directArtifactOrVector(_WindowsInteropVector vector) async {
  final artifactDirectory = Platform.environment['ATLAS_INTEROP_ARTIFACT_DIR'];
  if (artifactDirectory == null || artifactDirectory.isEmpty) {
    return Uint8List.fromList(vector.exportBytes);
  }
  final fileName = vector.caseId.startsWith('apple-to-windows')
      ? 'apple-to-windows.atlasvault'
      : vector.caseId.startsWith('android-to-windows')
      ? 'android-to-windows.atlasvault'
      : throw StateError('Unexpected interoperability vector.');
  final file = File('$artifactDirectory${Platform.pathSeparator}$fileName');
  expect(await file.exists(), isTrue, reason: vector.caseId);
  final bytes = await file.readAsBytes();
  expect(bytes, orderedEquals(vector.exportBytes), reason: vector.caseId);
  final digest = await atlasVaultSha256Hex(bytes);
  expect(digest, vector.exportSha256, reason: vector.caseId);
  final digestFile = File(
    '$artifactDirectory${Platform.pathSeparator}'
    '${fileName.substring(0, fileName.length - '.atlasvault'.length)}.sha256',
  );
  expect(await digestFile.exists(), isTrue, reason: vector.caseId);
  expect((await digestFile.readAsString()).trim(), digest);
  return bytes;
}

Future<bool> _journalExists(
  AtlasWindowsProtectedRecoveryImportJournalStore store,
) async {
  final bytes = await store.read();
  try {
    return bytes != null;
  } finally {
    bytes?.fillRange(0, bytes.length, 0);
  }
}

Future<void> _cleanup(
  String vaultId, {
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasWindowsVaultSecureKeyStore keyStore,
  required AtlasWindowsVaultLocalStoreIO localStore,
  required AtlasWindowsSelectedVaultStore selected,
  required AtlasWindowsProtectedRecoveryImportJournalStore importJournal,
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
        expectedSha256: await atlasVaultSha256Hex(journalBytes),
      );
    } finally {
      journalBytes.fillRange(0, journalBytes.length, 0);
    }
  }
}
