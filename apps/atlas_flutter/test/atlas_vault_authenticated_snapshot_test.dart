import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late List<AtlasVaultEncryptedPatchOperation> operations;
  late Uint8List queueKey;
  late Uint8List authenticationKey;

  setUpAll(() async {
    final root = loadAtlasVaultVector(
      'atlasvault_encrypted_patch_queue_vectors_v1.json',
    );
    operations = atlasVaultList(root['operations'])
        .map(
          (value) => AtlasVaultEncryptedPatchOperation.fromJson(
            atlasVaultObject(value),
          ),
        )
        .toList(growable: false);
    queueKey = Uint8List.fromList(
      (await Sha256().hash(
        utf8.encode('atlasvault-c18-synthetic-collection-queue'),
      )).bytes,
    );
    authenticationKey = Uint8List.fromList(
      (await Sha256().hash(
        utf8.encode('atlasvault-c18-synthetic-snapshot-authentication'),
      )).bytes,
    );
  });

  AtlasVaultEncryptedPatchOperation thirdOperation() {
    final value = operations.first.toJson();
    value['operation_id'] = '00000000-0000-4000-8000-000000000003';
    value['author_sequence'] = 3;
    value['lamport'] = 9;
    final envelope = value['envelope']! as Map<String, Object?>;
    envelope['object_id'] = 'object_b';
    envelope['revision'] = 'rev-b-001';
    envelope['parent_revision'] = null;
    return AtlasVaultEncryptedPatchOperation.fromJson(value);
  }

  AtlasVaultDurableEncryptedPatchCollection collection(File file) =>
      AtlasVaultDurableEncryptedPatchCollection(
        file,
        encryptionKey: queueKey,
        authenticationKey: authenticationKey,
        collectionId: 'collection_a',
      );

  test(
    'snapshot vector authenticates and rejects tamper or truncation',
    () async {
      final root = loadAtlasVaultVector(
        'atlasvault_authenticated_snapshot_vectors_v1.json',
      );
      final raw = atlasVaultObject(root['snapshot']);
      final snapshot = await AtlasVaultAuthenticatedCollectionSnapshot.decode(
        raw,
        authenticationKey: authenticationKey,
      );

      expect(snapshot.toJson(), raw);
      expect(snapshot.collectionRevision, 2);
      expect(snapshot.records.single.tombstone, isTrue);
      expect(
        snapshot.authenticationTagBase64,
        atlasVaultObject(raw['authentication'])['tag_b64'],
      );
      expect(
        snapshot.canonicalPayloadSha256,
        root['expected_canonical_payload_sha256'],
      );

      final tampered = jsonDecode(jsonEncode(raw)) as Map<String, dynamic>;
      final payload = tampered['payload']! as Map<String, dynamic>;
      final records = payload['records']! as List<dynamic>;
      (records.single as Map<String, dynamic>)['revision'] = 'rev-tampered';
      await expectLater(
        AtlasVaultAuthenticatedCollectionSnapshot.decode(
          tampered.cast<String, Object?>(),
          authenticationKey: authenticationKey,
        ),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
      final encoded = utf8.encode(jsonEncode(raw));
      await expectLater(
        AtlasVaultAuthenticatedCollectionSnapshot.decodeBytes(
          Uint8List.fromList(encoded.sublist(0, encoded.length - 9)),
          authenticationKey: authenticationKey,
        ),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
    },
  );

  test('compaction preserves replay bytes tombstones and receipts', () async {
    final directory = await Directory.systemTemp.createTemp('atlasvault-c18-');
    addTearDown(() => directory.delete(recursive: true));
    final full = collection(File('${directory.path}/full.collection'));
    final compacted = collection(
      File('${directory.path}/compacted.collection'),
    );
    final third = thirdOperation();

    for (final operation in <AtlasVaultEncryptedPatchOperation>[
      ...operations,
      third,
    ]) {
      await full.append(operation);
    }
    for (final operation in operations) {
      await compacted.append(operation);
    }

    final snapshot = await compacted.compact();
    expect(snapshot.collectionRevision, 2);
    expect(snapshot.records.map((value) => value.toJson()), <Object?>[
      operations.last.envelope.toJson(),
    ]);
    expect(snapshot.records.single.tombstone, isTrue);
    expect(await compacted.tailOperations(), isEmpty);

    await compacted.append(operations.first);
    expect(await compacted.committedOperationCount(), 2);
    await compacted.append(third);
    expect(
      (await compacted.currentRecords()).map((value) => value.toJson()),
      (await full.currentRecords()).map((value) => value.toJson()),
    );
    expect(await compacted.committedOperationCount(), 3);
    expect(await full.committedOperationCount(), 3);

    final changed = operations.first.toJson()..['lamport'] = 99;
    await expectLater(
      compacted.append(AtlasVaultEncryptedPatchOperation.fromJson(changed)),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );

    final stored = await File(
      '${directory.path}/compacted.collection',
    ).readAsBytes();
    for (final forbidden in <String>[
      operations.first.operationId,
      operations.last.envelope.revision,
      third.envelope.objectId,
    ]) {
      expect(
        utf8.decode(stored, allowMalformed: true),
        isNot(contains(forbidden)),
      );
    }
  });

  test(
    'kill mid-compaction restarts at valid pre or post state',
    () async {
      final directory = await Directory.systemTemp.createTemp(
        'atlasvault-c18-crash-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final file = File('${directory.path}/crash.collection');
      final ready = File('${directory.path}/ready');
      final before = collection(file);
      for (final operation in operations) {
        await before.append(operation);
      }
      final expected = (await before.currentRecords())
          .map((value) => value.toJson())
          .toList(growable: false);

      final process = await Process.start('/usr/bin/env', <String>[
        'dart',
        'run',
        'test/support/atlas_vault_snapshot_compaction_process.dart',
        file.path,
        ready.path,
      ], workingDirectory: Directory.current.path);
      addTearDown(() => process.kill(ProcessSignal.sigkill));
      final deadline = DateTime.now().add(const Duration(seconds: 30));
      while (!await ready.exists() && DateTime.now().isBefore(deadline)) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
      if (!await ready.exists()) {
        final stderr = await utf8.decodeStream(process.stderr);
        fail('snapshot helper did not become ready: $stderr');
      }
      expect(process.kill(ProcessSignal.sigkill), isTrue);
      expect(await process.exitCode, isNot(0));

      final restarted = collection(file);
      expect(
        (await restarted.currentRecords()).map((value) => value.toJson()),
        expected,
      );
      expect(await restarted.committedOperationCount(), 2);
      final snapshot = await restarted.readSnapshot();
      final tail = await restarted.tailOperations();
      expect(
        (snapshot == null && tail.length == 2) ||
            (snapshot != null && tail.isEmpty),
        isTrue,
      );
      final finalSnapshot = await restarted.compact();
      expect(finalSnapshot.records.map((value) => value.toJson()), expected);
      expect(await restarted.tailOperations(), isEmpty);
      expect(
        (await collection(
          file,
        ).currentRecords()).map((value) => value.toJson()),
        expected,
      );
    },
    timeout: const Timeout(Duration(minutes: 2)),
  );

  test('snapshot and journal fail closed with wrong key', () async {
    final directory = await Directory.systemTemp.createTemp(
      'atlasvault-c18-key-',
    );
    addTearDown(() => directory.delete(recursive: true));
    final file = File('${directory.path}/keys.collection');
    final value = collection(file);
    await value.append(operations.first);
    final raw = (await value.compact()).toJson();

    await expectLater(
      AtlasVaultAuthenticatedCollectionSnapshot.decode(
        raw,
        authenticationKey: Uint8List(32)..fillRange(0, 32, 0x78),
      ),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
    await expectLater(
      AtlasVaultDurableEncryptedPatchCollection(
        file,
        encryptionKey: Uint8List(32)..fillRange(0, 32, 0x79),
        authenticationKey: authenticationKey,
        collectionId: 'collection_a',
      ).currentRecords(),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
  });
}
