import 'dart:convert';
import 'dart:io';

import 'package:atlas/src/atlas_vault/sync_queue.dart';

import 'atlas_vault_runtime_fixtures.dart';

/// Disk-backed synthetic key slots prove process restart, not native protection.
/// Only status, sequence and counts leave the helper process.
Future<void> main(List<String> args) async {
  try {
    final root = Directory(args[0]), action = args[1];
    final slots = Directory('${root.path}/synthetic-slots');
    final fixture = RuntimeFixture();
    final binding = AtlasVaultRuntimeBinding(
      root: Directory('${root.path}/owners'),
      loadKey: (id) async {
        final file = File('${slots.path}/$id');
        return await file.exists() ? file.readAsBytes() : null;
      },
      createKey: (id, key) async {
        if (id.length >= 96) throw StateError('slot identifier too long');
        await slots.create(recursive: true);
        final file = File('${slots.path}/$id');
        if (await file.exists()) throw StateError('slot already exists');
        await file.writeAsBytes(key, flush: true);
      },
    );
    if (action == 'seed') {
      final owner = await fixture.initialize(
        binding.directory(RuntimeFixture.vaultID),
        activate: false,
      );
      await binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
        authenticatedRegistry: fixture.initialHistoryRegistry,
      );
      final session = await binding.open(RuntimeFixture.vaultID);
      await session.commit(
        payload: runtimePayloads()['profile_snippet']!,
        objectID: 'process-binding',
      );
      session.close();
      await File(
        '${root.path}/ready',
      ).writeAsString('binding_committed', flush: true);
      while (true) {
        await Future<void>.delayed(const Duration(seconds: 60));
      }
    }
    final session = await binding.open(RuntimeFixture.vaultID);
    final operation = (await session.read()).single.operation;
    final page = RuntimeSignedPage.fromPublication(
      await session.owner.runtimePublication(
        signingKey: await fixture.signer(),
      ),
      registry: fixture.initialHistoryRegistry,
    );
    final receiverRoot = Directory('${root.path}/receiver');
    final receiver = await File('${receiverRoot.path}/activation').exists()
        ? fixture.owner(receiverRoot, 1)
        : await fixture.initialize(receiverRoot, index: 1, activate: false);
    final accepted = await page.ingest(receiver);
    if (await page.ingest(receiver) != 0 ||
        (await receiver.runtimeRecords()).single.operation != operation ||
        (await session.owner.observation())['state_root'] !=
            page.view['root'] ||
        (await receiver.observation())['state_root'] != page.view['root'] ||
        (await session.owner.observation())['journal_phase'] != null ||
        (await slots.list().length) != 3) {
      throw StateError('reopened binding publication mismatch');
    }
    stdout.writeln(
      jsonEncode({
        'status': (await session.owner.observation())['status'],
        'sequence': page.view['sequence'],
        'key_epoch': page.view['key_epoch'],
        'operations': page.operations.length,
        'accepted': accepted,
        'outbox': (await session.owner.pendingOperations()).length,
        'slots': 3,
      }),
    );
    session.close();
  } catch (_) {
    stderr.writeln('C29_RUNTIME_BINDING_HELPER_FAILED');
    exitCode = 1;
  }
}
