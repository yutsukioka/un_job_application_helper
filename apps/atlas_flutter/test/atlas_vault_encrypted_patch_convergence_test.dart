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
  late Map<String, AtlasVaultEncryptedPatchOperation> operations;
  late Uint8List queueKey;
  late Uint8List authenticationKey;

  setUpAll(() async {
    root = loadAtlasVaultVector(
      'atlasvault_encrypted_patch_convergence_vectors_v1.json',
    );
    operations = <String, AtlasVaultEncryptedPatchOperation>{
      for (final entry in atlasVaultObject(root['operations']).entries)
        entry.key: AtlasVaultEncryptedPatchOperation.fromJson(
          atlasVaultObject(entry.value),
        ),
    };
    queueKey = Uint8List.fromList(
      (await Sha256().hash(
        utf8.encode('atlasvault-c19-synthetic-replica-queue'),
      )).bytes,
    );
    authenticationKey = Uint8List.fromList(
      (await Sha256().hash(
        utf8.encode('atlasvault-c19-synthetic-snapshot-authentication'),
      )).bytes,
    );
  });

  AtlasVaultDurableEncryptedConvergentReplica replica(File file) =>
      AtlasVaultDurableEncryptedConvergentReplica(
        file,
        encryptionKey: queueKey,
        authenticationKey: authenticationKey,
        collectionId: 'collection_a',
      );

  List<Map<String, Object?>> recordJson(
    List<AtlasVaultOpaqueCiphertextEnvelope> records,
  ) => records.map((item) => item.toJson()).toList(growable: false);

  test('concurrent conflicts are commutative and vector fixed', () async {
    final directory = await Directory.systemTemp.createTemp(
      'atlasvault-c19-order-',
    );
    addTearDown(() => directory.delete(recursive: true));
    final left = replica(File('${directory.path}/left'));
    final right = replica(File('${directory.path}/right'));

    for (final name in <String>['base', 'edit_a', 'edit_b']) {
      await left.ingestRemote(operations[name]!);
    }
    for (final name in <String>['base', 'edit_b', 'edit_a']) {
      await right.ingestRemote(operations[name]!);
    }

    expect(
      recordJson(await left.currentRecords()),
      recordJson(await right.currentRecords()),
    );
    expect(
      (await left.currentRecords()).single.revision,
      root['expected_concurrent_winner_revision'],
    );
    expect(await left.acceptedOperationCount(), 3);
    expect(await right.acceptedOperationCount(), 3);
  });

  test('delete wins over stale patch and authenticated snapshot', () async {
    final directory = await Directory.systemTemp.createTemp(
      'atlasvault-c19-delete-',
    );
    addTearDown(() => directory.delete(recursive: true));
    final snapshotSource = AtlasVaultDurableEncryptedPatchCollection(
      File('${directory.path}/snapshot-source'),
      encryptionKey: queueKey,
      authenticationKey: authenticationKey,
      collectionId: 'collection_a',
    );
    await snapshotSource.append(operations['base']!);
    final oldSnapshot = await snapshotSource.compact();
    final left = replica(File('${directory.path}/left'));
    final right = replica(File('${directory.path}/right'));

    for (final name in <String>['base', 'edit_a', 'delete', 'stale_edit']) {
      await left.ingestRemote(operations[name]!);
    }
    await left.mergeSnapshot(oldSnapshot);
    await right.mergeSnapshot(oldSnapshot);
    for (final name in <String>['stale_edit', 'delete', 'edit_a', 'base']) {
      await right.ingestRemote(operations[name]!);
    }

    expect(
      recordJson(await left.currentRecords()),
      recordJson(await right.currentRecords()),
    );
    expect((await left.currentRecords()).single.tombstone, isTrue);
    expect(
      (await left.currentRecords()).single.revision,
      root['expected_delete_winner_revision'],
    );
  });

  test(
    'offline queue survives kill reconnects and retries exactly once',
    () async {
      final directory = await Directory.systemTemp.createTemp(
        'atlasvault-c19-offline-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final state = File('${directory.path}/offline');
      final ready = File('${directory.path}/ready');
      final process = await startAtlasVaultDartHelper(
        'test/support/atlas_vault_convergence_process.dart',
        <String>[state.path, ready.path],
      );
      addTearDown(() => process.kill(ProcessSignal.sigkill));
      final deadline = DateTime.now().add(const Duration(seconds: 30));
      while (!await ready.exists() && DateTime.now().isBefore(deadline)) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
      if (!await ready.exists()) {
        fail(
          'convergence helper did not become ready: ${await utf8.decodeStream(process.stderr)}',
        );
      }
      expect(process.kill(ProcessSignal.sigkill), isTrue);
      expect(await process.exitCode, isNot(0));

      final restarted = replica(state);
      expect(
        (await restarted.pendingOperations()).map((item) => item.operationId),
        <String>[
          operations['base']!.operationId,
          operations['edit_a']!.operationId,
        ],
      );
      final remote = replica(File('${directory.path}/remote'));
      expect(await restarted.synchronizeTo(remote), 2);
      expect(await restarted.pendingOperations(), isEmpty);
      expect(await remote.acceptedOperationCount(), 2);
      expect(await remote.ingestRemote(operations['edit_a']!), isFalse);
      expect(await remote.acceptedOperationCount(), 2);

      final stored = utf8.decode(
        await state.readAsBytes(),
        allowMalformed: true,
      );
      expect(stored, isNot(contains(operations['edit_a']!.operationId)));
      expect(stored, isNot(contains(operations['edit_a']!.envelope.revision)));
    },
    timeout: const Timeout(Duration(minutes: 2)),
  );

  test('divergent offline edits converge and aliases fail closed', () async {
    final directory = await Directory.systemTemp.createTemp(
      'atlasvault-c19-reconnect-',
    );
    addTearDown(() => directory.delete(recursive: true));
    final left = replica(File('${directory.path}/left'));
    final right = replica(File('${directory.path}/right'));
    await left.queueLocal(operations['base']!);
    await right.ingestRemote(operations['base']!);
    await left.queueLocal(operations['edit_a']!);
    await right.queueLocal(operations['edit_b']!);

    expect(await left.synchronizeTo(right), 2);
    expect(await right.synchronizeTo(left), 1);
    expect(
      recordJson(await left.currentRecords()),
      recordJson(await right.currentRecords()),
    );
    expect((await left.currentRecords()).single.revision, 'rev-edit-b');
    expect(await left.acceptedOperationCount(), 3);
    expect(await right.acceptedOperationCount(), 3);

    final changed = operations['edit_a']!.toJson()..['lamport'] = 99;
    await expectLater(
      left.ingestRemote(AtlasVaultEncryptedPatchOperation.fromJson(changed)),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
  });

  test('snapshots reject conflicting author sequence owners', () async {
    final directory = await Directory.systemTemp.createTemp(
      'atlasvault-c19-snapshot-alias-',
    );
    addTearDown(() => directory.delete(recursive: true));
    final base = operations['base']!;
    final alias = AtlasVaultEncryptedPatchOperation.fromJson(
      base.toJson()..['operation_id'] = '10000000-0000-4000-8000-000000000099',
    );
    final snapshots = <AtlasVaultAuthenticatedCollectionSnapshot>[];
    for (final entry in <String, AtlasVaultEncryptedPatchOperation>{
      'first': base,
      'alias': alias,
    }.entries) {
      final source = AtlasVaultDurableEncryptedPatchCollection(
        File('${directory.path}/snapshot-${entry.key}'),
        encryptionKey: queueKey,
        authenticationKey: authenticationKey,
        collectionId: 'collection_a',
      );
      await source.append(entry.value);
      snapshots.add(await source.compact());
    }

    final target = replica(File('${directory.path}/target'));
    expect(await target.mergeSnapshot(snapshots.first), isTrue);
    await expectLater(
      target.mergeSnapshot(snapshots.last),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
  });
}
