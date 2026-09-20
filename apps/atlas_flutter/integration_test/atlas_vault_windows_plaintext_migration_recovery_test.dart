import 'dart:async';
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

const _defaultVaultId = '92000000-0000-4000-8000-000000000002';
const _authority = 'http://127.0.0.1:8765';
const _searchSentinel = 'WINDOWS_RECOVERY_SEARCH_SENTINEL';
const _trackerSentinel = 'WINDOWS_RECOVERY_TRACKER_SENTINEL';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Windows migration resumes completion in a fresh process', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    final stage =
        Platform.environment['ATLAS_WINDOWS_MIGRATION_RECOVERY_STAGE'] ??
        'full';
    final vaultId =
        Platform.environment['ATLAS_WINDOWS_MIGRATION_RECOVERY_VAULT_ID'] ??
        _defaultVaultId;
    final scenario = _RecoveryScenario(vaultId: vaultId);
    var leaveForFreshProcess = false;
    addTearDown(() async {
      if (!leaveForFreshProcess) {
        await scenario.cleanTestResources();
      }
    });

    switch (stage) {
      case 'prepare':
        await scenario.prepareCompletionPending();
        leaveForFreshProcess = true;
        tester.printToConsole(
          'Windows migration recovery prepare stage left completion-pending '
          'encrypted test authority.',
        );
        return;
      case 'verify':
        await scenario.verifyFreshProcessResume();
        tester.printToConsole(
          'Windows migration recovery verify stage resumed and cleaned the '
          'encrypted test authority.',
        );
        return;
      case 'full':
        await scenario.requireAbsent();
        await scenario.prepareCompletionPending();
        await scenario.verifyFreshProcessResume();
        tester.printToConsole(
          'Windows migration recovery full stage passed. Run prepare and '
          'verify separately for process-boundary evidence.',
        );
        return;
      case 'admission-waiter':
        leaveForFreshProcess = true;
        await scenario.runLegacyAdmissionWaiter();
        tester.printToConsole(
          'An already-running Windows legacy process was blocked by the '
          'journal and reopened only after rollback completed.',
        );
        return;
      case 'admission-prepare':
        leaveForFreshProcess = true;
        await scenario.prepareAdmissionFence();
        tester.printToConsole(
          'Windows migration journal was prepared under cross-process '
          'plaintext-authority admission.',
        );
        return;
      case 'admission-rollback':
        leaveForFreshProcess = true;
        await scenario.rollbackAdmissionFence();
        tester.printToConsole(
          'Windows migration rollback removed staged resources and reopened '
          'legacy admission atomically.',
        );
        return;
      case 'finalization-waiter':
        leaveForFreshProcess = true;
        await scenario.runFinalizationWaiter();
        tester.printToConsole(
          'Concurrent Windows plaintext writes stayed excluded through '
          'selected-vault commitment.',
        );
        return;
      case 'finalization-run':
        leaveForFreshProcess = true;
        await scenario.runFinalizationFence();
        tester.printToConsole(
          'Windows finalization held plaintext admission continuously through '
          'selected-vault read-back.',
        );
        return;
      case 'crash-holder':
        leaveForFreshProcess = true;
        await scenario.holdAdmissionUntilProcessTermination();
        return;
      case 'crash-verify':
        leaveForFreshProcess = true;
        await scenario.verifyAdmissionAfterProcessCrash();
        tester.printToConsole(
          'Windows released the cache admission lock after process '
          'termination.',
        );
        return;
      case 'cleanup':
        leaveForFreshProcess = true;
        await scenario.cleanTestResources();
        await scenario.cleanCoordinationResources();
        tester.printToConsole(
          'Windows migration fence test resources cleaned.',
        );
        return;
      default:
        fail('Unsupported fixed Windows migration recovery test stage.');
    }
  });
}

