import 'package:atlas/main.dart';
import 'package:atlas/atlas.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('Atlas app shell replaces the generated counter app', (
    tester,
  ) async {
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    expect(
      find.descendant(of: find.byType(AppBar), matching: find.text('Search')),
      findsOneWidget,
    );
    expect(find.text('Atlas'), findsNothing);
    expect(find.text('Flutter Demo Home Page'), findsNothing);
    expect(find.byIcon(Icons.add), findsNothing);
  });

  testWidgets('search header actions open filters and save current search', (
    tester,
  ) async {
    final transport = _SavedSearchTransport();
    final controller = AtlasAppController(
      initialBaseURL: Uri.parse('http://atlas.test:8765'),
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
    );
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      MaterialApp(home: AtlasHomeShell(controller: controller)),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Filters'));
    await tester.pumpAndSettle();
    expect(find.text('Filters'), findsOneWidget);
    expect(find.text('Open only'), findsWidgets);
    expect(find.text('Location'), findsOneWidget);
    expect(find.text('Scope'), findsOneWidget);
    expect(find.text('Apply filters'), findsOneWidget);

    await tester.tap(find.widgetWithText(SwitchListTile, 'Closing soon'));
    await tester.pumpAndSettle();
    await tester.drag(find.byType(ListView).last, const Offset(0, -520));
    await tester.pumpAndSettle();
    expect(find.text('Contract'), findsOneWidget);
    expect(find.text('Seniority'), findsOneWidget);
    expect(find.text('Grade'), findsOneWidget);
    await tester.drag(find.byType(ListView).last, const Offset(0, -760));
    await tester.pumpAndSettle();
    expect(find.text('Capability Tags'), findsOneWidget);
    await tester.tap(find.text('Apply filters'));
    await tester.pumpAndSettle();
    expect(controller.filters.closingSoon, isTrue);

    await tester.tap(find.byTooltip('Save search'));
    await tester.pumpAndSettle();
    expect(transport.savedSearchNames, ['Search 1']);

    await tester.tap(find.text('Saved').last);
    await tester.pumpAndSettle();

    expect(find.text('Saved Searches'), findsOneWidget);
    expect(find.textContaining('Search 1'), findsOneWidget);

    await tester.tap(find.textContaining('Search 1'));
    await tester.pumpAndSettle();
  });

  testWidgets('bottom navigation exposes primary Atlas tabs', (tester) async {
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    for (final label in ['Search', 'Saved', 'Updates', 'Sources', 'Settings']) {
      expect(find.text(label), findsWidgets);
    }

    await tester.tap(find.text('Saved').last);
    await tester.pumpAndSettle();
    expect(find.text('Saved Searches'), findsOneWidget);

    await tester.tap(find.text('Updates').last);
    await tester.pumpAndSettle();
    expect(find.text('Source Updates'), findsOneWidget);
    expect(find.text('No refresh runs available'), findsOneWidget);

    await tester.tap(find.text('Sources').last);
    await tester.pumpAndSettle();
    expect(find.text('Source Health'), findsOneWidget);
    expect(find.text('No source health returned'), findsOneWidget);

    await tester.tap(find.text('Settings').last);
    await tester.pumpAndSettle();
    expect(find.text('Atlas Settings'), findsOneWidget);
  });

  testWidgets(
    'search tab includes search, filters, status, sort, and empty state',
    (tester) async {
      await tester.pumpWidget(const MyApp());
      await tester.pumpAndSettle();

      expect(
        find.widgetWithText(
          TextField,
          'Title, keyword, skill, or organization',
        ),
        findsOneWidget,
      );
      expect(find.text('Closing soon'), findsOneWidget);
      expect(find.text('Remote'), findsOneWidget);
      expect(find.text('Best fit'), findsOneWidget);
      expect(find.text('0 searchable results'), findsOneWidget);
      expect(
        find.text('Offline until API connection is configured'),
        findsOneWidget,
      );
      expect(find.text('Sort: Closing soon'), findsOneWidget);
      expect(find.text('No local save available'), findsOneWidget);
      expect(
        find.text(
          'Connect to the local server once and refresh the local save to enable offline search.',
        ),
        findsOneWidget,
      );
    },
  );

  testWidgets('search tab keeps normal state compact but shows errors', (
    tester,
  ) async {
    final controller = AtlasAppController();
    addTearDown(controller.dispose);
    controller.reportValidationError('Connection failed: test server offline');

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasSearchSkeleton(controller: controller)),
      ),
    );
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.byIcon(AtlasIcons.info), findsOneWidget);
    expect(find.text('Connection failed: test server offline'), findsOneWidget);
  });

  testWidgets('source badge uses iOS-style source color monogram', (
    tester,
  ) async {
    final unicefJob = _badgeJob(
      sourceID: 'unicef_pageup',
      organization: 'UNICEF PageUp',
    );
    final undpJob = _badgeJob(
      sourceID: 'undp_oracle_hcm',
      organization: 'UNDP Oracle HCM',
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Row(
          children: [
            AtlasSourceBadge(job: unicefJob),
            AtlasSourceBadge(job: undpJob),
          ],
        ),
      ),
    );

    final unicefBox = tester.widget<Container>(
      find.widgetWithText(Container, 'UNI').first,
    );
    final undpBox = tester.widget<Container>(
      find.widgetWithText(Container, 'UND').first,
    );
    final unicefDecoration = unicefBox.decoration! as BoxDecoration;
    final undpDecoration = undpBox.decoration! as BoxDecoration;

    expect(unicefDecoration.color, isNot(AtlasPalette.accent));
    expect(unicefDecoration.color, isNot(undpDecoration.color));
    expect(tester.widget<Text>(find.text('UNI')).style?.color, Colors.white);
  });

  testWidgets('updates and sources tabs render live operational data', (
    tester,
  ) async {
    final transport = _OperationalTransport();
    final controller = AtlasAppController(
      initialBaseURL: Uri.parse('http://atlas.test:8765'),
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
    );
    addTearDown(controller.dispose);

    await controller.saveAndReload(Uri.parse('http://atlas.test:8765'));
    await tester.pumpWidget(
      MaterialApp(home: AtlasHomeShell(controller: controller)),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Updates').last);
    await tester.pumpAndSettle();
    expect(find.text('Source Updates'), findsOneWidget);
    expect(find.text('Recent Runs'), findsOneWidget);
    expect(find.text('UNDP Oracle HCM'), findsOneWidget);
    expect(
      find.textContaining('146 rows are still marked open'),
      findsOneWidget,
    );
    expect(find.textContaining('will show fetched'), findsNothing);

    await tester.tap(find.text('Sources').last);
    await tester.pumpAndSettle();
    expect(find.text('Source Health'), findsOneWidget);
    expect(find.text('UNDP Oracle HCM'), findsOneWidget);
    expect(find.textContaining('undp_oracle_hcm'), findsOneWidget);
    expect(find.textContaining('Each source will show'), findsNothing);

    await tester.tap(find.text('UNDP Oracle HCM'));
    await tester.pumpAndSettle();
    expect(
      find.descendant(of: find.byType(AppBar), matching: find.text('Search')),
      findsOneWidget,
    );
    expect(controller.filters.sourceIDs, {'undp_oracle_hcm'});
  });

  testWidgets('saved tab renders saved tracker records', (tester) async {
    final controller = AtlasAppController(
      clientFactory: (baseURL) => AtlasAPIClient(
        baseURL: baseURL,
        transport: _DetailFailingTransport(),
      ),
    );
    addTearDown(controller.dispose);
    controller.trackerRecords = [
      AtlasApplicationRecord(
        id: 'undp_oracle_hcm-34063',
        jobKey: 'undp_oracle_hcm:34063',
        status: 'saved',
      ),
    ];
    controller.savedSearches = [
      AtlasSavedSearch(
        name: 'Search 1',
        description: 'Existing search',
        request: const AtlasSearchRequest(),
      ),
    ];

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasSavedPanel(controller: controller)),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Saved Jobs'), findsOneWidget);
    expect(find.text('Saved vacancy 34063'), findsOneWidget);
    expect(find.textContaining('UNDP'), findsOneWidget);
    expect(find.text('Saved Searches'), findsOneWidget);

    await tester.tap(find.text('Saved vacancy 34063'));
    await tester.pumpAndSettle();

    expect(find.text('Job Detail'), findsOneWidget);
    expect(find.text('Weak detail state'), findsOneWidget);
    expect(find.text('Detail load failed'), findsOneWidget);
  });

  testWidgets(
    'job detail renders populated fields and keeps diagnostics hidden',
    (tester) async {
      final transport = _PopulatedDetailTransport();
      final controller = AtlasAppController(
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
      );
      addTearDown(controller.dispose);
      final job = JobSearchResult(
        jobKey: 'unicef_pageup:593420',
        title: 'Emergency Specialist',
        organization: 'UNICEF PageUp',
        sourceID: 'unicef_pageup',
        dutyStation: 'Nairobi, Kenya',
        gradeCode: 'P-3',
        contractLabel: 'Fixed term',
        workModality: 'Onsite',
        closingDate: DateTime.utc(2026, 7, 5, 23, 59),
        needsReview: false,
        scoreReasons: const ['Grade matched from source evidence.'],
        matchSummary: 'Location matched Nairobi from title evidence.',
        description: 'Search row fallback description.',
        status: 'open',
        nationalInternational: 'international',
      );

      await tester.pumpWidget(
        MaterialApp(
          home: AtlasJobDetailScreen(job: job, controller: controller),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Job Detail'), findsOneWidget);
      expect(find.text('Emergency Specialist'), findsOneWidget);
      expect(find.textContaining('UNICEF'), findsWidgets);
      expect(find.text('Full Description'), findsOneWidget);
      expect(
        find.textContaining('Coordinate emergency response'),
        findsOneWidget,
      );
      expect(find.text('Core Details'), findsOneWidget);
      expect(find.text('Deadline'), findsOneWidget);
      expect(find.text('Grade'), findsOneWidget);
      expect(find.text('P-3'), findsWidgets);
      expect(find.text('Contract'), findsOneWidget);
      expect(find.text('Fixed term'), findsWidgets);

      await tester.scrollUntilVisible(find.text('ATS page chrome hidden'), 300);
      await tester.pumpAndSettle();
      expect(find.text('ATS page chrome hidden'), findsOneWidget);
      expect(find.textContaining('Back to search results'), findsNothing);

      await tester.scrollUntilVisible(find.text('Responsibilities'), 300);
      await tester.pumpAndSettle();
      expect(find.text('Responsibilities'), findsOneWidget);
      expect(find.textContaining('Lead partner coordination'), findsOneWidget);

      await tester.scrollUntilVisible(
        find.text('Qualifications & education'),
        300,
      );
      await tester.pumpAndSettle();
      expect(find.text('Qualifications & education'), findsOneWidget);
      expect(find.textContaining('Advanced university degree'), findsOneWidget);

      await tester.scrollUntilVisible(find.text('Apply URL'), 300);
      await tester.pumpAndSettle();
      expect(find.text('Apply URL'), findsOneWidget);
      expect(find.text('Source URL'), findsOneWidget);

      await tester.scrollUntilVisible(find.text('Match diagnostics'), 300);
      await tester.pumpAndSettle();
      expect(find.text('Match diagnostics'), findsOneWidget);
      expect(
        find.text('Location matched Nairobi from title evidence.'),
        findsNothing,
      );
      expect(find.text('Raw Source Data'), findsNothing);
      expect(find.textContaining('debug source payload'), findsNothing);

      await tester.tap(find.text('Match diagnostics'));
      await tester.pumpAndSettle();
      expect(
        find.text('Location matched Nairobi from title evidence.'),
        findsOneWidget,
      );
      expect(find.text('Job Record'), findsOneWidget);
      expect(find.text('Detail quality status'), findsOneWidget);
      expect(find.text('Raw Source Data'), findsOneWidget);
      expect(find.textContaining('debug source payload'), findsOneWidget);

      await tester.tap(find.byTooltip('Save job'));
      await tester.pumpAndSettle();
      expect(controller.isJobSaved(job.jobKey), isTrue);
      expect(transport.savedJobKeys, ['unicef_pageup:593420']);
    },
  );
}

final class _SavedSearchTransport implements AtlasTransport {
  final savedSearchNames = <String>[];
  final savedSearchStore = <Map<String, Object?>>[];

  @override
  Future<Object?> send(AtlasRequest request) async {
    switch (request.path) {
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
        return savedSearchStore;
      case 'api/search':
        return {
          'total': 0,
          'limit': request.jsonBody?['limit'] ?? 50,
          'offset': 0,
          'results': <Object?>[],
          'facets': <String, Object?>{},
          'facet_labels': <String, Object?>{},
          'unclassified_count': 0,
        };
      default:
        fail('Unexpected request ${request.method} ${request.path}');
    }
  }
}

JobSearchResult _badgeJob({
  required String sourceID,
  required String organization,
}) {
  return JobSearchResult(
    jobKey: '$sourceID:1',
    title: 'Programme Officer',
    organization: organization,
    sourceID: sourceID,
    dutyStation: 'Geneva',
    gradeCode: 'P-3',
    contractLabel: 'Staff',
    workModality: 'Onsite',
    closingDate: DateTime.utc(2026, 7, 30),
    needsReview: false,
    scoreReasons: const [],
    matchSummary: 'Matched',
    description: 'Description',
  );
}

final class _OperationalTransport implements AtlasTransport {
  @override
  Future<Object?> send(AtlasRequest request) async {
    switch (request.path) {
      case 'api/health':
        return {
          'status': 'ok',
          'open_jobs': 2420,
          'enabled_sources': 12,
          'last_sync_at': '2026-07-02T00:00:00Z',
        };
      case 'api/search':
        return {
          'total': 2274,
          'limit': request.jsonBody?['limit'] ?? 50,
          'offset': 0,
          'results': <Object?>[],
          'facets': <String, Object?>{},
          'facet_labels': <String, Object?>{},
          'unclassified_count': 0,
        };
      case 'api/saved-searches':
        return <Object?>[];
      case 'api/tracker':
        return <Object?>[];
      case 'api/updates':
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
        return {
          'sources': [
            {
              'source_id': 'undp_oracle_hcm',
              'organization': 'UNDP Oracle HCM',
              'total_jobs': 2420,
              'open_jobs': 2274,
              'last_seen_at': '2026-07-02T00:00:00Z',
              'health_status': 'ok',
              'detail_attempted': 5,
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

final class _DetailFailingTransport implements AtlasTransport {
  @override
  Future<Object?> send(AtlasRequest request) async {
    throw const AtlasAPIException.http(503, 'detail unavailable');
  }
}

final class _PopulatedDetailTransport implements AtlasTransport {
  final savedJobKeys = <String>[];

  @override
  Future<Object?> send(AtlasRequest request) async {
    switch (request.path) {
      case 'api/job-detail':
        return {
          'job_key': request.queryParameters['job_key'],
          'title': 'Emergency Specialist',
          'description':
              'Coordinate emergency response planning with country teams and partners.',
          'status': 'open',
          'closes_at': '2026-07-05T23:59:00Z',
          'closes_at_local': '2026-07-06 08:59',
          'closes_tz': 'Asia/Tokyo',
          'apply_url': 'https://example.org/apply',
          'source_url': 'https://example.org/source',
          'deadline_info': {
            'source_text': 'Closes 5 July 2026',
            'source_local': '2026-07-05 23:59',
            'source_timezone': 'EAT',
          },
          'display_sections': [
            {
              'title': 'Responsibilities',
              'body':
                  'Achieving results such as:• Lead partner coordination and situation reporting across emergency clusters.• Maintain response dashboards and partner follow-up logs.• Prepare concise operational updates for senior leadership. Real responsibilities content continues with partner mapping, risk tracking, and field coordination. Back to search results Apply now Whatsapp Facebook LinkedIn Send me jobs like these',
            },
            {
              'title': 'Qualifications',
              'body': 'Advanced university degree and emergency experience.',
            },
            {
              'title': 'Job Record',
              'body': 'Hidden diagnostic metadata.',
              'rows': [
                {'label': 'Detail quality status', 'value': 'complete'},
                {'label': 'Extractor', 'value': 'fixture'},
              ],
            },
            {
              'title': 'Raw Source Data',
              'body': 'listing_html: <div>debug source payload</div>',
            },
          ],
        };
      case final path when path.startsWith('api/tracker/jobs/'):
        final jobKey = Uri.decodeComponent(
          path.substring('api/tracker/jobs/'.length),
        );
        savedJobKeys.add(jobKey);
        return {
          'id': 'saved-$jobKey',
          'job_key': jobKey,
          'status': 'saved',
          'updated_at': '2026-07-03T00:00:00Z',
        };
      default:
        fail('Unexpected request ${request.method} ${request.path}');
    }
  }
}
