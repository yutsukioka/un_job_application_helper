import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';

Map<String, Object?> object(Object? v) => Map<String, Object?>.from(v! as Map);
Map<String, Object?> copy(Map<String, Object?> v) =>
    object(jsonDecode(jsonEncode(v)));

void main() {
  final v = object(
    jsonDecode(
      File(
        '../../contracts/sync/test_vectors/atlasvault_history_bootstrap_v1.json',
      ).readAsStringSync(),
    ),
  );
  final c = object(v['checkpoint']);
  final trust = <String, Object?>{
    for (final k in ['account_id', 'vault_id', 'collection_id', 'key_epoch'])
      k: c[k],
    'trusted_signer_b64': v['trusted_signer_b64'],
    'registry': v['registry'],
    'anchor_root': c['root'],
    'recipient_device_id': v['recipient_device_id'],
    'confirmed_transcript': v['confirmed_transcript'],
    'current_context': v['current_context'],
  };
  final args = <String, Object?>{
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
  };
  late Directory directory;
  setUp(
    () async =>
        directory = await Directory.systemTemp.createTemp('c30-anchor-'),
  );
  tearDown(() async => directory.delete(recursive: true));
  AtlasVaultAnchoredSyncState open(String name) => AtlasVaultAnchoredSyncState(
    file: File('${directory.path}/$name'),
    encryptionKey: Uint8List.fromList(List.filled(32, 96)),
    trust: copy(trust),
  );
  Future<bool> ingest(AtlasVaultAnchoredSyncState client, String name) {
    final p = object(object(v['packets'])[name]);
    return client.ingest(
      object(p['view']),
      (p['registry']! as List).map(object).toList(),
      object(p['collection']),
      base64Decode(p['opaque_b64']! as String),
    );
  }

  test(
    'D105 shared anchor bootstrap restart duplicate and forward admission',
    () async {
      expect(await open('A').bootstrap(copy(args)), isTrue);
      expect((await open('A').checkpoint())['sequence'], 2);
      expect(await open('A').bootstrap(copy(args)), isFalse);
      expect(await ingest(open('A'), 'next'), isTrue);
      expect(await ingest(open('A'), 'next'), isFalse);
      expect((await open('A').checkpoint())['sequence'], 3);
    },
  );
  for (final attack in ['sub_anchor', 'non_chaining']) {
    test(
      'D105 anchored $attack rejects and remains fenced after reopen',
      () async {
        await open('A').bootstrap(copy(args));
        final before = await open('A').checkpoint();
        await expectLater(
          ingest(open('A'), attack),
          throwsA(isA<AtlasVaultStateViewException>()),
        );
        expect(await open('A').checkpoint(), before);
        expect((await open('A').recovery())['status'], 'RECOVERY_PENDING');
      },
    );
  }
  test('D105 two independent anchored branches remain fenced', () async {
    await open('A').bootstrap(copy(args));
    await open('B').bootstrap(copy(args));
    await ingest(open('A'), 'next');
    await ingest(open('B'), 'fork');
    final a = await open('A').exportEvidence(),
        b = await open('B').exportEvidence();
    await expectLater(
      open('A').compareEvidence(b),
      throwsA(isA<AtlasVaultStateViewException>()),
    );
    await expectLater(
      open('B').compareEvidence(a),
      throwsA(isA<AtlasVaultStateViewException>()),
    );
    expect((await open('A').recovery())['status'], 'RECOVERY_PENDING');
    expect((await open('B').recovery())['status'], 'RECOVERY_PENDING');
  });
  for (final field in c.keys) {
    test(
      'D102 certificate substitution $field rejects before persistence',
      () async {
        final bad = copy(args), p = object(bad['checkpoint']), old = p[field];
        p[field] = old is int ? old + 1 : 'substituted';
        bad['checkpoint'] = p;
        await expectLater(
          open('A').bootstrap(bad),
          throwsA(isA<AtlasVaultBootstrapException>()),
        );
        expect(await File('${directory.path}/A').exists(), isFalse);
      },
    );
  }
}