final class _RecoveryScenario {
  _RecoveryScenario({required this.vaultId})
    : root = Directory(
        '${Directory.systemTemp.path}${Platform.pathSeparator}'
        'atlas_windows_migration_recovery_'
        '${vaultId.replaceAll('-', '')}',
      ),
      coordinationRoot = Directory(
        '${Directory.systemTemp.path}${Platform.pathSeparator}'
        'atlas_windows_migration_coordination_'
        '${vaultId.replaceAll('-', '')}',
      ) {
    location = AtlasPersistentCacheLocation(
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

  final String vaultId;
  final Directory root;
  final Directory coordinationRoot;
  late final AtlasPersistentCacheLocation location;
  final keyStore = AtlasWindowsVaultSecureKeyStore();
  final localStore = AtlasWindowsVaultLocalStoreIO();
  final journalStore = AtlasWindowsProtectedMigrationJournalStore();
  final selectedStore = AtlasWindowsSelectedVaultStore();
  final runtimes = <AtlasVaultPrivateStateRuntime>[];

  AtlasSavedSearch get search => AtlasSavedSearch(
    name: 'Windows recovery fixture',
    description: 'Fake recovery data',
    request: const AtlasSearchRequest(text: _searchSentinel),
    createdAt: '2026-08-06T02:00:00.123456+00:00',
    updatedAt: '2026-08-06T02:01:00.654321+00:00',
  );

  AtlasApplicationRecord get tracker => AtlasApplicationRecord(
    id: 'windows-recovery-record',
    jobKey: 'windows-recovery:fake-job',
    status: 'saved',
    notes: _trackerSentinel,
    updatedAt: '2026-08-06T02:02:00.123456+00:00',
  );

  Future<void> requireAbsent() async {
    if (await journalStore.read() != null ||
        await selectedStore.read() != null ||
        await keyStore.containsVaultKey(vaultId) ||
        await localStore.read(vaultId) != null) {
      throw StateError('Windows migration test authority is not empty.');
    }
    if (await root.exists()) {
      await root.delete(recursive: true);
    }
  }

  Future<void> prepareCompletionPending() async {
    await requireAbsent();
    final plaintext = AtlasVaultPlaintextPrivateState(
      savedSearches: <AtlasSavedSearch>[search],
      trackerRecords: <AtlasApplicationRecord>[tracker],
      authorityBaseURL: Uri.parse(_authority),
    );
    final memory = _MemorySource(plaintext);
    final compatibility = _CompatibilitySource(plaintext);
    final snapshot = _snapshot(
      savedSearches: <AtlasSavedSearch>[search],
      trackerRecords: <AtlasApplicationRecord>[tracker],
    );
    await AtlasLocalCacheStore(file: location.cacheFile).write(snapshot);
    await AtlasLocalCacheStore(file: location.legacyFile).write(snapshot);

    final cache = _FailAfterCleanupSource(
      AtlasWindowsDesktopCacheMigrationSource(location),
    );
    final first = _coordinator(
      memory: memory,
      compatibility: compatibility,
      cache: cache,
      journal: journalStore,
      ids: _initialIds(vaultId),
    );
    await first.inventory();
    await first.prepare();
    cache.failAfterNextCleanup = true;
    await expectLater(
      first.finalizeAndActivate(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
    expect(await selectedStore.read(), isNull);
    expect((await _cacheState()).cacheCleanupComplete, isTrue);

    final interruptedJournal = _FailBeforeDeleteJournalStore(journalStore)
      ..failNextDelete = true;
    final second = _coordinator(
      memory: memory,
      compatibility: compatibility,
      cache: AtlasWindowsDesktopCacheMigrationSource(location),
      journal: interruptedJournal,
    );
    final pending = await second.resume();
    expect(pending.stage, AtlasVaultPlaintextMigrationStage.completionPending);
    expect(await selectedStore.read(), vaultId);
    final journalBytes = await journalStore.read();
    expect(journalBytes, isNotNull);
    final protectedJournal = journalBytes!;
    try {
      final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        protectedJournal,
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      expect(journal.vaultId, vaultId);
      expect(
        journal.stage,
        AtlasVaultPlaintextMigrationStage.completionPending,
      );
    } finally {
      protectedJournal.fillRange(0, protectedJournal.length, 0);
    }
    for (final runtime in runtimes) {
      await runtime.deactivate();
    }
  }

  Future<void> verifyFreshProcessResume() async {
    expect(await selectedStore.read(), vaultId);
    final journalBytes = await journalStore.read();
    expect(journalBytes, isNotNull);
    final protectedJournal = journalBytes!;
    try {
      final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        protectedJournal,
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      expect(journal.vaultId, vaultId);
    } finally {
      protectedJournal.fillRange(0, protectedJournal.length, 0);
    }
    final memory = _MemorySource(
      AtlasVaultPlaintextPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
        authorityBaseURL: Uri.parse(_authority),
      ),
    );
    final compatibility = _CompatibilitySource(
      AtlasVaultPlaintextPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
        authorityBaseURL: Uri.parse(_authority),
      ),
    );
    final coordinator = _coordinator(
      memory: memory,
      compatibility: compatibility,
      cache: AtlasWindowsDesktopCacheMigrationSource(location),
      journal: journalStore,
    );

    expect(
      await coordinator.inspectAuthority(),
      AtlasVaultPlaintextAuthorityState.migrationPending,
    );
    final completed = await coordinator.resume();
    expect(completed.stage, isNull);
    expect(completed.savedSearchCount, 1);
    expect(completed.trackerRecordCount, 1);
    expect(await journalStore.read(), isNull);
    expect(await selectedStore.read(), vaultId);
    expect(compatibility.readCalls, greaterThan(0));
    expect(compatibility.deleteCalls, 0);
    final runtime = runtimes.last;
    final encrypted = await runtime.read();
    expect(encrypted.savedSearches.single.request.text, _searchSentinel);
    expect(encrypted.trackerRecords.single.notes, _trackerSentinel);
    final cache = await _cacheState();
    expect(cache.cacheCleanupComplete, isTrue);
    expect(cache.savedSearches, isEmpty);
    expect(cache.trackerRecords, isEmpty);

    await cleanTestResources();
    expect(await selectedStore.read(), isNull);
    expect(await keyStore.containsVaultKey(vaultId), isFalse);
    expect(await localStore.read(vaultId), isNull);
  }

  Future<void> runLegacyAdmissionWaiter() async {
    await _signal('legacy-ready');
    await _waitForSignal('journal-ready');
    final cacheBefore = await location.cacheFile.readAsBytes();
    final admission = _authorityAdmission(selectedStore);
    var compatibilityWrites = 0;
    var cacheWrites = 0;
    var publicSearches = 0;
    publicSearches += 1;
    await expectLater(
      admission.runLegacyPrivateOperation(() async {
        compatibilityWrites += 2;
        await AtlasLocalCacheStore(
          file: location.cacheFile,
          mutationCoordinator: location.coordinateMutation,
        ).write(_fixtureSnapshot());
        cacheWrites += 1;
      }),
      throwsA(isA<AtlasVaultPlaintextAuthorityAdmissionException>()),
    );
    expect(compatibilityWrites, 0);
    expect(cacheWrites, 0);
    expect(publicSearches, 1);
    expect(await location.cacheFile.readAsBytes(), cacheBefore);
    await _signal('legacy-blocked');

    await _waitForSignal('rollback-complete');
    await admission.runLegacyPrivateOperation(() async {
      compatibilityWrites += 2;
      await AtlasLocalCacheStore(
        file: location.cacheFile,
        mutationCoordinator: location.coordinateMutation,
      ).write(_fixtureSnapshot());
      cacheWrites += 1;
    });
    expect(compatibilityWrites, 2);
    expect(cacheWrites, 1);
    expect(await journalStore.read(), isNull);
    expect(await selectedStore.read(), isNull);
    await _signal('legacy-reopened');
  }

  Future<void> prepareAdmissionFence() async {
    await _waitForSignal('legacy-ready');
    await requireAbsent();
    await _writeFixtureCaches();
    final plaintext = _fixturePlaintext();
    final memory = _MemorySource(plaintext);
    final compatibility = _CompatibilitySource(plaintext);
    final coordinator = _coordinator(
      memory: memory,
      compatibility: compatibility,
      cache: AtlasWindowsDesktopCacheMigrationSource(location),
      journal: journalStore,
      ids: _initialIds(vaultId),
    );
    await coordinator.inventory();
    final prepared = await coordinator.prepare();
    expect(prepared.stage, AtlasVaultPlaintextMigrationStage.encryptedVerified);
    expect(await journalStore.read(), isNotNull);
    await _signal('journal-ready');
  }

  Future<void> rollbackAdmissionFence() async {
    await _waitForSignal('legacy-blocked');
    final plaintext = _fixturePlaintext();
    final coordinator = _coordinator(
      memory: _MemorySource(plaintext),
      compatibility: _CompatibilitySource(plaintext),
      cache: AtlasWindowsDesktopCacheMigrationSource(location),
      journal: journalStore,
    );
    await coordinator.discardPrepared();
    await coordinator.restoreReviewedLegacyPrivateState(
      _RecoveryLegacyRestorer(),
    );
    expect(await journalStore.read(), isNull);
    expect(await selectedStore.read(), isNull);
    expect(await keyStore.containsVaultKey(vaultId), isFalse);
    expect(await localStore.read(vaultId), isNull);
    await _signal('rollback-complete');
    await _waitForSignal('legacy-reopened');
  }

  Future<void> runFinalizationWaiter() async {
    await _signal('finalization-waiter-ready');
    await _waitForSignal('plaintext-absence-verified');
    final cacheBefore = await location.cacheFile.readAsBytes();
    final admission = _authorityAdmission(selectedStore);
    var compatibilityWrites = 0;
    var cacheWrites = 0;
    var publicSearches = 0;
    publicSearches += 1;
    await _signal('finalization-write-attempted');
    await expectLater(
      admission.runLegacyPrivateOperation(() async {
        compatibilityWrites += 2;
        await AtlasLocalCacheStore(
          file: location.cacheFile,
          mutationCoordinator: location.coordinateMutation,
        ).write(_fixtureSnapshot());
        cacheWrites += 1;
      }),
      throwsA(isA<AtlasVaultPlaintextAuthorityAdmissionException>()),
    );
    expect(compatibilityWrites, 0);
    expect(cacheWrites, 0);
    expect(publicSearches, 1);
    expect(await location.cacheFile.readAsBytes(), cacheBefore);
    expect(await selectedStore.read(), vaultId);
    await _signal('finalization-write-blocked');
  }

  Future<void> runFinalizationFence() async {
    await _waitForSignal('finalization-waiter-ready');
    await requireAbsent();
    await _writeFixtureCaches();
    final plaintext = _fixturePlaintext();
    final memory = _MemorySource(plaintext);
    final compatibility = _CompatibilitySource(plaintext);
    final coordinatedSelection = _CoordinatedSelectedVaultStore(
      store: selectedStore,
      beforeCreate: () async {
        await _signal('plaintext-absence-verified');
        await _waitForSignal('finalization-write-attempted');
      },
    );
    final coordinator = _coordinator(
      memory: memory,
      compatibility: compatibility,
      cache: AtlasWindowsDesktopCacheMigrationSource(location),
      journal: journalStore,
      selected: coordinatedSelection,
      ids: _initialIds(vaultId),
    );
    await coordinator.inventory();
    await coordinator.prepare();
    final completed = await coordinator.finalizeAndActivate();
    expect(completed.stage, isNull);
    expect(await selectedStore.read(), vaultId);
    expect(await journalStore.read(), isNull);
    await _waitForSignal('finalization-write-blocked');
  }

  Future<void> holdAdmissionUntilProcessTermination() async {
    await location.coordinateMutation(() async {
      await _signal('crash-lock-held');
      await Completer<void>().future;
    });
  }

  Future<void> verifyAdmissionAfterProcessCrash() async {
    await _waitForSignal('crash-lock-held');
    var acquired = false;
    await location.coordinateMutation(() async {
      acquired = true;
      await _signal('crash-lock-reacquired');
    });
    expect(acquired, isTrue);
  }

  AtlasVaultPlaintextPrivateState _fixturePlaintext() {
    return AtlasVaultPlaintextPrivateState(
      savedSearches: <AtlasSavedSearch>[search],
      trackerRecords: <AtlasApplicationRecord>[tracker],
      authorityBaseURL: Uri.parse(_authority),
    );
  }

  AtlasLocalCacheSnapshot _fixtureSnapshot() {
    return _snapshot(
      savedSearches: <AtlasSavedSearch>[search],
      trackerRecords: <AtlasApplicationRecord>[tracker],
    );
  }

  Future<void> _writeFixtureCaches() async {
    final snapshot = _fixtureSnapshot();
    await AtlasLocalCacheStore(file: location.cacheFile).write(snapshot);
    await AtlasLocalCacheStore(file: location.legacyFile).write(snapshot);
  }

  AtlasWindowsPlaintextAuthorityAdmission _authorityAdmission(
    AtlasVaultSelectedVaultStore selection,
  ) {
    return AtlasWindowsPlaintextAuthorityAdmission(
      locationProvider: () async => location,
      journalStore: journalStore,
      selectedVaultStore: selection,
    );
  }

  File _signalFile(String name) =>
      File('${coordinationRoot.path}${Platform.pathSeparator}$name.ready');

  Future<void> _signal(String name) async {
    await coordinationRoot.create(recursive: true);
    await _signalFile(name).writeAsString('ready\n', flush: true);
  }

  Future<void> _waitForSignal(String name) async {
    await coordinationRoot.create(recursive: true);
    final file = _signalFile(name);
    if (await file.exists()) {
      return;
    }
    final ready = Completer<void>();
    late final StreamSubscription<FileSystemEvent> subscription;
    subscription = coordinationRoot.watch().listen((_) {
      if (!ready.isCompleted && file.existsSync()) {
        ready.complete();
      }
    });
    if (await file.exists() && !ready.isCompleted) {
      ready.complete();
    }
    try {
      await ready.future;
    } finally {
      await subscription.cancel();
    }
  }

  AtlasVaultPlaintextMigrationCoordinator _coordinator({
    required _MemorySource memory,
    required _CompatibilitySource compatibility,
    required AtlasLocalCacheMigrationSource cache,
    required AtlasVaultProtectedMigrationJournalStore journal,
    AtlasVaultSelectedVaultStore? selected,
    List<String>? ids,
  }) {
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
    );
    runtimes.add(runtime);
    final sequence = ids == null ? null : _SequenceIds(ids);
    final effectiveSelection = selected ?? selectedStore;
    return AtlasVaultPlaintextMigrationCoordinator(
      profile: AtlasVaultPlaintextMigrationProfile.windows,
      inMemorySource: memory,
      compatibilitySource: compatibility,
      cacheSource: cache,
      authorityAdmission: _authorityAdmission(effectiveSelection),
      conditionalSavedSearchDelete: compatibility.conditionalDeleteSavedSearch,
      conditionalTrackerDelete: compatibility.conditionalDeleteTrackerRecord,
      journalStore: journal,
      selectedVaultStore: effectiveSelection,
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      privateAuthority: _PrivateAuthority(memory: memory, runtime: runtime),
      now: () => DateTime.utc(2026, 8, 6, 2, 3, 4),
      uuidProvider: sequence?.next,
      vaultKeyProvider: ids == null
          ? null
          : () => Uint8List.fromList(
              List<int>.generate(32, (index) => 0xa0 + index),
            ),
      nonceProvider: ids == null
          ? null
          : () => Uint8List.fromList(
              List<int>.generate(12, (index) => 0x30 + index),
            ),
    );
  }

  Future<AtlasLocalCacheMigrationPrivateState> _cacheState() {
    return AtlasWindowsDesktopCacheMigrationSource(
      location,
    ).readPrivateStateForMigration();
  }

  Future<void> cleanTestResources() async {
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
        if (journal.vaultId != vaultId) {
          throw StateError('Refusing to remove another migration authority.');
        }
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
      if (selected != vaultId) {
        throw StateError('Refusing to clear another selected vault.');
      }
      await selectedStore.clear(vaultId);
    }
    await localStore.delete(vaultId);
    await keyStore.deleteVaultKey(vaultId);
    if (await root.exists()) {
      await root.delete(recursive: true);
    }
  }

