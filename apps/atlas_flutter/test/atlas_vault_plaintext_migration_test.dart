import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('construction invokes no migration dependency', () {
    final fixture = _MigrationFixture();

    fixture.coordinator;

    expect(fixture.totalDependencyCalls, 0);
  });

  test(
    'inventory reads four sources and deduplicates identical values',
    () async {
      final fixture = _MigrationFixture();

      final summary = await fixture.coordinator.inventory();

      expect(summary.savedSearchCount, 1);
      expect(summary.trackerRecordCount, 1);
      expect(summary.localCachePrivatePresent, isTrue);
      expect(summary.compatibilityPrivatePresent, isTrue);
      expect(fixture.memory.readCalls, 1);
      expect(fixture.cache.readCalls, 1);
      expect(fixture.compatibility.readCalls, 1);
      expect(fixture.journal.createCalls, 0);
      expect(fixture.keyStore.createCalls, 0);
      expect(fixture.localStore.createCalls, 0);
      expect(
        summary.toString(),
        'AtlasVaultPlaintextMigrationSummary(<redacted>)',
      );
      expect(summary.toString(), isNot(contains('PRIVATE_QUERY')));
      expect(summary.toString(), isNot(contains('tracker-private-notes')));
    },
  );

  test('saved-search conflicts fail before migration side effects', () async {
    final fixture = _MigrationFixture();
    fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
      savedSearches: <AtlasSavedSearch>[
        _savedSearch(requestText: 'CONFLICTING_QUERY'),
      ],
      trackerRecords: <AtlasApplicationRecord>[_trackerRecord()],
      privateSha256: '1' * 64,
    );

    await expectLater(
      fixture.coordinator.inventory(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );

    expect(fixture.journal.createCalls, 0);
    expect(fixture.keyStore.createCalls, 0);
    expect(fixture.localStore.createCalls, 0);
  });

  test('tracker conflicts fail before migration side effects', () async {
    final fixture = _MigrationFixture();
    fixture.compatibility.state = AtlasVaultPlaintextPrivateState(
      savedSearches: <AtlasSavedSearch>[_savedSearch()],
      trackerRecords: <AtlasApplicationRecord>[
        _trackerRecord(status: 'applied'),
      ],
    );

    await expectLater(
      fixture.coordinator.inventory(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );

    expect(fixture.journal.createCalls, 0);
    expect(fixture.keyStore.createCalls, 0);
    expect(fixture.localStore.createCalls, 0);
  });

  test(
    'compatibility inventory failure creates no migration resource',
    () async {
      final fixture = _MigrationFixture();
      fixture.compatibility.failReads = true;

      await expectLater(
        fixture.coordinator.inventory(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(fixture.journal.createCalls, 0);
      expect(fixture.keyStore.createCalls, 0);
      expect(fixture.localStore.createCalls, 0);
      expect(fixture.cache.removeCalls, 0);
      expect(fixture.compatibility.deleteSavedSearchCalls, 0);
      expect(fixture.compatibility.deleteTrackerCalls, 0);
    },
  );

  test(
    'prepare creates verified encrypted state and leaves plaintext untouched',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();

      final summary = await fixture.coordinator.prepare();

      expect(
        summary.stage,
        AtlasVaultPlaintextMigrationStage.encryptedVerified,
      );
      expect(fixture.journal.createCalls, 1);
      expect(fixture.keyStore.createCalls, 1);
      expect(fixture.keyStore.loadCalls, greaterThanOrEqualTo(1));
      expect(fixture.localStore.createCalls, 1);
      expect(fixture.localStore.readCalls, greaterThanOrEqualTo(1));
      expect(fixture.cache.removeCalls, 0);
      expect(fixture.compatibility.deleteSavedSearchCalls, 0);
      expect(fixture.compatibility.deleteTrackerCalls, 0);
      expect(fixture.memory.state.savedSearches, hasLength(1));
      expect(fixture.cache.state.savedSearches, hasLength(1));
      expect(fixture.compatibility.state.savedSearches, hasLength(1));

      final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        fixture.journal.bytes!,
      );
      expect(
        journal.stage,
        AtlasVaultPlaintextMigrationStage.encryptedVerified,
      );
      expect(journal.vaultKeySha256, isNotNull);
      expect(journal.storeSha256, isNotNull);
      expect(journal.remoteSavedSearchNames, <String>['UN roles']);
      expect(journal.remoteTrackerHandles.single.recordId, 'tracker-record-1');
      expect(
        journal.toString(),
        'AtlasVaultPlaintextMigrationJournal(<redacted>)',
      );
      expect(
        utf8.decode(journal.canonicalBytes()),
        isNot(
          contains(base64Encode(List<int>.generate(32, (index) => index + 1))),
        ),
      );

      final store = fixture.localStore.store;
      expect(store, isNotNull);
      final encodedStore = utf8.decode(store!.canonicalBytes());
      expect(encodedStore, isNot(contains('PRIVATE_QUERY')));
      expect(encodedStore, isNot(contains('tracker-private-notes')));
      expect(store.records, hasLength(2));

      final key = await fixture.keyStore.loadVaultKey(journal.vaultId);
      expect(key, isNotNull);
      final payloadTypes = <vault.AtlasVaultPayloadType>[];
      for (final record in store.records) {
        final plaintext = await vault.openAtlasVaultRecord(
          vaultKey: key!,
          vaultId: journal.vaultId,
          record: record,
        );
        try {
          payloadTypes.add(
            vault.AtlasVaultPayloadEnvelope.decodeJson(
              utf8.decode(plaintext),
            ).type,
          );
        } finally {
          plaintext.fillRange(0, plaintext.length, 0);
        }
      }
      key!.fillRange(0, key.length, 0);
      expect(payloadTypes, <vault.AtlasVaultPayloadType>[
        vault.AtlasVaultPayloadType.savedSearch,
        vault.AtlasVaultPayloadType.savedJob,
      ]);
    },
  );

  test('key-created interruption is adopted and preparation resumes', () async {
    final fixture = _MigrationFixture();
    await fixture.coordinator.inventory();
    fixture.keyStore.failAfterNextCreate = true;

    await expectLater(
      fixture.coordinator.prepare(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
    expect(fixture.journal.bytes, isNotNull);
    expect(fixture.keyStore.keys, hasLength(1));
    expect(fixture.localStore.store, isNull);

    final resumed = await fixture.coordinator.resumePreparation();

    expect(resumed.stage, AtlasVaultPlaintextMigrationStage.encryptedVerified);
    expect(fixture.keyStore.createCalls, 1);
    expect(fixture.localStore.createCalls, 1);
  });

  test(
    'pre-commit rollback removes staged resources and journal last',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();

      await fixture.coordinator.discardPrepared();

      expect(fixture.localStore.store, isNull);
      expect(fixture.keyStore.keys, isEmpty);
      expect(fixture.journal.bytes, isNull);
      expect(fixture.events.takeLast(3), <String>[
        'local-store.delete',
        'key-store.delete',
        'journal.delete',
      ]);
      expect(fixture.cache.removeCalls, 0);
      expect(fixture.compatibility.deleteSavedSearchCalls, 0);
      expect(fixture.compatibility.deleteTrackerCalls, 0);
      expect(fixture.memory.state.savedSearches, hasLength(1));
      expect(fixture.cache.state.savedSearches, hasLength(1));
      expect(fixture.compatibility.state.savedSearches, hasLength(1));
    },
  );

  test('strict journal rejects unknown fields and backward stages', () async {
    final fixture = _MigrationFixture();
    await fixture.coordinator.inventory();
    await fixture.coordinator.prepare();
    final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
      fixture.journal.bytes!,
    );
    final unknown = Map<String, Object?>.from(journal.toJson())
      ..['private_path'] = '/private/value';

    expect(
      () => AtlasVaultPlaintextMigrationJournal.fromJson(unknown),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
    expect(
      () => journal.transitionedTo(AtlasVaultPlaintextMigrationStage.prepared),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
  });
}

AtlasSavedSearch _savedSearch({String requestText = 'PRIVATE_QUERY'}) {
  return AtlasSavedSearch(
    name: 'UN roles',
    description: 'private description',
    request: AtlasSearchRequest(
      text: requestText,
      organizations: const <String>['UNICEF'],
      countriesISO3: const <String>['JPN'],
      limit: 50,
    ),
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-02T00:00:00Z',
  );
}

AtlasApplicationRecord _trackerRecord({String status = 'saved'}) {
  return AtlasApplicationRecord(
    id: 'tracker-record-1',
    jobKey: 'unicef:private-job',
    status: status,
    notes: 'tracker-private-notes',
    appliedAt: '2026-07-03T00:00:00Z',
    updatedAt: '2026-07-04T00:00:00Z',
  );
}

final class _MigrationFixture {
  _MigrationFixture() {
    final state = AtlasVaultPlaintextPrivateState(
      savedSearches: <AtlasSavedSearch>[_savedSearch()],
      trackerRecords: <AtlasApplicationRecord>[_trackerRecord()],
    );
    memory.state = state;
    compatibility.state = state;
    cache.state = AtlasLocalCacheMigrationPrivateState(
      savedSearches: state.savedSearches,
      trackerRecords: state.trackerRecords,
      privateSha256: '1' * 64,
    );
    coordinator = AtlasVaultPlaintextMigrationCoordinator(
      inMemorySource: memory,
      compatibilitySource: compatibility,
      cacheSource: cache,
      journalStore: journal,
      selectedVaultStore: selection,
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      now: () => DateTime.utc(2026, 7, 29, 1, 2, 3),
      uuidProvider: _ids.call,
      vaultKeyProvider: () =>
          Uint8List.fromList(List<int>.generate(32, (index) => index + 1)),
      nonceProvider: () =>
          Uint8List.fromList(List<int>.generate(12, (index) => index + 1)),
    );
  }

  final events = <String>[];
  final memory = _MemorySource();
  final compatibility = _CompatibilitySource();
  final cache = _CacheSource();
  late final _JournalStore journal = _JournalStore(events);
  final selection = _SelectedVaultStore();
  late final _SecureKeyStore keyStore = _SecureKeyStore(events);
  late final _LocalStoreIO localStore = _LocalStoreIO(events);
  final _ids = _SequenceIds(<String>[
    '11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222',
    '33333333-3333-4333-8333-333333333333',
    '44444444-4444-4444-8444-444444444444',
    '55555555-5555-4555-8555-555555555555',
    '66666666-6666-4666-8666-666666666666',
    '77777777-7777-4777-8777-777777777777',
    '88888888-8888-4888-8888-888888888888',
  ]);
  late final AtlasVaultPlaintextMigrationCoordinator coordinator;

  int get totalDependencyCalls {
    return memory.readCalls +
        compatibility.readCalls +
        compatibility.deleteSavedSearchCalls +
        compatibility.deleteTrackerCalls +
        cache.readCalls +
        cache.removeCalls +
        journal.readCalls +
        journal.createCalls +
        journal.replaceCalls +
        journal.deleteCalls +
        selection.readCalls +
        selection.createCalls +
        selection.clearCalls +
        keyStore.totalCalls +
        localStore.totalCalls;
  }
}

final class _MemorySource implements AtlasVaultPlaintextStateSource {
  AtlasVaultPlaintextPrivateState state = AtlasVaultPlaintextPrivateState(
    savedSearches: const <AtlasSavedSearch>[],
    trackerRecords: const <AtlasApplicationRecord>[],
  );
  int readCalls = 0;

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async {
    readCalls += 1;
    return state;
  }
}

final class _CompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  AtlasVaultPlaintextPrivateState state = AtlasVaultPlaintextPrivateState(
    savedSearches: const <AtlasSavedSearch>[],
    trackerRecords: const <AtlasApplicationRecord>[],
  );
  bool failReads = false;
  int readCalls = 0;
  int deleteSavedSearchCalls = 0;
  int deleteTrackerCalls = 0;

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async {
    readCalls += 1;
    if (failReads) {
      throw StateError('private compatibility value');
    }
    return state;
  }

  @override
  Future<bool> deleteSavedSearch(String name) async {
    deleteSavedSearchCalls += 1;
    return false;
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) async {
    deleteTrackerCalls += 1;
    return false;
  }
}

final class _CacheSource implements AtlasLocalCacheMigrationSource {
  AtlasLocalCacheMigrationPrivateState state =
      AtlasLocalCacheMigrationPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
        privateSha256: null,
      );
  int readCalls = 0;
  int removeCalls = 0;

  @override
  Future<AtlasLocalCacheMigrationPrivateState>
  readPrivateStateForMigration() async {
    readCalls += 1;
    return state;
  }

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) async {
    removeCalls += 1;
  }
}

