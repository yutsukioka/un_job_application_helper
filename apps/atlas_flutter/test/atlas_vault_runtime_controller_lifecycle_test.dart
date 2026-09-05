import 'dart:async';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:atlas/features/app_shell/atlas_private_records_panel.dart';
import 'package:atlas/src/atlas_vault/android_storage.dart';
import 'package:atlas/src/atlas_vault/local_store_io.dart';
import 'package:atlas/src/atlas_vault/private_state_runtime.dart';
import 'package:flutter/foundation.dart' show FlutterError;
import 'package:flutter/material.dart'
    show
        AnimatedBuilder,
        ListView,
        MaterialApp,
        Scaffold,
        SizedBox,
        TextField,
        ValueKey;
import 'package:flutter_test/flutter_test.dart';

const _vaultId = 'lifecycle-example';
const _timestamp = '2026-09-05T12:00:00Z';

void main() {
  for (final production in [false, true]) {
    for (final outcome in [
      AtlasVaultActivationResult.failed,
      AtlasVaultActivationResult.migrationRequired,
    ]) {
      test(
        'P1 pending and ${outcome.name} reactivation clears generic records (production=$production)',
        () async {
          final persistence = _Persistence()..includeLegacyProjection = false;
          final controller = AtlasAppController(
            privateStatePersistence: persistence,
            requireEncryptedPrivateState: production,
          );
          await controller.activateExistingAtlasVault(_vaultId);
          expect(controller.privateRecords.length, 1);
          await persistence.deactivate();
          final gate = persistence.activationGate = _Gate();
          persistence.activationResult = outcome;
          final activation = controller.activateExistingAtlasVault(_vaultId);
          final visibleImmediately = controller.privateRecords.length;
          await gate.entered.future;
          final visibleWhilePending = controller.privateRecords.length;
          final mutationEnabledWhilePending =
              controller.canMutatePrivateRecords;
          gate.release.complete();
          final result = await activation;
          final visibleAfterFailure = controller.privateRecords.length;
          controller.dispose();
          await controller.shutdown();
          expect(result, outcome);
          expect(mutationEnabledWhilePending, isFalse);
          expect(
            [visibleImmediately, visibleWhilePending, visibleAfterFailure],
            [0, 0, 0],
          );
        },
      );
    }
  }

  test(
    'P1 production admission clears every projection before notifying or awaiting',
    () async {
      final persistence = _Persistence();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
        requireEncryptedPrivateState: true,
      );
      await controller.activateExistingAtlasVault(_vaultId);
      expect(controller.savedSearches.length, 1);
      expect(controller.trackerRecords.length, 1);
      expect(controller.privateRecords.length, 1);
      await persistence.deactivate();
      final gate = persistence.activationGate = _Gate();
      persistence.activationResult = AtlasVaultActivationResult.failed;
      final observedEmpty = <bool>[];
      controller.addListener(() => observedEmpty.add(_empty(controller)));
      final activation = controller.activateExistingAtlasVault(_vaultId);
      final immediatelyEmpty = _empty(controller);
      final synchronousNotifications = observedEmpty.toList();
      gate.release.complete();
      final result = await activation;
      final finallyEmpty = _empty(controller);
      controller.dispose();
      await controller.shutdown();
      expect(immediatelyEmpty, isTrue);
      expect(synchronousNotifications, [true]);
      expect(observedEmpty, everyElement(isTrue));
      expect(result, AtlasVaultActivationResult.failed);
      expect(persistence.activations, 2);
      expect(finallyEmpty, isTrue);
    },
  );

  test(
    'P1 compatibility admission preserves legacy detection while clearing generic records',
    () async {
      final persistence = _Persistence();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
      );
      await controller.activateExistingAtlasVault(_vaultId);
      await persistence.deactivate();
      final result = await controller.activateExistingAtlasVault(_vaultId);
      final visible = [
        controller.privateRecords.length,
        controller.savedSearches.length,
        controller.trackerRecords.length,
      ];
      controller.dispose();
      await controller.shutdown();
      expect(result, AtlasVaultActivationResult.migrationRequired);
      expect(persistence.activations, 1);
      expect(visible, [0, 1, 1]);
    },
  );

  test(
    'P1 observer shutdown at admission prevents persistence activation',
    () async {
      final persistence = _Persistence();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
        requireEncryptedPrivateState: true,
      );
      Future<void>? shutdown;
      controller.addListener(() => shutdown ??= controller.shutdown());
      final result = await controller.activateExistingAtlasVault(_vaultId);
      await shutdown;
      controller.dispose();
      await controller.shutdown();
      expect(result, AtlasVaultActivationResult.failed);
      expect(persistence.activations, 0);
      expect(_empty(controller), isTrue);
    },
  );

  testWidgets(
    'P1 deactivation removes an open read view before persistence drains',
    (tester) async {
      final persistence = _Persistence()..includeLegacyProjection = false;
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
      );
      await controller.activateExistingAtlasVault(_vaultId);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ListView(
              children: [
                AnimatedBuilder(
                  animation: controller,
                  builder: (_, _) => AtlasPrivateRecordsPanel(
                    records: controller.privateRecords,
                    enabled: controller.canMutatePrivateRecords,
                    onCreate: (_) async {},
                    onUpdate: (_, _) async {},
                    onDelete: (_) async {},
                  ),
                ),
              ],
            ),
          ),
        ),
      );
      await tester.tap(find.byKey(const ValueKey('private-record-family')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Application notes').last);
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Read record'));
      await tester.pumpAndSettle();
      final field = tester.widget<TextField>(
        find.byWidgetPredicate(
          (widget) =>
              widget is TextField && widget.decoration?.labelText == 'Body',
        ),
      );
      expect(field.readOnly, isTrue);
      final body = field.controller!;
      expect(body.text, 'Example body');
      final gate = persistence.deactivationGate = _Gate();
      final deactivation = controller.deactivateAtlasVault();
      expect(_empty(controller), isTrue);
      expect(controller.canMutatePrivateRecords, isFalse);
      await tester.pumpAndSettle();
      expect(find.text('Record details'), findsNothing);
      expect(body.text, isEmpty);
      expect(find.byTooltip('Read record'), findsNothing);
      expect(find.text('No records'), findsOneWidget);
      gate.release.complete();
      await deactivation;
      await tester.pumpWidget(const SizedBox.shrink());
      controller.dispose();
      await controller.shutdown();
    },
  );

  test(
    'shutdown from an activation notification cannot report stale success',
    () async {
      final persistence = _Persistence();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
      );
      Future<void>? shutdown;
      controller.addListener(() {
        if (controller.privateRecords.isNotEmpty) {
          shutdown = controller.shutdown();
        }
      });
      expect(
        await controller.activateExistingAtlasVault(_vaultId),
        AtlasVaultActivationResult.failed,
      );
      await shutdown;
      expect(_empty(controller), isTrue);
      expect(persistence.isActive, isFalse);
      controller.dispose();
    },
  );

  test(
    'shutdown from a mutation notification prevents persistence admission',
    () async {
      final setup = _RuntimeSetup();
      final controller = setup.controller;
      await controller.activateExistingAtlasVault(_vaultId);
      await controller.createPrivateRecord(_payload());
      final replacesBefore = setup.store.replaces;
      Future<void>? shutdown;
      var notifications = 0;
      controller.addListener(() {
        notifications++;
        shutdown = controller.shutdown();
      });
      await expectLater(
        controller.createPrivateRecord(_payload()),
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );
      await shutdown;
      expect(setup.store.replaces, replacesBefore);
      expect(notifications, 1);
      expect(_empty(controller), isTrue);
      controller.dispose();
    },
  );

  test(
    'generic CRUD error clears all private projections without late work',
    () async {
      final setup = _RuntimeSetup();
      final controller = setup.controller;
      await controller.activateExistingAtlasVault(_vaultId);
      await controller.createPrivateRecord(_payload());
      setup.store.rejectReplace = true;
      await expectLater(
        controller.createPrivateRecord(_payload()),
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );
      expect(_empty(controller), isTrue);
      expect(controller.isMutatingPrivateRecord, isFalse);
      await controller.shutdown();
      controller.dispose();
    },
  );

  test('shutdown is awaitable, terminal and shared with dispose', () async {
    final persistence = _Persistence();
    final controller = AtlasAppController(privateStatePersistence: persistence);
    await controller.activateExistingAtlasVault(_vaultId);
    var notifications = 0;
    controller.addListener(() => notifications++);
    final gate = persistence.deactivationGate = _Gate();
    var drained = false;
    final shutdown = controller.shutdown();
    final completion = shutdown.then((_) => drained = true);
    expect(identical(shutdown, controller.shutdown()), isTrue);
    expect(_empty(controller), isTrue);
    expect(controller.canMutatePrivateRecords, isFalse);
    await gate.entered.future;
    expect(drained, isFalse);
    expect(
      await controller.activateExistingAtlasVault(_vaultId),
      AtlasVaultActivationResult.failed,
    );
    expect(persistence.activations, 1);
    controller.dispose();
    expect(identical(shutdown, controller.shutdown()), isTrue);
    expect(identical(shutdown, controller.deactivateAtlasVault()), isTrue);
    gate.release.complete();
    await completion;
    expect(persistence.deactivations, 1);
    expect(persistence.isActive, isFalse);
    expect(notifications, 0);
    // R029: direct invalid Flutter use still asserts; no blanket notify override.
    expect(controller.notifyListeners, throwsA(isA<FlutterError>()));
  });

  test(
    'dispose joins an already completed deactivation without repeating it',
    () async {
      final persistence = _Persistence();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
      );
      await controller.activateExistingAtlasVault(_vaultId);
      await controller.deactivateAtlasVault();
      controller.dispose();
      await controller.shutdown();
      expect(persistence.deactivations, 1);
    },
  );

  test(
    'shutdown waits for admission and prevents a late activation start',
    () async {
      final gate = _Gate();
      final persistence = _Persistence();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
        compatibilityPrivateStateAdmission: () async {
          await gate.wait();
          return false;
        },
      );
      final activation = controller.activateExistingAtlasVault(_vaultId);
      await gate.entered.future;
      controller.dispose();
      var drained = false;
      final shutdown = controller.shutdown().then((_) => drained = true);
      await Future<void>.value();
      expect(drained, isFalse);
      gate.release.complete();
      expect(await activation, AtlasVaultActivationResult.failed);
      await shutdown;
      expect(persistence.activations, 0);
      expect(_empty(controller), isTrue);
    },
  );

  test(
    'shutdown drains a pending activation read without reinstalling it',
    () async {
      final persistence = _Persistence()..readGate = _Gate();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
      );
      final activation = controller.activateExistingAtlasVault(_vaultId);
      await persistence.readGate!.entered.future;
      controller.dispose();
      var drained = false;
      final shutdown = controller.shutdown().then((_) => drained = true);
      await Future<void>.value();
      expect(drained, isFalse);
      persistence.readGate!.release.complete();
      expect(await activation, AtlasVaultActivationResult.failed);
      await shutdown;
      expect(persistence.isActive, isFalse);
      expect(_empty(controller), isTrue);
    },
  );

  test(
    'shutdown retains fixed persistence failure and clears records',
    () async {
      final persistence = _Persistence();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
      );
      await controller.activateExistingAtlasVault(_vaultId);
      final gate = persistence.deactivationGate = _Gate();
      persistence.deactivationFailure = const FormatException(
        'synthetic detail',
      );
      controller.dispose();
      final shutdown = controller.shutdown();
      final result = expectLater(
        shutdown,
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );
      await gate.entered.future;
      gate.release.complete();
      await result;
      expect(_empty(controller), isTrue);
      expect(controller.canMutatePrivateRecords, isFalse);
      expect(identical(shutdown, controller.shutdown()), isTrue);
    },
  );

  test(
    'shutdown never converts a Flutter programming error to a vault error',
    () async {
      final failure = FlutterError('Synthetic programming failure');
      final persistence = _Persistence()..deactivationFailure = failure;
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
      );
      await expectLater(controller.shutdown(), throwsA(same(failure)));
      expect(_empty(controller), isTrue);
    },
  );

  test(
    'deactivation clears all projections before persistence completes',
    () async {
      final persistence = _Persistence();
      final controller = AtlasAppController(
        privateStatePersistence: persistence,
      );
      await controller.activateExistingAtlasVault(_vaultId);
      final gate = persistence.deactivationGate = _Gate();
      final operation = controller.deactivateAtlasVault();
      final immediatelyEmpty = _empty(controller);
      final immediatelyFenced = !controller.canMutatePrivateRecords;
      gate.release.complete();
      await operation;
      expect(immediatelyEmpty, isTrue);
      expect(immediatelyFenced, isTrue);
      expect(_empty(controller), isTrue);
      controller.dispose();
    },
  );

  test('dispose during activation cannot publish or notify late', () async {
    final persistence = _Persistence()..activationGate = _Gate();
    final controller = AtlasAppController(privateStatePersistence: persistence);
    var notifications = 0;
    controller.addListener(() => notifications++);
    final activation = controller.activateExistingAtlasVault(_vaultId);
    final result = expectLater(
      activation,
      completion(AtlasVaultActivationResult.failed),
    );
    await persistence.activationGate!.entered.future;
    controller.dispose();
    final countAtDispose = notifications;
    var drained = false;
    final shutdown = controller.shutdown().then((_) => drained = true);
    await Future<void>.value();
    expect(drained, isFalse);
    persistence.activationGate!.release.complete();
    await result;
    await shutdown;
    expect(notifications, countAtDispose);
    expect(persistence.isActive, isFalse);
    expect(_empty(controller), isTrue);
  });

  test('dispose during pending deactivation cannot notify late', () async {
    final persistence = _Persistence();
    final controller = AtlasAppController(privateStatePersistence: persistence);
    await controller.activateExistingAtlasVault(_vaultId);
    final gate = persistence.deactivationGate = _Gate();
    final deactivation = controller.deactivateAtlasVault();
    final result = expectLater(deactivation, completes);
    await gate.entered.future;
    controller.dispose();
    gate.release.complete();
    await result;
    await controller.shutdown();
    expect(persistence.deactivations, 1);
    expect(_empty(controller), isTrue);
  });

  for (final operation in ['create', 'update', 'delete']) {
    test(
      'dispose during generic $operation rejects stale completion',
      () async {
        final setup = _RuntimeSetup();
        final controller = setup.controller;
        await controller.activateExistingAtlasVault(_vaultId);
        await controller.createPrivateRecord(_payload());
        final record = controller.privateRecords.single;
        final gate = setup.store.replaceGate = _Gate();
        final mutation = switch (operation) {
          'create' => controller.createPrivateRecord(_payload()),
          'update' => controller.updatePrivateRecord(
            record,
            _payload('Revised example'),
          ),
          _ => controller.deletePrivateRecord(record),
        };
        final result = expectLater(
          mutation,
          throwsA(isA<AtlasVaultPrivateStateException>()),
        );
        await gate.entered.future;
        controller.dispose();
        final immediatelyEmpty = _empty(controller);
        final immediatelyFenced = !controller.canMutatePrivateRecords;
        var drained = false;
        final shutdown = controller.shutdown().then((_) => drained = true);
        await Future<void>.value();
        expect(drained, isFalse);
        await expectLater(
          controller.createPrivateRecord(_payload()),
          throwsA(isA<AtlasVaultPrivateStateException>()),
        );
        gate.release.complete();
        await result;
        await shutdown;
        expect(immediatelyEmpty, isTrue);
        expect(immediatelyFenced, isTrue);
        expect(_empty(controller), isTrue);
        expect(setup.runtime.isActive, isFalse);
        expect(controller.isMutatingPrivateRecord, isFalse);
      },
    );
  }
}

