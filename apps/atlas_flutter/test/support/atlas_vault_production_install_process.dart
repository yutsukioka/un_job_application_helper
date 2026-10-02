import 'dart:convert';
import 'dart:io';

import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:atlas/src/atlas_vault/device_identity.dart';

import 'atlas_vault_runtime_fixtures.dart';
import 'atlas_vault_production_enrollment_fixture.dart';

// Process durability boundary only. Synthetic slots are not a native-custody proof.
Future<void> main(List<String> args) async {
  try {
    final root = Directory(args[0]), point = args[1];
    final slots = Directory('${root.path}/synthetic-slots');
    final binding = AtlasVaultRuntimeBinding(
      root: Directory('${root.path}/owners'),
      loadKey: (id) async {
        final f = File('${slots.path}/$id');
        return await f.exists() ? f.readAsBytes() : null;
      },
      createKey: (id, key) async {
        await slots.create(recursive: true);
        final f = File('${slots.path}/$id');
        if (await f.exists()) throw StateError('duplicate synthetic slot');
        await f.writeAsBytes(key, flush: true);
      },
    );
    final v = runtimeObject(productionEnrollmentFixture()['delivery_case']);
    final packet = runtimeObject(v['packet']);
    final recipient = await AtlasVaultDeviceIdentity.fromPrivateKeys(
      signingPrivateSeed: runtimeTestKey(90),
      agreementPrivateKey: runtimeTestKey(100),
      createdAt: '2026-01-01T00:00:00Z',
      keyEpoch: 3,
    );
    Future<void> pause() async {
      await File('${root.path}/ready').writeAsString(point, flush: true);
      while (true) {
        await Future<void>.delayed(const Duration(seconds: 60));
      }
    }

    var commits = 0;
    final beforeBound = await File(
      '${binding.directory(RuntimeFixture.vaultID).path}/runtime-binding',
    ).exists();
    await binding.installEnrollment(
      packet,
      pins: runtimeObject(v['pins']),
      trustedSigner: base64Decode(v['trusted_signer_b64']! as String),
      recipient: recipient,
      beforePublish: () async {
        commits++;
        if (point == 'before-owner' && commits == 1 ||
            point == 'before-binding' && commits == 2) {
          await pause();
        }
      },
    );
    if (point == 'after-binding') await pause();
    final session = await binding.open(RuntimeFixture.vaultID);
    final records = await session.read();
    if (records.length != 3 ||
        records.where((r) => r.payload == null).length != 1 ||
        (await session.owner.pendingOperations()).isNotEmpty) {
      throw StateError('publication mismatch');
    }
    session.close();
    await binding.installEnrollment(
      packet,
      pins: runtimeObject(v['pins']),
      trustedSigner: base64Decode(v['trusted_signer_b64']! as String),
      recipient: recipient,
    );
    stdout.writeln(
      jsonEncode({
        'before_bound': beforeBound,
        'records': 3,
        'tombstones': 1,
        'retry': 'idempotent',
        'status': 'ACTIVE',
      }),
    );
    recipient.destroy();
  } catch (_) {
    stderr.writeln('C30_PRODUCTION_INSTALL_REJECTED');
    exitCode = 1;
  }
}
