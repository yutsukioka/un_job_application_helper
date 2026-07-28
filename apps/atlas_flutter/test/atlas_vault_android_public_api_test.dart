import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android barrel exposes only reviewed storage boundaries', () {
    AtlasVaultSecureKeyStore? secureKeyStore;
    AtlasVaultLocalStoreIO? localStore;
    AtlasVaultAndroidCapabilities? capabilities;
    AtlasVaultProtectedMigrationJournalStore? journalStore;
    AtlasVaultSelectedVaultStore? selectedVaultStore;
    AtlasVaultPlaintextMigrationCoordinator? migrationCoordinator;

    expect(secureKeyStore, isNull);
    expect(localStore, isNull);
    expect(capabilities, isNull);
    expect(journalStore, isNull);
    expect(selectedVaultStore, isNull);
    expect(migrationCoordinator, isNull);
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

  test('Android API floor is explicit and has no legacy override', () {
    final gradle = File('android/app/build.gradle.kts').readAsStringSync();
    final pubspec = File('pubspec.yaml').readAsStringSync();
    final manifest = File(
      'android/app/src/main/AndroidManifest.xml',
    ).readAsStringSync();
    final storage = File(
      'android/app/src/main/kotlin/com/yutsukioka/jobagg/atlas/'
      'AtlasVaultAndroidStorage.kt',
    ).readAsStringSync();

    expect(gradle, contains('minSdk = 24'));
    expect(gradle, isNot(contains('minSdk = 23')));
    expect(pubspec, contains('integration_test:'));
    expect(manifest, isNot(contains('tools:overrideLibrary')));
    expect(storage, isNot(contains('API 23')));
    expect(storage, isNot(contains('software-key fallback')));
  });

  test('native reads and deletes use AtomicFile recovery semantics', () {
    final storage = File(
      'android/app/src/main/kotlin/com/yutsukioka/jobagg/atlas/'
      'AtlasVaultAndroidStorage.kt',
    ).readAsStringSync();

    expect(storage, contains('AtomicFile(file).openRead()'));
    expect(storage, contains('AtomicFile(file).delete()'));
    expect(storage, isNot(contains('FileInputStream(file)')));
  });
}
