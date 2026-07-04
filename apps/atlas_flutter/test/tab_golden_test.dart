import 'package:atlas/atlas.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('saved tab matches implemented Android parity golden', (
    tester,
  ) async {
    _configurePhoneViewport(tester);
    final controller = _tabGoldenController();
    addTearDown(controller.dispose);

    await _pumpPanel(tester, AtlasSavedPanel(controller: controller));

    await expectLater(
      find.byType(AtlasSavedPanel),
      matchesGoldenFile('goldens/android/saved_tab.png'),
    );
  });

  testWidgets('updates tab matches implemented Android parity golden', (
    tester,
  ) async {
    _configurePhoneViewport(tester);
    final controller = _tabGoldenController();
    addTearDown(controller.dispose);

    await _pumpPanel(tester, AtlasUpdatesPanel(controller: controller));

    await expectLater(
      find.byType(AtlasUpdatesPanel),
      matchesGoldenFile('goldens/android/updates_tab.png'),
    );
  });

  testWidgets('sources tab matches implemented Android parity golden', (
    tester,
  ) async {
    _configurePhoneViewport(tester);
    final controller = _tabGoldenController();
    addTearDown(controller.dispose);

    await _pumpPanel(
      tester,
      AtlasSourcesPanel(controller: controller, onSourceSelected: (_) {}),
    );

    await expectLater(
      find.byType(AtlasSourcesPanel),
      matchesGoldenFile('goldens/android/sources_tab.png'),
    );
  });

  testWidgets('settings tab matches implemented Android parity golden', (
    tester,
  ) async {
    _configurePhoneViewport(tester);
    final controller = _tabGoldenController();
    addTearDown(controller.dispose);

    await _pumpPanel(tester, AtlasSettingsPanel(controller: controller));

    await expectLater(
      find.byType(AtlasSettingsPanel),
      matchesGoldenFile('goldens/android/settings_tab.png'),
    );
  });
}

void _configurePhoneViewport(WidgetTester tester) {
  tester.view.physicalSize = const Size(393, 852);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

Future<void> _pumpPanel(WidgetTester tester, Widget panel) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(body: RepaintBoundary(child: panel)),
    ),
  );
  await tester.pumpAndSettle();
}

AtlasAppController _tabGoldenController() {
  final controller = AtlasAppController()
    ..connectionStatus = 'Connected'
    ..cacheSavedAt = DateTime.utc(2026, 7, 3, 4, 6)
    ..cachedJobCount = 2266
    ..total = 2266
    ..healthSummary = AtlasHealthSummary(
      status: 'ok',
      openJobs: 2420,
      enabledSources: 12,
      lastSyncAt: '2026-07-02T02:38:47.964722+00:00',
    )
    ..results = _tabJobs()
    ..savedSearches = [
      AtlasSavedSearch(
        name: 'Search 1',
        description: 'Query: climate · 3 filters · Sort: Closing soon',
        request: AtlasSearchRequest.fromFilters(
          filters: AtlasSearchFilters(
            closingSoon: true,
            countryISO3: 'JPN',
            capabilityTags: const {'environment'},
          ),
          query: 'climate',
          sortOrder: SortOrder.closingSoon,
          limit: 100,
          now: DateTime.utc(2026, 7, 3),
        ),
        createdAt: '2026-07-03T04:06:00Z',
      ),
    ]
    ..trackerRecords = [
      AtlasApplicationRecord(
        id: 'saved-593420',
        jobKey: 'unicef_pageup:593420',
        status: 'saved',
        updatedAt: '2026-07-03T04:05:00Z',
      ),
      AtlasApplicationRecord(
        id: 'saved-1893',
        jobKey: 'undp_quantum:1893',
        status: 'applied',
        appliedAt: '2026-07-02T12:00:00Z',
        updatedAt: '2026-07-03T02:10:00Z',
      ),
    ]
    ..updateRuns = [
      AtlasSourceRun(
        sourceID: 'undp_quantum',
        fetched: 42,
        inserted: 3,
        updated: 8,
        missing: 1,
        closed: 4,
        observedAt: '2026-07-03T04:00:00Z',
      ),
      AtlasSourceRun(
        sourceID: 'unicef_pageup',
        fetched: 31,
        inserted: 2,
        updated: 5,
        missing: 0,
        closed: 1,
        observedAt: '2026-07-03T03:45:00Z',
      ),
      AtlasSourceRun(
        sourceID: 'iom_taleo',
        fetched: 19,
        inserted: 0,
        updated: 2,
        missing: 2,
        closed: 0,
        observedAt: '2026-07-03T03:20:00Z',
      ),
    ]
    ..sources = [
      AtlasSourceSummary(
        sourceID: 'undp_quantum',
        organization: 'UNDP Quantum',
        totalJobs: 742,
        openJobs: 681,
        healthStatus: 'ok',
        lastSeenAt: '2026-07-03T04:00:00Z',
        detailAttempted: 120,
        detailFailed: 2,
        missingTransitionAllowed: true,
      ),
      AtlasSourceSummary(
        sourceID: 'unicef_pageup',
        organization: 'UNICEF PageUp',
        totalJobs: 512,
        openJobs: 463,
        healthStatus: 'ok',
        lastSeenAt: '2026-07-03T03:45:00Z',
        detailAttempted: 94,
        detailFailed: 1,
        missingTransitionAllowed: true,
      ),
      AtlasSourceSummary(
        sourceID: 'iom_taleo',
        organization: 'IOM Taleo',
        totalJobs: 184,
        openJobs: 166,
        healthStatus: 'warning',
        lastSeenAt: '2026-07-03T03:20:00Z',
        detailAttempted: 37,
        detailFailed: 6,
        missingTransitionAllowed: false,
      ),
    ];
  return controller;
}

List<JobSearchResult> _tabJobs() {
  return [
    JobSearchResult(
      jobKey: 'unicef_pageup:593420',
      title: 'Emergency Specialist',
      organization: 'UNICEF',
      sourceID: 'unicef_pageup',
      dutyStation: 'Nairobi, Kenya',
      gradeCode: 'P-3',
      contractLabel: 'Fixed term',
      workModality: 'Onsite',
      closingDate: DateTime.utc(2026, 7, 5, 23, 59),
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Location matched Nairobi from source evidence.',
      description: 'Coordinate emergency response planning.',
      status: 'open',
      nationalInternational: 'international',
    ),
    JobSearchResult(
      jobKey: 'undp_quantum:1893',
      title: 'Programme Analyst, Climate and Environment Portfolio',
      organization: 'UNDP',
      sourceID: 'undp_quantum',
      dutyStation: 'Tokyo, Japan',
      gradeCode: 'NPSA-9',
      contractLabel: 'National Personnel Service Agreement',
      workModality: 'Hybrid',
      closingDate: DateTime.utc(2026, 8, 12),
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Matched programme and location filters.',
      description: 'Coordinate climate portfolio delivery.',
      status: 'open',
      nationalInternational: 'national',
    ),
    JobSearchResult(
      jobKey: 'iom_taleo:8201',
      title: 'Supply Chain Assistant',
      organization: 'IOM',
      sourceID: 'iom_taleo',
      dutyStation: 'Bangkok, Thailand',
      gradeCode: 'G-5',
      contractLabel: 'General Service',
      workModality: 'Onsite',
      closingDate: DateTime.utc(2026, 8, 20),
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Matched supply chain keywords.',
      description: 'Support procurement tracking.',
      status: 'open',
      nationalInternational: 'national',
    ),
  ];
}
