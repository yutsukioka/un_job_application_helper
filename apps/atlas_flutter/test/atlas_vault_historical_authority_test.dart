import 'dart:convert';
import 'dart:io';

import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_runtime_fixtures.dart';
import 'support/atlas_vault_vector_loader.dart';
import 'support/atlas_vault_dart_helper_process.dart';

void main() {
  final v = runtimeRows(
    loadAtlasVaultVector(
      'atlasvault_bootstrap_authority_diagnostic.json',
    )['cases'],
  )[1];
  final original = runtimeObject(
    runtimeRows(
      loadAtlasVaultVector('atlasvault_historical_authority_v1.json')['cases'],
    )[1]['proof'],
  );
  Map<String, Object?> copy(Map<String, Object?> p) =>
      runtimeObject(jsonDecode(jsonEncode(p)));
  Future<AtlasVaultAnchoredSyncState> store([
    Map<String, Object?>? supplied,
    Directory? storage,
  ]) async {
    final v =
        supplied ??
        runtimeRows(
          loadAtlasVaultVector(
            'atlasvault_bootstrap_authority_diagnostic.json',
          )['cases'],
        )[1];
    final root =
        storage ??
        await Directory.systemTemp.createTemp('atlas-authority-test-');
    if (storage == null) addTearDown(() => root.delete(recursive: true));
    final c = runtimeObject(v['checkpoint']);
    final history = AtlasVaultAnchoredSyncState(
      file: File('${root.path}/history'),
      encryptionKey: runtimeTestKey(96),
      trust: {
        for (final k in [
          'account_id',
          'vault_id',
          'collection_id',
          'key_epoch',
        ])
          k: c[k],
        for (final k in [
          'trusted_signer_b64',
          'registry',
          'recipient_device_id',
          'confirmed_transcript',
          'current_context',
        ])
          k: v[k],
        'anchor_root': c['root'],
      },
    );
    await history.bootstrap({
      for (final k in [
        'checkpoint',
        'enrollment',
        'registry',
        'current_context',
        'recipient_device_id',
        'confirmed_transcript',
        'view',
        'collection',
        'opaque_b64',
      ])
        k: v[k],
    });
    return history;
  }

  Future<bool> install(AtlasVaultAnchoredSyncState h, Map<String, Object?> p) =>
      h.installHistoricalAuthority(
        p,
        collection: runtimeObject(v['collection']),
        opaqueState: base64Decode(v['opaque_b64']! as String),
      );
  for (final field in [
    'version',
    'account_id',
    'vault_id',
    'anchor_root',
    'registry_root',
    'key_epoch',
    'registry_generation',
    'collection_sha256',
    'recipient_device_id',
    'transcript_sha256',
  ]) {
    test('historical authority rejects bound $field substitution', () async {
      final h = await store(),
          before = await h.exportEvidence(),
          p = copy(original);
      p[field] = p[field] is int ? (p[field]! as int) + 1 : '0' * 64;
      await expectLater(install(h, p), throwsA(isA<Exception>()));
      expect(await h.exportEvidence(), before);
    });
  }
  final attacks = <String, void Function(Map<String, Object?>)>{
    'missing view': (p) => (p['views']! as List).removeAt(0),
    'duplicate view': (p) =>
        (p['views']! as List).add((p['views']! as List).first),
    'reordered views': (p) =>
        p['views'] = (p['views']! as List).reversed.toList(),
    'view root': (p) => ((p['views']! as List).first as Map)['root'] = '0' * 64,
    'view signature': (p) =>
        ((p['views']! as List).first as Map)['signature_b64'] = base64Encode(
          List.filled(64, 0),
        ),
    'missing descriptor': (p) => (p['signed_descriptors']! as List).removeAt(0),
    'duplicate descriptor': (p) => (p['signed_descriptors']! as List).add(
      (p['signed_descriptors']! as List).first,
    ),
    'descriptor signature': (p) =>
        ((p['signed_descriptors']! as List).first as Map)['signature_b64'] =
            base64Encode(List.filled(64, 0)),
    'missing revocation': (p) => p.remove('revocation'),
    'revoked signer': (p) => (p['revocation']! as Map)['initiator_device_id'] =
        (p['revocation']! as Map)['target_device_id'],
    'author state': (p) =>
        ((p['prior_registry']! as List).first as Map)['state'] = 'REVOKED',
    'missing registry row': (p) => (p['prior_registry']! as List).removeAt(0),
    'extra field': (p) => p['untrusted'] = 'extra',
    'oversized': (p) => p['account_id'] = 'x' * (128 * 1024),
  };
  for (final attack in attacks.entries) {
    test('historical authority rejects ${attack.key}', () async {
      final h = await store(),
          before = await h.exportEvidence(),
          p = copy(original);
      attack.value(p);
      await expectLater(install(h, p), throwsA(isA<Exception>()));
      expect(await h.exportEvidence(), before);
    });
  }
  test('historical authority exact installation retry is idempotent', () async {
    final h = await store(), before = await h.exportEvidence();
    expect(await install(h, copy(original)), isTrue);
    expect(await install(h, copy(original)), isFalse);
    expect(await h.exportEvidence(), before);
  });
  test('nonreconstructable prior projection fails closed', () async {
    final changed = runtimeObject(
      loadAtlasVaultVector(
        'atlasvault_historical_authority_attacks_v1.json',
      )['changed_projection'],
    );
    final h = await store(changed), before = await h.exportEvidence();
    await expectLater(
      h.installHistoricalAuthority(
        runtimeObject(changed['proof']),
        collection: runtimeObject(changed['collection']),
        opaqueState: base64Decode(changed['opaque_b64']! as String),
      ),
      throwsA(
        isA<AtlasVaultStateViewException>().having(
          (e) => e.code,
          'category',
          'ATLAS_HISTORY_PREIMAGE_REQUIRED',
        ),
      ),
    );
    expect(await h.exportEvidence(), before);
  });
  test(
    'signed contradictory evidence remains fenced after authority install',
    () async {
      final h = await store();
      await install(h, copy(original));
      final before = await h.exportEvidence();
      final fork = runtimeRows(
        loadAtlasVaultVector(
          'atlasvault_historical_authority_attacks_v1.json',
        )['fork_views'],
      )[1];
      await expectLater(h.compareEvidence([fork]), throwsA(isA<Exception>()));
      expect(await h.exportEvidence(), before);
      expect((await h.recovery())['status'], 'RECOVERY_PENDING');
      expect((await h.evidence())['peer'], [fork]);
      await expectLater(install(h, copy(original)), throwsA(isA<Exception>()));
      await expectLater(
        h.automaticSync(() async => fail('unfenced fork')),
        throwsA(isA<Exception>()),
      );
    },
  );
  for (final point in ['before', 'after']) {
    test('SIGKILL authority installation $point atomic replacement', () async {
      final root = await Directory.systemTemp.createTemp(
        'atlas-authority-kill-',
      );
      final h = await store(v, root), before = await h.exportEvidence();
      final worker = await startAtlasVaultDartHelper(
        'test/support/atlas_vault_historical_authority_process.dart',
        [root.path, point],
      );
      final out = worker.stdout.drain<void>(),
          err = worker.stderr.drain<void>();
      try {
        final ready = File('${root.path}/ready'),
            deadline = DateTime.now().add(const Duration(seconds: 30));
        while (!await ready.exists() && DateTime.now().isBefore(deadline)) {
          await Future<void>.delayed(const Duration(milliseconds: 50));
        }
        expect(await ready.exists(), isTrue);
        expect(worker.kill(ProcessSignal.sigkill), isTrue);
        await worker.exitCode.timeout(const Duration(seconds: 10));
        await out;
        await err;
        final reopened = await store(v, root);
        expect(await reopened.exportEvidence(), before);
        expect(
          runtimeObject(
            (await reopened.publicationOrigin())['anchor'],
          ).containsKey('historical_authority'),
          point == 'after',
        );
        expect(await install(reopened, copy(original)), point == 'before');
      } finally {
        worker.kill(ProcessSignal.sigkill);
        await worker.exitCode;
        await root.delete(recursive: true);
      }
    });
  }
}