final class _JournalStore implements AtlasVaultProtectedMigrationJournalStore {
  _JournalStore(this.events);

  final List<String> events;
  Uint8List? bytes;
  int readCalls = 0;
  int createCalls = 0;
  int replaceCalls = 0;
  int deleteCalls = 0;

  @override
  Future<Uint8List?> read() async {
    readCalls += 1;
    return bytes == null ? null : Uint8List.fromList(bytes!);
  }

  @override
  Future<void> create(Uint8List canonicalBytes) async {
    createCalls += 1;
    if (bytes != null) {
      throw StateError('duplicate');
    }
    bytes = Uint8List.fromList(canonicalBytes);
    events.add('journal.create');
  }

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) async {
    replaceCalls += 1;
    final current = bytes;
    if (current == null ||
        await vault.atlasVaultSha256Hex(current) != expectedSha256) {
      throw StateError('stale');
    }
    bytes = Uint8List.fromList(canonicalBytes);
    events.add('journal.replace');
  }

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) async {
    deleteCalls += 1;
    final current = bytes;
    if (current == null) {
      if (allowAbsent) {
        return;
      }
      throw StateError('absent');
    }
    if (await vault.atlasVaultSha256Hex(current) != expectedSha256) {
      throw StateError('stale');
    }
    bytes = null;
    events.add('journal.delete');
  }
}

