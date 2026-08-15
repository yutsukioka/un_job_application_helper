import 'dart:convert';
import 'dart:io';
import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_pairing_fakes.dart';
import 'support/atlas_vault_vector_loader.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Map<String, Object?> vector;

  setUpAll(() {
    vector = loadAtlasVaultVector(
      'atlasvault_trusted_pairing_delivery_vectors_v1.json',
    );
  });

  test('device fingerprint is canonical and cross-platform stable', () {
    expect(
      atlasVaultPairingDeviceFingerprint(
        atlasVaultObject(vector['inviter'])['device_id']! as String,
      ),
      'E198-A89D-6D33-8FB2',
    );
    expect(
      () => atlasVaultPairingDeviceFingerprint('invalid'),
      throwsA(isA<AtlasVaultPairingException>()),
    );
  });

  test('platform integration journey is reusable across both roles', () async {
    final stores = AtlasVaultPairingPlatformStores(
      identity: AtlasVaultPairingMemoryIdentityStore(),
      registry: AtlasVaultPairingMemoryRegistryStore(),
      replay: AtlasVaultPairingMemoryReplayStore(),
      transaction: AtlasVaultPairingMemoryTransactionStore(),
      staging: AtlasVaultPairingMemoryStageStore(),
      secureKey: AtlasVaultPairingMemorySecureKeyStore(),
      localStore: AtlasVaultPairingMemoryLocalStore(),
      selectedVault: AtlasVaultPairingMemorySelectedVaultStore(),
    );
    final invitee = await runAtlasVaultPairingPlatformJourney(
      vector: vector,
      platformRole: AtlasVaultPairingRole.invitee,
      platformStores: stores,
    );
    final inviter = await runAtlasVaultPairingPlatformJourney(
      vector: vector,
      platformRole: AtlasVaultPairingRole.inviter,
      platformStores: stores,
    );
    expect(invitee.artifacts, hasLength(4));
    expect(inviter.artifacts, hasLength(4));
    expect(invitee.tombstoneCount, 1);
    expect(inviter.tombstoneCount, 1);
  });

  test('explicit pairing installs then trusts in the reviewed order', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);

    expect(
      (await journey.inviter.inspect()).disposition,
      AtlasVaultTrustedPairingDisposition.identityReady,
    );
    expect(
      (await journey.invitee.inspect()).disposition,
      AtlasVaultTrustedPairingDisposition.identityReady,
    );

    expect(
      (await journey.inviter.createPairingOffer()).disposition,
      AtlasVaultTrustedPairingDisposition.offerReady,
    );
    expect(
      (await journey.inviter.savePairingOffer()).disposition,
      AtlasVaultTrustedPairingDisposition.offerSaved,
    );
    final inviteeAcceptance = await journey.invitee.importPairingOffer();
    expect(
      inviteeAcceptance.disposition,
      AtlasVaultTrustedPairingDisposition.acceptanceReady,
    );
    expect(inviteeAcceptance.sas, isNotNull);
    expect(
      (await journey.invitee.savePairingAcceptance()).disposition,
      AtlasVaultTrustedPairingDisposition.acceptanceSaved,
    );
    final inviterCodes = await journey.inviter.importPairingAcceptance();
    final inviteeCodes = await journey.invitee.inspect();
    expect(
      inviterCodes.disposition,
      AtlasVaultTrustedPairingDisposition.codesReady,
    );
    expect(inviterCodes.sas, inviteeCodes.sas);

    expect(
      (await journey.inviter.confirmCodesMatch()).disposition,
      AtlasVaultTrustedPairingDisposition.deliveryReady,
    );
    expect(
      (await journey.invitee.confirmCodesMatch()).disposition,
      AtlasVaultTrustedPairingDisposition.codesConfirmed,
    );
    expect(
      (await journey.inviter.saveKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.deliverySaved,
    );
    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady,
    );

    final storeCreate = journey.inviteeEvents.indexOf('store.create');
    final keyCreate = journey.inviteeEvents.indexOf('key.create');
    final selectionCreate = journey.inviteeEvents.indexOf('selection.create');
    final activation = journey.inviteeEvents.indexOf('runtime.activate');
    final trust = journey.inviteeEvents.lastIndexOf('registry.replace');
    final acknowledgementStage = journey.inviteeEvents.indexOf(
      'stage.create:acknowledgement',
    );
    expect(storeCreate, greaterThanOrEqualTo(0));
    expect(storeCreate, lessThan(keyCreate));
    expect(keyCreate, lessThan(selectionCreate));
    expect(selectionCreate, lessThan(activation));
    expect(activation, lessThan(trust));
    expect(trust, lessThan(acknowledgementStage));

    final installed = journey.inviteeLocal.values[journey.vaultId]!;
    expect(
      installed.records.map((record) => record.toJson()).toList(),
      journey.bootstrap.records.map((record) => record.toJson()).toList(),
    );
    expect(installed.records.where((record) => record.deleted), isNotEmpty);

    expect(
      (await journey.invitee.savePairingAcknowledgement()).disposition,
      AtlasVaultTrustedPairingDisposition.completed,
    );
    expect(
      (await journey.inviter.importPairingAcknowledgement()).disposition,
      AtlasVaultTrustedPairingDisposition.completed,
    );
    expect(journey.inviterRegistry.value!.devices, hasLength(1));
    expect(journey.inviteeRegistry.value!.devices, hasLength(1));
    expect(journey.inviterTransactions.value, isNull);
    expect(journey.inviteeTransactions.value, isNull);
    expect(
      journey.inviteeEvents.lastIndexOf('transaction.delete'),
      greaterThan(journey.inviteeEvents.lastIndexOf('stage.delete:offer')),
    );
  });

  test(
    'pairing construction performs no identity or platform operation',
    () async {
      final journey = await _PairingJourney.create(vector);
      addTearDown(journey.stop);

      expect(journey.inviterIdentity.loadCalls, 0);
      expect(journey.inviteeIdentity.loadCalls, 0);
      expect(journey.inviterEvents, isEmpty);
      expect(journey.inviteeEvents, isEmpty);
    },
  );

  test('inviter requires an active encrypted vault', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await journey.inviterRuntime.deactivate();

    final result = await journey.inviter.createPairingOffer();

    expect(result.disposition, AtlasVaultTrustedPairingDisposition.unavailable);
    expect(journey.inviterTransactions.value, isNull);
    expect(journey.mailbox.bytes, isNull);
  });

  for (final gate in <AtlasVaultPairingCleanInstallDisposition>[
    AtlasVaultPairingCleanInstallDisposition.migrationRequired,
    AtlasVaultPairingCleanInstallDisposition.existingVault,
    AtlasVaultPairingCleanInstallDisposition.unavailable,
  ]) {
    test('invitee clean-install gate reports ${gate.name}', () async {
      final journey = await _PairingJourney.create(
        vector,
        inviteeCleanDisposition: gate,
      );
      addTearDown(journey.stop);
      await journey.inviter.createPairingOffer();
      await journey.inviter.savePairingOffer();

      final result = await journey.invitee.importPairingOffer();

      expect(result.disposition.name, gate.name);
      expect(journey.inviteeTransactions.value, isNull);
      expect(journey.inviteeStage.values, isEmpty);
      expect(journey.mailbox.bytes, isNotNull);
    });
  }

  test('both code confirmations gate delivery and installation', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);

    final earlyDelivery = await journey.inviter.saveKeyDelivery();
    final earlyInstall = await journey.invitee.importKeyDelivery();
    expect(
      earlyDelivery.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      earlyInstall.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(journey.inviteeLocal.values, isEmpty);

    await journey.inviter.confirmCodesMatch();
    expect(
      (await journey.inviter.saveKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.deliverySaved,
    );
    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(journey.inviteeLocal.values, isEmpty);
  });

  test(
    'pre-selection discard preserves replay and clears staged secrets',
    () async {
      final journey = await _PairingJourney.create(vector);
      addTearDown(journey.stop);
      await _exchangeAcceptance(journey);
      await journey.invitee.confirmCodesMatch();

      expect(journey.inviteeReplay.value?.entries, hasLength(1));
      expect(journey.inviteeTransactions.value?.ephemeralPrivateKey, isNotNull);

      final result = await journey.invitee.discardPairing();

      expect(
        result.disposition,
        AtlasVaultTrustedPairingDisposition.identityReady,
      );
      expect(journey.inviteeTransactions.value, isNull);
      expect(journey.inviteeStage.values, isEmpty);
      expect(journey.inviteeReplay.value?.entries, hasLength(1));
      expect(journey.inviteeLocal.values, isEmpty);
      expect(journey.inviteeKeys.values, isEmpty);
      expect(journey.inviteeSelected.value, isNull);
      expect(journey.inviteeRegistry.value, isNull);
    },
  );

  test('consumed offer is rejected after discard and restart', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);
    await journey.invitee.confirmCodesMatch();
    final offerBytes = Uint8List.fromList(
      journey.inviterStage.values[AtlasVaultPairingArtifactKind.offer]!,
    );
    await journey.invitee.discardPairing();
    journey.mailbox.bytes = offerBytes;

    expect(
      (await journey.invitee.importPairingOffer()).disposition,
      AtlasVaultTrustedPairingDisposition.acceptanceReady,
    );
    await journey.invitee.savePairingAcceptance();
    expect(
      (await journey.invitee.confirmCodesMatch()).disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(journey.inviteeReplay.value?.entries, hasLength(1));
  });

  test('expired offer fails before invitee transaction creation', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await journey.inviter.createPairingOffer();
    await journey.inviter.savePairingOffer();
    journey.clock.value = journey.clock.value.add(const Duration(minutes: 11));

    final result = await journey.invitee.importPairingOffer();

    expect(
      result.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(journey.inviteeTransactions.value, isNull);
    expect(journey.inviteeLocal.values, isEmpty);
  });

  test('inviter becomes resume-only after delivery export', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);
    await journey.inviter.confirmCodesMatch();
    await journey.invitee.confirmCodesMatch();
    await journey.inviter.saveKeyDelivery();

    final result = await journey.inviter.discardPairing();

    expect(
      result.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(journey.inviterTransactions.value, isNotNull);
    expect(journey.inviterStage.values, isNotEmpty);
  });

  test('pairing transaction is strict canonical and forward-only', () {
    final transaction = AtlasVaultPairingTransaction.fromJson(
      _transactionJson(vector),
    );
    final restored = AtlasVaultPairingTransaction.fromCanonicalBytes(
      transaction.canonicalBytes(),
    );
    expect(restored.toJson(), transaction.toJson());
    expect(restored.role, AtlasVaultPairingRole.invitee);
    expect(restored.stage, AtlasVaultPairingStage.acceptanceCreated);

    final backwards = AtlasVaultPairingTransaction.fromJson(<String, Object?>{
      ...transaction.toJson(),
      'revision': '42000000-0000-4000-8000-000000000004',
      'parent_revision': transaction.revision,
      'stage': 'offer_imported',
      'updated_at': '2026-08-15T10:02:00Z',
    });
    expect(
      () => validateAtlasVaultPairingTransition(transaction, backwards),
      throwsA(isA<AtlasVaultPairingTransactionException>()),
    );

    expect(
      () => AtlasVaultPairingTransaction.fromJson(<String, Object?>{
        ...transaction.toJson(),
        'vault_id': '../not-a-vault',
      }),
      throwsA(isA<AtlasVaultPairingTransactionException>()),
    );
    expect(
      () => AtlasVaultPairingTransaction.fromCanonicalBytes(
        Uint8List(atlasVaultMaximumPairingTransactionByteCount + 1),
      ),
      throwsA(isA<AtlasVaultPairingTransactionException>()),
    );

    final copiedSecret = transaction.ephemeralPrivateKey!;
    expect(copiedSecret, isNot(everyElement(0)));
    transaction.destroy();
    expect(transaction.ephemeralPrivateKey, everyElement(0));
    expect(copiedSecret, isNot(everyElement(0)));
    copiedSecret.fillRange(0, copiedSecret.length, 0);
  });

  test(
    'pairing replay adapter accepts the full protected-state bound',
    () async {
      final replay = _largeReplayStore();
      final bytes = replay.canonicalBytes();
      expect(
        bytes.length,
        greaterThan(atlasVaultMaximumPairingTransactionByteCount),
      );
      expect(bytes.length, lessThan(atlasVaultMaximumPairingStateByteCount));
      final recorder = AtlasVaultPairingMethodCallRecorder(
        channelName: atlasVaultWindowsMethodChannelName,
      )..install();
      addTearDown(recorder.uninstall);
      recorder.handler = (_) async => bytes;

      final restored = await AtlasWindowsPairingReplayStore(
        channel: recorder.channel,
      ).read();
      expect(restored?.entries.length, replay.entries.length);
      expect(bytes, replay.canonicalBytes());

      recorder.handler = (_) async =>
          Uint8List(atlasVaultMaximumPairingStateByteCount + 1);
      await expectLater(
        AtlasWindowsPairingReplayStore(channel: recorder.channel).read(),
        throwsA(isA<AtlasVaultPairingStorageException>()),
      );
    },
  );

  for (final platform in <String>['android', 'windows']) {
    test(
      '$platform protected pairing stores use create CAS and cleanup',
      () async {
        final channelName = platform == 'android'
            ? atlasVaultAndroidMethodChannelName
            : atlasVaultWindowsMethodChannelName;
        final recorder = AtlasVaultPairingMethodCallRecorder(
          channelName: channelName,
        )..install();
        addTearDown(recorder.uninstall);
        final registry = AtlasVaultTrustedDeviceRegistry.fromJson(
          atlasVaultObject(vector['trusted_registry']),
        );
        final replay = AtlasVaultPairingReplayStore.fromJson(
          atlasVaultObject(vector['replay_store']),
        );
        final transaction = AtlasVaultPairingTransaction.fromJson(
          _transactionJson(vector),
        );
        final artifact = AtlasVaultPairingArtifact.fromCanonicalBytes(
          _artifactBytes(vector, 'acceptance'),
        );

        final stores = platform == 'android'
            ? _PairingStores.android(recorder.channel)
            : _PairingStores.windows(recorder.channel);
        recorder.handler = (call) async {
          switch (call.method) {
            case 'readTrustedDeviceRegistry':
              return registry.canonicalBytes();
            case 'readPairingReplayStore':
              return replay.canonicalBytes();
            case 'readPairingTransaction':
              return transaction.canonicalBytes();
            case 'readStagedPairingArtifact':
              return artifact.canonicalBytes();
            case 'pickPairingArtifact':
              return null;
            case 'savePairingArtifact':
              return false;
            default:
              return null;
          }
        };

        await stores.registry.create(registry);
        await stores.registry.replace(registry, expectedSha256: '1' * 64);
        expect(await stores.registry.read(), registry);
        await stores.replay.create(replay);
        await stores.replay.replace(replay, expectedSha256: '2' * 64);
        expect((await stores.replay.read())!.toJson(), replay.toJson());
        await stores.transaction.create(transaction);
        await stores.transaction.replace(transaction, expectedSha256: '3' * 64);
        expect(
          (await stores.transaction.read())!.toJson(),
          transaction.toJson(),
        );
        await stores.staging.create(artifact);
        expect(
          (await stores.staging.read(artifact.kind))!.canonicalBytes(),
          artifact.canonicalBytes(),
        );
        await stores.staging.delete(artifact.kind, expectedSha256: '4' * 64);
        await stores.transaction.delete(expectedSha256: '5' * 64);
        expect(await stores.transport.pick(), isNull);
        expect(await stores.transport.save(artifact), isFalse);

        expect(
          recorder.calls.map((call) => call.method),
          containsAll(<String>[
            'createTrustedDeviceRegistry',
            'replaceTrustedDeviceRegistry',
            'createPairingReplayStore',
            'replacePairingReplayStore',
            'createPairingTransaction',
            'replacePairingTransaction',
            'createStagedPairingArtifact',
            'deleteStagedPairingArtifact',
            'deletePairingTransaction',
            'pickPairingArtifact',
            'savePairingArtifact',
          ]),
        );
      },
    );
  }

  test(
    'tampered protected transaction and wrong staged kind fail closed',
    () async {
      final recorder = AtlasVaultPairingMethodCallRecorder(
        channelName: atlasVaultWindowsMethodChannelName,
      )..install();
      addTearDown(recorder.uninstall);
      final transaction = AtlasVaultPairingTransaction.fromJson(
        _transactionJson(vector),
      );
      final tampered = transaction.canonicalBytes()..last ^= 1;
      recorder.handler = (call) async {
        if (call.method == 'readPairingTransaction') return tampered;
        if (call.method == 'readStagedPairingArtifact') {
          return _artifactBytes(vector, 'offer');
        }
        return null;
      };

      await expectLater(
        AtlasWindowsPairingTransactionStore(channel: recorder.channel).read(),
        throwsA(isA<AtlasVaultPairingStorageException>()),
      );
      await expectLater(
        AtlasWindowsPairingArtifactStageStore(
          channel: recorder.channel,
        ).read(AtlasVaultPairingArtifactKind.acceptance),
        throwsA(isA<AtlasVaultPairingStorageException>()),
      );
      tampered.fillRange(0, tampered.length, 0);
      transaction.destroy();
    },
  );

  test('platform errors and descriptions expose no path or secret', () async {
    final recorder = AtlasVaultPairingMethodCallRecorder(
      channelName: atlasVaultWindowsMethodChannelName,
    )..install();
    addTearDown(recorder.uninstall);
    recorder.handler = (_) async => throw PlatformException(
      code: 'win32_5',
      message: r'C:\Users\private\pairing.atlaspair FAKE_EPHEMERAL_SECRET',
    );
    final transport = AtlasWindowsPairingArtifactTransport(
      channel: recorder.channel,
    );

    await expectLater(
      transport.pick(),
      throwsA(
        isA<AtlasVaultPairingStorageException>()
            .having(
              (error) => error.toString(),
              'path',
              isNot(contains(r'C:\Users')),
            )
            .having(
              (error) => error.toString(),
              'secret',
              isNot(contains('FAKE_EPHEMERAL_SECRET')),
            ),
      ),
    );
  });

  test('native sources declare protected state and path-free transport', () {
    final android = File(
      'android/app/src/main/kotlin/com/yutsukioka/jobagg/atlas/'
      'AtlasVaultAndroidStorage.kt',
    ).readAsStringSync();
    final windows = File(
      'windows/runner/atlas_vault_windows_storage.cpp',
    ).readAsStringSync();

    for (final purpose in <String>[
      'trusted-devices',
      'pairing-replay',
      'pairing-transaction',
    ]) {
      expect(android, contains(purpose));
      expect(windows, contains(purpose));
    }
    expect(android, contains('noBackupFilesDir'));
    expect(android, contains('ACTION_OPEN_DOCUMENT'));
    expect(android, contains('ACTION_CREATE_DOCUMENT'));
    expect(android, isNot(contains('takePersistableUriPermission')));
    expect(android, isNot(contains('FLAG_GRANT_PERSISTABLE_URI_PERMISSION')));
    expect(android, contains('MAX_PAIRING_STATE_BYTES = 2 * 1024 * 1024'));
    expect(windows, contains('CryptProtectData'));
    expect(windows, contains('CRYPTPROTECT_UI_FORBIDDEN'));
    expect(windows, isNot(contains('CRYPTPROTECT_LOCAL_MACHINE')));
    expect(windows, contains('FOS_DONTADDTORECENT'));
    expect(windows, contains('*.atlaspair'));
    expect(
      windows,
      contains('kPairingStatePlaintextMaximumLength = 2 * 1024 * 1024'),
    );
  });
}

