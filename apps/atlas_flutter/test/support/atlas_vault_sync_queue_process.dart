import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:cryptography/cryptography.dart';

import 'atlas_vault_vector_loader.dart';

Future<void> main(List<String> arguments) async {
  if (arguments.length != 3) {
    exitCode = 64;
    return;
  }
  final root = loadAtlasVaultVector(
    'atlasvault_encrypted_patch_queue_vectors_v1.json',
  );
  final operations = atlasVaultList(root['operations'])
      .map(
        (value) => AtlasVaultEncryptedPatchOperation.fromJson(
          atlasVaultObject(value),
        ),
      )
      .toList(growable: false);
  final key = Uint8List.fromList(
    (await Sha256().hash(utf8.encode('atlasvault-c17-synthetic-queue-key')))
        .bytes,
  );
  final outbox = AtlasVaultDurableEncryptedOutbox(
    File(arguments[0]),
    encryptionKey: key,
  );
  await outbox.enqueue(operations.last);
  await outbox.enqueue(operations.first);
  final inbox = AtlasVaultDurableEncryptedInbox(
    File(arguments[1]),
    encryptionKey: key,
  );
  await inbox.stagePage(
    expectedCursor: null,
    nextCursor: 'cursor-after-two',
    operations: operations,
  );
  await File(arguments[2]).writeAsString('ready\n', flush: true);
  await Completer<void>().future;
}
