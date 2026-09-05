import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:cryptography/cryptography.dart';
import 'package:atlas/src/atlas_vault/device_enrollment.dart';

void main() {
  final v = jsonDecode(File('../../contracts/sync/test_vectors/atlasvault_device_enrollment_v1.json').readAsStringSync()) as Map;
  Map<String, Object?> map(Object? x) => Map<String, Object?>.from(x as Map);
  final proof = map(v['proof']), context = map(v['context']);
  final registry = (v['registry'] as List).map(map).toList();
  Future<List<Map<String, Object?>>> verify({Map<String, Object?>? p, Map<String, Object?>? c, List<Map<String, Object?>>? r, String? transcript, String status = 'ACTIVE'}) =>
      AtlasVaultDeviceEnrollment.verify(p ?? proof, registry: r ?? registry, context: c ?? context,
        confirmedTranscript: transcript ?? proof['transcript_sha256'] as String, status: status);
  test('C30 shared enrollment signature and deterministic fields', () async {
    final after = await verify();
    expect(after.any((e) => e['device_id'] == proof['target_device_id']), isTrue);
    final unsigned = {...proof}..remove('root')..remove('signature_b64');
    final created = await AtlasVaultDeviceEnrollment.create(unsigned, registry: registry, context: context,
      confirmedTranscript: proof['transcript_sha256'] as String, status: 'ACTIVE',
      signingKey: await Ed25519().newKeyPairFromSeed(List.filled(32, 10)));
    expect(created, proof);
    expect(AtlasVaultDeviceEnrollment.canonicalHash(proof), v['canonical_sha256']);
  });
  test('C30 field tamper and signed context substitution fail closed', () async {
    for (final key in proof.keys) {
      await expectLater(verify(p: {...proof, key: 'substitution'}), throwsException, reason: key);
    }
    for (final key in context.keys) {
      final value = context[key];
      await expectLater(verify(c: {...context, key: value is int ? value + 1 : 'wrong-context'}), throwsException, reason: key);
    }
  });
  test('C30 replay revoked signer pending and confirmation fences', () async {
    await expectLater(verify(r: await verify()), throwsException);
    await expectLater(verify(r: [for (var i = 0; i < registry.length; i++) {...registry[i], if (i == 0) 'state': 'REVOKED'}]), throwsException);
    for (final status in ['RECOVERY_PENDING', 'ACTIVATION_PENDING', 'CATCH_UP_PENDING', 'CLEANUP_PENDING', 'REVOKED']) {
      await expectLater(verify(status: status), throwsException);
    }
    for (final transcript in ['', '00' * 32, 'aa' * 32]) {
      await expectLater(verify(transcript: transcript), throwsException);
    }
  });
}
