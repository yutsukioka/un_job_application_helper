import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

/// Removes the final View's focus observer before its manager is disposed.
///
/// Register this inside testWidgets, before other resource teardowns. Flutter
/// mounts a final View after the test callback returns, so callback-finally
/// cleanup is too early. User teardowns run before binding.postTest, in reverse
/// registration order, allowing native resources to close before view cleanup.
void registerAtlasViewCleanup(WidgetTester tester) {
  addTearDown(() async {
    final view = tester.binding.platformDispatcher.implicitView!;
    await tester.pumpWidget(
      // Keep a render target for native device packets. An empty ViewCollection
      // removes it, but LiveTestWidgetsFlutterBinding still routes such packets
      // through the tester. RawView has no View focus observer or FocusScope.
      RawView(view: view, child: const SizedBox.shrink()),
      wrapWithView: false,
    );
    await tester.pump();
    expect(find.byType(View), findsNothing);
    expect(find.byType(FocusScope), findsNothing);
    expect(
      tester.binding.renderViews.map((renderView) => renderView.flutterView),
      contains(view),
    );
    expect(tester.takeException(), isNull);
  });
}
