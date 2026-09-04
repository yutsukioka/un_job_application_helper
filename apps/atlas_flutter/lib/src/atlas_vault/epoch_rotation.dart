import 'dart:convert';
import 'dart:typed_data';
import 'dart:math';

import 'package:cryptography/cryptography.dart';
import 'package:pointycastle/export.dart' show SHA256Digest;

import 'sync_queue.dart';
import 'key_epochs.dart';
export 'sync_queue.dart' show AtlasVaultEpochVault;

final class AtlasVaultRotationException implements Exception {
  const AtlasVaultRotationException([
    this.code = 'ATLAS_EPOCH_ROTATION_REJECTED',
  ]);
  final String code;
  @override
  String toString() => code;
}

Never _reject() => throw const AtlasVaultRotationException();
Object? _ordered(Object? value) {
  if (value is Map) {
    final keys = value.keys.cast<String>().toList()..sort();
    return {for (final key in keys) key: _ordered(value[key])};
  }
  if (value is List) return value.map(_ordered).toList();
  return value;
}

Uint8List rotationCanonical(Map<String, Object?> value) =>
    Uint8List.fromList(ascii.encode(jsonEncode(_ordered(value))));
String _digest(List<int> bytes) => SHA256Digest()
    .process(Uint8List.fromList(bytes))
    .map((b) => b.toRadixString(16).padLeft(2, '0'))
    .join();
void _exact(Map<String, Object?> value, Set<String> fields) {
  if (value.length != fields.length || !fields.containsAll(value.keys)) {
    _reject();
  }
}

Uint8List _bytes(Object? value, int size) {
  if (value is! String || value.length != 4 * ((size + 2) ~/ 3)) _reject();
  final result = base64Decode(value);
  if (result.length != size || base64Encode(result) != value) _reject();
  return result;
}

Map<String, Object?> _map(Object? raw) => Map<String, Object?>.from(raw as Map);

abstract final class AtlasVaultEpochRotation {
  static Future<Map<String, Object?>> create(
    Map<String, Object?> transition, {
    required List<Map<String, Object?>> registry,
    required String stateRoot,
    required SimpleKeyPair signingKey,
  }) async {
    try {
      final after = await AtlasVaultRevocation.verify(transition, registry);
      final recipients =
          after
              .where((e) => e['state'] == 'ACTIVE')
              .map((e) => e['device_id']! as String)
              .toList()
            ..sort();
      final plan = <String, Object?>{
        'format': 'atlasvault-rotation-plan',
        'version': 1,
        'account_id': transition['account_id'],
        'vault_id': transition['vault_id'],
        'previous_epoch': transition['key_epoch'],
        'new_epoch': (transition['key_epoch']! as int) + 1,
        'prior_registry_root': transition['prior_registry_root'],
        'resulting_registry_root': transition['resulting_registry_root'],
        'state_root': stateRoot,
        'initiator_device_id': transition['initiator_device_id'],
        'revocation_root': transition['root'],
        'recipients': recipients,
      };
      AtlasVaultRevocation.validateRotationPlan(
        plan,
        transition,
        after,
        stateRoot,
      );
      final random = Random.secure(),
          material = Uint8List.fromList(
            List.generate(32, (_) => random.nextInt(256)),
          );
      final ring = AtlasVaultKeyEpochRing.fromEntries(
        currentKeyEpoch: plan['new_epoch']! as int,
        keys: {plan['new_epoch']! as int: material},
      );
      material.fillRange(0, material.length, 0);
      final deliveries = <Map<String, Object?>>[];
      for (final device in recipients) {
        final entry = after.firstWhere((e) => e['device_id'] == device);
        final sealed = await ring.sealCurrentHPKEV2(
          recipientPublicKey: _bytes(entry['agreement_public_b64'], 32),
          context: Uint8List.fromList(
            ascii.encode(
              'atlasvault-rotation-delivery-v1:${binding(plan)}:$device',
            ),
          ),
        );
        deliveries.add({
          'device_id': device,
          'key_epoch': sealed.keyEpoch,
          'encapsulated_key_b64': base64Encode(sealed.encapsulatedKey),
          'ciphertext_b64': base64Encode(sealed.ciphertext),
        });
      }
      final proof = <String, Object?>{
        'format': 'atlasvault-epoch-rotation',
        'version': 1,
        'plan': plan,
        'revocation': transition,
        'registry': registry,
        'rotation_signer_device_id': transition['initiator_device_id'],
        'deliveries': deliveries,
      };
      final root = _digest([
        ...ascii.encode('atlasvault-epoch-rotation-v1\n'),
        ...rotationCanonical(proof),
      ]);
      final signature = await Ed25519().sign([
        ...ascii.encode('atlasvault-epoch-rotation-signature-v1\u0000'),
        for (var i = 0; i < 64; i += 2)
          int.parse(root.substring(i, i + 2), radix: 16),
      ], keyPair: signingKey);
      proof.addAll({
        'root': root,
        'signature_b64': base64Encode(signature.bytes),
      });
      await verify(
        proof,
        registry: registry,
        accountID: plan['account_id']! as String,
        vaultID: plan['vault_id']! as String,
        previousEpoch: plan['previous_epoch']! as int,
        stateRoot: stateRoot,
      );
      return proof;
    } catch (_) {
      _reject();
    }
  }

