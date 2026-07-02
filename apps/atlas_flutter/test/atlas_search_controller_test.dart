import 'package:atlas/atlas.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

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
    final transport = _RecordingTransport();
    final controller = AtlasAppController(
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
    expect(controller.statusSubtitle, 'Connected to http://10.253.1.43:8765');

    await controller.saveAndReload(Uri.parse('http://atlas.test:8765'));
    expect(controller.baseURL.toString(), 'http://atlas.test:8765');
    expect(controller.total, 1);
    expect(controller.cachedJobCount, 1);
    expect(controller.results.single.title, 'Programme Analyst');
    expect(
      controller.connectionMessage,
      'Saved http://atlas.test:8765 and refreshed 1 job.',
    );
    expect(controller.statusSubtitle, startsWith('Local save · updated '));

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

    expect(transport.searchTexts.single, 'analyst');
    expect(find.text('1 results'), findsOneWidget);
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

    await tester.tap(find.byIcon(Icons.close).first);
    await tester.pumpAndSettle();
    expect(controller.filters.openOnly, isFalse);

    await tester.tap(find.text('Sort: Closing soon'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Newest posted').last);
    await tester.pumpAndSettle();
    expect(controller.sortOrder, SortOrder.newestPosted);

    await tester.tap(find.text('Programme Analyst'));
    await tester.pumpAndSettle();

    expect(find.text('Why this matched'), findsOneWidget);
    expect(find.text('Matched the current search filters.'), findsOneWidget);
  });
}

final class _RecordingTransport implements AtlasTransport {
  final searchTexts = <String?>[];
  final searchSorts = <String?>[];
  final searchBodies = <Map<String, Object?>>[];
  final savedSearchNames = <String>[];
  final savedSearchStore = <Map<String, Object?>>[];

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
        return savedSearchStore;
      default:
        fail('Unexpected request ${request.method} ${request.path}');
    }
  }
}

final class _FailingTransport implements AtlasTransport {
  @override
  Future<Object?> send(AtlasRequest request) async {
    throw const AtlasAPIException.http(503, 'job-api unavailable');
  }
}

const _jobJson = {
  'job_key': 'undp_oracle_hcm:34063',
  'title': 'Programme Analyst',
  'organization': 'UNDP Oracle HCM',
  'source_id': 'undp_oracle_hcm',
  'duty_station': 'Nairobi, Kenya',
  'grade_code': 'P-3',
  'contract_group': 'Fixed term',
  'work_modality': 'Onsite',
  'closing_date': '2026-07-30T23:59:00Z',
  'needs_review': false,
  'score_reasons': <String>[],
  'match_summary': 'Matched current filters',
  'description': 'Role summary',
  'status': 'open',
};
