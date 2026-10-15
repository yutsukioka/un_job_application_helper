import 'dart:ui' as ui;

import 'package:flutter_test/flutter_test.dart';

import 'atlas_view_cleanup.dart';

/// Delivers a platform focus event through the normal observer path, after
/// Flutter has disposed the test's focus manager. Never pumps after postTest.
mixin AtlasLateViewFocusBinding on TestWidgetsFlutterBinding {
  int lateFocusEventCount = 0;
  Future<void>? lateFocusEventDrained;

  @override
  void postTest() {
    final viewId = platformDispatcher.implicitView!.viewId;
    super.postTest();
    handleViewFocusChanged(
      ui.ViewFocusEvent(
        viewId: viewId,
        state: ui.ViewFocusState.focused,
        direction: ui.ViewFocusDirection.undefined,
      ),
    );
    lateFocusEventCount += 1;
    // The event queues a focus microtask when a stale View remains mounted.
    // Keep the suite alive until it runs; leave asynchronous errors uncaught.
    lateFocusEventDrained = Future<void>.delayed(Duration.zero);
  }
}

void testAtlasViewFocusLifecycle(AtlasLateViewFocusBinding binding) {
  var bodyRan = false;
  testWidgets('late view focus after postTest has no disposed manager', (
    tester,
  ) async {
    registerAtlasViewCleanup(tester);
    addTearDown(() async {
      // Park on the root so the late View event requests a real transition.
      // Drain this setup's microtask while the manager is alive: scheduling it
      // inside synchronous postTest would itself leave work after disposal.
      binding.focusManager.rootScope.requestScopeFocus();
      await tester.pump();
    });
    bodyRan = true;
  });
  // Construct the integration binding before registering this callback so it
  // drains before integration_test's own final result reporter (LIFO order).
  tearDownAll(() async {
    expect(bodyRan, isTrue);
    expect(binding.lateFocusEventCount, 1);
    expect(binding.lateFocusEventDrained, isNotNull);
    await binding.lateFocusEventDrained;
  });
}
