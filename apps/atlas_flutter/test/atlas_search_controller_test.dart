import 'dart:async';
import 'dart:io';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _cacheFixtureSavedAt = DateTime.utc(2026, 7, 2, 12);
final _cacheFixtureNow = DateTime.utc(2026, 7, 3, 12);

void main() {
  test('active filter chips use value equality', () {
    const openChip = AtlasActiveFilterChip(
      id: 'status.open',
      title: 'Open only',
    );
    const matchingChip = AtlasActiveFilterChip(
      id: 'status.open',
      title: 'Open only',
    );
    const differentChip = AtlasActiveFilterChip(
      id: 'deadline.soon',
      title: 'Closing soon',
    );

    expect(openChip, matchingChip);
    expect(openChip == differentChip, isFalse);
    expect(openChip.hashCode, matchingChip.hashCode);
  });

  test('controller saves server refreshes search and updates sort', () async {
    final tempDir = await Directory.systemTemp.createTemp(
      'atlas_controller_refresh_cache_test_',
    );
    addTearDown(() async {
      if (await tempDir.exists()) {
        await tempDir.delete(recursive: true);
      }
    });
    final store = AtlasLocalCacheStore(
      file: File('${tempDir.path}/atlas-local-cache.json'),
    );
    final transport = _RecordingTransport();
    final controller = AtlasAppController(
      localCacheStore: store,
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
    );
    addTearDown(controller.dispose);

    expect(
      controller.statusSubtitle,
      'Offline until API connection is configured',
    );

    await controller.testConnection(Uri.parse('http://atlas.test:8765'));
    expect(controller.connectionStatus, 'Connected');
    expect(
      controller.connectionMessage,
      'Connected: ok, 128 open jobs, 12 enabled sources.',
    );
    expect(controller.healthSummary?.openJobs, 128);
    expect(controller.updateRuns.single.sourceID, 'undp_oracle_hcm');
    expect(controller.sources.single.openJobs, 1);
    expect(controller.statusSubtitle, 'Connected to http://10.253.1.43:8765');

    await controller.saveAndReload(Uri.parse('http://atlas.test:8765'));
    expect(controller.baseURL.toString(), 'http://atlas.test:8765');
    expect(controller.total, 1);
    expect(controller.cachedJobCount, 1);
    expect(controller.results.single.title, 'Programme Analyst');
    expect(controller.resultCountLabel, '1 searchable result');
    expect(controller.countReconciliationSummary, contains('127'));
    expect(
      controller.connectionMessage,
      'Saved http://atlas.test:8765 and refreshed 1 job.',
    );
    expect(controller.statusSubtitle, startsWith('Local save · updated '));
    final persistedAfterReload = await store.read();
    expect(persistedAfterReload, isNotNull);
    expect(persistedAfterReload!.baseURL.toString(), 'http://atlas.test:8765');
    expect(persistedAfterReload.searchResponse.total, 1);
    expect(
      persistedAfterReload.searchResponse.results.single.title,
      'Programme Analyst',
    );

    controller.connectionMessage = 'Dismiss me';
    controller.clearConnectionMessage();
    expect(controller.connectionMessage, isNull);
    controller.clearConnectionMessage();
    expect(controller.connectionMessage, isNull);

    await controller.setSortOrder(SortOrder.newestPosted);
    expect(controller.sortOrder, SortOrder.newestPosted);
    expect(transport.searchSorts.last, 'posted_date_desc');
    final searchCount = transport.searchSorts.length;

    await controller.setSortOrder(SortOrder.newestPosted);
    expect(transport.searchSorts, hasLength(searchCount));

    await controller.toggleQuickFilter('Remote');
    expect(controller.filters.isRemoteOnly, isTrue);
    expect(
      transport.searchBodies.last['work_modalities'],
      containsAll(AtlasSearchFilters.remoteWorkModalities),
    );

    await controller.removeActiveFilter('work.modalities');
    expect(controller.filters.isRemoteOnly, isFalse);

    await controller.toggleQuickFilter('Remote');
    await controller.toggleQuickFilter('Closing soon');
    await controller.saveCurrentSearch();
    expect(transport.savedSearchNames, ['Search 1']);
    expect(controller.savedSearches.single.name, 'Search 1');
    final persistedAfterSearchSave = await store.read();
    expect(persistedAfterSearchSave!.savedSearches.single.name, 'Search 1');

    await controller.removeActiveFilter('work.modalities');
    await controller.removeActiveFilter('deadline.soon');
    expect(controller.filters.isRemoteOnly, isFalse);
    expect(controller.filters.closingSoon, isFalse);

    await controller.runSavedSearch(controller.savedSearches.single);
    expect(controller.filters.isRemoteOnly, isTrue);
    expect(controller.filters.closingSoon, isTrue);
  });

  test('controller reports validation and local refresh failures', () async {
    final controller = AtlasAppController(
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: _FailingTransport()),
    );
    addTearDown(controller.dispose);

    controller.reportValidationError('Bad URL');
    expect(controller.connectionStatus, 'Not connected');
    expect(controller.connectionMessage, 'Bad URL');

    await controller.refreshLocalSave();
    expect(controller.connectionStatus, 'Not connected');
    expect(
      controller.connectionMessage,
      startsWith('Local save refresh failed:'),
    );

    controller.isSearching = true;
    expect(
      controller.statusSubtitle,
      'Refreshing from http://10.253.1.43:8765',
    );
  });

  test(
    'controller loads persisted cache before offline refresh fails',
    () async {
      expect(
        _cacheFixtureNow.difference(_cacheFixtureSavedAt),
        lessThanOrEqualTo(AtlasLocalCacheSnapshot.retainFor),
      );
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_controller_cache_test_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final store = AtlasLocalCacheStore(
        file: File('${tempDir.path}/atlas-local-cache.json'),
        now: () => _cacheFixtureNow,
      );
      await store.write(
        AtlasLocalCacheSnapshot(
          schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
          baseURL: Uri.parse('http://atlas.cached:8765'),
          savedAt: _cacheFixtureSavedAt,
          searchRequest: const AtlasSearchRequest(text: 'Programme'),
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
            results: [JobSearchResult.fromJson(_jobJson)],
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
              description: 'Cached search',
              request: const AtlasSearchRequest(text: 'Programme'),
            ),
          ],
          trackerRecords: [
            AtlasApplicationRecord(
              id: 'undp_oracle_hcm-34063',
              jobKey: 'undp_oracle_hcm:34063',
              status: 'saved',
            ),
          ],
        ),
      );
      final controller = AtlasAppController(
        localCacheStore: store,
        now: () => _cacheFixtureNow,
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: _FailingTransport()),
      );
      addTearDown(controller.dispose);

      await controller.loadPersistedCache();

      expect(controller.baseURL.toString(), 'http://atlas.cached:8765');
      expect(controller.query, 'Programme');
      expect(controller.total, 1);
      expect(controller.results.single.title, 'Programme Analyst');
      expect(controller.savedSearches.single.name, 'Search 1');
      expect(controller.isJobSaved('undp_oracle_hcm:34063'), isTrue);
      expect(controller.connectionStatus, 'Offline (cached)');
      expect(controller.statusSubtitle, startsWith('Local save · updated '));
      expect(controller.connectionMessage, isNull);

      await controller.refreshLocalSave();

      expect(controller.total, 1);
      expect(controller.results.single.title, 'Programme Analyst');
      expect(
        controller.connectionMessage,
        startsWith('Local save refresh failed:'),
      );

      await controller.clearPersistedCache();

      expect(await store.read(), isNull);
      expect(controller.results, isEmpty);
      expect(controller.total, 0);
      expect(controller.connectionMessage, 'Local cache cleared.');
    },
  );

  test(
    'controller filters cached rows offline and cascades location and grade facets',
    () async {
      expect(
        _cacheFixtureNow.difference(_cacheFixtureSavedAt),
        lessThanOrEqualTo(AtlasLocalCacheSnapshot.retainFor),
      );
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_controller_cascade_cache_test_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final store = AtlasLocalCacheStore(
        file: File('${tempDir.path}/atlas-local-cache.json'),
        now: () => _cacheFixtureNow,
      );
      final cachedJobs = [
        _facetJob(
          jobKey: 'job:tokyo-p1',
          title: 'Tokyo Programme Officer',
          city: 'Tokyo',
          countryISO3: 'JPN',
          gradeCode: 'P-1',
          standardSeniorityTier: 'T1_ENTRY_SUPPORT',
        ),
        _facetJob(
          jobKey: 'job:osaka-g5',
          title: 'Osaka Admin Assistant',
          city: 'Osaka',
          countryISO3: 'JPN',
          gradeCode: 'G-5',
          standardSeniorityTier: 'T2_JUNIOR_PROFESSIONAL',
        ),
        _facetJob(
          jobKey: 'job:nairobi-p4',
          title: 'Nairobi Senior Specialist',
          city: 'Nairobi',
          countryISO3: 'KEN',
          gradeCode: 'P-4',
          standardSeniorityTier: 'T4_SENIOR_PROFESSIONAL',
        ),
      ];
      await store.write(
        AtlasLocalCacheSnapshot(
          schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
          baseURL: Uri.parse('http://atlas.cached:8765'),
          savedAt: _cacheFixtureSavedAt,
          searchRequest: const AtlasSearchRequest(),
          searchResponse: AtlasSearchResponse(
            total: cachedJobs.length,
            limit: cachedJobs.length,
            offset: 0,
            results: cachedJobs,
            facets: const {},
            facetLabels: const {},
            unclassifiedCount: 0,
          ),
          cachedAllJobs: cachedJobs,
        ),
      );
      final controller = AtlasAppController(
        localCacheStore: store,
        now: () => _cacheFixtureNow,
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: _FailingTransport()),
      );
      addTearDown(controller.dispose);

      await controller.loadPersistedCache();
      expect(controller.total, 3);

      await controller.applyFilters(
        controller.filters.copyWith(countryISO3: 'JPN'),
      );
      expect(controller.connectionStatus, 'Offline (cached)');
      expect(controller.total, 2);
      expect(
        controller.results.map((job) => job.city),
        containsAll(['Tokyo', 'Osaka']),
      );
      expect(
        controller
            .availabilityFacetOptions('cities')
            .map((option) => option.id),
        containsAll(['Tokyo', 'Osaka']),
      );
      expect(
        controller
            .availabilityFacetOptions('cities')
            .map((option) => option.id),
        isNot(contains('Nairobi')),
      );

      await controller.applyFilters(
        controller.filters.copyWith(city: 'Tokyo', countryISO3: ''),
      );
      expect(controller.total, 1);
      expect(controller.results.single.title, contains('Tokyo'));
      expect(
        controller
            .availabilityFacetOptions('countries')
            .map((option) => option.id),
        ['JPN'],
      );

      await controller.applyFilters(AtlasSearchFilters(city: 'Tokyo, Nairobi'));
      expect(controller.total, 2);
      expect(
        controller.results.map((job) => job.city),
        containsAll(['Tokyo', 'Nairobi']),
      );
      expect(
        controller
            .availabilityFacetOptions('countries')
            .map((option) => option.id),
        containsAll(['JPN', 'KEN']),
      );

      await controller.applyFilters(
        AtlasSearchFilters(countryISO3: 'JPN, KEN'),
      );
      expect(controller.total, 3);
      expect(
        controller
            .availabilityFacetOptions('cities')
            .map((option) => option.id),
        containsAll(['Tokyo', 'Osaka', 'Nairobi']),
      );

      await controller.applyFilters(
        AtlasSearchFilters(seniorityGroups: {'entry_junior'}),
      );
      expect(controller.total, 2);
      expect(
        controller
            .availabilityFacetOptions('grades')
            .map((option) => option.id),
        containsAll(['P1', 'G5']),
      );
      expect(
        controller
            .availabilityFacetOptions('grades')
            .map((option) => option.id),
        isNot(contains('P4')),
      );

      await controller.applyFilters(AtlasSearchFilters(gradeCodes: {'P1'}));
      expect(controller.total, 1);
      expect(
        controller
            .availabilityFacetOptions('seniority_groups')
            .map((option) => option.id),
        ['entry_junior'],
      );
    },
  );

  test(
    'controller excludes deadline-past open rows from cached open-only search',
    () async {
      expect(
        _cacheFixtureNow.difference(_cacheFixtureSavedAt),
        lessThanOrEqualTo(AtlasLocalCacheSnapshot.retainFor),
      );
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_controller_deadline_cache_test_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final store = AtlasLocalCacheStore(
        file: File('${tempDir.path}/atlas-local-cache.json'),
        now: () => _cacheFixtureNow,
      );
      final cachedJobs = [
        _facetJob(
          jobKey: 'job:future',
          title: 'Future open role',
          city: 'Tokyo',
          countryISO3: 'JPN',
          gradeCode: 'P-2',
          standardSeniorityTier: 'T1_ENTRY_SUPPORT',
          closingDate: DateTime.utc(2026, 7, 10, 23, 59),
        ),
        _facetJob(
          jobKey: 'job:unknown',
          title: 'Unknown deadline open role',
          city: 'Geneva',
          countryISO3: 'CHE',
          gradeCode: 'P-3',
          standardSeniorityTier: 'T3_MID_LEVEL',
          hasClosingDate: false,
        ),
        _facetJob(
          jobKey: 'job:expired',
          title: 'Expired open role',
          city: 'Nairobi',
          countryISO3: 'KEN',
          gradeCode: 'P-4',
          standardSeniorityTier: 'T4_SENIOR_PROFESSIONAL',
          closingDate: DateTime.utc(2026, 7, 1, 23, 59),
        ),
        _facetJob(
          jobKey: 'job:closed',
          title: 'Closed future role',
          city: 'Osaka',
          countryISO3: 'JPN',
          gradeCode: 'G-5',
          standardSeniorityTier: 'T2_JUNIOR_PROFESSIONAL',
          closingDate: DateTime.utc(2026, 7, 15, 23, 59),
          status: 'closed',
        ),
      ];
      await store.write(
        AtlasLocalCacheSnapshot(
          schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
          baseURL: Uri.parse('http://atlas.cached:8765'),
          savedAt: _cacheFixtureSavedAt,
          searchRequest: const AtlasSearchRequest(),
          searchResponse: AtlasSearchResponse(
            total: cachedJobs.length,
            limit: cachedJobs.length,
            offset: 0,
            results: cachedJobs,
            facets: const {},
            facetLabels: const {},
            unclassifiedCount: 0,
          ),
          cachedAllJobs: cachedJobs,
        ),
      );
      final controller = AtlasAppController(
        localCacheStore: store,
        now: () => _cacheFixtureNow,
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: _FailingTransport()),
      );
      addTearDown(controller.dispose);

      await controller.loadPersistedCache();

      expect(controller.connectionStatus, 'Offline (cached)');
      expect(controller.filters.openOnly, isTrue);
      expect(controller.total, 2);
      expect(
        controller.results.map((job) => job.jobKey),
        containsAll(['job:future', 'job:unknown']),
      );
      expect(
        controller.results.map((job) => job.jobKey),
        isNot(contains('job:expired')),
      );
      expect(
        controller.results.map((job) => job.jobKey),
        isNot(contains('job:closed')),
      );
      expect(
        controller.facetOptions('cities').map((option) => option.id),
        isNot(contains('Nairobi')),
      );
    },
  );

  test('controller persists and serves cached job details offline', () async {
    final tempDir = await Directory.systemTemp.createTemp(
      'atlas_controller_detail_cache_test_',
    );
    addTearDown(() async {
      if (await tempDir.exists()) {
        await tempDir.delete(recursive: true);
      }
    });
    final store = AtlasLocalCacheStore(
      file: File('${tempDir.path}/atlas-local-cache.json'),
    );
    final transport = _RecordingTransport();
    final controller = AtlasAppController(
      localCacheStore: store,
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
    );
    addTearDown(controller.dispose);

    await controller.saveAndReload(Uri.parse('http://atlas.test:8765'));
    expect(controller.cachedDetailCount, 0);

    final detail = await controller.loadJobDetail('undp_oracle_hcm:34063');

    expect(detail.title, 'Programme Analyst');
    expect(controller.cachedDetailCount, 1);
    expect(transport.detailRequests, ['undp_oracle_hcm:34063']);
    final persisted = await store.read();
    expect(persisted?.cachedJobDetails, hasLength(1));
    expect(
      persisted?.cachedJobDetails['undp_oracle_hcm:34063']?.description,
      contains('Full role description'),
    );

    final offlineController = AtlasAppController(
      localCacheStore: store,
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: _FailingTransport()),
    );
    addTearDown(offlineController.dispose);

    await offlineController.loadPersistedCache();
    expect(offlineController.cachedDetailCount, 1);

    final offlineDetail = await offlineController.loadJobDetail(
      'undp_oracle_hcm:34063',
    );

    expect(offlineDetail.title, 'Programme Analyst');
    expect(offlineDetail.displaySections.first.title, 'Responsibilities');
  });

  test(
    'controller debounces query changes and reports save failures',
    () async {
      final transport = _RecordingTransport();
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
      );
      addTearDown(controller.dispose);

      controller.connectionStatus = 'Connected';
      controller.updateQuery('finance');
      await Future<void>.delayed(const Duration(milliseconds: 450));

      expect(transport.searchTexts.last, 'finance');

      await controller.saveCurrentSearch();
      expect(controller.savedSearches.single.description, contains('finance'));

      final failingController = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: _FailingTransport()),
      );
      addTearDown(failingController.dispose);

      await failingController.saveCurrentSearch();
      expect(
        failingController.connectionMessage,
        startsWith('Save search failed:'),
      );
    },
  );

  test('controller reports save job failures', () async {
    final controller = AtlasAppController(
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: _FailingTransport()),
    );
    addTearDown(controller.dispose);

    await controller.saveJob(JobSearchResult.fromJson(_jobJson));

    expect(controller.connectionMessage, startsWith('Save job failed:'));
  });

  test(
    'controller continues saved search numbering from server state',
    () async {
      final transport = _RecordingTransport()
        ..savedSearchStore.add({
          'name': 'Search 7',
          'description': 'Existing saved search',
          'request': <String, Object?>{},
          'created_at': '2026-07-01T00:00:00Z',
        });
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
      );
      addTearDown(controller.dispose);

      await controller.testConnection(Uri.parse('http://atlas.test:8765'));
      await controller.saveCurrentSearch();

      expect(transport.savedSearchNames, ['Search 8']);
    },
  );

  test('saved searches restore text, filters, and sort', () async {
    final controller = AtlasAppController();
    addTearDown(controller.dispose);

    await controller.runSavedSearch(
      AtlasSavedSearch(
        name: 'Policy',
        request: const AtlasSearchRequest(
          text: 'policy',
          status: ['open'],
          cities: ['Geneva'],
          countriesISO3: ['che'],
          nationalInternational: ['international', 'local'],
          gradeCodes: ['P-4'],
          workModalities: ['home_based', 'online_remote'],
          sourceIDs: ['unicef_pageup'],
          organizations: ['UNICEF'],
          contractGroups: ['fixed_term'],
          seniorityGroups: ['mid'],
          volunteerKinds: ['un_volunteer'],
          unvCategories: ['international_specialist'],
          unvVolunteerTypes: ['online'],
          ccogFamilies: ['Political Affairs'],
          capabilityTags: ['analysis'],
          includeLowConfidence: true,
          closingDateTo: '2026-07-09',
          sort: 'closing_date_desc',
        ),
      ),
    );

    expect(controller.query, 'policy');
    expect(controller.filters.openOnly, isTrue);
    expect(controller.filters.trimmedCity, 'Geneva');
    expect(controller.filters.trimmedCountryISO3, 'che');
    expect(controller.filters.scope, AtlasScopeFilter.international);
    expect(controller.filters.sortedGradeCodes, ['P-4']);
    expect(controller.filters.isRemoteOnly, isTrue);
    expect(controller.filters.includeLowConfidence, isTrue);
    expect(controller.filters.closingSoon, isTrue);
    expect(controller.sortOrder, SortOrder.deadlineLatest);

    await controller.runSavedSearch(
      AtlasSavedSearch(
        name: 'National',
        request: const AtlasSearchRequest(
          nationalInternational: ['national', 'unknown'],
        ),
      ),
    );
    expect(controller.filters.scope, AtlasScopeFilter.national);

    await controller.runSavedSearch(
      AtlasSavedSearch(
        name: 'Unknown',
        request: const AtlasSearchRequest(
          nationalInternational: ['unknown', 'other'],
        ),
      ),
    );
    expect(controller.filters.scope, AtlasScopeFilter.unspecified);
  });

  test('private persistence construction performs no operation', () {
    final privatePersistence = _FakePrivateStatePersistence();
    final controller = AtlasAppController(
      privateStatePersistence: privatePersistence,
    );
    addTearDown(controller.dispose);

    expect(privatePersistence.calls, isEmpty);
    expect(privatePersistence.isActive, isFalse);
  });

  test(
    'in-memory plaintext requires migration before secure operations',
    () async {
      final privatePersistence = _FakePrivateStatePersistence();
      final controller = AtlasAppController(
        privateStatePersistence: privatePersistence,
      );
      addTearDown(controller.dispose);
      controller.savedSearches = <AtlasSavedSearch>[
        AtlasSavedSearch(
          name: 'Legacy private search',
          request: const AtlasSearchRequest(text: 'legacy'),
        ),
      ];

      final result = await controller.activateExistingAtlasVault('vault-alpha');

      expect(result, AtlasVaultActivationResult.migrationRequired);
      expect(privatePersistence.calls, isEmpty);
      expect(controller.savedSearches.single.name, 'Legacy private search');
    },
  );

  test(
    'persisted plaintext requires migration without secure-store I/O',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_private_activation_preflight_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final store = AtlasLocalCacheStore(
        file: File('${tempDir.path}/atlas-local-cache.json'),
        now: () => _cacheFixtureNow,
      );
      await store.write(_privateCacheSnapshot());
      final privatePersistence = _FakePrivateStatePersistence();
      final controller = AtlasAppController(
        localCacheStore: store,
        privateStatePersistence: privatePersistence,
      );
      addTearDown(controller.dispose);

      final result = await controller.activateExistingAtlasVault('vault-alpha');

      expect(result, AtlasVaultActivationResult.migrationRequired);
      expect(privatePersistence.calls, isEmpty);
      expect(await store.read(), isNotNull);
      expect((await store.read())!.containsPrivateState, isTrue);
    },
  );

  test(
    'successful explicit activation publishes only committed snapshot',
    () async {
      final privatePersistence = _FakePrivateStatePersistence(
        activationSnapshot: AtlasVaultPrivateStateSnapshot(
          savedSearches: <AtlasSavedSearch>[
            AtlasSavedSearch(
              name: 'Encrypted search',
              request: const AtlasSearchRequest(text: 'encrypted'),
            ),
          ],
          trackerRecords: <AtlasApplicationRecord>[
            AtlasApplicationRecord(
              id: 'encrypted-record',
              jobKey: 'undp:encrypted',
              status: 'saved',
            ),
          ],
        ),
      );
      final controller = AtlasAppController(
        privateStatePersistence: privatePersistence,
      );
      addTearDown(controller.dispose);

      final result = await controller.activateExistingAtlasVault('vault-alpha');

      expect(result, AtlasVaultActivationResult.activated);
      expect(privatePersistence.calls, <String>['activate', 'read']);
      expect(controller.savedSearches.single.name, 'Encrypted search');
      expect(controller.trackerRecords.single.jobKey, 'undp:encrypted');
    },
  );

  test(
    'active private mutations are non-optimistic and avoid compatibility APIs',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_active_private_cache_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final privatePersistence = _FakePrivateStatePersistence();
      final store = AtlasLocalCacheStore(
        file: File('${tempDir.path}/atlas-local-cache.json'),
        now: () => _cacheFixtureNow,
        privateStateProtectionActive: () => privatePersistence.isActive,
      );
      final transport = _RecordingTransport();
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStore: store,
        privateStatePersistence: privatePersistence,
        now: () => _cacheFixtureNow,
      );
      addTearDown(controller.dispose);
      await controller.saveAndReload(Uri.parse('http://atlas.test:8765'));
      transport.resetPrivateCounts();
      expect(
        await controller.activateExistingAtlasVault('vault-alpha'),
        AtlasVaultActivationResult.activated,
      );

      final enteredSearch = Completer<void>();
      final releaseSearch = Completer<void>();
      privatePersistence.enteredSave = enteredSearch;
      privatePersistence.releaseSave = releaseSearch;
      final saveSearch = controller.saveCurrentSearch();
      await enteredSearch.future;
      expect(controller.savedSearches, isEmpty);
      expect(transport.savedSearchNames, isEmpty);
      releaseSearch.complete();
      await saveSearch;
      expect(controller.savedSearches.single.name, 'Search 1');

      final enteredJob = Completer<void>();
      final releaseJob = Completer<void>();
      privatePersistence.enteredSave = enteredJob;
      privatePersistence.releaseSave = releaseJob;
      final saveJob = controller.saveJob(JobSearchResult.fromJson(_jobJson));
      await enteredJob.future;
      expect(controller.trackerRecords, isEmpty);
      expect(transport.savedJobKeys, isEmpty);
      releaseJob.complete();
      await saveJob;
      expect(controller.trackerRecords.single.jobKey, 'undp_oracle_hcm:34063');

      final persisted = await store.read();
      expect(persisted, isNotNull);
      expect(persisted!.containsPrivateState, isFalse);
      expect(persisted.savedSearches, isEmpty);
      expect(persisted.trackerRecords, isEmpty);
      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
    },
  );

  test(
    'active refresh preserves public endpoints and suppresses private reads',
    () async {
      final transport = _RecordingTransport();
      final privatePersistence = _FakePrivateStatePersistence();
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        privateStatePersistence: privatePersistence,
      );
      addTearDown(controller.dispose);
      await controller.activateExistingAtlasVault('vault-alpha');

      await controller.testConnection(Uri.parse('http://atlas.test:8765'));

      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
      expect(transport.updateReadCount, 1);
      expect(transport.sourceReadCount, 1);
      expect(controller.updateRuns, isNotEmpty);
      expect(controller.sources, isNotEmpty);
    },
  );

  test('explicit deactivation clears private controller state', () async {
    final privatePersistence = _FakePrivateStatePersistence(
      activationSnapshot: AtlasVaultPrivateStateSnapshot(
        savedSearches: <AtlasSavedSearch>[
          AtlasSavedSearch(
            name: 'Encrypted search',
            request: const AtlasSearchRequest(text: 'encrypted'),
          ),
        ],
        trackerRecords: <AtlasApplicationRecord>[
          AtlasApplicationRecord(
            id: 'encrypted-record',
            jobKey: 'undp:encrypted',
            status: 'saved',
          ),
        ],
      ),
    );
    final controller = AtlasAppController(
      privateStatePersistence: privatePersistence,
    );
    addTearDown(controller.dispose);
    await controller.activateExistingAtlasVault('vault-alpha');

    await controller.deactivateAtlasVault();

    expect(privatePersistence.calls.last, 'deactivate');
    expect(privatePersistence.isActive, isFalse);
    expect(controller.savedSearches, isEmpty);
    expect(controller.trackerRecords, isEmpty);
  });

  testWidgets('search submits query and renders refreshed result rows', (
    tester,
  ) async {
    final transport = _RecordingTransport();
    final controller = AtlasAppController(
      initialBaseURL: Uri.parse('http://atlas.test:8765'),
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
    );
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasSearchSkeleton(controller: controller)),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.widgetWithText(TextField, 'Title, keyword, skill, or organization'),
      ' analyst ',
    );
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();

    expect(transport.searchTexts.last, 'analyst');
    expect(find.text('1 searchable result'), findsOneWidget);
    expect(find.textContaining('Local save · updated'), findsOneWidget);
    expect(find.text('Programme Analyst'), findsOneWidget);
    expect(find.textContaining('UNDP'), findsWidgets);
    expect(find.textContaining('Nairobi, Kenya'), findsOneWidget);
    expect(find.textContaining('P-3'), findsOneWidget);
    expect(find.textContaining('Fixed'), findsOneWidget);
    expect(find.textContaining('Onsite'), findsOneWidget);
    expect(find.text('Matched the current search filters.'), findsNothing);
    expect(find.text('Sort: Closing soon'), findsOneWidget);

    await tester.tap(find.text('Remote'));
    await tester.pumpAndSettle();
    expect(controller.filters.isRemoteOnly, isTrue);

    await tester.tap(find.byIcon(AtlasIcons.close).first);
    await tester.pumpAndSettle();
    expect(controller.filters.openOnly, isFalse);

    await tester.tap(find.text('Sort: Closing soon'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Newest posted').last);
    await tester.pumpAndSettle();
    expect(controller.sortOrder, SortOrder.newestPosted);

    await tester.tap(find.text('Programme Analyst'));
    await tester.pumpAndSettle();

    expect(find.text('Full Description'), findsOneWidget);
    expect(find.text('Core Details'), findsOneWidget);
    expect(find.text('Responsibilities'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Apply URL'), 300);
    expect(find.text('Apply URL'), findsOneWidget);
    await tester.tap(find.widgetWithIcon(IconButton, AtlasIcons.copy).first);
    await tester.pump();
    expect(find.text('Apply URL copied'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Match diagnostics'), 300);
    expect(find.text('Match diagnostics'), findsOneWidget);
    expect(find.text('Matched the current search filters.'), findsNothing);

    await tester.tap(find.text('Match diagnostics'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Location matched Nairobi, Kenya'),
      findsOneWidget,
    );
    expect(find.text('Reason'), findsOneWidget);
    expect(find.text('Term in title'), findsOneWidget);
    expect(find.text('Classification'), findsOneWidget);
    expect(find.text('Classifier evidence'), findsOneWidget);

    await tester.tap(find.byTooltip('Save job'));
    await tester.pumpAndSettle();
    expect(transport.savedJobKeys, ['undp_oracle_hcm:34063']);
    expect(controller.isJobSaved('undp_oracle_hcm:34063'), isTrue);
  });
}

final class _RecordingTransport implements AtlasTransport {
  final searchTexts = <String?>[];
  final searchSorts = <String?>[];
  final searchBodies = <Map<String, Object?>>[];
  final savedSearchNames = <String>[];
  final savedJobKeys = <String>[];
  final detailRequests = <String>[];
  final savedSearchStore = <Map<String, Object?>>[];
  int savedSearchReadCount = 0;
  int trackerReadCount = 0;
  int updateReadCount = 0;
  int sourceReadCount = 0;

  void resetPrivateCounts() {
    savedSearchNames.clear();
    savedJobKeys.clear();
    savedSearchReadCount = 0;
    trackerReadCount = 0;
  }

  @override
  Future<Object?> send(AtlasRequest request) async {
    switch (request.path) {
      case 'api/health':
        expect(request.method, 'GET');
        return {
          'status': 'ok',
          'open_jobs': 128,
          'enabled_sources': 12,
          'schema_version': '2026-07',
        };
      case 'api/search':
        expect(request.method, 'POST');
        searchBodies.add(request.jsonBody ?? const <String, Object?>{});
        searchTexts.add(request.jsonBody?['text'] as String?);
        searchSorts.add(request.jsonBody?['sort'] as String?);
        return {
          'total': 1,
          'limit': request.jsonBody?['limit'] ?? 50,
          'offset': 0,
          'facets': <String, Object?>{},
          'facet_labels': <String, Object?>{},
          'unclassified_count': 0,
          'results': [_jobJson],
        };
      case 'api/saved-searches':
        if (request.method == 'POST') {
          final name = request.jsonBody?['name'] as String? ?? '';
          savedSearchNames.add(name);
          final savedSearch = {
            'name': name,
            'description': request.jsonBody?['summary'],
            'request': request.jsonBody?['request'],
            'created_at': '2026-07-02T00:00:00Z',
          };
          savedSearchStore.removeWhere((search) => search['name'] == name);
          savedSearchStore.insert(0, savedSearch);
          return savedSearch;
        }
        expect(request.method, 'GET');
        savedSearchReadCount += 1;
        return savedSearchStore;
      case 'api/job-detail':
        detailRequests.add(request.queryParameters['job_key'] ?? '');
        return {
          'job_key': request.queryParameters['job_key'],
          'title': 'Programme Analyst',
          'description':
              'Full role description with responsibilities and qualifications.',
          'status': 'open',
          'apply_url': 'https://example.org/apply',
          'source_url': 'https://example.org/source',
          'display_sections': [
            {
              'title': 'Responsibilities',
              'body': 'Lead programme analysis and partner coordination.',
              'rows': [
                {'label': 'Duty', 'value': 'Coordinate delivery.'},
              ],
            },
            {'title': 'Classification', 'body': 'Classifier evidence'},
            {
              'title': 'Job Record',
              'rows': [
                {'label': 'Detail Quality Status', 'value': 'complete'},
              ],
            },
          ],
        };
      case 'api/tracker/jobs/undp_oracle_hcm%3A34063':
        savedJobKeys.add('undp_oracle_hcm:34063');
        return {
          'id': 'undp_oracle_hcm-34063',
          'job_key': 'undp_oracle_hcm:34063',
          'status': 'saved',
          'updated_at': '2026-07-02T00:00:00Z',
        };
      case 'api/tracker':
        trackerReadCount += 1;
        return <Object?>[];
      case 'api/updates':
        updateReadCount += 1;
        return {
          'recent_source_runs': [
            {
              'source_id': 'undp_oracle_hcm',
              'fetched': 7,
              'inserted': 1,
              'updated': 2,
              'missing': 0,
              'closed': 0,
              'observed_at': '2026-07-02T00:00:00Z',
            },
          ],
        };
      case 'api/sources':
        sourceReadCount += 1;
        return {
          'sources': [
            {
              'source_id': 'undp_oracle_hcm',
              'organization': 'UNDP Oracle HCM',
              'total_jobs': 4,
              'open_jobs': 1,
              'last_seen_at': '2026-07-02T00:00:00Z',
              'health_status': 'ok',
              'detail_attempted': 1,
              'detail_failed': 0,
              'missing_transition_allowed': true,
            },
          ],
        };
      default:
        fail('Unexpected request ${request.method} ${request.path}');
    }
  }
}

final class _FakePrivateStatePersistence
    implements AtlasVaultPrivateStatePersistence {
  _FakePrivateStatePersistence({
    AtlasVaultPrivateStateSnapshot? activationSnapshot,
    this.activationResult = AtlasVaultActivationResult.activated,
  }) : _snapshot =
           activationSnapshot ??
           AtlasVaultPrivateStateSnapshot(
             savedSearches: const <AtlasSavedSearch>[],
             trackerRecords: const <AtlasApplicationRecord>[],
           );

  final AtlasVaultActivationResult activationResult;
  final List<String> calls = <String>[];
  AtlasVaultPrivateStateSnapshot _snapshot;
  Completer<void>? enteredSave;
  Completer<void>? releaseSave;

  @override
  bool isActive = false;

  @override
  Future<AtlasVaultActivationResult> activateExisting(String vaultId) async {
    calls.add('activate');
    if (activationResult == AtlasVaultActivationResult.activated) {
      isActive = true;
    }
    return activationResult;
  }

  @override
  Future<void> deactivate() async {
    calls.add('deactivate');
    isActive = false;
    _snapshot = AtlasVaultPrivateStateSnapshot(
      savedSearches: const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
    );
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> read() async {
    calls.add('read');
    return _snapshot;
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> saveSearch(
    AtlasSavedSearch value,
  ) async {
    calls.add('saveSearch');
    enteredSave?.complete();
    if (releaseSave != null) {
      await releaseSave!.future;
    }
    final remaining = _snapshot.savedSearches
        .where((existing) => existing.name != value.name)
        .toList();
    _snapshot = AtlasVaultPrivateStateSnapshot(
      savedSearches: <AtlasSavedSearch>[value, ...remaining],
      trackerRecords: _snapshot.trackerRecords,
    );
    return _snapshot;
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> saveTrackerRecord(
    AtlasApplicationRecord value,
  ) async {
    calls.add('saveTrackerRecord');
    enteredSave?.complete();
    if (releaseSave != null) {
      await releaseSave!.future;
    }
    final remaining = _snapshot.trackerRecords
        .where((existing) => existing.jobKey != value.jobKey)
        .toList();
    _snapshot = AtlasVaultPrivateStateSnapshot(
      savedSearches: _snapshot.savedSearches,
      trackerRecords: <AtlasApplicationRecord>[value, ...remaining],
    );
    return _snapshot;
  }
}

AtlasLocalCacheSnapshot _privateCacheSnapshot() {
  return AtlasLocalCacheSnapshot(
    schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
    baseURL: Uri.parse('http://atlas.test:8765'),
    savedAt: _cacheFixtureSavedAt,
    searchRequest: const AtlasSearchRequest(),
    searchResponse: AtlasSearchResponse(
      total: 0,
      limit: 0,
      offset: 0,
      results: const <JobSearchResult>[],
      facets: const <String, Map<String, int>>{},
      facetLabels: const <String, Map<String, String>>{},
      unclassifiedCount: 0,
    ),
    savedSearches: <AtlasSavedSearch>[
      AtlasSavedSearch(
        name: 'Legacy private search',
        request: const AtlasSearchRequest(text: 'legacy'),
      ),
    ],
    trackerRecords: <AtlasApplicationRecord>[
      AtlasApplicationRecord(
        id: 'legacy-record',
        jobKey: 'undp:legacy',
        status: 'saved',
      ),
    ],
  );
}

final class _FailingTransport implements AtlasTransport {
  @override
  Future<Object?> send(AtlasRequest request) async {
    throw const AtlasAPIException.http(503, 'job-api unavailable');
  }
}

JobSearchResult _facetJob({
  required String jobKey,
  required String title,
  required String city,
  required String countryISO3,
  required String gradeCode,
  required String standardSeniorityTier,
  DateTime? closingDate,
  bool hasClosingDate = true,
  String status = 'open',
}) {
  return JobSearchResult(
    jobKey: jobKey,
    title: title,
    organization: 'UNDP Oracle HCM',
    sourceID: 'undp_oracle_hcm',
    dutyStation: '$city, $countryISO3',
    city: city,
    countryISO3: countryISO3,
    gradeCode: gradeCode,
    standardSeniorityTier: standardSeniorityTier,
    contractGroup: 'staff',
    contractLabel: 'Staff',
    workModality: 'Onsite',
    closingDate: hasClosingDate
        ? closingDate ?? DateTime.utc(2026, 8, 30, 23, 59)
        : null,
    needsReview: false,
    scoreReasons: const [],
    matchSummary: 'Cached facet row',
    description: 'Cached description',
    status: status,
  );
}

const _jobJson = {
  'job_key': 'undp_oracle_hcm:34063',
  'title': 'Programme Analyst',
  'organization': 'UNDP Oracle HCM',
  'source_id': 'undp_oracle_hcm',
  'duty_station': 'Nairobi, Kenya',
  'standard_seniority_tier': 'T2_JUNIOR_PROFESSIONAL',
  'grade_code': 'P-3',
  'contract_group': 'Fixed term',
  'work_modality': 'Onsite',
  'closing_date': '2026-07-30T23:59:00Z',
  'needs_review': false,
  'score_reasons': ['Term in title'],
  'match_summary': 'Matched current filters',
  'description': 'Role summary',
  'status': 'open',
  'match_evidence': {
    'location': {
      'matched_city': 'Nairobi',
      'matched_country_iso3': 'KEN',
      'source_field': 'fixture',
      'confidence': 0.9,
    },
  },
};