bool _empty(AtlasAppController controller) =>
    controller.privateRecords.isEmpty &&
    controller.savedSearches.isEmpty &&
    controller.trackerRecords.isEmpty;

vault.AtlasVaultPayloadEnvelope _payload([String body = 'Example body']) =>
    vault.AtlasVaultPayloadEnvelope.fromJson({
      'type': 'application_note',
      'payload_schema': 1,
      'payload': {
        'body': body,
        'note_kind': 'Example kind',
        'created_at': _timestamp,
        'updated_at': _timestamp,
      },
      'client_created_at': _timestamp,
      'client_updated_at': _timestamp,
    });

class _Gate {
  _Gate() {
    addTearDown(() {
      if (!release.isCompleted) release.complete();
    });
  }
  final entered = Completer<void>();
  final release = Completer<void>();

  Future<void> wait() async {
    if (!entered.isCompleted) entered.complete();
    await release.future;
  }
}

class _Persistence implements AtlasVaultPrivateStatePersistence {
  _Gate? activationGate;
  _Gate? deactivationGate;
  _Gate? readGate;
  Object? deactivationFailure;
  bool includeLegacyProjection = true;
  AtlasVaultActivationResult activationResult =
      AtlasVaultActivationResult.activated;
  int deactivations = 0;
  int activations = 0;
  @override
  bool isActive = false;

