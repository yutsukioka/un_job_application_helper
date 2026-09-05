import 'dart:io';
import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_apple.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('C29 native encrypted storage lifecycle', (tester) async {
    expect(Platform.isMacOS || Platform.isIOS || Platform.isWindows, isTrue);
    final id = 'c29-native-$pid-${DateTime.now().microsecondsSinceEpoch}';
    final AtlasVaultSecureKeyStore keys = Platform.isWindows
        ? AtlasWindowsVaultSecureKeyStore()
        : AtlasAppleVaultSecureKeyStore();
    final AtlasVaultLocalStoreIO files = Platform.isWindows
        ? AtlasWindowsVaultLocalStoreIO()
        : AtlasAppleVaultLocalStoreIO();
    final key = Uint8List.fromList(List.generate(32, (i) => i + 11));
    var keyCreated = false, storeCreated = false;
    try {
      expect(await keys.containsVaultKey(id), isFalse);
      await keys.createVaultKey(id, key);
      keyCreated = true;
      final loaded = await keys.loadVaultKey(id);
      expect(
        loaded != null &&
            loaded.length == 32 &&
            List.generate(32, (i) => i).every((i) => loaded[i] == key[i]),
        isTrue,
      );
      loaded?.fillRange(0, loaded.length, 0);
      final template = vault.AtlasVaultEncryptedRecord.fromJson({
        'id': '20000000-0000-4000-8000-000000000071',
        'schema_version': 1,
        'revision': '20000000-0000-4000-8000-000000000072',
        'parent_revision': null,
        'deleted': false,
        'key_id': 'synthetic-c29',
        'nonce': base64Encode(Uint8List(12)),
        'ciphertext': base64Encode(Uint8List(16)),
      });
      final synthetic = Uint8List.fromList(List.generate(64, (i) => i + 32));
      final encrypted = await vault.sealAtlasVaultRecord(
        plaintext: synthetic,
        vaultKey: key,
        vaultId: id,
        record: template,
      );
      final store = vault.AtlasVaultLocalStore.fromJson({
        'format': 'atlasvault-local-store',
        'version': 1,
        'store_id': '20000000-0000-4000-8000-000000000029',
        'created_at': '2026-09-05T00:00:00Z',
        'updated_at': '2026-09-05T00:00:00Z',
        'vault_metadata': {
          'format': 'atlas-vault',
          'version': 1,
          'vault_id': id,
          'crypto': {
            'record_aead': 'AES-256-GCM',
            'kdf': 'Argon2id',
            'subkey_kdf': 'HKDF-SHA256',
            'key_wrap_aead': 'AES-256-GCM',
          },
          'key_wraps': <Object>[],
        },
        'records': [encrypted.toJson()],
      });
      await files.create(id, store);
      storeCreated = true;
      final restored = await files.read(id);
      expect(restored != null && restored.vaultMetadata.vaultId == id, isTrue);
      final opened = await vault.openAtlasVaultRecord(
        record: restored!.records.single,
        vaultKey: key,
        vaultId: id,
      );
      expect(
        opened.length == synthetic.length &&
            List.generate(
              synthetic.length,
              (i) => i,
            ).every((i) => opened[i] == synthetic[i]),
        isTrue,
      );
      opened.fillRange(0, opened.length, 0);
      synthetic.fillRange(0, synthetic.length, 0);
      await files.replace(
        id,
        store,
        expectedSha256: await vault.atlasVaultSha256Hex(store.canonicalBytes()),
      );
      await keys.deleteVaultKey(id);
      expect(await keys.loadVaultKey(id) == null, isTrue);
      await keys.deleteVaultKey(id);
      await files.delete(id);
      expect(await files.read(id) == null, isTrue);
      await files.delete(id);
    } finally {
      key.fillRange(0, key.length, 0);
      if (storeCreated) await files.delete(id);
      if (keyCreated) await keys.deleteVaultKey(id);
    }
  });
}
