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
  });
}

AtlasLocalCacheSnapshot _snapshot({required DateTime savedAt}) {
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
    savedSearches: [
      AtlasSavedSearch(
        name: 'Search 1',
        description: 'Cached saved search',
        request: const AtlasSearchRequest(text: 'analyst'),
      ),
    ],
    trackerRecords: [
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
