import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Android Keystore and atomic store round trip', (tester) async {
    final suffix = DateTime.now().microsecondsSinceEpoch;
    final vaultId = 'integration-vault-$suffix';
    final otherVaultId = 'integration-other-$suffix';
    final keyStore = AtlasAndroidVaultSecureKeyStore();
    final localStore = AtlasAndroidVaultLocalStoreIO();
    final key = Uint8List.fromList(
      List<int>.generate(32, (index) => (index + 17) & 0xff),
    );

    addTearDown(() async {
      await localStore.delete(vaultId);
      await keyStore.deleteVaultKey(vaultId);
      key.fillRange(0, key.length, 0);
    });

    final capabilities = await keyStore.capabilities();
    expect(capabilities.secureBoundaryAvailable, isTrue);
    expect(capabilities.aesGcmKeystoreAvailable, isTrue);
    expect(capabilities.noBackupStorageAvailable, isTrue);
    expect(capabilities.apiLevel, greaterThanOrEqualTo(23));

    await keyStore.createVaultKey(vaultId, key);
    await expectLater(
      keyStore.createVaultKey(vaultId, key),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );
    expect(await keyStore.containsVaultKey(vaultId), isTrue);
    expect(await keyStore.loadVaultKey(vaultId), orderedEquals(key));
    expect(await keyStore.loadVaultKey(otherVaultId), isNull);

    final original = _emptyStore(vaultId, updatedAt: '2026-07-28T00:00:00Z');
    await localStore.create(vaultId, original);
    await expectLater(
      localStore.create(vaultId, original),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );
    expect(await localStore.read(vaultId), original);

    final originalDigest = await atlasVaultSha256Hex(original.canonicalBytes());
    final replacement = _emptyStore(vaultId, updatedAt: '2026-07-28T00:00:01Z');
    await localStore.replace(
      vaultId,
      replacement,
      expectedSha256: originalDigest,
    );
    expect(await localStore.read(vaultId), replacement);
    await expectLater(
      localStore.replace(vaultId, original, expectedSha256: originalDigest),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );

    final encoded = String.fromCharCodes(replacement.canonicalBytes());
    expect(encoded, isNot(contains('FAKE_PRIVATE_ANDROID_SENTINEL')));

    await localStore.delete(vaultId);
    await localStore.delete(vaultId);
    expect(await localStore.read(vaultId), isNull);
    await keyStore.deleteVaultKey(vaultId);
    await keyStore.deleteVaultKey(vaultId);
    expect(await keyStore.containsVaultKey(vaultId), isFalse);
    expect(await keyStore.loadVaultKey(vaultId), isNull);
  });
}

AtlasVaultLocalStore _emptyStore(String vaultId, {required String updatedAt}) {
  return AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': 'atlasvault-local-store',
    'version': 1,
    'store_id': '99999999-8888-4777-8666-555555555555',
    'created_at': '2026-07-28T00:00:00Z',
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
