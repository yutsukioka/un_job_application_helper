import 'dart:collection';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';

const _collectionId = 'collection_c20';

Future<Uint8List> _key(String label) async => Uint8List.fromList(
  (await Sha256().hash(utf8.encode('atlasvault-c20-synthetic-$label'))).bytes,
);

Future<Map<String, Object?>> _load(String path) async =>
    (jsonDecode(await File(path).readAsString()) as Map)
        .cast<String, Object?>();

Future<void> _write(String path, Map<String, Object?> value) async {
  await File(
    path,
  ).writeAsString('${jsonEncode(_canonical(value))}\n', flush: true);
}

Object? _canonical(Object? value) {
  if (value is Map) {
    final ordered = SplayTreeMap<String, Object?>();
    for (final entry in value.entries) {
      ordered[entry.key as String] = _canonical(entry.value);
    }
    return ordered;
  }
  if (value is List) return value.map(_canonical).toList(growable: false);
  return value;
}

Future<
  (
    AtlasVaultDurableEncryptedConvergentReplica,
    AtlasVaultDurableEncryptedOutbox,
    AtlasVaultDurableEncryptedInbox,
  )
>
_parts(Directory directory) async {
  await directory.create(recursive: true);
  final replica = AtlasVaultDurableEncryptedConvergentReplica(
    File('${directory.path}/replica.state'),
    encryptionKey: await _key('replica'),
    authenticationKey: await _key('snapshot-authentication'),
    collectionId: _collectionId,
  );
  final outbox = AtlasVaultDurableEncryptedOutbox(
    File('${directory.path}/outbox.state'),
    encryptionKey: await _key('outbox'),
  );
  final inbox = AtlasVaultDurableEncryptedInbox(
    File('${directory.path}/inbox.state'),
    encryptionKey: await _key('inbox'),
  );
  return (replica, outbox, inbox);
}

Future<Map<String, AtlasVaultEncryptedPatchOperation>> _operations(
  String path,
) async {
  final root = await _load(path);
  final raw = (root['operations']! as Map).cast<String, Object?>();
  return <String, AtlasVaultEncryptedPatchOperation>{
    for (final entry in raw.entries)
      entry.key: AtlasVaultEncryptedPatchOperation.fromJson(
        (entry.value! as Map).cast<String, Object?>(),
      ),
  };
}

Future<AtlasVaultEncryptedPatchOperation> _decryptTransport(
  AtlasVaultEncryptedPatchOperation operation,
) async {
  final encrypted = base64Decode(operation.envelope.ciphertextBase64);
  final clear = await AesGcm.with256bits().decrypt(
    SecretBox(
      encrypted.sublist(0, encrypted.length - 16),
      nonce: base64Decode(operation.envelope.nonceBase64),
      mac: Mac(encrypted.sublist(encrypted.length - 16)),
    ),
    secretKey: SecretKey(await _key('transport')),
    aad: base64Decode(operation.envelope.aadBase64),
  );
  return AtlasVaultEncryptedPatchOperation.fromJson(
    (jsonDecode(utf8.decode(clear)) as Map).cast<String, Object?>(),
  );
}

Future<Map<String, Object?>> _result(
  AtlasVaultDurableEncryptedConvergentReplica replica,
  AtlasVaultDurableEncryptedOutbox outbox,
  AtlasVaultDurableEncryptedInbox inbox,
) async {
  final records = (await replica.currentRecords())
      .map((item) => item.toJson())
      .toList(growable: false);
  final encoded = utf8.encode(jsonEncode(_canonical(records)));
  final digest = await Sha256().hash(encoded);
  return <String, Object?>{
    'pid': pid,
    'records': records,
    'state_sha256': digest.bytes
        .map((value) => value.toRadixString(16).padLeft(2, '0'))
        .join(),
    'accepted_operation_count': await replica.acceptedOperationCount(),
    'pending_replica_count': (await replica.pendingOperations()).length,
    'pending_outbox_count': (await outbox.pendingOperations()).length,
    'cursor': await inbox.readCursor(),
  };
}

Future<void> _prepare(List<String> arguments) async {
  if (arguments.length != 5) exit(64);
  final operations = await _operations(arguments[2]);
  final (replica, outbox, inbox) = await _parts(Directory(arguments[1]));
  for (final name in arguments[3].split(',')) {
    await replica.queueLocal(operations[name]!);
    await outbox.enqueue(operations[name]!);
  }
  final result = await _result(replica, outbox, inbox);
  result['operations'] = (await outbox.pendingOperations())
      .map((item) => item.toJson())
      .toList(growable: false);
  await _write(arguments[4], result);
}

Future<void> _apply(List<String> arguments) async {
  if (arguments.length != 4) exit(64);
  final page = await _load(arguments[2]);
  final (replica, outbox, inbox) = await _parts(Directory(arguments[1]));
  final pendingReplica = (await replica.pendingOperations())
      .map((item) => item.operationId)
      .toSet();
  final pendingOutbox = (await outbox.pendingOperations())
      .map((item) => item.operationId)
      .toSet();
  for (final operationId
      in (page['accepted_operation_ids']! as List).cast<String>()) {
    if (pendingReplica.contains(operationId)) {
      await replica.confirmRemoteAcceptance(operationId);
    }
    if (pendingOutbox.contains(operationId)) {
      await outbox.confirmRemoteAcceptance(operationId);
    }
  }
  await inbox.stagePage(
    expectedCursor: page['expected_cursor'] as String?,
    nextCursor: page['next_cursor'] as String?,
    operations: (page['transport_operations']! as List).map(
      (item) => AtlasVaultEncryptedPatchOperation.fromJson(
        (item as Map).cast<String, Object?>(),
      ),
    ),
  );
  while (await inbox.applyNext((transport) async {
        await replica.ingestRemote(await _decryptTransport(transport));
      }) !=
      null) {}
  await _write(arguments[3], await _result(replica, outbox, inbox));
}

Future<void> main(List<String> arguments) async {
  if (arguments.isEmpty) exit(64);
  if (arguments.first == 'prepare') {
    await _prepare(arguments);
  } else if (arguments.first == 'apply') {
    await _apply(arguments);
  } else {
    exit(64);
  }
}
