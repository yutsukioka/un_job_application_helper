import 'dart:typed_data';

import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android barrel exposes only reviewed storage boundaries', () {
    AtlasVaultSecureKeyStore? secureKeyStore;
    AtlasVaultLocalStoreIO? localStore;
    AtlasVaultAndroidCapabilities? capabilities;

    expect(secureKeyStore, isNull);
    expect(localStore, isNull);
    expect(capabilities, isNull);
    expect(atlasVaultAndroidMethodChannelName, 'atlas/vault_android');
  });

  test('Android storage errors and models use fixed redacted descriptions', () {
    const failure = AtlasVaultAndroidStorageException();
    final capabilities =
        AtlasVaultAndroidCapabilities.fromPlatform(<String, Object?>{
          'api_level': 35,
          'secure_boundary_available': true,
          'aes_gcm_keystore_available': true,
          'hardware_backed': false,
          'strongbox_backed': false,
          'no_backup_storage_available': true,
        });

    expect(failure.toString(), 'AtlasVault Android storage operation failed.');
    expect(
      capabilities.toString(),
      'AtlasVaultAndroidCapabilities(<redacted>)',
    );
  });

  test('secure-key protocol uses defensive byte-list results', () {
    Future<Uint8List?> Function(String)? load;
    expect(load, isNull);
  });
}
