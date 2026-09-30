import 'dart:convert';
import 'dart:io';
import 'dart:async';
import 'dart:typed_data';

import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:atlas/atlas.dart' show AtlasApplicationRecord;
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/src/atlas_vault/epoch_rotation.dart';
import 'package:atlas/src/atlas_vault/payloads.dart';
import 'package:atlas/src/atlas_vault/private_state_runtime.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_runtime_fixtures.dart';
import 'support/atlas_vault_runtime_native_harness.dart';
import 'support/atlas_vault_dart_helper_process.dart';

Matcher runtimeError(String code) => throwsA(
  isA<AtlasVaultRotationException>().having((e) => e.code, 'code', code),
);

Future<Directory> runtimeDirectory() async {
  final directory = await Directory.systemTemp.createTemp('atlas-runtime-c29-');
  addTearDown(() => directory.delete(recursive: true));
  return directory;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final payloads = runtimePayloads();
  runtimeLifecycleTests(payloads.values.first);
  runtimeAdmissionTests(payloads);
  runtimeBindingTests(payloads);
  runtimePrivateStateTests(payloads.values.first);
  runtimeProcessTests();
  runtimeInteropTests(payloads);

  for (final family in payloads.keys) {
    test('runtime $family CRUD, revisions and outbox survive reopen', () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      var owner = await fixture.initialize(root);
      final signer = await fixture.signer(), payload = payloads[family]!;
      final created = await owner.commitRuntimeRecord(
        payload: payload,
        objectID: 'record-c29',
        signingKey: signer,
      );
      owner = fixture.owner(root);
      expect(created.envelope.keyEpoch, 4);
      expect(created.authorSequence, 1);
      expect(created.lamport, 1);
      expect((await owner.runtimeRecords()).single.payload == payload, isTrue);
      expect((await owner.pendingOperations()).single == created, isTrue);
      final updatedPayload = runtimeUpdated(payload);
      final updated = await owner.commitRuntimeRecord(
        payload: updatedPayload,
        objectID: 'record-c29',
        expectedRevision: created.envelope.revision,
        signingKey: signer,
      );
      expect(updated.envelope.parentRevision, created.envelope.revision);
      expect(updated.authorSequence, 2);
      expect(updated.lamport, 2);
      owner = fixture.owner(root);
      expect(
        (await owner.runtimeRecords()).single.payload == updatedPayload,
        isTrue,
      );
      for (final stale in [null, created.envelope.revision]) {
        await expectLater(
          owner.commitRuntimeRecord(
            payload: payload,
            objectID: 'record-c29',
            expectedRevision: stale,
            signingKey: signer,
          ),
          runtimeError('ATLAS_RUNTIME_REVISION_CONFLICT'),
        );
      }
      final deleted = await owner.commitRuntimeRecord(
        payload: null,
        objectID: 'record-c29',
        expectedRevision: updated.envelope.revision,
        signingKey: signer,
      );
      expect(deleted.envelope.tombstone, isTrue);
      expect(deleted.envelope.parentRevision, updated.envelope.revision);
      expect(deleted.authorSequence, 3);
      owner = fixture.owner(root);
      expect((await owner.runtimeRecords()).single.payload, isNull);
      expect(
        (await owner.runtimeRecords()).single.operation == deleted,
        isTrue,
      );
      expect(await owner.pendingOperations(), hasLength(3));
      await expectLater(
        owner.commitRuntimeRecord(
          payload: payload,
          objectID: 'record-c29',
          expectedRevision: deleted.envelope.revision,
          signingKey: signer,
        ),
        runtimeError('ATLAS_RUNTIME_REVISION_CONFLICT'),
      );
      for (final operation in [created, updated, deleted]) {
        await owner.confirmRemoteAcceptance(operation.operationId);
      }
      owner = fixture.owner(root);
      expect(await owner.pendingOperations(), isEmpty);
      expect((await owner.runtimeRecords()).single.payload, isNull);
      final disk = await File('${root.path}/activation').readAsString();
      expect(disk.contains(jsonEncode(payload.toJson())), isFalse);
      expect(disk.contains(base64Encode(runtimeTestKey(50))), isFalse);
      expect(
        (await owner.runtimeRecords()).single.toString(),
        'AtlasVaultRuntimeRecord(<redacted>)',
      );
    });
  }

  test(
    'runtime invalid create delete type and signer leave no partial state',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final owner = await fixture.initialize(root),
          signer = await fixture.signer();
      for (final request in [
        (payload: null, revision: null),
        (payload: payloads.values.first, revision: 'missing-parent'),
      ]) {
        await expectLater(
          owner.commitRuntimeRecord(
            payload: request.payload,
            objectID: 'missing',
            expectedRevision: request.revision,
            signingKey: signer,
          ),
          runtimeError('ATLAS_RUNTIME_REVISION_CONFLICT'),
        );
      }
      await expectLater(
        owner.commitRuntimeRecord(
          payload: payloads.values.first,
          objectID: 'wrong-signer',
          signingKey: await fixture.signer(1),
        ),
        throwsA(isA<AtlasVaultRotationException>()),
      );
      expect(await fixture.owner(root).runtimeRecords(), isEmpty);
      expect(await fixture.owner(root).pendingOperations(), isEmpty);
      final created = await owner.commitRuntimeRecord(
        payload: payloads.values.first,
        objectID: 'type-check',
        signingKey: signer,
      );
      await expectLater(
        owner.commitRuntimeRecord(
          payload: payloads.values.last,
          objectID: 'type-check',
          expectedRevision: created.envelope.revision,
          signingKey: signer,
        ),
        runtimeError('ATLAS_RUNTIME_REVISION_CONFLICT'),
      );
      expect(
        (await fixture.owner(root).runtimeRecords()).single.operation ==
            created,
        isTrue,
      );
      expect(await fixture.owner(root).pendingOperations(), hasLength(1));
    },
  );

  test(
    'runtime signed page accepted atomically and duplicate is idempotent',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final source = await fixture.initialize(Directory('${root.path}/source'));
      final receiverRoot = Directory('${root.path}/receiver');
      final receiver = await fixture.initialize(receiverRoot, index: 1);
      final op = await source.commitRuntimeRecord(
        payload: payloads.values.first,
        objectID: 'accepted',
        signingKey: await fixture.signer(),
      );
      final page = await RuntimeSignedPage.sign(fixture, [op.toJson()]);
      expect(await page.ingest(receiver), 1);
      expect(await page.ingest(receiver), 0);
      final reopened = fixture.owner(receiverRoot, 1);
      expect((await reopened.runtimeRecords()).single.operation == op, isTrue);
      expect(await reopened.pendingOperations(), isEmpty);
      expect((await reopened.observation())['state_root'], page.view['root']);
      expect((await reopened.observation())['sequence'], 2);
    },
  );
}

void runtimeInteropTests(Map<String, AtlasVaultPayloadEnvelope> payloads) {
  final swiftPath = Platform.environment['ATLAS_C29_SWIFT_PAGE'];
  final dartPath = Platform.environment['ATLAS_C29_DART_EXPORT'];
  Future<void> verify(
    RuntimeSignedPage page,
    AtlasVaultEpochVault receiver, {
    bool swiftFixture = false,
  }) async {
    expect(
      await vault.atlasVaultSha256Hex(page.bytes),
      page.collection['state_sha256'],
    );
    expect(await page.ingest(receiver), 5);
    expect(await page.ingest(receiver), 0);
    final records = await receiver.runtimeRecords();
    expect(records, hasLength(5));
    expect(
      records.map((row) => row.payload?.type).toSet(),
      AtlasVaultPayloadType.values.toSet(),
    );
    for (final record in records) {
      if (!swiftFixture) {
        expect(
          record.payload == payloads[record.payload!.type.wireName],
          isTrue,
          reason: 'Dart export must match its shared payload vector',
        );
      } else {
        // These values are the existing RuntimeIntegrationFixture.payloads()
        // in AtlasVaultRuntimeIntegrationTests.swift, not the shared JSON set.
        const expected = {
          'saved_search': {
            'name': 'synthetic-runtime-search',
            'summary': 'test',
          },
          'saved_job': {'job_key': 'synthetic-runtime-job', 'status': 'saved'},
          'application_note': {
            'body': 'synthetic-runtime-note',
            'note_kind': 'general',
          },
          'profile_snippet': {
            'title': 'synthetic-runtime-profile',
            'body': 'test',
          },
          'draft_metadata': {
            'target_system': 'OTHER',
            'document_type': 'cv',
            'generated_document_reference': 'synthetic-runtime-reference',
            'draft_status': 'draft',
          },
        };
        final body = runtimeObject(record.payload!.toJson()['payload']);
        for (final entry in expected[record.payload!.type.wireName]!.entries) {
          expect(
            body[entry.key] == entry.value,
            isTrue,
            reason: 'Swift fixture field ${entry.key} must survive import',
          );
        }
        expect(
          record.payload!.clientCreatedAt == '2026-01-01T00:00:00Z',
          isTrue,
        );
        expect(
          record.payload!.clientUpdatedAt == '2026-01-01T00:00:00Z',
          isTrue,
        );
      }
    }
    expect((await receiver.observation())['state_root'], page.view['root']);
    expect((await receiver.observation())['sequence'], page.view['sequence']);
    expect(await receiver.pendingOperations(), isEmpty);
  }

  test(
    'runtime cross-language imports signed Swift publication',
    () async {
      final exported = runtimeObject(
        jsonDecode(await File(swiftPath!).readAsString()),
      );
      final page = RuntimeSignedPage(
        runtimeObject(exported['view']),
        runtimeRows(exported['registry']),
        runtimeObject(exported['collection']),
        base64Decode(exported['opaque_state_b64']! as String),
        runtimeRows(exported['operations']),
      );
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final receiver = await fixture.initialize(root, index: 1);
      await verify(page, receiver, swiftFixture: true);
      expect((await fixture.owner(root, 1).runtimeRecords()).length, 5);
    },
    skip: swiftPath == null
        ? 'Set ATLAS_C29_SWIFT_PAGE to a concrete Swift export'
        : false,
  );

  test(
    'runtime cross-language exports signed Dart publication',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final owner = await fixture.initialize(Directory('${root.path}/source'));
      for (final entry in payloads.entries) {
        await owner.commitRuntimeRecord(
          payload: entry.value,
          objectID: 'c29-dart-${entry.key}',
          signingKey: await fixture.signer(),
        );
      }
      final exported = {
        ...await owner.runtimePublication(signingKey: await fixture.signer()),
        'registry': await fixture.activeRegistry(),
      };
      final page = RuntimeSignedPage(
        runtimeObject(exported['view']),
        runtimeRows(exported['registry']),
        runtimeObject(exported['collection']),
        base64Decode(exported['opaque_state_b64']! as String),
        runtimeRows(exported['operations']),
      );
      final receiver = await fixture.initialize(
        Directory('${root.path}/receiver'),
        index: 1,
      );
      await verify(page, receiver);
      // The export contains signed ciphertext only. Do not print its body or keys.
      final output = File(dartPath!);
      if (await output.exists()) {
        // Preserve the concrete page already handed to Swift and its receipt hash.
        final saved = runtimeObject(jsonDecode(await output.readAsString()));
        final savedPage = RuntimeSignedPage(
          runtimeObject(saved['view']),
          runtimeRows(saved['registry']),
          runtimeObject(saved['collection']),
          base64Decode(saved['opaque_state_b64']! as String),
          runtimeRows(saved['operations']),
        );
        await verify(
          savedPage,
          await fixture.initialize(
            Directory('${root.path}/saved-receiver'),
            index: 1,
          ),
        );
      } else {
        await output.writeAsString(jsonEncode(exported), flush: true);
      }
    },
    skip: dartPath == null
        ? 'Set ATLAS_C29_DART_EXPORT to the signed-page output path'
        : false,
  );
}

