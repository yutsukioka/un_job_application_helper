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
    'Android migration resumes preparation interruptions and rolls back',
    (tester) async {
      final journalScenario = await _RecoveryScenario.create();
      try {
        await journalScenario.coordinator.inventory();
        journalScenario.journal.failAfterNextCreate = true;

        await expectLater(
          journalScenario.coordinator.prepare(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          await journalScenario.journalStage(),
          AtlasVaultPlaintextMigrationStage.prepared,
        );

        await journalScenario.coordinator.discardPrepared();
        await journalScenario.expectRolledBack();
      } finally {
        await journalScenario.dispose();
      }

      final keyScenario = await _RecoveryScenario.create();
      try {
        await keyScenario.coordinator.inventory();
        keyScenario.keyStore.failAfterNextCreate = true;

        await expectLater(
          keyScenario.coordinator.prepare(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          await keyScenario.keyStore.containsVaultKey(
            keyScenario.expectedVaultId,
          ),
          isTrue,
        );
        expect(
          await keyScenario.localStore.read(keyScenario.expectedVaultId),
          isNull,
        );

        expect(
          (await keyScenario.coordinator.resume()).stage,
          AtlasVaultPlaintextMigrationStage.encryptedVerified,
        );
        await keyScenario.coordinator.discardPrepared();
        await keyScenario.expectRolledBack();
      } finally {
        await keyScenario.dispose();
      }

      final storeScenario = await _RecoveryScenario.create();
      try {
        await storeScenario.coordinator.inventory();
        storeScenario.localStore.failAfterNextCreate = true;

        await expectLater(
          storeScenario.coordinator.prepare(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          await storeScenario.localStore.read(storeScenario.expectedVaultId),
          isNotNull,
        );
        expect(
          await storeScenario.journalStage(),
          AtlasVaultPlaintextMigrationStage.prepared,
        );

        expect(
          (await storeScenario.coordinator.resume()).stage,
          AtlasVaultPlaintextMigrationStage.encryptedVerified,
        );
        await storeScenario.coordinator.discardPrepared();
        await storeScenario.expectRolledBack();
      } finally {
        await storeScenario.dispose();
      }

      final verificationScenario = await _RecoveryScenario.create();
      try {
        await verificationScenario.coordinator.inventory();
        verificationScenario.journal.failAfterReplacingStage =
            AtlasVaultPlaintextMigrationStage.encryptedVerified;

        await expectLater(
          verificationScenario.coordinator.prepare(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          await verificationScenario.journalStage(),
          AtlasVaultPlaintextMigrationStage.encryptedVerified,
        );

        expect(
          (await verificationScenario.coordinator.resume()).stage,
          AtlasVaultPlaintextMigrationStage.encryptedVerified,
        );
        await verificationScenario.coordinator.discardPrepared();
        await verificationScenario.expectRolledBack();
      } finally {
        await verificationScenario.dispose();
      }

      tester.printToConsole(
        'AtlasVault Android preparation interruption recovery passed.',
      );
    },
  );

  testWidgets(
    'Android migration resumes every finalization persistence boundary',
    (tester) async {
      final noDeletionScenario = await _RecoveryScenario.create();
      try {
        await noDeletionScenario.prepare();
        await noDeletionScenario.forceCommitInProgress();
        noDeletionScenario.compatibility.failNextRead = true;

        await expectLater(
          noDeletionScenario.coordinator.resume(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          noDeletionScenario.compatibility.state.savedSearches,
          hasLength(1),
        );
        expect(
          noDeletionScenario.compatibility.state.trackerRecords,
          hasLength(1),
        );
        await expectLater(
          noDeletionScenario.coordinator.discardPrepared(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );

        await noDeletionScenario.coordinator.resume();
        await noDeletionScenario.expectCompleted();
      } finally {
        await noDeletionScenario.dispose();
      }

      final savedSearchScenario = await _RecoveryScenario.create();
      try {
        await savedSearchScenario.prepare();
        savedSearchScenario.compatibility.failAfterNextSavedSearchDelete = true;

        await expectLater(
          savedSearchScenario.coordinator.finalizeAndActivate(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(savedSearchScenario.compatibility.state.savedSearches, isEmpty);
        expect(
          await savedSearchScenario.journalStage(),
          AtlasVaultPlaintextMigrationStage.commitInProgress,
        );

        await savedSearchScenario.coordinator.resume();
        await savedSearchScenario.expectCompleted();
      } finally {
        await savedSearchScenario.dispose();
      }

      final trackerScenario = await _RecoveryScenario.create();
      try {
        await trackerScenario.prepare();
        trackerScenario.compatibility.failAfterNextTrackerDelete = true;

        await expectLater(
          trackerScenario.coordinator.finalizeAndActivate(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(trackerScenario.compatibility.state.savedSearches, isEmpty);
        expect(trackerScenario.compatibility.state.trackerRecords, isEmpty);
        expect(
          await trackerScenario.journalStage(),
          AtlasVaultPlaintextMigrationStage.commitInProgress,
        );

        await trackerScenario.coordinator.resume();
        await trackerScenario.expectCompleted();
      } finally {
        await trackerScenario.dispose();
      }

      final cacheScenario = await _RecoveryScenario.create();
      try {
        await cacheScenario.prepare();
        cacheScenario.cache.failAfterNextRemove = true;

        await expectLater(
          cacheScenario.coordinator.finalizeAndActivate(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          (await cacheScenario.cache.readPrivateStateForMigration())
              .privateSha256,
          isNull,
        );
        expect(await cacheScenario.selectedStore.read(), isNull);

        await cacheScenario.coordinator.resume();
        await cacheScenario.expectCompleted();
      } finally {
        await cacheScenario.dispose();
      }

      final selectionScenario = await _RecoveryScenario.create();
      try {
        await selectionScenario.prepare();
        selectionScenario.selectedStore.failAfterNextCreate = true;

        await expectLater(
          selectionScenario.coordinator.finalizeAndActivate(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          await selectionScenario.selectedStore.read(),
          selectionScenario.expectedVaultId,
        );
        expect(selectionScenario.runtime.isActive, isFalse);

        await selectionScenario.coordinator.resume();
        await selectionScenario.expectCompleted();
      } finally {
        await selectionScenario.dispose();
      }

      final activationScenario = await _RecoveryScenario.create();
      try {
        await activationScenario.prepare();
        activationScenario.authority.failAfterNextActivation = true;

        await expectLater(
          activationScenario.coordinator.finalizeAndActivate(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(activationScenario.runtime.isActive, isTrue);
        expect(
          await activationScenario.journalStage(),
          AtlasVaultPlaintextMigrationStage.selectionCommitted,
        );

        await activationScenario.coordinator.resume();
        await activationScenario.expectCompleted();
      } finally {
        await activationScenario.dispose();
      }

      final completionScenario = await _RecoveryScenario.create();
      try {
        await completionScenario.prepare();
        completionScenario.journal.failNextDelete = true;

        final result = await completionScenario.coordinator
            .finalizeAndActivate();
        expect(
          result.stage,
          AtlasVaultPlaintextMigrationStage.completionPending,
        );
        expect(
          await completionScenario.journalStage(),
          AtlasVaultPlaintextMigrationStage.completionPending,
        );
        expect(completionScenario.runtime.isActive, isTrue);

        await completionScenario.coordinator.resume();
        await completionScenario.expectCompleted();
      } finally {
        await completionScenario.dispose();
      }

      tester.printToConsole(
        'AtlasVault Android finalization interruption recovery passed.',
      );
    },
  );

  testWidgets(
    'Android migration preserves changed and unknown remote records',
    (tester) async {
      final changedScenario = await _RecoveryScenario.create();
      try {
        await changedScenario.prepare();
        await changedScenario.forceCommitInProgress();
        changedScenario.compatibility.state = AtlasVaultPlaintextPrivateState(
          savedSearches: <AtlasSavedSearch>[
            AtlasSavedSearch(
              name: changedScenario.search.name,
              request: const AtlasSearchRequest(text: 'CHANGED_PRIVATE_QUERY'),
              createdAt: changedScenario.search.createdAt,
              updatedAt: changedScenario.search.updatedAt,
            ),
          ],
          trackerRecords: <AtlasApplicationRecord>[changedScenario.tracker],
        );

        await expectLater(
          changedScenario.coordinator.resume(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          changedScenario.compatibility.state.savedSearches.single.request.text,
          'CHANGED_PRIVATE_QUERY',
        );
        expect(changedScenario.compatibility.deleteSavedSearchCalls, 0);
        expect(await changedScenario.selectedStore.read(), isNull);
      } finally {
        await changedScenario.dispose();
      }

      final unknownScenario = await _RecoveryScenario.create();
      try {
        await unknownScenario.prepare();
        await unknownScenario.forceCommitInProgress();
        const unknownName = 'Unknown remote search';
        unknownScenario.compatibility.state = AtlasVaultPlaintextPrivateState(
          savedSearches: <AtlasSavedSearch>[
            unknownScenario.search,
            AtlasSavedSearch(
              name: unknownName,
              request: const AtlasSearchRequest(text: 'UNKNOWN_PRIVATE_QUERY'),
            ),
          ],
          trackerRecords: <AtlasApplicationRecord>[unknownScenario.tracker],
        );

        await expectLater(
          unknownScenario.coordinator.resume(),
          throwsA(isA<AtlasVaultPlaintextMigrationException>()),
        );
        expect(
          unknownScenario.compatibility.state.savedSearches.map(
            (value) => value.name,
          ),
          contains(unknownName),
        );
        expect(unknownScenario.compatibility.deleteSavedSearchCalls, 0);
        expect(await unknownScenario.selectedStore.read(), isNull);
      } finally {
        await unknownScenario.dispose();
      }

      tester.printToConsole(
        'AtlasVault Android remote mutation containment passed.',
      );
    },
  );
}

final class _RecoveryScenario {
  _RecoveryScenario._({
    required this.expectedVaultId,
    required this.search,
    required this.tracker,
    required this.cacheDirectory,
    required this.cacheStore,
    required this.nativeKeyStore,
    required this.nativeLocalStore,
    required this.nativeJournalStore,
    required this.nativeSelectedStore,
    required this.keyStore,
    required this.localStore,
    required this.journal,
    required this.selectedStore,
    required this.cache,
    required this.memory,
    required this.compatibility,
    required this.runtime,
    required this.authority,
    required this._identifiers,
  });

  static int _sequence = 0;

  final String expectedVaultId;
  final AtlasSavedSearch search;
  final AtlasApplicationRecord tracker;
  final Directory cacheDirectory;
  final AtlasLocalCacheStore cacheStore;
  final AtlasAndroidVaultSecureKeyStore nativeKeyStore;
  final AtlasAndroidVaultLocalStoreIO nativeLocalStore;
  final AtlasAndroidProtectedMigrationJournalStore nativeJournalStore;
  final AtlasAndroidSelectedVaultStore nativeSelectedStore;
  final _InterruptingKeyStore keyStore;
  final _InterruptingLocalStore localStore;
  final _InterruptingJournalStore journal;
  final _InterruptingSelectedStore selectedStore;
  final _InterruptingCacheSource cache;
  final _RecoveryMemorySource memory;
  final _RecoveryCompatibilitySource compatibility;
  final AtlasVaultPrivateStateRuntime runtime;
  final _RecoveryPrivateAuthority authority;
  final List<String> _identifiers;

  late final AtlasVaultPlaintextMigrationCoordinator coordinator =
      AtlasVaultPlaintextMigrationCoordinator(
        inMemorySource: memory,
        compatibilitySource: compatibility,
        cacheSource: cache,
        journalStore: journal,
        selectedVaultStore: selectedStore,
        secureKeyStore: keyStore,
        localStoreIO: localStore,
        privateAuthority: authority,
        now: () => DateTime.utc(2026, 7, 29, 4, 5, 6),
        uuidProvider: () {
          if (_identifiers.isEmpty) {
            throw StateError('identifier fixture exhausted');
          }
          return _identifiers.removeAt(0);
        },
        vaultKeyProvider: () =>
            Uint8List.fromList(List<int>.generate(32, (index) => index + 21)),
        nonceProvider: () =>
            Uint8List.fromList(List<int>.generate(12, (index) => index + 31)),
      );

  static Future<_RecoveryScenario> create() async {
    _sequence += 1;
    final rawSuffix = (DateTime.now().microsecondsSinceEpoch + _sequence)
        .toRadixString(16);
    final suffix = rawSuffix
        .padLeft(12, '0')
        .substring(rawSuffix.padLeft(12, '0').length - 12);
    final identifiers = <String>[
      _uuid('11000000', suffix),
      _uuid('22000000', suffix),
      _uuid('33000000', suffix),
      _uuid('44000000', suffix),
      _uuid('55000000', suffix),
      _uuid('66000000', suffix),
      _uuid('77000000', suffix),
    ];
    final expectedVaultId = identifiers[1];
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

    final nativeKeyStore = AtlasAndroidVaultSecureKeyStore();
    final nativeLocalStore = AtlasAndroidVaultLocalStoreIO();
    final nativeJournalStore = AtlasAndroidProtectedMigrationJournalStore();
    final nativeSelectedStore = AtlasAndroidSelectedVaultStore();
    final keyStore = _InterruptingKeyStore(nativeKeyStore);
    final localStore = _InterruptingLocalStore(nativeLocalStore);
    final journal = _InterruptingJournalStore(nativeJournalStore);
    final selectedStore = _InterruptingSelectedStore(nativeSelectedStore);
    final cache = _InterruptingCacheSource(
      AtlasLocalCacheMigrationStoreSource(cacheStore),
    );
    final memory = _RecoveryMemorySource(initialState);
    final compatibility = _RecoveryCompatibilitySource(initialState);
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: nativeKeyStore,
      localStoreIO: nativeLocalStore,
    );
    final authority = _RecoveryPrivateAuthority(
      memory: memory,
      runtime: runtime,
    );
    return _RecoveryScenario._(
      expectedVaultId: expectedVaultId,
      search: search,
      tracker: tracker,
      cacheDirectory: cacheDirectory,
      cacheStore: cacheStore,
      nativeKeyStore: nativeKeyStore,
      nativeLocalStore: nativeLocalStore,
      nativeJournalStore: nativeJournalStore,
      nativeSelectedStore: nativeSelectedStore,
      keyStore: keyStore,
      localStore: localStore,
      journal: journal,
      selectedStore: selectedStore,
      cache: cache,
      memory: memory,
      compatibility: compatibility,
      runtime: runtime,
      authority: authority,
      identifiers: identifiers,
    );
  }

  Future<void> prepare() async {
    await coordinator.inventory();
    final summary = await coordinator.prepare();
    expect(summary.stage, AtlasVaultPlaintextMigrationStage.encryptedVerified);
  }

  Future<AtlasVaultPlaintextMigrationStage?> journalStage() async {
    final bytes = await nativeJournalStore.read();
    if (bytes == null) {
      return null;
    }
    try {
      return AtlasVaultPlaintextMigrationJournal.decodeBytes(bytes).stage;
    } finally {
      bytes.fillRange(0, bytes.length, 0);
    }
  }

  Future<void> forceCommitInProgress() async {
    final bytes = await nativeJournalStore.read();
    if (bytes == null) {
      throw StateError('missing journal fixture');
    }
    try {
      final current = AtlasVaultPlaintextMigrationJournal.decodeBytes(bytes);
      final replacement = current.transitionedTo(
        AtlasVaultPlaintextMigrationStage.commitInProgress,
      );
      final replacementBytes = replacement.canonicalBytes();
      try {
        await nativeJournalStore.replace(
          replacementBytes,
          expectedSha256: await vault.atlasVaultSha256Hex(bytes),
        );
      } finally {
        replacementBytes.fillRange(0, replacementBytes.length, 0);
      }
    } finally {
      bytes.fillRange(0, bytes.length, 0);
    }
  }

  Future<void> expectRolledBack() async {
    expect(await nativeJournalStore.read(), isNull);
    expect(await nativeSelectedStore.read(), isNull);
    expect(await nativeLocalStore.read(expectedVaultId), isNull);
    expect(await nativeKeyStore.containsVaultKey(expectedVaultId), isFalse);
    expect(memory.state.savedSearches, hasLength(1));
    expect(memory.state.trackerRecords, hasLength(1));
    expect(compatibility.state.savedSearches, hasLength(1));
    expect(compatibility.state.trackerRecords, hasLength(1));
    final cacheState = await cacheStore.readPrivateStateForMigration();
    expect(cacheState.savedSearches, hasLength(1));
    expect(cacheState.trackerRecords, hasLength(1));
  }

  Future<void> expectCompleted() async {
    expect(await nativeJournalStore.read(), isNull);
    expect(await nativeSelectedStore.read(), expectedVaultId);
    expect(runtime.isActive, isTrue);
    expect(compatibility.state.savedSearches, isEmpty);
    expect(compatibility.state.trackerRecords, isEmpty);
    final cacheState = await cacheStore.readPrivateStateForMigration();
    expect(cacheState.privateSha256, isNull);
    expect(cacheState.savedSearches, isEmpty);
    expect(cacheState.trackerRecords, isEmpty);
    final privateState = await runtime.read();
    expect(privateState.savedSearches.single.name, search.name);
    expect(privateState.trackerRecords.single.jobKey, tracker.jobKey);
  }

  Future<void> dispose() async {
    journal.failAfterNextCreate = false;
    journal.failAfterReplacingStage = null;
    journal.failNextDelete = false;
    keyStore.failAfterNextCreate = false;
    localStore.failAfterNextCreate = false;
    selectedStore.failAfterNextCreate = false;
    cache.failAfterNextRemove = false;
    authority.failAfterNextActivation = false;
    compatibility.failNextRead = false;
    compatibility.failAfterNextSavedSearchDelete = false;
    compatibility.failAfterNextTrackerDelete = false;
    await runtime.deactivate();

    final journalBytes = await nativeJournalStore.read();
    if (journalBytes != null) {
      try {
        await nativeJournalStore.delete(
          expectedSha256: await vault.atlasVaultSha256Hex(journalBytes),
          allowAbsent: true,
        );
      } finally {
        journalBytes.fillRange(0, journalBytes.length, 0);
      }
    }
    final selected = await nativeSelectedStore.read();
    if (selected != null) {
      await nativeSelectedStore.clear(selected);
    }
    await nativeLocalStore.delete(expectedVaultId);
    await nativeKeyStore.deleteVaultKey(expectedVaultId);
    if (await cacheDirectory.exists()) {
      await cacheDirectory.delete(recursive: true);
    }
  }
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

final class _InterruptingKeyStore implements AtlasVaultMigrationSecureKeyStore {
  _InterruptingKeyStore(this.delegate);

  final AtlasVaultMigrationSecureKeyStore delegate;
  bool failAfterNextCreate = false;

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    await delegate.createVaultKey(vaultId, vaultKey);
    if (failAfterNextCreate) {
      failAfterNextCreate = false;
      throw StateError('interrupted after key creation');
    }
  }

  @override
  Future<bool> containsVaultKey(String vaultId) {
    return delegate.containsVaultKey(vaultId);
  }

  @override
  Future<void> deleteVaultKey(String vaultId) {
    return delegate.deleteVaultKey(vaultId);
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) {
    return delegate.loadVaultKey(vaultId);
  }
}

final class _InterruptingLocalStore implements AtlasVaultLocalStoreIO {
  _InterruptingLocalStore(this.delegate);

  final AtlasVaultLocalStoreIO delegate;
  bool failAfterNextCreate = false;

  @override
  Future<void> create(String vaultId, vault.AtlasVaultLocalStore store) async {
    await delegate.create(vaultId, store);
    if (failAfterNextCreate) {
      failAfterNextCreate = false;
      throw StateError('interrupted after store creation');
    }
  }

  @override
  Future<void> delete(String vaultId) {
    return delegate.delete(vaultId);
  }

  @override
  Future<vault.AtlasVaultLocalStore?> read(String vaultId) {
    return delegate.read(vaultId);
  }

  @override
  Future<void> replace(
    String vaultId,
    vault.AtlasVaultLocalStore store, {
    required String expectedSha256,
  }) {
    return delegate.replace(vaultId, store, expectedSha256: expectedSha256);
  }
}

final class _InterruptingJournalStore
    implements AtlasVaultProtectedMigrationJournalStore {
  _InterruptingJournalStore(this.delegate);

  final AtlasVaultProtectedMigrationJournalStore delegate;
  bool failAfterNextCreate = false;
  AtlasVaultPlaintextMigrationStage? failAfterReplacingStage;
  bool failNextDelete = false;

  @override
  Future<void> create(Uint8List canonicalBytes) async {
    await delegate.create(canonicalBytes);
    if (failAfterNextCreate) {
      failAfterNextCreate = false;
      throw StateError('interrupted after journal creation');
    }
  }

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) {
    if (failNextDelete) {
      failNextDelete = false;
      throw StateError('interrupted before journal deletion');
    }
    return delegate.delete(
      expectedSha256: expectedSha256,
      allowAbsent: allowAbsent,
    );
  }

  @override
  Future<Uint8List?> read() {
    return delegate.read();
  }

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) async {
    final replacement = AtlasVaultPlaintextMigrationJournal.decodeBytes(
      canonicalBytes,
    );
    await delegate.replace(canonicalBytes, expectedSha256: expectedSha256);
    if (replacement.stage == failAfterReplacingStage) {
      failAfterReplacingStage = null;
      throw StateError('interrupted after journal replacement');
    }
  }
}

final class _InterruptingSelectedStore implements AtlasVaultSelectedVaultStore {
  _InterruptingSelectedStore(this.delegate);

  final AtlasVaultSelectedVaultStore delegate;
  bool failAfterNextCreate = false;

  @override
  Future<void> clear(String expectedVaultId) {
    return delegate.clear(expectedVaultId);
  }

  @override
  Future<void> create(String vaultId) async {
    await delegate.create(vaultId);
    if (failAfterNextCreate) {
      failAfterNextCreate = false;
      throw StateError('interrupted after selection creation');
    }
  }

  @override
  Future<String?> read() {
    return delegate.read();
  }
}

final class _InterruptingCacheSource implements AtlasLocalCacheMigrationSource {
  _InterruptingCacheSource(this.delegate);

  final AtlasLocalCacheMigrationSource delegate;
  bool failAfterNextRemove = false;

  @override
  Future<AtlasLocalCacheMigrationPrivateState> readPrivateStateForMigration() {
    return delegate.readPrivateStateForMigration();
  }

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) async {
    await delegate.removePrivateStateForMigration(
      expectedPrivateSha256: expectedPrivateSha256,
    );
    if (failAfterNextRemove) {
      failAfterNextRemove = false;
      throw StateError('interrupted after cache cleanup');
    }
  }
}

final class _RecoveryMemorySource implements AtlasVaultPlaintextStateSource {
  _RecoveryMemorySource(this.state);

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

final class _RecoveryCompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  _RecoveryCompatibilitySource(this.state);

  AtlasVaultPlaintextPrivateState state;
  bool failNextRead = false;
  bool failAfterNextSavedSearchDelete = false;
  bool failAfterNextTrackerDelete = false;
  int deleteSavedSearchCalls = 0;

  @override
  Uri get authorityBaseURL => Uri.parse('http://127.0.0.1:8765');

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async {
    if (failNextRead) {
      failNextRead = false;
      throw StateError('interrupted before compatibility cleanup');
    }
    return state;
  }

  @override
  Future<bool> deleteSavedSearch(String name) async {
    deleteSavedSearchCalls += 1;
    final found = state.savedSearches.any((value) => value.name == name);
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches
          .where((value) => value.name != name)
          .toList(growable: false),
      trackerRecords: state.trackerRecords,
    );
    if (failAfterNextSavedSearchDelete) {
      failAfterNextSavedSearchDelete = false;
      throw StateError('interrupted after saved-search deletion');
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
    if (failAfterNextTrackerDelete) {
      failAfterNextTrackerDelete = false;
      throw StateError('interrupted after tracker deletion');
    }
    return found;
  }
}

final class _RecoveryPrivateAuthority
    implements AtlasVaultPlaintextMigrationPrivateAuthority {
  _RecoveryPrivateAuthority({required this.memory, required this.runtime});

  final _RecoveryMemorySource memory;
  final AtlasVaultPrivateStateRuntime runtime;
  bool failAfterNextActivation = false;

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
    final activated =
        await runtime.activateExisting(vaultId) ==
        AtlasVaultActivationResult.activated;
    if (failAfterNextActivation) {
      failAfterNextActivation = false;
      throw StateError('interrupted after runtime activation');
    }
    return activated;
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
