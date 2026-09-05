import 'dart:convert';
import 'dart:io';

import 'package:atlas/src/atlas_vault/device_enrollment.dart';
import 'package:atlas/src/atlas_vault/device_delivery.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_runtime_fixtures.dart';
import 'support/atlas_vault_vector_loader.dart';

void main() {
  test(
    'C30 independent epoch stores admit the same signed additive member',
    () async {
      final root = await Directory.systemTemp.createTemp(
        'atlas-c30-enrollment-',
      );
      addTearDown(() => root.delete(recursive: true));
      final f = RuntimeFixture();
      final clients = [
        await f.initialize(Directory('${root.path}/a')),
        await f.initialize(Directory('${root.path}/b'), index: 1),
      ];
      final v = runtimeObject(
        jsonDecode(
          File(
            '${atlasVaultRepositoryRoot().path}/contracts/sync/test_vectors/atlasvault_device_enrollment_v1.json',
          ).readAsStringSync(),
        ),
      );
      final c = await clients.first.enrollmentContext();
      final registry = await clients.first.enrollmentRegistry();
      final unsigned = <String, Object?>{...runtimeObject(v['proof']), ...c}
        ..remove('root')
        ..remove('signature_b64');
      unsigned.addAll({
        'issuer_device_id': f.deviceID(0),
        'next_registry_generation': (c['registry_generation'] as int) + 1,
        'prior_registry_root': AtlasVaultRevocation.registryRoot(registry),
        'resulting_registry_root': AtlasVaultRevocation.registryRoot([
          ...registry,
          runtimeObject(v['target']),
        ]),
      });
      final proof = await AtlasVaultDeviceEnrollment.create(
        unsigned,
        registry: registry,
        context: c,
        confirmedTranscript: unsigned['transcript_sha256'] as String,
        status: 'ACTIVE',
        signingKey: await f.signer(),
      );
      final transcript = proof['transcript_sha256'] as String;
      final before = await clients.first.observation();
      await expectLater(
        clients.first.acceptEnrollment(proof, confirmedTranscript: '00' * 32),
        throwsException,
      );
      expect(await clients.first.observation(), before);
      for (var i = 0; i < 2; i++) {
        expect(
          await clients[i].acceptEnrollment(
            proof,
            confirmedTranscript: transcript,
          ),
          isTrue,
        );
        expect(
          await clients[i].acceptEnrollment(
            proof,
            confirmedTranscript: transcript,
          ),
          isFalse,
        );
        final reopened = f.owner(
          Directory('${root.path}/${i == 0 ? 'a' : 'b'}'),
          i,
        );
        expect((await reopened.enrollmentContext())['registry_generation'], 5);
        expect((await reopened.observation())['key_epoch'], 4);
        expect(
          (await reopened.observation())['recipients'],
          contains(proof['target_device_id']),
        );
        expect(await reopened.observation(), await clients[i].observation());
      }
      expect(
        (await clients[0].observation())['registry_root'],
        (await clients[1].observation())['registry_root'],
      );
      final publication = await clients[0].runtimePublication(
        signingKey: await f.signer(),
      );
      expect(
        runtimeObject(publication['view'])['registry_root'],
        proof['resulting_registry_root'],
      );
      expect(jsonEncode(f.record), jsonEncode(RuntimeFixture().record));
      final packet = await AtlasVaultDeviceDelivery.create(
        f.record,
        recipientDeviceID: f.deviceID(0),
        issuerDeviceID: f.deviceID(0),
        signingKey: await f.signer(),
        currentRegistry: await clients[0].enrollmentRegistry(),
        recoveryPending: false,
      );
      expect(
        await clients[0].catchUp(
          [packet],
          currentActivationID: f.record['transition_id'] as String,
          agreementPrivateKey: runtimeTestKey(20),
        ),
        isTrue,
      );
      expect(
        await clients[0].catchUp(
          [packet],
          currentActivationID: f.record['transition_id'] as String,
          agreementPrivateKey: runtimeTestKey(20),
        ),
        isFalse,
      );
      expect(
        (await clients[0].observation())['registry_root'],
        proof['resulting_registry_root'],
      );
      expect(
        (await f
            .owner(Directory('${root.path}/a'))
            .enrollmentContext())['registry_generation'],
        5,
      );
    },
  );
}
