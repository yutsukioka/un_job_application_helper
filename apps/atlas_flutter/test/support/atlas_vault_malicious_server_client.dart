import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';

Map<String, Object?> object(dynamic value) =>
    Map<String, Object?>.from(value as Map);
dynamic sorted(dynamic value) {
  if (value is Map) {
    final keys = value.keys.cast<String>().toList()..sort();
    return {for (final key in keys) key: sorted(value[key])};
  }
  if (value is List) return value.map(sorted).toList();
  return value;
}

List<int> canonical(dynamic value) => utf8.encode(jsonEncode(sorted(value)));
Future<String> digest(dynamic value) async => (await Sha256().hash(
  canonical(value),
)).bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

Future<void> main(List<String> args) async {
  final mode = args[0], directory = args[1];
  final plan = jsonDecode(File(args[2]).readAsStringSync()) as Map;
  final context = plan['context'] as Map, results = <String, Object?>{};
  for (final entry in (plan['scenarios'] as Map).entries) {
    final name = entry.key as String, scenario = entry.value as Map;
    final root = Directory('$directory/$name')..createSync(recursive: true);
    final key = Uint8List.fromList(
      (await Sha256().hash(utf8.encode('c24-synthetic-client-storage'))).bytes,
    );
    final c = AtlasVaultGuardedSyncState(
      file: File('${root.path}/accepted-recovery.state'),
      encryptionKey: key,
      accountId: context['account_id'] as String,
      vaultId: context['vault_id'] as String,
      collectionId: context['collection_id'] as String,
      keyEpoch: context['key_epoch'] as int,
      trustedSigner: base64Decode(plan['public_b64'] as String),
    );
    final inbox = AtlasVaultDurableEncryptedInbox(
      File('${root.path}/inbox.state'),
      encryptionKey: key,
    );
    final outbox = AtlasVaultDurableEncryptedOutbox(
      File('${root.path}/outbox.state'),
      encryptionKey: key,
    );
    if (mode == 'prepare') await c.initialize();
    final categories = <String>[], before = await c.checkpoint();
    final actions = mode == 'prepare'
        ? scenario['baseline'] as List
        : mode == 'attack'
        ? scenario['attack'] as List
        : [];
    for (final action in actions) {
      Future<void> interrupt(String point) async {
        if (action['stop_after'] == point) {
          File(args[3]).writeAsBytesSync(canonical({'interrupted_after': point}), flush: true);
          while (true) { await Future<void>.delayed(const Duration(seconds: 60)); }
        }
      }
      try {
        if (action['peer'] != null) {
          final peer =
              jsonDecode(File(action['peer'] as String).readAsStringSync())
                  as Map;
          await c.compareEvidence(
            (peer[name]['history'] as List).map(object).toList(),
          );
          categories.add('COMPATIBLE_PREFIX_NOT_FRESHNESS');
        } else {
          final p = plan['packets'][action['packet']] as Map;
          final accepted = await c.ingest(
            object(p['view']),
            (p['registry'] as List).map(object).toList(),
            object(p['collection']),
            base64Decode(p['opaque_b64'] as String),
          );
          await interrupt('admission');
          if (accepted) {
            final op = AtlasVaultEncryptedPatchOperation.fromJson(
              object(p['operation']),
            );
            await outbox.enqueue(op);
            await interrupt('outbox');
            await inbox.stagePage(
              expectedCursor: await inbox.readCursor(),
              nextCursor: p['view']['root'] as String,
              operations: [op],
            );
            await interrupt('inbox');
            while (await inbox.applyNext((_) async {}) != null) {}
            await interrupt('receipt');
            await outbox.confirmRemoteAcceptance(op.operationId);
          }
          categories.add(accepted ? 'ACCEPTED' : 'IDEMPOTENT');
        }
      } on AtlasVaultStateViewException catch (error) {
        categories.add(error.code);
      }
    }
    var called = false;
    try {
      await c.automaticSync(() async {
        called = true;
      });
    } on AtlasVaultStateViewException catch (error) {
      if (error.code != 'ATLAS_RECOVERY_PENDING') rethrow;
    }
    final checkpoint = await c.checkpoint(), recovery = await c.recovery();
    results[name] = {
      'before': before,
      'checkpoint': checkpoint,
      'recovery': recovery,
      'history': await c.exportEvidence(),
      'evidence': await c.evidence(),
      'categories': categories,
      'automatic_sync_fenced': !called,
      'cursor': await inbox.readCursor(),
      'pending_outbox': (await outbox.pendingOperations()).length,
      'state_sha256': await digest(checkpoint),
      'recovery_sha256': await digest(recovery),
    };
  }
  File(args[3]).writeAsBytesSync(canonical(results), flush: true);
  if (mode != 'inspect') {
    while (true) {
      await Future<void>.delayed(const Duration(seconds: 60));
    }
  }
}
