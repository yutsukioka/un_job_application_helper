import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/src/atlas_vault/canonical_json.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_pairing_fakes.dart';
import 'support/atlas_vault_vector_loader.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final vector = loadAtlasVaultVector(
    'atlasvault_trusted_pairing_delivery_vectors_v1.json',
  );
  final fixed = {
    for (final kind in AtlasVaultPairingArtifactKind.values)
      kind: base64Decode(
        atlasVaultObject(
              atlasVaultObject(vector['artifacts'])[kind.encoded],
            )['canonical_b64']!
            as String,
      ),
  };

  test(
    'legacy fixed-vector ingress retains signed delivery verification',
    () async {
      await verifyAtlasVaultPairingArtifactSet(
        fixed,
        vector,
        runtimeEnrollment: false,
      );
    },
  );

  for (final role in AtlasVaultPairingRole.values) {
    test(
      'runtime ring verifies enrollment, HPKE and receipt as ${role.name}',
      () async {
        // With device defines, prove the whole journey and verifier work without
        // a repository filesystem, as on the Android emulator.
        const deviceVectors = String.fromEnvironment(
          'ATLAS_ACTIVATION_VECTOR_B64',
        );
        final previousDirectory = Directory.current;
        final outside = deviceVectors.isEmpty
            ? null
            : await Directory.systemTemp.createTemp('atlas-device-vectors-');
        if (outside != null) Directory.current = outside;
        try {
          final journey = await runAtlasVaultPairingPlatformJourney(
            vector: vector,
            platformRole: role,
            platformStores: AtlasVaultPairingPlatformStores(
              identity: AtlasVaultPairingMemoryIdentityStore(),
              registry: AtlasVaultPairingMemoryRegistryStore(),
              replay: AtlasVaultPairingMemoryReplayStore(),
              transaction: AtlasVaultPairingMemoryTransactionStore(),
              staging: AtlasVaultPairingMemoryStageStore(),
              secureKey: AtlasVaultPairingMemorySecureKeyStore(),
              localStore: AtlasVaultPairingMemoryLocalStore(),
              selectedVault: AtlasVaultPairingMemorySelectedVaultStore(),
            ),
          );
          await verifyAtlasVaultPairingArtifactSet(
            journey.artifacts,
            vector,
            runtimeEnrollment: true,
          );
          for (final attack in [
            'signature',
            'proof',
            'receipt',
            'canonical',
            'legacy',
          ]) {
            final artifacts =
                Map<AtlasVaultPairingArtifactKind, Uint8List>.from(
                  journey.artifacts,
                );
            final kind = attack == 'receipt'
                ? AtlasVaultPairingArtifactKind.acknowledgement
                : AtlasVaultPairingArtifactKind.delivery;
            final envelope =
                jsonDecode(utf8.decode(artifacts[kind]!))
                    as Map<String, dynamic>;
            final payload = envelope['payload'] as Map<String, dynamic>;
            switch (attack) {
              case 'signature':
                (payload['enrollment_delivery'] as Map)['signature_b64'] =
                    base64Encode(List.filled(64, 0));
              case 'proof':
                payload['inviter_proof'] = base64Encode(List.filled(32, 0));
              case 'receipt':
                (payload['enrollment_acknowledgement']
                        as Map)['delivery_sha256'] =
                    '00' * 32;
            }
            artifacts[kind] = switch (attack) {
              'canonical' => Uint8List.fromList(
                utf8.encode(' ${utf8.decode(artifacts[kind]!)}'),
              ),
              'legacy' => fixed[kind]!,
              _ => encodeCanonicalJson(envelope),
            };
            await expectLater(
              verifyAtlasVaultPairingArtifactSet(
                artifacts,
                vector,
                runtimeEnrollment: true,
              ),
              throwsA(anything),
              reason: 'runtime ring must reject $attack',
            );
          }
        } finally {
          Directory.current = previousDirectory;
          await outside?.delete();
        }
      },
    );
  }
}
