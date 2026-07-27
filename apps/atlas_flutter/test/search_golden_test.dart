import 'dart:io';

import 'package:atlas/atlas.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

bool get _skipAndroidParityPixelGoldens {
  // Android parity PNGs are supported on Linux; macOS and Windows use the
  // host-independent semantic tests below.
  return Platform.isMacOS || Platform.isWindows;
}

void main() {
  testWidgets(
    'search top matches compact Android parity golden',
    (tester) async {
      _configurePhoneViewport(tester);

      final controller = _searchTopGoldenController();
      addTearDown(controller.dispose);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: RepaintBoundary(
              child: AtlasSearchSkeleton(controller: controller),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await expectLater(
        find.byType(AtlasSearchSkeleton),
        matchesGoldenFile('goldens/android/search_top_compact.png'),
      );
    },
    skip: _skipAndroidParityPixelGoldens,
  );

  testWidgets(
    'filter sheet top matches dark iOS parity golden',
    (tester) async {
      _configurePhoneViewport(tester);

      final controller = _filterSheetGoldenController();
      addTearDown(controller.dispose);

      await _pumpFilterSheetGolden(tester, controller);

      await expectLater(
        find.byType(AtlasFilterSheet),
        matchesGoldenFile('goldens/android/filter_sheet_top.png'),
      );
    },
    skip: _skipAndroidParityPixelGoldens,
  );

  testWidgets(
    'filter sheet country cascade matches Android parity golden',
    (tester) async {
      _configurePhoneViewport(tester);

      final controller = _filterSheetGoldenController(
        filters: AtlasSearchFilters(countryISO3: 'JPN'),
      );
      addTearDown(controller.dispose);

      await _pumpFilterSheetGolden(tester, controller);

      await expectLater(
        find.byType(AtlasFilterSheet),
        matchesGoldenFile('goldens/android/filter_country_jpn.png'),
      );
    },
    skip: _skipAndroidParityPixelGoldens,
  );

  testWidgets(
    'filter sheet city cascade matches Android parity golden',
    (tester) async {
      _configurePhoneViewport(tester);

      final controller = _filterSheetGoldenController(
        filters: AtlasSearchFilters(city: 'Tokyo'),
      );
      addTearDown(controller.dispose);

      await _pumpFilterSheetGolden(tester, controller);

      await expectLater(
        find.byType(AtlasFilterSheet),
        matchesGoldenFile('goldens/android/filter_city_tokyo.png'),
      );
    },
    skip: _skipAndroidParityPixelGoldens,
  );

  testWidgets(
    'job detail top matches populated Android parity golden',
    (tester) async {
      _configurePhoneViewport(tester);

      final transport = _GoldenDetailTransport();
      final controller = AtlasAppController(
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
      );
      addTearDown(controller.dispose);

      await tester.pumpWidget(
        MaterialApp(
          home: RepaintBoundary(
            child: AtlasJobDetailScreen(
              job: _detailGoldenJob(),
              controller: controller,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await expectLater(
        find.byType(AtlasJobDetailScreen),
        matchesGoldenFile('goldens/android/job_detail_top.png'),
      );
    },
    skip: _skipAndroidParityPixelGoldens,
  );

  testWidgets('filter sheet exposes stable semantics on every host', (
    tester,
  ) async {
    _configurePhoneViewport(tester);
    final controller = _filterSheetGoldenController();
    addTearDown(controller.dispose);

    await _pumpFilterSheetGolden(tester, controller);

    expect(find.text('Filters'), findsOneWidget);
    expect(find.text('Open only'), findsWidgets);
    expect(find.text('Location'), findsOneWidget);
    expect(find.text('Scope'), findsOneWidget);
    expect(find.text('Apply filters'), findsOneWidget);
  });

  testWidgets('country cascade remains semantically selected on every host', (
    tester,
  ) async {
    _configurePhoneViewport(tester);
    final controller = _filterSheetGoldenController(
      filters: AtlasSearchFilters(countryISO3: 'JPN'),
    );
    addTearDown(controller.dispose);

    await _pumpFilterSheetGolden(tester, controller);

    expect(controller.filters.countryISO3, 'JPN');
    expect(find.text('Location'), findsOneWidget);
    expect(find.text('Apply filters'), findsOneWidget);
  });

  testWidgets('city cascade remains semantically selected on every host', (
    tester,
  ) async {
    _configurePhoneViewport(tester);
    final controller = _filterSheetGoldenController(
      filters: AtlasSearchFilters(city: 'Tokyo'),
    );
    addTearDown(controller.dispose);

    await _pumpFilterSheetGolden(tester, controller);

    expect(controller.filters.city, 'Tokyo');
    expect(find.text('Location'), findsOneWidget);
    expect(find.text('Apply filters'), findsOneWidget);
  });

  testWidgets('job detail exposes stable semantics on every host', (
    tester,
  ) async {
    _configurePhoneViewport(tester);
    final transport = _GoldenDetailTransport();
    final controller = AtlasAppController(
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
    );
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: AtlasJobDetailScreen(
          job: _detailGoldenJob(),
          controller: controller,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Job Detail'), findsOneWidget);
    expect(find.text('Emergency Specialist'), findsOneWidget);
    expect(find.text('Full Description'), findsOneWidget);
    expect(
      find.textContaining('Coordinate emergency response'),
      findsOneWidget,
    );
  });
}

void _configurePhoneViewport(WidgetTester tester) {
  tester.view.physicalSize = const Size(393, 852);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

Future<void> _pumpFilterSheetGolden(
  WidgetTester tester,
  AtlasAppController controller,
) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        backgroundColor: Colors.black,
        body: Align(
          alignment: Alignment.bottomCenter,
          child: RepaintBoundary(
            child: AtlasFilterSheet(controller: controller),
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

AtlasAppController _searchTopGoldenController() {
  final controller = AtlasAppController()
    ..connectionStatus = 'Connected'
    ..cacheSavedAt = DateTime.utc(2026, 7, 3, 4)
    ..total = 2274
    ..results = [
      JobSearchResult(
        jobKey: 'unicef_pageup:593906',
        title: 'Health Manager, (P-4), FT, Gaza, State of Palestine, MENAR',
        organization: 'UNICEF',
        sourceID: 'unicef_pageup',
        dutyStation: 'State OF Palestine (SoP)',
        gradeCode: 'P-4',
        contractLabel: 'STAFF',
        workModality: 'Unknown',
        nationalInternational: 'International',
        closingDate: null,
        needsReview: false,
        scoreReasons: const [],
        matchSummary:
            'Location matched State OF Palestine from source evidence.',
        description:
            'Lead health programme planning and implementation with partners.',
        status: 'open',
      ),
      JobSearchResult(
        jobKey: 'unicef_pageup:593907',
        title:
            'Investigation Officer (P-2), FT, #00121870, Office of Internal Audit & Investigation, Nairobi, Kenya',
        organization: 'UNICEF',
        sourceID: 'unicef_pageup',
        dutyStation: 'Nairobi, Kenya',
        gradeCode: 'P-2',
        contractLabel: 'STAFF',
        workModality: 'Onsite',
        nationalInternational: 'International',
        closingDate: null,
        needsReview: false,
        scoreReasons: const [],
        matchSummary: 'Location matched Nairobi from source evidence.',
        description: 'Investigate issues and prepare concise reports.',
        status: 'open',
      ),
    ];
  return controller;
}

AtlasAppController _filterSheetGoldenController({AtlasSearchFilters? filters}) {
  final controller = AtlasAppController()
    ..connectionStatus = 'Connected'
    ..cacheSavedAt = DateTime.utc(2026, 7, 3, 4)
    ..total = 2274
    ..filters = filters ?? AtlasSearchFilters()
    ..results = _goldenJobs()
    ..facetLabels = const {
      'organizations': {
        'UNICEF': 'UNICEF',
        'UNDP': 'UNDP',
        'IOM': 'IOM',
        'UNV': 'UNV',
      },
      'ccog_families': {
        'programme_management': 'Programme management',
        'medical_and_health': 'Medical and health',
        'administrative': 'Administrative',
        'social_scientists': 'Social scientists',
      },
    };
  return controller;
}

List<JobSearchResult> _goldenJobs() {
  return [
    JobSearchResult(
      jobKey: 'unicef_pageup:593906',
      title: 'Health Manager, (P-4), FT, Gaza, State of Palestine, MENAR',
      organization: 'UNICEF',
      sourceID: 'unicef_pageup',
      dutyStation: 'Gaza, State of Palestine',
      city: 'Gaza',
      countryISO3: 'PSE',
      gradeCode: 'P-4',
      standardSeniorityTier: 'senior',
      contractGroup: 'staff',
      contractLabel: 'STAFF',
      workModality: 'onsite',
      nationalInternational: 'international',
      ccogFamilyCode: 'medical_and_health',
      ccogFamilyLabel: 'Medical and health',
      capabilityTags: const ['health', 'programme_management'],
      closingDate: null,
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Location matched Gaza from source evidence.',
      description:
          'Lead health programme planning and implementation with partners.',
      status: 'open',
    ),
    JobSearchResult(
      jobKey: 'unicef_pageup:593907',
      title:
          'Investigation Officer (P-2), FT, #00121870, Office of Internal Audit & Investigation, Nairobi, Kenya',
      organization: 'UNICEF',
      sourceID: 'unicef_pageup',
      dutyStation: 'Nairobi, Kenya',
      city: 'Nairobi',
      countryISO3: 'KEN',
      gradeCode: 'P-2',
      standardSeniorityTier: 'entry_junior',
      contractGroup: 'staff',
      contractLabel: 'STAFF',
      workModality: 'onsite',
      nationalInternational: 'international',
      ccogFamilyCode: 'administrative',
      ccogFamilyLabel: 'Administrative',
      capabilityTags: const ['auditing', 'writing_editing'],
      closingDate: null,
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Location matched Nairobi from source evidence.',
      description: 'Investigate issues and prepare concise reports.',
      status: 'open',
    ),
    JobSearchResult(
      jobKey: 'undp_quantum:1893',
      title: 'Programme Analyst, Climate and Environment Portfolio',
      organization: 'UNDP',
      sourceID: 'undp_quantum',
      dutyStation: 'Tokyo, Japan',
      city: 'Tokyo',
      countryISO3: 'JPN',
      gradeCode: 'NPSA-9',
      standardSeniorityTier: 'mid',
      contractGroup: 'consultant_contractor',
      contractLabel: 'National Personnel Service Agreement',
      workModality: 'hybrid',
      nationalInternational: 'national',
      ccogFamilyCode: 'programme_management',
      ccogFamilyLabel: 'Programme management',
      capabilityTags: const ['environment', 'project_management'],
      closingDate: DateTime.utc(2026, 8, 12),
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Matched programme and location filters.',
      description:
          'Coordinate climate portfolio delivery, reporting, and partnerships.',
      status: 'open',
    ),
    JobSearchResult(
      jobKey: 'iom_taleo:8201',
      title: 'Supply Chain Assistant',
      organization: 'IOM',
      sourceID: 'iom_taleo',
      dutyStation: 'Bangkok, Thailand',
      city: 'Bangkok',
      countryISO3: 'THA',
      gradeCode: 'G-5',
      standardSeniorityTier: 'entry_junior',
      contractGroup: 'staff',
      contractLabel: 'General Service',
      workModality: 'onsite',
      nationalInternational: 'national',
      ccogFamilyCode: 'administrative',
      ccogFamilyLabel: 'Administrative',
      capabilityTags: const ['procurement', 'erp_systems'],
      closingDate: DateTime.utc(2026, 8, 20),
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Matched supply chain keywords.',
      description: 'Support procurement tracking and vendor coordination.',
      status: 'open',
    ),
    JobSearchResult(
      jobKey: 'unv_uvp:111',
      title: 'Online Volunteer - Social Media Campaign Support',
      organization: 'UNV',
      sourceID: 'unv_uvp',
      dutyStation: 'Home based',
      city: 'Remote',
      countryISO3: 'UNK',
      gradeCode: 'UG',
      standardSeniorityTier: 'generic_volunteer',
      contractGroup: 'volunteer',
      contractLabel: 'Volunteer',
      workModality: 'online_remote',
      nationalInternational: 'unknown',
      ccogFamilyCode: 'social_scientists',
      ccogFamilyLabel: 'Social scientists',
      capabilityTags: const ['social_media', 'writing_editing'],
      closingDate: DateTime.utc(2026, 7, 15),
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Matched remote volunteer filters.',
      description: 'Draft social posts and support online campaign reporting.',
      status: 'open',
    ),
  ];
}

JobSearchResult _detailGoldenJob() {
  return JobSearchResult(
    jobKey: 'unicef_pageup:593420',
    title: 'Emergency Specialist',
    organization: 'UNICEF PageUp',
    sourceID: 'unicef_pageup',
    dutyStation: 'Nairobi, Kenya',
    city: 'Nairobi',
    countryISO3: 'KEN',
    gradeCode: 'P-3',
    standardSeniorityTier: 'mid',
    contractGroup: 'staff',
    contractLabel: 'Fixed term',
    workModality: 'Onsite',
    closingDate: DateTime.utc(2026, 7, 5, 23, 59),
    needsReview: false,
    scoreReasons: const ['Grade matched from source evidence.'],
    matchSummary: 'Location matched Nairobi from title evidence.',
    description: 'Search row fallback description.',
    status: 'open',
    nationalInternational: 'international',
    applyURL: Uri.parse('https://example.org/apply'),
    sourceURL: Uri.parse('https://example.org/source'),
  );
}

final class _GoldenDetailTransport implements AtlasTransport {
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
                  'Achieving results such as:• Lead partner coordination and situation reporting across emergency clusters.• Maintain response dashboards and partner follow-up logs.• Prepare concise operational updates for senior leadership. Back to search results Apply now Whatsapp Facebook LinkedIn Send me jobs like these',
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
      default:
        fail('Unexpected request ${request.method} ${request.path}');
    }
  }
}
