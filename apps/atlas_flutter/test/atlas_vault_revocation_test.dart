import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';

void main() {
  final v =
      jsonDecode(
            File(
              '../../contracts/sync/test_vectors/atlasvault_revocation_v1.json',
            ).readAsStringSync(),
          )
          as Map<String, dynamic>;
  List<Map<String, Object?>> entries(String name) => (v[name] as List)
      .map((e) => Map<String, Object?>.from(e as Map))
      .toList();
  Map<String, Object?> object(String name) =>
      Map<String, Object?>.from(v[name] as Map);
  final a = entries('registry')[0]['device_id']! as String;
  final b = entries('registry')[1]['device_id']! as String;
  late Directory dir;
  AtlasVaultRevocationRegistry store(String name) =>
      AtlasVaultRevocationRegistry(
        file: File('${dir.path}/$name'),
        encryptionKey: Uint8List.fromList(List.filled(32, 7)),
        accountId: 'account-c25',
        vaultId: 'vault-c25',
        keyEpoch: 3,
        registry: entries('registry'),
        stateRoot: List.filled(32, 'ab').join(),
      );
  setUp(() async {
    dir = await Directory.systemTemp.createTemp('c25-');
  });
  tearDown(() async {
    await dir.delete(recursive: true);
  });
  test(
    'shared roots, signed transition, separate persisted devices and retry',
    () async {
      expect(
        AtlasVaultRevocation.registryRoot(entries('registry')),
        object('transition')['prior_registry_root'],
      );
      expect(
        AtlasVaultRevocation.registryRoot(entries('revoked_registry')),
        object('transition')['resulting_registry_root'],
      );
      await AtlasVaultRevocation.verify(
        object('transition'),
        entries('registry'),
      );
      AtlasVaultRevocation.validateRotationPlan(
        object('rotation_plan'),
        object('transition'),
        entries('revoked_registry'),
        List.filled(32, 'ab').join(),
      );
      for (final device in ['A', 'B']) {
        final client = store(device);
        await client.initialize();
        expect(await client.commit(object('transition')), isTrue);
        expect(await store(device).commit(object('transition')), isFalse);
        final restarted = await store(device).snapshot();
        expect(restarted['registry'], entries('revoked_registry'));
        expect(restarted['status'], 'REVOCATION_PENDING');
      }
    },
  );
  for (final attack in v['attacks'] as List) {
    test('validly signed hostile ${attack['name']}', () async {
      final client = store('A');
      await client.initialize();
      if (attack['after_revocation'] == true) {
        await client.commit(object('transition'));
      }
      final before = await client.snapshot();
      await expectLater(
        client.commit(Map<String, Object?>.from(attack['transition'] as Map)),
        throwsA(isA<AtlasVaultRevocationException>()),
      );
      expect(await client.snapshot(), before);
    });
  }
  test('public Swift signature agrees and is idempotent', () async {
    final alternate = object('swift_transition');
    await AtlasVaultRevocation.verify(alternate, entries('registry'));
    expect(alternate['root'], object('transition')['root']);
    final client = store('A');
    await client.initialize();
    await client.commit(alternate);
    expect(await client.commit(object('transition')), isFalse);
  });
  test('last-device and unauthorized removals fail closed', () async {
    final normal = store('self-removal');
    await normal.initialize();
    await expectLater(
      normal.prepare(a, a),
      throwsA(isA<AtlasVaultRevocationException>()),
    );

    final client = AtlasVaultRevocationRegistry(
      file: File('${dir.path}/solo'),
      encryptionKey: Uint8List.fromList(List.filled(32, 7)),
      accountId: 'account-c25',
      vaultId: 'vault-c25',
      keyEpoch: 3,
      registry: entries('registry').take(1).toList(),
      stateRoot: List.filled(32, 'ab').join(),
    );
    await client.initialize();
    for (final pair in [
      [a, a],
      [a, b],
      [b, a],
    ]) {
      await expectLater(
        client.prepare(pair[0], pair[1]),
        throwsA(isA<AtlasVaultRevocationException>()),
      );
    }
    expect((await client.snapshot())['sequence'], 0);
  });
  test('actual P6 fork fences removal before authorization', () async {
    final p6 =
        jsonDecode(
              File(
                '../../contracts/sync/test_vectors/atlasvault_sync_recovery_vectors_v1.json',
              ).readAsStringSync(),
            )
            as Map;
    final history = AtlasVaultGuardedSyncState(
      file: File('${dir.path}/history'),
      encryptionKey: Uint8List.fromList(List.filled(32, 8)),
      accountId: 'account_c22',
      vaultId: 'vault_c22',
      collectionId: 'collection_c21',
      keyEpoch: 2,
      trustedSigner: base64Decode(p6['signing_public_b64'] as String),
    );
    await history.initialize();
    for (final name in ['one', 'two', 'fork_two']) {
      final p = p6['packets'][name] as Map;
      final delivery = history.ingest(
        Map<String, Object?>.from(p['view'] as Map),
        (p['registry'] as List)
            .map((e) => Map<String, Object?>.from(e as Map))
            .toList(),
        Map<String, Object?>.from(p['collection'] as Map),
        base64Decode(p['opaque_b64'] as String),
      );
      if (name == 'fork_two') {
        await expectLater(
          delivery,
          throwsA(isA<AtlasVaultStateViewException>()),
        );
      } else {
        await delivery;
      }
    }
    final client = AtlasVaultRevocationRegistry(
      file: File('${dir.path}/registry'),
      encryptionKey: Uint8List.fromList(List.filled(32, 7)),
      accountId: 'account_c22',
      vaultId: 'vault_c22',
      keyEpoch: 2,
      registry: entries('registry'),
      stateRoot: (await history.checkpoint())['cursor']! as String,
    );
    await client.initialize();
    final controller = AtlasVaultRemovalController(
      registry: client,
      initiator: a,
      history: history,
      authorize: () async {
        fail('P6 fork reached prompt');
      },
      sign: (_) async {
        fail('P6 fork reached signing');
      },
    );
    controller.select(b);
    await expectLater(
      controller.remove(b),
      throwsA(isA<AtlasVaultRevocationException>()),
    );
    expect((await client.snapshot())['status'], 'RECOVERY_PENDING');
    expect((await client.snapshot())['sequence'], 0);
    expect((await history.recovery())['status'], 'MANUAL_REQUIRED');
  });
  for (final field in object('transition').keys) {
    test('tampered signed $field fails without persistence', () async {
      final client = store('A');
      await client.initialize();
      final before = await client.snapshot();
      final bad = object('transition');
      bad[field] = bad[field] is int
          ? (bad[field]! as int) + 1
          : 'substitution';
      await expectLater(
        client.commit(bad),
        throwsA(isA<AtlasVaultRevocationException>()),
      );
      expect(await client.snapshot(), before);
    });
  }
  test('no secret fields enter transition or rotation metadata', () async {
    final client = store('A');
    await client.initialize();
    for (final field in [
      'passphrase',
      'vault_key',
      'wrapped_vault_key',
      'access_token',
      'plaintext',
      'signing_private_key',
    ]) {
      await expectLater(
        client.commit({...object('transition'), field: 'forbidden-sentinel'}),
        throwsA(isA<AtlasVaultRevocationException>()),
      );
      expect(
        () => AtlasVaultRevocation.validateRotationPlan(
          {...object('rotation_plan'), field: 'forbidden-sentinel'},
          object('transition'),
          entries('revoked_registry'),
          List.filled(32, 'ab').join(),
        ),
        throwsA(isA<AtlasVaultRevocationException>()),
      );
    }
    expect((await client.snapshot())['sequence'], 0);
  });
  for (final field in object('rotation_plan').keys) {
    test('epoch protocol rejects substituted $field', () {
      final bad = object('rotation_plan');
      bad[field] = bad[field] is int
          ? (bad[field]! as int) + 1
          : 'substitution';
      expect(
        () => AtlasVaultRevocation.validateRotationPlan(
          bad,
          object('transition'),
          entries('revoked_registry'),
          List.filled(32, 'ab').join(),
        ),
        throwsA(isA<AtlasVaultRevocationException>()),
      );
    });
  }
  for (final attack in [
    'denied',
    'cancelled',
    'unavailable',
    'malformed',
    'exception',
    'target',
    'registry',
    'cancel',
    'timeout',
    'wrong-confirmation',
    'fork',
  ]) {
    test('fresh authorization rejects $attack', () async {
      final client = store('A');
      await client.initialize();
      var tick = Duration.zero;
      late AtlasVaultRemovalController controller;
      controller = AtlasVaultRemovalController(
        registry: client,
        initiator: a,
        clock: () => tick,
        authorize: () async {
          if (attack == 'target') controller.select(a);
          if (attack == 'registry') await client.commit(object('transition'));
          if (attack == 'cancel') controller.cancel();
          if (attack == 'timeout') tick = const Duration(seconds: 61);
          if (attack == 'exception') throw StateError('private sentinel');
          if (['denied', 'cancelled', 'unavailable'].contains(attack)) {
            return false;
          }
          if (attack == 'malformed') return 'true';
          return true;
        },
        sign: (_) async {
          fail('unauthorized signing');
        },
      );
      controller.select(b);
      if (attack == 'fork') await client.fence();
      await expectLater(
        controller.remove(attack == 'wrong-confirmation' ? a : b),
        throwsA(isA<AtlasVaultRevocationException>()),
      );
      if (attack != 'registry') {
        expect((await client.snapshot())['sequence'], 0);
      }
    });
  }
  test('two independent device registries survive process kill', () async {
    for (final name in ['A', 'B']) {
      final ready = File('${dir.path}/$name-ready');
      final flutter = Platform.environment['FLUTTER_ROOT'];
      final child =
          await Process.start(flutter == null ? 'dart' : '$flutter/bin/dart', [
            'run',
            'test/support/atlas_vault_revocation_process.dart',
            '${dir.path}/$name',
            ready.path,
          ]);
      final output = child.stdout.drain<void>(),
          errors = child.stderr.drain<void>();
      try {
        final limit = Stopwatch()..start();
        while (!await ready.exists() &&
            limit.elapsed < const Duration(seconds: 30)) {
          await Future<void>.delayed(const Duration(milliseconds: 50));
        }
        expect(await ready.exists(), isTrue);
        child.kill(ProcessSignal.sigkill);
        await child.exitCode.timeout(const Duration(seconds: 10));
        expect(
          (await store(name).snapshot())['registry'],
          entries('revoked_registry'),
        );
        expect(await store(name).commit(object('transition')), isFalse);
        await expectLater(
          store(name).initialize(),
          throwsA(isA<AtlasVaultRevocationException>()),
        );
      } finally {
        child.kill(ProcessSignal.sigkill);
        await child.exitCode;
        await output;
        await errors;
      }
    }
  });
  test('denial cannot be reused, accepted prompt signs exact vector', () async {
    final client = store('A');
    await client.initialize();
    var prompts = 0;
    final key = await Ed25519().newKeyPairFromSeed(List.generate(32, (i) => i));
    final controller = AtlasVaultRemovalController(
      registry: client,
      initiator: a,
      authorize: () async => ++prompts == 2,
      sign: (message) async => Uint8List.fromList(
        (await Ed25519().sign(message, keyPair: key)).bytes,
      ),
    );
    controller.select(b);
    await expectLater(
      controller.remove(b),
      throwsA(isA<AtlasVaultRevocationException>()),
    );
    expect(await controller.remove(b), object('transition'));
    expect(prompts, 2);
  });
}
