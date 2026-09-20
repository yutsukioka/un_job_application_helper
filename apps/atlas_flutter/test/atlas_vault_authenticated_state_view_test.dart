import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';

final vectors =
    jsonDecode(
          File(
            '../../contracts/sync/test_vectors/atlasvault_authenticated_state_view_vectors_v2.json',
          ).readAsStringSync(),
        )
        as Map<String, dynamic>;
final packages = vectors['packages'] as Map<String, dynamic>;
final fixtureKey = Uint8List.fromList(List.generate(32, (i) => i));
AtlasVaultAuthenticatedHistory client(String path) =>
    AtlasVaultAuthenticatedHistory(
      file: File(path),
      encryptionKey: fixtureKey,
      accountId: 'account_c22',
      vaultId: 'vault_c22',
      collectionId: 'collection_c21',
      keyEpoch: 1,
      trustedSigner: base64Decode(vectors['signing_public_b64'] as String),
    );

final class MaliciousServer {
  MaliciousServer(String name)
    : package = jsonDecode(jsonEncode(packages[name])) as Map<String, dynamic>;
  final Map<String, dynamic> package;
  Future<bool> serve(AtlasVaultAuthenticatedHistory c) => c.observe(
    Map<String, Object?>.from(package['view'] as Map),
    (package['registry'] as List)
        .map((e) => Map<String, Object?>.from(e as Map))
        .toList(),
    Map<String, Object?>.from(package['collection'] as Map),
    base64Decode(package['opaque_b64'] as String),
  );
}

Matcher failure([String? code]) => throwsA(
  isA<AtlasVaultStateViewException>().having(
    (e) => e.toString(),
    'code',
    code ?? startsWith('ATLAS_'),
  ),
);

void main() {
  late Directory dir;
  setUp(() async {
    dir = await Directory.systemTemp.createTemp('c22-');
  });
  tearDown(() async {
    await dir.delete(recursive: true);
  });
  test(
    'shared view roots signatures and legitimate registry transitions',
    () async {
      final c = client('${dir.path}/C');
      await c.initialize();
      final signer = await Ed25519().newKeyPairFromSeed(fixtureKey);
      for (final name in ['one', 'two', 'three']) {
        final s = MaliciousServer(name),
            v = Map<String, Object?>.from(packages[name]['view'] as Map);
        final unsigned = Map<String, Object?>.of(v)
          ..remove('root')
          ..remove('signature_b64');
        expect(
          await AtlasVaultAuthenticatedStateView.sign(unsigned, signer),
          v,
        );
        expect(
          AtlasVaultAuthenticatedStateView.registryRoot(
            (s.package['registry'] as List)
                .map((e) => Map<String, Object?>.from(e as Map))
                .toList(),
          ),
          v['registry_root'],
        );
        expect(await s.serve(c), isTrue);
        expect(await s.serve(c), isFalse);
      }
      expect((await client('${dir.path}/C').exportEvidence()).length, 3);
    },
  );
  for (final fork in ['fork_two', 'fork_state', 'fork_three']) {
    test(
      'independent persisted clients expose $fork on evidence exchange',
      () async {
        var a = client('${dir.path}/A/anchor'),
            b = client('${dir.path}/B/anchor');
        await a.initialize();
        await b.initialize();
        for (final c in [a, b]) {
          await MaliciousServer('one').serve(c);
        }
        await MaliciousServer('two').serve(a);
        await MaliciousServer(
          fork == 'fork_three' ? 'fork_two' : fork,
        ).serve(b);
        if (fork == 'fork_three') {
          await MaliciousServer(fork).serve(b);
        }
        a = client('${dir.path}/A/anchor');
        b = client('${dir.path}/B/anchor');
        final left = await a.exportEvidence(), right = await b.exportEvidence();
        expect(left[1]['root'], isNot(right[1]['root']));
        await expectLater(
          a.compareEvidence(right),
          failure('ATLAS_STATE_EQUIVOCATION'),
        );
        await expectLater(
          b.compareEvidence(left),
          failure('ATLAS_STATE_EQUIVOCATION'),
        );
        await expectLater(
          MaliciousServer('three').serve(client('${dir.path}/A/anchor')),
          failure('ATLAS_STATE_EQUIVOCATION'),
        );
      },
    );
  }
  for (final attack in [
    'substituted',
    'added',
    'removed',
    'registry_rollback',
  ]) {
    test('server $attack device list rejected', () async {
      final a = client('${dir.path}/A'), b = client('${dir.path}/B');
      await a.initialize();
      await b.initialize();
      for (final c in [a, b]) {
        await MaliciousServer('one').serve(c);
      }
      await MaliciousServer('two').serve(a);
      final s = MaliciousServer('two');
      if (attack == 'removed' || attack == 'registry_rollback') {
        s.package['registry'] = packages['one']['registry'];
      } else if (attack == 'added') {
        (s.package['registry'] as List).add({
          'device_id': 'f' * 64,
          'descriptor_sha256': 'e' * 64,
        });
      } else {
        (s.package['registry'] as List)[0]['descriptor_sha256'] = 'f' * 64;
      }
      await expectLater(s.serve(b), failure('ATLAS_REGISTRY_SUBSTITUTION'));
      expect((await b.exportEvidence()).length, 1);
    });
  }
  for (final name in ['other_account', 'other_vault', 'other_epoch']) {
    test('validly signed $name rejected', () async {
      final c = client('${dir.path}/C');
      await c.initialize();
      await expectLater(
        MaliciousServer(name).serve(c),
        failure('ATLAS_STATE_VIEW_REJECTED'),
      );
    });
  }
  test('all authenticated fields reject tampering', () async {
    final c = client('${dir.path}/C');
    await c.initialize();
    for (final field in (packages['one']['view'] as Map).keys) {
      final s = MaliciousServer('one'), v = s.package['view'] as Map;
      final value = v[field];
      v[field] = value is int
          ? value + 1
          : 'x${(value as String).substring(1)}';
      await expectLater(s.serve(c), failure());
    }
    expect(await c.exportEvidence(), isEmpty);
  });
  test(
    'rollback and broken peer chain reject; matching prefix is not freshness',
    () async {
      final a = client('${dir.path}/A'), b = client('${dir.path}/B');
      await a.initialize();
      await b.initialize();
      await expectLater(
        a.compareEvidence([]),
        failure('ATLAS_CHECKPOINT_REQUIRED'),
      );
      for (final c in [a, b]) {
        await MaliciousServer('one').serve(c);
      }
      await MaliciousServer('two').serve(a);
      expect(await a.compareEvidence(await b.exportEvidence()), 1);
      await expectLater(
        MaliciousServer('one').serve(a),
        failure('ATLAS_ROLLBACK_REJECTED'),
      );
      final bad = await a.exportEvidence();
      bad[1]['previous_root'] = 'e' * 64;
      await expectLater(
        b.compareEvidence(bad),
        failure('ATLAS_STATE_VIEW_REJECTED'),
      );
      await expectLater(
        client('${dir.path}/missing').exportEvidence(),
        failure(),
      );
      await File('${dir.path}/B').writeAsString('corrupt');
      await expectLater(b.exportEvidence(), failure());
    },
  );
}
