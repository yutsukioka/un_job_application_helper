import 'dart:io';

import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Windows exposes the protected recovery import journal', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    final value = await const MethodChannel(
      atlasVaultWindowsMethodChannelName,
    ).invokeMethod<Object?>('readRecoveryImportJournal');
    expect(value, isNull);
    tester.printToConsole(
      'Windows AtlasVault protected recovery-import journal is available.',
    );
  });
}