  Future<void> cleanCoordinationResources() async {
    if (await coordinationRoot.exists()) {
      await coordinationRoot.delete(recursive: true);
    }
  }
}

final class _CoordinatedSelectedVaultStore
    implements AtlasVaultSelectedVaultStore {
  const _CoordinatedSelectedVaultStore({
    required this.store,
    required this.beforeCreate,
  });

  final AtlasVaultSelectedVaultStore store;
  final Future<void> Function() beforeCreate;

  @override
  Future<String?> read() => store.read();

  @override
  Future<void> create(String vaultId) async {
    await beforeCreate();
    await store.create(vaultId);
  }

  @override
  Future<void> clear(String expectedVaultId) => store.clear(expectedVaultId);
}

final class _RecoveryLegacyRestorer
    implements AtlasVaultLegacyPrivateStateRestoring {
  @override
  Future<void> restoreLegacyPrivateStateAfterRollback(
    AtlasVaultPlaintextPrivateState reviewedState,
  ) async {}
}

final class _FailAfterCleanupSource
    implements
        AtlasLocalCacheMigrationSource,
        AtlasLocalCacheMigrationCleanupSource {
  _FailAfterCleanupSource(this.source);

  final AtlasWindowsDesktopCacheMigrationSource source;
  bool failAfterNextCleanup = false;

  @override
  Future<AtlasLocalCacheMigrationPrivateState> readPrivateStateForMigration() =>
      source.readPrivateStateForMigration();

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) => source.removePrivateStateForMigration(
    expectedPrivateSha256: expectedPrivateSha256,
  );

  @override
  Future<void> completePrivateStateCleanupForMigration({
    required String? expectedPrivateSha256,
  }) async {
    await source.completePrivateStateCleanupForMigration(
      expectedPrivateSha256: expectedPrivateSha256,
    );
    if (failAfterNextCleanup) {
      failAfterNextCleanup = false;
      throw StateError('Simulated process loss after cache cleanup.');
    }
  }
}

