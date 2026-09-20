import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Windows DPAPI and atomic store persist across processes', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    final stage = Platform.environment['ATLAS_WINDOWS_STORAGE_TEST_STAGE'];
    final vaultId = Platform.environment['ATLAS_WINDOWS_STORAGE_TEST_VAULT_ID'];
    final expectTamper =
        Platform.environment['ATLAS_WINDOWS_STORAGE_TEST_EXPECT_TAMPER'] ==
        'true';
    if ((stage != 'prepare' && stage != 'verify') ||
        vaultId == null ||
        vaultId.isEmpty) {
      fail('Windows storage integration environment is invalid.');
    }

    final keyStore = AtlasWindowsVaultSecureKeyStore();
    final localStore = AtlasWindowsVaultLocalStoreIO();
    final key = Uint8List.fromList(
      List<int>.generate(32, (index) => (index + 109) & 0xff),
    );
    try {
      if (stage == 'prepare') {
        final capabilities = await keyStore.capabilities();
        expect(capabilities.secureBoundaryAvailable, isTrue);
        expect(capabilities.dpapiAvailable, isTrue);
        expect(capabilities.currentUserScope, isTrue);
        expect(capabilities.localAppDataAvailable, isTrue);
        expect(capabilities.atomicReplaceAvailable, isTrue);
        expect(capabilities.hardwareBackedGuaranteed, isFalse);

        await keyStore.createVaultKey(vaultId, key);
        await expectLater(
          keyStore.createVaultKey(vaultId, key),
          throwsA(isA<AtlasVaultWindowsStorageException>()),
        );
        final store = _emptyStore(vaultId, updatedAt: '2026-07-31T00:00:00Z');
        await localStore.create(vaultId, store);
        await expectLater(
          localStore.create(vaultId, store),
          throwsA(isA<AtlasVaultWindowsStorageException>()),
        );
        tester.printToConsole('Windows AtlasVault storage prepare passed.');
        return;
      }

      if (expectTamper) {
        await expectLater(
          keyStore.loadVaultKey(vaultId),
          throwsA(isA<AtlasVaultWindowsStorageException>()),
        );
        await localStore.delete(vaultId);
        await keyStore.deleteVaultKey(vaultId);
        tester.printToConsole('Windows AtlasVault key tamper rejected.');
        return;
      }

      expect(await keyStore.loadVaultKey(vaultId), orderedEquals(key));
      expect(await keyStore.loadVaultKey('${vaultId}_other'), isNull);
      final current = await localStore.read(vaultId);
      expect(current, isNotNull);
      final digest = await atlasVaultSha256Hex(current!.canonicalBytes());
      final replacement = _emptyStore(
        vaultId,
        updatedAt: '2026-07-31T00:00:01Z',
      );
      await localStore.replace(vaultId, replacement, expectedSha256: digest);
      await expectLater(
        localStore.replace(vaultId, current, expectedSha256: digest),
        throwsA(isA<AtlasVaultWindowsStorageException>()),
      );
      expect(await localStore.read(vaultId), replacement);
      await localStore.delete(vaultId);
      await keyStore.deleteVaultKey(vaultId);
      expect(await localStore.read(vaultId), isNull);
      expect(await keyStore.containsVaultKey(vaultId), isFalse);
      tester.printToConsole('Windows AtlasVault storage verify passed.');
    } finally {
      key.fillRange(0, key.length, 0);
    }
  });
}

AtlasVaultLocalStore _emptyStore(String vaultId, {required String updatedAt}) {
  return AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': 'atlasvault-local-store',
    'version': 1,
    'store_id': '77777777-8888-4999-8aaa-bbbbbbbbbbbb',
    'created_at': '2026-07-31T00:00:00Z',
    'updated_at': updatedAt,
    'vault_metadata': <String, Object?>{
      'format': 'atlas-vault',
      'version': 1,
      'vault_id': vaultId,
      'crypto': <String, Object?>{
        'record_aead': 'AES-256-GCM',
        'kdf': 'Argon2id',
        'subkey_kdf': 'HKDF-SHA256',
        'key_wrap_aead': 'AES-256-GCM',
      },
      'key_wraps': <Object?>[],
    },
    'records': <Object?>[],
  });
}
