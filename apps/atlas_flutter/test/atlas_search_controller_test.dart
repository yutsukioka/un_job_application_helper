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
  test(
    'conditional saved-search deletion sends the exact reviewed snapshot',
    () async {
      final transport = _ConditionalDeleteTransport(
        response: const <String, Object?>{'outcome': 'deleted'},
      );
      final client = AtlasAPIClient(
        baseURL: Uri.parse('http://atlas.test:8765'),
        transport: transport,
      );
      final expected = AtlasSavedSearch(
        name: 'UN roles / reviewed',
        description: 'reviewed private description',
        request: const AtlasSearchRequest(text: 'reviewed-private-query'),
        createdAt: '2026-08-01T00:00:00Z',
        updatedAt: '2026-08-02T00:00:00Z',
      );

      final outcome = await client.conditionalDeleteSavedSearch(expected);

      expect(outcome, AtlasConditionalDeleteOutcome.deleted);
      expect(transport.requests, hasLength(1));
      final request = transport.requests.single;
      expect(request.method, 'POST');
      expect(
        request.path,
        'api/saved-searches/'
        '~sha256-14a89575e595c292e36023156d316eb2e09ae65b1d4d20cf679ca4b90c12f1d9'
        '/conditional-delete',
      );
      expect(request.jsonBody, <String, Object?>{
        'expected': expected.toCompatibilityStoredSnapshotJson(),
      });
      expect(request.path, isNot(contains(expected.name)));
    },
  );

  test(
    'conditional saved-search deletion preserves the complete stored request',
    () async {
      final storedRequest = _compatibilityMigrationRequest(
        text: 'reviewed-private-query',
      );
      final storedSnapshot = <String, Object?>{
        'name': 'UN roles / reviewed',
        'description': 'reviewed private description',
        'request': storedRequest,
        'created_at': '2026-08-01T00:00:00.123456+00:00',
        'updated_at': '2026-08-02T12:34:56.654321Z',
      };
      final transport = _StoredSavedSearchConditionalDeleteTransport(
        storedSnapshot,
      );
      final client = AtlasAPIClient(
        baseURL: Uri.parse('http://atlas.test:8765'),
        transport: transport,
      );

      final expected =
          (await client.savedSearchesForPlaintextMigration()).single;
      final outcome = await client.conditionalDeleteSavedSearch(expected);

      expect(outcome, AtlasConditionalDeleteOutcome.deleted);
      expect(expected.createdAt, storedSnapshot['created_at']);
      expect(expected.updatedAt, storedSnapshot['updated_at']);
      final request = transport.requests.last;
      expect(request.method, 'POST');
      expect(request.jsonBody, <String, Object?>{'expected': storedSnapshot});
      final sentRequest =
          (request.jsonBody!['expected'] as Map<String, Object?>)['request']
              as Map<String, Object?>;
      expect(sentRequest.keys.toSet(), storedRequest.keys.toSet());
      expect(sentRequest, isNot(contains('include_facets')));
    },
  );

  test('conditional tracker deletion sends the exact reviewed record', () async {
    final transport = _ConditionalDeleteTransport(
      response: const <String, Object?>{'outcome': 'absent'},
    );
    final client = AtlasAPIClient(
      baseURL: Uri.parse('http://atlas.test:8765'),
      transport: transport,
    );
    final expected = AtlasApplicationRecord(
      id: 'tracker/private/reviewed',
      jobKey: 'unicef:reviewed-private-job',
      status: 'applied',
      notes: 'reviewed private notes',
      appliedAt: '2026-08-01T00:00:00Z',
      updatedAt: '2026-08-02T00:00:00Z',
    );

    final outcome = await client.conditionalDeleteTrackerRecord(expected);

    expect(outcome, AtlasConditionalDeleteOutcome.absent);
    final request = transport.requests.single;
    expect(request.method, 'POST');
    expect(
      request.path,
      'api/tracker/'
      '~sha256-d680b1848ffccd6687f2310d2ba3c7da227ce8d54b0249d17352050898f7ddd4'
      '/conditional-delete',
    );
    expect(request.jsonBody, <String, Object?>{'expected': expected.toJson()});
    expect(request.path, isNot(contains(expected.id)));
  });

  test(
    'conditional tracker deletion preserves exact reviewed timestamps',
    () async {
      final storedSnapshot = <String, Object?>{
        'id': 'tracker/private/reviewed',
        'job_key': 'unicef:reviewed-private-job',
        'status': 'applied',
        'notes': 'reviewed private notes',
        'applied_at': '2026-08-01T00:00:00.123456+00:00',
        'updated_at': '2026-08-02T12:34:56.654321Z',
      };
      final transport = _StoredTrackerConditionalDeleteTransport(
        storedSnapshot,
      );
      final client = AtlasAPIClient(
        baseURL: Uri.parse('http://atlas.test:8765'),
        transport: transport,
      );

      final expected =
          (await client.trackerRecordsForPlaintextMigration()).single;
      final outcome = await client.conditionalDeleteTrackerRecord(expected);

      expect(outcome, AtlasConditionalDeleteOutcome.deleted);
      expect(expected.appliedAt, storedSnapshot['applied_at']);
      expect(expected.updatedAt, storedSnapshot['updated_at']);
      expect(transport.requests.last.jsonBody, <String, Object?>{
        'expected': storedSnapshot,
      });
    },
  );

  test(
    'conditional deletion maps HTTP 412 without exposing private content',
    () async {
      const privateSentinel = 'PRIVATE_CHANGED_SERVER_CONTENT';
      final transport = _ConditionalDeleteTransport(
        error: const AtlasAPIException.http(412, privateSentinel),
      );
      final client = AtlasAPIClient(
        baseURL: Uri.parse('http://atlas.test:8765'),
        transport: transport,
      );
      final expected = AtlasSavedSearch(
        name: 'Reviewed search',
        request: const AtlasSearchRequest(text: 'PRIVATE_REVIEWED_QUERY'),
        createdAt: '2026-08-01T00:00:00Z',
        updatedAt: '2026-08-02T00:00:00Z',
      );

      final outcome = await client.conditionalDeleteSavedSearch(expected);

      expect(outcome, AtlasConditionalDeleteOutcome.preconditionFailed);
      expect(outcome.toString(), isNot(contains(privateSentinel)));
    },
  );

  test(
    'Windows admission fences an already-running legacy controller and reopens',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_controller_authority_admission_test_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final cacheFile = File('${tempDir.path}/atlas-local-cache.json');
      final transport = _RecordingTransport();
      final admission = _ControllerPlaintextAuthorityAdmission();
      final controller = AtlasAppController(
        localCacheStore: AtlasLocalCacheStore(file: cacheFile),
        plaintextAuthorityAdmission: admission,
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
      );
      addTearDown(controller.dispose);

      await controller.saveAndReload(Uri.parse('http://atlas.test:8765'));
      await controller.saveCurrentSearch();
      final cacheBeforeFence = await cacheFile.readAsBytes();
      transport.resetPrivateCounts();
      admission.blocked = true;

      await controller.saveCurrentSearch();
      await controller.saveJob(JobSearchResult.fromAPIJson(_jobJson));
      await controller.refreshLocalSave();

      expect(transport.savedSearchNames, isEmpty);
      expect(transport.savedJobKeys, isEmpty);
      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
      expect(await cacheFile.readAsBytes(), cacheBeforeFence);
      expect(controller.results.single.title, 'Programme Analyst');
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
      expect(
        controller.connectionMessage,
        contains('AtlasVault migration is pending'),
      );

      admission.blocked = false;
      await controller.saveCurrentSearch();

      expect(transport.savedSearchNames, <String>['Search 3']);
      expect(controller.savedSearches.single.name, 'Search 3');
      expect(admission.rejectedCalls, greaterThanOrEqualTo(3));
    },
  );

  test(
    'Windows admission rejects cache resolution before legacy import',
    () async {
      final admission = _ControllerPlaintextAuthorityAdmission()
        ..blocked = true;
      var cacheFactoryCalls = 0;
      final controller = AtlasAppController(
        plaintextAuthorityAdmission: admission,
        localCacheStoreFactory: ({privateStateProtectionActive}) async {
          cacheFactoryCalls += 1;
          return null;
        },
      );
      addTearDown(controller.dispose);

      await controller.loadPersistedCache();

      expect(cacheFactoryCalls, 0);
      expect(admission.rejectedCalls, 1);
    },
  );

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

  test(
    'candidate server private state cannot mutate configured authority',
    () async {
      final configuredTransport = _RecordingTransport();
      final candidateTransport = _RecordingTransport()
        ..savedSearchStore.add(<String, Object?>{
          'name': 'Candidate private search',
          'description': 'candidate private description',
          'request': const AtlasSearchRequest(
            text: 'candidate-private',
          ).toJson(),
          'created_at': '2026-07-01T00:00:00Z',
        })
        ..trackerStore.add(<String, Object?>{
          'id': 'candidate-private-record',
          'job_key': 'candidate:private-job',
          'status': 'saved',
          'updated_at': '2026-07-02T00:00:00Z',
        });
      final configuredAuthority = Uri.parse(
        'http://configured-atlas.test:8765',
      );
      final candidateAuthority = Uri.parse('http://candidate-atlas.test:8765');
      final controller = AtlasAppController(
        initialBaseURL: configuredAuthority,
        clientFactory: (baseURL) => AtlasAPIClient(
          baseURL: baseURL,
          transport: baseURL == candidateAuthority
              ? candidateTransport
              : configuredTransport,
        ),
      );
      addTearDown(controller.dispose);

      await controller.testConnection(candidateAuthority);

      expect(controller.baseURL, configuredAuthority);
      expect(controller.savedSearches.single.name, 'Candidate private search');
      expect(controller.trackerRecords.single.id, 'candidate-private-record');

      await controller.saveCurrentSearch();
      await controller.saveJob(JobSearchResult.fromAPIJson(_jobJson));

      expect(configuredTransport.savedSearchNames, isEmpty);
      expect(configuredTransport.savedJobKeys, isEmpty);
      expect(
        controller.connectionMessage,
        'Save job failed: AtlasVault private-state operation failed.',
      );
      expect(controller.savedSearches.single.name, 'Candidate private search');
      expect(controller.trackerRecords.single.id, 'candidate-private-record');
    },
  );

  test(
    'candidate private family blocks cross-family configured mutation',
    () async {
      Future<void> expectMutationBlocked({
        required _RecordingTransport candidateTransport,
        required Future<void> Function(AtlasAppController controller) mutate,
      }) async {
        final configuredTransport = _RecordingTransport();
        final configuredAuthority = Uri.parse(
          'http://configured-atlas.test:8765',
        );
        final candidateAuthority = Uri.parse(
          'http://candidate-atlas.test:8765',
        );
        final controller = AtlasAppController(
          initialBaseURL: configuredAuthority,
          clientFactory: (baseURL) => AtlasAPIClient(
            baseURL: baseURL,
            transport: baseURL == candidateAuthority
                ? candidateTransport
                : configuredTransport,
          ),
        );
        addTearDown(controller.dispose);

        await controller.testConnection(candidateAuthority);
        await mutate(controller);

        expect(configuredTransport.savedSearchNames, isEmpty);
        expect(configuredTransport.savedJobKeys, isEmpty);
      }

      final savedSearchOnlyTransport = _RecordingTransport()
        ..savedSearchStore.add(<String, Object?>{
          'name': 'Candidate private search',
          'description': 'candidate private description',
          'request': const AtlasSearchRequest(
            text: 'candidate-private',
          ).toJson(),
          'created_at': '2026-07-01T00:00:00Z',
        });
      await expectMutationBlocked(
        candidateTransport: savedSearchOnlyTransport,
        mutate: (controller) =>
            controller.saveJob(JobSearchResult.fromAPIJson(_jobJson)),
      );

      final trackerOnlyTransport = _RecordingTransport()
        ..trackerStore.add(<String, Object?>{
          'id': 'candidate-private-record',
          'job_key': 'candidate:private-job',
          'status': 'saved',
          'updated_at': '2026-07-02T00:00:00Z',
        });
      await expectMutationBlocked(
        candidateTransport: trackerOnlyTransport,
        mutate: (controller) => controller.saveCurrentSearch(),
      );
    },
  );

  test('saved authority supersedes stale candidate private reads', () async {
    final candidateSavedSearchEntered = Completer<void>();
    final releaseCandidateSavedSearch = Completer<void>();
    addTearDown(() {
      if (!releaseCandidateSavedSearch.isCompleted) {
        releaseCandidateSavedSearch.complete();
      }
    });
    final candidateTransport = _RecordingTransport()
      ..savedSearchStore.add(<String, Object?>{
        'name': 'Stale candidate search',
        'description': 'stale candidate private description',
        'request': const AtlasSearchRequest(
          text: 'stale-candidate-private',
        ).toJson(),
        'created_at': '2026-07-01T00:00:00Z',
      })
      ..trackerStore.add(<String, Object?>{
        'id': 'stale-candidate-record',
        'job_key': 'candidate:stale-private-job',
        'status': 'saved',
        'updated_at': '2026-07-02T00:00:00Z',
      })
      ..enteredCompatibilitySavedSearchRead = candidateSavedSearchEntered
      ..releaseCompatibilitySavedSearchRead = releaseCandidateSavedSearch;
    final savedTransport = _RecordingTransport()
      ..savedSearchStore.add(<String, Object?>{
        'name': 'Saved authority search',
        'description': 'saved authority private description',
        'request': const AtlasSearchRequest(
          text: 'saved-authority-private',
        ).toJson(),
        'created_at': '2026-07-03T00:00:00Z',
      })
      ..trackerStore.add(<String, Object?>{
        'id': 'saved-authority-record',
        'job_key': 'saved:private-job',
        'status': 'saved',
        'updated_at': '2026-07-04T00:00:00Z',
      });
    final candidateAuthority = Uri.parse('http://candidate-atlas.test:8765');
    final savedAuthority = Uri.parse('http://saved-atlas.test:8765');
    final controller = AtlasAppController(
      initialBaseURL: Uri.parse('http://configured-atlas.test:8765'),
      clientFactory: (baseURL) => AtlasAPIClient(
        baseURL: baseURL,
        transport: baseURL == candidateAuthority
            ? candidateTransport
            : savedTransport,
      ),
    );
    addTearDown(controller.dispose);

    final staleTest = controller.testConnection(candidateAuthority);
    await candidateSavedSearchEntered.future;
    await controller.saveAndReload(savedAuthority);

    expect(controller.baseURL, savedAuthority);
    expect(controller.savedSearches.single.name, 'Saved authority search');
    expect(controller.trackerRecords.single.id, 'saved-authority-record');

    releaseCandidateSavedSearch.complete();
    await staleTest;

    expect(controller.baseURL, savedAuthority);
    expect(controller.savedSearches.single.name, 'Saved authority search');
    expect(controller.trackerRecords.single.id, 'saved-authority-record');
    expect(
      controller.connectionMessage,
      'Saved http://saved-atlas.test:8765 and refreshed 1 job.',
    );
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

  testWidgets('query debounce resumes after private activation completes', (
    tester,
  ) async {
    final transport = _RecordingTransport();
    final enteredActivation = Completer<void>();
    final releaseActivation = Completer<void>();
    final privatePersistence = _FakePrivateStatePersistence()
      ..enteredActivation = enteredActivation
      ..releaseActivation = releaseActivation;
    _TestTimer? scheduledSearch;
    addTearDown(() {
      if (!releaseActivation.isCompleted) {
        releaseActivation.complete();
      }
    });
    final controller = AtlasAppController(
      initialBaseURL: Uri.parse('http://atlas.test:8765'),
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
      privateStatePersistence: privatePersistence,
      searchDebounceTimerFactory: (duration, callback) {
        expect(duration, const Duration(milliseconds: 350));
        final timer = _TestTimer(callback);
        scheduledSearch = timer;
        return timer;
      },
    );
    addTearDown(controller.dispose);
    controller.connectionStatus = 'Connected';

    final activation = controller.activateExistingAtlasVault('vault-alpha');
    await enteredActivation.future;
    controller.updateQuery('blocked during activation');
    await tester.pump();
    expect(scheduledSearch, isNull);
    expect(transport.searchTexts, isEmpty);

    releaseActivation.complete();
    expect(await activation, AtlasVaultActivationResult.activated);
    controller.updateQuery('active vault query');
    expect(scheduledSearch, isNotNull);
    final enteredActiveSearch = Completer<void>();
    transport
      ..expectedSearchText = 'active vault query'
      ..enteredExpectedSearch = enteredActiveSearch;
    scheduledSearch!.fire();
    await enteredActiveSearch.future;

    expect(transport.searchTexts.last, 'active vault query');
  });

  testWidgets('pending debounce does not run during private transitions', (
    tester,
  ) async {
    final transport = _RecordingTransport();
    final enteredActivation = Completer<void>();
    final releaseActivation = Completer<void>();
    final enteredDeactivation = Completer<void>();
    final releaseDeactivation = Completer<void>();
    final privatePersistence = _FakePrivateStatePersistence()
      ..enteredActivation = enteredActivation
      ..releaseActivation = releaseActivation
      ..enteredDeactivation = enteredDeactivation
      ..releaseDeactivation = releaseDeactivation;
    _TestTimer? scheduledSearch;
    addTearDown(() {
      if (!releaseActivation.isCompleted) {
        releaseActivation.complete();
      }
      if (!releaseDeactivation.isCompleted) {
        releaseDeactivation.complete();
      }
    });
    final controller = AtlasAppController(
      initialBaseURL: Uri.parse('http://atlas.test:8765'),
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
      privateStatePersistence: privatePersistence,
      searchDebounceTimerFactory: (duration, callback) {
        expect(duration, const Duration(milliseconds: 350));
        final timer = _TestTimer(callback);
        scheduledSearch = timer;
        return timer;
      },
    );
    addTearDown(controller.dispose);
    controller.connectionStatus = 'Connected';

    controller.updateQuery('queued before activation');
    final preActivationTimer = scheduledSearch!;
    final activation = controller.activateExistingAtlasVault('vault-alpha');
    await enteredActivation.future;
    preActivationTimer.fire();
    expect(transport.healthReadCount, 0);

    releaseActivation.complete();
    expect(await activation, AtlasVaultActivationResult.activated);
    scheduledSearch = null;
    controller.updateQuery('queued before deactivation');
    final preDeactivationTimer = scheduledSearch!;
    final deactivation = controller.deactivateAtlasVault();
    await enteredDeactivation.future;
    preDeactivationTimer.fire();
    expect(transport.healthReadCount, 0);

    releaseDeactivation.complete();
    await deactivation;
    await tester.pump();
  });

  test(
    'query entered during activation is scheduled after it stabilizes',
    () async {
      final transport = _RecordingTransport();
      final enteredActivation = Completer<void>();
      final releaseActivation = Completer<void>();
      final enteredSearch = Completer<void>();
      final privatePersistence = _FakePrivateStatePersistence()
        ..enteredActivation = enteredActivation
        ..releaseActivation = releaseActivation;
      _TestTimer? scheduledSearch;
      var scheduleCount = 0;
      addTearDown(() {
        if (!releaseActivation.isCompleted) {
          releaseActivation.complete();
        }
      });
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        privateStatePersistence: privatePersistence,
        searchDebounceTimerFactory: (duration, callback) {
          expect(duration, const Duration(milliseconds: 350));
          scheduleCount += 1;
          final timer = _TestTimer(callback);
          scheduledSearch = timer;
          return timer;
        },
      );
      addTearDown(controller.dispose);
      controller.connectionStatus = 'Connected';
      final refreshCompleted = Completer<void>();
      void observeRefreshCompletion() {
        if (enteredSearch.isCompleted &&
            !controller.isRefreshingLocalSave &&
            !refreshCompleted.isCompleted) {
          refreshCompleted.complete();
        }
      }

      controller.addListener(observeRefreshCompletion);
      addTearDown(() => controller.removeListener(observeRefreshCompletion));

      final activation = controller.activateExistingAtlasVault('vault-alpha');
      await enteredActivation.future;
      controller.updateQuery('query entered during activation');
      expect(scheduledSearch, isNull);
      expect(scheduleCount, 0);

      transport
        ..expectedSearchText = 'query entered during activation'
        ..enteredExpectedSearch = enteredSearch;
      releaseActivation.complete();
      expect(await activation, AtlasVaultActivationResult.activated);
      expect(scheduledSearch, isNotNull);
      expect(scheduleCount, 1);

      scheduledSearch!.fire();
      await enteredSearch.future;
      observeRefreshCompletion();
      await refreshCompleted.future;
    },
  );

  test(
    'query entered during deactivation is scheduled after it stabilizes',
    () async {
      final transport = _RecordingTransport();
      final enteredDeactivation = Completer<void>();
      final releaseDeactivation = Completer<void>();
      final enteredSearch = Completer<void>();
      final privatePersistence = _FakePrivateStatePersistence();
      _TestTimer? scheduledSearch;
      var scheduleCount = 0;
      addTearDown(() {
        if (!releaseDeactivation.isCompleted) {
          releaseDeactivation.complete();
        }
      });
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        privateStatePersistence: privatePersistence,
        searchDebounceTimerFactory: (duration, callback) {
          expect(duration, const Duration(milliseconds: 350));
          scheduleCount += 1;
          final timer = _TestTimer(callback);
          scheduledSearch = timer;
          return timer;
        },
      );
      addTearDown(controller.dispose);
      controller.connectionStatus = 'Connected';
      final refreshCompleted = Completer<void>();
      void observeRefreshCompletion() {
        if (enteredSearch.isCompleted &&
            !controller.isRefreshingLocalSave &&
            !refreshCompleted.isCompleted) {
          refreshCompleted.complete();
        }
      }

      controller.addListener(observeRefreshCompletion);
      addTearDown(() => controller.removeListener(observeRefreshCompletion));
      expect(
        await controller.activateExistingAtlasVault('vault-alpha'),
        AtlasVaultActivationResult.activated,
      );
      privatePersistence
        ..enteredDeactivation = enteredDeactivation
        ..releaseDeactivation = releaseDeactivation;
      scheduledSearch = null;

      final deactivation = controller.deactivateAtlasVault();
      await enteredDeactivation.future;
      controller.updateQuery('query entered during deactivation');
      expect(scheduledSearch, isNull);
      expect(scheduleCount, 0);

      transport
        ..expectedSearchText = 'query entered during deactivation'
        ..enteredExpectedSearch = enteredSearch;
      releaseDeactivation.complete();
      await deactivation;
      expect(scheduledSearch, isNotNull);
      expect(scheduleCount, 1);

      scheduledSearch!.fire();
      await enteredSearch.future;
      observeRefreshCompletion();
      await refreshCompleted.future;
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
      controller.updateQuery('PRIVATE_CACHE_SENTINEL');

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
      expect(
        await store.file.readAsString(),
        isNot(contains('PRIVATE_CACHE_SENTINEL')),
      );
      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
    },
  );

  test(
    'active saved-search refresh preserves the last public cache request',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_active_search_request_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      const publicQuery = 'PUBLIC_MANUAL_QUERY';
      const privateQuery = 'ENCRYPTED_PRIVATE_QUERY';
      final privateSearch = AtlasSavedSearch(
        name: 'Encrypted private search',
        request: const AtlasSearchRequest(
          text: privateQuery,
          organizations: <String>['PRIVATE_ORGANIZATION'],
        ),
      );
      final privatePersistence = _FakePrivateStatePersistence(
        activationSnapshot: AtlasVaultPrivateStateSnapshot(
          savedSearches: <AtlasSavedSearch>[privateSearch],
          trackerRecords: const <AtlasApplicationRecord>[],
        ),
      );
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
      controller.updateQuery(publicQuery);
      await controller.saveAndReload(Uri.parse('http://atlas.test:8765'));
      expect((await store.read())?.searchRequest.text, publicQuery);
      expect(
        await controller.activateExistingAtlasVault('vault-alpha'),
        AtlasVaultActivationResult.activated,
      );

      await controller.runSavedSearch(privateSearch);

      final rawCache = await store.file.readAsString();
      expect(rawCache, isNot(contains(privateQuery)));
      expect(rawCache, isNot(contains('PRIVATE_ORGANIZATION')));
      expect((await store.read())?.searchRequest.text, publicQuery);
    },
  );

  test(
    'private search crossing deactivation cannot publish cache criteria',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_deactivation_search_fence_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      const privateQuery = 'DEACTIVATING_PRIVATE_QUERY';
      const privateOrganization = 'DEACTIVATING_PRIVATE_ORGANIZATION';
      final privateSearch = AtlasSavedSearch(
        name: 'Encrypted transition search',
        request: const AtlasSearchRequest(
          text: privateQuery,
          organizations: <String>[privateOrganization],
        ),
      );
      final privatePersistence = _FakePrivateStatePersistence(
        activationSnapshot: AtlasVaultPrivateStateSnapshot(
          savedSearches: <AtlasSavedSearch>[privateSearch],
          trackerRecords: const <AtlasApplicationRecord>[],
        ),
      );
      final store = AtlasLocalCacheStore(
        file: File('${tempDir.path}/atlas-local-cache.json'),
        now: () => _cacheFixtureNow,
        privateStateProtectionActive: () => privatePersistence.isActive,
      );
      final enteredSearch = Completer<void>();
      final releaseSearch = Completer<void>();
      addTearDown(() {
        if (!releaseSearch.isCompleted) {
          releaseSearch.complete();
        }
      });
      final transport = _RecordingTransport()
        ..expectedSearchText = privateQuery
        ..enteredExpectedSearch = enteredSearch
        ..releaseExpectedSearch = releaseSearch;
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStore: store,
        privateStatePersistence: privatePersistence,
        now: () => _cacheFixtureNow,
      );
      addTearDown(controller.dispose);
      controller.connectionStatus = 'Connected';
      expect(
        await controller.activateExistingAtlasVault('vault-alpha'),
        AtlasVaultActivationResult.activated,
      );

      final refresh = controller.runSavedSearch(privateSearch);
      await enteredSearch.future;
      await controller.deactivateAtlasVault();
      releaseSearch.complete();
      await refresh;

      final rawCache = await store.file.readAsString();
      expect(rawCache, isNot(contains(privateQuery)));
      expect(rawCache, isNot(contains(privateOrganization)));
      final persisted = await store.read();
      expect(persisted?.searchRequest.text, isNull);
      expect(persisted?.searchRequest.organizations, isEmpty);
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

  test(
    'activation rejects an in-flight compatibility saved-search result',
    () async {
      final entered = Completer<void>();
      final release = Completer<void>();
      final transport = _RecordingTransport()
        ..enteredCompatibilitySaveSearch = entered
        ..releaseCompatibilitySaveSearch = release;
      final enteredActivation = Completer<void>();
      final privatePersistence = _FakePrivateStatePersistence()
        ..enteredActivation = enteredActivation;
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        privateStatePersistence: privatePersistence,
      );
      addTearDown(controller.dispose);

      final compatibilitySave = controller.saveCurrentSearch();
      await entered.future;
      final activation = controller.activateExistingAtlasVault('vault-alpha');
      await Future<void>.delayed(Duration.zero);
      expect(enteredActivation.isCompleted, isFalse);

      release.complete();
      await enteredActivation.future;
      expect(await activation, AtlasVaultActivationResult.activated);
      await compatibilitySave;

      expect(privatePersistence.isActive, isTrue);
      expect(transport.savedSearchNames, <String>['Search 1']);
      expect(controller.savedSearches, isEmpty);
      expect(controller.connectionMessage, startsWith('Save search failed:'));
    },
  );

  test(
    'activation rejects an in-flight compatibility tracker result',
    () async {
      final entered = Completer<void>();
      final release = Completer<void>();
      final transport = _RecordingTransport()
        ..enteredCompatibilitySaveJob = entered
        ..releaseCompatibilitySaveJob = release;
      final enteredActivation = Completer<void>();
      final privatePersistence = _FakePrivateStatePersistence()
        ..enteredActivation = enteredActivation;
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        privateStatePersistence: privatePersistence,
      );
      addTearDown(controller.dispose);

      final compatibilitySave = controller.saveJob(
        JobSearchResult.fromJson(_jobJson),
      );
      await entered.future;
      final activation = controller.activateExistingAtlasVault('vault-alpha');
      await Future<void>.delayed(Duration.zero);
      expect(enteredActivation.isCompleted, isFalse);

      release.complete();
      await enteredActivation.future;
      expect(await activation, AtlasVaultActivationResult.activated);
      await compatibilitySave;

      expect(privatePersistence.isActive, isTrue);
      expect(transport.savedJobKeys, <String>['undp_oracle_hcm:34063']);
      expect(controller.trackerRecords, isEmpty);
      expect(controller.connectionMessage, startsWith('Save job failed:'));
    },
  );

  test('deactivation fences a late activation completion', () async {
    final enteredActivation = Completer<void>();
    final releaseActivation = Completer<void>();
    final privatePersistence =
        _FakePrivateStatePersistence(
            activationSnapshot: AtlasVaultPrivateStateSnapshot(
              savedSearches: <AtlasSavedSearch>[
                AtlasSavedSearch(
                  name: 'Encrypted search',
                  request: const AtlasSearchRequest(text: 'encrypted'),
                ),
              ],
              trackerRecords: const <AtlasApplicationRecord>[],
            ),
          )
          ..enteredActivation = enteredActivation
          ..releaseActivation = releaseActivation;
    final controller = AtlasAppController(
      privateStatePersistence: privatePersistence,
    );
    addTearDown(controller.dispose);

    final activation = controller.activateExistingAtlasVault('vault-alpha');
    await enteredActivation.future;
    await controller.deactivateAtlasVault();
    expect(controller.savedSearches, isEmpty);

    releaseActivation.complete();

    expect(await activation, AtlasVaultActivationResult.failed);
    expect(privatePersistence.isActive, isFalse);
    expect(controller.savedSearches, isEmpty);
    expect(controller.trackerRecords, isEmpty);
  });

  test(
    'activation rejects an in-flight compatibility saved-search read',
    () async {
      final enteredRead = Completer<void>();
      final releaseRead = Completer<void>();
      final transport = _RecordingTransport()
        ..enteredCompatibilitySavedSearchRead = enteredRead
        ..releaseCompatibilitySavedSearchRead = releaseRead
        ..savedSearchStore.add(<String, Object?>{
          'name': 'Compatibility search',
          'request': const <String, Object?>{},
          'created_at': '2026-07-02T00:00:00Z',
        });
      final privatePersistence = _FakePrivateStatePersistence(
        activationSnapshot: AtlasVaultPrivateStateSnapshot(
          savedSearches: <AtlasSavedSearch>[
            AtlasSavedSearch(
              name: 'Encrypted search',
              request: const AtlasSearchRequest(text: 'encrypted'),
            ),
          ],
          trackerRecords: const <AtlasApplicationRecord>[],
        ),
      );
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        privateStatePersistence: privatePersistence,
      );
      addTearDown(controller.dispose);

      final compatibilityRead = controller.testConnection(
        Uri.parse('http://atlas.test:8765'),
      );
      await enteredRead.future;
      expect(
        await controller.activateExistingAtlasVault('vault-alpha'),
        AtlasVaultActivationResult.activated,
      );
      releaseRead.complete();
      await compatibilityRead;

      expect(privatePersistence.isActive, isTrue);
      expect(controller.savedSearches.map((value) => value.name), <String>[
        'Encrypted search',
      ]);
    },
  );

  test('activation rejects an in-flight compatibility tracker read', () async {
    final enteredRead = Completer<void>();
    final releaseRead = Completer<void>();
    final transport = _RecordingTransport()
      ..enteredCompatibilityTrackerRead = enteredRead
      ..releaseCompatibilityTrackerRead = releaseRead
      ..trackerStore.add(<String, Object?>{
        'id': 'compatibility-record',
        'job_key': 'undp:compatibility',
        'status': 'saved',
        'updated_at': '2026-07-02T00:00:00Z',
      });
    final privatePersistence = _FakePrivateStatePersistence(
      activationSnapshot: AtlasVaultPrivateStateSnapshot(
        savedSearches: const <AtlasSavedSearch>[],
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
      initialBaseURL: Uri.parse('http://atlas.test:8765'),
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
      privateStatePersistence: privatePersistence,
    );
    addTearDown(controller.dispose);

    final compatibilityRead = controller.testConnection(
      Uri.parse('http://atlas.test:8765'),
    );
    await enteredRead.future;
    expect(
      await controller.activateExistingAtlasVault('vault-alpha'),
      AtlasVaultActivationResult.activated,
    );
    releaseRead.complete();
    await compatibilityRead;

    expect(privatePersistence.isActive, isTrue);
    expect(controller.trackerRecords.map((value) => value.jobKey), <String>[
      'undp:encrypted',
    ]);
  });

  test(
    'authority change fails closed during private activation admission',
    () async {
      final originalAuthority = Uri.parse('http://atlas.test:8765');
      final replacementAuthority = Uri.parse('http://atlas.next:8765');
      final admissionEntered = Completer<void>();
      final releaseAdmission = Completer<void>();
      addTearDown(() {
        if (!releaseAdmission.isCompleted) {
          releaseAdmission.complete();
        }
      });
      final transport = _RecordingTransport();
      final privatePersistence = _FakePrivateStatePersistence();
      final controller = AtlasAppController(
        initialBaseURL: originalAuthority,
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        privateStatePersistence: privatePersistence,
        compatibilityPrivateStateAdmission: () async {
          admissionEntered.complete();
          await releaseAdmission.future;
          return false;
        },
      );
      addTearDown(controller.dispose);

      final activation = controller.activateExistingAtlasVault('vault-alpha');
      await admissionEntered.future;
      await controller.saveAndReload(replacementAuthority);
      expect(controller.baseURL, originalAuthority);
      expect(controller.connectionStatus, 'Not connected');

      releaseAdmission.complete();

      expect(await activation, AtlasVaultActivationResult.activated);
      expect(privatePersistence.isActive, isTrue);
      expect(privatePersistence.calls, contains('activate'));
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
    },
  );

  test(
    'switching authorities deactivates encrypted private state before reload',
    () async {
      final originalAuthority = Uri.parse('http://atlas.test:8765');
      final replacementAuthority = Uri.parse('http://atlas.next:8765');
      final enteredDeactivation = Completer<void>();
      final releaseDeactivation = Completer<void>();
      addTearDown(() {
        if (!releaseDeactivation.isCompleted) {
          releaseDeactivation.complete();
        }
      });
      final originalTransport = _RecordingTransport();
      final replacementTransport = _RecordingTransport()
        ..savedSearchStore.add(<String, Object?>{
          'name': 'Replacement compatibility search',
          'request': const <String, Object?>{},
          'created_at': '2026-07-02T00:00:00Z',
        })
        ..trackerStore.add(<String, Object?>{
          'id': 'replacement-record',
          'job_key': 'undp:replacement',
          'status': 'saved',
          'updated_at': '2026-07-02T00:00:00Z',
        });
      final privatePersistence =
          _FakePrivateStatePersistence(
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
            )
            ..enteredDeactivation = enteredDeactivation
            ..releaseDeactivation = releaseDeactivation;
      final controller = AtlasAppController(
        initialBaseURL: originalAuthority,
        clientFactory: (baseURL) => AtlasAPIClient(
          baseURL: baseURL,
          transport: baseURL == replacementAuthority
              ? replacementTransport
              : originalTransport,
        ),
        privateStatePersistence: privatePersistence,
      );
      addTearDown(controller.dispose);
      expect(
        await controller.activateExistingAtlasVault('vault-alpha'),
        AtlasVaultActivationResult.activated,
      );

      final reload = controller.saveAndReload(replacementAuthority);
      await enteredDeactivation.future;

      expect(controller.baseURL, originalAuthority);
      expect(replacementTransport.savedSearchReadCount, 0);
      expect(replacementTransport.trackerReadCount, 0);

      releaseDeactivation.complete();
      await reload;

      expect(controller.baseURL, replacementAuthority);
      expect(privatePersistence.isActive, isFalse);
      expect(privatePersistence.calls, contains('deactivate'));
      expect(replacementTransport.savedSearchReadCount, 1);
      expect(replacementTransport.trackerReadCount, 1);
      expect(controller.savedSearches.map((value) => value.name), <String>[
        'Replacement compatibility search',
      ]);
      expect(controller.trackerRecords.map((value) => value.jobKey), <String>[
        'undp:replacement',
      ]);
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

  test(
    'deactivation keeps compatibility and cache fenced until completion',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_deactivation_fence_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final enteredDeactivation = Completer<void>();
      final releaseDeactivation = Completer<void>();
      final privatePersistence =
          _FakePrivateStatePersistence(
              activationSnapshot: AtlasVaultPrivateStateSnapshot(
                savedSearches: <AtlasSavedSearch>[
                  AtlasSavedSearch(
                    name: 'ENCRYPTED_PRIVATE_NAME',
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
            )
            ..enteredDeactivation = enteredDeactivation
            ..releaseDeactivation = releaseDeactivation;
      final transport = _RecordingTransport();
      final store = AtlasLocalCacheStore(
        file: File('${tempDir.path}/atlas-local-cache.json'),
        now: () => _cacheFixtureNow,
      );
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStore: store,
        privateStatePersistence: privatePersistence,
        now: () => _cacheFixtureNow,
      );
      addTearDown(controller.dispose);
      expect(
        await controller.activateExistingAtlasVault('vault-alpha'),
        AtlasVaultActivationResult.activated,
      );

      final deactivation = controller.deactivateAtlasVault();
      await enteredDeactivation.future;
      expect(privatePersistence.isActive, isFalse);

      var listenerAttemptedCompatibilityWrite = false;
      Future<void>? listenerCompatibilityWrite;
      controller.addListener(() {
        if (listenerAttemptedCompatibilityWrite ||
            controller.savedSearches.isNotEmpty) {
          return;
        }
        listenerAttemptedCompatibilityWrite = true;
        listenerCompatibilityWrite = controller.saveCurrentSearch();
      });

      await controller.saveCurrentSearch();
      await controller.saveJob(JobSearchResult.fromJson(_jobJson));
      await controller.saveAndReload(Uri.parse('http://atlas.test:8765'));

      expect(transport.savedSearchNames, isEmpty);
      expect(transport.savedJobKeys, isEmpty);
      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
      final cached = await store.read();
      expect(cached, isNotNull);
      expect(cached!.containsPrivateState, isFalse);
      expect(
        await store.file.readAsString(),
        isNot(contains('ENCRYPTED_PRIVATE_NAME')),
      );

      releaseDeactivation.complete();
      await deactivation;
      await listenerCompatibilityWrite;
      expect(listenerAttemptedCompatibilityWrite, isTrue);
      expect(transport.savedSearchNames, isEmpty);
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
    },
  );

  test(
    'activation drains an admitted plaintext cache write before preflight',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_activation_cache_fence_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final backingFile = File(
        '${tempDir.path}/not-created/atlas-local-cache.json',
      );
      final enteredWrite = Completer<void>();
      final releaseWrite = Completer<void>();
      addTearDown(() {
        if (!releaseWrite.isCompleted) {
          releaseWrite.complete();
        }
      });
      var protectionActive = false;
      final store = AtlasLocalCacheStore(
        file: _ParentCreateGatedFile(
          backingFile,
          entered: enteredWrite,
          release: releaseWrite,
        ),
        now: () => _cacheFixtureNow,
        privateStateProtectionActive: () => protectionActive,
      );
      final privatePersistence = _FakePrivateStatePersistence();
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: _RecordingTransport()),
        localCacheStore: store,
        privateStatePersistence: privatePersistence,
        now: () => _cacheFixtureNow,
      );
      addTearDown(controller.dispose);
      controller.cacheSavedAt = _cacheFixtureNow;

      final compatibilitySave = controller.saveCurrentSearch();
      await enteredWrite.future;
      expect(controller.savedSearches, isNotEmpty);
      controller.savedSearches = const <AtlasSavedSearch>[];
      protectionActive = true;

      final activation = controller.activateExistingAtlasVault('vault-alpha');
      await Future<void>(() {});
      AtlasVaultActivationResult? activationResult;
      try {
        expect(privatePersistence.calls, isEmpty);
      } finally {
        if (!releaseWrite.isCompleted) {
          releaseWrite.complete();
        }
        await compatibilitySave;
        activationResult = await activation;
      }
      expect(activationResult, AtlasVaultActivationResult.activated);
      expect(await backingFile.exists(), isFalse);
      expect(await File('${backingFile.path}.tmp').exists(), isFalse);
    },
  );

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

  test(
    'migration authority bootstraps before private cache and endpoint reads',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_migration_authority_bootstrap_',
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
      final transport = _RecordingTransport();
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
      );
      addTearDown(migrationOwner.dispose);
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStore: store,
        now: () => _cacheFixtureNow,
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(controller.dispose);

      await controller.bootstrapPrivateAuthorityAndLoadPersistedCache();

      expect(migrationCoordinator.calls, <String>['inspect-authority']);
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
      expect(controller.connectionStatus, 'Offline (cached)');

      await controller.testConnection(Uri.parse('http://atlas.test:8765'));
      await controller.saveCurrentSearch();

      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
      expect(transport.savedSearchNames, isEmpty);
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
    },
  );

  test(
    'pending recovery import blocks legacy authority before migration bootstrap',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_recovery_import_authority_bootstrap_',
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
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.legacy,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
      );
      addTearDown(migrationOwner.dispose);
      var importJournalReads = 0;
      final controller = AtlasAppController(
        localCacheStore: store,
        recoveryImportPending: () async {
          importJournalReads += 1;
          return true;
        },
        now: () => _cacheFixtureNow,
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(controller.dispose);

      await controller.bootstrapPrivateAuthorityAndLoadPersistedCache();

      expect(importJournalReads, 1);
      expect(migrationCoordinator.calls, isEmpty);
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
      expect(controller.connectionStatus, 'Offline (cached)');
    },
  );

  test(
    'recovery-import journal read failure blocks legacy authority',
    () async {
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.legacy,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
      );
      addTearDown(migrationOwner.dispose);
      final controller = AtlasAppController(
        recoveryImportPending: () async {
          throw StateError('deterministic protected journal read failure');
        },
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(controller.dispose);

      await controller.bootstrapPrivateAuthorityAndLoadPersistedCache();

      expect(migrationCoordinator.calls, isEmpty);
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
    },
  );

  test('pending recovery import blocks ordinary vault activation', () async {
    final persistence = _FakePrivateStatePersistence();
    final controller = AtlasAppController(
      privateStatePersistence: persistence,
      recoveryImportPending: () async => true,
    );
    addTearDown(controller.dispose);
    await controller.bootstrapPrivateAuthorityAndLoadPersistedCache();

    final result = await controller.activateExistingAtlasVault('vault-alpha');

    expect(result, AtlasVaultActivationResult.failed);
    expect(persistence.calls, isEmpty);
  });

  test(
    'import activation leaves journal authority to the pending callback',
    () async {
      final source = await File(
        'lib/features/app_shell/atlas_app.dart',
      ).readAsString().then((value) => value.replaceAll('\r\n', '\n'));
      final activationStart = source.indexOf(
        'Future<AtlasVaultActivationResult> _activateExistingAtlasVault',
      );
      final activationEnd = source.indexOf(
        'Future<void> deactivateAtlasVault()',
        activationStart,
      );
      expect(activationStart, isNonNegative);
      expect(activationEnd, greaterThan(activationStart));
      final activationSource = source.substring(activationStart, activationEnd);

      expect(
        activationSource,
        isNot(contains('_recoveryImportBlocksLegacyPrivateAuthority = false')),
      );
      expect(
        source,
        contains(
          'void _recoveryImportPendingDidChange(bool pending) {\n'
          '    _recoveryImportBlocksLegacyPrivateAuthority = pending;',
        ),
      );
    },
  );

  test(
    'restart rollback restores preserved private cache without network reads',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_migration_rollback_restore_',
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
      final transport = _RecordingTransport();
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
        rollbackRestorationState: _privateCachePlaintextState(),
      )..resumeStage = AtlasVaultPlaintextMigrationStage.encryptedVerified;
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStore: store,
        now: () => _cacheFixtureNow,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
        legacyPrivateStateRestorer: controller,
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(migrationOwner.dispose);
      addTearDown(controller.dispose);

      await controller.bootstrapPrivateAuthorityAndLoadPersistedCache();
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);

      await migrationOwner.resumeMigration();
      expect(
        migrationOwner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.prepared,
      );
      await migrationOwner.discardPreparedMigration();

      expect(
        migrationOwner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
      );
      expect(controller.savedSearches.single.name, 'Legacy private search');
      expect(controller.trackerRecords.single.id, 'legacy-record');
      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
      expect(migrationCoordinator.calls, <String>[
        'inspect-authority',
        'resume',
        'discard',
        'inspect-authority',
        'restore-reviewed',
      ]);
    },
  );

  test(
    'restart rollback restores remote-only private state before legacy readiness',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_migration_remote_rollback_restore_',
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
      final transport = _RecordingTransport()
        ..savedSearchStore.add(<String, Object?>{
          'name': 'Remote legacy search',
          'description': 'remote private description',
          'request': _compatibilityMigrationRequest(text: 'remote-private'),
          'created_at': '2026-07-01T00:00:00Z',
          'updated_at': '2026-07-02T00:00:00Z',
        })
        ..trackerStore.add(<String, Object?>{
          'id': 'remote-legacy-record',
          'job_key': 'undp:remote-legacy',
          'status': 'saved',
          'notes': 'remote private notes',
          'applied_at': '2026-07-03T00:00:00Z',
          'updated_at': '2026-07-04T00:00:00Z',
        });
      final restoreEntered = Completer<void>();
      final releaseRestore = Completer<void>();
      addTearDown(() {
        if (!releaseRestore.isCompleted) {
          releaseRestore.complete();
        }
      });
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
        rollbackRestorationState: _remoteOnlyPlaintextState(),
        restoreEntered: restoreEntered,
        releaseRestore: releaseRestore,
      )..resumeStage = AtlasVaultPlaintextMigrationStage.encryptedVerified;
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStore: store,
        now: () => _cacheFixtureNow,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
        legacyPrivateStateRestorer: controller,
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(migrationOwner.dispose);
      addTearDown(controller.dispose);

      await controller.bootstrapPrivateAuthorityAndLoadPersistedCache();
      await migrationOwner.resumeMigration();
      expect(
        migrationOwner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.prepared,
      );

      var discardCompleted = false;
      final discard = migrationOwner.discardPreparedMigration().whenComplete(
        () => discardCompleted = true,
      );
      await restoreEntered.future;
      expect(
        discardCompleted,
        isFalse,
        reason: 'legacy readiness must wait for reviewed-inventory restore',
      );
      expect(
        migrationOwner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.restoringLegacy,
      );
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);

      releaseRestore.complete();
      await discard;

      expect(
        migrationOwner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
      );
      expect(controller.savedSearches.single.name, 'Remote legacy search');
      expect(controller.trackerRecords.single.id, 'remote-legacy-record');
      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
      expect(migrationCoordinator.calls, contains('restore-reviewed'));
    },
  );

  test(
    'restart rollback restores reviewed cache and remote-only records together',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_migration_mixed_rollback_restore_',
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
      final transport = _RecordingTransport()
        ..savedSearchStore.add(<String, Object?>{
          'name': 'Remote legacy search',
          'description': 'remote private description',
          'request': _compatibilityMigrationRequest(text: 'remote-private'),
          'created_at': '2026-07-01T00:00:00Z',
          'updated_at': '2026-07-02T00:00:00Z',
        })
        ..trackerStore.add(<String, Object?>{
          'id': 'remote-legacy-record',
          'job_key': 'undp:remote-legacy',
          'status': 'saved',
          'notes': 'remote private notes',
          'applied_at': '2026-07-03T00:00:00Z',
          'updated_at': '2026-07-04T00:00:00Z',
        });
      final cacheState = _privateCachePlaintextState();
      final remoteState = _remoteOnlyPlaintextState();
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
        rollbackRestorationState: AtlasVaultPlaintextPrivateState(
          savedSearches: <AtlasSavedSearch>[
            ...cacheState.savedSearches,
            ...remoteState.savedSearches,
          ],
          trackerRecords: <AtlasApplicationRecord>[
            ...cacheState.trackerRecords,
            ...remoteState.trackerRecords,
          ],
          authorityBaseURL: Uri.parse('http://atlas.test:8765'),
        ),
      )..resumeStage = AtlasVaultPlaintextMigrationStage.encryptedVerified;
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStore: store,
        now: () => _cacheFixtureNow,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
        legacyPrivateStateRestorer: controller,
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(migrationOwner.dispose);
      addTearDown(controller.dispose);

      await controller.bootstrapPrivateAuthorityAndLoadPersistedCache();
      await migrationOwner.resumeMigration();
      await migrationOwner.discardPreparedMigration();

      expect(
        migrationOwner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
      );
      expect(controller.savedSearches.map((value) => value.name), <String>[
        'Legacy private search',
        'Remote legacy search',
      ]);
      expect(controller.trackerRecords.map((value) => value.id), <String>[
        'legacy-record',
        'remote-legacy-record',
      ]);
      expect(transport.savedSearchReadCount, 0);
      expect(transport.trackerReadCount, 0);
      expect(migrationCoordinator.calls, contains('restore-reviewed'));
    },
  );

  test(
    'prepared migration suppresses public cache writes without stripping private state',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_prepared_migration_cache_write_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final cacheFile = File('${tempDir.path}/atlas-local-cache.json');
      final seedStore = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => _cacheFixtureNow,
      );
      await seedStore.write(_privateCacheSnapshot());
      final initialBytes = await cacheFile.readAsBytes();
      final initialPrivateState = await seedStore
          .readPrivateStateForMigration();
      final transport = _RecordingTransport();
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.legacy,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
      );
      addTearDown(migrationOwner.dispose);
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStoreFactory: ({privateStateProtectionActive}) async {
          return AtlasLocalCacheStore(
            file: cacheFile,
            now: () => _cacheFixtureNow,
            privateStateProtectionActive: privateStateProtectionActive,
          );
        },
        now: () => _cacheFixtureNow,
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(controller.dispose);
      await controller.bootstrapPrivateAuthorityAndLoadPersistedCache();
      await migrationOwner.reviewInventory();
      await migrationOwner.prepareEncryptedMigration();

      await controller.refreshLocalSave();

      expect(transport.searchBodies, isNotEmpty);
      expect(await cacheFile.readAsBytes(), orderedEquals(initialBytes));
      final preservedPrivateState = await seedStore
          .readPrivateStateForMigration();
      expect(
        preservedPrivateState.privateSha256,
        initialPrivateState.privateSha256,
      );
      expect(
        preservedPrivateState.savedSearches.single.name,
        'Legacy private search',
      );
      expect(preservedPrivateState.trackerRecords.single.id, 'legacy-record');

      await controller.clearPersistedCache();

      expect(
        controller.connectionMessage,
        'Local cache changes are unavailable during AtlasVault migration.',
      );
      expect(await cacheFile.exists(), isTrue);
      expect(await cacheFile.readAsBytes(), orderedEquals(initialBytes));
    },
  );

  test(
    'cache clear rechecks migration blocking after async store resolution',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_migration_clear_recheck_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final cacheFile = File('${tempDir.path}/atlas-local-cache.json');
      final seedStore = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => _cacheFixtureNow,
      );
      await seedStore.write(_privateCacheSnapshot());
      final initialBytes = await cacheFile.readAsBytes();
      final enteredFactory = Completer<void>();
      final releaseFactory = Completer<void>();
      addTearDown(() {
        if (!releaseFactory.isCompleted) {
          releaseFactory.complete();
        }
      });
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.legacy,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
      );
      addTearDown(migrationOwner.dispose);
      final controller = AtlasAppController(
        localCacheStoreFactory: ({privateStateProtectionActive}) async {
          enteredFactory.complete();
          await releaseFactory.future;
          return AtlasLocalCacheStore(
            file: cacheFile,
            now: () => _cacheFixtureNow,
            privateStateProtectionActive: privateStateProtectionActive,
          );
        },
        now: () => _cacheFixtureNow,
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(controller.dispose);
      await migrationOwner.bootstrapAuthority();
      await migrationOwner.reviewInventory();

      final clear = controller.clearPersistedCache();
      await enteredFactory.future;
      await migrationOwner.prepareEncryptedMigration();
      releaseFactory.complete();
      await clear;

      expect(
        controller.connectionMessage,
        'Local cache changes are unavailable during AtlasVault migration.',
      );
      expect(await cacheFile.exists(), isTrue);
      expect(await cacheFile.readAsBytes(), orderedEquals(initialBytes));
    },
  );

  test(
    'migration admission drains a previously admitted cache clear',
    () async {
      final tempDir = await Directory.systemTemp.createTemp(
        'atlas_migration_clear_drain_',
      );
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });
      final cacheFile = File('${tempDir.path}/atlas-local-cache.json');
      await cacheFile.writeAsString('public cache');
      final enteredDelete = Completer<void>();
      final releaseDelete = Completer<void>();
      addTearDown(() {
        if (!releaseDelete.isCompleted) {
          releaseDelete.complete();
        }
      });
      final migrationCoordinator = _ControllerMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.legacy,
      );
      final migrationOwner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: migrationCoordinator,
      );
      addTearDown(migrationOwner.dispose);
      final controller = AtlasAppController(
        localCacheStore: AtlasLocalCacheStore(
          file: _DeleteGatedFile(
            cacheFile,
            entered: enteredDelete,
            release: releaseDelete,
          ),
        ),
      );
      controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: migrationOwner),
      );
      addTearDown(controller.dispose);
      await migrationOwner.bootstrapAuthority();
      await migrationOwner.reviewInventory();
      controller.savedSearches = <AtlasSavedSearch>[
        AtlasSavedSearch(
          name: 'Controller-only legacy search',
          request: const AtlasSearchRequest(text: 'legacy'),
        ),
      ];
      controller.trackerRecords = <AtlasApplicationRecord>[
        AtlasApplicationRecord(
          id: 'controller-only-record',
          jobKey: 'legacy:controller-only',
          status: 'saved',
        ),
      ];

      final clear = controller.clearPersistedCache();
      await enteredDelete.future;
      await migrationOwner.prepareEncryptedMigration();
      var drainCompleted = false;
      final drain = controller.drainAdmittedPlaintextOperations().whenComplete(
        () => drainCompleted = true,
      );
      await Future<void>(() {});

      expect(drainCompleted, isFalse);

      releaseDelete.complete();
      await clear;
      await drain;

      expect(drainCompleted, isTrue);
      expect(await cacheFile.exists(), isFalse);
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
    },
  );

  test(
    'migration admission drains all admitted compatibility writes before reads',
    () async {
      final enteredSearch = Completer<void>();
      final releaseSearch = Completer<void>();
      final enteredTracker = Completer<void>();
      final releaseTracker = Completer<void>();
      addTearDown(() {
        if (!releaseSearch.isCompleted) {
          releaseSearch.complete();
        }
        if (!releaseTracker.isCompleted) {
          releaseTracker.complete();
        }
      });
      final transport = _RecordingTransport()
        ..enteredCompatibilitySaveSearch = enteredSearch
        ..releaseCompatibilitySaveSearch = releaseSearch
        ..enteredCompatibilitySaveJob = enteredTracker
        ..releaseCompatibilitySaveJob = releaseTracker;
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
      );
      addTearDown(controller.dispose);

      final savedSearch = controller.saveCurrentSearch();
      await enteredSearch.future;
      final savedTracker = controller.saveJob(
        JobSearchResult.fromJson(_jobJson),
      );
      var drainCompleted = false;
      final drain = controller.drainAdmittedPlaintextOperations().whenComplete(
        () => drainCompleted = true,
      );
      await Future<void>(() {});
      expect(drainCompleted, isFalse);

      releaseSearch.complete();
      await enteredTracker.future;
      await Future<void>(() {});
      expect(drainCompleted, isFalse);

      releaseTracker.complete();
      await drain;
      await savedSearch;
      await savedTracker;

      final compatibilityClient = AtlasAPIClient(
        baseURL: Uri.parse('http://atlas.test:8765'),
        transport: transport,
      );
      expect(
        (await compatibilityClient.savedSearches()).map((value) => value.name),
        <String>['Search 1'],
      );
      expect(
        (await compatibilityClient.trackerRecords()).map(
          (value) => value.jobKey,
        ),
        <String>['undp_oracle_hcm:34063'],
      );
    },
  );

  test(
    'recovery import admission blocks new legacy writes and drains admitted work',
    () async {
      final enteredSearch = Completer<void>();
      final releaseSearch = Completer<void>();
      addTearDown(() {
        if (!releaseSearch.isCompleted) {
          releaseSearch.complete();
        }
      });
      final transport = _RecordingTransport()
        ..enteredCompatibilitySaveSearch = enteredSearch
        ..releaseCompatibilitySaveSearch = releaseSearch;
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://atlas.test:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
      );
      addTearDown(controller.dispose);

      final admittedSave = controller.saveCurrentSearch();
      await enteredSearch.future;
      var admissionCompleted = false;
      final admission = controller.beginRecoveryImportAdmission().whenComplete(
        () => admissionCompleted = true,
      );
      await Future<void>.value();

      expect(admissionCompleted, isFalse);
      await controller.saveJob(JobSearchResult.fromJson(_jobJson));
      expect(transport.savedJobKeys, isEmpty);

      releaseSearch.complete();
      await admittedSave;
      await admission;
      expect(admissionCompleted, isTrue);
      expect(transport.savedSearchNames, <String>['Search 1']);

      controller.endRecoveryImportAdmission();
      await controller.saveJob(JobSearchResult.fromJson(_jobJson));
      expect(transport.savedJobKeys, <String>['undp_oracle_hcm:34063']);
    },
  );

  test('migration context attaches once without starting authority work', () {
    final firstCoordinator = _ControllerMigrationCoordinator(
      authorityState: AtlasVaultPlaintextAuthorityState.legacy,
    );
    final secondCoordinator = _ControllerMigrationCoordinator(
      authorityState: AtlasVaultPlaintextAuthorityState.legacy,
    );
    final firstOwner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: firstCoordinator,
    );
    final secondOwner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: secondCoordinator,
    );
    final controller = AtlasAppController();
    addTearDown(firstOwner.dispose);
    addTearDown(secondOwner.dispose);
    addTearDown(controller.dispose);

    controller.attachPlaintextMigrationContext(
      AtlasVaultPlaintextMigrationContext(owner: firstOwner),
    );

    expect(firstCoordinator.calls, isEmpty);
    expect(
      () => controller.attachPlaintextMigrationContext(
        AtlasVaultPlaintextMigrationContext(owner: secondOwner),
      ),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );
    expect(secondCoordinator.calls, isEmpty);
  });

  test('Windows default assembly owns explicit migration without auto-run', () {
    final source = File(
      'lib/features/app_shell/atlas_app.dart',
    ).readAsStringSync();
    final windowsStart = source.indexOf('if (Platform.isWindows) {');
    final fallbackStart = source.indexOf(
      'if (!Platform.isAndroid)',
      windowsStart < 0 ? 0 : windowsStart,
    );

    expect(windowsStart, isNonNegative);
    expect(fallbackStart, greaterThan(windowsStart));
    final windowsAssembly = source.substring(windowsStart, fallbackStart);
    final helperStart = source.indexOf(
      'AtlasVaultPlaintextMigrationPresentationOwner _attachWindowsMigration({',
    );
    final helperEnd = source.indexOf(
      '_AtlasDefaultControllerAssembly _buildDefaultControllerAssembly()',
      helperStart < 0 ? 0 : helperStart,
    );
    expect(helperStart, isNonNegative);
    expect(helperEnd, greaterThan(helperStart));
    final migrationAssembly = source.substring(helperStart, helperEnd);
    expect(windowsAssembly, contains('AtlasWindowsVaultSecureKeyStore()'));
    expect(windowsAssembly, contains('AtlasWindowsVaultLocalStoreIO()'));
    expect(windowsAssembly, contains('AtlasVaultPrivateStateRuntime('));
    expect(windowsAssembly, contains('privateStatePersistence: runtime'));
    expect(
      windowsAssembly,
      contains('_AtlasControllerCompatibilityMigrationSource'),
    );
    expect(windowsAssembly, contains('_attachWindowsMigration('));
    expect(
      windowsAssembly,
      contains('AtlasWindowsProtectedMigrationJournalStore()'),
    );
    expect(
      windowsAssembly,
      contains('AtlasWindowsProtectedRecoveryImportJournalStore()'),
    );
    expect(windowsAssembly, contains('AtlasWindowsSelectedVaultStore()'));
    expect(
      windowsAssembly,
      contains('AtlasWindowsPlaintextAuthorityAdmission('),
    );
    expect(
      migrationAssembly,
      contains('AtlasVaultPlaintextMigrationCoordinator('),
    );
    expect(
      migrationAssembly,
      contains('profile: AtlasVaultPlaintextMigrationProfile.windows'),
    );
    expect(
      migrationAssembly,
      contains('AtlasWindowsDesktopCacheMigrationSource'),
    );
    expect(
      migrationAssembly,
      contains('AtlasVaultPlaintextMigrationPresentationOwner('),
    );
    expect(migrationAssembly, contains('attachPlaintextMigrationContext('));
    expect(
      migrationAssembly,
      contains(
        'platform: AtlasVaultPlaintextMigrationPresentationPlatform.windows',
      ),
    );
    final sideEffectFreeAssembly = '$windowsAssembly\n$migrationAssembly';
    expect(
      sideEffectFreeAssembly,
      isNot(contains('activateExistingAtlasVault(')),
    );
    expect(sideEffectFreeAssembly, isNot(contains('.inventory(')));
    expect(sideEffectFreeAssembly, isNot(contains('.prepare(')));
    expect(sideEffectFreeAssembly, isNot(contains('.resume(')));
    expect(windowsAssembly, contains('_attachWindowsEncryptedBackup('));
    expect(
      migrationAssembly,
      contains('AtlasWindowsEncryptedDocumentTransport()'),
    );
    expect(
      migrationAssembly,
      contains('AtlasVaultInteroperabilityCoordinator('),
    );
    expect(
      migrationAssembly,
      contains('AtlasVaultInteroperabilityPresentationOwner('),
    );
    expect(migrationAssembly, contains('recoveryImportJournalStore:'));
    expect(migrationAssembly, contains('importTransactionAdmission:'));
    expect(migrationAssembly, contains('activateImportedVault:'));
    expect(migrationAssembly, contains('attachInteroperabilityContext('));
    expect(sideEffectFreeAssembly, isNot(contains('.beginRecoverySetup(')));
    expect(sideEffectFreeAssembly, isNot(contains('.savePreparedExport(')));
  });
}