final class _PairingJourney {
  _PairingJourney({
    required this.inviter,
    required this.invitee,
    required this.inviterIdentity,
    required this.inviteeIdentity,
    required this.inviterRegistry,
    required this.inviteeRegistry,
    required this.inviterTransactions,
    required this.inviteeTransactions,
    required this.inviterStage,
    required this.inviteeStage,
    required this.inviteeReplay,
    required this.mailbox,
    required this.inviterRuntime,
    required this.inviteeLocal,
    required this.inviteeKeys,
    required this.inviteeSelected,
    required this.bootstrap,
    required this.vaultId,
    required this.inviterEvents,
    required this.inviteeEvents,
    required this.clock,
  });

  final AtlasVaultTrustedPairingCoordinator inviter;
  final AtlasVaultTrustedPairingCoordinator invitee;
  final AtlasVaultPairingMemoryIdentityStore inviterIdentity;
  final AtlasVaultPairingMemoryIdentityStore inviteeIdentity;
  final AtlasVaultPairingMemoryRegistryStore inviterRegistry;
  final AtlasVaultPairingMemoryRegistryStore inviteeRegistry;
  final AtlasVaultPairingMemoryTransactionStore inviterTransactions;
  final AtlasVaultPairingMemoryTransactionStore inviteeTransactions;
  final AtlasVaultPairingMemoryStageStore inviterStage;
  final AtlasVaultPairingMemoryStageStore inviteeStage;
  final AtlasVaultPairingMemoryReplayStore inviteeReplay;
  final AtlasVaultPairingMailbox mailbox;
  final AtlasVaultPrivateStateRuntime inviterRuntime;
  final AtlasVaultPairingMemoryLocalStore inviteeLocal;
  final AtlasVaultPairingMemorySecureKeyStore inviteeKeys;
  final AtlasVaultPairingMemorySelectedVaultStore inviteeSelected;
  final AtlasVaultPairingBootstrap bootstrap;
  final String vaultId;
  final List<String> inviterEvents;
  final List<String> inviteeEvents;
  final _PairingClock clock;

