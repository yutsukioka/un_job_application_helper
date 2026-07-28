import 'dart:convert';
import 'dart:io';

import 'package:atlas/atlas.dart';
import 'package:flutter_test/flutter_test.dart';

final _fixtureSavedAt = DateTime.utc(2026, 7, 2, 12);
final _fixtureReadAt = DateTime.utc(2026, 7, 3, 13);

void main() {
  group('AtlasLocalCacheStore', () {
    late Directory tempDir;
    late File cacheFile;

    setUp(() async {
      tempDir = await Directory.systemTemp.createTemp('atlas_cache_test_');
      cacheFile = File('${tempDir.path}/atlas-local-cache.json');
    });

    tearDown(() async {
      if (await tempDir.exists()) {
        await tempDir.delete(recursive: true);
      }
    });

    test('writes and reads a full local search snapshot', () async {
      final store = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => _fixtureReadAt,
      );
      final snapshot = _snapshot(savedAt: _fixtureSavedAt);

      await store.write(snapshot);
      final restored = await store.read();

      expect(restored, isNotNull);
      expect(restored!.baseURL.toString(), 'http://atlas.test:8765');
      expect(restored.searchResponse.total, 1);
      expect(restored.searchResponse.results.single.title, 'Cached Analyst');
      expect(restored.cachedAllJobs, hasLength(1));
      expect(restored.cachedAllJobs.single.city, 'Tokyo');
      expect(restored.cachedAllJobs.single.countryISO3, 'JPN');
      expect(restored.healthSummary?.openJobs, 128);
      expect(restored.savedSearches.single.name, 'Search 1');
      expect(restored.trackerRecords.single.jobKey, 'undp_oracle_hcm:34063');
      expect(restored.updateRuns.single.inserted, 1);
      expect(restored.sources.single.openJobs, 1);
      expect(restored.cachedJobDetails, hasLength(1));
      expect(
        restored.cachedJobDetails['undp_oracle_hcm:34063']?.title,
        'Cached Analyst Detail',
      );
      expect(restored.isStale(now: DateTime.utc(2026, 7, 3, 13)), isTrue);
      expect(restored.isExpired(now: DateTime.utc(2026, 7, 9, 12, 1)), isTrue);
    });

    test('retains a snapshot exactly at the retention boundary', () async {
      final store = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => _fixtureSavedAt.add(AtlasLocalCacheSnapshot.retainFor),
      );
      await store.write(_snapshot(savedAt: _fixtureSavedAt));

      expect(await store.read(), isNotNull);
    });

    test(
      'rejects a snapshot immediately after the retention boundary',
      () async {
        final store = AtlasLocalCacheStore(
          file: cacheFile,
          now: () => _fixtureSavedAt
              .add(AtlasLocalCacheSnapshot.retainFor)
              .add(const Duration(microseconds: 1)),
        );
        await store.write(_snapshot(savedAt: _fixtureSavedAt));

        expect(await store.read(), isNull);
      },
    );

    test('corrupted cache is ignored without crashing', () async {
      await cacheFile.writeAsString('{not valid json');
      final store = AtlasLocalCacheStore(file: cacheFile);

      final restored = await store.read();

      expect(restored, isNull);
    });

    test('clear removes the persisted snapshot', () async {
      final store = AtlasLocalCacheStore(file: cacheFile);
      await store.write(_snapshot(savedAt: _fixtureSavedAt));
      await File('${cacheFile.path}.tmp').writeAsString('stale temp');

      await store.clear();

      expect(await cacheFile.exists(), isFalse);
      expect(await File('${cacheFile.path}.tmp').exists(), isFalse);
      expect(await store.read(), isNull);
    });

    test('private-state detection and public-only copy are immutable', () {
      final snapshot = _snapshot(savedAt: _fixtureSavedAt);

      final publicOnly = snapshot.withoutPrivateState();

      expect(snapshot.containsPrivateState, isTrue);
      expect(publicOnly.containsPrivateState, isFalse);
      expect(publicOnly.savedSearches, isEmpty);
      expect(publicOnly.trackerRecords, isEmpty);
      expect(snapshot.savedSearches, hasLength(1));
      expect(snapshot.trackerRecords, hasLength(1));
      expect(publicOnly.baseURL, snapshot.baseURL);
      expect(publicOnly.savedAt, snapshot.savedAt);
      expect(
        publicOnly.searchRequest.toJson(),
        snapshot.searchRequest.toJson(),
      );
      expect(
        publicOnly.searchResponse.toJson(),
        snapshot.searchResponse.toJson(),
      );
      expect(publicOnly.cachedAllJobs, snapshot.cachedAllJobs);
      expect(publicOnly.cachedJobDetails, snapshot.cachedJobDetails);
      expect(publicOnly.updateRuns, snapshot.updateRuns);
      expect(publicOnly.sources, snapshot.sources);
    });

    test(
      'active plaintext guard rejects before file or temporary mutation',
      () async {
        const original = '{"public":"unchanged"}';
        await cacheFile.writeAsString(original);
        final temporaryFile = File('${cacheFile.path}.tmp');
        final store = AtlasLocalCacheStore(
          file: cacheFile,
          privateStateProtectionActive: () => true,
        );

        await expectLater(
          store.write(_snapshot(savedAt: _fixtureSavedAt)),
          throwsA(isA<AtlasPrivateStatePlaintextWriteBlocked>()),
        );

        expect(await cacheFile.readAsString(), original);
        expect(await temporaryFile.exists(), isFalse);
      },
    );

    test(
      'active plaintext guard accepts an explicit public-only copy',
      () async {
        final store = AtlasLocalCacheStore(
          file: cacheFile,
          now: () => _fixtureReadAt,
          privateStateProtectionActive: () => true,
        );

        await store.write(
          _snapshot(savedAt: _fixtureSavedAt).withoutPrivateState(),
        );
        final restored = await store.read();

        expect(restored, isNotNull);
        expect(restored!.containsPrivateState, isFalse);
        expect(restored.savedSearches, isEmpty);
        expect(restored.trackerRecords, isEmpty);
      },
    );

    test(
      'protection transition during write blocks before cache commit',
      () async {
        final nestedFile = File(
          '${tempDir.path}/not-created/atlas-local-cache.json',
        );
        var protectionActive = false;
        final store = AtlasLocalCacheStore(
          file: nestedFile,
          privateStateProtectionActive: () => protectionActive,
        );

        final write = store.write(_snapshot(savedAt: _fixtureSavedAt));
        protectionActive = true;

        await expectLater(
          write,
          throwsA(isA<AtlasPrivateStatePlaintextWriteBlocked>()),
        );
        expect(await nestedFile.exists(), isFalse);
        expect(await File('${nestedFile.path}.tmp').exists(), isFalse);
      },
    );

    test('migration reads expired private state strictly', () async {
      final expiredNow = _fixtureSavedAt
          .add(AtlasLocalCacheSnapshot.retainFor)
          .add(const Duration(days: 30));
      final store = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => expiredNow,
      );
      await store.write(_snapshot(savedAt: _fixtureSavedAt));

      expect(await store.read(), isNull);
      final migrationState = await store.readPrivateStateForMigration();

      expect(migrationState.cachePresent, isTrue);
      expect(
        migrationState.authorityBaseURL,
        Uri.parse('http://atlas.test:8765'),
      );
      expect(migrationState.savedSearches.single.name, 'Search 1');
      expect(
        migrationState.trackerRecords.single.jobKey,
        'undp_oracle_hcm:34063',
      );
      expect(migrationState.privateSha256, matches(RegExp(r'^[0-9a-f]{64}$')));
    });

    test('migration canonicalizes valid legacy backend timestamps', () async {
      final store = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => _fixtureReadAt,
      );
      await store.write(
        _snapshot(
          savedAt: _fixtureSavedAt,
          savedSearches: <AtlasSavedSearch>[
            AtlasSavedSearch(
              name: 'Search 1',
              description: 'Cached saved search',
              request: const AtlasSearchRequest(text: 'analyst'),
              createdAt: '2026-07-01T01:02:03.456789+00:00',
              updatedAt: '2026-07-02T02:03:04.125Z',
            ),
          ],
          trackerRecords: <AtlasApplicationRecord>[
            AtlasApplicationRecord(
              id: 'undp_oracle_hcm-34063',
              jobKey: 'undp_oracle_hcm:34063',
              status: 'saved',
              appliedAt: '2026-07-03T03:04:05.500000+00:00',
              updatedAt: '2026-07-04T04:05:06.999999Z',
            ),
          ],
        ),
      );

      final migrationState = await store.readPrivateStateForMigration();

      expect(
        migrationState.savedSearches.single.createdAt,
        '2026-07-01T01:02:03Z',
      );
      expect(
        migrationState.savedSearches.single.updatedAt,
        '2026-07-02T02:03:04Z',
      );
      expect(
        migrationState.trackerRecords.single.appliedAt,
        '2026-07-03T03:04:05Z',
      );
      expect(
        migrationState.trackerRecords.single.updatedAt,
        '2026-07-04T04:05:06Z',
      );
    });

    test('migration rejects ambiguous legacy backend timestamps', () async {
      final store = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => _fixtureReadAt,
      );
      await store.write(
        _snapshot(
          savedAt: _fixtureSavedAt,
          savedSearches: <AtlasSavedSearch>[
            AtlasSavedSearch(
              name: 'Search 1',
              request: const AtlasSearchRequest(text: 'analyst'),
              createdAt: '2026-07-01T01:02:03',
            ),
          ],
        ),
      );

      await expectLater(
        store.readPrivateStateForMigration(),
        throwsA(isA<AtlasLocalCacheMigrationException>()),
      );
    });

    test('migration enforces the ISO-8601 maximum UTC offset', () async {
      for (final timestamp in <String>[
        '2026-07-01T01:02:03+14:01',
        '2026-07-01T01:02:03-15:00',
      ]) {
        final store = AtlasLocalCacheStore(
          file: cacheFile,
          now: () => _fixtureReadAt,
        );
        await store.write(
          _snapshot(
            savedAt: _fixtureSavedAt,
            savedSearches: <AtlasSavedSearch>[
              AtlasSavedSearch(
                name: 'Search 1',
                request: const AtlasSearchRequest(text: 'analyst'),
                createdAt: timestamp,
              ),
            ],
          ),
        );

        await expectLater(
          store.readPrivateStateForMigration(),
          throwsA(isA<AtlasLocalCacheMigrationException>()),
        );
      }

      final store = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => _fixtureReadAt,
      );
      await store.write(
        _snapshot(
          savedAt: _fixtureSavedAt,
          savedSearches: <AtlasSavedSearch>[
            AtlasSavedSearch(
              name: 'Search 1',
              request: const AtlasSearchRequest(text: 'analyst'),
              createdAt: '2026-07-01T14:02:03+14:00',
            ),
          ],
        ),
      );

      final migrationState = await store.readPrivateStateForMigration();
      expect(
        migrationState.savedSearches.single.createdAt,
        '2026-07-01T00:02:03Z',
      );
    });

    test(
      'migration removal verifies digest and preserves public state',
      () async {
        final store = AtlasLocalCacheStore(
          file: cacheFile,
          now: () => _fixtureReadAt,
        );
        await store.write(_snapshot(savedAt: _fixtureSavedAt));
        final migrationState = await store.readPrivateStateForMigration();

        await store.removePrivateStateForMigration(
          expectedPrivateSha256: migrationState.privateSha256!,
        );

        final restored = await store.read();
        final migrationRestored = await store.readPrivateStateForMigration();
        expect(restored, isNotNull);
        expect(migrationRestored.cachePresent, isTrue);
        expect(restored!.containsPrivateState, isFalse);
        expect(restored.searchResponse.results.single.title, 'Cached Analyst');
        expect(restored.cachedJobDetails, hasLength(1));
        expect(restored.updateRuns, hasLength(1));
        expect(restored.sources, hasLength(1));
      },
    );

    test(
      'migration distinguishes an absent cache from a scrubbed cache',
      () async {
        final store = AtlasLocalCacheStore(file: cacheFile);

        final migrationState = await store.readPrivateStateForMigration();

        expect(migrationState.cachePresent, isFalse);
        expect(migrationState.privateSha256, isNull);
        expect(migrationState.savedSearches, isEmpty);
        expect(migrationState.trackerRecords, isEmpty);
      },
    );

    test('migration digest mismatch performs no cache write', () async {
      final store = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => _fixtureReadAt,
      );
      await store.write(_snapshot(savedAt: _fixtureSavedAt));
      final before = await cacheFile.readAsBytes();

      await expectLater(
        store.removePrivateStateForMigration(expectedPrivateSha256: '0' * 64),
        throwsA(isA<AtlasLocalCacheMigrationException>()),
      );

      expect(await cacheFile.readAsBytes(), orderedEquals(before));
    });

    test(
      'migration read rejects malformed cache instead of treating it empty',
      () async {
        await cacheFile.writeAsString(
          '{"schema_version":1,"saved_searches":{}}',
        );
        final store = AtlasLocalCacheStore(file: cacheFile);

        await expectLater(
          store.readPrivateStateForMigration(),
          throwsA(isA<AtlasLocalCacheMigrationException>()),
        );
      },
    );

    test(
      'migration strictly validates every cached public row and object',
      () async {
        final store = AtlasLocalCacheStore(
          file: cacheFile,
          now: () => _fixtureReadAt,
        );
        await store.write(_snapshot(savedAt: _fixtureSavedAt));
        final original = Map<String, Object?>.from(
          jsonDecode(await cacheFile.readAsString()) as Map,
        );
        final corruptions = <void Function(Map<String, Object?>)>[
          (value) => value['search_request'] = <String, Object?>{},
          (value) {
            final response = Map<String, Object?>.from(
              value['search_response']! as Map,
            )..['results'] = <Object?>[<String, Object?>{}];
            value['search_response'] = response;
          },
          (value) => value['cached_all_jobs'] = <Object?>[<String, Object?>{}],
          (value) => value['health_summary'] = <String, Object?>{},
          (value) => value['cached_job_details'] = <String, Object?>{
            'undp_oracle_hcm:34063': <String, Object?>{},
          },
          (value) => value['update_runs'] = <Object?>[<String, Object?>{}],
          (value) => value['sources'] = <Object?>[<String, Object?>{}],
        ];

        for (final corrupt in corruptions) {
          final candidate = Map<String, Object?>.from(
            jsonDecode(jsonEncode(original)) as Map,
          );
          corrupt(candidate);
          await cacheFile.writeAsString(jsonEncode(candidate));

          await expectLater(
            store.readPrivateStateForMigration(),
            throwsA(isA<AtlasLocalCacheMigrationException>()),
          );
        }
      },
    );
  });
}