final class _FailBeforeDeleteJournalStore
    implements AtlasVaultProtectedMigrationJournalStore {
  _FailBeforeDeleteJournalStore(this.store);

  final AtlasWindowsProtectedMigrationJournalStore store;
  bool failNextDelete = false;

  @override
  Future<Uint8List?> read() => store.read();

  @override
  Future<void> create(Uint8List canonicalBytes) => store.create(canonicalBytes);

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) => store.replace(canonicalBytes, expectedSha256: expectedSha256);

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) async {
    if (failNextDelete) {
      failNextDelete = false;
      throw StateError('Simulated process loss before journal deletion.');
    }
    await store.delete(
      expectedSha256: expectedSha256,
      allowAbsent: allowAbsent,
    );
  }
}

final class _MemorySource implements AtlasVaultPlaintextStateSource {
  _MemorySource(this.state);

  AtlasVaultPlaintextPrivateState state;

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async =>
      state;
}

final class _CompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  _CompatibilitySource(this.state);

  AtlasVaultPlaintextPrivateState state;
  int readCalls = 0;
  int deleteCalls = 0;

  @override
  Uri get authorityBaseURL => Uri.parse(_authority);

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async {
    readCalls += 1;
    return state;
  }

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
    if (jsonEncode(current.single.toJson()) != jsonEncode(expected.toJson())) {
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
    if (jsonEncode(current.single.toJson()) != jsonEncode(expected.toJson())) {
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

final class _PrivateAuthority
    implements AtlasVaultPlaintextMigrationPrivateAuthority {
  _PrivateAuthority({required this.memory, required this.runtime});

  final _MemorySource memory;
  final AtlasVaultPrivateStateRuntime runtime;

  @override
  bool get isEncryptedPrivateStateActive => runtime.isActive;

  @override
  void hideLegacyPrivateState() {
    memory.state = AtlasVaultPlaintextPrivateState(
      savedSearches: const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
      authorityBaseURL: Uri.parse(_authority),
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

final class _SequenceIds {
  _SequenceIds(this.values);

  final List<String> values;
  var index = 0;

  String next() {
    if (index >= values.length) {
      throw StateError('No deterministic migration UUID remains.');
    }
    return values[index++];
  }
}

List<String> _initialIds(String vaultId) => <String>[
  '91000000-0000-4000-8000-000000000001',
  vaultId,
  '93000000-0000-4000-8000-000000000003',
  '94000000-0000-4000-8000-000000000004',
  '95000000-0000-4000-8000-000000000005',
  '96000000-0000-4000-8000-000000000006',
  '97000000-0000-4000-8000-000000000007',
];

AtlasLocalCacheSnapshot _snapshot({
  required List<AtlasSavedSearch> savedSearches,
  required List<AtlasApplicationRecord> trackerRecords,
}) {
  return AtlasLocalCacheSnapshot(
    schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
    baseURL: Uri.parse(_authority),
    savedAt: DateTime.utc(2026, 8, 6, 2),
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
