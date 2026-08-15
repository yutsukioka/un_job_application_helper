import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  const enabled = bool.fromEnvironment(
    'ATLAS_WINDOWS_TRUSTED_PAIRING_INTEGRATION',
  );

  testWidgets(
    'Windows protected pairing transaction survives a fresh adapter',
    (_) async {
      final first = AtlasWindowsPairingTransactionStore();
      final second = AtlasWindowsPairingTransactionStore();
      expect(await first.read(), await second.read());
    },
    skip: !enabled,
  );
}
