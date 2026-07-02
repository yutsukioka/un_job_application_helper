import 'package:atlas/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('Atlas app shell replaces the generated counter app', (
    tester,
  ) async {
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    expect(find.text('Atlas'), findsOneWidget);
    expect(find.text('Flutter Demo Home Page'), findsNothing);
    expect(find.byIcon(Icons.add), findsNothing);
  });

  testWidgets('bottom navigation exposes primary Atlas tabs', (tester) async {
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    for (final label in ['Search', 'Saved', 'Updates', 'Sources', 'Settings']) {
      expect(find.text(label), findsWidgets);
    }

    await tester.tap(find.text('Saved').last);
    await tester.pumpAndSettle();
    expect(find.text('Saved Jobs'), findsOneWidget);

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
}
