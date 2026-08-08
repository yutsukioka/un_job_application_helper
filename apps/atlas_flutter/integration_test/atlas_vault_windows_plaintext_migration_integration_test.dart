import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_windows.dart';
import 'package:atlas/features/app_shell/atlas_cache_location.dart';
import 'package:atlas/src/atlas_vault/plaintext_migration.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'Windows migration preparation and rollback protect staging state',
    (tester) async {
      if (!Platform.isWindows) {
        return;
      }
      const searchSentinel = 'WINDOWS_MIGRATION_SEARCH_SENTINEL';
      const trackerSentinel = 'WINDOWS_MIGRATION_TRACKER_SENTINEL';
      final suffix = DateTime.now().microsecondsSinceEpoch
          .toRadixString(16)
          .padLeft(12, '0')
          .substring(0, 12);
      final identifiers = <String>[
        for (var index = 1; index <= 16; index += 1)
          _uuid(index.toString().padLeft(8, '0'), suffix),
      ];
      final search = AtlasSavedSearch(
        name: 'Windows migration fixture',
        description: 'Fake migration data',
        request: const AtlasSearchRequest(
          text: searchSentinel,
          organizations: <String>['UNICEF'],
        ),
        createdAt: '2026-08-06T01:00:00.123456+00:00',
        updatedAt: '2026-08-06T01:01:00.654321+00:00',
      );
      final tracker = AtlasApplicationRecord(
        id: 'windows-migration-$suffix',
        jobKey: 'windows-fixture:$suffix',
        status: 'saved',
        notes: trackerSentinel,
        appliedAt: '2026-08-06T01:02:00.123456+00:00',
        updatedAt: '2026-08-06T01:03:00.654321+00:00',
      );
      final plaintext = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[search],
        trackerRecords: <AtlasApplicationRecord>[tracker],
      );
      final cacheRoot = await Directory.systemTemp.createTemp(
        'atlas_windows_migration_',
      );
      final location = AtlasPersistentCacheLocation(
        cacheFile: File(
          '${cacheRoot.path}${Platform.pathSeparator}durable'
          '${Platform.pathSeparator}$atlasLocalCacheFileName',
        ),
        legacyFile: File(
          '${cacheRoot.path}${Platform.pathSeparator}legacy'
          '${Platform.pathSeparator}$atlasLocalCacheFileName',
        ),
        legacyImportRetiredFile: File(
          '${cacheRoot.path}${Platform.pathSeparator}durable'
          '${Platform.pathSeparator}$atlasLegacyImportRetiredFileName',
        ),
      );
      final snapshot = _snapshot(
        savedAt: DateTime.utc(2026, 8, 6, 1),
        savedSearches: <AtlasSavedSearch>[search],
        trackerRecords: <AtlasApplicationRecord>[tracker],
      );
      await AtlasLocalCacheStore(file: location.cacheFile).write(snapshot);
      await AtlasLocalCacheStore(file: location.legacyFile).write(snapshot);

      final keyStore = AtlasWindowsVaultSecureKeyStore();
      final localStore = AtlasWindowsVaultLocalStoreIO();
      final journalStore = AtlasWindowsProtectedMigrationJournalStore();
      final selectedStore = AtlasWindowsSelectedVaultStore();
      final authorityAdmission = AtlasWindowsPlaintextAuthorityAdmission(
        locationProvider: () async => location,
        journalStore: journalStore,
        selectedVaultStore: selectedStore,
      );
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
          profile: AtlasVaultPlaintextMigrationProfile.windows,
          inMemorySource: memory,
          compatibilitySource: compatibility,
          cacheSource: AtlasWindowsDesktopCacheMigrationSource(location),
          authorityAdmission: authorityAdmission,
          conditionalSavedSearchDelete:
              compatibility.conditionalDeleteSavedSearch,
          conditionalTrackerDelete:
              compatibility.conditionalDeleteTrackerRecord,
          journalStore: journalStore,
          selectedVaultStore: selectedStore,
          secureKeyStore: keyStore,
          localStoreIO: localStore,
          privateAuthority: _IntegrationPrivateAuthority(
            memory: memory,
            runtime: runtime,
          ),
          now: () => DateTime.utc(2026, 8, 6, 2, 3, 4),
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
              profile: AtlasVaultPlaintextMigrationProfile.windows,
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
        if (await cacheRoot.exists()) {
          await cacheRoot.delete(recursive: true);
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
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      journalBytes.fillRange(0, journalBytes.length, 0);
      stagedVaultId = journal.vaultId;
      final localAppData = Platform.environment['LOCALAPPDATA'];
      expect(localAppData, isNotNull);
      final journalFile = File(
        '$localAppData${Platform.pathSeparator}UNApplications'
        '${Platform.pathSeparator}AtlasVault${Platform.pathSeparator}v1'
        '${Platform.pathSeparator}migrations${Platform.pathSeparator}'
        'plaintext-private-state.bin',
      );
      final protectedJournal = await journalFile.readAsBytes();
      expect(protectedJournal.take(8), orderedEquals('AVWBLB01'.codeUnits));
      expect(
        _containsSequence(protectedJournal, searchSentinel.codeUnits),
        isFalse,
      );
      expect(
        _containsSequence(protectedJournal, trackerSentinel.codeUnits),
        isFalse,
      );
      protectedJournal.fillRange(0, protectedJournal.length, 0);

      final encryptedStore = await localStore.read(journal.vaultId);
      expect(encryptedStore, isNotNull);
      final storeBytes = encryptedStore!.canonicalBytes();
      expect(_containsSequence(storeBytes, searchSentinel.codeUnits), isFalse);
      expect(_containsSequence(storeBytes, trackerSentinel.codeUnits), isFalse);
      storeBytes.fillRange(0, storeBytes.length, 0);
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
              String.fromCharCodes(recordPlaintext),
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

      final cacheBeforeRollback = await AtlasWindowsDesktopCacheMigrationSource(
        location,
      ).readPrivateStateForMigration();
      expect(cacheBeforeRollback.savedSearches.single.name, search.name);
      expect(cacheBeforeRollback.retainedLegacyCachePresent, isTrue);
      expect(compatibility.deleteCalls, 0);
      expect(memory.state.trackerRecords.single.notes, trackerSentinel);

      await coordinator.discardPrepared();
      final rollbackRelaunched = buildCoordinator();
      expect(
        await rollbackRelaunched.inspectAuthority(),
        AtlasVaultPlaintextAuthorityState.migrationPending,
      );
      expect((await rollbackRelaunched.resume()).stage, isNull);
      final restorer = _CapturingLegacyRestorer();
      await rollbackRelaunched.restoreReviewedLegacyPrivateState(restorer);
      expect(restorer.restored?.savedSearches.single.name, search.name);
      expect(restorer.restored?.trackerRecords.single.jobKey, tracker.jobKey);
      expect(await journalStore.read(), isNull);
      expect(await keyStore.containsVaultKey(journal.vaultId), isFalse);
      expect(await localStore.read(journal.vaultId), isNull);
      expect(await selectedStore.read(), isNull);
      final cacheAfterRollback = await AtlasWindowsDesktopCacheMigrationSource(
        location,
      ).readPrivateStateForMigration();
      expect(cacheAfterRollback.savedSearches.single.name, search.name);
      expect(cacheAfterRollback.trackerRecords.single.notes, trackerSentinel);
      expect(compatibility.deleteCalls, 0);
      expect(memory.state.savedSearches.single.request.text, searchSentinel);

      final finalCoordinator = buildCoordinator();
      await finalCoordinator.inventory();
      final finalPrepared = await finalCoordinator.prepare();
      expect(
        finalPrepared.stage,
        AtlasVaultPlaintextMigrationStage.encryptedVerified,
      );
      final finalJournalBytes = await journalStore.read();
      expect(finalJournalBytes, isNotNull);
      final finalJournal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        finalJournalBytes!,
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      finalJournalBytes.fillRange(0, finalJournalBytes.length, 0);
      stagedVaultId = finalJournal.vaultId;

      final completed = await finalCoordinator.finalizeAndActivate();

      expect(completed.stage, isNull);
      expect(compatibility.state.savedSearches, isEmpty);
      expect(compatibility.state.trackerRecords, isEmpty);
      expect(compatibility.deleteCalls, 2);
      final migratedCache = await AtlasWindowsDesktopCacheMigrationSource(
        location,
      ).readPrivateStateForMigration();
      expect(migratedCache.savedSearches, isEmpty);
      expect(migratedCache.trackerRecords, isEmpty);
      expect(migratedCache.cacheCleanupComplete, isTrue);
      expect(await location.cacheFile.exists(), isTrue);
      expect(await location.legacyFile.exists(), isFalse);
      expect(await location.legacyImportRetiredFile.exists(), isTrue);
      expect(await location.privateMigrationIntentFile.exists(), isFalse);
      expect(await selectedStore.read(), finalJournal.vaultId);
      expect(await journalStore.read(), isNull);

      await runtimes.last.deactivate();
      final relaunched = buildCoordinator();
      expect(
        await relaunched.inspectAuthority(),
        AtlasVaultPlaintextAuthorityState.encryptedSelectedInactive,
      );
      final activated = await relaunched.activateSelected();
      expect(activated.stage, isNull);
      expect(activated.savedSearchCount, 1);
      expect(activated.trackerRecordCount, 1);
      tester.printToConsole(
        'AtlasVault Windows DPAPI migration rollback, finalization, and '
        'explicit reactivation passed.',
      );
    },
  );
}

