import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/device_delivery.dart';

void main() {
  final vector =
      jsonDecode(
            File(
              '../../contracts/sync/test_vectors/atlasvault_device_delivery_v2.json',
            ).readAsStringSync(),
          )
          as Map;
  final record =
      (jsonDecode(
                File(
                  '../../contracts/sync/test_vectors/atlasvault_activation_v1.json',
                ).readAsStringSync(),
              )
              as Map)['record']
          as Map;
  final original = record['proof'] as Map, plan = original['plan'] as Map;
  Future<Map<String, Object?>> verify(Map<String, Object?> packet) =>
      AtlasVaultDeviceDelivery.verify(
        packet,
        registry: (original['registry'] as List)
            .map((x) => Map<String, Object?>.from(x as Map))
            .toList(),
        accountID: plan['account_id'] as String,
        vaultID: plan['vault_id'] as String,
        previousEpoch: 3,
        stateRoot: plan['state_root'] as String,
        activationID: original['root'] as String,
        recipientDeviceID: vector['recipient_device_id'] as String,
      );
  test('C27 independent v2 proof agrees with shared vector', () async {
    final packet = Map<String, Object?>.from(vector['packet'] as Map);
    expect((await verify(packet))['new_epoch'], 4);
    expect(
      AtlasVaultDeviceDelivery.canonicalHash(
        Map<String, Object?>.from(packet['proof'] as Map),
      ),
      vector['canonical_sha256'],
    );
    expect((packet['proof'] as Map).containsKey('deliveries'), isFalse);
  });
  test(
    'C27 all signed field substitutions and wrong wrapper fail closed',
    () async {
      for (final field in (vector['packet']['proof'] as Map).keys) {
        final packet = Map<String, Object?>.from(
          jsonDecode(jsonEncode(vector['packet'])) as Map,
        );
        (packet['proof'] as Map)[field] = 'substitution';
        await expectLater(
          verify(packet),
          throwsException,
          reason: field as String,
        );
      }
      final packet = Map<String, Object?>.from(
        jsonDecode(jsonEncode(vector['packet'])) as Map,
      );
      packet['wrapper'] = (original['deliveries'] as List).firstWhere(
        (d) => (d as Map)['device_id'] != vector['recipient_device_id'],
      );
      await expectLater(verify(packet), throwsException);
      await expectLater(
        verify(Map<String, Object?>.from(record)),
        throwsException,
      );
    },
  );
}
