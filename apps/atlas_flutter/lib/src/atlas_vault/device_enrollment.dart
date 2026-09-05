import 'dart:convert';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';
import 'package:pointycastle/export.dart' show SHA256Digest;
import 'epoch_rotation.dart' show rotationCanonical;
import 'sync_queue.dart' show AtlasVaultRevocation;

final class AtlasVaultEnrollmentException implements Exception {
  const AtlasVaultEnrollmentException();
  @override
  String toString() => 'ATLAS_ENROLLMENT_REJECTED';
}

/// Additive D099 signature; current context and confirmation come from custody.
abstract final class AtlasVaultDeviceEnrollment {
  static const contextFields = {
    'account_id',
    'vault_id',
    'registry_generation',
    'key_epoch',
    'state_root',
    'activation_id',
  };
  static const fields = {
    ...contextFields,
    'format',
    'version',
    'next_registry_generation',
    'prior_registry_root',
    'resulting_registry_root',
    'target_device_id',
    'target_signing_public_b64',
    'target_agreement_public_b64',
    'target_agreement_sha256',
    'transcript_sha256',
    'issuer_device_id',
    'authorization_category',
    'signature_algorithm',
  };
  static Never _reject() => throw const AtlasVaultEnrollmentException();
  static void _exact(Map<String, Object?> p, Set<String> keys) {
    if (p.length != keys.length || !keys.containsAll(p.keys)) _reject();
  }

  static String _digest(List<int> bytes) => SHA256Digest()
      .process(Uint8List.fromList(bytes))
      .map((b) => b.toRadixString(16).padLeft(2, '0'))
      .join();
  static Map<String, Object?> _unsigned(Map<String, Object?> p) => {...p}
    ..remove('root')
    ..remove('signature_b64');
  static String canonicalHash(Map<String, Object?> p) =>
      _digest(rotationCanonical(_unsigned(p)));
  static String _root(Map<String, Object?> p) => _digest([
    ...ascii.encode('atlasvault-device-enrollment-v1\n'),
    ...rotationCanonical(_unsigned(p)),
  ]);
  static List<int> _message(String root) => [
    ...ascii.encode('atlasvault-device-enrollment-signature-v1\u0000'),
    for (var i = 0; i < 64; i += 2)
      int.parse(root.substring(i, i + 2), radix: 16),
  ];
  static Uint8List _bytes(Object? v, int n) {
    if (v is! String || v.length != 4 * ((n + 2) ~/ 3)) _reject();
    final result = base64Decode(v);
    if (result.length != n || base64Encode(result) != v) _reject();
    return result;
  }

  static void _number(Object? n) {
    if (n is! int || n < 1 || n >= 9007199254740991) _reject();
  }

  static void _hex(Object? s) {
    if (s is! String || !RegExp(r'^[0-9a-f]{64}$').hasMatch(s)) _reject();
  }

  static Future<List<Map<String, Object?>>> verify(
    Map<String, Object?> p, {
    required List<Map<String, Object?>> registry,
    required Map<String, Object?> context,
    required String confirmedTranscript,
    required String status,
  }) async {
    try {
      _exact(p, {...fields, 'root', 'signature_b64'});
      _exact(context, contextFields);
      if (status != 'ACTIVE' ||
          p['version'] is! int ||
          p['version'] != 1 ||
          p['format'] != 'atlasvault-device-enrollment' ||
          p['authorization_category'] != 'SAS_CONFIRMED' ||
          p['signature_algorithm'] != 'Ed25519') {
        _reject();
      }
      for (final name in contextFields) {
        final value = context[name];
        if (['key_epoch', 'registry_generation'].contains(name)) {
          _number(value);
        } else if (['state_root', 'activation_id'].contains(name)) {
          _hex(value);
        } else if (value is! String ||
            !RegExp(r'^[A-Za-z0-9_.~-]{1,128}$').hasMatch(value)) {
          _reject();
        }
        if (p[name] != value || (value is int && p[name] is! int)) _reject();
      }
      _number(p['next_registry_generation']);
      _hex(confirmedTranscript);
      if (p['next_registry_generation'] !=
              (context['registry_generation'] as int) + 1 ||
          confirmedTranscript == '0' * 64 ||
          p['transcript_sha256'] != confirmedTranscript ||
          AtlasVaultRevocation.registryRoot(registry) !=
              p['prior_registry_root']) {
        _reject();
      }
      final target = <String, Object?>{
        'device_id': p['target_device_id'],
        'signing_public_b64': p['target_signing_public_b64'],
        'agreement_public_b64': p['target_agreement_public_b64'],
        'state': 'ACTIVE',
      };
      if (registry.any((e) => e['device_id'] == target['device_id'])) _reject();
      final after =
          [
            for (final e in registry) {...e},
            target,
          ]..sort(
            (a, b) =>
                (a['device_id'] as String).compareTo(b['device_id'] as String),
          );
      if (AtlasVaultRevocation.registryRoot(after) !=
              p['resulting_registry_root'] ||
          _digest(_bytes(target['agreement_public_b64'], 32)) !=
              p['target_agreement_sha256']) {
        _reject();
      }
      final signer = registry.firstWhere(
        (e) =>
            e['device_id'] == p['issuer_device_id'] && e['state'] == 'ACTIVE',
      );
      final root = _root(p);
      if (p['root'] != root ||
          !await Ed25519().verify(
            _message(root),
            signature: Signature(
              _bytes(p['signature_b64'], 64),
              publicKey: SimplePublicKey(
                _bytes(signer['signing_public_b64'], 32),
                type: KeyPairType.ed25519,
              ),
            ),
          )) {
        _reject();
      }
      return after;
    } catch (_) {
      _reject();
    }
  }

  static Future<Map<String, Object?>> create(
    Map<String, Object?> unsigned, {
    required List<Map<String, Object?>> registry,
    required Map<String, Object?> context,
    required String confirmedTranscript,
    required String status,
    required SimpleKeyPair signingKey,
  }) async {
    try {
      _exact(unsigned, fields);
      _hex(confirmedTranscript);
      if (status != 'ACTIVE' ||
          confirmedTranscript == '0' * 64 ||
          unsigned['transcript_sha256'] != confirmedTranscript) {
        _reject();
      }
      final p = {...unsigned}, root = _root(unsigned);
      final signature = await Ed25519().sign(
        _message(root),
        keyPair: signingKey,
      );
      p.addAll({'root': root, 'signature_b64': base64Encode(signature.bytes)});
      await verify(
        p,
        registry: registry,
        context: context,
        confirmedTranscript: confirmedTranscript,
        status: status,
      );
      return p;
    } catch (_) {
      _reject();
    }
  }
}
