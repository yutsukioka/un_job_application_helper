import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter/services.dart';
import 'package:atlas/src/atlas_vault/android_storage.dart';
import 'package:atlas/src/atlas_vault/windows_storage.dart';
import 'package:atlas/src/atlas_vault/plaintext_migration.dart';
import 'package:atlas/src/atlas_vault/epoch_secure_cleanup.dart';

Map<String, Object?> m(Object? v) => Map<String, Object?>.from(v as Map);
List<Map<String, Object?>> rows(Object? v) => (v as List).map(m).toList();

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
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
  test('C27 cleanup persists its intent and calls platform deletion boundaries',() async {
    for(final platform in ['android','windows']) {
      final root=await Directory.systemTemp.createTemp('atlas-c27-delete-');
      final channel=MethodChannel('atlas-c27-$platform');
      final ids={3:'10000000-0000-4000-8000-000000000003',4:'10000000-0000-4000-8000-000000000004',5:'10000000-0000-4000-8000-000000000005'};
      final present=ids.values.toSet(),deleted=<String>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call) async {
        final id=(call.arguments as Map)['vault_id'] as String;
        if(call.method=='deleteVaultKey'){deleted.add(id);present.remove(id);return null;}
        if(call.method=='loadVaultKey') return present.contains(id)?Uint8List.fromList(List.filled(32,7)):null;
        if(call.method=='containsVaultKey') return present.contains(id);
        throw StateError('unexpected method');
      });
      try {
        final c=await device(root,2,initialize:true);
        await c.catchUp(rows((v['packets'] as List)[2]),currentActivationID:v['target_activation_id'] as String,agreementPrivateKey:Uint8List.fromList(List.filled(32,22)));
        await expectLater(c.cleanupEpochsForTesting(retainEpochs:{5},deleteEpoch:(_) async {},containsEpoch:(_) async=>false,checkpoint:(stage){if(stage=='cleanup_pending') throw StateError('synthetic interruption');}),throwsA(anything));
        final reopened=await device(root,2);
        expect((await reopened.recovery())['status'],'CLEANUP_PENDING');
        await expectLater(reopened.cleanupEpochs(retainEpochs:{4,5},deleteEpoch:(_) async {},containsEpoch:(_) async=>false),throwsA(anything));
        final AtlasVaultMigrationSecureKeyStore store=platform=='android'?AtlasAndroidVaultSecureKeyStore(channel:channel):AtlasWindowsVaultSecureKeyStore(channel:channel);
        await reopened.cleanupSecureStorage(retainEpochs:{5},epochStorageIDs:ids,store:store);
        expect(deleted, [ids[3],ids[4]]);
        expect(await store.loadVaultKey(ids[3]!),isNull);
        expect(await store.loadVaultKey(ids[4]!),isNull);
        expect(await reopened.availableEpochs(),[5]);
      } finally {
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,null);
        await root.delete(recursive:true);
      }
    }
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
