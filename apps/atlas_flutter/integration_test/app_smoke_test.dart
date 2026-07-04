import 'package:atlas/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('app launches and navigates primary Atlas tabs', (tester) async {
    await tester.pumpWidget(const MyApp());
    await _pumpAtlas(tester, ready: _hasSearchResultState);

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
      await _pumpAtlas(
        tester,
        ready: () => find.text(tab).evaluate().isNotEmpty,
      );
      expect(find.text(tab), findsWidgets);
    }

    expect(_hasSearchResultState(), isTrue);
  });
}

Future<void> _pumpAtlas(
  WidgetTester tester, {
  required bool Function() ready,
}) async {
  for (var attempt = 0; attempt < 80; attempt += 1) {
    await tester.pump();
    if (ready()) {
      return;
    }
    await tester.pump(const Duration(milliseconds: 50));
  }
  fail('Atlas UI did not reach the expected state.');
}

bool _hasSearchResultState() {
  return find.text('No local save available').evaluate().isNotEmpty ||
      find.textContaining('searchable result').evaluate().isNotEmpty;
}
