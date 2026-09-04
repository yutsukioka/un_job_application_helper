import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/epoch_rotation.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';
import 'support/atlas_vault_dart_helper_process.dart';

void main() {
  final vectors =
      jsonDecode(
            File(
              '../../contracts/sync/test_vectors/atlasvault_epoch_rotation_v1.json',
            ).readAsStringSync(),
          )
          as Map<String, dynamic>;
  final proof = Map<String, Object?>.from(vectors['proof'] as Map);
  test(
    'independent epoch generations and revoked reconnect survive reopen',
    () async {
      final root = await Directory.systemTemp.createTemp('atlas-c26-');
      final activation = Map<String, Object?>.from(
        jsonDecode(
              File(
                '../../contracts/sync/test_vectors/atlasvault_activation_v1.json',
              ).readAsStringSync(),
            )
            as Map,
      );
      final record = Map<String, Object?>.from(activation['record'] as Map),
          proof = Map<String, Object?>.from(
            (activation['record'] as Map)['proof'] as Map,
          );
      AtlasVaultEpochVault device(int i) => AtlasVaultEpochVault(
        Directory('${root.path}/$i'),
        storageKey: Uint8List.fromList(List.filled(32, 50 + i)),
        deviceID: (activation['device_ids'] as List)[i] as String,
        registry: (proof['registry'] as List)
            .map((e) => Map<String, Object?>.from(e as Map))
            .toList(),
        accountID: (proof['plan'] as Map)['account_id'] as String,
        vaultID: 'vault-c26',
        keyEpoch: 3,
        stateRoot: (proof['plan'] as Map)['state_root'] as String,
      );
      try {
        final clients = [for (var i = 0; i < 3; i++) device(i)];
        for (var i = 0; i < 3; i++) {
          final signer = await Ed25519().newKeyPairFromSeed(
            List.filled(32, 10),
          );
          final history = AtlasVaultGuardedSyncState(
            file: File('${root.path}/history-$i'),
            encryptionKey: Uint8List.fromList(List.filled(32, 60 + i)),
            accountId: (proof['plan'] as Map)['account_id'] as String,
            vaultId: 'vault-c26',
            collectionId: 'collection-c26',
            keyEpoch: 3,
            trustedSigner: Uint8List.fromList(
              (await signer.extractPublicKey()).bytes,
            ),
          );
          await history.initialize();
          await history.ingest(
            Map<String, Object?>.from(activation['initial_view'] as Map),
            (activation['initial_registry'] as List)
                .map((e) => Map<String, Object?>.from(e as Map))
                .toList(),
            Map<String, Object?>.from(activation['initial_collection'] as Map),
            base64Decode(activation['opaque_state_b64'] as String),
          );
          await clients[i].initialize({
            3: Uint8List.fromList(List.filled(32, 30)),
          }, history: history);
        }
        for (var i = 0; i < 2; i++) {
          await clients[i].beginActivation(proof);
          expect(
            (await device(i).observation())['status'],
            'ACTIVATION_PENDING',
          );
          expect(
            await clients[i].acceptRotation(
              proof,
              acceptedRecord: record,
              agreementPrivateKey: Uint8List.fromList(List.filled(32, 20 + i)),
            ),
            isTrue,
          );
          expect((await device(i).observation())['key_epoch'], 4);
          expect(
            (await device(i).observation())['recipient_commitment'],
            activation['recipient_commitment'],
          );
          expect(
            await clients[i].acceptRotation(
              proof,
              acceptedRecord: record,
              agreementPrivateKey: Uint8List.fromList(List.filled(32, 20 + i)),
            ),
            isFalse,
          );
        }
        expect((await clients[2].observation())['key_epoch'], 3);
        await expectLater(
          clients[2].acceptRotation(
            proof,
            acceptedRecord: record,
            agreementPrivateKey: Uint8List.fromList(List.filled(32, 22)),
          ),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        expect((await device(2).observation())['status'], 'REVOKED');
        final signing = await Ed25519().newKeyPairFromSeed(List.filled(32, 10));
        for (final kind in ['patch', 'snapshot']) {
          final sealed = await clients[0].seal(
            kind,
            Uint8List.fromList(List.filled(32, 71)),
            objectID: 'record-c26',
            revision: 'revision-$kind',
            signingKey: signing,
          );
          expect(sealed.keyEpoch, 4);
          if (kind == 'patch') {
            await clients[0].queueOperation(
              AtlasVaultEncryptedPatchOperation.fromJson({
                'format': 'atlasvault-encrypted-patch-operation',
                'version': 1,
                'operation_id': '10000000-0000-4000-8000-000000000001',
                'operation_type': 'upsert',
                'author_device_id': (activation['device_ids'] as List)[0],
                'author_sequence': 1,
                'lamport': 1,
                'envelope': sealed.toJson(),
              }),
            );
            expect(
              (await device(0).pendingOperations()).single.envelope.keyEpoch,
              4,
            );
          }
          expect(await clients[1].open(sealed), List.filled(32, 71));
          await expectLater(
            clients[2].open(sealed),
            throwsA(isA<AtlasVaultRotationException>()),
          );
        }
        for (final recipient in (proof['plan'] as Map)['recipients'] as List) {
          expect(
            (await clients[0].delivery(recipient as String))['key_epoch'],
            4,
          );
        }
        await expectLater(
          clients[0].delivery((activation['device_ids'] as List)[2] as String),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        final committed = await clients[0].createCommitment(
          base64Decode(activation['opaque_state_b64'] as String),
          signingKey: signing,
        );
        expect((committed['view'] as Map)['key_epoch'], 4);
        final first = await AtlasVaultEpochRotation.create(
          Map<String, Object?>.from(proof['revocation'] as Map),
          registry: (proof['registry'] as List)
              .map((e) => Map<String, Object?>.from(e as Map))
              .toList(),
          stateRoot: (proof['plan'] as Map)['state_root'] as String,
          signingKey: signing,
        );
        final second = await AtlasVaultEpochRotation.create(
          Map<String, Object?>.from(proof['revocation'] as Map),
          registry: (proof['registry'] as List)
              .map((e) => Map<String, Object?>.from(e as Map))
              .toList(),
          stateRoot: (proof['plan'] as Map)['state_root'] as String,
          signingKey: signing,
        );
        expect(first['root'], isNot(second['root']));
        final unsigned =
            Map<String, Object?>.from(activation['initial_view'] as Map)
              ..remove('root')
              ..remove('signature_b64');
        unsigned['collection_root'] = List.filled(32, 'de').join();
        final fork = await AtlasVaultAuthenticatedStateView.sign(
          unsigned,
          signing,
        );
        await expectLater(
          clients[0].compareEvidence([fork]),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        expect((await device(0).observation())['status'], 'RECOVERY_PENDING');
        expect((await device(0).recovery())['peer'], isNotEmpty);
        await expectLater(
          device(0).beginActivation(proof),
          throwsA(isA<AtlasVaultRotationException>()),
        );
      } finally {
        await root.delete(recursive: true);
      }
    },
  );
  for (final stage in [
    'prepared',
    'backend_accepted',
    'local_publishing',
    'before_local_commit',
    'after_local_commit',
    'missing_delivery',
  ]) {
    test(
      'D087 SIGKILL and fresh-process fence $stage',
      () async {
        final dir = await Directory.systemTemp.createTemp('atlas-c26-kill-');
        final process = await startAtlasVaultDartHelper(
          'test/support/atlas_vault_epoch_activation_process.dart',
          [dir.path, stage],
        );
        final errors = process.stderr.transform(utf8.decoder).join();
        process.stdout.drain<void>();
        try {
          final deadline = DateTime.now().add(const Duration(seconds: 30));
          while (!await File('${dir.path}/ready').exists() &&
              DateTime.now().isBefore(deadline)) {
            await Future<void>.delayed(const Duration(milliseconds: 50));
          }
          final ready = await File('${dir.path}/ready').exists();
          expect(process.kill(ProcessSignal.sigkill), true);
          await process.exitCode;
          expect(ready, true, reason: await errors);
          final reopened = await startAtlasVaultDartHelper(
            'test/support/atlas_vault_epoch_activation_process.dart',
            [dir.path, 'observe'],
          );
          final output = await reopened.stdout.transform(utf8.decoder).join();
          final error = await reopened.stderr.transform(utf8.decoder).join();
          expect(await reopened.exitCode, 0, reason: error);
          final observation = jsonDecode(output) as Map;
          final complete = stage == 'after_local_commit',
              pending = stage != 'prepared' && !complete;
          expect(observation['key_epoch'], complete ? 4 : 3);
          expect(
            observation['status'],
            pending ? 'ACTIVATION_PENDING' : 'ACTIVE',
          );
          expect(observation['writes_fenced'], pending);
          // Only same-activation completion is exercised, never missed-epoch catch-up.
          if (pending && stage != 'missing_delivery') {
            final resumed = await startAtlasVaultDartHelper(
              'test/support/atlas_vault_epoch_activation_process.dart',
              [dir.path, 'resume'],
            );
            final result = await resumed.stdout.transform(utf8.decoder).join();
            final error = await resumed.stderr.transform(utf8.decoder).join();
            expect(await resumed.exitCode, 0, reason: error);
            expect((jsonDecode(result) as Map)['key_epoch'], 4);
          }
          stdout.writeln(
            'C26 D087 Dart SIGKILL stage=$stage pid=${process.pid} status=${observation['status']} epoch=${observation['key_epoch']}',
          );
        } finally {
          process.kill(ProcessSignal.sigkill);
          await process.exitCode;
          await dir.delete(recursive: true);
        }
      },
      timeout: const Timeout(Duration(minutes: 2)),
    );
  }
  Future<Map<String, Object?>> check(Map<String, Object?> value) =>
      AtlasVaultEpochRotation.verify(
        value,
        registry: (proof['registry'] as List)
            .map((e) => Map<String, Object?>.from(e as Map))
            .toList(),
        accountID: (proof['plan'] as Map)['account_id'] as String,
        vaultID: 'vault-c26',
        previousEpoch: 3,
        stateRoot: List.filled(32, 'ab').join(),
      );
  test('shared signed epoch binding and active recipients', () async {
    final result = await check(proof);
    expect(result['new_epoch'], 4);
    expect(result['binding_root'], vectors['binding_root']);
    expect(
      result['recipients'],
      isNot(contains((vectors['device_ids'] as List)[2])),
    );
  });
  for (final field in (proof['plan'] as Map).keys) {
    test('reject substituted epoch field $field', () async {
      final changed = Map<String, Object?>.from(
        jsonDecode(jsonEncode(proof)) as Map,
      );
      final plan = changed['plan'] as Map;
      final old = plan[field];
      plan[field] = old is int
          ? old + 1
          : old is List
          ? <Object?>[]
          : 'substituted';
      await expectLater(
        check(changed),
        throwsA(isA<AtlasVaultRotationException>()),
      );
    });
  }
}