  @override
  Future<AtlasVaultActivationResult> activateExisting(String vaultId) async {
    activations++;
    await activationGate?.wait();
    isActive = activationResult == AtlasVaultActivationResult.activated;
    return activationResult;
  }

  @override
  Future<void> deactivate() async {
    deactivations++;
    await deactivationGate?.wait();
    isActive = false;
    final failure = deactivationFailure;
    if (failure != null) throw failure;
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> read() async {
    await readGate?.wait();
    return AtlasVaultPrivateStateSnapshot(
      savedSearches: [
        if (includeLegacyProjection)
          AtlasSavedSearch(
            name: 'Example search',
            request: const AtlasSearchRequest(),
          ),
      ],
      trackerRecords: [
        if (includeLegacyProjection)
          AtlasApplicationRecord(
            id: 'example',
            jobKey: 'example:1',
            status: 'saved',
          ),
      ],
      records: [
        AtlasVaultPrivateRecord(
          recordId: 'example',
          revision: 'example-revision',
          parentRevision: null,
          keyId: 'example-key',
          envelope: _payload(),
        ),
      ],
    );
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> saveSearch(AtlasSavedSearch value) =>
      read();

  @override
  Future<AtlasVaultPrivateStateSnapshot> saveTrackerRecord(
    AtlasApplicationRecord value,
  ) => read();
}

class _RuntimeSetup {
  _RuntimeSetup() {
    runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: _KeyStore(),
      localStoreIO: store,
    );
    controller = AtlasAppController(
      privateStatePersistence: runtime,
      requireEncryptedPrivateState: true,
    );
  }
  final store = _MemoryStore();
  late final AtlasVaultPrivateStateRuntime runtime;
  late final AtlasAppController controller;
}

class _KeyStore implements AtlasVaultSecureKeyStore {
  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async =>
      Uint8List.fromList(List.filled(32, 7));
  @override
  Future<bool> containsVaultKey(String vaultId) async => true;
  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async =>
      throw UnimplementedError();
  @override
  Future<void> deleteVaultKey(String vaultId) async =>
      throw UnimplementedError();
}

class _MemoryStore implements AtlasVaultLocalStoreIO {
  _Gate? replaceGate;
  bool rejectReplace = false;
  int replaces = 0;
  vault.AtlasVaultLocalStore? store = vault.AtlasVaultLocalStore.fromJson({
    'format': 'atlasvault-local-store',
    'version': 1,
    'store_id': '99999999-8888-4777-8666-555555555555',
    'created_at': _timestamp,
    'updated_at': _timestamp,
    'vault_metadata': {
      'format': 'atlas-vault',
      'version': 1,
      'vault_id': _vaultId,
      'crypto': {
        'record_aead': 'AES-256-GCM',
        'kdf': 'Argon2id',
        'subkey_kdf': 'HKDF-SHA256',
        'key_wrap_aead': 'AES-256-GCM',
      },
      'key_wraps': <Object?>[],
    },
    'records': <Object?>[],
  });

  @override
  Future<vault.AtlasVaultLocalStore?> read(String vaultId) async => store;
  @override
  Future<void> create(String vaultId, vault.AtlasVaultLocalStore value) async =>
      store = value;
  @override
  Future<void> delete(String vaultId) async => store = null;
  @override
  Future<void> replace(
    String vaultId,
    vault.AtlasVaultLocalStore value, {
    required String expectedSha256,
  }) async {
    replaces++;
    if (rejectReplace ||
        await vault.atlasVaultSha256Hex(store!.canonicalBytes()) !=
            expectedSha256) {
      throw const AtlasVaultPrivateStateException();
    }
    await replaceGate?.wait();
    store = value;
  }
}
