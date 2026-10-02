import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/src/atlas_vault/device_identity.dart';
import 'package:atlas/src/atlas_vault/canonical_json.dart';
import 'package:atlas/src/atlas_vault/crypto.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:atlas/src/atlas_vault/private_state_runtime.dart';
import 'package:atlas/src/atlas_vault/pairing_transaction.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_pairing_fakes.dart';
import 'support/atlas_vault_production_enrollment_fixture.dart';
import 'support/atlas_vault_dart_helper_process.dart';
import 'support/atlas_vault_runtime_fixtures.dart';

void main() {
  test(
    'C30 production acknowledgement agrees cross-language and rejects substitution',
    () async {
      final v = runtimeObject(
        runtimeObject(
          jsonDecode(
            await File(
              '../../contracts/sync/test_vectors/atlasvault_production_enrollment_v1.json',
            ).readAsString(),
          ),
        )['delivery_case'],
      );
      final packet = runtimeObject(v['packet']);
      final recipient = await AtlasVaultDeviceIdentity.fromPrivateKeys(
        signingPrivateSeed: runtimeTestKey(90),
        agreementPrivateKey: runtimeTestKey(100),
        createdAt: '2026-01-01T00:00:00Z',
        keyEpoch: 3,
      );
      addTearDown(recipient.destroy);
      final ack = await AtlasVaultEnrollmentDelivery.acknowledge(
        packet,
        'a1' * 32,
        recipient,
      );
      expect(
        await atlasVaultSha256Hex(encodeCanonicalJson(ack)) ==
            v['acknowledgement_sha256'],
        isTrue,
      );
      await AtlasVaultEnrollmentDelivery.verifyAcknowledgement(
        packet,
        'a1' * 32,
        ack,
        recipient.descriptor,
      );
      for (final field in [
        'format',
        'version',
        'delivery_sha256',
        'anchor_root',
        'transcript_sha256',
        'recipient_device_id',
        'signature_b64',
      ]) {
        await expectLater(
          AtlasVaultEnrollmentDelivery.verifyAcknowledgement(
            packet,
            'a1' * 32,
            {...ack, field: field == 'version' ? 2 : 'invalid'},
            recipient.descriptor,
          ),
          throwsA(anything),
        );
      }
    },
  );
  for (final point in ['before-owner', 'before-binding', 'after-binding']) {
    test(
      'C30 runtime publication SIGKILL $point resumes without partial ACTIVE state',
      () async {
        final root = await Directory.systemTemp.createTemp('c30-install-kill-');
        addTearDown(() => root.delete(recursive: true));
        final worker = await startAtlasVaultDartHelper(
          'test/support/atlas_vault_production_install_process.dart',
          [root.path, point],
        );
        final out = worker.stdout.drain<void>(),
            err = worker.stderr.drain<void>();
        try {
          final ready = File('${root.path}/ready'),
              limit = DateTime.now().add(const Duration(seconds: 40));
          while (!await ready.exists() && DateTime.now().isBefore(limit)) {
            await Future<void>.delayed(const Duration(milliseconds: 50));
          }
          expect(await ready.exists(), isTrue);
          expect(worker.kill(ProcessSignal.sigkill), isTrue);
          await worker.exitCode.timeout(const Duration(seconds: 10));
          await out;
          await err;
          final restarted = await startAtlasVaultDartHelper(
            'test/support/atlas_vault_production_install_process.dart',
            [root.path, 'resume'],
          );
          final output = restarted.stdout.transform(utf8.decoder).join(),
              errors = restarted.stderr.drain<void>();
          expect(
            await restarted.exitCode.timeout(const Duration(seconds: 40)),
            0,
          );
          final result = runtimeObject(jsonDecode(await output));
          await errors;
          expect(result['before_bound'], point == 'after-binding');
          expect(result['status'], 'ACTIVE');
          expect(result['records'], 3);
          expect(result['tombstones'], 1);
          expect(result['retry'], 'idempotent');
        } finally {
          worker.kill(ProcessSignal.sigkill);
          await worker.exitCode;
        }
      },
      timeout: const Timeout(Duration(seconds: 120)),
    );
  }
  for (final scenario in [
    'normal',
    'sender-retry',
    'recipient-retry',
    'denied',
    'wrapper-substitution',
    'recipient-substitution',
    'context-substitution',
    'missing-preimage',
    'timeout',
    'cancel-import',
  ]) {
    for (final retained in [false, true]) {
      test(
        'C30 production ceremony installs, acknowledges and reopens independent nonempty state retained=$retained scenario=$scenario',
        () async {
          final root = await Directory.systemTemp.createTemp(
            'c30-production-ceremony-',
          );
          addTearDown(() => root.delete(recursive: true));
          final fixture = RuntimeFixture();
          final slots = List.generate(2, (_) => <String, Uint8List>{});
          addTearDown(() {
            for (final device in slots) {
              for (final key in device.values) {
                key.fillRange(0, key.length, 0);
              }
            }
          });
          final bindings = List.generate(
            2,
            (i) => AtlasVaultRuntimeBinding(
              root: Directory('${root.path}/device-$i'),
              loadKey: (id) async => slots[i][id] == null
                  ? null
                  : Uint8List.fromList(slots[i][id]!),
              createKey: (id, key) async {
                if (slots[i].containsKey(id)) {
                  throw StateError('duplicate synthetic slot');
                }
                slots[i][id] = Uint8List.fromList(key);
              },
            ),
          );
          final owner = retained
              ? await productionEnrollmentSender(
                  bindings[0].directory(RuntimeFixture.vaultID),
                )
              : await fixture.initialize(
                  bindings[0].directory(RuntimeFixture.vaultID),
                );
          final payload = runtimePayloads().values.first;
          if (!retained) {
            await owner.commitRuntimeRecord(
              payload: payload,
              objectID: 'ceremony-record',
              signingKey: await fixture.signer(),
            );
            final dead = await owner.commitRuntimeRecord(
              payload: payload,
              objectID: 'ceremony-deleted',
              signingKey: await fixture.signer(),
            );
            await owner.commitRuntimeRecord(
              payload: null,
              objectID: 'ceremony-deleted',
              expectedRevision: dead.envelope.revision,
              signingKey: await fixture.signer(),
            );
          }
          await bindings[0].provision(
            owner: owner,
            signingSeed: runtimeTestKey(10),
          );
          final runtimes = List.generate(
            2,
            (i) => AtlasVaultPrivateStateRuntime(
              secureKeyStore: AtlasVaultPairingMemorySecureKeyStore(),
              localStoreIO: AtlasVaultPairingMemoryLocalStore(),
              epochSessionFactory: bindings[i].open,
              epochEnrollmentInstaller: bindings[i].installEnrollment,
            ),
          );
          addTearDown(() async {
            for (final r in runtimes) {
              await r.deactivate();
            }
          });
          expect(
            await runtimes[0].activateExisting(RuntimeFixture.vaultID),
            AtlasVaultActivationResult.activated,
          );
          final mailbox = AtlasVaultPairingMailbox();
          final transports = List.generate(
            2,
            (_) => AtlasVaultPairingMemoryTransport(mailbox),
          );
          var now = DateTime.utc(2026, 9, 9), elapsed = Duration.zero;
          final identities = List.generate(
            2,
            (_) => AtlasVaultPairingMemoryIdentityStore(),
          );
          final registries = List.generate(
            2,
            (_) => AtlasVaultPairingMemoryRegistryStore(),
          );
          final journals = [
            AtlasVaultPairingMemoryTransactionStore(
              failReplaceStage: AtlasVaultPairingStage.deliveryCreated,
              failReplaceCount: scenario == 'sender-retry' ? 1 : 0,
            ),
            AtlasVaultPairingMemoryTransactionStore(
              failReplaceStage: AtlasVaultPairingStage.runtimeActivated,
              failReplaceCount: scenario == 'recipient-retry' ? 1 : 0,
            ),
          ];
          if (retained) {
            await registries[0].create(productionEnrollmentDescriptors());
          }
          final devices = List.generate(
            2,
            (i) => AtlasVaultTrustedPairingCoordinator(
              identityStore: identities[i],
              registryStore: registries[i],
              replayStore: AtlasVaultPairingMemoryReplayStore(),
              transactionStore: journals[i],
              stageStore: AtlasVaultPairingMemoryStageStore(),
              artifactTransport: transports[i],
              runtime: runtimes[i],
              selectedVaultStore: AtlasVaultPairingMemorySelectedVaultStore(),
              cleanInstallProbe: () async => i == 0
                  ? AtlasVaultPairingCleanInstallDisposition.existingVault
                  : AtlasVaultPairingCleanInstallDisposition.clean,
              authorizeKeyRelease: (_) async => scenario != 'denied',
              now: () => now,
              monotonicNow: () => elapsed,
              identityGenerator: () => AtlasVaultDeviceIdentity.fromPrivateKeys(
                signingPrivateSeed: runtimeTestKey(i == 0 ? 10 : 90),
                agreementPrivateKey: runtimeTestKey(i == 0 ? 20 : 100),
                createdAt: '2026-01-01T00:00:00Z',
                keyEpoch: 3,
              ),
            ),
          );
          addTearDown(() async {
            for (final d in devices) {
              await d.stop();
            }
            for (final d in identities) {
              await d.deletePrimaryIdentity();
            }
          });
          for (final d in devices) {
            await d.createDeviceIdentity();
          }
          final a = devices[0], b = devices[1];
          expect(
            (await a.createPairingOffer()).disposition,
            AtlasVaultTrustedPairingDisposition.offerReady,
          );
          await a.savePairingOffer();
          await b.importPairingOffer();
          await b.savePairingAcceptance();
          final left = await a.importPairingAcceptance(),
              right = await b.inspect();
          expect(left.sas != null && left.sas == right.sas, isTrue);
          await b.confirmCodesMatch(
            expectedTranscriptSha256: right.transcriptSha256,
          );
          final before = jsonEncode(await owner.observation());
          var prepared = await a.confirmCodesMatch(
            expectedTranscriptSha256: left.transcriptSha256,
          );
          if (scenario == 'denied') {
            expect(
              prepared.disposition,
              isNot(AtlasVaultTrustedPairingDisposition.deliveryReady),
            );
            expect(jsonEncode(await owner.observation()) == before, isTrue);
            expect(
              await bindings[1].directory(RuntimeFixture.vaultID).exists(),
              isFalse,
            );
            expect(runtimes[1].isActive, isFalse);
            return;
          }
          if (scenario == 'sender-retry') {
            expect(
              prepared.disposition,
              AtlasVaultTrustedPairingDisposition.recoveryRequired,
            );
            prepared = await a.resumePairing();
          }
          expect(
            prepared.disposition,
            AtlasVaultTrustedPairingDisposition.deliveryReady,
          );
          expect(
            (await a.saveKeyDelivery()).disposition,
            AtlasVaultTrustedPairingDisposition.deliverySaved,
          );
          if (scenario.endsWith('substitution') ||
              scenario == 'missing-preimage') {
            final altered = runtimeObject(
              jsonDecode(utf8.decode(mailbox.bytes!)),
            );
            final packet =
                (altered['payload']! as Map)['enrollment_delivery']! as Map;
            final anchor = (packet['anchor']! as Map)['checkpoint']! as Map;
            switch (scenario) {
              case 'wrapper-substitution':
                (packet['deliveries']! as List).first['ciphertext_b64'] =
                    base64Encode(Uint8List(48));
              case 'recipient-substitution':
                anchor['recipient_device_id'] = 'different-recipient';
              case 'context-substitution':
                anchor['registry_generation'] = 1;
              case 'missing-preimage':
                packet['historical_authority'] = null;
            }
            // Current-only packets have no authority witness: remove their covered projection instead.
            if (!retained && scenario == 'missing-preimage') {
              packet['opaque_b64'] = base64Encode(utf8.encode('{}'));
            }
            mailbox.bytes = encodeCanonicalJson(altered);
          }
          if (scenario == 'timeout') {
            now = now.add(const Duration(hours: 1));
            elapsed = const Duration(hours: 1);
          }
          if (scenario == 'cancel-import') transports[1].cancelNextPick = true;
          var imported = await b.importKeyDelivery();
          if (scenario.endsWith('substitution') ||
              scenario == 'missing-preimage' ||
              scenario == 'timeout' ||
              scenario == 'cancel-import') {
            expect(
              imported.disposition,
              isNot(AtlasVaultTrustedPairingDisposition.acknowledgementReady),
            );
            expect(runtimes[1].isActive, isFalse);
            expect(
              await bindings[1].directory(RuntimeFixture.vaultID).exists(),
              isFalse,
            );
            expect(slots[1].isEmpty, isTrue);
            return;
          }
          if (scenario == 'recipient-retry') {
            expect(
              imported.disposition,
              AtlasVaultTrustedPairingDisposition.recoveryRequired,
            );
            imported = await b.resumePairing();
          }
          expect(
            imported.disposition,
            AtlasVaultTrustedPairingDisposition.acknowledgementReady,
          );
          expect(
            (await b.savePairingAcknowledgement()).disposition,
            AtlasVaultTrustedPairingDisposition.completed,
          );
          expect(
            (await a.importPairingAcknowledgement()).disposition,
            AtlasVaultTrustedPairingDisposition.completed,
          );
          await runtimes[1].deactivate();
          final reopened = await bindings[1].open(RuntimeFixture.vaultID);
          final records = await reopened.read();
          expect(records.length, retained ? 3 : 2);
          if (retained) {
            for (final id in [
              'retained-active-author',
              'retained-revoked-author',
            ]) {
              expect(
                records
                        .singleWhere((r) => r.operation.envelope.objectId == id)
                        .payload ==
                    runtimePayloads()['profile_snippet'],
                isTrue,
              );
            }
            expect(
              records
                      .singleWhere(
                        (r) =>
                            r.operation.envelope.objectId == 'terminal-delete',
                      )
                      .payload ==
                  null,
              isTrue,
            );
          } else {
            expect(
              records
                      .singleWhere(
                        (r) =>
                            r.operation.envelope.objectId == 'ceremony-record',
                      )
                      .payload ==
                  payload,
              isTrue,
            );
            expect(
              records
                      .singleWhere(
                        (r) =>
                            r.operation.envelope.objectId == 'ceremony-deleted',
                      )
                      .payload ==
                  null,
              isTrue,
            );
          }
          reopened.close();
        },
      );
    }
  }
  test(
    'C30 runtime enrollment installs a nonempty authenticated P5 projection',
    () async {
      final root = await Directory.systemTemp.createTemp(
        'c30-production-enrollment-',
      );
      addTearDown(() => root.delete(recursive: true));
      final fixture = RuntimeFixture();
      final sender = await fixture.initialize(Directory('${root.path}/sender'));
      final issuer = await AtlasVaultDeviceIdentity.fromPrivateKeys(
        signingPrivateSeed: runtimeTestKey(10),
        agreementPrivateKey: runtimeTestKey(20),
        createdAt: '2026-01-01T00:00:00Z',
        keyEpoch: 3,
      );
      final recipient = await AtlasVaultDeviceIdentity.fromPrivateKeys(
        signingPrivateSeed: runtimeTestKey(90),
        agreementPrivateKey: runtimeTestKey(100),
        createdAt: '2026-01-01T00:00:00Z',
        keyEpoch: 3,
      );
      addTearDown(issuer.destroy);
      addTearDown(recipient.destroy);
      final payload = runtimePayloads().values.first;
      await sender.commitRuntimeRecord(
        payload: payload,
        objectID: 'retained-object',
        signingKey: await fixture.signer(),
      );
      final deleted = await sender.commitRuntimeRecord(
        payload: payload,
        objectID: 'deleted-object',
        signingKey: await fixture.signer(),
      );
      await sender.commitRuntimeRecord(
        payload: null,
        objectID: 'deleted-object',
        expectedRevision: deleted.envelope.revision,
        signingKey: await fixture.signer(),
      );
      final expected = await sender.enrollmentContext();
      final packet = await sender.prepareRuntimeEnrollment(
        issuer: issuer,
        target: await recipient.signDescriptor(),
        confirmedTranscript: 'ab' * 32,
        expectedContext: expected,
        signedDescriptors: const [],
        beforePublish: () async {},
      );
      final checkpoint = runtimeObject(
        runtimeObject(packet['anchor'])['checkpoint'],
      );
      final pins = <String, Object?>{
        'anchor_root': checkpoint['root'],
        'recipient_device_id': recipient.deviceId,
        'confirmed_transcript': 'ab' * 32,
        'current_context': await sender.enrollmentContext(),
      };
      final receiver = await AtlasVaultEnrollmentDelivery.installRuntime(
        Directory('${root.path}/recipient'),
        packet,
        pins: pins,
        trustedSigner: issuer.signingPublicKey,
        recipient: recipient,
        agreementPrivateKey: runtimeTestKey(100),
        storageKey: runtimeTestKey(111),
      );
      final records = await receiver.runtimeRecords();
      expect(records.length, 2);
      expect(
        records
                .singleWhere(
                  (r) => r.operation.envelope.objectId == 'retained-object',
                )
                .payload ==
            payload,
        isTrue,
      );
      expect(
        records
                .singleWhere(
                  (r) => r.operation.envelope.objectId == 'deleted-object',
                )
                .payload ==
            null,
        isTrue,
      );
      final again = await sender.prepareRuntimeEnrollment(
        issuer: issuer,
        target: await recipient.signDescriptor(),
        confirmedTranscript: 'ab' * 32,
        expectedContext: expected,
        signedDescriptors: const [],
        beforePublish: () async {},
      );
      expect(jsonEncode(again) == jsonEncode(packet), isTrue);
      final slots = <String, Uint8List>{};
      addTearDown(() {
        for (final key in slots.values) {
          key.fillRange(0, key.length, 0);
        }
      });
      final binding = AtlasVaultRuntimeBinding(
        root: Directory('${root.path}/protected-recipient'),
        loadKey: (id) async =>
            slots[id] == null ? null : Uint8List.fromList(slots[id]!),
        createKey: (id, key) async {
          if (slots.containsKey(id)) {
            throw StateError('duplicate synthetic slot');
          }
          slots[id] = Uint8List.fromList(key);
        },
      );
      for (var retry = 0; retry < 2; retry++) {
        await binding.installEnrollment(
          packet,
          pins: pins,
          trustedSigner: issuer.signingPublicKey,
          recipient: recipient,
        );
        final session = await binding.open(RuntimeFixture.vaultID);
        expect((await session.read()).length, 2);
        session.close();
      }
    },
  );
}
