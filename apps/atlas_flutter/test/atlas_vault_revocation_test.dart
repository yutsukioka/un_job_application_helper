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
          if (['denied', 'cancelled', 'unavailable'].contains(attack))
            return false;
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
      if (attack != 'registry')
        expect((await client.snapshot())['sequence'], 0);
    });
  }
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
