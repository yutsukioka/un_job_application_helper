import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_dart_helper_process.dart';
import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> root;
  late List<AtlasVaultEncryptedPatchOperation> operations;
  late Uint8List queueKey;

  setUpAll(() async {
    root = loadAtlasVaultVector(
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
        utf8.encode('atlasvault-c17-synthetic-queue-key'),
      )).bytes,
    );
  });

  test('shared patch contract is strict and deterministically ordered', () {
    final ordered = [...operations]..sort();

    expect(
      ordered.map((value) => value.operationId),
      atlasVaultList(root['expected_transport_order']).cast<String>(),
    );
    expect(operations.first.idempotencyKey, operations.first.operationId);
    expect(operations.first.envelope.parentRevision, isNull);
    expect(
      operations.last.envelope.parentRevision,
      operations.first.envelope.revision,
    );

    final malformed = operations.first.toJson()..['plaintext'] = 'forbidden';
    expect(
      () => AtlasVaultEncryptedPatchOperation.fromJson(malformed),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
    final inconsistent = operations.last.toJson()
      ..['operation_type'] = 'upsert';
    expect(
      () => AtlasVaultEncryptedPatchOperation.fromJson(inconsistent),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
  });

  test('restart helpers launch Dart directly and queue renames sync', () async {
    final helperSource = await File(
      'test/support/atlas_vault_dart_helper_process.dart',
    ).readAsString();
    expect(helperSource, contains("Platform.environment['PATH']"));
    expect(helperSource, contains('existsSync()'));
    expect(helperSource, isNot(contains('runInShell:')));

    final queueSource = await File(
      'lib/src/atlas_vault/sync_queue.dart',
    ).readAsString();
    final replacement = queueSource.indexOf('await replaceCacheFile(');
    final directorySync = queueSource.indexOf(
      'await _syncParentDirectory(file);',
      replacement,
    );
    expect(replacement, greaterThanOrEqualTo(0));
    expect(directorySync, greaterThan(replacement));
    expect(
      queueSource,
      contains("lookupFunction<_FsyncNative, _FsyncDart>('fsync')"),
    );
  });

  test('outbox is encrypted, ordered, durable, and ack gated', () async {
    final directory = await Directory.systemTemp.createTemp(
      'atlasvault-c17-outbox-',
    );
    addTearDown(() => directory.delete(recursive: true));
    final file = File('${directory.path}/outbox.queue');
    final outbox = AtlasVaultDurableEncryptedOutbox(
      file,
      encryptionKey: queueKey,
    );

    await outbox.enqueue(operations.last);
    await outbox.enqueue(operations.first);
    await outbox.enqueue(operations.first);

    expect(await outbox.nextPending(), operations.first);
    expect(await outbox.pendingOperations(), operations);
    expect(await outbox.nextPending(), operations.first);
    final stored = await file.readAsBytes();
    for (final forbidden in <String>[
      operations.first.operationId,
      operations.first.envelope.objectId,
      operations.first.envelope.revision,
      operations.first.envelope.ciphertextBase64,
    ]) {
      expect(utf8.decode(stored), isNot(contains(forbidden)));
    }
    expect(
      (jsonDecode(utf8.decode(stored)) as Map<String, dynamic>).keys.toSet(),
      <String>{'format', 'version', 'nonce_b64', 'ciphertext_b64'},
    );

    final restarted = AtlasVaultDurableEncryptedOutbox(
      file,
      encryptionKey: queueKey,
    );
    expect(await restarted.pendingOperations(), operations);
    await restarted.confirmRemoteAcceptance(operations.first.operationId);
    expect(await restarted.pendingOperations(), <Object>[operations.last]);
    expect(
      () => restarted.confirmRemoteAcceptance(operations.first.operationId),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
    expect(
      () => AtlasVaultDurableEncryptedOutbox(
        file,
        encryptionKey: Uint8List(32)..fillRange(0, 32, 0x78),
      ).pendingOperations(),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
  });

  test('inbox advances cursor after durable apply and deduplicates', () async {
    final directory = await Directory.systemTemp.createTemp(
      'atlasvault-c17-inbox-',
    );
    addTearDown(() => directory.delete(recursive: true));
    final file = File('${directory.path}/inbox.queue');
    final inbox = AtlasVaultDurableEncryptedInbox(
      file,
      encryptionKey: queueKey,
    );
    await inbox.stagePage(
      expectedCursor: null,
      nextCursor: 'cursor-after-two',
      operations: operations,
    );

    expect(await inbox.readCursor(), isNull);
    expect(await inbox.pendingOperations(), operations);
    final applied = <String>[];
    expect(
      await inbox.applyNext((value) => applied.add(value.operationId)),
      operations.first,
    );
    expect(await inbox.readCursor(), isNull);
    expect(
      await AtlasVaultDurableEncryptedInbox(
        file,
        encryptionKey: queueKey,
      ).pendingOperations(),
      <Object>[operations.last],
    );
    expect(
      await inbox.applyNext((value) => applied.add(value.operationId)),
      operations.last,
    );
    expect(await inbox.readCursor(), 'cursor-after-two');
    expect(applied, operations.map((value) => value.operationId));

    await inbox.stagePage(
      expectedCursor: 'cursor-after-two',
      nextCursor: null,
      operations: operations,
    );
    expect(await inbox.pendingOperations(), isEmpty);
    expect(await inbox.readCursor(), isNull);
    expect(
      await inbox.applyNext((value) => applied.add(value.operationId)),
      isNull,
    );
    expect(applied, operations.map((value) => value.operationId));

    final changed = operations.first.toJson()..['lamport'] = 99;
    expect(
      () => inbox.stagePage(
        expectedCursor: null,
        nextCursor: 'cursor-invalid',
        operations: <AtlasVaultEncryptedPatchOperation>[
          AtlasVaultEncryptedPatchOperation.fromJson(changed),
        ],
      ),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
  });

  test(
    'inbox rejects ordering and parent regressions before persistence',
    () async {
      final directory = await Directory.systemTemp.createTemp(
        'atlasvault-c17-invalid-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final file = File('${directory.path}/inbox.queue');
      final inbox = AtlasVaultDurableEncryptedInbox(
        file,
        encryptionKey: queueKey,
      );

      await expectLater(
        inbox.stagePage(
          expectedCursor: null,
          nextCursor: 'cursor-invalid',
          operations: operations.reversed.toList(),
        ),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
      expect(await file.exists(), isFalse);

      final invalid = operations.last.toJson();
      (invalid['envelope']! as Map<String, Object?>)['parent_revision'] =
          'wrong-parent';
      await expectLater(
        inbox.stagePage(
          expectedCursor: null,
          nextCursor: 'cursor-invalid',
          operations: <AtlasVaultEncryptedPatchOperation>[
            operations.first,
            AtlasVaultEncryptedPatchOperation.fromJson(invalid),
          ],
        ),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
      expect(await file.exists(), isFalse);
    },
  );

  test(
    'outbox and inbox survive process kill and restart',
    () async {
      final directory = await Directory.systemTemp.createTemp(
        'atlasvault-c17-process-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final outboxFile = File('${directory.path}/outbox.queue');
      final inboxFile = File('${directory.path}/inbox.queue');
      final readyFile = File('${directory.path}/ready');
      final process = await startAtlasVaultDartHelper(
        'test/support/atlas_vault_sync_queue_process.dart',
        <String>[outboxFile.path, inboxFile.path, readyFile.path],
      );
      addTearDown(() => process.kill(ProcessSignal.sigkill));
      final deadline = DateTime.now().add(const Duration(seconds: 30));
      while (!await readyFile.exists() && DateTime.now().isBefore(deadline)) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
      if (!await readyFile.exists()) {
        final stderr = await utf8.decodeStream(process.stderr);
        fail('queue helper did not become ready: $stderr');
      }
      expect(process.kill(ProcessSignal.sigkill), isTrue);
      expect(await process.exitCode, isNot(0));

      expect(
        await AtlasVaultDurableEncryptedOutbox(
          outboxFile,
          encryptionKey: queueKey,
        ).pendingOperations(),
        operations,
      );
      final inbox = AtlasVaultDurableEncryptedInbox(
        inboxFile,
        encryptionKey: queueKey,
      );
      expect(await inbox.readCursor(), isNull);
      expect(await inbox.pendingOperations(), operations);
    },
    timeout: const Timeout(Duration(minutes: 2)),
  );
}
