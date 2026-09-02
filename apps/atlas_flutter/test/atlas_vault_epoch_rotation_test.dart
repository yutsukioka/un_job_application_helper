import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/epoch_rotation.dart';

void main() {
  final vectors = jsonDecode(File('../../contracts/sync/test_vectors/atlasvault_epoch_rotation_v1.json').readAsStringSync()) as Map<String, dynamic>;
  final proof = Map<String, Object?>.from(vectors['proof'] as Map);
  Future<Map<String, Object?>> check(Map<String, Object?> value) =>
      AtlasVaultEpochRotation.verify(value,
        registry: (proof['registry'] as List).map((e) => Map<String, Object?>.from(e as Map)).toList(),
        accountID: (proof['plan'] as Map)['account_id'] as String,
        vaultID: 'vault-c26', previousEpoch: 3, stateRoot: List.filled(32, 'ab').join());
  test('shared signed epoch binding and active recipients', () async {
    final result = await check(proof);
    expect(result['new_epoch'], 4);
    expect(result['binding_root'], vectors['binding_root']);
    expect(result['recipients'], isNot(contains((vectors['device_ids'] as List)[2])));
  });
  for (final field in (proof['plan'] as Map).keys) {
    test('reject substituted epoch field $field', () async {
      final changed = Map<String, Object?>.from(jsonDecode(jsonEncode(proof)) as Map);
      final plan = changed['plan'] as Map;
      final old = plan[field];
      plan[field] = old is int ? old + 1 : old is List ? <Object?>[] : 'substituted';
      await expectLater(check(changed), throwsA(isA<AtlasVaultRotationException>()));
    });
  }
}
