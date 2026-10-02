import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:atlas/src/atlas_vault/trusted_devices.dart';

import 'atlas_vault_runtime_fixtures.dart';
import 'atlas_vault_vector_loader.dart';

Map<String, Object?> productionEnrollmentFixture() =>
    loadAtlasVaultVector('atlasvault_production_enrollment_v1.json');

Future<AtlasVaultEpochVault> productionEnrollmentSender(
  Directory directory,
) async {
  final v = productionEnrollmentFixture(), f = RuntimeFixture();
  final view = runtimeObject(v['initial_view']);
  final proof = runtimeObject(runtimeObject(v['record'])['proof']);
  final h = AtlasVaultGuardedSyncState(
    file: File('${directory.path}/initial-history'),
    encryptionKey: runtimeTestKey(60),
    accountId: view['account_id']! as String,
    vaultId: RuntimeFixture.vaultID,
    collectionId: RuntimeFixture.collectionID,
    keyEpoch: 3,
    trustedSigner: Uint8List.fromList(
      (await (await f.signer()).extractPublicKey()).bytes,
    ),
  );
  await h.initialize();
  final bytes = base64Decode(v['opaque_state_b64']! as String);
  await h.ingest(
    view,
    runtimeRows(v['initial_registry']),
    runtimeObject(v['initial_collection']),
    bytes,
  );
  final owner = AtlasVaultEpochVault(
    directory,
    storageKey: runtimeTestKey(50),
    deviceID: (v['device_ids']! as List).first as String,
    registry: runtimeRows(proof['registry']),
    accountID: view['account_id']! as String,
    vaultID: RuntimeFixture.vaultID,
    keyEpoch: 3,
    stateRoot: view['root']! as String,
  );
  await owner.initialize({3: runtimeTestKey(30)}, history: h);
  await owner.ingestRuntimePage(
    view: view,
    registry: runtimeRows(v['initial_registry']),
    collection: runtimeObject(v['initial_collection']),
    opaqueState: bytes,
    operations: runtimeRows(v['operations']),
  );
  await owner.acceptRotation(
    proof,
    acceptedRecord: runtimeObject(v['record']),
    agreementPrivateKey: runtimeTestKey(20),
  );
  return owner;
}

AtlasVaultTrustedDeviceRegistry productionEnrollmentDescriptors() {
  final v = productionEnrollmentFixture();
  final descriptors = runtimeRows(v['signed_descriptors']);
  final rows =
      [
        for (final entry in descriptors.skip(1))
          <String, Object?>{
            'peer_device_id': runtimeObject(entry['descriptor'])['device_id'],
            'peer_descriptor': entry,
            'pairing_transcript_sha256': '12' * 32,
            'linked_at': '2026-01-01T00:00:00Z',
            'role': 'inviter',
            'vault_id': RuntimeFixture.vaultID,
            'key_epoch': 3,
            'delivery_id': '20000000-0000-4000-8000-000000000001',
            'acknowledgement_sha256': '23' * 32,
          },
      ]..sort(
        (a, b) => (a['peer_device_id']! as String).compareTo(
          b['peer_device_id']! as String,
        ),
      );
  return AtlasVaultTrustedDeviceRegistry.fromJson({
    'format': 'atlasvault-trusted-device-registry',
    'version': 1,
    'local_device_id': (v['device_ids']! as List).first,
    'revision': '20000000-0000-4000-8000-000000000001',
    'parent_revision': null,
    'created_at': '2026-01-01T00:00:00Z',
    'updated_at': '2026-01-01T00:00:00Z',
    'devices': rows,
  });
}
