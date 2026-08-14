import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/support/atlas_vault_vector_loader.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Android device identity persists across fresh processes', (
    tester,
  ) async {
    if (!Platform.isAndroid) {
      return;
    }
    final stage = Platform.environment['ATLAS_DEVICE_IDENTITY_TEST_STAGE'];
    if (stage != 'prepare' && stage != 'verify') {
      fail('Android device identity integration environment is invalid.');
    }

    final vector = loadAtlasVaultVector(
      'atlasvault_device_identity_pairing_vectors_v1.json',
    );
    final bundle = loadAtlasVaultDeviceIdentitySecretBytes(vector, 'device_a');
    final store = AtlasAndroidDeviceIdentitySecretStore();

    try {
      if (stage == 'prepare') {
        if (await store.containsPrimaryIdentity()) {
          fail(
            'A primary device identity already exists; no data was changed.',
          );
        }
        final capabilities = await AtlasAndroidVaultSecureKeyStore()
            .capabilities();
        await store.createPrimaryIdentity(bundle);
        await expectLater(
          store.createPrimaryIdentity(bundle),
          throwsA(isA<AtlasVaultAndroidStorageException>()),
        );
        expect(await store.loadPrimaryIdentity(), bundle);
        tester.printToConsole(
          'Android identity prepare passed: '
          'api=${capabilities.apiLevel}, '
          'hardwareBacked=${capabilities.hardwareBacked}, '
          'strongBoxBacked=${capabilities.strongBoxBacked}',
        );
        return;
      }

      final loaded = await store.loadPrimaryIdentity();
      expect(loaded, isNotNull);
      expect(loaded, bundle);
      loaded![0] ^= 1;
      expect(await store.loadPrimaryIdentity(), bundle);

      final secret = AtlasVaultDeviceIdentitySecret.fromJson(
        atlasVaultObject(jsonDecode(utf8.decode(bundle))),
      );
      final identity = await secret.loadIdentity();
      final signed = await identity.signDescriptor();
      expect(
        (await verifyAtlasVaultSignedDeviceDescriptor(signed)).deviceId,
        identity.deviceId,
      );
      final peer = atlasVaultObject(vector['device_b']);
      final shared = await identity.sharedSecretFor(
        Uint8List.fromList(
          base64Decode(peer['agreement_public_key']! as String),
        ),
      );
      expect(
        shared,
        base64Decode(
          atlasVaultObject(vector['pairing'])['x25519_shared_secret']!
              as String,
        ),
      );
      shared.fillRange(0, shared.length, 0);
      identity.destroy();
      secret.destroy();

      await store.deletePrimaryIdentity();
      await store.deletePrimaryIdentity();
      expect(await store.containsPrimaryIdentity(), isFalse);
      expect(await store.loadPrimaryIdentity(), isNull);
      tester.printToConsole('Android identity verify and cleanup passed.');
    } finally {
      bundle.fillRange(0, bundle.length, 0);
    }
  });
}
