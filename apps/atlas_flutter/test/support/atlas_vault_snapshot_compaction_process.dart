import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';

Future<void> main(List<String> arguments) async {
  if (arguments.length != 2) {
    exitCode = 64;
    return;
  }
  final queueKey = Uint8List.fromList(
    (await Sha256().hash(
      utf8.encode('atlasvault-c18-synthetic-collection-queue'),
    )).bytes,
  );
  final authenticationKey = Uint8List.fromList(
    (await Sha256().hash(
      utf8.encode('atlasvault-c18-synthetic-snapshot-authentication'),
    )).bytes,
  );
  final collection = AtlasVaultDurableEncryptedPatchCollection(
    File(arguments[0]),
    encryptionKey: queueKey,
    authenticationKey: authenticationKey,
    collectionId: 'collection_a',
  );
  await collection.compact(
    beforeReplace: () async {
      await File(arguments[1]).writeAsString('ready\n', flush: true);
      await Completer<void>().future;
    },
  );
}
