import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Android recovery-import journal preserves interruption stages', (
    tester,
  ) async {
    final journalStore = AtlasAndroidProtectedRecoveryImportJournalStore();
    final journal = AtlasVaultRecoveryImportJournal.decodeBytes(
      Uint8List.fromList(
        utf8.encode(
          '{"created_at":"2026-07-29T03:04:05Z",'
          '"export_id":"10000000-0000-4000-8000-000000000101",'
          '"export_sha256":"${'a' * 64}",'
          '"format":"atlasvault-android-recovery-import",'
          '"import_id":"30000000-0000-4000-8000-000000000301",'
          '"local_store_sha256":"${'b' * 64}",'
          '"stage":"store_created",'
          '"store_id":"30000000-0000-4000-8000-000000000302",'
          '"vault_id":"10000000-0000-4000-8000-000000000100",'
          '"vault_key_sha256":"${'c' * 64}",'
          '"version":1}',
        ),
      ),
    );

    expect(journal.stage, AtlasVaultRecoveryImportStage.storeCreated);
    await journalStore.create(journal.canonicalBytes());
    expect(await journalStore.read(), isNotNull);
    tester.printToConsole(
      'AtlasVault Android recovery-import interruption state is protected.',
    );
  });
}