AtlasLocalCacheSnapshot _snapshot({
  required DateTime savedAt,
  List<AtlasSavedSearch>? savedSearches,
  List<AtlasApplicationRecord>? trackerRecords,
}) {
  return AtlasLocalCacheSnapshot(
    schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
    baseURL: Uri.parse('http://atlas.test:8765'),
    savedAt: savedAt,
    searchRequest: const AtlasSearchRequest(limit: 50),
    searchResponse: AtlasSearchResponse(
      total: 1,
      limit: 50,
      offset: 0,
      facets: const {
        'organizations': {'UNDP': 1},
      },
      facetLabels: const {
        'organizations': {'UNDP': 'UNDP'},
      },
      unclassifiedCount: 0,
      results: [
        JobSearchResult(
          jobKey: 'undp_oracle_hcm:34063',
          title: 'Cached Analyst',
          organization: 'UNDP',
          sourceID: 'undp_oracle_hcm',
          dutyStation: 'Tokyo, Japan',
          city: 'Tokyo',
          countryISO3: 'JPN',
          gradeCode: 'P-2',
          standardSeniorityTier: 'T2_JUNIOR_PROFESSIONAL',
          contractLabel: 'Fixed term',
          workModality: 'Onsite',
          closingDate: DateTime.utc(2026, 7, 30, 23, 59),
          needsReview: false,
          scoreReasons: const [],
          matchSummary: 'Cached row',
          description: 'Cached description',
        ),
      ],
    ),
    healthSummary: AtlasHealthSummary(
      status: 'ok',
      openJobs: 128,
      enabledSources: 12,
      lastSyncAt: '2026-07-02T02:38:47Z',
    ),
    savedSearches:
        savedSearches ??
        [
          AtlasSavedSearch(
            name: 'Search 1',
            description: 'Cached saved search',
            request: const AtlasSearchRequest(text: 'analyst'),
          ),
        ],
    trackerRecords:
        trackerRecords ??
        [
          AtlasApplicationRecord(
            id: 'undp_oracle_hcm-34063',
            jobKey: 'undp_oracle_hcm:34063',
            status: 'saved',
          ),
        ],
    updateRuns: [
      AtlasSourceRun(
        sourceID: 'undp_oracle_hcm',
        fetched: 7,
        inserted: 1,
        updated: 2,
        missing: 0,
        closed: 0,
        observedAt: '2026-07-02T00:00:00Z',
      ),
    ],
    sources: [
      AtlasSourceSummary(
        sourceID: 'undp_oracle_hcm',
        organization: 'UNDP Oracle HCM',
        totalJobs: 4,
        openJobs: 1,
        lastSeenAt: '2026-07-02T00:00:00Z',
        healthStatus: 'ok',
      ),
    ],
    cachedJobDetails: {
      'undp_oracle_hcm:34063': AtlasJobDetail(
        jobKey: 'undp_oracle_hcm:34063',
        title: 'Cached Analyst Detail',
        description: 'Cached full detail',
        status: 'open',
        applyURL: Uri.parse('https://example.org/apply'),
        sourceURL: Uri.parse('https://example.org/source'),
        displaySections: [
          AtlasDetailSection(
            title: 'Responsibilities',
            body: 'Cached responsibilities',
            rows: [
              AtlasDetailRow(label: 'Duty', value: 'Coordinate delivery.'),
            ],
          ),
        ],
      ),
    },
  );
}