  static Future<_PairingJourney> create(
    Map<String, Object?> vector, {
    AtlasVaultPairingCleanInstallDisposition inviteeCleanDisposition =
        AtlasVaultPairingCleanInstallDisposition.clean,
  }) async {
    final inviterEvents = <String>[];
    final inviteeEvents = <String>[];
    final inviterIdentity = AtlasVaultPairingMemoryIdentityStore(
      await _identitySecret(vector, 'inviter'),
    );
    final inviteeIdentity = AtlasVaultPairingMemoryIdentityStore(
      await _identitySecret(vector, 'invitee'),
    );
    final inviterRegistry = AtlasVaultPairingMemoryRegistryStore(
      events: inviterEvents,
    );
    final inviteeRegistry = AtlasVaultPairingMemoryRegistryStore(
      events: inviteeEvents,
    );
    final inviterReplay = AtlasVaultPairingMemoryReplayStore(
      events: inviterEvents,
    );
    final inviteeReplay = AtlasVaultPairingMemoryReplayStore(
      events: inviteeEvents,
    );
    final inviterTransactions = AtlasVaultPairingMemoryTransactionStore(
      events: inviterEvents,
    );
    final inviteeTransactions = AtlasVaultPairingMemoryTransactionStore(
      events: inviteeEvents,
    );
    final inviterStage = AtlasVaultPairingMemoryStageStore(
      events: inviterEvents,
    );
    final inviteeStage = AtlasVaultPairingMemoryStageStore(
      events: inviteeEvents,
    );
    final mailbox = AtlasVaultPairingMailbox();
    final inviterTransport = AtlasVaultPairingMemoryTransport(
      mailbox,
      events: inviterEvents,
    );
    final inviteeTransport = AtlasVaultPairingMemoryTransport(
      mailbox,
      events: inviteeEvents,
    );
    final inviterKeys = AtlasVaultPairingMemorySecureKeyStore(
      events: inviterEvents,
    );
    final inviteeKeys = AtlasVaultPairingMemorySecureKeyStore(
      events: inviteeEvents,
    );
    final inviterLocal = AtlasVaultPairingMemoryLocalStore(
      events: inviterEvents,
    );
    final inviteeLocal = AtlasVaultPairingMemoryLocalStore(
      events: inviteeEvents,
    );
    final inviterSelected = AtlasVaultPairingMemorySelectedVaultStore(
      events: inviterEvents,
    );
    final inviteeSelected = AtlasVaultPairingMemorySelectedVaultStore(
      events: inviteeEvents,
    );
    final bootstrap = AtlasVaultPairingBootstrap.fromJson(
      atlasVaultObject(vector['bootstrap']),
    );
    final vaultId = bootstrap.vaultMetadata.vaultId;
    final vaultKey = Uint8List.fromList(
      base64Decode(vector['test_only_vault_key_b64']! as String),
    );
    inviterKeys.values[vaultId] = Uint8List.fromList(vaultKey);
    inviterLocal.values[vaultId] = AtlasVaultLocalStore.fromJson(
      <String, Object?>{
        'format': AtlasVaultLocalStore.format,
        'version': AtlasVaultLocalStore.version,
        'store_id': '53000000-0000-4000-8000-000000000001',
        'created_at': '2026-08-15T10:00:00Z',
        'updated_at': '2026-08-15T10:00:00Z',
        'vault_metadata': bootstrap.vaultMetadata.toJson(),
        'records': <Object?>[
          for (final record in bootstrap.records) record.toJson(),
        ],
      },
    );
    inviterSelected.value = vaultId;
    final inviterRuntime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: inviterKeys,
      localStoreIO: inviterLocal,
    );
    final inviteeRuntime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: inviteeKeys,
      localStoreIO: inviteeLocal,
    );
    expect(
      await inviterRuntime.activateExisting(vaultId),
      AtlasVaultActivationResult.activated,
    );
    inviterEvents.clear();
    inviteeEvents.clear();
    final inviterDeterminism = AtlasVaultPairingDeterminism(seed: 10);
    final inviteeDeterminism = AtlasVaultPairingDeterminism(seed: 500);
    final clock = _PairingClock(DateTime.utc(2026, 8, 15, 10, 5));

    final inviter = AtlasVaultTrustedPairingCoordinator(
      identityStore: inviterIdentity,
      registryStore: inviterRegistry,
      replayStore: inviterReplay,
      transactionStore: inviterTransactions,
      stageStore: inviterStage,
      artifactTransport: inviterTransport,
      runtime: inviterRuntime,
      cleanInstallProbe: () async =>
          AtlasVaultPairingCleanInstallDisposition.existingVault,
      secureKeyStore: inviterKeys,
      localStoreIO: inviterLocal,
      selectedVaultStore: inviterSelected,
      uuidProvider: inviterDeterminism.uuid,
      randomBytes: inviterDeterminism.bytes,
      now: () => clock.value,
    );
    final invitee = AtlasVaultTrustedPairingCoordinator(
      identityStore: inviteeIdentity,
      registryStore: inviteeRegistry,
      replayStore: inviteeReplay,
      transactionStore: inviteeTransactions,
      stageStore: inviteeStage,
      artifactTransport: inviteeTransport,
      runtime: inviteeRuntime,
      cleanInstallProbe: () async => inviteeCleanDisposition,
      secureKeyStore: inviteeKeys,
      localStoreIO: inviteeLocal,
      selectedVaultStore: inviteeSelected,
      activateInstalledVault: (candidate) async {
        inviteeEvents.add('runtime.activate');
        return await inviteeRuntime.activateExisting(candidate) ==
            AtlasVaultActivationResult.activated;
      },
      uuidProvider: inviteeDeterminism.uuid,
      randomBytes: inviteeDeterminism.bytes,
      now: () => clock.value,
    );
    vaultKey.fillRange(0, vaultKey.length, 0);
    return _PairingJourney(
      inviter: inviter,
      invitee: invitee,
      inviterIdentity: inviterIdentity,
      inviteeIdentity: inviteeIdentity,
      inviterRegistry: inviterRegistry,
      inviteeRegistry: inviteeRegistry,
      inviterTransactions: inviterTransactions,
      inviteeTransactions: inviteeTransactions,
      inviterStage: inviterStage,
      inviteeStage: inviteeStage,
      inviteeReplay: inviteeReplay,
      mailbox: mailbox,
      inviterRuntime: inviterRuntime,
      inviteeLocal: inviteeLocal,
      inviteeKeys: inviteeKeys,
      inviteeSelected: inviteeSelected,
      bootstrap: bootstrap,
      vaultId: vaultId,
      inviterEvents: inviterEvents,
      inviteeEvents: inviteeEvents,
      clock: clock,
    );
  }

  Future<void> stop() async {
    await inviter.stop();
    await invitee.stop();
  }
}