void runtimeProcessTests() {
  test(
    'runtime initial registry binding SIGKILL reopens with no-argument publication',
    () async {
      const script = 'test/support/atlas_vault_runtime_binding_process.dart';
      final root = await runtimeDirectory();
      final child = await startAtlasVaultDartHelper(script, [
        root.path,
        'seed',
      ]);
      final output = child.stdout.drain<void>(),
          errors = child.stderr.drain<void>();
      var exited = false;
      final completion = child.exitCode.then((code) {
        exited = true;
        return code;
      });
      try {
        final ready = File('${root.path}/ready');
        final deadline = DateTime.now().add(const Duration(seconds: 45));
        while (!await ready.exists() &&
            !exited &&
            DateTime.now().isBefore(deadline)) {
          await Future<void>.delayed(const Duration(milliseconds: 25));
        }
        expect(
          await ready.exists(),
          isTrue,
          reason: 'binding process must commit before SIGKILL',
        );
        expect(await ready.readAsString(), 'binding_committed');
        expect(child.kill(ProcessSignal.sigkill), isTrue);
        expect(await completion, isNot(0));
        for (final sequence in [2, 3]) {
          final restarted = await startAtlasVaultDartHelper(script, [
            root.path,
            'publish',
          ]);
          final text = restarted.stdout.transform(utf8.decoder).join();
          final stderr = restarted.stderr.drain<void>();
          try {
            expect(
              await restarted.exitCode.timeout(const Duration(seconds: 45)),
              0,
              reason:
                  'restarted binding helper must publish; output is redacted',
            );
            await stderr;
            final observed = runtimeObject(jsonDecode(await text));
            expect(observed, {
              'status': 'ACTIVE',
              'sequence': sequence,
              'key_epoch': 3,
              'operations': 1,
              'accepted': sequence == 2 ? 1 : 0,
              'outbox': 1,
              'slots': 3,
            });
          } finally {
            restarted.kill(ProcessSignal.sigkill);
            await restarted.exitCode;
            await stderr;
          }
        }
        stdout.writeln(
          'C29 initial binding SIGKILL pid=${child.pid} reopened_publications=2 native_slots=false',
        );
      } finally {
        child.kill(ProcessSignal.sigkill);
        await completion;
        await output;
        await errors;
      }
    },
    tags: ['runtime-process'],
    timeout: const Timeout(Duration(minutes: 3)),
  );

  const helper = 'test/support/atlas_vault_runtime_mutation_process.dart';
  for (final mutation in ['create', 'update', 'delete']) {
    for (final stage in [
      'runtime_staged',
      'before_local_commit',
      'after_recovery_record',
      'after_local_commit',
    ]) {
      test(
        'runtime SIGKILL $mutation at $stage restarts with atomic projection and outbox',
        () async {
          final root = await runtimeDirectory();
          Future<Map<String, Object?>> run(String action) async {
            final child = await startAtlasVaultDartHelper(helper, [
              root.path,
              action,
              mutation,
            ]);
            final output = child.stdout.transform(utf8.decoder).join();
            final errors = child.stderr.drain<void>();
            addTearDown(() async {
              child.kill(ProcessSignal.sigkill);
              await child.exitCode;
            });
            final result = await child.exitCode.timeout(
              const Duration(seconds: 45),
            );
            await errors;
            expect(
              result,
              0,
              reason: 'runtime helper $action must succeed; output is redacted',
            );
            return runtimeObject(jsonDecode(await output));
          }

          expect((await run('seed'))['seeded'], isTrue);
          final child = await startAtlasVaultDartHelper(helper, [
            root.path,
            'mutate',
            mutation,
            stage,
          ]);
          final output = child.stdout.drain<void>(),
              errors = child.stderr.drain<void>();
          var exited = false;
          final completion = child.exitCode.then((code) {
            exited = true;
            return code;
          });
          try {
            final readyFile = File('${root.path}/ready');
            final deadline = DateTime.now().add(const Duration(seconds: 45));
            while (!await readyFile.exists() &&
                !exited &&
                DateTime.now().isBefore(deadline)) {
              await Future<void>.delayed(const Duration(milliseconds: 25));
            }
            final ready = await readyFile.exists();
            expect(
              ready,
              isTrue,
              reason: 'helper must reach $stage before process kill',
            );
            expect(await readyFile.readAsString(), stage);
            expect(child.kill(ProcessSignal.sigkill), isTrue);
            expect(await completion, isNot(0));
            await output;
            await errors;
            final observed = await run('observe');
            final committed =
                stage == 'after_recovery_record' ||
                stage == 'after_local_commit';
            final sequence = mutation == 'create'
                ? (committed ? 1 : 0)
                : (committed ? 2 : 1);
            expect(observed['sequence'], sequence);
            expect(observed['lamport'], sequence);
            expect(observed['outbox'], sequence);
            expect(observed['records'], sequence == 0 ? 0 : 1);
            expect(observed['tombstone'], committed && mutation == 'delete');
            expect(observed['parent_present'], sequence == 2);
            expect(observed['recovered'], stage == 'after_recovery_record');
            expect(
              observed['recovery_fenced'],
              stage == 'after_recovery_record',
            );
            expect(observed['status'], 'ACTIVE');
            expect(observed['key_epoch'], 5);
            final resumed = await run('resume');
            expect(resumed['sequence'], mutation == 'create' ? 1 : 2);
            expect(resumed['outbox'], mutation == 'create' ? 1 : 2);
            expect(resumed['tombstone'], mutation == 'delete');
            final reopened = await run('observe');
            expect(reopened, resumed);
            stdout.writeln(
              'C29 Dart SIGKILL mutation=$mutation stage=$stage pid=${child.pid} atomic=true',
            );
          } finally {
            child.kill(ProcessSignal.sigkill);
            await completion;
            await output;
            await errors;
          }
        },
        tags: ['runtime-process'],
        timeout: const Timeout(Duration(minutes: 3)),
      );
    }
  }
}

void runtimePrivateStateTests(AtlasVaultPayloadEnvelope payload) {
  test(
    'epoch tracker upsert preserves imported identity and commit metadata',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final owner = await fixture.initialize(
        native.binding.directory(RuntimeFixture.vaultID),
      );
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: native.keyStore,
        localStoreIO: native.localStore,
        epochSessionFactory: native.binding.open,
        now: () => DateTime.parse('2026-09-30T12:00:00Z'),
      );
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      await runtime.createRecord(
        AtlasVaultPayloadEnvelope.fromJson({
          'type': 'saved_job',
          'payload_schema': 1,
          'payload': {
            'id': 'imported-job',
            'job_key': 'un:123',
            'status': 'saved',
            'updated_at': '2026-01-01T00:00:00Z',
          },
          'client_created_at': '2026-01-01T00:00:00Z',
          'client_updated_at': '2026-01-01T00:00:00Z',
        }),
      );
      final before = (await runtime.read()).records.single;
      await runtime.saveTrackerRecord(
        AtlasApplicationRecord(id: '', jobKey: 'un:123', status: 'applied'),
      );
      await runtime.deactivate();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      final after = (await runtime.read()).records.single;
      final saved = after.envelope.payload as AtlasSavedJobPayload;
      expect(saved.id, 'imported-job');
      expect(saved.updatedAt, '2026-09-30T12:00:00Z');
      expect(saved.status, 'applied');
      expect(after.recordId, before.recordId);
      expect(after.parentRevision, before.revision);
      expect(after.envelope.clientCreatedAt, before.envelope.clientCreatedAt);
      expect(await owner.pendingOperations(), hasLength(2));
      await runtime.deactivate();
    },
  );

  test(
    'runtime deactivation during owner read never returns a decrypted snapshot',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final owner = await fixture.initialize(
        native.binding.directory(RuntimeFixture.vaultID),
      );
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      await owner.commitRuntimeRecord(
        payload: payload,
        objectID: 'read-race',
        signingKey: await fixture.signer(),
      );
      final runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      // read() yields at actual encrypted-file I/O, while deactivate drains the
      // already-empty mutation queue. No native-channel delay stands in for read.
      final pending = runtime.read();
      final rejected = expectLater(
        pending,
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );
      await runtime.deactivate();
      await rejected;
      expect(runtime.isActive, isFalse);
      expect(runtime.runtimeState, 'LOCKED');
    },
  );

  for (final state in [
    'ACTIVATION_PENDING',
    'CATCH_UP_PENDING',
    'CLEANUP_PENDING',
    'RECOVERY_PENDING',
    'REVOKED',
  ]) {
    test(
      'runtime private factory maps owner $state and never falls back',
      () async {
        final catchUp = state == 'CATCH_UP_PENDING';
        final fixture = RuntimeFixture(catchUp: catchUp),
            native = await runtimeNativeHarness();
        final root = native.binding.directory(RuntimeFixture.vaultID);
        final index = state == 'REVOKED' || catchUp ? 2 : 0;
        final owner = await fixture.initialize(
          root,
          index: index,
          activate: false,
        );
        await native.binding.provision(
          owner: owner,
          signingSeed: runtimeTestKey(10 + index),
          authenticatedRegistry: fixture.initialHistoryRegistry,
        );
        final runtime = native.runtime();
        expect(
          await runtime.activateExisting(RuntimeFixture.vaultID),
          AtlasVaultActivationResult.activated,
        );
        expect(runtime.runtimeState, 'ACTIVE');
        switch (state) {
          case 'ACTIVATION_PENDING':
            await owner.beginActivation(fixture.proof);
          case 'REVOKED':
            await expectLater(
              fixture.accept(owner, index),
              runtimeError('ATLAS_DEVICE_REVOKED'),
            );
          case 'CATCH_UP_PENDING':
            await expectLater(
              owner.catchUp(
                [],
                currentActivationID:
                    fixture.vector['target_activation_id']! as String,
                agreementPrivateKey: runtimeTestKey(22),
              ),
              throwsA(isA<AtlasVaultRotationException>()),
            );
          case 'CLEANUP_PENDING':
            await fixture.accept(owner);
            await expectLater(
              owner.cleanupEpochsForTesting(
                retainEpochs: {3, 4},
                deleteEpoch: (_) async {},
                containsEpoch: (_) async => false,
                checkpoint: (stage) {
                  if (stage == 'cleanup_pending') {
                    throw StateError('synthetic interruption');
                  }
                },
              ),
              throwsA(isA<AtlasVaultRotationException>()),
            );
          case 'RECOVERY_PENDING':
            final unsigned = {...fixture.initialView}
              ..remove('root')
              ..remove('signature_b64');
            unsigned['collection_root'] = 'ab' * 32;
            final fork = await AtlasVaultAuthenticatedStateView.sign(
              unsigned,
              await fixture.signer(),
            );
            await expectLater(
              owner.compareEvidence([fork]),
              throwsA(isA<AtlasVaultRotationException>()),
            );
        }
        await expectLater(
          runtime.read(),
          throwsA(isA<AtlasVaultPrivateStateException>()),
        );
        expect(runtime.runtimeState, state);
        expect(runtime.isActive, isFalse);
        await expectLater(
          runtime.createRecord(payload),
          throwsA(isA<AtlasVaultPrivateStateException>()),
        );
        expect(
          native.calls.any(
            (call) =>
                call.method == 'loadVaultKey' &&
                call.id == RuntimeFixture.vaultID,
          ),
          isFalse,
        );
        expect(native.stores.length, 0);
        expect(await fixture.owner(root, index).pendingOperations(), isEmpty);
        await runtime.deactivate();
        expect(runtime.runtimeState, 'LOCKED');
      },
    );
  }

  test(
    'runtime controller disposal closes epoch session without default fallback',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final owner = await fixture.initialize(
        native.binding.directory(RuntimeFixture.vaultID),
      );
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      final runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      final controller = AtlasAppController(
        privateStatePersistence: runtime,
        requireEncryptedPrivateState: true,
        localCacheStoreFactory:
            ({bool Function()? privateStateProtectionActive}) async =>
                throw StateError('plaintext fallback forbidden'),
      );
      controller.dispose();
      // dispose is synchronous; the runtime must close via its async drain path.
      for (var n = 0; n < 30 && runtime.isActive; n++) {
        await Future<void>.delayed(const Duration(milliseconds: 10));
      }
      expect(runtime.isActive, isFalse);
      expect(runtime.runtimeState, 'LOCKED');
      await expectLater(
        runtime.createRecord(payload),
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );
      expect(await owner.pendingOperations(), isEmpty);
      expect(native.stores.length, 0);
    },
  );

  test(
    'runtime delayed epoch activation closes returned session after deactivate',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final owner = await fixture.initialize(
        native.binding.directory(RuntimeFixture.vaultID),
      );
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      final entered = Completer<void>(),
          release = Completer<AtlasVaultRuntimeSession>();
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: native.keyStore,
        localStoreIO: native.localStore,
        epochSessionFactory: (_) {
          entered.complete();
          return release.future;
        },
      );
      final activation = runtime.activateExisting(RuntimeFixture.vaultID);
      await entered.future;
      await runtime.deactivate();
      final session = await native.binding.open(RuntimeFixture.vaultID);
      release.complete(session);
      expect(await activation, AtlasVaultActivationResult.failed);
      expect(runtime.isActive, isFalse);
      expect(() => session.read(), runtimeError('ATLAS_RUNTIME_LOCKED'));
      expect(
        native.calls.any(
          (call) =>
              call.method == 'loadVaultKey' &&
              call.id == RuntimeFixture.vaultID,
        ),
        isFalse,
      );
    },
  );
}

