import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';

Map<String, Object?> m(Object? v) => Map<String, Object?>.from(v as Map);
List<Map<String, Object?>> rows(Object? v) => (v as List).map(m).toList();

void main() {
  final v = m(
    jsonDecode(
      File(
        '../../contracts/sync/test_vectors/atlasvault_epoch_catch_up_v2.json',
      ).readAsStringSync(),
    ),
  );
  Future<AtlasVaultEpochVault> device(
    Directory root,
    int i, {
    bool initialize = false,
    Map<String, Object?>? vector,
  }) async {
    final fixture = vector ?? v;
    final c = AtlasVaultEpochVault(
      Directory('${root.path}/$i'),
      storageKey: Uint8List.fromList(List.filled(32, 50 + i)),
      deviceID: (fixture['device_ids'] as List)[i] as String,
      registry: rows(fixture['initial_registry']),
      accountID: m(fixture['initial_view'])['account_id'] as String,
      vaultID: 'vault-c26',
      keyEpoch: 3,
      stateRoot: m(fixture['initial_view'])['root'] as String,
    );
    if (initialize) {
      final signer = await Ed25519().newKeyPairFromSeed(List.filled(32, 10));
      final h = AtlasVaultGuardedSyncState(
        file: File('${root.path}/history-$i'),
        encryptionKey: Uint8List.fromList(List.filled(32, 60 + i)),
        accountId: m(fixture['initial_view'])['account_id'] as String,
        vaultId: 'vault-c26',
        collectionId: 'collection-c26',
        keyEpoch: 3,
        trustedSigner: Uint8List.fromList(
          (await signer.extractPublicKey()).bytes,
        ),
      );
      await h.initialize();
      await h.ingest(
        m(fixture['initial_view']),
        rows(fixture['initial_history_registry']),
        m(fixture['initial_collection']),
        base64Decode(fixture['opaque_state_b64'] as String),
      );
      await c.initialize({
        3: Uint8List.fromList(List.filled(32, 30)),
      }, history: h);
    }
    return c;
  }

  test('C27 intervening authenticated history is required and preserved', () async {
    final fixture=m(jsonDecode(File('../../contracts/sync/test_vectors/atlasvault_epoch_catch_up_history_v2.json').readAsStringSync()));
    final root=await Directory.systemTemp.createTemp('atlas-c27-history-');
    try {
      final c=await device(root,2,initialize:true,vector:fixture);
      final packets=rows((fixture['packets'] as List)[2]);
      await expectLater(c.catchUp(packets,currentActivationID:fixture['target_activation_id'] as String,agreementPrivateKey:Uint8List.fromList(List.filled(32,22))),throwsA(anything));
      expect((await c.observation())['sequence'],1);
      expect(await c.catchUp(packets,historyUpdates:rows(fixture['history_updates']),currentActivationID:fixture['target_activation_id'] as String,agreementPrivateKey:Uint8List.fromList(List.filled(32,22))),isTrue);
      expect((await c.observation())['sequence'],2);
      expect((await c.observation())['state_root'],m(rows(fixture['history_updates'])[0]['view'])['root']);
    } finally {await root.delete(recursive:true);}
  });

  test(
    'C27 independent multi-epoch catch-up and recovery publication',
    () async {
      final root = await Directory.systemTemp.createTemp('atlas-c27-');
      try {
        final roots = <String>[];
        for (var i = 0; i < 3; i++) {
          final c = await device(root, i, initialize: true),
              packets = rows((v['packets'] as List)[i]);
          expect(
            await c.catchUp(
              packets,
              currentActivationID: v['target_activation_id'] as String,
              agreementPrivateKey: Uint8List.fromList(List.filled(32, 20 + i)),
            ),
            isTrue,
          );
          expect(
            await c.catchUp(
              packets,
              currentActivationID: v['target_activation_id'] as String,
              agreementPrivateKey: Uint8List.fromList(List.filled(32, 20 + i)),
            ),
            isFalse,
          );
          final reopened = await device(root, i);
          expect((await reopened.observation())['key_epoch'], 5);
          roots.add((await reopened.observation())['registry_root'] as String);
          await File(
            '${root.path}/$i/activation',
          ).writeAsString('corrupted synthetic publication');
          await reopened.recoverPublication();
          expect((await reopened.observation())['status'], 'CATCH_UP_PENDING');
          await reopened.catchUp(packets,currentActivationID:v['target_activation_id'] as String,agreementPrivateKey:Uint8List.fromList(List.filled(32,20+i)));
          expect((await reopened.observation())['status'], 'ACTIVE');
          final removed = <int>{};
          await reopened.cleanupEpochs(
            retainEpochs: {5},
            deleteEpoch: (e) async {
              removed.add(e);
            },
            containsEpoch: (e) async => !removed.contains(e),
          );
          expect(removed, {3, 4});
          expect(await reopened.availableEpochs(), [5]);
        }
        expect(roots.toSet().length, 1);
      } finally {
        await root.delete(recursive: true);
      }
    },
  );
  for (final attack in ['missing', 'reordered', 'recipient', 'state-root']) {
    test('C27 $attack remains fenced without partially active keys', () async {
      final root = await Directory.systemTemp.createTemp('atlas-c27-');
      try {
        final c = await device(root, 2, initialize: true);
        var packets = rows(jsonDecode(jsonEncode((v['packets'] as List)[2])));
        if (attack == 'missing') packets = packets.sublist(1);
        if (attack == 'reordered') packets = packets.reversed.toList();
        if (attack == 'recipient')
          packets[1] = rows((v['packets'] as List)[0])[1];
        if (attack == 'state-root')
          (packets[0]['proof'] as Map)['plan']['state_root'] = 'ab' * 32;
        await expectLater(
          c.catchUp(
            packets,
            currentActivationID: v['target_activation_id'] as String,
            agreementPrivateKey: Uint8List.fromList(List.filled(32, 22)),
          ),
          throwsA(anything),
        );
        final observed = await (await device(root, 2)).observation();
        expect(observed['status'], 'CATCH_UP_PENDING');
        expect(observed['key_epoch'], 3);
      } finally {
        await root.delete(recursive: true);
      }
    });
  }
}
