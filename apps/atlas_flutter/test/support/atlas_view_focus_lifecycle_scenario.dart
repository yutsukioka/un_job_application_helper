import 'dart:ui' as ui;

import 'package:flutter_test/flutter_test.dart';

import 'atlas_view_cleanup.dart';

/// Uses the existing binding, including one initialized by the native launcher.
/// Delivers focus after the framework's real postTest, without pumping afterward.
void testAtlasViewFocusLifecycle(TestWidgetsFlutterBinding binding) {
  var bodyRan = false;
  Object? testFocusManager;
  var lateFocusEventCount = 0;
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
    testFocusManager = binding.focusManager;
    bodyRan = true;
  });
  // Per-test teardown has called the real binding.postTest by this point.
  // The integration binding's reporter was registered earlier, so this callback
  // and its uncaught focus microtask finish before that reporter (LIFO order).
  tearDownAll(() async {
    expect(bodyRan, isTrue);
    expect(binding.inTest, isFalse);
    expect(testFocusManager, isNotNull);
    expect(identical(binding.focusManager, testFocusManager), isFalse);
    binding.handleViewFocusChanged(
      ui.ViewFocusEvent(
        viewId: binding.platformDispatcher.implicitView!.viewId,
        state: ui.ViewFocusState.focused,
        direction: ui.ViewFocusDirection.undefined,
      ),
    );
    lateFocusEventCount += 1;
    expect(lateFocusEventCount, 1);
    // A stale View queues work on its disposed manager. Do not catch that error
    // or replace the test runner's handlers; let it fail this suite.
    await Future<void>.delayed(Duration.zero);
  });
}