bool _containsSequence(List<int> source, List<int> target) {
  if (target.isEmpty || source.length < target.length) {
    return false;
  }
  for (var offset = 0; offset <= source.length - target.length; offset += 1) {
    var matches = true;
    for (var index = 0; index < target.length; index += 1) {
      if (source[offset + index] != target[index]) {
        matches = false;
        break;
      }
    }
    if (matches) {
      return true;
    }
  }
  return false;
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
    return AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches,
      trackerRecords: state.trackerRecords,
      authorityBaseURL: Uri.parse('http://127.0.0.1:8765'),
    );
  }
}

final class _CompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  _CompatibilitySource(this.state);

  AtlasVaultPlaintextPrivateState state;
  int deleteCalls = 0;

  @override
  Uri get authorityBaseURL => Uri.parse('http://127.0.0.1:8765');

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async => state;

  @override
  Future<bool> deleteSavedSearch(String name) async {
    deleteCalls += 1;
    final present = state.savedSearches.any((value) => value.name == name);
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches
          .where((value) => value.name != name)
          .toList(growable: false),
      trackerRecords: state.trackerRecords,
    );
    return present;
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) async {
    deleteCalls += 1;
    final present = state.trackerRecords.any((value) => value.id == recordId);
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches,
      trackerRecords: state.trackerRecords
          .where((value) => value.id != recordId)
          .toList(growable: false),
    );
    return present;
  }

  Future<AtlasConditionalDeleteOutcome> conditionalDeleteSavedSearch(
    AtlasSavedSearch expected,
  ) async {
    deleteCalls += 1;
    final current = state.savedSearches
        .where((value) => value.name == expected.name)
        .toList(growable: false);
    if (current.isEmpty) {
      return AtlasConditionalDeleteOutcome.absent;
    }
    if (!_sameJson(current.single.toJson(), expected.toJson())) {
      return AtlasConditionalDeleteOutcome.preconditionFailed;
    }
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches
          .where((value) => value.name != expected.name)
          .toList(growable: false),
      trackerRecords: state.trackerRecords,
    );
    return AtlasConditionalDeleteOutcome.deleted;
  }

  Future<AtlasConditionalDeleteOutcome> conditionalDeleteTrackerRecord(
    AtlasApplicationRecord expected,
  ) async {
    deleteCalls += 1;
    final current = state.trackerRecords
        .where((value) => value.id == expected.id)
        .toList(growable: false);
    if (current.isEmpty) {
      return AtlasConditionalDeleteOutcome.absent;
    }
    if (!_sameJson(current.single.toJson(), expected.toJson())) {
      return AtlasConditionalDeleteOutcome.preconditionFailed;
    }
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches,
      trackerRecords: state.trackerRecords
          .where((value) => value.id != expected.id)
          .toList(growable: false),
    );
    return AtlasConditionalDeleteOutcome.deleted;
  }
}

bool _sameJson(Object? left, Object? right) {
  return jsonEncode(left) == jsonEncode(right);
}

final class _CapturingLegacyRestorer
    implements AtlasVaultLegacyPrivateStateRestoring {
  AtlasVaultPlaintextPrivateState? restored;

  @override
  Future<void> restoreLegacyPrivateStateAfterRollback(
    AtlasVaultPlaintextPrivateState reviewedState,
  ) async {
    restored = reviewedState;
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
