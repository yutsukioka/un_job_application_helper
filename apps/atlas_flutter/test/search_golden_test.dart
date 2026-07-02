import 'package:atlas/atlas.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('search top matches compact Android parity golden', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(393, 852);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final controller = AtlasAppController();
    addTearDown(controller.dispose);
    controller.connectionStatus = 'Connected';
    controller.cacheSavedAt = DateTime.utc(2026, 7, 3, 4);
    controller.total = 2274;
    controller.results = [
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
        scoreReasons: [],
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
        scoreReasons: [],
        matchSummary: 'Location matched Nairobi from source evidence.',
        description: 'Investigate issues and prepare concise reports.',
        status: 'open',
      ),
    ];

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
  });
}
