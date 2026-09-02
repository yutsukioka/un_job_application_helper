import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:atlas/src/atlas_vault/sync_queue.dart';

Future<void> main(List<String> args) async {
  final v =
      jsonDecode(
            File(
              '../../contracts/sync/test_vectors/atlasvault_sync_recovery_vectors_v1.json',
            ).readAsStringSync(),
          )
          as Map;
  final c = AtlasVaultGuardedSyncState(
    file: File(args[0]),
    encryptionKey: Uint8List.fromList(List.generate(32, (i) => i)),
    accountId: 'account_c22',
    vaultId: 'vault_c22',
    collectionId: 'collection_c21',
    keyEpoch: 2,
    trustedSigner: base64Decode(v['signing_public_b64'] as String),
  );
  await c.initialize();
  for (final n in ['one', 'two', 'fork_two']) {
    final p = v['packets'][n] as Map;
    try {
      await c.ingest(
        Map<String, Object?>.from(p['view'] as Map),
        (p['registry'] as List)
            .map((e) => Map<String, Object?>.from(e as Map))
            .toList(),
        Map<String, Object?>.from(p['collection'] as Map),
        base64Decode(p['opaque_b64'] as String),
      );
    } on AtlasVaultStateViewException catch (e) {
      if (n != 'fork_two' || e.code != 'ATLAS_STATE_EQUIVOCATION') rethrow;
    }
  }
  if ((await c.recovery())['status'] != 'MANUAL_REQUIRED') {
    throw StateError('alarm absent');
  }
  await File(args[1]).writeAsString('alarm durable', flush: true);
  while (true) {
    await Future<void>.delayed(const Duration(minutes: 1));
  }
}
