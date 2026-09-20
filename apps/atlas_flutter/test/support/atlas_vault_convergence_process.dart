import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';

import 'atlas_vault_vector_loader.dart';

Future<void> main(List<String> arguments) async {
  if (arguments.length != 2) {
    exitCode = 64;
    return;
  }
  final root = loadAtlasVaultVector(
    'atlasvault_encrypted_patch_convergence_vectors_v1.json',
  );
  final raw = atlasVaultObject(root['operations']);
  final operations = <String, AtlasVaultEncryptedPatchOperation>{
    for (final entry in raw.entries)
      entry.key: AtlasVaultEncryptedPatchOperation.fromJson(
        atlasVaultObject(entry.value),
      ),
  };
  final queueKey = Uint8List.fromList(
    (await Sha256().hash(
      utf8.encode('atlasvault-c19-synthetic-replica-queue'),
    )).bytes,
  );
  final authenticationKey = Uint8List.fromList(
    (await Sha256().hash(
      utf8.encode('atlasvault-c19-synthetic-snapshot-authentication'),
    )).bytes,
  );
  final replica = AtlasVaultDurableEncryptedConvergentReplica(
    File(arguments[0]),
    encryptionKey: queueKey,
    authenticationKey: authenticationKey,
    collectionId: 'collection_a',
  );
  await replica.queueLocal(operations['base']!);
  await replica.queueLocal(operations['edit_a']!);
  await File(arguments[1]).writeAsString('ready\n', flush: true);
  while (true) {
    await Future<void>.delayed(const Duration(minutes: 1));
  }
}
