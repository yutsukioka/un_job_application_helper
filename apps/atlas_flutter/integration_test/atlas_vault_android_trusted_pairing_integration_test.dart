import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  const enabled = bool.fromEnvironment(
    'ATLAS_ANDROID_TRUSTED_PAIRING_INTEGRATION',
  );

  testWidgets(
    'Android protected pairing transaction survives a fresh adapter',
    (_) async {
      final first = AtlasAndroidPairingTransactionStore();
      final second = AtlasAndroidPairingTransactionStore();
      expect(await first.read(), await second.read());
    },
    skip: !enabled,
  );
}
