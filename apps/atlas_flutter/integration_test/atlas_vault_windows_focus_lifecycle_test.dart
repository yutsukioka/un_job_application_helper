import 'package:integration_test/integration_test.dart';

import '../test/support/atlas_view_focus_lifecycle_scenario.dart';

class _FocusLifecycleBinding extends IntegrationTestWidgetsFlutterBinding
    with AtlasLateViewFocusBinding {}

void main() {
  testAtlasViewFocusLifecycle(_FocusLifecycleBinding());
}
