import 'dart:convert';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';
import 'package:pointycastle/export.dart' show SHA256Digest;
import 'epoch_rotation.dart';
import 'sync_queue.dart' show AtlasVaultRevocation;

/// D089 authenticates existing wrapper bytes; it never encrypts or reinterprets v1.
abstract final class AtlasVaultDeviceDelivery {
  static const suite = '0x0020/0x0001/0x0002';
  static const fields = {
    'format',
    'version',
    'activation_id',
    'plan',
    'revocation',
    'registry',
    'recipient_device_id',
    'recipient_agreement_sha256',
    'wrapper_sha256',
    'registry_generation',
    'issuer_device_id',
    'rotation_signer_device_id',
    'hpke_suite',
    'hpke_version',
    'signature_algorithm',
    'signature_version',
    'root',
    'signature_b64',
  };
  static Never fail([String code = 'ATLAS_DEVICE_DELIVERY_REJECTED']) =>
      throw AtlasVaultRotationException(code);
  static Map<String, Object?> map(Object? x) =>
      Map<String, Object?>.from(x as Map);
  static List<Map<String, Object?>> rows(Object? x) =>
      (x as List).map(map).toList();
  static void exact(Map<String, Object?> p, Set<String> keys) {
    if (p.length != keys.length || !keys.containsAll(p.keys)) fail();
  }

  static Uint8List bytes(Object? x, int size) {
    if (x is! String || x.length != 4 * ((size + 2) ~/ 3)) fail();
    final b = base64Decode(x);
    if (b.length != size || base64Encode(b) != x) fail();
    return b;
  }

  static String digest(List<int> b) => SHA256Digest()
      .process(Uint8List.fromList(b))
      .map((x) => x.toRadixString(16).padLeft(2, '0'))
      .join();
  static Map<String, Object?> unsigned(Map<String, Object?> p) => {
    for (final e in p.entries)
      if (!{'root', 'signature_b64'}.contains(e.key)) e.key: e.value,
  };
  static String canonicalHash(Map<String, Object?> p) =>
      digest(rotationCanonical(unsigned(p)));
  static String root(Map<String, Object?> p) => digest([
    ...ascii.encode('atlasvault-device-delivery-proof-v2\n'),
    ...rotationCanonical(unsigned(p)),
  ]);
  static List<int> message(String root) => [
    ...ascii.encode('atlasvault-device-delivery-signature-v2\u0000'),
    for (var i = 0; i < 64; i += 2)
      int.parse(root.substring(i, i + 2), radix: 16),
  ];
  static Future<Map<String, Object?>> create(
    Map<String, Object?> record, {
    required String recipientDeviceID,
    required String issuerDeviceID,
    required SimpleKeyPair signingKey,
    required List<Map<String, Object?>> currentRegistry,
    required bool recoveryPending,
  }) async {
    try {
      exact(record, {'format', 'version', 'status', 'transition_id', 'proof'});
      final old = map(record['proof']), plan = map(old['plan']);
      if (recoveryPending ||
          record['format'] != 'atlasvault-activation-record' ||
          record['version'] is! int ||
          record['version'] != 1 ||
          record['status'] != 'ACTIVATION_ACCEPTED' ||
          record['transition_id'] != old['root']) {
        fail();
      }
      final result = await AtlasVaultEpochRotation.verify(
        old,
        registry: rows(old['registry']),
        accountID: plan['account_id']! as String,
        vaultID: plan['vault_id']! as String,
        previousEpoch: plan['previous_epoch']! as int,
        stateRoot: plan['state_root']! as String,
      );
      AtlasVaultRevocation.registryRoot(currentRegistry);
      for (final id in [issuerDeviceID, recipientDeviceID]) {
        final historic = rows(
          result['registry'],
        ).firstWhere((e) => e['device_id'] == id && e['state'] == 'ACTIVE');
        final current = currentRegistry.firstWhere(
          (e) => e['device_id'] == id && e['state'] == 'ACTIVE',
        );
        if (digest(rotationCanonical(historic)) !=
            digest(rotationCanonical(current))) {
          fail();
        }
      }
      final wrapper = rows(
        old['deliveries'],
      ).firstWhere((e) => e['device_id'] == recipientDeviceID);
      final recipient = rows(
        result['registry'],
      ).firstWhere((e) => e['device_id'] == recipientDeviceID);
      final proof = <String, Object?>{
        'format': 'atlasvault-device-delivery-proof',
        'version': 2,
        'activation_id': old['root'],
        'plan': plan,
        'revocation': old['revocation'],
        'registry': old['registry'],
        'recipient_device_id': recipientDeviceID,
        'recipient_agreement_sha256': digest(
          bytes(recipient['agreement_public_b64'], 32),
        ),
        'wrapper_sha256': digest(rotationCanonical(wrapper)),
        'registry_generation': plan['new_epoch'],
        'issuer_device_id': issuerDeviceID,
        'rotation_signer_device_id': old['rotation_signer_device_id'],
        'hpke_suite': suite,
        'hpke_version': 2,
        'signature_algorithm': 'Ed25519',
        'signature_version': 1,
      };
      proof['root'] = root(proof);
      proof['signature_b64'] = base64Encode(
        (await Ed25519().sign(
          message(proof['root']! as String),
          keyPair: signingKey,
        )).bytes,
      );
      final packet = <String, Object?>{'proof': proof, 'wrapper': wrapper};
      await verify(
        packet,
        registry: rows(old['registry']),
        accountID: plan['account_id']! as String,
        vaultID: plan['vault_id']! as String,
        previousEpoch: plan['previous_epoch']! as int,
        stateRoot: plan['state_root']! as String,
        activationID: old['root']! as String,
        recipientDeviceID: recipientDeviceID,
      );
      return map(jsonDecode(jsonEncode(packet)));
    } catch (_) {
      fail();
    }
  }

