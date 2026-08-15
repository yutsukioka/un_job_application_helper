import 'dart:convert';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> root;

  setUpAll(() {
    root = loadAtlasVaultVector(
      'atlasvault_trusted_pairing_delivery_vectors_v1.json',
    );
  });

  test('trusted registry and replay bytes match the shared vector', () {
    final registry = AtlasVaultTrustedDeviceRegistry.fromJson(
      atlasVaultObject(root['trusted_registry']),
    );
    final replay = AtlasVaultPairingReplayStore.fromJson(
      atlasVaultObject(root['replay_store']),
    );

    expect(
      registry.canonicalBytes(),
      base64Decode(root['trusted_registry_canonical_b64']! as String),
    );
    expect(
      replay.canonicalBytes(),
      base64Decode(root['replay_store_canonical_b64']! as String),
    );
  });

  test('registry descriptor signatures are verified on trusted load', () async {
    final encoded = base64Decode(
      root['trusted_registry_canonical_b64']! as String,
    );
    final verified = await decodeAndVerifyAtlasVaultTrustedDeviceRegistry(
      encoded,
    );
    expect(verified.toJson(), root['trusted_registry']);

    final tampered = jsonDecode(jsonEncode(root['trusted_registry']));
    final devices = (tampered as Map<String, dynamic>)['devices']! as List;
    final descriptor =
        (devices.single as Map<String, dynamic>)['peer_descriptor']!
            as Map<String, dynamic>;
    final signature = base64Decode(descriptor['signature']! as String);
    signature[0] ^= 1;
    descriptor['signature'] = base64Encode(signature);

    await expectLater(
      verifyAtlasVaultTrustedDeviceRegistry(
        AtlasVaultTrustedDeviceRegistry.fromJson(
          Map<String, Object?>.from(tampered),
        ),
      ),
      throwsA(isA<AtlasVaultTrustedDeviceStateException>()),
    );
  });

  test('trust is create-only, idempotent, and conflict safe', () async {
    final empty = AtlasVaultTrustedDeviceRegistry.fromJson(
      atlasVaultObject(root['empty_trusted_registry']),
    );
    final peer = AtlasVaultTrustedDevicePeer.fromJson(
      atlasVaultObject(root['trusted_peer']),
    );
    final committed = await commitAtlasVaultTrustedDevice(
      empty,
      peer,
      revision: root['registry_commit_revision']! as String,
      updatedAt: root['registry_commit_timestamp']! as String,
    );
    expect(committed.outcome, AtlasVaultTrustedDeviceCommitOutcome.committed);
    expect(committed.registry.toJson(), root['trusted_registry']);

    final duplicate = await commitAtlasVaultTrustedDevice(
      committed.registry,
      peer,
      revision: root['unused_revision']! as String,
      updatedAt: root['later_timestamp']! as String,
    );
    expect(
      duplicate.outcome,
      AtlasVaultTrustedDeviceCommitOutcome.alreadyTrusted,
    );
    expect(
      duplicate.registry.canonicalBytes(),
      committed.registry.canonicalBytes(),
    );
  });

  test('replay conflicts fail with redacted errors', () {
    final empty = AtlasVaultPairingReplayStore.fromJson(
      atlasVaultObject(root['empty_replay_store']),
    );
    final entry = AtlasVaultPairingReplayEntry.fromJson(
      atlasVaultObject(root['offer_replay_entry']),
    );
    final consumed = consumeAtlasVaultPairingReplay(
      empty,
      entry,
      revision: root['replay_commit_revision']! as String,
      updatedAt: root['replay_commit_timestamp']! as String,
      currentTime: root['verification_time']! as String,
    );
    expect(consumed.outcome, AtlasVaultReplayConsumeOutcome.consumed);

    final conflict = Map<String, Object?>.from(entry.toJson())
      ..['transcript_sha256'] = List<String>.filled(32, '00').join();
    expect(
      () => consumeAtlasVaultPairingReplay(
        consumed.store,
        AtlasVaultPairingReplayEntry.fromJson(conflict),
        revision: root['unused_revision']! as String,
        updatedAt: root['later_timestamp']! as String,
        currentTime: root['verification_time']! as String,
      ),
      throwsA(
        isA<AtlasVaultTrustedDeviceStateException>().having(
          (error) => error.toString(),
          'redacted',
          isNot(contains(root['offer_replay_entry'].toString())),
        ),
      ),
    );
  });
}
