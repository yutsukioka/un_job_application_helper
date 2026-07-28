import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'Android migration resumes after an interrupted remote deletion',
    (tester) async {
      final suffix = DateTime.now().microsecondsSinceEpoch
          .toRadixString(16)
          .padLeft(12, '0')
          .substring(0, 12);
      final identifiers = <String>[
        _uuid('11000000', suffix),
        _uuid('22000000', suffix),
        _uuid('33000000', suffix),
        _uuid('44000000', suffix),
        _uuid('55000000', suffix),
        _uuid('66000000', suffix),
        _uuid('77000000', suffix),
      ];
      final search = AtlasSavedSearch(
        name: 'Recovery fixture',
        request: const AtlasSearchRequest(text: 'RECOVERY_PRIVATE_QUERY'),
        createdAt: '2026-07-29T03:00:00Z',
        updatedAt: '2026-07-29T03:01:00Z',
      );
      final tracker = AtlasApplicationRecord(
        id: 'recovery-record-$suffix',
        jobKey: 'recovery:$suffix',
        status: 'saved',
        notes: 'RECOVERY_PRIVATE_NOTES',
        updatedAt: '2026-07-29T03:02:00Z',
      );
      final initialState = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[search],
        trackerRecords: <AtlasApplicationRecord>[tracker],
      );
      final cacheDirectory = await Directory.systemTemp.createTemp(
        'atlas_migration_recovery_',
      );
      final cacheStore = AtlasLocalCacheStore(
        file: File('${cacheDirectory.path}/atlas-local-cache.json'),
        now: () => DateTime.utc(2026, 7, 29, 4),
      );
      await cacheStore.write(
        _snapshot(
          savedAt: DateTime.utc(2026, 7, 29, 3),
          savedSearches: <AtlasSavedSearch>[search],
          trackerRecords: <AtlasApplicationRecord>[tracker],
        ),
      );

      final keyStore = AtlasAndroidVaultSecureKeyStore();
      final localStore = AtlasAndroidVaultLocalStoreIO();
      final journalStore = AtlasAndroidProtectedMigrationJournalStore();
      final selectedStore = AtlasAndroidSelectedVaultStore();
      final memory = _RecoveryMemorySource(initialState);
      final compatibility = _RecoveryCompatibilitySource(initialState);
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: keyStore,
        localStoreIO: localStore,
      );
      final authority = _RecoveryPrivateAuthority(
        memory: memory,
        runtime: runtime,
      );
      AtlasVaultPlaintextMigrationCoordinator buildCoordinator() {
        return AtlasVaultPlaintextMigrationCoordinator(
          inMemorySource: memory,
          compatibilitySource: compatibility,
          cacheSource: AtlasLocalCacheMigrationStoreSource(cacheStore),
          journalStore: journalStore,
          selectedVaultStore: selectedStore,
          secureKeyStore: keyStore,
          localStoreIO: localStore,
          privateAuthority: authority,
          now: () => DateTime.utc(2026, 7, 29, 4, 5, 6),
          uuidProvider: () => identifiers.removeAt(0),
          vaultKeyProvider: () =>
              Uint8List.fromList(List<int>.generate(32, (index) => index + 21)),
          nonceProvider: () =>
              Uint8List.fromList(List<int>.generate(12, (index) => index + 31)),
        );
      }

      String? vaultId;
      addTearDown(() async {
        await runtime.deactivate();
        final journalBytes = await journalStore.read();
        if (journalBytes != null) {
          try {
            final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
              journalBytes,
            );
            vaultId ??= journal.vaultId;
            await journalStore.delete(
              expectedSha256: await vault.atlasVaultSha256Hex(journalBytes),
              allowAbsent: true,
            );
          } finally {
            journalBytes.fillRange(0, journalBytes.length, 0);
          }
        }
        final selected = await selectedStore.read();
        if (selected != null) {
          vaultId ??= selected;
          await selectedStore.clear(selected);
        }
        if (vaultId != null) {
          await localStore.delete(vaultId!);
          await keyStore.deleteVaultKey(vaultId!);
        }
        if (await cacheDirectory.exists()) {
          await cacheDirectory.delete(recursive: true);
        }
      });

      final first = buildCoordinator();
      await first.inventory();
      await first.prepare();
      final journalBytes = await journalStore.read();
      expect(journalBytes, isNotNull);
      final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        journalBytes!,
      );
      journalBytes.fillRange(0, journalBytes.length, 0);
      vaultId = journal.vaultId;
      compatibility.failAfterNextSavedSearchDelete = true;

      await expectLater(
        first.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(compatibility.state.savedSearches, isEmpty);
      expect(await journalStore.read(), isNotNull);
      expect(await selectedStore.read(), isNull);

      final resumed = buildCoordinator();
      await resumed.resume();

      expect(compatibility.state.savedSearches, isEmpty);
      expect(compatibility.state.trackerRecords, isEmpty);
      expect(
        (await cacheStore.readPrivateStateForMigration()).privateSha256,
        isNull,
      );
      expect(await selectedStore.read(), vaultId);
      expect(runtime.isActive, isTrue);
      expect((await runtime.read()).savedSearches.single.name, search.name);
      expect(
        (await runtime.read()).trackerRecords.single.jobKey,
        tracker.jobKey,
      );
      expect(await journalStore.read(), isNull);
      tester.printToConsole(
        'AtlasVault Android interrupted migration recovery passed.',
      );
    },
  );
}

