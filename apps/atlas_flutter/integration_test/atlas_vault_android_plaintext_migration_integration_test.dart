import 'dart:convert';
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
    'Android migration preparation and rollback use protected state',
    (tester) async {
      const searchSentinel = 'MIGRATION_PRIVATE_SEARCH_SENTINEL';
      const noteSentinel = 'MIGRATION_PRIVATE_TRACKER_SENTINEL';
      final suffix = DateTime.now().microsecondsSinceEpoch
          .toRadixString(16)
          .padLeft(12, '0')
          .substring(0, 12);
      final identifiers = <String>[
        for (var index = 1; index <= 16; index += 1)
          _uuid(index.toString().padLeft(8, '0'), suffix),
      ];
      final search = AtlasSavedSearch(
        name: 'Migration fixture',
        description: 'Fake migration data',
        request: const AtlasSearchRequest(
          text: searchSentinel,
          organizations: <String>['UNICEF'],
        ),
        createdAt: '2026-07-29T01:00:00.123456+00:00',
        updatedAt: '2026-07-29T01:01:00.654321+00:00',
      );
      final tracker = AtlasApplicationRecord(
        id: 'migration-remote-$suffix',
        jobKey: 'fixture:$suffix',
        status: 'saved',
        notes: noteSentinel,
        appliedAt: '2026-07-29T01:02:00.123456+00:00',
        updatedAt: '2026-07-29T01:03:00.654321+00:00',
      );
      final plaintext = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[search],
        trackerRecords: <AtlasApplicationRecord>[tracker],
      );
      final cacheDirectory = await Directory.systemTemp.createTemp(
        'atlas_migration_integration_',
      );
      final cacheStore = AtlasLocalCacheStore(
        file: File('${cacheDirectory.path}/atlas-local-cache.json'),
        now: () => DateTime.utc(2026, 7, 29, 2),
      );
      await cacheStore.write(
        _snapshot(
          savedAt: DateTime.utc(2026, 7, 29, 1),
          savedSearches: <AtlasSavedSearch>[search],
          trackerRecords: <AtlasApplicationRecord>[tracker],
        ),
      );

      final keyStore = AtlasAndroidVaultSecureKeyStore();
      final localStore = AtlasAndroidVaultLocalStoreIO();
      final journalStore = AtlasAndroidProtectedMigrationJournalStore();
      final selectedStore = AtlasAndroidSelectedVaultStore();
      final memory = _MemorySource(plaintext);
      final compatibility = _CompatibilitySource(plaintext);
      final runtimes = <AtlasVaultPrivateStateRuntime>[];
      AtlasVaultPlaintextMigrationCoordinator buildCoordinator() {
        final runtime = AtlasVaultPrivateStateRuntime(
          secureKeyStore: keyStore,
          localStoreIO: localStore,
        );
        runtimes.add(runtime);
        return AtlasVaultPlaintextMigrationCoordinator(
          inMemorySource: memory,
          compatibilitySource: compatibility,
          cacheSource: AtlasLocalCacheMigrationStoreSource(cacheStore),
          journalStore: journalStore,
          selectedVaultStore: selectedStore,
          secureKeyStore: keyStore,
          localStoreIO: localStore,
          privateAuthority: _IntegrationPrivateAuthority(
            memory: memory,
            runtime: runtime,
          ),
          now: () => DateTime.utc(2026, 7, 29, 2, 3, 4),
          uuidProvider: () => identifiers.removeAt(0),
          vaultKeyProvider: () =>
              Uint8List.fromList(List<int>.generate(32, (index) => index + 1)),
          nonceProvider: () =>
              Uint8List.fromList(List<int>.generate(12, (index) => index + 11)),
        );
      }

      final coordinator = buildCoordinator();

      String? stagedVaultId;
      addTearDown(() async {
        for (final runtime in runtimes) {
          await runtime.deactivate();
        }
        final journalBytes = await journalStore.read();
        if (journalBytes != null) {
          try {
            final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
              journalBytes,
            );
            stagedVaultId ??= journal.vaultId;
            final digest = await vault.atlasVaultSha256Hex(journalBytes);
            await journalStore.delete(
              expectedSha256: digest,
              allowAbsent: true,
            );
          } finally {
            journalBytes.fillRange(0, journalBytes.length, 0);
          }
        }
        final vaultId = stagedVaultId;
        if (vaultId != null) {
          if (await selectedStore.read() == vaultId) {
            await selectedStore.clear(vaultId);
          }
          await localStore.delete(vaultId);
          await keyStore.deleteVaultKey(vaultId);
        }
        if (await cacheDirectory.exists()) {
          await cacheDirectory.delete(recursive: true);
        }
      });

      expect(await journalStore.read(), isNull);
      expect(await selectedStore.read(), isNull);

      final inventory = await coordinator.inventory();
      expect(inventory.savedSearchCount, 1);
      expect(inventory.trackerRecordCount, 1);
      final prepared = await coordinator.prepare();
      expect(
        prepared.stage,
        AtlasVaultPlaintextMigrationStage.encryptedVerified,
      );

      final journalBytes = await journalStore.read();
      expect(journalBytes, isNotNull);
      final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        journalBytes!,
      );
      journalBytes.fillRange(0, journalBytes.length, 0);
      stagedVaultId = journal.vaultId;
      final protectedJournalFile = File(
        '/data/user/0/com.yutsukioka.jobagg.atlas/no_backup/'
        'atlasvault/v1/migrations/plaintext-private-state.json.enc',
      );
      expect(await protectedJournalFile.exists(), isTrue);
      final protectedJournalText = await protectedJournalFile.readAsString();
      expect(
        protectedJournalText,
        contains('"format":"atlasvault-android-protected-blob"'),
      );
      expect(
        protectedJournalText,
        contains('"purpose":"plaintext-private-state-migration"'),
      );
      expect(protectedJournalText, isNot(contains(searchSentinel)));
      expect(protectedJournalText, isNot(contains(noteSentinel)));

      final encryptedStore = await localStore.read(journal.vaultId);
      expect(encryptedStore, isNotNull);
      final rawStore = utf8.decode(encryptedStore!.canonicalBytes());
      expect(rawStore, isNot(contains(searchSentinel)));
      expect(rawStore, isNot(contains(noteSentinel)));
      expect(rawStore, isNot(contains('saved_search')));
      expect(rawStore, isNot(contains('saved_job')));

      final key = await keyStore.loadVaultKey(journal.vaultId);
      expect(key, isNotNull);
      final payloadTypes = <vault.AtlasVaultPayloadType>[];
      for (final record in encryptedStore.records) {
        final recordPlaintext = await vault.openAtlasVaultRecord(
          vaultKey: key!,
          vaultId: journal.vaultId,
          record: record,
        );
        try {
          payloadTypes.add(
            vault.AtlasVaultPayloadEnvelope.decodeJson(
              utf8.decode(recordPlaintext),
            ).type,
          );
        } finally {
          recordPlaintext.fillRange(0, recordPlaintext.length, 0);
        }
      }
      key!.fillRange(0, key.length, 0);
      expect(payloadTypes, <vault.AtlasVaultPayloadType>[
        vault.AtlasVaultPayloadType.savedSearch,
        vault.AtlasVaultPayloadType.savedJob,
      ]);

      final cacheBeforeRollback = await cacheStore
          .readPrivateStateForMigration();
      expect(cacheBeforeRollback.savedSearches.single.name, search.name);
      expect(compatibility.state.savedSearches.single.name, search.name);
      expect(compatibility.deleteCalls, 0);
      expect(memory.state.savedSearches.single.name, search.name);

      await coordinator.discardPrepared();

      expect(await journalStore.read(), isNull);
      expect(await keyStore.containsVaultKey(journal.vaultId), isFalse);
      expect(await localStore.read(journal.vaultId), isNull);
      expect(await selectedStore.read(), isNull);
      expect(
        (await cacheStore.readPrivateStateForMigration())
            .savedSearches
            .single
            .request
            .text,
        searchSentinel,
      );
      expect(compatibility.deleteCalls, 0);
      expect(memory.state.trackerRecords.single.notes, noteSentinel);

      await coordinator.inventory();
      await coordinator.prepare();
      final finalJournalBytes = await journalStore.read();
      expect(finalJournalBytes, isNotNull);
      final finalJournal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        finalJournalBytes!,
      );
      finalJournalBytes.fillRange(0, finalJournalBytes.length, 0);
      stagedVaultId = finalJournal.vaultId;

      await coordinator.finalizeAndActivate();

      expect(compatibility.state.savedSearches, isEmpty);
      expect(compatibility.state.trackerRecords, isEmpty);
      expect(
        (await cacheStore.readPrivateStateForMigration()).privateSha256,
        isNull,
      );
      expect(await selectedStore.read(), finalJournal.vaultId);
      expect(await journalStore.read(), isNull);
      expect(runtimes.first.isActive, isTrue);
      expect(
        (await runtimes.first.read()).savedSearches.single.name,
        search.name,
      );
      expect(
        (await runtimes.first.read()).trackerRecords.single.jobKey,
        tracker.jobKey,
      );

      await runtimes.first.deactivate();
      final compatibilityReadCount = compatibility.readCalls;
      final relaunched = buildCoordinator();
      expect(
        await relaunched.inspectAuthority(),
        AtlasVaultPlaintextAuthorityState.encryptedSelectedInactive,
      );
      expect(compatibility.readCalls, compatibilityReadCount);
      await relaunched.activateSelected();
      expect(runtimes.last.isActive, isTrue);
      expect(
        (await runtimes.last.read()).savedSearches.single.name,
        search.name,
      );
      expect(compatibility.readCalls, compatibilityReadCount);
      tester.printToConsole(
        'AtlasVault Android migration preparation, rollback, finalization, '
        'and explicit relaunch activation passed.',
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

final class _MemorySource implements AtlasVaultPlaintextStateSource {
  _MemorySource(this.state);

  AtlasVaultPlaintextPrivateState state;

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async {
    return state;
  }
}

final class _CompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  _CompatibilitySource(this.state);

  AtlasVaultPlaintextPrivateState state;
  int deleteCalls = 0;
  int readCalls = 0;

  @override
  Uri get authorityBaseURL => Uri.parse('http://127.0.0.1:8765');

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async {
    readCalls += 1;
    return state;
  }

  @override
  Future<bool> deleteSavedSearch(String name) async {
    deleteCalls += 1;
    final found = state.savedSearches.any((value) => value.name == name);
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches
          .where((value) => value.name != name)
          .toList(growable: false),
      trackerRecords: state.trackerRecords,
    );
    return found;
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) async {
    deleteCalls += 1;
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

final class _IntegrationPrivateAuthority
    implements AtlasVaultPlaintextMigrationPrivateAuthority {
  _IntegrationPrivateAuthority({required this.memory, required this.runtime});

  final _MemorySource memory;
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
