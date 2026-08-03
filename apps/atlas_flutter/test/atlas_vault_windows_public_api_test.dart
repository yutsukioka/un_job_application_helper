import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Windows barrel exposes only reviewed storage boundaries', () {
    AtlasVaultSecureKeyStore? keyStore;
    AtlasVaultLocalStoreIO? localStore;
    AtlasVaultWindowsCapabilities? capabilities;
    AtlasVaultPrivateStatePersistence? persistence;
    Future<Uint8List?> Function(String)? load;

    expect(keyStore, isNull);
    expect(localStore, isNull);
    expect(capabilities, isNull);
    expect(persistence, isNull);
    expect(load, isNull);
    expect(atlasVaultWindowsMethodChannelName, 'atlas/vault_windows');
  });

  test('Windows errors and descriptions remain fixed and redacted', () {
    const failure = AtlasVaultWindowsStorageException();
    final capabilities =
        AtlasVaultWindowsCapabilities.fromPlatform(<String, Object?>{
          'secure_boundary_available': true,
          'dpapi_available': true,
          'current_user_scope': true,
          'local_app_data_available': true,
          'atomic_replace_available': true,
          'hardware_backed_guaranteed': false,
        });

    expect(failure.toString(), 'AtlasVault Windows storage operation failed.');
    expect(
      capabilities.toString(),
      'AtlasVaultWindowsCapabilities(<redacted>)',
    );
  });

  test('pure-Dart AtlasVault barrel remains platform neutral', () {
    final source = File('lib/atlas_vault.dart').readAsStringSync();

    expect(source, isNot(contains('atlas_vault_windows')));
    expect(source, isNot(contains('package:flutter')));
    expect(source, isNot(contains('MethodChannel')));
  });
}
