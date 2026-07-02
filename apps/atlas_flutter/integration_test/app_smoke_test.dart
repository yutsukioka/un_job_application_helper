import 'package:atlas/main.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('app launches and navigates primary Atlas tabs', (tester) async {
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    expect(find.text('Atlas'), findsOneWidget);
    expect(find.text('No local save available'), findsOneWidget);

    for (final tab in ['Saved', 'Updates', 'Sources', 'Settings', 'Search']) {
      await tester.tap(find.text(tab).last);
      await tester.pumpAndSettle();
      expect(find.text(tab), findsWidgets);
    }

    expect(find.text('0 results'), findsOneWidget);
  });
}