String _uuid(String firstGroup, String suffix) {
  return '$firstGroup-0000-4000-8000-$suffix';
}

AtlasLocalCacheSnapshot _snapshot({
  required DateTime savedAt,
  required List<AtlasSavedSearch> savedSearches,
  required List<AtlasApplicationRecord> trackerRecords,
}) {
  return AtlasLocalCacheSnapshot(
    schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
    baseURL: Uri.parse('http://127.0.0.1:8765'),
    savedAt: savedAt,
    searchRequest: const AtlasSearchRequest(),
    searchResponse: AtlasSearchResponse(
      total: 0,
      limit: 50,
      offset: 0,
      results: const <JobSearchResult>[],
      facets: const <String, Map<String, int>>{},
      facetLabels: const <String, Map<String, String>>{},
      unclassifiedCount: 0,
    ),
    savedSearches: savedSearches,
    trackerRecords: trackerRecords,
  );
}

final class _RecoveryMemorySource implements AtlasVaultPlaintextStateSource {
  _RecoveryMemorySource(this.state);

  AtlasVaultPlaintextPrivateState state;

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async {
    return state;
  }
}

final class _RecoveryCompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  _RecoveryCompatibilitySource(this.state);

  AtlasVaultPlaintextPrivateState state;
  bool failAfterNextSavedSearchDelete = false;

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async {
    return state;
  }

  @override
  Future<bool> deleteSavedSearch(String name) async {
    final found = state.savedSearches.any((value) => value.name == name);
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches
          .where((value) => value.name != name)
          .toList(growable: false),
      trackerRecords: state.trackerRecords,
    );
    if (failAfterNextSavedSearchDelete) {
      failAfterNextSavedSearchDelete = false;
      throw StateError('interrupted');
    }
    return found;
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) async {
    final found = state.trackerRecords.any((value) => value.id == recordId);
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches,
      trackerRecords: state.trackerRecords
          .where((value) => value.id != recordId)
          .toList(growable: false),
    );
    return found;
  }
}

final class _RecoveryPrivateAuthority
    implements AtlasVaultPlaintextMigrationPrivateAuthority {
  _RecoveryPrivateAuthority({required this.memory, required this.runtime});

  final _RecoveryMemorySource memory;
  final AtlasVaultPrivateStateRuntime runtime;

  @override
  bool get isEncryptedPrivateStateActive => runtime.isActive;

  @override
  void hideLegacyPrivateState() {
    memory.state = AtlasVaultPlaintextPrivateState(
      savedSearches: const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
    );
  }

  @override
  Future<bool> activateEncryptedPrivateState(String vaultId) async {
    return await runtime.activateExisting(vaultId) ==
        AtlasVaultActivationResult.activated;
  }

  @override
  Future<AtlasVaultPlaintextPrivateState> readEncryptedPrivateState() async {
    final snapshot = await runtime.read();
    return AtlasVaultPlaintextPrivateState(
      savedSearches: snapshot.savedSearches,
      trackerRecords: snapshot.trackerRecords,
    );
  }
}