final class _ControllerMigrationCoordinator
    implements AtlasVaultPlaintextMigrationCoordinating {
  _ControllerMigrationCoordinator({
    required this.authorityState,
    this.rollbackRestorationState,
    this.restoreEntered,
    this.releaseRestore,
  });

  final List<String> calls = <String>[];
  AtlasVaultPlaintextAuthorityState authorityState;
  final AtlasVaultPlaintextPrivateState? rollbackRestorationState;
  final Completer<void>? restoreEntered;
  final Completer<void>? releaseRestore;
  AtlasVaultPlaintextMigrationStage resumeStage =
      AtlasVaultPlaintextMigrationStage.commitInProgress;

  AtlasVaultPlaintextMigrationSummary _summary({
    AtlasVaultPlaintextMigrationStage? stage,
  }) {
    return AtlasVaultPlaintextMigrationSummary(
      savedSearchCount: 1,
      trackerRecordCount: 1,
      localCachePrivatePresent: true,
      compatibilityPrivatePresent: true,
      stage: stage,
    );
  }

  @override
  Future<AtlasVaultPlaintextAuthorityState> inspectAuthority() async {
    calls.add('inspect-authority');
    return authorityState;
  }

  @override
  Future<bool> inspectPreparedRollbackAvailability() async {
    return resumeStage == AtlasVaultPlaintextMigrationStage.prepared ||
        resumeStage == AtlasVaultPlaintextMigrationStage.encryptedVerified;
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> inventory() async {
    calls.add('inventory');
    return _summary();
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> prepare() async {
    calls.add('prepare');
    return _summary(stage: AtlasVaultPlaintextMigrationStage.encryptedVerified);
  }

  @override
  Future<void> discardPrepared() async {
    calls.add('discard');
    authorityState = AtlasVaultPlaintextAuthorityState.legacy;
  }

  @override
  Future<void> restoreReviewedLegacyPrivateState(
    AtlasVaultLegacyPrivateStateRestoring restorer,
  ) async {
    calls.add('restore-reviewed');
    restoreEntered?.complete();
    await releaseRestore?.future;
    await restorer.restoreLegacyPrivateStateAfterRollback(
      rollbackRestorationState ??
          AtlasVaultPlaintextPrivateState(
            savedSearches: const <AtlasSavedSearch>[],
            trackerRecords: const <AtlasApplicationRecord>[],
            authorityBaseURL: Uri.parse('http://atlas.test:8765'),
          ),
    );
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> finalizeAndActivate() async {
    calls.add('finalize');
    return _summary(stage: AtlasVaultPlaintextMigrationStage.completionPending);
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> resume() async {
    calls.add('resume');
    return _summary(stage: resumeStage);
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> activateSelected() async {
    calls.add('activate-selected');
    return _summary(stage: AtlasVaultPlaintextMigrationStage.completionPending);
  }
}

final class _ControllerPlaintextAuthorityAdmission
    implements AtlasVaultPlaintextAuthorityAdmission {
  bool blocked = false;
  int rejectedCalls = 0;

  @override
  Future<T> runLegacyPrivateOperation<T>(Future<T> Function() operation) {
    if (blocked) {
      rejectedCalls += 1;
      return Future<T>.error(
        const AtlasVaultPlaintextAuthorityAdmissionException(),
      );
    }
    return operation();
  }

  @override
  Future<T> runMigrationTransaction<T>(Future<T> Function() operation) {
    return operation();
  }
}

final class _RecordingTransport implements AtlasTransport {
  final searchTexts = <String?>[];
  final searchSorts = <String?>[];
  final searchBodies = <Map<String, Object?>>[];
  final savedSearchNames = <String>[];
  final savedJobKeys = <String>[];
  final detailRequests = <String>[];
  final savedSearchStore = <Map<String, Object?>>[];
  final trackerStore = <Map<String, Object?>>[];
  int savedSearchReadCount = 0;
  int trackerReadCount = 0;
  int updateReadCount = 0;
  int sourceReadCount = 0;
  Completer<void>? enteredCompatibilitySaveSearch;
  Completer<void>? releaseCompatibilitySaveSearch;
  Completer<void>? enteredCompatibilitySaveJob;
  Completer<void>? releaseCompatibilitySaveJob;
  Completer<void>? enteredCompatibilitySavedSearchRead;
  Completer<void>? releaseCompatibilitySavedSearchRead;
  Completer<void>? enteredCompatibilityTrackerRead;
  Completer<void>? releaseCompatibilityTrackerRead;
  String? expectedSearchText;
  Completer<void>? enteredExpectedSearch;
  Completer<void>? releaseExpectedSearch;
  int healthReadCount = 0;

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
        healthReadCount += 1;
        return {
          'status': 'ok',
          'open_jobs': 128,
          'enabled_sources': 12,
          'schema_version': '2026-07',
        };
      case 'api/search':
        expect(request.method, 'POST');
        searchBodies.add(request.jsonBody ?? const <String, Object?>{});
        final searchText = request.jsonBody?['text'] as String?;
        searchTexts.add(searchText);
        searchSorts.add(request.jsonBody?['sort'] as String?);
        if (searchText == expectedSearchText &&
            !(enteredExpectedSearch?.isCompleted ?? true)) {
          enteredExpectedSearch!.complete();
        }
        if (searchText == expectedSearchText && releaseExpectedSearch != null) {
          await releaseExpectedSearch!.future;
        }
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
          enteredCompatibilitySaveSearch?.complete();
          if (releaseCompatibilitySaveSearch != null) {
            await releaseCompatibilitySaveSearch!.future;
          }
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
        enteredCompatibilitySavedSearchRead?.complete();
        if (releaseCompatibilitySavedSearchRead != null) {
          await releaseCompatibilitySavedSearchRead!.future;
        }
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
        enteredCompatibilitySaveJob?.complete();
        if (releaseCompatibilitySaveJob != null) {
          await releaseCompatibilitySaveJob!.future;
        }
        savedJobKeys.add('undp_oracle_hcm:34063');
        final record = <String, Object?>{
          'id': 'undp_oracle_hcm-34063',
          'job_key': 'undp_oracle_hcm:34063',
          'status': 'saved',
          'updated_at': '2026-07-02T00:00:00Z',
        };
        trackerStore
          ..removeWhere((existing) => existing['id'] == 'undp_oracle_hcm-34063')
          ..insert(0, record);
        return record;
      case 'api/tracker':
        trackerReadCount += 1;
        enteredCompatibilityTrackerRead?.complete();
        if (releaseCompatibilityTrackerRead != null) {
          await releaseCompatibilityTrackerRead!.future;
        }
        return trackerStore;
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

final class _ConditionalDeleteTransport implements AtlasTransport {
  _ConditionalDeleteTransport({this.response, this.error});

  final Object? response;
  final Object? error;
  final List<AtlasRequest> requests = <AtlasRequest>[];

  @override
  Future<Object?> send(AtlasRequest request) async {
    requests.add(request);
    final failure = error;
    if (failure != null) {
      throw failure;
    }
    return response;
  }
}

final class _StoredSavedSearchConditionalDeleteTransport
    implements AtlasTransport {
  _StoredSavedSearchConditionalDeleteTransport(this.storedSnapshot);

  final Map<String, Object?> storedSnapshot;
  final List<AtlasRequest> requests = <AtlasRequest>[];

  @override
  Future<Object?> send(AtlasRequest request) async {
    requests.add(request);
    if (request.method == 'GET' && request.path == 'api/saved-searches') {
      return <Object?>[storedSnapshot];
    }
    if (request.method == 'POST' &&
        request.path.endsWith('/conditional-delete')) {
      return const <String, Object?>{'outcome': 'deleted'};
    }
    fail('Unexpected request ${request.method} ${request.path}');
  }
}

final class _StoredTrackerConditionalDeleteTransport implements AtlasTransport {
  _StoredTrackerConditionalDeleteTransport(this.storedSnapshot);

  final Map<String, Object?> storedSnapshot;
  final List<AtlasRequest> requests = <AtlasRequest>[];

  @override
  Future<Object?> send(AtlasRequest request) async {
    requests.add(request);
    if (request.method == 'GET' && request.path == 'api/tracker') {
      return <Object?>[storedSnapshot];
    }
    if (request.method == 'POST' &&
        request.path.endsWith('/conditional-delete')) {
      return const <String, Object?>{'outcome': 'deleted'};
    }
    fail('Unexpected request ${request.method} ${request.path}');
  }
}

final class _FakePrivateStatePersistence
    implements AtlasVaultPrivateStatePersistence {
  _FakePrivateStatePersistence({
    AtlasVaultPrivateStateSnapshot? activationSnapshot,
  }) : _snapshot =
           activationSnapshot ??
           AtlasVaultPrivateStateSnapshot(
             savedSearches: const <AtlasSavedSearch>[],
             trackerRecords: const <AtlasApplicationRecord>[],
           );

  final List<String> calls = <String>[];
  AtlasVaultPrivateStateSnapshot _snapshot;
  Completer<void>? enteredSave;
  Completer<void>? releaseSave;
  Completer<void>? enteredActivation;
  Completer<void>? releaseActivation;
  Completer<void>? enteredDeactivation;
  Completer<void>? releaseDeactivation;

  @override
  bool isActive = false;

  @override
  Future<AtlasVaultActivationResult> activateExisting(String vaultId) async {
    calls.add('activate');
    enteredActivation?.complete();
    if (releaseActivation != null) {
      await releaseActivation!.future;
    }
    isActive = true;
    return AtlasVaultActivationResult.activated;
  }

  @override
  Future<void> deactivate() async {
    calls.add('deactivate');
    isActive = false;
    enteredDeactivation?.complete();
    if (releaseDeactivation != null) {
      await releaseDeactivation!.future;
    }
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

final class _ParentCreateGatedFile implements File {
  _ParentCreateGatedFile(
    this._delegate, {
    required this.entered,
    required this.release,
  });

  final File _delegate;
  final Completer<void> entered;
  final Completer<void> release;

  @override
  String get path => _delegate.path;

  @override
  Directory get parent {
    return _ParentCreateGatedDirectory(
      _delegate.parent,
      entered: entered,
      release: release,
    );
  }

  @override
  Future<bool> exists() => Future<bool>.value(false);

  @override
  dynamic noSuchMethod(Invocation invocation) {
    return super.noSuchMethod(invocation);
  }
}

final class _DeleteGatedFile implements File {
  _DeleteGatedFile(
    this._delegate, {
    required this.entered,
    required this.release,
  });

  final File _delegate;
  final Completer<void> entered;
  final Completer<void> release;

  @override
  String get path => _delegate.path;

  @override
  Future<bool> exists() => _delegate.exists();

  @override
  Future<FileSystemEntity> delete({bool recursive = false}) async {
    if (!entered.isCompleted) {
      entered.complete();
    }
    await release.future;
    return _delegate.delete(recursive: recursive);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) {
    return super.noSuchMethod(invocation);
  }
}

final class _ParentCreateGatedDirectory implements Directory {
  _ParentCreateGatedDirectory(
    this._delegate, {
    required this.entered,
    required this.release,
  });

  final Directory _delegate;
  final Completer<void> entered;
  final Completer<void> release;

  @override
  String get path => _delegate.path;

  @override
  Future<Directory> create({bool recursive = false}) async {
    if (!entered.isCompleted) {
      entered.complete();
    }
    await release.future;
    return _delegate.create(recursive: recursive);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) {
    return super.noSuchMethod(invocation);
  }
}

final class _TestTimer implements Timer {
  _TestTimer(this._callback);

  final void Function() _callback;
  bool _isActive = true;

  void fire() {
    if (!_isActive) {
      return;
    }
    _isActive = false;
    _callback();
  }

  @override
  bool get isActive => _isActive;

  @override
  int get tick => 0;

  @override
  void cancel() {
    _isActive = false;
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

AtlasVaultPlaintextPrivateState _privateCachePlaintextState() {
  final snapshot = _privateCacheSnapshot();
  return AtlasVaultPlaintextPrivateState(
    savedSearches: snapshot.savedSearches,
    trackerRecords: snapshot.trackerRecords,
    authorityBaseURL: snapshot.baseURL,
  );
}

AtlasVaultPlaintextPrivateState _remoteOnlyPlaintextState() {
  return AtlasVaultPlaintextPrivateState(
    savedSearches: <AtlasSavedSearch>[
      AtlasSavedSearch(
        name: 'Remote legacy search',
        description: 'remote private description',
        request: const AtlasSearchRequest(text: 'remote-private'),
        createdAt: '2026-07-01T00:00:00Z',
        updatedAt: '2026-07-02T00:00:00Z',
      ),
    ],
    trackerRecords: <AtlasApplicationRecord>[
      AtlasApplicationRecord(
        id: 'remote-legacy-record',
        jobKey: 'undp:remote-legacy',
        status: 'saved',
        notes: 'remote private notes',
        appliedAt: '2026-07-03T00:00:00Z',
        updatedAt: '2026-07-04T00:00:00Z',
      ),
    ],
    authorityBaseURL: Uri.parse('http://atlas.test:8765'),
  );
}

Map<String, Object?> _compatibilityMigrationRequest({required String text}) {
  return <String, Object?>{
    'text': text,
    'status': <String>['open'],
    'organizations': <String>[],
    'source_ids': <String>[],
    'ats_families': <String>[],
    'cities': <String>[],
    'countries_iso3': <String>[],
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
