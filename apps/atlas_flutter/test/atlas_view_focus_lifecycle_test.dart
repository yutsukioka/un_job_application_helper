import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_view_focus_lifecycle_scenario.dart';

class _FocusLifecycleBinding extends LiveTestWidgetsFlutterBinding
    with AtlasLateViewFocusBinding {}

void main() {
  testAtlasViewFocusLifecycle(_FocusLifecycleBinding());
}
