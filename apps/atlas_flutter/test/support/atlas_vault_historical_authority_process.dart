import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:atlas/src/atlas_vault/sync_queue.dart';

Map<String, Object?> object(Object? v) => Map<String, Object?>.from(v! as Map);
Future<void> main(List<String> args) async {
  final root = Directory(args[0]), point = args[1];
  final v = object(
    (object(
          jsonDecode(
            File(
              '../../contracts/sync/test_vectors/atlasvault_bootstrap_authority_diagnostic.json',
            ).readAsStringSync(),
          ),
        )['cases']!
        as List)[1],
  );
  final p = object(v['checkpoint']);
  final proof = object(
    object(
      (object(
            jsonDecode(
              File(
                '../../contracts/sync/test_vectors/atlasvault_historical_authority_v1.json',
              ).readAsStringSync(),
            ),
          )['cases']!
          as List)[1],
    )['proof'],
  );
  final h = AtlasVaultAnchoredSyncState(
    file: File('${root.path}/history'),
    encryptionKey: Uint8List.fromList(List.filled(32, 96)),
    trust: {
      for (final k in ['account_id', 'vault_id', 'collection_id', 'key_epoch'])
        k: p[k],
      for (final k in [
        'trusted_signer_b64',
        'registry',
        'recipient_device_id',
        'confirmed_transcript',
        'current_context',
      ])
        k: v[k],
      'anchor_root': p['root'],
    },
  );
  Future<void> barrier() async {
    await File('${root.path}/ready').writeAsString('ready', flush: true);
    while (true) {
      await Future<void>.delayed(const Duration(minutes: 1));
    }
  }

  await h.installHistoricalAuthorityForTesting(
    proof,
    collection: object(v['collection']),
    opaqueState: base64Decode(v['opaque_b64']! as String),
    beforeReplace: () async {
      if (point == 'before') await barrier();
    },
  );
  await barrier();
}
