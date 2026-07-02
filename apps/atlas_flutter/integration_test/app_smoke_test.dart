import 'package:atlas/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('app launches and navigates primary Atlas tabs', (tester) async {
    await tester.pumpWidget(const MyApp());
    await _pumpAtlas(tester);

    expect(
      find.descendant(of: find.byType(AppBar), matching: find.text('Search')),
      findsOneWidget,
    );
    expect(_hasSearchResultState(), isTrue);
    expect(
      find.widgetWithText(TextField, 'Title, keyword, skill, or organization'),
      findsOneWidget,
    );
    expect(find.text('Sort: Closing soon'), findsOneWidget);

    for (final tab in ['Saved', 'Updates', 'Sources', 'Settings', 'Search']) {
      await tester.tap(find.text(tab).last);
      await _pumpAtlas(tester);
      expect(find.text(tab), findsWidgets);
    }

    expect(_hasSearchResultState(), isTrue);
  });
}

Future<void> _pumpAtlas(WidgetTester tester) async {
  await tester.pump(const Duration(milliseconds: 250));
  await tester.pump(const Duration(milliseconds: 750));
}

bool _hasSearchResultState() {
  return find.text('No local save available').evaluate().isNotEmpty ||
      find.textContaining('searchable result').evaluate().isNotEmpty;
}