Future<RuntimeNativeHarness> runtimeNativeHarness() async {
  final result = RuntimeNativeHarness(await runtimeDirectory())..install();
  addTearDown(result.dispose);
  return result;
}

void runtimeBindingTests(Map<String, AtlasVaultPayloadEnvelope> payloads) {
  test(
    'runtime initial registry binding reopens and publishes without argument',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final root = native.binding.directory(RuntimeFixture.vaultID);
      final owner = await fixture.initialize(root, activate: false);
      final supplied = fixture.initialHistoryRegistry;
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
        authenticatedRegistry: supplied,
      );
      supplied.first['descriptor_sha256'] = '0' * 64;
      expect(native.keys.length, 3);
      var session = await native.binding.open(RuntimeFixture.vaultID);
      final operation = await session.commit(
        payload: payloads.values.first,
        objectID: 'bound-initial',
      );
      session.close();
      final recreated = AtlasVaultRuntimeBinding(
        root: native.binding.root,
        loadKey: native.keyStore.loadVaultKey,
        createKey: native.keyStore.createVaultKey,
      );
      session = await recreated.open(RuntimeFixture.vaultID);
      final receiver = await fixture.initialize(
        Directory('${native.binding.root.path}/independent-receiver'),
        index: 1,
        activate: false,
      );
      final page = RuntimeSignedPage.fromPublication(
        await session.owner.runtimePublication(
          signingKey: await fixture.signer(),
        ),
        registry: fixture.initialHistoryRegistry,
      );
      expect(await page.ingest(receiver), 1);
      expect(await page.ingest(receiver), 0);
      expect(page.view['sequence'], 2);
      expect(page.view['registry_root'], fixture.initialView['registry_root']);
      session.close();
      session = await recreated.open(RuntimeFixture.vaultID);
      final updated = await session.commit(
        payload: runtimeUpdated(payloads.values.first),
        objectID: 'bound-initial',
        expectedRevision: operation.envelope.revision,
      );
      final successor = RuntimeSignedPage.fromPublication(
        await session.owner.runtimePublication(
          signingKey: await fixture.signer(),
        ),
        registry: fixture.initialHistoryRegistry,
      );
      expect(successor.view['sequence'], 3);
      expect(successor.view['previous_root'], page.view['root']);
      expect(await successor.ingest(receiver), 1);
      expect(
        (await receiver.runtimeRecords()).single.operation == updated,
        isTrue,
      );
      expect((await session.owner.observation())['journal_phase'], isNull);
      expect(native.keys.length, 3);
      expect(
        native.calls.where((call) => call.method == 'createVaultKey'),
        hasLength(3),
      );
      session.close();
    },
  );

  for (final attack in ['missing', 'empty', 'wrong-descriptor', 'membership']) {
    test(
      'runtime initial registry binding rejects $attack before protected writes',
      () async {
        final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
        final root = native.binding.directory(RuntimeFixture.vaultID);
        final owner = await fixture.initialize(root, activate: false);
        final registry = switch (attack) {
          'missing' => null,
          'empty' => <Map<String, Object?>>[],
          'membership' => fixture.initialRegistry,
          _ =>
            fixture.initialHistoryRegistry
              ..first['descriptor_sha256'] = '0' * 64,
        };
        final before = await owner.observation();
        final disk = await File('${root.path}/activation').readAsBytes();
        await expectLater(
          native.binding.provision(
            owner: owner,
            signingSeed: runtimeTestKey(10),
            authenticatedRegistry: registry,
          ),
          switch (attack) {
            'empty' => throwsA(
              isA<AtlasVaultStateViewException>().having(
                (error) => error.code,
                'code',
                'ATLAS_REGISTRY_SUBSTITUTION',
              ),
            ),
            'membership' => throwsA(
              isA<AtlasVaultStateViewException>().having(
                (error) => error.code,
                'code',
                'ATLAS_STATE_VIEW_REJECTED',
              ),
            ),
            'missing' => runtimeError('ATLAS_RUNTIME_PROVISIONING_REQUIRED'),
            _ => runtimeError('ATLAS_RUNTIME_BINDING_REJECTED'),
          },
        );
        expect(native.keys, isEmpty);
        expect(
          native.calls.where((call) => call.method == 'createVaultKey'),
          isEmpty,
        );
        expect(await File('${root.path}/runtime-binding').exists(), isFalse);
        expect(
          await vault.atlasVaultSha256Hex(
            await File('${root.path}/activation').readAsBytes(),
          ),
          await vault.atlasVaultSha256Hex(disk),
        );
        expect(await owner.observation(), before);
        await native.binding.provision(
          owner: owner,
          signingSeed: runtimeTestKey(10),
          authenticatedRegistry: fixture.initialHistoryRegistry,
        );
        final session = await native.binding.open(RuntimeFixture.vaultID);
        await session.owner.runtimePublication(
          signingKey: await fixture.signer(),
        );
        expect((await session.owner.observation())['sequence'], 2);
        session.close();
      },
    );
  }

  for (final stage in [1, 2, 3]) {
    test(
      'runtime interrupted provision after secure slot $stage resumes exact enrollment',
      () async {
        final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
        var completed = 0, interrupt = true;
        final binding = AtlasVaultRuntimeBinding(
          root: native.binding.root,
          loadKey: native.keyStore.loadVaultKey,
          createKey: (id, bytes) async {
            await native.keyStore.createVaultKey(id, bytes);
            completed++;
            if (interrupt && completed == stage) {
              throw StateError('synthetic provisioning interruption');
            }
          },
        );
        final root = binding.directory(RuntimeFixture.vaultID),
            file = File(
              '${binding.directory(RuntimeFixture.vaultID).path}/runtime-binding',
            );
        final owner = await fixture.initialize(root);
        await expectLater(
          binding.provision(owner: owner, signingSeed: runtimeTestKey(10)),
          throwsA(isA<StateError>()),
        );
        expect(await file.exists(), isFalse);
        expect(native.keys.length, stage);
        final fingerprints = <String, String>{
          for (final entry in native.keys.entries)
            entry.key: await vault.atlasVaultSha256Hex(entry.value),
        };
        interrupt = false;
        await binding.provision(owner: owner, signingSeed: runtimeTestKey(10));
        expect(await file.exists(), isTrue);
        expect(native.keys.length, 3);
        for (final entry in fingerprints.entries) {
          expect(
            await vault.atlasVaultSha256Hex(native.keys[entry.key]!) ==
                entry.value,
            isTrue,
          );
        }
        final session = await binding.open(RuntimeFixture.vaultID);
        expect(await session.read(), isEmpty);
        await session.commit(
          payload: payloads.values.first,
          objectID: 'provisioned',
        );
        expect(
          (await session.read()).single.payload == payloads.values.first,
          isTrue,
        );
        session.close();
        final previousFile = await vault.atlasVaultSha256Hex(
          await file.readAsBytes(),
        );
        final writes = completed;
        await expectLater(
          binding.provision(owner: owner, signingSeed: runtimeTestKey(10)),
          runtimeError('ATLAS_RUNTIME_BINDING_EXISTS'),
        );
        expect(completed, writes);
        expect(
          await vault.atlasVaultSha256Hex(await file.readAsBytes()) ==
              previousFile,
          isTrue,
        );
      },
    );
  }

  for (final mismatch in ['storage', 'signing']) {
    test(
      'runtime interrupted provision refuses mismatched $mismatch slot without mutation',
      () async {
        final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
        var completed = 0, interrupt = true;
        final binding = AtlasVaultRuntimeBinding(
          root: native.binding.root,
          loadKey: native.keyStore.loadVaultKey,
          createKey: (id, bytes) async {
            await native.keyStore.createVaultKey(id, bytes);
            completed++;
            if (interrupt && completed == 2) {
              throw StateError('synthetic provisioning interruption');
            }
          },
        );
        final root = binding.directory(RuntimeFixture.vaultID);
        final owner = await fixture.initialize(root);
        await expectLater(
          binding.provision(owner: owner, signingSeed: runtimeTestKey(10)),
          throwsA(isA<StateError>()),
        );
        final slot = native.keys.keys.singleWhere(
          (name) => name.startsWith('runtime-$mismatch-'),
        );
        native.keys[slot] = runtimeTestKey(99);
        final fingerprints = <String, String>{
          for (final entry in native.keys.entries)
            entry.key: await vault.atlasVaultSha256Hex(entry.value),
        };
        interrupt = false;
        await expectLater(
          binding.provision(owner: owner, signingSeed: runtimeTestKey(10)),
          runtimeError('ATLAS_RUNTIME_BINDING_REJECTED'),
        );
        expect(await File('${root.path}/runtime-binding').exists(), isFalse);
        expect(completed, 2);
        expect(native.keys.length, 2);
        for (final entry in fingerprints.entries) {
          expect(
            await vault.atlasVaultSha256Hex(native.keys[entry.key]!) ==
                entry.value,
            isTrue,
          );
        }
      },
    );
  }

  for (final failure in [
    'late-revision-conflict',
    'late-ciphertext-corruption',
    'wrong-key',
  ]) {
    test(
      'runtime session import $failure leaves the whole batch unpublished',
      () async {
        final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
        final root = native.binding.directory(RuntimeFixture.vaultID);
        final owner = await fixture.initialize(root);
        await native.binding.provision(
          owner: owner,
          signingSeed: runtimeTestKey(10),
        );
        native.keys[RuntimeFixture.vaultID] = runtimeTestKey(71);
        native.stores[RuntimeFixture.vaultID] = runtimeEmptyLegacyStore(
          RuntimeFixture.vaultID,
        ).canonicalBytes();
        var sequence = 0;
        final legacy = AtlasVaultPrivateStateRuntime(
          secureKeyStore: native.keyStore,
          localStoreIO: native.localStore,
          uuidProvider: () =>
              '10000000-0000-4000-8000-${(++sequence).toString().padLeft(12, '0')}',
        );
        expect(
          await legacy.activateExisting(RuntimeFixture.vaultID),
          AtlasVaultActivationResult.activated,
        );
        await legacy.createRecord(payloads.values.first);
        await legacy.createRecord(payloads.values.last);
        final old = await legacy.read();
        await legacy.deactivate();
        final original = (await native.localStore.read(
          RuntimeFixture.vaultID,
        ))!;
        final sourceBytes = base64Encode(original.canonicalBytes());
        var presented = original;
        AtlasVaultEncryptedPatchOperation? prior;
        if (failure == 'late-revision-conflict') {
          final last = old.records.last;
          prior = await owner.commitRuntimeRecord(
            payload: last.envelope,
            objectID: last.recordId,
            signingKey: await fixture.signer(),
          );
        } else if (failure == 'late-ciphertext-corruption') {
          final rows = runtimeRows(original.toJson()['records']);
          final corrupted = base64Decode(rows.last['ciphertext']! as String);
          corrupted[corrupted.length - 1] ^= 1;
          rows.last['ciphertext'] = base64Encode(corrupted);
          presented = vault.AtlasVaultLocalStore.fromJson({
            ...original.toJson(),
            'records': rows,
          });
        }
        final session = await native.binding.open(RuntimeFixture.vaultID);
        await expectLater(
          session.importLegacy(
            store: presented,
            vaultKey: runtimeTestKey(failure == 'wrong-key' ? 99 : 71),
          ),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        final reopened = fixture.owner(root);
        expect((await reopened.runtimeRecords()).length, prior == null ? 0 : 1);
        expect(
          (await reopened.pendingOperations()).length,
          prior == null ? 0 : 1,
        );
        if (prior != null) {
          expect(
            (await reopened.runtimeRecords()).single.operation == prior,
            isTrue,
          );
        } else {
          expect(
            await session.importLegacy(
              store: original,
              vaultKey: runtimeTestKey(71),
            ),
            2,
          );
          expect(
            await session.importLegacy(
              store: original,
              vaultKey: runtimeTestKey(71),
            ),
            0,
          );
          expect(await reopened.pendingOperations(), hasLength(2));
        }
        expect(
          base64Encode(native.stores[RuntimeFixture.vaultID]!) == sourceBytes,
          isTrue,
        );
        session.close();
        await expectLater(
          session.importLegacy(store: original, vaultKey: runtimeTestKey(71)),
          runtimeError('ATLAS_RUNTIME_LOCKED'),
        );
      },
    );
  }

  test(
    'runtime production restores existing encrypted legacy records atomically and idempotently',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final root = native.binding.directory(RuntimeFixture.vaultID);
      final owner = await fixture.initialize(root);
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      native.keys[RuntimeFixture.vaultID] = runtimeTestKey(71);
      native.stores[RuntimeFixture.vaultID] = runtimeEmptyLegacyStore(
        RuntimeFixture.vaultID,
      ).canonicalBytes();
      final legacy = AtlasVaultPrivateStateRuntime(
        secureKeyStore: native.keyStore,
        localStoreIO: native.localStore,
      );
      expect(
        await legacy.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      for (final payload in payloads.values) {
        await legacy.createRecord(payload);
      }
      for (final record in (await legacy.read()).records) {
        await legacy.updateRecord(
          recordId: record.recordId,
          currentRevision: record.revision,
          envelope: runtimeUpdated(record.envelope),
        );
      }
      final toDelete = (await legacy.read()).records.last;
      await legacy.deleteRecord(
        recordId: toDelete.recordId,
        currentRevision: toDelete.revision,
      );
      final old = await legacy.read();
      final unchanged = base64Encode(native.stores[RuntimeFixture.vaultID]!);
      await legacy.deactivate();
      var runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      var restored = await runtime.read();
      expect(restored.records, hasLength(4));
      expect(restored.tombstones, hasLength(1));
      for (final record in old.records) {
        final found = restored.records.singleWhere(
          (row) => row.recordId == record.recordId,
        );
        expect(found.revision, record.revision);
        expect(found.parentRevision, record.parentRevision);
        expect(found.envelope == record.envelope, isTrue);
        expect(found.keyId, 'epoch-4');
      }
      expect(
        restored.tombstones.single.recordId,
        old.tombstones.single.recordId,
      );
      expect(
        restored.tombstones.single.revision,
        old.tombstones.single.revision,
      );
      expect(
        restored.tombstones.single.parentRevision,
        old.tombstones.single.parentRevision,
      );
      expect(await fixture.owner(root).pendingOperations(), hasLength(5));
      expect(
        base64Encode(native.stores[RuntimeFixture.vaultID]!) == unchanged,
        isTrue,
      );
      await runtime.deactivate();
      runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      expect(await fixture.owner(root).pendingOperations(), hasLength(5));
      restored = await runtime.read();
      final edit = restored.records.first, deletion = restored.records[1];
      final editPayload = AtlasVaultPayloadEnvelope.fromJson({
        ...edit.envelope.toJson(),
        'client_updated_at': '2026-09-06T00:00:00Z',
      });
      await runtime.updateRecord(
        recordId: edit.recordId,
        currentRevision: edit.revision,
        envelope: editPayload,
      );
      await runtime.deleteRecord(
        recordId: deletion.recordId,
        currentRevision: deletion.revision,
      );
      await runtime.deactivate();
      runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      final current = await runtime.read();
      expect(current.records, hasLength(3));
      expect(current.tombstones, hasLength(2));
      expect(
        current.records
                .singleWhere((row) => row.recordId == edit.recordId)
                .envelope ==
            editPayload,
        isTrue,
      );
      expect(
        current.tombstones.any((row) => row.recordId == deletion.recordId),
        isTrue,
      );
      expect(await fixture.owner(root).pendingOperations(), hasLength(7));
      expect(
        base64Encode(native.stores[RuntimeFixture.vaultID]!) == unchanged,
        isTrue,
      );
      await runtime.deactivate();
    },
  );

  test(
    'runtime production refuses legacy edits after import without partial reconciliation',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final root = native.binding.directory(RuntimeFixture.vaultID);
      final owner = await fixture.initialize(root);
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      native.keys[RuntimeFixture.vaultID] = runtimeTestKey(71);
      native.stores[RuntimeFixture.vaultID] = runtimeEmptyLegacyStore(
        RuntimeFixture.vaultID,
      ).canonicalBytes();
      var legacy = AtlasVaultPrivateStateRuntime(
        secureKeyStore: native.keyStore,
        localStoreIO: native.localStore,
      );
      expect(
        await legacy.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      await legacy.createRecord(payloads.values.first);
      await legacy.deactivate();
      var runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      await runtime.deactivate();
      final before = (await owner.runtimeRecords()).single.operation;
      legacy = AtlasVaultPrivateStateRuntime(
        secureKeyStore: native.keyStore,
        localStoreIO: native.localStore,
      );
      expect(
        await legacy.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      final changed = (await legacy.read()).records.single;
      await legacy.updateRecord(
        recordId: changed.recordId,
        currentRevision: changed.revision,
        envelope: runtimeUpdated(changed.envelope),
      );
      await legacy.deactivate();
      runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.migrationRequired,
      );
      expect(runtime.isActive, isFalse);
      expect(
        (await fixture.owner(root).runtimeRecords()).single.operation == before,
        isTrue,
      );
      expect(
        (await fixture.owner(root).pendingOperations()).single == before,
        isTrue,
      );
      await runtime.deactivate();
    },
  );

  test(
    'runtime protected binding requires explicit provision and matching active signer',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final root = native.binding.directory(RuntimeFixture.vaultID);
      await expectLater(
        native.binding.open(RuntimeFixture.vaultID),
        runtimeError('ATLAS_RUNTIME_PROVISIONING_REQUIRED'),
      );
      expect(native.keys.length, 0);
      expect(await root.exists(), isFalse);
      final owner = await fixture.initialize(root, activate: false);
      await owner.beginActivation(fixture.proof);
      await expectLater(
        native.binding.provision(owner: owner, signingSeed: runtimeTestKey(10)),
        runtimeError('ATLAS_ACTIVATION_PENDING'),
      );
      expect(native.keys.length, 0);
      await fixture.accept(owner);
      await expectLater(
        native.binding.provision(owner: owner, signingSeed: runtimeTestKey(11)),
        runtimeError('ATLAS_DEVICE_REVOKED'),
      );
      expect(native.keys.length, 0);
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      expect(native.keys.length, 3);
      expect(native.keys.keys.every((name) => name.length < 96), isTrue);
      expect(native.keys.containsKey(RuntimeFixture.vaultID), isFalse);
      await expectLater(
        native.binding.provision(owner: owner, signingSeed: runtimeTestKey(10)),
        runtimeError('ATLAS_RUNTIME_BINDING_EXISTS'),
      );
      final session = await native.binding.open(RuntimeFixture.vaultID);
      final created = await session.commit(
        payload: payloads.values.first,
        objectID: 'session',
      );
      expect(created.envelope.keyEpoch, 4);
      expect(
        (await session.read()).single.payload == payloads.values.first,
        isTrue,
      );
      expect(session.toString(), 'AtlasVaultRuntimeSession(<redacted>)');
      session.close();
      expect(() => session.read(), runtimeError('ATLAS_RUNTIME_LOCKED'));
      await expectLater(
        session.commit(payload: payloads.values.first, objectID: 'closed'),
        runtimeError('ATLAS_RUNTIME_LOCKED'),
      );
      final reopened = await native.binding.open(RuntimeFixture.vaultID);
      expect((await reopened.read()).single.operation == created, isTrue);
      reopened.close();
    },
  );

  test(
    'runtime binding rejects owner at a different protected directory',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final owner = await fixture.initialize(await runtimeDirectory());
      await expectLater(
        native.binding.provision(owner: owner, signingSeed: runtimeTestKey(10)),
        runtimeError('ATLAS_RUNTIME_BINDING_REJECTED'),
      );
      expect(native.keys.length, 0);
    },
  );

  for (final kind in ['binding', 'storage', 'signing']) {
    test(
      'runtime protected $kind slot missing or mismatched never repairs enrollment',
      () async {
        final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
        final owner = await fixture.initialize(
          native.binding.directory(RuntimeFixture.vaultID),
        );
        await native.binding.provision(
          owner: owner,
          signingSeed: runtimeTestKey(10),
        );
        final slot = native.keys.keys.singleWhere(
          (name) => name.startsWith('runtime-$kind-'),
        );
        final saved = native.keys.remove(slot)!;
        final writes = native.calls
            .where((call) => call.method == 'createVaultKey')
            .length;
        await expectLater(
          native.binding.open(RuntimeFixture.vaultID),
          runtimeError('ATLAS_RUNTIME_PROVISIONING_REQUIRED'),
        );
        native.keys[slot] = runtimeTestKey(99);
        await expectLater(
          native.binding.open(RuntimeFixture.vaultID),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        expect(
          native.calls.where((call) => call.method == 'createVaultKey').length,
          writes,
        );
        native.keys[slot] = saved;
        final session = await native.binding.open(RuntimeFixture.vaultID);
        expect(await session.read(), isEmpty);
        session.close();
      },
    );
  }

  test(
    'runtime binding rejects missing and tampered binding file and vault mismatch',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final root = native.binding.directory(RuntimeFixture.vaultID);
      final owner = await fixture.initialize(root);
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      final file = File('${root.path}/runtime-binding'),
          bytes = await File('${root.path}/runtime-binding').readAsBytes();
      await file.delete();
      await expectLater(
        native.binding.open(RuntimeFixture.vaultID),
        throwsA(isA<AtlasVaultRotationException>()),
      );
      await file.writeAsString('synthetic binding corruption', flush: true);
      await expectLater(
        native.binding.open(RuntimeFixture.vaultID),
        throwsA(isA<AtlasVaultRotationException>()),
      );
      await file.writeAsBytes(bytes, flush: true);
      await expectLater(
        native.binding.open('different-vault'),
        runtimeError('ATLAS_RUNTIME_PROVISIONING_REQUIRED'),
      );
      expect(native.keys.length, 3);
    },
  );

  test(
    'runtime production epoch factory needs no legacy key or store and supports all families',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final ownerRoot = native.binding.directory(RuntimeFixture.vaultID);
      final owner = await fixture.initialize(ownerRoot);
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      native.calls.clear();
      var runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      expect(
        native.calls.any(
          (call) =>
              call.method == 'loadVaultKey' &&
              call.id == RuntimeFixture.vaultID,
        ),
        isFalse,
      );
      for (final payload in payloads.values) {
        await runtime.createRecord(payload);
      }
      final created = await runtime.read();
      expect(
        created.records.map((row) => row.envelope.type).toSet(),
        AtlasVaultPayloadType.values.toSet(),
      );
      for (final record in created.records) {
        await runtime.updateRecord(
          recordId: record.recordId,
          currentRevision: record.revision,
          envelope: runtimeUpdated(record.envelope),
        );
      }
      await runtime.deactivate();
      runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      final updated = await runtime.read();
      expect(updated.records, hasLength(5));
      for (final record in updated.records) {
        expect(record.keyId, 'epoch-4');
        expect(
          record.parentRevision,
          created.records
              .singleWhere((row) => row.recordId == record.recordId)
              .revision,
        );
        await runtime.deleteRecord(
          recordId: record.recordId,
          currentRevision: record.revision,
        );
      }
      await runtime.deactivate();
      runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      final deleted = await runtime.read();
      expect(deleted.records, isEmpty);
      expect(deleted.tombstones, hasLength(5));
      expect(await fixture.owner(ownerRoot).pendingOperations(), hasLength(15));
      expect(
        native.calls.any(
          (call) =>
              call.method == 'createLocalStore' ||
              call.method == 'replaceLocalStore',
        ),
        isFalse,
      );
      await runtime.deactivate();
      expect(runtime.isActive, isFalse);
      await expectLater(
        runtime.read(),
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );
    },
  );

  test(
    'runtime production missing binding fails closed despite usable legacy storage',
    () async {
      final native = await runtimeNativeHarness();
      native.keys[RuntimeFixture.vaultID] = runtimeTestKey(71);
      native.stores[RuntimeFixture.vaultID] = runtimeEmptyLegacyStore(
        RuntimeFixture.vaultID,
      ).canonicalBytes();
      final runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.failed,
      );
      expect(runtime.isActive, isFalse);
      expect(
        native.calls.any(
          (call) =>
              call.method == 'loadVaultKey' &&
              call.id == RuntimeFixture.vaultID,
        ),
        isFalse,
      );
      expect(
        native.calls.any((call) => call.method == 'createVaultKey'),
        isFalse,
      );
      await expectLater(
        runtime.createRecord(payloads.values.first),
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );
    },
  );

  test(
    'runtime production matching legacy IDs and unrelated tombstones do not prove migration',
    () async {
      final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
      final owner = await fixture.initialize(
        native.binding.directory(RuntimeFixture.vaultID),
      );
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      native.keys[RuntimeFixture.vaultID] = runtimeTestKey(71);
      native.stores[RuntimeFixture.vaultID] = runtimeEmptyLegacyStore(
        RuntimeFixture.vaultID,
      ).canonicalBytes();
      final legacy = AtlasVaultPrivateStateRuntime(
        secureKeyStore: native.keyStore,
        localStoreIO: native.localStore,
      );
      expect(
        await legacy.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      await legacy.createRecord(payloads.values.first);
      await legacy.createRecord(payloads.values.last);
      final old = (await legacy.read()).records;
      await legacy.deactivate();
      native.keys.remove(RuntimeFixture.vaultID)?.fillRange(0, 32, 0);
      final signer = await fixture.signer();
      await owner.commitRuntimeRecord(
        payload: old.first.envelope,
        objectID: old.first.recordId,
        signingKey: signer,
      );
      var runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.migrationRequired,
      );
      expect(runtime.isActive, isFalse);
      final mapped = await owner.commitRuntimeRecord(
        payload: old.last.envelope,
        objectID: old.last.recordId,
        signingKey: signer,
      );
      await owner.commitRuntimeRecord(
        payload: null,
        objectID: old.last.recordId,
        expectedRevision: mapped.envelope.revision,
        signingKey: signer,
      );
      runtime = native.runtime();
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.migrationRequired,
      );
      expect(runtime.isActive, isFalse);
      await runtime.deactivate();
    },
  );

  for (final exact in [false, true]) {
    test(
      'runtime production exact legacy revisions ${exact ? 'and payloads prove' : 'but mismatched payloads do not prove'} migration',
      () async {
        final fixture = RuntimeFixture(), native = await runtimeNativeHarness();
        final owner = await fixture.initialize(
          native.binding.directory(RuntimeFixture.vaultID),
        );
        await native.binding.provision(
          owner: owner,
          signingSeed: runtimeTestKey(10),
        );
        native.keys[RuntimeFixture.vaultID] = runtimeTestKey(71);
        native.stores[RuntimeFixture.vaultID] = runtimeEmptyLegacyStore(
          RuntimeFixture.vaultID,
        ).canonicalBytes();
        final legacy = AtlasVaultPrivateStateRuntime(
          secureKeyStore: native.keyStore,
          localStoreIO: native.localStore,
        );
        expect(
          await legacy.activateExisting(RuntimeFixture.vaultID),
          AtlasVaultActivationResult.activated,
        );
        await legacy.createRecord(payloads.values.first);
        final old = (await legacy.read()).records.single;
        await legacy.deactivate();
        final operation = await runtimeSignedOperation(
          fixture,
          owner,
          exact ? old.envelope : runtimeUpdated(old.envelope),
          objectID: old.recordId,
          revision: old.revision,
        );
        final page = await RuntimeSignedPage.sign(fixture, [operation]);
        expect(await page.ingest(owner), 1);
        final runtime = native.runtime();
        expect(
          await runtime.activateExisting(RuntimeFixture.vaultID),
          exact
              ? AtlasVaultActivationResult.activated
              : AtlasVaultActivationResult.migrationRequired,
        );
        expect(runtime.isActive, exact);
        if (exact) {
          expect(
            (await runtime.read()).records.single.envelope == old.envelope,
            isTrue,
          );
        }
        await runtime.deactivate();
      },
    );
  }
}

