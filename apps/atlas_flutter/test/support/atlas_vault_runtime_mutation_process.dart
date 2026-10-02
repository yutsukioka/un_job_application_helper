import 'dart:convert';
import 'dart:io';

import 'package:atlas/src/atlas_vault/epoch_rotation.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';

import 'atlas_vault_runtime_fixtures.dart';

/// The parent kills this real Dart process, never a mocked persistence future.
/// Output is limited to state categories and counts, never records or key bytes.
Future<void> main(List<String> args) async {
  try {
    final root = Directory(args[0]), action = args[1], mutation = args[2];
    final stage = args.length > 3 ? args[3] : '';
    final fixture = RuntimeFixture(catchUp: true);
    final signer = await fixture.signer(2);
    final payload = runtimePayloads()['profile_snippet']!;
    final owner = fixture.owner(root, 2);
    if (action == 'seed') {
      await fixture.initialize(root, index: 2, activate: false);
      await fixture.catchUp(owner);
      if (mutation != 'create') {
        await owner.commitRuntimeRecord(
          payload: payload,
          objectID: 'process-record',
          signingKey: signer,
        );
      }
      stdout.writeln(jsonEncode({'seeded': true}));
      return;
    }

    var recovered = false, fencedDuringRecovery = false;
    try {
      await owner.runtimeRecords();
    } on AtlasVaultRotationException {
      recovered = true;
      await owner.recoverPublication();
      try {
        await owner.runtimeRecords();
      } on AtlasVaultRotationException catch (error) {
        fencedDuringRecovery = error.code == 'ATLAS_CATCH_UP_PENDING';
      }
      if (!fencedDuringRecovery) throw StateError('recovery fence missing');
      await fixture.catchUp(owner);
    }
    final targetSequence = mutation == 'create' ? 1 : 2;
    if (action == 'mutate' || action == 'resume') {
      final before = await owner.runtimeRecords();
      final current = before.isEmpty ? null : before.single;
      if (current == null ||
          current.operation.authorSequence < targetSequence) {
        await owner.commitRuntimeRecordForTesting(
          payload: mutation == 'delete'
              ? null
              : mutation == 'update'
              ? runtimeUpdated(payload)
              : payload,
          objectID: 'process-record',
          expectedRevision: current?.operation.envelope.revision,
          signingKey: signer,
          checkpoint: (point) async {
            if (action == 'mutate' && point == stage) {
              await File(
                '${root.path}/ready',
              ).writeAsString(point, flush: true);
              while (true) {
                await Future<void>.delayed(const Duration(seconds: 60));
              }
            }
          },
        );
      }
    }
    final records = await owner.runtimeRecords(),
        outbox = await owner.pendingOperations();
    final current = records.isEmpty ? null : records.single;
    final sequence = current?.operation.authorSequence ?? 0;
    if (outbox.length != sequence ||
        (current != null && !outbox.contains(current.operation))) {
      throw StateError('projection outbox publication differs');
    }
    if (current != null &&
        !current.operation.envelope.tombstone &&
        current.payload !=
            (sequence == 2 && mutation == 'update'
                ? runtimeUpdated(payload)
                : payload)) {
      throw StateError('payload mismatch');
    }
    stdout.writeln(
      jsonEncode({
        'status': (await owner.observation())['status'],
        'key_epoch': (await owner.observation())['key_epoch'],
        'records': records.length,
        'outbox': outbox.length,
        'sequence': sequence,
        'lamport': current?.operation.lamport ?? 0,
        'tombstone': current?.operation.envelope.tombstone ?? false,
        'parent_present': current?.operation.envelope.parentRevision != null,
        'recovered': recovered,
        'recovery_fenced': fencedDuringRecovery,
      }),
    );
  } catch (_) {
    stderr.writeln('C29_RUNTIME_HELPER_FAILED');
    exitCode = 1;
  }
}
