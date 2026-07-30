import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'Android installs an Apple-origin export through protected resources',
    (tester) async {
      final journal = AtlasAndroidProtectedRecoveryImportJournalStore();
      final selected = AtlasAndroidSelectedVaultStore();

      expect(await journal.read(), isNull);
      expect(await selected.read(), isNull);

      final canonicalJournal = Uint8List.fromList(
        utf8.encode(
          '{"created_at":"2026-07-29T03:04:05Z",'
          '"export_id":"10000000-0000-4000-8000-000000000101",'
          '"export_sha256":"${'a' * 64}",'
          '"format":"atlasvault-android-recovery-import",'
          '"import_id":"30000000-0000-4000-8000-000000000301",'
          '"local_store_sha256":"${'b' * 64}",'
          '"stage":"prepared",'
          '"store_id":"30000000-0000-4000-8000-000000000302",'
          '"vault_id":"10000000-0000-4000-8000-000000000100",'
          '"vault_key_sha256":"${'c' * 64}",'
          '"version":1}',
        ),
      );

      await journal.create(canonicalJournal);
      final protected = await journal.read();
      expect(protected, orderedEquals(canonicalJournal));
      tester.printToConsole(
        'AtlasVault Android recovery-import protected journal is available.',
      );
    },
  );
}
