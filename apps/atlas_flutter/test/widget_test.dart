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
    expect(find.text('Remote'), findsWidgets);

    await tester.tap(find.widgetWithText(SwitchListTile, 'Remote'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(SwitchListTile, 'Closing soon'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(SwitchListTile, 'Best fit'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(SwitchListTile, 'Open only'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Close filters'));
    await tester.pumpAndSettle();
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

    await tester.tap(find.text('Sources').last);
    await tester.pumpAndSettle();
    expect(find.text('Source Health'), findsOneWidget);

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
      expect(find.text('0 results'), findsOneWidget);
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
    await tester.pumpAndSettle();

    expect(find.byIcon(Icons.info_outline), findsOneWidget);
    expect(find.text('Connection failed: test server offline'), findsOneWidget);
  });
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