final class _PairingClock {
  _PairingClock(this.value);

  DateTime value;
}

Future<void> _exchangeAcceptance(_PairingJourney journey) async {
  await journey.inviter.createPairingOffer();
  await journey.inviter.savePairingOffer();
  await journey.invitee.importPairingOffer();
  await journey.invitee.savePairingAcceptance();
  await journey.inviter.importPairingAcceptance();
}

Future<Uint8List> _identitySecret(
  Map<String, Object?> vector,
  String name,
) async {
  final data = atlasVaultObject(vector[name]);
  final identity = await AtlasVaultDeviceIdentity.fromPrivateKeys(
    signingPrivateSeed: Uint8List.fromList(
      base64Decode(data['signing_private_seed_b64']! as String),
    ),
    agreementPrivateKey: Uint8List.fromList(
      base64Decode(data['agreement_private_key_b64']! as String),
    ),
    createdAt: data['created_at']! as String,
    keyEpoch: data['key_epoch']! as int,
    expectedDeviceId: data['device_id']! as String,
  );
  final secret = identity.secretBundle();
  try {
    return secret.canonicalBytes();
  } finally {
    secret.destroy();
    identity.destroy();
  }
}

final class _PairingStores {
  const _PairingStores({
    required this.registry,
    required this.replay,
    required this.transaction,
    required this.staging,
    required this.transport,
  });

