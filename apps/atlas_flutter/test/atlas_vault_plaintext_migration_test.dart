import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_android_fakes.dart';

void main() {
  test('construction invokes no migration dependency', () {
    final fixture = _MigrationFixture();

    fixture.coordinator;

    expect(fixture.totalDependencyCalls, 0);
  });

  test('Windows coordinator requires admission and conditional deletion', () {
    final fixture = _MigrationFixture();

    expect(
      () => AtlasVaultPlaintextMigrationCoordinator(
        profile: AtlasVaultPlaintextMigrationProfile.windows,
        inMemorySource: fixture.memory,
        compatibilitySource: fixture.compatibility,
        cacheSource: fixture.cache,
        journalStore: fixture.journal,
        selectedVaultStore: fixture.selection,
        secureKeyStore: fixture.keyStore,
        localStoreIO: fixture.localStore,
        privateAuthority: fixture.privateAuthority,
      ),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
    expect(fixture.totalDependencyCalls, 0);
  });

  test(
    'inventory waits for admitted plaintext operations before reading sources',
    () async {
      final fixture = _MigrationFixture();
      fixture.admission.holdNextDrain();

      final inventory = fixture.coordinator.inventory();
      await fixture.admission.entered;

      expect(fixture.memory.readCalls, 0);
      expect(fixture.cache.readCalls, 0);
      expect(fixture.compatibility.readCalls, 0);

      fixture.admission.release();
      await inventory;

      expect(fixture.admission.drainCalls, 1);
      expect(fixture.memory.readCalls, 1);
      expect(fixture.cache.readCalls, 1);
      expect(fixture.compatibility.readCalls, 1);
    },
  );

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

  test(
    'cache authority mismatch fails before compatibility inventory access',
    () async {
      final fixture = _MigrationFixture();
      fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: <AtlasSavedSearch>[_savedSearch()],
        trackerRecords: <AtlasApplicationRecord>[_trackerRecord()],
        privateSha256: '1' * 64,
        authorityBaseURL: Uri.parse('http://cached-authority.test:8765'),
      );
      fixture.compatibility.authorityBaseURL = Uri.parse(
        'http://current-authority.test:8765',
      );

      await expectLater(
        fixture.coordinator.inventory(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(fixture.compatibility.readCalls, 0);
      expect(fixture.journal.createCalls, 0);
      expect(fixture.keyStore.createCalls, 0);
      expect(fixture.localStore.createCalls, 0);
      expect(fixture.compatibility.deleteSavedSearchCalls, 0);
      expect(fixture.compatibility.deleteTrackerCalls, 0);
    },
  );

  test(
    'in-memory private state from another compatibility authority fails closed',
    () async {
      final fixture = _MigrationFixture();
      fixture.memory.state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[
          AtlasSavedSearch(
            name: 'Tested authority search',
            request: const AtlasSearchRequest(
              text: 'TESTED_AUTHORITY_PRIVATE_QUERY',
            ),
          ),
        ],
        trackerRecords: const <AtlasApplicationRecord>[],
        authorityBaseURL: Uri.parse('http://tested-authority.test:8765'),
      );
      fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
        privateSha256: null,
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );

      await expectLater(
        fixture.coordinator.inventory(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(fixture.compatibility.readCalls, 0);
      expect(fixture.journal.createCalls, 0);
      expect(fixture.keyStore.createCalls, 0);
      expect(fixture.localStore.createCalls, 0);
      expect(fixture.compatibility.deleteSavedSearchCalls, 0);
      expect(fixture.compatibility.deleteTrackerCalls, 0);
    },
  );

  test(
    'prepared migration rejects changed compatibility authority before deletion',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      final compatibilityReads = fixture.compatibility.readCalls;
      fixture.compatibility.authorityBaseURL = Uri.parse(
        'http://changed-authority.test:8765',
      );

      await expectLater(
        fixture.coordinator.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(fixture.compatibility.readCalls, compatibilityReads);
      expect(fixture.compatibility.deleteSavedSearchCalls, 0);
      expect(fixture.compatibility.deleteTrackerCalls, 0);
      expect(fixture.cache.removeCalls, 0);
      expect(fixture.selection.createCalls, 0);
      expect(fixture.cache.state.savedSearches, isNotEmpty);
      expect(fixture.cache.state.trackerRecords, isNotEmpty);
    },
  );

  test(
    'commit resume binds compatibility authority when cache is public only',
    () async {
      final fixture = _MigrationFixture();
      fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
        privateSha256: null,
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      final verified = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        fixture.journal.bytes!,
      );
      expect(
        verified.toJson()['compatibility_authority'],
        'http://atlas.test:8765',
      );
      fixture.journal.bytes = verified
          .transitionedTo(AtlasVaultPlaintextMigrationStage.commitInProgress)
          .canonicalBytes();
      fixture.compatibility.authorityBaseURL = Uri.parse(
        'http://replacement-authority.test:8765',
      );
      fixture.compatibility.state = AtlasVaultPlaintextPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
      );

      await expectLater(
        fixture.coordinator.resume(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(fixture.compatibility.deleteSavedSearchCalls, 0);
      expect(fixture.compatibility.deleteTrackerCalls, 0);
      expect(fixture.cache.removeCalls, 0);
      expect(fixture.selection.createCalls, 0);
      expect(fixture.journal.bytes, isNotNull);
    },
  );

  test(
    'legacy backend timestamps normalize before strict vault validation',
    () async {
      final fixture = _MigrationFixture();
      final state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[
          _savedSearch(
            createdAt: '2026-07-01T01:02:03.456789+00:00',
            updatedAt: '2026-07-02T02:03:04.125Z',
          ),
        ],
        trackerRecords: <AtlasApplicationRecord>[
          _trackerRecord(
            appliedAt: '2026-07-03T03:04:05.500000+00:00',
            updatedAt: '2026-07-04T18:05:06.999999+14:00',
          ),
        ],
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );
      fixture.memory.state = state;
      fixture.compatibility.state = state;
      fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: state.savedSearches,
        trackerRecords: state.trackerRecords,
        privateSha256: '1' * 64,
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );

      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();

      final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        fixture.journal.bytes!,
      );
      expect(journal.savedSearches.single.createdAt, '2026-07-01T01:02:03Z');
      expect(journal.savedSearches.single.updatedAt, '2026-07-02T02:03:04Z');
      expect(journal.trackerRecords.single.appliedAt, '2026-07-03T03:04:05Z');
      expect(journal.trackerRecords.single.updatedAt, '2026-07-04T04:05:06Z');
      expect(
        journal.remoteSavedSearches.single.createdAt,
        '2026-07-01T01:02:03Z',
      );
      expect(
        journal.remoteTrackerRecords.single.updatedAt,
        '2026-07-04T04:05:06Z',
      );
    },
  );

  test(
    'Windows journal preserves exact remote timestamps for conditional delete',
    () async {
      final fixture = _MigrationFixture(
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      final state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[
          _savedSearch(
            createdAt: '2026-07-01T01:02:03.456789+00:00',
            updatedAt: '2026-07-02T02:03:04.125Z',
          ),
        ],
        trackerRecords: <AtlasApplicationRecord>[
          _trackerRecord(
            appliedAt: '2026-07-03T03:04:05.500000+00:00',
            updatedAt: '2026-07-04T18:05:06.999999+14:00',
          ),
        ],
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );
      fixture.memory.state = state;
      fixture.compatibility.state = state;
      fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: state.savedSearches,
        trackerRecords: state.trackerRecords,
        privateSha256: '1' * 64,
        durablePrivateSha256: '2' * 64,
        legacyPrivateSha256: '3' * 64,
        retainedLegacyCachePresent: true,
        cacheCleanupComplete: false,
        requiresPhysicalCleanup: true,
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );

      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();

      final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        fixture.journal.bytes!,
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      expect(journal.savedSearches.single.createdAt, '2026-07-01T01:02:03Z');
      expect(
        journal.remoteSavedSearches.single.createdAt,
        '2026-07-01T01:02:03.456789+00:00',
      );
      expect(
        journal.remoteTrackerRecords.single.updatedAt,
        '2026-07-04T18:05:06.999999+14:00',
      );
      fixture.encryptedState = AtlasVaultPlaintextPrivateState(
        savedSearches: journal.savedSearches,
        trackerRecords: journal.trackerRecords,
      );

      final completed = await fixture.coordinator.finalizeAndActivate();

      expect(completed.stage, isNull);
      expect(fixture.compatibility.state.savedSearches, isEmpty);
      expect(fixture.compatibility.state.trackerRecords, isEmpty);
      expect(fixture.selection.value, isNotNull);
    },
  );

  test(
    'Windows journal preserves the complete reviewed saved-search request',
    () async {
      final storedRequest = _storedCompatibilitySearchRequest();
      final storedSnapshot = <String, Object?>{
        'name': 'UN roles',
        'description': 'private description',
        'request': storedRequest,
        'created_at': '2026-07-01T00:00:00.123456+00:00',
        'updated_at': '2026-07-02T00:00:00.654321Z',
      };
      final client = AtlasAPIClient(
        baseURL: Uri.parse('http://atlas.test:8765'),
        transport: _StoredSavedSearchTransport(storedSnapshot),
      );
      final reviewed =
          (await client.savedSearchesForPlaintextMigration()).single;
      final fixture = _MigrationFixture(
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      fixture.compatibility.state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[reviewed],
        trackerRecords: fixture.compatibility.state.trackerRecords,
      );

      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();

      final journal =
          jsonDecode(utf8.decode(fixture.journal.bytes!))
              as Map<String, Object?>;
      final remoteSavedSearch =
          (journal['remote_saved_searches'] as List<Object?>).single
              as Map<String, Object?>;
      expect(
        jsonDecode(remoteSavedSearch['compatibility_request_json']! as String),
        storedRequest,
      );
      expect(remoteSavedSearch['created_at'], storedSnapshot['created_at']);
      expect(remoteSavedSearch['updated_at'], storedSnapshot['updated_at']);
    },
  );

  test('legacy timestamp normalization rejects ambiguous values', () async {
    for (final timestamp in <String>[
      '2026-07-01T01:02:03',
      '2026-02-30T01:02:03+00:00',
      '2026-07-01T01:02:03+24:00',
      '2026-07-01T01:02:03+14:01',
      '2026-07-01T01:02:03-15:00',
    ]) {
      final fixture = _MigrationFixture();
      final invalid = _savedSearch(createdAt: timestamp);
      final state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[invalid],
        trackerRecords: <AtlasApplicationRecord>[_trackerRecord()],
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );
      fixture.memory.state = state;
      fixture.compatibility.state = state;
      fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: state.savedSearches,
        trackerRecords: state.trackerRecords,
        privateSha256: '1' * 64,
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );

      await expectLater(
        fixture.coordinator.inventory(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(fixture.journal.createCalls, 0);
      expect(fixture.keyStore.createCalls, 0);
      expect(fixture.localStore.createCalls, 0);
    }
  });

  test('saved-search conflicts fail before migration side effects', () async {
    final fixture = _MigrationFixture();
    fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
      savedSearches: <AtlasSavedSearch>[
        _savedSearch(requestText: 'CONFLICTING_QUERY'),
      ],
      trackerRecords: <AtlasApplicationRecord>[_trackerRecord()],
      privateSha256: '1' * 64,
      authorityBaseURL: Uri.parse('http://atlas.test:8765'),
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
    'remote deletion and resume validate original rows when merged data is richer',
    () async {
      final fixture = _MigrationFixture();
      fixture.compatibility.state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[
          AtlasSavedSearch(name: 'UN roles', request: _savedSearch().request),
        ],
        trackerRecords: <AtlasApplicationRecord>[
          AtlasApplicationRecord(
            id: 'tracker-record-1',
            jobKey: 'unicef:private-job',
            status: 'saved',
          ),
        ],
      );

      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      final prepared = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        fixture.journal.bytes!,
      );
      expect(prepared.savedSearches.single.description, isNotNull);
      expect(prepared.remoteSavedSearches.single.description, isNull);
      expect(prepared.trackerRecords.single.notes, isNotNull);
      expect(prepared.remoteTrackerRecords.single.notes, isNull);
      fixture.compatibility.failAfterNextSavedSearchDelete = true;

      await expectLater(
        fixture.coordinator.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      final result = await fixture.coordinator.resume();

      expect(result.stage, isNull);
      expect(fixture.compatibility.state.savedSearches, isEmpty);
      expect(fixture.compatibility.state.trackerRecords, isEmpty);
      expect(fixture.compatibility.deleteSavedSearchCalls, 2);
      expect(fixture.selection.value, isNotNull);
      expect(fixture.journal.bytes, isNull);
    },
  );

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
    'journal-created interruption resumes without duplicate journal',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      fixture.journal.failAfterNextCreate = true;

      await expectLater(
        fixture.coordinator.prepare(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(fixture.journal.bytes, isNotNull);
      expect(fixture.keyStore.keys, isEmpty);
      expect(fixture.localStore.store, isNull);

      final resumed = await fixture.coordinator.resumePreparation();

      expect(
        resumed.stage,
        AtlasVaultPlaintextMigrationStage.encryptedVerified,
      );
      expect(fixture.journal.createCalls, 1);
      expect(fixture.keyStore.createCalls, 1);
      expect(fixture.localStore.createCalls, 1);
    },
  );

  test('store-created interruption adopts verified store on resume', () async {
    final fixture = _MigrationFixture();
    await fixture.coordinator.inventory();
    fixture.localStore.failAfterNextCreate = true;

    await expectLater(
      fixture.coordinator.prepare(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
    expect(fixture.localStore.store, isNotNull);
    final interruptedJournal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
      fixture.journal.bytes!,
    );
    expect(
      interruptedJournal.stage,
      AtlasVaultPlaintextMigrationStage.prepared,
    );
    expect(interruptedJournal.storeSha256, isNull);

    final resumed = await fixture.coordinator.resumePreparation();

    expect(resumed.stage, AtlasVaultPlaintextMigrationStage.encryptedVerified);
    expect(fixture.localStore.createCalls, 1);
    expect(fixture.keyStore.createCalls, 1);
  });

  test(
    'pre-commit rollback removes staged resources and journal last',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      expect(
        await fixture.coordinator.inspectPreparedRollbackAvailability(),
        isTrue,
      );
      final rollbackEventOffset = fixture.events.length;

      await fixture.coordinator.discardPrepared();

      expect(fixture.localStore.store, isNull);
      expect(fixture.keyStore.keys, isEmpty);
      expect(fixture.journal.bytes, isNotNull);
      expect(fixture.events.skip(rollbackEventOffset), <String>[
        'journal.replace',
        'local-store.delete',
        'journal.replace',
        'key-store.delete',
      ]);
      expect(fixture.cache.removeCalls, 0);
      expect(fixture.compatibility.deleteSavedSearchCalls, 0);
      expect(fixture.compatibility.deleteTrackerCalls, 0);
      expect(fixture.memory.state.savedSearches, hasLength(1));
      expect(fixture.cache.state.savedSearches, hasLength(1));
      expect(fixture.compatibility.state.savedSearches, hasLength(1));

      await fixture.coordinator.restoreReviewedLegacyPrivateState(
        _CapturingLegacyPrivateStateRestorer(),
      );

      expect(fixture.journal.bytes, isNull);
      expect(fixture.events.last, 'journal.delete');
    },
  );

  test(
    'rollback transfers the exact reviewed cache and remote inventory',
    () async {
      final fixture = _MigrationFixture();
      final cacheSearch = _savedSearch(requestText: 'CACHE_ONLY_QUERY');
      final cacheTracker = _trackerRecord(status: 'applied');
      final remoteSearch = AtlasSavedSearch(
        name: 'Remote only search',
        request: const AtlasSearchRequest(text: 'REMOTE_ONLY_QUERY'),
        createdAt: '2026-07-05T00:00:00Z',
        updatedAt: '2026-07-06T00:00:00Z',
      );
      final remoteTracker = AtlasApplicationRecord(
        id: 'remote-tracker-record',
        jobKey: 'unhcr:remote-private-job',
        status: 'saved',
        updatedAt: '2026-07-07T00:00:00Z',
      );
      fixture.memory.state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[cacheSearch],
        trackerRecords: <AtlasApplicationRecord>[cacheTracker],
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );
      fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: <AtlasSavedSearch>[cacheSearch],
        trackerRecords: <AtlasApplicationRecord>[cacheTracker],
        privateSha256: '1' * 64,
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );
      fixture.compatibility.state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[remoteSearch],
        trackerRecords: <AtlasApplicationRecord>[remoteTracker],
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.memory.state = AtlasVaultPlaintextPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
      );

      await fixture.coordinator.discardPrepared();
      expect(fixture.journal.bytes, isNotNull);
      final restartedCoordinator = fixture.restartCoordinator();
      expect(
        await restartedCoordinator.inspectAuthority(),
        AtlasVaultPlaintextAuthorityState.migrationPending,
      );
      expect(
        await restartedCoordinator.inspectPreparedRollbackAvailability(),
        isFalse,
      );
      final resumed = await restartedCoordinator.resume();
      expect(resumed.stage, isNull);
      final restorer = _CapturingLegacyPrivateStateRestorer();
      await restartedCoordinator.restoreReviewedLegacyPrivateState(restorer);

      expect(restorer.calls, 1);
      expect(
        restorer.restored!.savedSearches.map((value) => value.name),
        <String>['Remote only search', 'UN roles'],
      );
      expect(
        restorer.restored!.trackerRecords.map((value) => value.jobKey),
        <String>['unhcr:remote-private-job', 'unicef:private-job'],
      );
      expect(
        restorer.restored!.authorityBaseURL,
        Uri.parse('http://atlas.test:8765'),
      );
      expect(fixture.journal.bytes, isNull);
      expect(fixture.selection.value, isNull);
      await expectLater(
        fixture.coordinator.restoreReviewedLegacyPrivateState(restorer),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(restorer.calls, 1);
    },
  );

  test(
    'rollback resumes after staged store deletion without recreating resources',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.localStore.failAfterNextDelete = true;

      await expectLater(
        fixture.coordinator.discardPrepared(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(fixture.localStore.store, isNull);
      expect(fixture.keyStore.keys, isNotEmpty);
      expect(fixture.journal.bytes, isNotNull);
      expect(
        await fixture.coordinator.inspectPreparedRollbackAvailability(),
        isTrue,
      );
      final createCalls = fixture.localStore.createCalls;

      final resumed = await fixture.coordinator.resume();

      expect(resumed.stage, isNull);
      expect(fixture.localStore.createCalls, createCalls);
      expect(fixture.localStore.store, isNull);
      expect(fixture.keyStore.keys, isEmpty);
      expect(fixture.journal.bytes, isNotNull);
      expect(
        await fixture.coordinator.inspectAuthority(),
        AtlasVaultPlaintextAuthorityState.migrationPending,
      );
      await fixture.coordinator.restoreReviewedLegacyPrivateState(
        _CapturingLegacyPrivateStateRestorer(),
      );
      expect(fixture.journal.bytes, isNull);
      expect(
        await fixture.coordinator.inspectAuthority(),
        AtlasVaultPlaintextAuthorityState.legacy,
      );
    },
  );

  test(
    'rollback resumes after staged key deletion without recreating resources',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.keyStore.failAfterNextDelete = true;

      await expectLater(
        fixture.coordinator.discardPrepared(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(fixture.localStore.store, isNull);
      expect(fixture.keyStore.keys, isEmpty);
      expect(fixture.journal.bytes, isNotNull);
      final keyCreateCalls = fixture.keyStore.createCalls;
      final storeCreateCalls = fixture.localStore.createCalls;

      final resumed = await fixture.coordinator.resume();

      expect(resumed.stage, isNull);
      expect(fixture.keyStore.createCalls, keyCreateCalls);
      expect(fixture.localStore.createCalls, storeCreateCalls);
      expect(fixture.localStore.store, isNull);
      expect(fixture.keyStore.keys, isEmpty);
      expect(fixture.journal.bytes, isNotNull);
      await fixture.coordinator.restoreReviewedLegacyPrivateState(
        _CapturingLegacyPrivateStateRestorer(),
      );
      expect(fixture.journal.bytes, isNull);
    },
  );

  test('rollback is unavailable after the point of no return', () async {
    final fixture = _MigrationFixture();
    await fixture.coordinator.inventory();
    await fixture.coordinator.prepare();
    final verified = AtlasVaultPlaintextMigrationJournal.decodeBytes(
      fixture.journal.bytes!,
    );
    fixture.journal.bytes = verified
        .transitionedTo(AtlasVaultPlaintextMigrationStage.commitInProgress)
        .canonicalBytes();

    expect(
      await fixture.coordinator.inspectPreparedRollbackAvailability(),
      isFalse,
    );

    await expectLater(
      fixture.coordinator.discardPrepared(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );

    expect(fixture.journal.bytes, isNotNull);
    expect(fixture.localStore.store, isNotNull);
    expect(fixture.keyStore.keys, isNotEmpty);
    expect(fixture.cache.removeCalls, 0);
  });

  test(
    'resume maps asynchronous dependency failures to fixed errors',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      final verified = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        fixture.journal.bytes!,
      );
      fixture.journal.bytes = verified
          .transitionedTo(AtlasVaultPlaintextMigrationStage.commitInProgress)
          .canonicalBytes();
      fixture.compatibility.failReads = true;

      await expectLater(
        fixture.coordinator.resume(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
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
    final invalidRollback = Map<String, Object?>.from(journal.toJson())
      ..['rollback_started'] = false
      ..['rollback_store_deleted'] = true;
    expect(
      () => AtlasVaultPlaintextMigrationJournal.fromJson(invalidRollback),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
    final noncanonicalAuthority = Map<String, Object?>.from(journal.toJson())
      ..['compatibility_authority'] = 'HTTP://ATLAS.TEST:8765/';
    expect(
      () => AtlasVaultPlaintextMigrationJournal.fromJson(noncanonicalAuthority),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
  });

  test(
    'Windows journal profile is strict and rejects Android journal bytes',
    () async {
      final windows = _MigrationFixture(
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      await windows.coordinator.inventory();
      await windows.coordinator.prepare();

      final windowsBytes = windows.journal.bytes!;
      final windowsJournal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        windowsBytes,
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      expect(
        windowsJournal.toJson()['format'],
        'atlasvault-windows-plaintext-migration',
      );
      expect(windowsJournal.durableCachePrivateSha256, isNotNull);
      expect(windowsJournal.legacyCachePrivateSha256, isNotNull);
      final noncanonicalSnapshot =
          jsonDecode(jsonEncode(windowsJournal.toJson()))
              as Map<String, Object?>;
      final noncanonicalRemote =
          (noncanonicalSnapshot['remote_saved_searches']! as List<Object?>)
                  .single
              as Map<String, Object?>;
      noncanonicalRemote['compatibility_request_json'] =
          ' ${noncanonicalRemote['compatibility_request_json']}';
      expect(
        () => AtlasVaultPlaintextMigrationJournal.fromJson(
          noncanonicalSnapshot,
          profile: AtlasVaultPlaintextMigrationProfile.windows,
        ),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      final inconsistentSnapshot =
          jsonDecode(jsonEncode(windowsJournal.toJson()))
              as Map<String, Object?>;
      final inconsistentRemote =
          (inconsistentSnapshot['remote_saved_searches']! as List<Object?>)
                  .single
              as Map<String, Object?>;
      final reducedRequest =
          inconsistentRemote['request']! as Map<String, Object?>;
      reducedRequest['text'] = 'different-reviewed-query';
      expect(
        () => AtlasVaultPlaintextMigrationJournal.fromJson(
          inconsistentSnapshot,
          profile: AtlasVaultPlaintextMigrationProfile.windows,
        ),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(
        () => AtlasVaultPlaintextMigrationJournal.decodeBytes(windowsBytes),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      final android = _MigrationFixture();
      await android.coordinator.inventory();
      await android.coordinator.prepare();
      final androidBytes = android.journal.bytes!;
      final androidJournal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
        androidBytes,
        profile: AtlasVaultPlaintextMigrationProfile.android,
      );
      expect(
        androidJournal.toJson()['format'],
        'atlasvault-android-plaintext-migration',
      );
      expect(
        androidJournal.toJson().containsKey('durable_cache_private_sha256'),
        isFalse,
      );
      expect(
        () => AtlasVaultPlaintextMigrationJournal.decodeBytes(
          androidBytes,
          profile: AtlasVaultPlaintextMigrationProfile.windows,
        ),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
    },
  );

  test(
    'finalization removes plaintext before selection and clears journal last',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();

      final result = await fixture.coordinator.finalizeAndActivate();

      expect(result.stage, isNull);
      expect(fixture.compatibility.state.savedSearches, isEmpty);
      expect(fixture.compatibility.state.trackerRecords, isEmpty);
      expect(fixture.cache.state.savedSearches, isEmpty);
      expect(fixture.cache.state.trackerRecords, isEmpty);
      expect(fixture.memory.state.savedSearches, isEmpty);
      expect(fixture.memory.state.trackerRecords, isEmpty);
      expect(fixture.selection.value, isNotNull);
      expect(fixture.privateAuthority.isEncryptedPrivateStateActive, isTrue);
      expect(fixture.journal.bytes, isNull);
      expect(
        fixture.events.indexOf('selection.create'),
        greaterThan(fixture.events.indexOf('cache.remove')),
      );
      expect(
        fixture.events.indexOf('private-authority.activate'),
        greaterThan(fixture.events.indexOf('selection.create')),
      );
      expect(fixture.events.last, 'journal.delete');
    },
  );

  test(
    'Windows finalization completes physical cache cleanup before selection',
    () async {
      final fixture = _MigrationFixture(
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();

      final result = await fixture.coordinator.finalizeAndActivate();

      expect(result.stage, isNull);
      expect(fixture.cache.cleanupCalls, 1);
      expect(fixture.cache.state.cacheCleanupComplete, isTrue);
      expect(fixture.cache.state.cacheCleanupPending, isFalse);
      expect(fixture.cache.state.requiresPhysicalCleanup, isFalse);
      expect(fixture.cache.state.retainedLegacyCachePresent, isFalse);
      expect(
        fixture.events.indexOf('selection.create'),
        greaterThan(fixture.events.indexOf('cache.cleanup')),
      );
      expect(fixture.events.last, 'journal.delete');
    },
  );

  test(
    'Windows resume completes an interrupted two-file cache cleanup',
    () async {
      final fixture = _MigrationFixture(
        profile: AtlasVaultPlaintextMigrationProfile.windows,
      );
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.cache.failAfterNextCleanup = true;

      await expectLater(
        fixture.coordinator.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(fixture.cache.state.cacheCleanupPending, isTrue);
      expect(fixture.cache.state.durablePrivateSha256, isNull);
      expect(fixture.cache.state.legacyPrivateSha256, '3' * 64);
      expect(fixture.selection.createCalls, 0);

      final result = await fixture.restartCoordinator().resume();

      expect(result.stage, isNull);
      expect(fixture.cache.cleanupCalls, 2);
      expect(fixture.cache.state.cacheCleanupComplete, isTrue);
      expect(fixture.selection.createCalls, 1);
      expect(fixture.journal.bytes, isNull);
    },
  );

  test(
    'resume adopts an acknowledged cache scrub with its authority intact',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.cache.failAfterNextRemove = true;

      await expectLater(
        fixture.coordinator.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(fixture.cache.state.cachePresent, isTrue);
      expect(fixture.cache.state.privateSha256, isNull);
      expect(
        fixture.cache.state.authorityBaseURL,
        Uri.parse('http://atlas.test:8765'),
      );
      expect(fixture.selection.createCalls, 0);
      final savedSearchDeletes = fixture.compatibility.deleteSavedSearchCalls;
      final trackerDeletes = fixture.compatibility.deleteTrackerCalls;

      final summary = await fixture.coordinator.resume();

      expect(summary.stage, isNull);
      expect(fixture.compatibility.deleteSavedSearchCalls, savedSearchDeletes);
      expect(fixture.compatibility.deleteTrackerCalls, trackerDeletes);
      expect(fixture.selection.createCalls, 1);
      expect(fixture.journal.bytes, isNull);
    },
  );

  test(
    'selected encrypted authority activates only after explicit call',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      await fixture.coordinator.finalizeAndActivate();
      fixture.privateAuthority.isEncryptedPrivateStateActive = false;

      expect(
        await fixture.coordinator.inspectAuthority(),
        AtlasVaultPlaintextAuthorityState.encryptedSelectedInactive,
      );
      expect(fixture.privateAuthority.activateCalls, 1);

      await fixture.coordinator.activateSelected();

      expect(fixture.privateAuthority.activateCalls, 2);
      expect(fixture.privateAuthority.isEncryptedPrivateStateActive, isTrue);
    },
  );

  test(
    'resume journals an already deleted saved search exactly once',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.compatibility.failAfterNextSavedSearchDelete = true;

      await expectLater(
        fixture.coordinator.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(fixture.compatibility.state.savedSearches, isEmpty);
      expect(fixture.compatibility.deleteSavedSearchCalls, 1);
      expect(fixture.selection.value, isNull);

      await fixture.coordinator.resume();

      expect(fixture.compatibility.deleteSavedSearchCalls, 2);
      expect(fixture.compatibility.state.trackerRecords, isEmpty);
      expect(fixture.cache.state.savedSearches, isEmpty);
      expect(fixture.selection.value, isNotNull);
      expect(fixture.journal.bytes, isNull);
    },
  );

  test(
    'unknown remote record after commit is preserved and fails closed',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.compatibility.failAfterNextSavedSearchDelete = true;
      await expectLater(
        fixture.coordinator.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      fixture.compatibility.state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[
          AtlasSavedSearch(
            name: 'Unexpected remote search',
            request: const AtlasSearchRequest(text: 'unexpected'),
          ),
        ],
        trackerRecords: fixture.compatibility.state.trackerRecords,
      );

      await expectLater(
        fixture.coordinator.resume(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(
        fixture.compatibility.state.savedSearches.single.name,
        'Unexpected remote search',
      );
      expect(fixture.compatibility.deleteSavedSearchCalls, 1);
      expect(fixture.selection.value, isNull);
      expect(fixture.journal.bytes, isNotNull);
    },
  );

  test('changed saved search survives the review-to-delete race', () async {
    final fixture = _MigrationFixture();
    await fixture.coordinator.inventory();
    await fixture.coordinator.prepare();
    final changed = AtlasSavedSearch(
      name: 'UN roles',
      description: 'changed after migration review',
      request: const AtlasSearchRequest(text: 'CHANGED_PRIVATE_QUERY'),
      createdAt: '2026-07-01T00:00:00Z',
      updatedAt: '2026-08-08T00:00:00Z',
    );
    fixture.compatibility.replaceSavedSearchBeforeNextDelete = changed;

    await expectLater(
      fixture.coordinator.finalizeAndActivate(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );

    expect(fixture.compatibility.state.savedSearches, <AtlasSavedSearch>[
      changed,
    ]);
    expect(fixture.selection.value, isNull);
    final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
      fixture.journal.bytes!,
    );
    expect(journal.deletedSavedSearchNames, isEmpty);
  });

  test('changed tracker survives the review-to-delete race', () async {
    final fixture = _MigrationFixture();
    await fixture.coordinator.inventory();
    await fixture.coordinator.prepare();
    final changed = AtlasApplicationRecord(
      id: 'tracker-record-1',
      jobKey: 'unicef:private-job',
      status: 'applied',
      notes: 'changed after migration review',
      appliedAt: '2026-08-07T00:00:00Z',
      updatedAt: '2026-08-08T00:00:00Z',
    );
    fixture.compatibility.replaceTrackerBeforeNextDelete = changed;

    await expectLater(
      fixture.coordinator.finalizeAndActivate(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );

    expect(fixture.compatibility.state.trackerRecords, <AtlasApplicationRecord>[
      changed,
    ]);
    expect(fixture.selection.value, isNull);
    final journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
      fixture.journal.bytes!,
    );
    expect(journal.deletedTrackerRecordIds, isEmpty);
  });

  test(
    'plaintext creation after absence verification cannot commit selection',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      var legacyWriteRejected = false;
      fixture.selection.beforeNextCreate = () async {
        try {
          await fixture.authorityAdmission.runLegacyPrivateOperation(() async {
            fixture.compatibility.state = AtlasVaultPlaintextPrivateState(
              savedSearches: <AtlasSavedSearch>[
                AtlasSavedSearch(
                  name: 'Concurrent private search',
                  request: const AtlasSearchRequest(text: 'CONCURRENT_PRIVATE'),
                  createdAt: '2026-08-08T00:00:00Z',
                  updatedAt: '2026-08-08T00:00:00Z',
                ),
              ],
              trackerRecords: const <AtlasApplicationRecord>[],
            );
          });
        } on AtlasVaultPlaintextAuthorityAdmissionException {
          legacyWriteRejected = true;
        }
      };

      final completed = await fixture.coordinator.finalizeAndActivate();

      expect(completed.stage, isNull);
      expect(legacyWriteRejected, isTrue);
      expect(fixture.compatibility.state.savedSearches, isEmpty);
      expect(fixture.selection.value, isNotNull);
    },
  );

  test(
    'unexpected cache disappearance after preparation fails closed',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.cache.state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
        privateSha256: null,
        cachePresent: false,
      );

      await expectLater(
        fixture.coordinator.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );

      expect(fixture.cache.removeCalls, 0);
      expect(fixture.selection.value, isNull);
      expect(fixture.journal.bytes, isNotNull);
    },
  );

  test('selected-vault create interruption is adopted on resume', () async {
    final fixture = _MigrationFixture();
    await fixture.coordinator.inventory();
    await fixture.coordinator.prepare();
    fixture.selection.failAfterNextCreate = true;

    await expectLater(
      fixture.coordinator.finalizeAndActivate(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
    expect(fixture.selection.value, isNotNull);
    expect(fixture.privateAuthority.activateCalls, 0);
    expect(
      await fixture.coordinator.inspectAuthority(),
      AtlasVaultPlaintextAuthorityState.migrationPending,
    );
    final exactSelection = fixture.selection.value!;
    fixture.selection.value = '99999999-9999-4999-8999-999999999999';
    expect(
      await fixture.coordinator.inspectAuthority(),
      AtlasVaultPlaintextAuthorityState.recoveryRequired,
    );
    fixture.selection.value = exactSelection;

    await fixture.coordinator.resume();

    expect(fixture.selection.createCalls, 1);
    expect(fixture.privateAuthority.activateCalls, 1);
    expect(fixture.journal.bytes, isNull);
  });

  test(
    'runtime activation interruption resumes without dual authority',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.privateAuthority.failAfterNextActivation = true;

      await expectLater(
        fixture.coordinator.finalizeAndActivate(),
        throwsA(isA<AtlasVaultPlaintextMigrationException>()),
      );
      expect(fixture.privateAuthority.isEncryptedPrivateStateActive, isTrue);
      expect(fixture.compatibility.state.savedSearches, isEmpty);
      expect(fixture.cache.state.savedSearches, isEmpty);

      await fixture.coordinator.resume();

      expect(fixture.privateAuthority.activateCalls, 1);
      expect(fixture.journal.bytes, isNull);
      expect(fixture.selection.value, isNotNull);
    },
  );

  test('journal clear failure remains resumable completion pending', () async {
    final fixture = _MigrationFixture();
    await fixture.coordinator.inventory();
    await fixture.coordinator.prepare();
    fixture.journal.failNextDelete = true;

    final pending = await fixture.coordinator.finalizeAndActivate();

    expect(pending.stage, AtlasVaultPlaintextMigrationStage.completionPending);
    expect(fixture.journal.bytes, isNotNull);
    expect(fixture.privateAuthority.isEncryptedPrivateStateActive, isTrue);
    expect(fixture.compatibility.state.savedSearches, isEmpty);

    await fixture.coordinator.resume();

    expect(fixture.journal.bytes, isNull);
    expect(fixture.selection.value, isNotNull);
  });

  test(
    'acknowledged journal clear with lost response completes immediately',
    () async {
      final fixture = _MigrationFixture();
      await fixture.coordinator.inventory();
      await fixture.coordinator.prepare();
      fixture.journal.failAfterNextDelete = true;

      final completed = await fixture.coordinator.finalizeAndActivate();

      expect(completed.stage, isNull);
      expect(fixture.journal.bytes, isNull);
      expect(fixture.selection.value, isNotNull);
      expect(fixture.privateAuthority.isEncryptedPrivateStateActive, isTrue);
    },
  );
}

AtlasSavedSearch _savedSearch({
  String requestText = 'PRIVATE_QUERY',
  String createdAt = '2026-07-01T00:00:00Z',
  String updatedAt = '2026-07-02T00:00:00Z',
}) {
  return AtlasSavedSearch(
    name: 'UN roles',
    description: 'private description',
    request: AtlasSearchRequest(
      text: requestText,
      organizations: const <String>['UNICEF'],
      countriesISO3: const <String>['JPN'],
      limit: 50,
    ),
    createdAt: createdAt,
    updatedAt: updatedAt,
  );
}

AtlasApplicationRecord _trackerRecord({
  String status = 'saved',
  String appliedAt = '2026-07-03T00:00:00Z',
  String updatedAt = '2026-07-04T00:00:00Z',
}) {
  return AtlasApplicationRecord(
    id: 'tracker-record-1',
    jobKey: 'unicef:private-job',
    status: status,
    notes: 'tracker-private-notes',
    appliedAt: appliedAt,
    updatedAt: updatedAt,
  );
}

Map<String, Object?> _storedCompatibilitySearchRequest() {
  return <String, Object?>{
    'text': 'PRIVATE_QUERY',
    'status': <String>['open'],
    'organizations': <String>['UNICEF'],
    'source_ids': <String>[],
    'ats_families': <String>[],
    'cities': <String>[],
    'countries_iso3': <String>['JPN'],
    'regions': <String>[],
    'location_types': <String>['primary', 'duty_station', 'outposted'],
    'national_international': <String>[],
    'contract_categories': <String>[],
    'grade_systems': <String>[],
    'grade_families': <String>[],
    'grade_codes': <String>[],
    'ccog_codes': <String>[],
    'ccog_families': <String>[],
    'occupational_family_codes': <String>[],
    'occupational_medium_codes': <String>[],
    'mandate_network_codes': <String>[],
    'mandate_family_codes': <String>[],
    'capability_tags': <String>[],
    'contract_groups': <String>[],
    'seniority_groups': <String>[],
    'work_modalities': <String>[],
    'volunteer_kinds': <String>[],
    'unv_categories': <String>[],
    'unv_volunteer_types': <String>[],
    'closing_date_from': null,
    'closing_date_to': null,
    'posted_date_from': null,
    'posted_date_to': null,
    'min_location_confidence': 0.7,
    'min_grade_confidence': 0.7,
    'include_low_confidence': false,
    'exclude_expired_open': true,
    'limit': 50,
    'offset': 0,
    'sort': 'closing_date_asc',
  };
}

final class _StoredSavedSearchTransport implements AtlasTransport {
  _StoredSavedSearchTransport(this.storedSnapshot);

  final Map<String, Object?> storedSnapshot;

  @override
  Future<Object?> send(AtlasRequest request) async {
    expect(request.method, 'GET');
    expect(request.path, 'api/saved-searches');
    return <Object?>[storedSnapshot];
  }
}

final class _CapturingLegacyPrivateStateRestorer
    implements AtlasVaultLegacyPrivateStateRestoring {
  int calls = 0;
  AtlasVaultPlaintextPrivateState? restored;

  @override
  Future<void> restoreLegacyPrivateStateAfterRollback(
    AtlasVaultPlaintextPrivateState reviewedState,
  ) async {
    calls += 1;
    restored = reviewedState;
  }
}

final class _MigrationFixture {
  _MigrationFixture({
    this.profile = AtlasVaultPlaintextMigrationProfile.android,
  }) {
    final state = AtlasVaultPlaintextPrivateState(
      savedSearches: <AtlasSavedSearch>[_savedSearch()],
      trackerRecords: <AtlasApplicationRecord>[_trackerRecord()],
      authorityBaseURL: Uri.parse('http://atlas.test:8765'),
    );
    memory.state = state;
    compatibility.state = state;
    cache.state = AtlasLocalCacheMigrationPrivateState(
      savedSearches: state.savedSearches,
      trackerRecords: state.trackerRecords,
      privateSha256: '1' * 64,
      durablePrivateSha256:
          profile == AtlasVaultPlaintextMigrationProfile.windows
          ? '2' * 64
          : null,
      legacyPrivateSha256:
          profile == AtlasVaultPlaintextMigrationProfile.windows
          ? '3' * 64
          : null,
      retainedLegacyCachePresent:
          profile == AtlasVaultPlaintextMigrationProfile.windows,
      cacheCleanupComplete: false,
      requiresPhysicalCleanup:
          profile == AtlasVaultPlaintextMigrationProfile.windows,
      authorityBaseURL: Uri.parse('http://atlas.test:8765'),
    );
    coordinator = AtlasVaultPlaintextMigrationCoordinator(
      inMemorySource: memory,
      compatibilitySource: compatibility,
      cacheSource: cache,
      operationAdmission: admission,
      authorityAdmission: authorityAdmission,
      conditionalSavedSearchDelete: compatibility.conditionalDeleteSavedSearch,
      conditionalTrackerDelete: compatibility.conditionalDeleteTrackerRecord,
      journalStore: journal,
      selectedVaultStore: selection,
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      privateAuthority: privateAuthority,
      profile: profile,
      now: () => DateTime.utc(2026, 7, 29, 1, 2, 3),
      uuidProvider: _ids.call,
      vaultKeyProvider: () =>
          Uint8List.fromList(List<int>.generate(32, (index) => index + 1)),
      nonceProvider: () =>
          Uint8List.fromList(List<int>.generate(12, (index) => index + 1)),
    );
  }

  AtlasVaultPlaintextMigrationCoordinator restartCoordinator() {
    return AtlasVaultPlaintextMigrationCoordinator(
      inMemorySource: memory,
      compatibilitySource: compatibility,
      cacheSource: cache,
      operationAdmission: admission,
      authorityAdmission: authorityAdmission,
      conditionalSavedSearchDelete: compatibility.conditionalDeleteSavedSearch,
      conditionalTrackerDelete: compatibility.conditionalDeleteTrackerRecord,
      journalStore: journal,
      selectedVaultStore: selection,
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      privateAuthority: privateAuthority,
      profile: profile,
      now: () => DateTime.utc(2026, 7, 29, 1, 2, 3),
      uuidProvider: () => throw StateError('Unexpected UUID request.'),
      vaultKeyProvider: () => throw StateError('Unexpected key request.'),
      nonceProvider: () => throw StateError('Unexpected nonce request.'),
    );
  }

  final events = <String>[];
  final AtlasVaultPlaintextMigrationProfile profile;
  final admission = _OperationAdmission();
  final authorityAdmission = _AuthorityAdmission();
  final memory = _MemorySource();
  final compatibility = _CompatibilitySource();
  late final _CacheSource cache = _CacheSource(events);
  late final _JournalStore journal = _JournalStore(events);
  late final _SelectedVaultStore selection = _SelectedVaultStore(events);
  late final _SecureKeyStore keyStore = _SecureKeyStore(events);
  late final _LocalStoreIO localStore = _LocalStoreIO(events);
  AtlasVaultPlaintextPrivateState encryptedState =
      AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[_savedSearch()],
        trackerRecords: <AtlasApplicationRecord>[_trackerRecord()],
      );
  late final TestAtlasVaultPlaintextMigrationPrivateAuthority privateAuthority =
      TestAtlasVaultPlaintextMigrationPrivateAuthority(
        events: events,
        onHide: () {
          memory.state = AtlasVaultPlaintextPrivateState(
            savedSearches: const <AtlasSavedSearch>[],
            trackerRecords: const <AtlasApplicationRecord>[],
          );
        },
        readEncryptedState: () async => encryptedState,
      );
  final _ids = _SequenceIds(<String>[
    '11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222',
    '33333333-3333-4333-8333-333333333333',
    '44444444-4444-4444-8444-444444444444',
    '55555555-5555-4555-8555-555555555555',
    '66666666-6666-4666-8666-666666666666',
    '77777777-7777-4777-8777-777777777777',
    '88888888-8888-4888-8888-888888888888',
    '99999999-9999-4999-8999-999999999999',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  ]);
  late final AtlasVaultPlaintextMigrationCoordinator coordinator;

  int get totalDependencyCalls {
    return admission.drainCalls +
        memory.readCalls +
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

final class _OperationAdmission
    implements AtlasVaultPlaintextMigrationOperationAdmission {
  int drainCalls = 0;
  Completer<void>? _entered;
  Completer<void>? _release;

  Future<void> get entered => _entered?.future ?? Future<void>.value();

  void holdNextDrain() {
    _entered = Completer<void>();
    _release = Completer<void>();
  }

  void release() {
    _release?.complete();
  }

  @override
  Future<void> drainAdmittedPlaintextOperations() async {
    drainCalls += 1;
    _entered?.complete();
    await (_release?.future ?? Future<void>.value());
    _entered = null;
    _release = null;
  }
}

final class _AuthorityAdmission
    implements AtlasVaultPlaintextAuthorityAdmission {
  bool _migrationTransactionActive = false;

  @override
  Future<T> runLegacyPrivateOperation<T>(Future<T> Function() operation) {
    if (_migrationTransactionActive) {
      return Future<T>.error(
        const AtlasVaultPlaintextAuthorityAdmissionException(),
      );
    }
    return operation();
  }

  @override
  Future<T> runMigrationTransaction<T>(Future<T> Function() operation) async {
    if (_migrationTransactionActive) {
      return operation();
    }
    _migrationTransactionActive = true;
    try {
      return await operation();
    } finally {
      _migrationTransactionActive = false;
    }
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
  @override
  Uri authorityBaseURL = Uri.parse('http://atlas.test:8765');

  AtlasVaultPlaintextPrivateState state = AtlasVaultPlaintextPrivateState(
    savedSearches: const <AtlasSavedSearch>[],
    trackerRecords: const <AtlasApplicationRecord>[],
  );
  bool failReads = false;
  bool failAfterNextSavedSearchDelete = false;
  AtlasSavedSearch? replaceSavedSearchBeforeNextDelete;
  AtlasApplicationRecord? replaceTrackerBeforeNextDelete;
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
    final replacement = replaceSavedSearchBeforeNextDelete;
    if (replacement != null) {
      replaceSavedSearchBeforeNextDelete = null;
      state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[replacement],
        trackerRecords: state.trackerRecords,
      );
    }
    final existing = state.savedSearches
        .where((value) => value.name == name)
        .toList(growable: false);
    if (existing.isEmpty) {
      return false;
    }
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
    return true;
  }

  Future<AtlasConditionalDeleteOutcome> conditionalDeleteSavedSearch(
    AtlasSavedSearch expected,
  ) async {
    deleteSavedSearchCalls += 1;
    final replacement = replaceSavedSearchBeforeNextDelete;
    if (replacement != null) {
      replaceSavedSearchBeforeNextDelete = null;
      state = AtlasVaultPlaintextPrivateState(
        savedSearches: <AtlasSavedSearch>[replacement],
        trackerRecords: state.trackerRecords,
      );
    }
    final existing = state.savedSearches
        .where((value) => value.name == expected.name)
        .toList(growable: false);
    if (existing.isEmpty) {
      return AtlasConditionalDeleteOutcome.absent;
    }
    if (!_sameJson(existing.single.toJson(), expected.toJson())) {
      return AtlasConditionalDeleteOutcome.preconditionFailed;
    }
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches
          .where((value) => value.name != expected.name)
          .toList(growable: false),
      trackerRecords: state.trackerRecords,
    );
    if (failAfterNextSavedSearchDelete) {
      failAfterNextSavedSearchDelete = false;
      throw StateError('interrupted');
    }
    return AtlasConditionalDeleteOutcome.deleted;
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) async {
    deleteTrackerCalls += 1;
    final replacement = replaceTrackerBeforeNextDelete;
    if (replacement != null) {
      replaceTrackerBeforeNextDelete = null;
      state = AtlasVaultPlaintextPrivateState(
        savedSearches: state.savedSearches,
        trackerRecords: <AtlasApplicationRecord>[replacement],
      );
    }
    final existing = state.trackerRecords
        .where((value) => value.id == recordId)
        .toList(growable: false);
    if (existing.isEmpty) {
      return false;
    }
    state = AtlasVaultPlaintextPrivateState(
      savedSearches: state.savedSearches,
      trackerRecords: state.trackerRecords
          .where((value) => value.id != recordId)
          .toList(growable: false),
    );
    return true;
  }

  Future<AtlasConditionalDeleteOutcome> conditionalDeleteTrackerRecord(
    AtlasApplicationRecord expected,
  ) async {
    deleteTrackerCalls += 1;
    final replacement = replaceTrackerBeforeNextDelete;
    if (replacement != null) {
      replaceTrackerBeforeNextDelete = null;
      state = AtlasVaultPlaintextPrivateState(
        savedSearches: state.savedSearches,
        trackerRecords: <AtlasApplicationRecord>[replacement],
      );
    }
    final existing = state.trackerRecords
        .where((value) => value.id == expected.id)
        .toList(growable: false);
    if (existing.isEmpty) {
      return AtlasConditionalDeleteOutcome.absent;
    }
    if (!_sameJson(existing.single.toJson(), expected.toJson())) {
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

final class _CacheSource
    implements
        AtlasLocalCacheMigrationSource,
        AtlasLocalCacheMigrationCleanupSource {
  _CacheSource(this.events);

  final List<String> events;
  AtlasLocalCacheMigrationPrivateState state =
      AtlasLocalCacheMigrationPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
        privateSha256: null,
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      );
  int readCalls = 0;
  int removeCalls = 0;
  int cleanupCalls = 0;
  bool failAfterNextRemove = false;
  bool failAfterNextCleanup = false;

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
    events.add('cache.remove');
    final authorityBaseURL = state.authorityBaseURL;
    state = AtlasLocalCacheMigrationPrivateState(
      savedSearches: const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
      privateSha256: null,
      authorityBaseURL: authorityBaseURL,
    );
    if (failAfterNextRemove) {
      failAfterNextRemove = false;
      throw StateError('interrupted after cache scrub');
    }
  }

  @override
  Future<void> completePrivateStateCleanupForMigration({
    required String? expectedPrivateSha256,
  }) async {
    cleanupCalls += 1;
    events.add('cache.cleanup');
    if (state.privateSha256 != null &&
        state.privateSha256 != expectedPrivateSha256 &&
        !state.cacheCleanupPending) {
      throw StateError('stale');
    }
    final authorityBaseURL = state.authorityBaseURL;
    if (failAfterNextCleanup) {
      failAfterNextCleanup = false;
      state = AtlasLocalCacheMigrationPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: state.trackerRecords,
        privateSha256: '4' * 64,
        durablePrivateSha256: null,
        legacyPrivateSha256: state.legacyPrivateSha256,
        retainedLegacyCachePresent: true,
        cacheCleanupPending: true,
        cacheCleanupComplete: false,
        requiresPhysicalCleanup: true,
        authorityBaseURL: authorityBaseURL,
      );
      throw StateError('interrupted during cache cleanup');
    }
    state = AtlasLocalCacheMigrationPrivateState(
      savedSearches: const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
      privateSha256: null,
      durablePrivateSha256: null,
      legacyPrivateSha256: null,
      retainedLegacyCachePresent: false,
      cacheCleanupPending: false,
      cacheCleanupComplete: true,
      requiresPhysicalCleanup: false,
      authorityBaseURL: authorityBaseURL,
    );
  }
}

final class _JournalStore implements AtlasVaultProtectedMigrationJournalStore {
  _JournalStore(this.events);

  final List<String> events;
  Uint8List? bytes;
  bool failAfterNextCreate = false;
  bool failNextDelete = false;
  bool failAfterNextDelete = false;
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
    if (failAfterNextCreate) {
      failAfterNextCreate = false;
      throw StateError('interrupted');
    }
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
    if (failNextDelete) {
      failNextDelete = false;
      throw StateError('interrupted');
    }
    bytes = null;
    events.add('journal.delete');
    if (failAfterNextDelete) {
      failAfterNextDelete = false;
      throw StateError('interrupted');
    }
  }
}

final class _SelectedVaultStore implements AtlasVaultSelectedVaultStore {
  _SelectedVaultStore(this.events);

  final List<String> events;
  String? value;
  int readCalls = 0;
  int createCalls = 0;
  int clearCalls = 0;
  bool failAfterNextCreate = false;
  Future<void> Function()? beforeNextCreate;

  @override
  Future<String?> read() async {
    readCalls += 1;
    return value;
  }

  @override
  Future<void> create(String vaultId) async {
    createCalls += 1;
    await beforeNextCreate?.call();
    beforeNextCreate = null;
    if (value != null) {
      throw StateError('duplicate');
    }
    value = vaultId;
    events.add('selection.create');
    if (failAfterNextCreate) {
      failAfterNextCreate = false;
      throw StateError('interrupted');
    }
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
  bool failAfterNextDelete = false;
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
    if (failAfterNextDelete) {
      failAfterNextDelete = false;
      throw StateError('interrupted');
    }
  }
}

final class _LocalStoreIO implements AtlasVaultLocalStoreIO {
  _LocalStoreIO(this.events);

  final List<String> events;
  vault.AtlasVaultLocalStore? store;
  bool failAfterNextCreate = false;
  bool failAfterNextDelete = false;
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
    if (failAfterNextCreate) {
      failAfterNextCreate = false;
      throw StateError('interrupted');
    }
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
    if (failAfterNextDelete) {
      failAfterNextDelete = false;
      throw StateError('interrupted');
    }
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
