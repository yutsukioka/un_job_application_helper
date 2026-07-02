import 'package:atlas/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('app launches and navigates primary Atlas tabs', (tester) async {
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    expect(
      find.descendant(of: find.byType(AppBar), matching: find.text('Search')),
      findsOneWidget,
    );
    expect(find.text('No local save available'), findsOneWidget);
    expect(
      find.widgetWithText(TextField, 'Title, keyword, skill, or organization'),
      findsOneWidget,
    );
    expect(find.text('Sort: Closing soon'), findsOneWidget);

    await tester.tap(find.byTooltip('Filters'));
    await tester.pumpAndSettle();
    expect(find.text('Filters'), findsOneWidget);
    await tester.tap(find.byTooltip('Close filters'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Sort: Closing soon'));
    await tester.pumpAndSettle();
    expect(find.text('Newest posted'), findsOneWidget);
    await tester.tap(find.text('Newest posted').last);
    await tester.pumpAndSettle();
    expect(find.text('Sort: Newest posted'), findsOneWidget);

    for (final tab in ['Saved', 'Updates', 'Sources', 'Settings', 'Search']) {
      await tester.tap(find.text(tab).last);
      await tester.pumpAndSettle();
      expect(find.text(tab), findsWidgets);
    }

    expect(find.text('0 results'), findsOneWidget);
  });
}
