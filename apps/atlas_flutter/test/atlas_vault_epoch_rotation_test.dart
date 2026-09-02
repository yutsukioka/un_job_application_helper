import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/epoch_rotation.dart';

void main() {
  final vectors =
      jsonDecode(
            File(
              '../../contracts/sync/test_vectors/atlasvault_epoch_rotation_v1.json',
            ).readAsStringSync(),
          )
          as Map<String, dynamic>;
  final proof = Map<String, Object?>.from(vectors['proof'] as Map);
  test('independent epoch generations and revoked reconnect survive reopen', () async {
    final root = await Directory.systemTemp.createTemp('atlas-c26-');
    AtlasVaultEpochVault device(int i) => AtlasVaultEpochVault(
      Directory('${root.path}/$i'), storageKey: Uint8List.fromList(List.filled(32, 50 + i)),
      deviceID: (vectors['device_ids'] as List)[i] as String,
      registry: (proof['registry'] as List).map((e) => Map<String, Object?>.from(e as Map)).toList(),
      accountID: (proof['plan'] as Map)['account_id'] as String, vaultID: 'vault-c26',
      keyEpoch: 3, stateRoot: List.filled(32, 'ab').join());
    try {
      final clients = [for (var i=0;i<3;i++) device(i)];
      for (final c in clients) { await c.initialize({3: Uint8List.fromList(List.filled(32, 30))}); }
      for (var i=0;i<2;i++) {
        expect(await clients[i].acceptRotation(proof, agreementPrivateKey: Uint8List.fromList(List.filled(32, 20+i))), isTrue);
        expect((await device(i).observation())['key_epoch'], 4);
        expect(await clients[i].acceptRotation(proof, agreementPrivateKey: Uint8List.fromList(List.filled(32, 20+i))), isFalse);
      }
      expect((await clients[2].observation())['key_epoch'], 3);
      await expectLater(clients[2].acceptRotation(proof, agreementPrivateKey: Uint8List.fromList(List.filled(32,22))), throwsA(isA<AtlasVaultRotationException>()));
      expect((await device(2).observation())['status'], 'REVOKED');
    } finally { await root.delete(recursive:true); }
  });
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
