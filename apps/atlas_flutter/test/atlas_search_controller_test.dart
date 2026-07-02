import 'package:atlas/atlas.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
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
    expect(controller.statusSubtitle, startsWith('Updated '));

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
    expect(
      find.text('Local save refreshed: 1 job cached for this session.'),
      findsOneWidget,
    );
    expect(find.text('Programme Analyst'), findsOneWidget);
    expect(find.textContaining('UNDP'), findsWidgets);
    expect(find.textContaining('Nairobi, Kenya'), findsOneWidget);
    expect(find.text('P-3'), findsOneWidget);
    expect(find.textContaining('Fixed'), findsOneWidget);
    expect(find.text('Onsite'), findsOneWidget);
    expect(find.text('Matched the current search filters.'), findsOneWidget);
    expect(find.text('Sort: Closing soon'), findsOneWidget);
  });
}

final class _RecordingTransport implements AtlasTransport {
  final searchTexts = <String?>[];
  final searchSorts = <String?>[];

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
