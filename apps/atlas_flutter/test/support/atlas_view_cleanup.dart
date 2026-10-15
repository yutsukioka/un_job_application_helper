import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

/// Unmounts the framework's final View before its focus manager is disposed.
///
/// Register this inside testWidgets, before other resource teardowns. Flutter
/// mounts a final View after the test callback returns, so callback-finally
/// cleanup is too early. User teardowns run before binding.postTest, in reverse
/// registration order, allowing native resources to close before view cleanup.
void registerAtlasViewCleanup(WidgetTester tester) {
  addTearDown(() async {
    await tester.pumpWidget(
      const ViewCollection(views: <Widget>[]),
      wrapWithView: false,
    );
    await tester.pump();
    expect(find.byType(View), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
