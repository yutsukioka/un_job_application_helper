import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';

final vectors =
    jsonDecode(
          File(
            '../../contracts/sync/test_vectors/atlasvault_state_commitment_vectors_v1.json',
          ).readAsStringSync(),
        )
        as Map<String, dynamic>;
final states = (vectors['states'] as List).cast<Map<String, dynamic>>();
final publicKey = base64Decode(vectors['signing_public_b64'] as String);
final key = Uint8List.fromList(List.generate(32, (i) => i));

AtlasVaultRollbackTracker tracker(File file) => AtlasVaultRollbackTracker(
  file: file,
  encryptionKey: key,
  collectionId: 'collection_c21',
  trustedSigner: publicKey,
);

final class HostileServer {
  HostileServer(Map<String, dynamic> state, {bool omit = false})
    : commitment = Map<String, Object?>.from(state['commitment'] as Map),
      body = base64Decode(state['opaque_b64'] as String) {
    if (omit) body = Uint8List.sublistView(body, 0, body.length - 1);
  }
  Map<String, Object?> commitment;
  Uint8List body;
  Future<bool> serve(AtlasVaultRollbackTracker client) =>
      client.accept(commitment, body);
}

void main() {
  late Directory directory;
  setUp(() async {
    directory = await Directory.systemTemp.createTemp('c21-');
  });
  tearDown(() async {
    await directory.delete(recursive: true);
  });

  test('shared roots and signatures are byte-exact', () async {
    final signer = await Ed25519().newKeyPairFromSeed(key);
    for (final state in states) {
      final c = state['commitment'] as Map<String, dynamic>;
      final signed = await AtlasVaultSignedStateCommitment.sign(
        base64Decode(state['opaque_b64'] as String),
        collectionId: c['collection_id'] as String,
        sequence: c['sequence'] as int,
        previousRoot: c['previous_root'] as String,
        signingKey: signer,
      );
      expect(signed.toJson(), c);
    }
  });

  test('Swift signatures share roots and duplicate identity', () async {
    final client = tracker(File('${directory.path}/anchor'));
    await client.initialize();
    for (final state in states) {
      final server = HostileServer(state);
      server.commitment['signature_b64'] = state['swift_signature_b64'];
      expect(await server.serve(client), isTrue);
      expect(await HostileServer(state).serve(client), isFalse);
    }
  });

  for (final attack in [
    'replay',
    'old_snapshot',
    'omission',
    'gap',
    'non_chaining',
    'same_sequence',
  ]) {
    test('hostile server $attack rejected after durable observation', () async {
      final file = File('${directory.path}/anchor');
      var client = tracker(file);
      await client.initialize();
      await HostileServer(states[0]).serve(client);
      if (attack != 'gap') await HostileServer(states[1]).serve(client);
      final before = await file.readAsBytes();
      client = tracker(file);
      final server = HostileServer(
        states[attack == 'replay' || attack == 'old_snapshot' ? 0 : 2],
        omit: attack == 'omission',
      );
      if (attack == 'non_chaining' || attack == 'same_sequence') {
        server.commitment = (await AtlasVaultSignedStateCommitment.sign(
          server.body,
          collectionId: 'collection_c21',
          sequence: attack == 'non_chaining' ? 3 : 2,
          previousRoot: 'f' * 64,
          signingKey: await Ed25519().newKeyPairFromSeed(key),
        )).toJson();
      }
      await expectLater(
        server.serve(client),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
      expect(await file.readAsBytes(), before);
      expect((await client.checkpoint())['sequence'], attack == 'gap' ? 1 : 2);
    });
  }

  test(
    'duplicate, encrypted anchor and missing/corrupt state fail closed',
    () async {
      final file = File('${directory.path}/anchor');
      final client = tracker(file);
      await expectLater(
        HostileServer(states[0]).serve(client),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
      await client.initialize();
      for (final state in states) {
        expect(await HostileServer(state).serve(client), isTrue);
      }
      final before = await file.readAsString();
      expect(await HostileServer(states[2]).serve(tracker(file)), isFalse);
      expect(await file.readAsString(), before);
      expect(before, isNot(contains('collection_c21')));
      expect(before, isNot(contains((states[2]['commitment'] as Map)['root'])));
      await expectLater(
        client.initialize(),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
      await file.writeAsString('corrupt');
      await expectLater(
        client.checkpoint(),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
      await file.delete();
      await expectLater(
        client.checkpoint(),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
    },
  );

  test('malformed, signature, wrong key and scope rejected', () async {
    final file = File('${directory.path}/anchor');
    final client = tracker(file);
    await client.initialize();
    for (final mutation in <Map<String, Object?>>[
      {'sequence': true},
      {'sequence': 0},
      {'sequence': 1.0},
      {'sequence': 9007199254740992},
      {'previous_root': 'F' * 64},
      {'signature_b64': base64Encode(Uint8List(64))},
      {'collection_id': 'other'},
      {'plaintext': 'forbidden'},
    ]) {
      final server = HostileServer(states[0]);
      server.commitment.addAll(mutation);
      await expectLater(
        server.serve(client),
        throwsA(isA<AtlasVaultEncryptedPatchException>()),
      );
    }
    final wrongKey = AtlasVaultRollbackTracker(
      file: file,
      encryptionKey: Uint8List(32),
      collectionId: 'collection_c21',
      trustedSigner: publicKey,
    );
    await expectLater(
      wrongKey.checkpoint(),
      throwsA(isA<AtlasVaultEncryptedPatchException>()),
    );
    expect((await client.checkpoint())['sequence'], 0);
  });
}
