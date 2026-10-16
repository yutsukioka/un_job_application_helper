import 'package:flutter/gestures.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_view_cleanup.dart';

void main() {
  final binding = LiveTestWidgetsFlutterBinding();

  testWidgets('device pointers retain a render target after view cleanup', (
    tester,
  ) async {
    // Register first so device packets arrive after the actual cleanup under
    // reverse teardown ordering, while the tester still owns device dispatch.
    addTearDown(() async {
      expect(binding.deviceEventDispatcher, same(tester));
      expect(binding.shouldPropagateDevicePointerEvents, isFalse);
      final viewId = binding.platformDispatcher.implicitView!.viewId;
      for (final event in <PointerEvent>[
        PointerHoverEvent(
          viewId: viewId,
          kind: PointerDeviceKind.mouse,
          position: const Offset(10, 10),
        ),
        PointerDownEvent(
          viewId: viewId,
          pointer: 1,
          kind: PointerDeviceKind.mouse,
          position: const Offset(10, 10),
          buttons: kPrimaryMouseButton,
        ),
        PointerUpEvent(
          viewId: viewId,
          pointer: 1,
          kind: PointerDeviceKind.mouse,
          position: const Offset(10, 10),
        ),
      ]) {
        binding.handlePointerEventForSource(
          event,
          source: TestBindingEventSource.device,
        );
      }
      await tester.pump();
      expect(find.byType(View), findsNothing);
      expect(find.byType(FocusScope), findsNothing);
      expect(tester.takeException(), isNull);
    });
    registerAtlasViewCleanup(tester);
  });
}