  factory _PairingStores.android(MethodChannel channel) => _PairingStores(
    registry: AtlasAndroidTrustedDeviceRegistryStore(channel: channel),
    replay: AtlasAndroidPairingReplayStore(channel: channel),
    transaction: AtlasAndroidPairingTransactionStore(channel: channel),
    staging: AtlasAndroidPairingArtifactStageStore(channel: channel),
    transport: AtlasAndroidPairingArtifactTransport(channel: channel),
  );

  factory _PairingStores.windows(MethodChannel channel) => _PairingStores(
    registry: AtlasWindowsTrustedDeviceRegistryStore(channel: channel),
    replay: AtlasWindowsPairingReplayStore(channel: channel),
    transaction: AtlasWindowsPairingTransactionStore(channel: channel),
    staging: AtlasWindowsPairingArtifactStageStore(channel: channel),
    transport: AtlasWindowsPairingArtifactTransport(channel: channel),
  );

  final AtlasVaultTrustedDeviceRegistryStore registry;
  final AtlasVaultPairingReplayStateStore replay;
  final AtlasVaultPairingTransactionStore transaction;
  final AtlasVaultPairingArtifactStageStore staging;
  final AtlasVaultPairingArtifactTransport transport;
}

Map<String, Object?> _transactionJson(Map<String, Object?> vector) {
  final artifacts = atlasVaultObject(vector['artifacts']);
  final offer = atlasVaultObject(artifacts['offer']);
  final acceptance = atlasVaultObject(artifacts['acceptance']);
  return <String, Object?>{
    'format': 'atlasvault-pairing-transaction',
    'version': 1,
    'transaction_id': '42000000-0000-4000-8000-000000000001',
    'revision': '42000000-0000-4000-8000-000000000002',
    'parent_revision': null,
    'role': 'invitee',
    'stage': 'acceptance_created',
    'created_at': '2026-08-15T10:00:00Z',
    'updated_at': '2026-08-15T10:01:00Z',
    'local_device_id': atlasVaultObject(vector['invitee'])['device_id'],
    'peer_device_id': atlasVaultObject(vector['inviter'])['device_id'],
    'transcript_sha256': vector['transcript_sha256'],
    'offer_sha256': offer['sha256'],
    'acceptance_sha256': acceptance['sha256'],
    'delivery_sha256': null,
    'acknowledgement_sha256': null,
    'bootstrap_sha256': null,
    'vault_id': null,
    'key_epoch': null,
    'ephemeral_private_key': vector['invitee_ephemeral_private_key_b64'],
    'store_sha256': null,
    'vault_key_sha256': null,
    'selection_committed': false,
    'staged_artifacts': <Object?>[
      <String, Object?>{
        'kind': 'offer',
        'sha256': offer['sha256'],
        'byte_count': _artifactBytes(vector, 'offer').length,
      },
      <String, Object?>{
        'kind': 'acceptance',
        'sha256': acceptance['sha256'],
        'byte_count': _artifactBytes(vector, 'acceptance').length,
      },
    ],
  };
}

Uint8List _artifactBytes(Map<String, Object?> vector, String kind) {
  final artifact = atlasVaultObject(
    atlasVaultObject(vector['artifacts'])[kind],
  );
  return Uint8List.fromList(base64Decode(artifact['canonical_b64']! as String));
}

AtlasVaultPairingReplayStore _largeReplayStore() {
  return AtlasVaultPairingReplayStore.fromJson(<String, Object?>{
    'format': 'atlasvault-pairing-replay',
    'version': 1,
    'local_device_id': 'avd1-${'a' * 64}',
    'revision': '43000000-0000-4000-8000-000000000001',
    'parent_revision': null,
    'created_at': '2026-08-15T10:00:00Z',
    'updated_at': '2026-08-15T10:01:00Z',
    'entries': <Object?>[
      for (var index = 1; index <= 400; index += 1)
        <String, Object?>{
          'kind': 'offer',
          'object_id':
              '43000000-0000-4000-8000-${index.toString().padLeft(12, '0')}',
          'transcript_sha256': 'b' * 64,
          'consumed_at': '2026-08-15T10:00:00Z',
          'expires_at': '2026-08-15T10:10:00Z',
        },
    ],
  });
}