Future<void> expectRuntimeFenced(
  AtlasVaultEpochVault owner,
  RuntimeFixture fixture,
  AtlasVaultPayloadEnvelope payload,
  String code,
) async {
  final before = await owner.pendingOperations();
  final signer = await fixture.signer();
  await expectLater(owner.runtimeRecords(), runtimeError(code));
  for (final deletion in [false, true]) {
    await expectLater(
      owner.commitRuntimeRecord(
        payload: deletion ? null : payload,
        objectID: 'fenced',
        signingKey: signer,
      ),
      runtimeError(code),
    );
  }
  await expectLater(
    owner.runtimePublication(signingKey: signer),
    runtimeError(code),
  );
  await expectLater(
    owner.ingestRuntimePage(
      view: {},
      registry: [],
      collection: {},
      opaqueState: runtimeTestKey(7),
      operations: [],
    ),
    runtimeError(code),
  );
  expect((await owner.pendingOperations()).length, before.length);
}

void runtimeLifecycleTests(AtlasVaultPayloadEnvelope payload) {
  test(
    'runtime pending activation fences read mutation and signed publication after reopen',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final owner = await fixture.initialize(root, activate: false);
      await owner.beginActivation(fixture.proof);
      await expectRuntimeFenced(
        fixture.owner(root),
        fixture,
        payload,
        'ATLAS_ACTIVATION_PENDING',
      );
      await fixture.accept(owner);
      final operation = await owner.commitRuntimeRecord(
        payload: payload,
        objectID: 'activated',
        signingKey: await fixture.signer(),
      );
      expect(operation.envelope.keyEpoch, 4);
      expect(
        (await fixture.owner(root).runtimeRecords()).single.payload == payload,
        isTrue,
      );
    },
  );

  test('runtime revoked owner remains fenced after reopen', () async {
    final fixture = RuntimeFixture(), root = await runtimeDirectory();
    final owner = await fixture.initialize(root, index: 2, activate: false);
    await expectLater(
      fixture.accept(owner, 2),
      runtimeError('ATLAS_DEVICE_REVOKED'),
    );
    expect((await fixture.owner(root, 2).observation())['status'], 'REVOKED');
    await expectRuntimeFenced(
      fixture.owner(root, 2),
      fixture,
      payload,
      'ATLAS_DEVICE_REVOKED',
    );
  });

  test(
    'runtime catchup cleanup and publication recovery retain records and fences',
    () async {
      final fixture = RuntimeFixture(catchUp: true),
          root = await runtimeDirectory();
      var owner = await fixture.initialize(root, index: 2, activate: false);
      await expectLater(
        owner.catchUp(
          fixture.packets(2).sublist(1),
          currentActivationID:
              fixture.vector['target_activation_id']! as String,
          agreementPrivateKey: runtimeTestKey(22),
        ),
        throwsA(isA<AtlasVaultRotationException>()),
      );
      await expectRuntimeFenced(
        fixture.owner(root, 2),
        fixture,
        payload,
        'ATLAS_CATCH_UP_PENDING',
      );
      expect(await fixture.catchUp(owner), isTrue);
      final operation = await owner.commitRuntimeRecord(
        payload: payload,
        objectID: 'after-catchup',
        signingKey: await fixture.signer(2),
      );
      expect(operation.envelope.keyEpoch, 5);
      await expectLater(
        owner.cleanupEpochsForTesting(
          retainEpochs: {5},
          deleteEpoch: (_) async {},
          containsEpoch: (_) async => false,
          checkpoint: (stage) {
            if (stage == 'cleanup_pending') {
              throw StateError('synthetic interruption');
            }
          },
        ),
        throwsA(isA<AtlasVaultRotationException>()),
      );
      owner = fixture.owner(root, 2);
      await expectRuntimeFenced(
        owner,
        fixture,
        payload,
        'ATLAS_CLEANUP_PENDING',
      );
      final deleted = <int>{};
      await owner.cleanupEpochs(
        retainEpochs: {5},
        deleteEpoch: (epoch) async {
          deleted.add(epoch);
        },
        containsEpoch: (epoch) async => !deleted.contains(epoch),
      );
      expect(deleted, {3, 4});
      expect(await fixture.owner(root, 2).availableEpochs(), [5]);
      expect(
        (await fixture.owner(root, 2).runtimeRecords()).single.operation ==
            operation,
        isTrue,
      );
      await File(
        '${root.path}/activation',
      ).writeAsString('synthetic corrupt publication', flush: true);
      await expectLater(
        owner.runtimeRecords(),
        throwsA(isA<AtlasVaultRotationException>()),
      );
      await owner.recoverPublication();
      await expectRuntimeFenced(
        fixture.owner(root, 2),
        fixture,
        payload,
        'ATLAS_CATCH_UP_PENDING',
      );
      expect(await fixture.catchUp(owner), isTrue);
      expect(
        (await fixture.owner(root, 2).runtimeRecords()).single.operation ==
            operation,
        isTrue,
      );
      expect(
        (await fixture.owner(root, 2).pendingOperations()).single == operation,
        isTrue,
      );
    },
  );

  test(
    'runtime historical projection keys cannot be cleaned after outbox acknowledgement',
    () async {
      final fixture = RuntimeFixture(catchUp: true),
          root = await runtimeDirectory();
      final owner = await fixture.initialize(root, index: 2, activate: false);
      final op = await owner.commitRuntimeRecord(
        payload: payload,
        objectID: 'historical',
        signingKey: await fixture.signer(2),
      );
      await owner.confirmRemoteAcceptance(op.operationId);
      await fixture.catchUp(owner);
      expect((await owner.runtimeRecords()).single.payload == payload, isTrue);
      var deleted = false;
      await expectLater(
        owner.cleanupEpochs(
          retainEpochs: {5},
          deleteEpoch: (_) async {
            deleted = true;
          },
          containsEpoch: (_) async => false,
        ),
        runtimeError('ATLAS_CLEANUP_PENDING'),
      );
      expect(deleted, isFalse);
      expect(
        (await fixture.owner(root, 2).runtimeRecords()).single.operation == op,
        isTrue,
      );
    },
  );
}