  static String binding(Map<String, Object?> plan) => _digest([
    ...ascii.encode('atlasvault-rotation-binding-v1\n'),
    ...rotationCanonical(plan),
  ]);
  static Future<Map<String, Object?>> verify(
    Map<String, Object?> proof, {
    required List<Map<String, Object?>> registry,
    required String accountID,
    required String vaultID,
    required int previousEpoch,
    required String stateRoot,
  }) async {
    try {
      _exact(proof, {
        'format',
        'version',
        'plan',
        'revocation',
        'registry',
        'rotation_signer_device_id',
        'deliveries',
        'root',
        'signature_b64',
      });
      if (proof['format'] != 'atlasvault-epoch-rotation' ||
          proof['version'] is! int ||
          proof['version'] != 1) {
        _reject();
      }
      final supplied = (proof['registry'] as List).map(_map).toList();
      if (AtlasVaultRevocation.registryRoot(supplied) !=
          AtlasVaultRevocation.registryRoot(registry)) {
        _reject();
      }
      final revocation = _map(proof['revocation']);
      final after = await AtlasVaultRevocation.verify(revocation, registry);
      if (revocation['account_id'] != accountID ||
          revocation['vault_id'] != vaultID ||
          revocation['key_epoch'] != previousEpoch ||
          revocation['sequence'] != 1) {
        _reject();
      }
      final plan = _map(proof['plan']);
      AtlasVaultRevocation.validateRotationPlan(
        plan,
        revocation,
        after,
        stateRoot,
      );
      final recipients = (plan['recipients'] as List).cast<String>();
      if (!recipients.contains(proof['rotation_signer_device_id'])) _reject();
      final deliveries = proof['deliveries'] as List;
      if (deliveries.length != recipients.length) _reject();
      for (var i = 0; i < recipients.length; i++) {
        final delivery = _map(deliveries[i]);
        _exact(delivery, {
          'device_id',
          'key_epoch',
          'encapsulated_key_b64',
          'ciphertext_b64',
        });
        if (delivery['device_id'] != recipients[i] ||
            delivery['key_epoch'] is! int ||
            delivery['key_epoch'] != plan['new_epoch']) {
          _reject();
        }
        _bytes(delivery['encapsulated_key_b64'], 32);
        _bytes(delivery['ciphertext_b64'], 48);
      }
      final unsigned = Map<String, Object?>.of(proof)
        ..remove('root')
        ..remove('signature_b64');
      final root = _digest([
        ...ascii.encode('atlasvault-epoch-rotation-v1\n'),
        ...rotationCanonical(unsigned),
      ]);
      if (proof['root'] != root) _reject();
      final signer = after.firstWhere(
        (e) => e['device_id'] == proof['rotation_signer_device_id'],
      );
      final message = [
        ...ascii.encode('atlasvault-epoch-rotation-signature-v1\u0000'),
        for (var i = 0; i < 64; i += 2)
          int.parse(root.substring(i, i + 2), radix: 16),
      ];
      if (!await Ed25519().verify(
        message,
        signature: Signature(
          _bytes(proof['signature_b64'], 64),
          publicKey: SimplePublicKey(
            _bytes(signer['signing_public_b64'], 32),
            type: KeyPairType.ed25519,
          ),
        ),
      )) {
        _reject();
      }
      return {
        'new_epoch': plan['new_epoch'],
        'recipients': recipients,
        'binding_root': binding(plan),
        'registry': after,
      };
    } on AtlasVaultRotationException {
      rethrow;
    } catch (_) {
      _reject();
    }
  }
}