final class _SelectedVaultStore implements AtlasVaultSelectedVaultStore {
  String? value;
  int readCalls = 0;
  int createCalls = 0;
  int clearCalls = 0;

  @override
  Future<String?> read() async {
    readCalls += 1;
    return value;
  }

  @override
  Future<void> create(String vaultId) async {
    createCalls += 1;
    if (value != null) {
      throw StateError('duplicate');
    }
    value = vaultId;
  }

  @override
  Future<void> clear(String expectedVaultId) async {
    clearCalls += 1;
    if (value != expectedVaultId) {
      throw StateError('mismatch');
    }
    value = null;
  }
}

final class _SecureKeyStore implements AtlasVaultSecureKeyStore {
  _SecureKeyStore(this.events);

  final List<String> events;
  final keys = <String, Uint8List>{};
  bool failAfterNextCreate = false;
  int createCalls = 0;
  int loadCalls = 0;
  int containsCalls = 0;
  int deleteCalls = 0;

  int get totalCalls => createCalls + loadCalls + containsCalls + deleteCalls;

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    createCalls += 1;
    if (keys.containsKey(vaultId)) {
      throw StateError('duplicate');
    }
    keys[vaultId] = Uint8List.fromList(vaultKey);
    events.add('key-store.create');
    if (failAfterNextCreate) {
      failAfterNextCreate = false;
      throw StateError('interrupted');
    }
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async {
    loadCalls += 1;
    final value = keys[vaultId];
    return value == null ? null : Uint8List.fromList(value);
  }