void runtimeAdmissionTests(Map<String, AtlasVaultPayloadEnvelope> payloads) {
  test(
    'runtime publication before first revocation uses authenticated initial P6 history',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final ownerRoot = Directory('${root.path}/owner');
      final owner = await fixture.initialize(ownerRoot, activate: false);
      final signer = await fixture.signer();
      final initial = fixture.initialView;
      final registry = runtimeRows(fixture.vector['initial_registry']);
      expect(initial['version'], 2);
      expect(initial['key_epoch'], 3);
      expect(initial['sequence'], 1);
      expect(
        AtlasVaultAuthenticatedStateView.registryRoot(registry),
        initial['registry_root'],
      );
      expect(
        AtlasVaultRevocation.registryRoot(fixture.initialRegistry) ==
            initial['registry_root'],
        isFalse,
      );
      final beforeCommit = await owner.observation();
      expect(beforeCommit['status'], 'ACTIVE');
      expect(beforeCommit['journal_phase'], isNull);
      final op = await owner.commitRuntimeRecord(
        payload: payloads.values.first,
        objectID: 'initial-publication',
        signingKey: signer,
      );
      expect(op.envelope.keyEpoch, 3);

      // Control: the actual initial descriptor registry can authenticate a v2
      // successor without a revocation, synthetic bridge, or new key delivery.
      final bytes = Uint8List.fromList(
        utf8.encode(
          jsonEncode({
            'format': 'atlasvault-guarded-collection',
            'version': 1,
            'route': 'patch',
            'records': [op.envelope.toJson()],
          }),
        ),
      );
      final collection = await AtlasVaultSignedStateCommitment.sign(
        bytes,
        collectionId: RuntimeFixture.collectionID,
        sequence: 2,
        previousRoot: initial['collection_root']! as String,
        signingKey: signer,
      );
      final view = await AtlasVaultAuthenticatedStateView.sign({
        'format': 'atlasvault-authenticated-state-view',
        'version': 2,
        'account_id': initial['account_id'],
        'vault_id': initial['vault_id'],
        'sequence': 2,
        'previous_root': initial['root'],
        'collection_root': collection.root,
        'registry_root': initial['registry_root'],
        'previous_registry_root': initial['registry_root'],
        'key_epoch': 3,
      }, signer);
      final control = RuntimeSignedPage(
        view,
        registry,
        collection.toJson(),
        bytes,
        [op.toJson()],
      );
      final receiverRoot = Directory('${root.path}/control-receiver');
      final receiver = await fixture.initialize(
        receiverRoot,
        index: 1,
        activate: false,
      );
      expect(await control.ingest(receiver), 1);
      expect(await control.ingest(receiver), 0);
      expect(
        (await fixture.owner(receiverRoot, 1).runtimeRecords())
                .single
                .operation ==
            op,
        isTrue,
      );
      expect((await receiver.observation())['state_root'], view['root']);
      expect((await receiver.observation())['journal_phase'], isNull);
      expect(await receiver.pendingOperations(), isEmpty);
      stdout.writeln(
        'C29 initial publication control: P6=v2 epoch=3 signed_successor_accepted=true rotation=false',
      );

      final beforePublication = await owner.observation();
      Map<String, Object?>? publication;
      try {
        publication = await owner.runtimePublication(
          signingKey: signer,
          authenticatedRegistry: registry,
        );
        final published = RuntimeSignedPage(
          runtimeObject(publication['view']),
          registry,
          runtimeObject(publication['collection']),
          base64Decode(publication['opaque_state_b64']! as String),
          runtimeRows(publication['operations']),
        );
        final fresh = await fixture.initialize(
          Directory('${root.path}/publication-receiver'),
          index: 1,
          activate: false,
        );
        expect(await published.ingest(fresh), 1);
        expect((await owner.observation())['sequence'], 2);
      } finally {
        final reopened = fixture.owner(ownerRoot);
        if (publication == null) {
          expect(await reopened.observation(), beforePublication);
        }
        expect(
          (await reopened.runtimeRecords()).single.operation == op,
          isTrue,
        );
        expect((await reopened.pendingOperations()).single == op, isTrue);
        expect((await reopened.observation())['journal_phase'], isNull);
      }
    },
  );

  for (final attack in ['missing', 'empty', 'wrong-descriptor', 'membership']) {
    test(
      'runtime initial registry rejects $attack without history mutation',
      () async {
        final fixture = RuntimeFixture(), root = await runtimeDirectory();
        final owner = await fixture.initialize(root, activate: false);
        final signer = await fixture.signer();
        final operation = await owner.commitRuntimeRecord(
          payload: payloads.values.first,
          objectID: 'registry-rejection',
          signingKey: signer,
        );
        final registry = switch (attack) {
          'missing' => null,
          'empty' => <Map<String, Object?>>[],
          'membership' => fixture.initialRegistry,
          _ =>
            fixture.initialHistoryRegistry
              ..first['descriptor_sha256'] = '0' * 64,
        };
        final before = await owner.observation();
        final disk = await File('${root.path}/activation').readAsBytes();
        await expectLater(
          owner.runtimePublication(
            signingKey: signer,
            authenticatedRegistry: registry,
          ),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        expect(
          await vault.atlasVaultSha256Hex(
            await File('${root.path}/activation').readAsBytes(),
          ),
          await vault.atlasVaultSha256Hex(disk),
        );
        final reopened = fixture.owner(root);
        expect(await reopened.observation(), before);
        expect((await reopened.recovery())['status'], 'ACTIVE');
        expect(
          (await reopened.runtimeRecords()).single.operation == operation,
          isTrue,
        );
        expect(
          (await reopened.pendingOperations()).single == operation,
          isTrue,
        );
        final page = RuntimeSignedPage.fromPublication(
          await reopened.runtimePublication(
            signingKey: signer,
            authenticatedRegistry: fixture.initialHistoryRegistry,
          ),
          registry: fixture.initialHistoryRegistry,
        );
        final receiver = await fixture.initialize(
          Directory('${root.path}/receiver'),
          index: 1,
          activate: false,
        );
        expect(await page.ingest(receiver), 1);
        expect(page.view['sequence'], 2);
      },
    );
  }

  for (final activate in [false, true]) {
    test(
      'runtime publication pinned signer rejects active deputy prebridge=${!activate}',
      () async {
        final fixture = RuntimeFixture(), root = await runtimeDirectory();
        final owner = await fixture.initialize(root, activate: activate);
        final signer = await fixture.signer();
        final operation = await owner.commitRuntimeRecord(
          payload: payloads.values.first,
          objectID: 'pinned-signer',
          signingKey: signer,
        );
        final registry = activate
            ? await fixture.activeRegistry()
            : fixture.initialHistoryRegistry;
        final before = await owner.observation();
        final disk = await File('${root.path}/activation').readAsBytes();
        await expectLater(
          owner.runtimePublication(
            signingKey: await fixture.signer(1),
            authenticatedRegistry: registry,
          ),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        expect(
          await vault.atlasVaultSha256Hex(
            await File('${root.path}/activation').readAsBytes(),
          ),
          await vault.atlasVaultSha256Hex(disk),
        );
        expect(await fixture.owner(root).observation(), before);
        expect((await owner.recovery())['status'], 'ACTIVE');
        expect((await owner.pendingOperations()).single == operation, isTrue);
        final page = RuntimeSignedPage.fromPublication(
          await owner.runtimePublication(
            signingKey: signer,
            authenticatedRegistry: registry,
          ),
          registry: registry,
        );
        final receiver = await fixture.initialize(
          Directory('${root.path}/receiver'),
          index: 1,
          activate: activate,
        );
        expect(await page.ingest(receiver), 1);
      },
    );
  }

  for (final attack in ['initial-descriptors', 'wrong-current']) {
    test(
      'runtime postbridge registry override rejects $attack atomically',
      () async {
        final fixture = RuntimeFixture(), root = await runtimeDirectory();
        final owner = await fixture.initialize(root),
            signer = await fixture.signer();
        final operation = await owner.commitRuntimeRecord(
          payload: payloads.values.first,
          objectID: 'postbridge-registry',
          signingKey: signer,
        );
        final registry = attack == 'initial-descriptors'
            ? fixture.initialHistoryRegistry
            : ((await fixture.activeRegistry())..first['state'] = 'REVOKED');
        final before = await owner.observation();
        final disk = await File('${root.path}/activation').readAsBytes();
        await expectLater(
          owner.runtimePublication(
            signingKey: signer,
            authenticatedRegistry: registry,
          ),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        expect(
          await vault.atlasVaultSha256Hex(
            await File('${root.path}/activation').readAsBytes(),
          ),
          await vault.atlasVaultSha256Hex(disk),
        );
        expect(await owner.observation(), before);
        expect((await owner.recovery())['status'], 'ACTIVE');
        expect((await owner.pendingOperations()).single == operation, isTrue);
        final page = RuntimeSignedPage.fromPublication(
          await owner.runtimePublication(signingKey: signer),
          registry: await fixture.activeRegistry(),
        );
        final receiver = await fixture.initialize(
          Directory('${root.path}/receiver'),
          index: 1,
        );
        expect(await page.ingest(receiver), 1);
      },
    );
  }

  test(
    'runtime authoritative metadata accepts canonical UUID revision and opaque object ID',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final source = await fixture.initialize(Directory('${root.path}/source'));
      final receiver = await fixture.initialize(
        Directory('${root.path}/receiver'),
        index: 1,
      );
      final operation = await runtimeSignedOperation(
        fixture,
        source,
        payloads.values.first,
        objectID: 'opaque-${'x' * 121}',
      );
      final page = await RuntimeSignedPage.sign(fixture, [operation]);
      expect(await page.ingest(receiver), 1);
      final record = (await receiver.runtimeRecords()).single;
      expect(record.operation.envelope.objectId.length, 128);
      expect(record.payload == payloads.values.first, isTrue);
    },
  );

  for (final attack in [
    'revision-non-uuid',
    'revision-uppercase',
    'revision-compact',
    'parent-non-uuid',
    'parent-uppercase',
    'body-operation-id',
    'body-author',
    'body-sequence-double',
    'body-lamport-double',
    'body-object-id',
    'body-revision',
    'body-parent',
    'body-tombstone',
    'body-null-payload',
  ]) {
    test('runtime authoritative metadata rejects re-signed $attack', () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final source = await fixture.initialize(Directory('${root.path}/source'));
      final receiverRoot = Directory('${root.path}/receiver');
      final receiver = await fixture.initialize(receiverRoot, index: 1);
      var revision = 'a0000000-0000-4000-8000-000000000001';
      String? parent;
      final body = <String, Object?>{};
      switch (attack) {
        case 'revision-non-uuid':
          revision = 'not-a-uuid';
        case 'revision-uppercase':
          revision = revision.toUpperCase();
        case 'revision-compact':
          revision = revision.replaceAll('-', '');
        case 'parent-non-uuid':
          parent = 'not-a-uuid';
        case 'parent-uppercase':
          parent = revision.toUpperCase();
        case 'body-operation-id':
          body['operation_id'] = '10000000-0000-4000-8000-000000000099';
        case 'body-author':
          body['author_device_id'] = fixture.deviceID(1);
        case 'body-sequence-double':
          body['author_sequence'] = 1.0;
        case 'body-lamport-double':
          body['lamport'] = 1.0;
        case 'body-object-id':
          body['object_id'] = 'different-opaque-id';
        case 'body-revision':
          body['revision'] = '10000000-0000-4000-8000-000000000099';
        case 'body-parent':
          body['parent_revision'] = '10000000-0000-4000-8000-000000000099';
        case 'body-tombstone':
          body['tombstone'] = true;
        case 'body-null-payload':
          body['payload'] = null;
      }
      final operation = await runtimeSignedOperation(
        fixture,
        source,
        payloads.values.first,
        revision: revision,
        parentRevision: parent,
        bodyChanges: body,
      );
      final page = await RuntimeSignedPage.sign(fixture, [operation]);
      final before = await receiver.observation();
      await expectLater(
        page.ingest(receiver),
        runtimeError('ATLAS_RUNTIME_RECORD_REJECTED'),
      );
      final reopened = fixture.owner(receiverRoot, 1);
      expect(await reopened.observation(), before);
      expect(await reopened.runtimeRecords(), isEmpty);
      expect(await reopened.pendingOperations(), isEmpty);
    });
  }

  test(
    'runtime production publication is a complete accepted signed page',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final source = await fixture.initialize(Directory('${root.path}/source'));
      final receiver = await fixture.initialize(
        Directory('${root.path}/receiver'),
        index: 1,
      );
      for (final entry in payloads.entries) {
        await source.commitRuntimeRecord(
          payload: entry.value,
          objectID: entry.key,
          signingKey: await fixture.signer(),
        );
      }
      for (final op in await source.pendingOperations()) {
        await source.confirmRemoteAcceptance(op.operationId);
      }
      expect(await source.pendingOperations(), isEmpty);
      final publication = await source.runtimePublication(
        signingKey: await fixture.signer(),
      );
      expect(runtimeRows(publication['operations']), hasLength(5));
      final page = RuntimeSignedPage(
        runtimeObject(publication['view']),
        await fixture.activeRegistry(),
        runtimeObject(publication['collection']),
        base64Decode(publication['opaque_state_b64']! as String),
        runtimeRows(publication['operations']),
      );
      expect(await page.ingest(receiver), 5);
      expect(await page.ingest(receiver), 0);
      expect(
        (await receiver.runtimeRecords())
            .map((row) => row.payload?.type)
            .toSet(),
        AtlasVaultPayloadType.values.toSet(),
      );
      expect(
        (await receiver.observation())['state_root'],
        (await source.observation())['state_root'],
      );
      expect(await receiver.pendingOperations(), isEmpty);
    },
  );

  for (final mismatch in ['missing-operation', 'extra-operation']) {
    test(
      'runtime signed winner set binds separately supplied $mismatch history',
      () async {
        final fixture = RuntimeFixture(), root = await runtimeDirectory();
        final source = await fixture.initialize(
          Directory('${root.path}/source'),
        );
        final receiverRoot = Directory('${root.path}/receiver');
        final receiver = await fixture.initialize(receiverRoot, index: 1);
        final op = await source.commitRuntimeRecord(
          payload: payloads.values.first,
          objectID: 'projection-bound',
          signingKey: await fixture.signer(),
        );
        final valid = await RuntimeSignedPage.sign(
          fixture,
          mismatch == 'missing-operation' ? [op.toJson()] : [],
        );
        final mismatched = RuntimeSignedPage(
          valid.view,
          valid.registry,
          valid.collection,
          valid.bytes,
          mismatch == 'missing-operation' ? [] : [op.toJson()],
        );
        final before = await receiver.observation();
        await expectLater(
          mismatched.ingest(receiver),
          runtimeError('ATLAS_RUNTIME_RECORD_REJECTED'),
        );
        final reopened = fixture.owner(receiverRoot, 1);
        expect(await reopened.observation(), before);
        expect(await reopened.runtimeRecords(), isEmpty);
        expect(await reopened.pendingOperations(), isEmpty);
      },
    );
  }

  test(
    'runtime accepted remote projection preserves divergent local unsent edit',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final source = await fixture.initialize(Directory('${root.path}/source'));
      final receiverRoot = Directory('${root.path}/receiver');
      final receiver = await fixture.initialize(receiverRoot, index: 1);
      final op = await source.commitRuntimeRecord(
        payload: payloads.values.first,
        objectID: 'optimistic',
        signingKey: await fixture.signer(),
      );
      final first = await RuntimeSignedPage.sign(fixture, [op.toJson()]);
      expect(await first.ingest(receiver), 1);
      final local = await receiver.commitRuntimeRecord(
        payload: runtimeUpdated(payloads.values.first),
        objectID: op.envelope.objectId,
        expectedRevision: op.envelope.revision,
        signingKey: await fixture.signer(1),
      );
      final second = await RuntimeSignedPage.sign(fixture, [
        op.toJson(),
      ], previous: first.view);
      expect(await second.ingest(receiver), 0);
      final reopened = fixture.owner(receiverRoot, 1);
      expect(
        (await reopened.runtimeRecords()).single.operation == local,
        isTrue,
      );
      expect((await reopened.pendingOperations()).single == local, isTrue);
      expect((await reopened.observation())['state_root'], second.view['root']);
    },
  );

  for (final mismatch in [
    'opaque-bytes',
    'registry',
    'collection',
    'root-signature',
  ]) {
    test(
      'runtime P6 $mismatch substitution cannot publish projection or cursor',
      () async {
        final fixture = RuntimeFixture(), root = await runtimeDirectory();
        final source = await fixture.initialize(
          Directory('${root.path}/source'),
        );
        final receiverRoot = Directory('${root.path}/receiver');
        final receiver = await fixture.initialize(receiverRoot, index: 1);
        final op = await source.commitRuntimeRecord(
          payload: payloads.values.first,
          objectID: 'substitution',
          signingKey: await fixture.signer(),
        );
        final valid = await RuntimeSignedPage.sign(fixture, [op.toJson()]);
        var view = valid.view, collection = valid.collection;
        var registry = valid.registry, bytes = valid.bytes;
        switch (mismatch) {
          case 'opaque-bytes':
            bytes = runtimeTestKey(7);
          case 'registry':
            registry = fixture.initialRegistry;
          case 'collection':
            collection = runtimeObject(fixture.vector['initial_collection']);
          case 'root-signature':
            view = {
              ...view,
              'signature_b64': base64Encode(List<int>.filled(64, 0)),
            };
        }
        final before = await receiver.observation();
        await expectLater(
          RuntimeSignedPage(
            view,
            registry,
            collection,
            bytes,
            valid.operations,
          ).ingest(receiver),
          throwsA(isA<AtlasVaultRotationException>()),
        );
        final after = await fixture.owner(receiverRoot, 1).observation();
        expect(after['state_root'], before['state_root']);
        expect(after['sequence'], before['sequence']);
        expect(
          await fixture.owner(receiverRoot, 1).pendingOperations(),
          isEmpty,
        );
        expect(
          (await fixture.owner(receiverRoot, 1).recovery())['status'],
          'MANUAL_REQUIRED',
        );
      },
    );
  }

  for (final attack in [
    'operation_id',
    'author_sequence',
    'lamport',
    'author_device_id',
    'parent_revision',
    'tombstone',
  ]) {
    test(
      'runtime signed page rejects tampered $attack without partial publication',
      () async {
        final fixture = RuntimeFixture(), root = await runtimeDirectory();
        final source = await fixture.initialize(
          Directory('${root.path}/source'),
        );
        final receiverRoot = Directory('${root.path}/receiver');
        final receiver = await fixture.initialize(receiverRoot, index: 1);
        final signer = await fixture.signer();
        final good = await source.commitRuntimeRecord(
          payload: payloads.values.first,
          objectID: 'good',
          signingKey: signer,
        );
        final bad = (await source.commitRuntimeRecord(
          payload: payloads.values.last,
          objectID: 'bad',
          signingKey: signer,
        )).toJson();
        switch (attack) {
          case 'operation_id':
            bad[attack] = '10000000-0000-4000-8000-000000000099';
          case 'author_sequence':
            bad[attack] = 99;
          case 'lamport':
            bad[attack] = 99;
          case 'author_device_id':
            bad[attack] = fixture.deviceID(1);
          case 'parent_revision':
            bad['envelope'] = {
              ...runtimeObject(bad['envelope']),
              attack: 'forged-parent',
            };
          case 'tombstone':
            bad['operation_type'] = 'delete';
            bad['envelope'] = {...runtimeObject(bad['envelope']), attack: true};
        }
        final page = await RuntimeSignedPage.sign(fixture, [
          good.toJson(),
          bad,
        ]);
        final before = await receiver.observation();
        await expectLater(
          page.ingest(receiver),
          runtimeError('ATLAS_RUNTIME_RECORD_REJECTED'),
        );
        final reopened = fixture.owner(receiverRoot, 1);
        expect(await reopened.observation(), before);
        expect(await reopened.runtimeRecords(), isEmpty);
        expect(await reopened.pendingOperations(), isEmpty);
        final valid = await RuntimeSignedPage.sign(fixture, [good.toJson()]);
        expect(await valid.ingest(reopened), 1);
      },
    );
  }

  test(
    'runtime signer-deputy B signature cannot claim author A in a matching body',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final source = await fixture.initialize(Directory('${root.path}/source'));
      final deputy = await fixture.initialize(
        Directory('${root.path}/deputy'),
        index: 1,
      );
      final receiverRoot = Directory('${root.path}/receiver');
      final receiver = await fixture.initialize(receiverRoot);
      final original = await source.commitRuntimeRecord(
        payload: payloads.values.first,
        objectID: 'deputy',
        signingKey: await fixture.signer(),
      );
      final body = await source.open(original.envelope);
      final sealed = await deputy.seal(
        'patch',
        body,
        objectID: original.envelope.objectId,
        revision: original.envelope.revision,
        signingKey: await fixture.signer(1),
      );
      body.fillRange(0, body.length, 0);
      final forged = {...original.toJson(), 'envelope': sealed.toJson()};
      final page = await RuntimeSignedPage.sign(fixture, [forged]);
      final before = await receiver.observation();
      await expectLater(
        page.ingest(receiver),
        runtimeError('ATLAS_RUNTIME_RECORD_REJECTED'),
      );
      expect(await fixture.owner(receiverRoot).observation(), before);
      expect(await fixture.owner(receiverRoot).runtimeRecords(), isEmpty);
      expect(await fixture.owner(receiverRoot).pendingOperations(), isEmpty);
    },
  );

  test(
    'runtime authenticated fork preserves recovery evidence and fences after reopen',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final source = await fixture.initialize(Directory('${root.path}/source'));
      final receiverRoot = Directory('${root.path}/receiver');
      final receiver = await fixture.initialize(receiverRoot, index: 1);
      final op = await source.commitRuntimeRecord(
        payload: payloads.values.first,
        objectID: 'forked',
        signingKey: await fixture.signer(),
      );
      final first = await RuntimeSignedPage.sign(fixture, [op.toJson()]);
      expect(await first.ingest(receiver), 1);
      final fork = await RuntimeSignedPage.sign(fixture, []);
      await expectLater(
        fork.ingest(receiver),
        throwsA(isA<AtlasVaultRotationException>()),
      );
      final evidence = await receiver.recovery();
      expect(evidence['status'], 'MANUAL_REQUIRED');
      expect((await receiver.observation())['status'], 'RECOVERY_PENDING');
      expect(evidence['local'], isNotEmpty);
      expect(evidence['peer'], isNotEmpty);
      final reopened = fixture.owner(receiverRoot, 1);
      expect(await reopened.recovery(), evidence);
      await expectRuntimeFenced(
        reopened,
        fixture,
        payloads.values.first,
        'ATLAS_RECOVERY_PENDING',
      );
      final safe = jsonEncode(evidence);
      for (final forbidden in [
        'ciphertext_b64',
        'vault_key',
        'private_key',
        'payload',
      ]) {
        expect(safe.contains(forbidden), isFalse);
      }
    },
  );

  test(
    'runtime independent authors converge under accepted reordered duplicate signed pages',
    () async {
      final fixture = RuntimeFixture(), root = await runtimeDirectory();
      final aRoot = Directory('${root.path}/a'),
          bRoot = Directory('${root.path}/b');
      final a = await fixture.initialize(aRoot),
          b = await fixture.initialize(bRoot, index: 1);
      final aOps = <AtlasVaultEncryptedPatchOperation>[],
          bOps = <AtlasVaultEncryptedPatchOperation>[];
      for (final family in payloads.keys) {
        aOps.add(
          await a.commitRuntimeRecord(
            payload: payloads[family],
            objectID: family,
            signingKey: await fixture.signer(),
          ),
        );
        bOps.add(
          await b.commitRuntimeRecord(
            payload: runtimeUpdated(payloads[family]!),
            objectID: family,
            signingKey: await fixture.signer(1),
          ),
        );
      }
      expect(aOps.map((op) => op.authorDeviceId).toSet(), {
        fixture.deviceID(0),
      });
      expect(bOps.map((op) => op.authorDeviceId).toSet(), {
        fixture.deviceID(1),
      });
      final all = [...aOps, ...bOps];
      final first = await RuntimeSignedPage.sign(
        fixture,
        [
          ...all.reversed,
          all.first,
          all.last,
        ].map((op) => op.toJson()).toList(),
      );
      expect(await first.ingest(a), 5);
      final reversed = RuntimeSignedPage(
        first.view,
        first.registry,
        first.collection,
        first.bytes,
        first.operations.reversed.toList(),
      );
      expect(await reversed.ingest(b), 5);
      expect(await first.ingest(a), 0);
      expect(await first.ingest(b), 0);
      final second = await RuntimeSignedPage.sign(
        fixture,
        all.map((op) => op.toJson()).toList(),
        previous: first.view,
      );
      expect(await second.ingest(a), 0);
      expect(await second.ingest(b), 0);
      final aRecords = await fixture.owner(aRoot).runtimeRecords();
      final bRecords = await fixture.owner(bRoot, 1).runtimeRecords();
      expect(aRecords, hasLength(5));
      expect(
        aRecords.map((row) => row.operation.operationId).toList(),
        bRecords.map((row) => row.operation.operationId).toList(),
      );
      expect(
        aRecords.map((row) => row.payload?.type).toSet(),
        AtlasVaultPayloadType.values.toSet(),
      );
      expect(
        (await a.observation())['state_root'],
        (await b.observation())['state_root'],
      );
      expect(await a.pendingOperations(), hasLength(5));
      expect(await b.pendingOperations(), hasLength(5));
      for (final op in aOps) {
        await a.confirmRemoteAcceptance(op.operationId);
      }
      for (final op in bOps) {
        await b.confirmRemoteAcceptance(op.operationId);
      }
      expect(await fixture.owner(aRoot).pendingOperations(), isEmpty);
      expect(await fixture.owner(bRoot, 1).pendingOperations(), isEmpty);

      final target = aRecords.first;
      final edit = await a.commitRuntimeRecord(
        payload: runtimeUpdated(target.payload!),
        objectID: target.operation.envelope.objectId,
        expectedRevision: target.operation.envelope.revision,
        signingKey: await fixture.signer(),
      );
      final deletion = await b.commitRuntimeRecord(
        payload: null,
        objectID: target.operation.envelope.objectId,
        expectedRevision: target.operation.envelope.revision,
        signingKey: await fixture.signer(1),
      );
      final third = await RuntimeSignedPage.sign(
        fixture,
        [deletion, edit, ...all, deletion].map((op) => op.toJson()).toList(),
        previous: second.view,
      );
      expect(await third.ingest(a), 1);
      expect(await third.ingest(b), 1);
      final finalA = await fixture.owner(aRoot).runtimeRecords();
      final finalB = await fixture.owner(bRoot, 1).runtimeRecords();
      expect(
        finalA.map((row) => row.operation.operationId).toList(),
        finalB.map((row) => row.operation.operationId).toList(),
      );
      expect(
        finalA
            .where(
              (row) =>
                  row.operation.envelope.objectId == deletion.envelope.objectId,
            )
            .single
            .payload,
        isNull,
      );
      final fourth = await RuntimeSignedPage.sign(
        fixture,
        [edit, ...all.reversed, deletion].map((op) => op.toJson()).toList(),
        previous: third.view,
      );
      expect(await fourth.ingest(a), 0);
      expect(await fourth.ingest(b), 0);
      for (final client in [a, b]) {
        await expectLater(
          client.commitRuntimeRecord(
            payload: target.payload,
            objectID: deletion.envelope.objectId,
            expectedRevision: deletion.envelope.revision,
            signingKey: await fixture.signer(),
          ),
          runtimeError('ATLAS_RUNTIME_REVISION_CONFLICT'),
        );
      }
    },
  );
}
