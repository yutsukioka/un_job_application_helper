import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';
import 'package:atlas/src/atlas_vault/epoch_rotation.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';

Map<String, Object?> object(Object? value) =>
    Map<String, Object?>.from(value! as Map);
List<Map<String, Object?>> rows(Object? value) =>
    (value! as List).map(object).toList();

Future<void> main(List<String> args) async {
  final dir = Directory(args[0]), stage = args[1];
  final v = object(
    jsonDecode(
      File(
        '../../contracts/sync/test_vectors/atlasvault_activation_v1.json',
      ).readAsStringSync(),
    ),
  );
  final record = object(v['record']),
      proof = object(object(v['record'])['proof']),
      plan = object(object(object(v['record'])['proof'])['plan']);
  final vault = AtlasVaultEpochVault(
    dir,
    storageKey: Uint8List.fromList(List.filled(32, 50)),
    deviceID: (v['device_ids']! as List)[0] as String,
    registry: rows(proof['registry']),
    accountID: plan['account_id']! as String,
    vaultID: 'vault-c26',
    keyEpoch: 3,
    stateRoot: plan['state_root']! as String,
  );
  final signer = await Ed25519().newKeyPairFromSeed(List.filled(32, 10));
  if (!await File('${dir.path}/activation').exists()) {
    await dir.create(recursive: true);
    final h = AtlasVaultGuardedSyncState(
      file: File('${dir.path}/initial-history'),
      encryptionKey: Uint8List.fromList(List.filled(32, 60)),
      accountId: plan['account_id']! as String,
      vaultId: 'vault-c26',
      collectionId: 'collection-c26',
      keyEpoch: 3,
      trustedSigner: Uint8List.fromList(
        (await signer.extractPublicKey()).bytes,
      ),
    );
    await h.initialize();
    await h.ingest(
      object(v['initial_view']),
      rows(v['initial_registry']),
      object(v['initial_collection']),
      base64Decode(v['opaque_state_b64']! as String),
    );
    await vault.initialize({
      3: Uint8List.fromList(List.filled(32, 30)),
    }, history: h);
  }
  Future<void> point(String value) async {
    if (value == stage) {
      await File('${dir.path}/ready').writeAsString(value, flush: true);
      while (true) {
        await Future<void>.delayed(const Duration(seconds: 60));
      }
    }
  }

  if (stage == 'observe') {
    final state = await vault.observation();
    if (state['key_epoch'] == 3 &&
        (await vault.pendingActivation())?['root'] != proof['root']) {
      throw StateError('Pending request not retained');
    }
    var fenced = false;
    try {
      await vault.seal(
        'patch',
        Uint8List.fromList(List.filled(32, 7)),
        objectID: 'probe',
        revision: 'probe',
        signingKey: signer,
      );
    } on AtlasVaultRotationException catch (e) {
      fenced = e.code == 'ATLAS_ACTIVATION_PENDING';
    }
    stdout.writeln(jsonEncode({...state, 'writes_fenced': fenced}));
    return;
  }
  if (stage == 'prepared') {
    await vault.prepareRotation(proof);
    await point('prepared');
    return;
  }
  if ((await vault.observation())['status'] == 'ACTIVE' &&
      (await vault.observation())['key_epoch'] == 3) {
    await vault.beginActivation(proof);
  }
  if (stage == 'missing_delivery') {
    try {
      await vault.acceptRotation(
        proof,
        acceptedRecord: record,
        agreementPrivateKey: Uint8List.fromList(List.filled(32, 99)),
      );
    } on AtlasVaultRotationException {
      await point(stage);
    }
    throw StateError('Missing delivery was not rejected');
  }
  await vault.acceptRotationForTesting(
    proof,
    acceptedRecord: record,
    agreementPrivateKey: Uint8List.fromList(List.filled(32, 20)),
    checkpoint: point,
  );
  stdout.writeln(jsonEncode(await vault.observation()));
}