  static Future<Map<String, Object?>> verify(
    Map<String, Object?> packet, {
    required List<Map<String, Object?>> registry,
    required String accountID,
    required String vaultID,
    required int previousEpoch,
    required String stateRoot,
    required String activationID,
    required String recipientDeviceID,
  }) async {
    try {
      if ({
        'atlasvault-activation-record',
        'atlasvault-epoch-rotation',
      }.contains(packet['format'])) {
        fail('ATLAS_PER_DEVICE_PROOF_REQUIRED');
      }
      exact(packet, {'proof', 'wrapper'});
      final p = map(packet['proof']), w = map(packet['wrapper']);
      exact(p, fields);
      if (p['format'] != 'atlasvault-device-delivery-proof' ||
          p['version'] is! int ||
          p['version'] != 2 ||
          p['hpke_suite'] != suite ||
          p['hpke_version'] is! int ||
          p['hpke_version'] != 2 ||
          p['signature_algorithm'] != 'Ed25519' ||
          p['signature_version'] is! int ||
          p['signature_version'] != 1 ||
          !RegExp(r'^[a-f0-9]{64}$').hasMatch(activationID) ||
          p['activation_id'] != activationID ||
          p['recipient_device_id'] != recipientDeviceID ||
          AtlasVaultRevocation.registryRoot(rows(p['registry'])) !=
              AtlasVaultRevocation.registryRoot(registry)) {
        fail();
      }
      final t = map(p['revocation']), plan = map(p['plan']);
      final after = await AtlasVaultRevocation.verify(t, registry);
      if (t['account_id'] != accountID ||
          t['vault_id'] != vaultID ||
          t['key_epoch'] != previousEpoch ||
          t['sequence'] != 1) {
        fail();
      }
      AtlasVaultRevocation.validateRotationPlan(plan, t, after, stateRoot);
      if (p['registry_generation'] is! int ||
          p['registry_generation'] != plan['new_epoch']) {
        fail();
      }
      final issuer = after.firstWhere(
        (e) =>
            e['device_id'] == p['issuer_device_id'] && e['state'] == 'ACTIVE',
      );
      after.firstWhere(
        (e) =>
            e['device_id'] == p['rotation_signer_device_id'] &&
            e['state'] == 'ACTIVE',
      );
      final recipient = after.firstWhere(
        (e) => e['device_id'] == recipientDeviceID && e['state'] == 'ACTIVE',
      );
      exact(w, {
        'device_id',
        'key_epoch',
        'encapsulated_key_b64',
        'ciphertext_b64',
      });
      if (w['device_id'] != recipientDeviceID ||
          w['key_epoch'] is! int ||
          w['key_epoch'] != plan['new_epoch']) {
        fail();
      }
      bytes(w['encapsulated_key_b64'], 32);
      bytes(w['ciphertext_b64'], 48);
      if (p['wrapper_sha256'] != digest(rotationCanonical(w)) ||
          p['recipient_agreement_sha256'] !=
              digest(bytes(recipient['agreement_public_b64'], 32)) ||
          p['root'] != root(p)) {
        fail();
      }
      if (!await Ed25519().verify(
        message(p['root']! as String),
        signature: Signature(
          bytes(p['signature_b64'], 64),
          publicKey: SimplePublicKey(
            bytes(issuer['signing_public_b64'], 32),
            type: KeyPairType.ed25519,
          ),
        ),
      )) {
        fail();
      }
      return {
        'new_epoch': plan['new_epoch'],
        'registry': after,
        'recipients': plan['recipients'],
        'recipient_commitment': digest([
          ...ascii.encode('atlasvault-active-recipients-v1\n'),
          ...rotationCanonical({'recipients': plan['recipients']}),
        ]),
      };
    } on AtlasVaultRotationException {
      rethrow;
    } catch (_) {
      fail();
    }
  }
}