  @override
  Future<bool> containsVaultKey(String vaultId) async {
    containsCalls += 1;
    return keys.containsKey(vaultId);
  }

  @override
  Future<void> deleteVaultKey(String vaultId) async {
    deleteCalls += 1;
    keys.remove(vaultId);
    events.add('key-store.delete');
  }
}

final class _LocalStoreIO implements AtlasVaultLocalStoreIO {
  _LocalStoreIO(this.events);

  final List<String> events;
  vault.AtlasVaultLocalStore? store;
  int createCalls = 0;
  int readCalls = 0;
  int replaceCalls = 0;
  int deleteCalls = 0;

  int get totalCalls => createCalls + readCalls + replaceCalls + deleteCalls;

  @override
  Future<vault.AtlasVaultLocalStore?> read(String vaultId) async {
    readCalls += 1;
    return store;
  }

  @override
  Future<void> create(String vaultId, vault.AtlasVaultLocalStore value) async {
    createCalls += 1;
    if (store != null) {
      throw StateError('duplicate');
    }
    store = value;
    events.add('local-store.create');
  }

  @override
  Future<void> replace(
    String vaultId,
    vault.AtlasVaultLocalStore value, {
    required String expectedSha256,
  }) async {
    replaceCalls += 1;
    store = value;
  }

  @override
  Future<void> delete(String vaultId) async {
    deleteCalls += 1;
    store = null;
    events.add('local-store.delete');
  }
}

final class _SequenceIds {
  _SequenceIds(this.values);

  final List<String> values;
  int index = 0;

  String call() {
    if (index >= values.length) {
      throw StateError('No deterministic UUID remains.');
    }
    final value = values[index];
    index += 1;
    return value;
  }
}

extension<T> on Iterable<T> {
  List<T> takeLast(int count) {
    final values = toList(growable: false);
    return values.sublist(values.length - count);
  }
}
