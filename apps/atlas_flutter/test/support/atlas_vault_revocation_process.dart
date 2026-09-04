import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:atlas/src/atlas_vault/sync_queue.dart';

Future<void> main(List<String> args) async {
  final v =
      jsonDecode(
            File(
              '../../contracts/sync/test_vectors/atlasvault_revocation_v1.json',
            ).readAsStringSync(),
          )
          as Map;
  final registry = AtlasVaultRevocationRegistry(
    file: File(args[0]),
    encryptionKey: Uint8List.fromList(List.filled(32, 7)),
    accountId: 'account-c25',
    vaultId: 'vault-c25',
    keyEpoch: 3,
    registry: (v['registry'] as List)
        .map((e) => Map<String, Object?>.from(e as Map))
        .toList(),
    stateRoot: List.filled(32, 'ab').join(),
  );
  await registry.initialize();
  await registry.commit(Map<String, Object?>.from(v['transition'] as Map));
  await File(args[1]).writeAsString('DURABLE', flush: true);
  while (true) {
    await Future<void>.delayed(const Duration(minutes: 1));
  }
}
