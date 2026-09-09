import 'dart:convert';
import 'dart:io';

import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_runtime_fixtures.dart';
import 'support/atlas_vault_vector_loader.dart';

void main() {
  final cases = runtimeRows(
    loadAtlasVaultVector(
      'atlasvault_bootstrap_authority_diagnostic.json',
    )['cases'],
  );
  for (var i = 0; i < cases.length; i++) {
    test('C30 retained current-view author authority case $i', () async {
      final v = cases[i], c = runtimeObject(v['checkpoint']);
      final root = await Directory.systemTemp.createTemp(
        'atlas-c30-authority-',
      );
      addTearDown(() => root.delete(recursive: true));
      final trust = <String, Object?>{
        for (final key in [
          'account_id',
          'vault_id',
          'collection_id',
          'key_epoch',
        ])
          key: c[key],
        for (final key in [
          'trusted_signer_b64',
          'registry',
          'recipient_device_id',
          'confirmed_transcript',
          'current_context',
        ])
          key: v[key],
        'anchor_root': c['root'],
      };
      final history = AtlasVaultAnchoredSyncState(
        file: File('${root.path}/history'),
        encryptionKey: runtimeTestKey(96),
        trust: trust,
      );
      final args = <String, Object?>{
        for (final key in [
          'checkpoint',
          'enrollment',
          'registry',
          'current_context',
          'recipient_device_id',
          'confirmed_transcript',
          'view',
          'collection',
          'opaque_b64',
        ])
          key: v[key],
      };
      expect(await history.bootstrap(args), isTrue);
      final origin = await history.publicationOrigin();
      final owner = AtlasVaultEpochVault(
        Directory('${root.path}/owner'),
        storageKey: runtimeTestKey(97),
        deviceID: v['recipient_device_id']! as String,
        registry: runtimeRows(v['registry']),
        accountID: c['account_id']! as String,
        vaultID: c['vault_id']! as String,
        keyEpoch: 4,
        stateRoot: c['state_root']! as String,
        historyOrigin: origin,
      );
      await owner.initialize({
        3: runtimeTestKey(30),
        4: runtimeTestKey(98),
      }, history: history);
      final before = await owner.observation();
      expect(before['state_root'], c['state_root']);
      final opened = await owner.open(
        AtlasVaultOpaqueCiphertextEnvelope.fromJson(
          runtimeObject(v['ciphertext']),
        ),
      );
      try {
        expect(utf8.decode(opened) == 'c30-synthetic-current-view', isTrue);
      } finally {
        opened.fillRange(0, opened.length, 0);
      }
      expect(await owner.observation(), before);
    });
  }
}
