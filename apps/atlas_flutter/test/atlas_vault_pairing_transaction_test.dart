import 'dart:convert';
import 'dart:io';
import 'dart:async';
import 'package:atlas/atlas.dart' as app;
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
    expect(acknowledgementStage, lessThan(trust));

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

  test(
    'forged acknowledgement cannot enroll a third device or poison staging',
    () async {
      final journey = await _PairingJourney.create(vector);
      addTearDown(journey.stop);
      await _exchangeDelivery(journey);
      final deliveryArtifact = AtlasVaultPairingArtifact.fromCanonicalBytes(
        journey.inviterStage.values[AtlasVaultPairingArtifactKind.delivery]!,
      );
      final delivery = AtlasVaultSignedVaultKeyDelivery.fromJson(
        atlasVaultObject(deliveryArtifact.payload['signed_delivery']),
      );
      final attacker = await AtlasVaultDeviceIdentity.fromPrivateKeys(
        signingPrivateSeed: Uint8List.fromList(
          List<int>.generate(32, (i) => i + 1),
        ),
        agreementPrivateKey: Uint8List.fromList(
          List<int>.generate(32, (i) => i + 65),
        ),
        createdAt: '2026-08-15T10:00:00Z',
      );
      addTearDown(attacker.destroy);
      final acknowledgement =
          AtlasVaultPairingAcknowledgement.fromJson(<String, Object?>{
            'format': 'atlasvault-pairing-acknowledgement',
            'version': 1,
            'acknowledgement_id': '54000000-0000-4000-8000-000000000001',
            'delivery_id': delivery.delivery.deliveryId,
            'transcript_sha256': delivery.delivery.transcriptSha256,
            'inviter_device_id': delivery.delivery.inviterDeviceId,
            'invitee_device_id': attacker.deviceId,
            'vault_id': delivery.delivery.vaultId,
            'key_epoch': delivery.delivery.keyEpoch,
            'bootstrap_sha256': delivery.delivery.bootstrapSha256,
            'installed_at': '2026-08-15T10:09:00Z',
          });
      final forged = AtlasVaultSignedPairingAcknowledgement.fromJson(
        <String, Object?>{
          'format': 'atlasvault-signed-pairing-acknowledgement',
          'version': 1,
          'acknowledgement': acknowledgement.toJson(),
          'invitee': (await attacker.signDescriptor()).toJson(),
          'signature': base64Encode(
            await attacker.signBytes(<int>[
              ...utf8.encode(
                'atlasvault-pairing-acknowledgement-signature-v1:',
              ),
              ...acknowledgement.canonicalBytes(),
            ]),
          ),
        },
      );
      final forgedArtifact = AtlasVaultPairingArtifact.fromJson(
        <String, Object?>{
          'format': 'atlasvault-pairing-artifact',
          'version': 1,
          'kind': 'acknowledgement',
          'payload': <String, Object?>{
            'signed_acknowledgement': forged.toJson(),
          },
        },
      );
      journey.mailbox.bytes = Uint8List.fromList(
        forgedArtifact.canonicalBytes(),
      );

      final result = await journey.inviter.importPairingAcknowledgement();

      expect(
        result.disposition,
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
      );
      expect(
        journey.inviterTransactions.value?.stage,
        AtlasVaultPairingStage.deliverySaved,
      );
      expect(journey.inviterRegistry.value, isNull);
      expect(journey.inviterReplay.value, isNull);
      expect(
        journey.inviterStage.values,
        isNot(contains(AtlasVaultPairingArtifactKind.acknowledgement)),
      );
    },
  );

  test('foreign delivery fails before journaling or staging', () async {
    final journey = await _PairingJourney.create(vector);
    final foreign = await _PairingJourney.create(
      vector,
      inviterDeterminismSeed: 30,
      inviteeDeterminismSeed: 700,
    );
    addTearDown(journey.stop);
    addTearDown(foreign.stop);
    await _exchangeDelivery(journey);
    final expectedDelivery = Uint8List.fromList(journey.mailbox.bytes!);
    await _exchangeDelivery(foreign);
    journey.mailbox.bytes = Uint8List.fromList(foreign.mailbox.bytes!);

    final rejected = await journey.invitee.importKeyDelivery();

    expect(
      rejected.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviteeTransactions.value?.stage,
      AtlasVaultPairingStage.offerConsumed,
    );
    expect(journey.inviteeTransactions.value?.deliverySha256, isNull);
    expect(journey.inviteeTransactions.value?.bootstrapSha256, isNull);
    expect(journey.inviteeTransactions.value?.vaultId, isNull);
    expect(
      journey.inviteeStage.values,
      isNot(contains(AtlasVaultPairingArtifactKind.delivery)),
    );

    journey.mailbox.bytes = expectedDelivery;
    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady,
    );
  });

  test('delivery import remains resumable after its expiry', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviteeTransactionReplaceFailureStage:
          AtlasVaultPairingStage.storeCreated,
      inviteeTransactionReplaceFailures: 1,
    );
    addTearDown(journey.stop);
    await _exchangeDelivery(journey);

    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviteeTransactions.value?.stage,
      AtlasVaultPairingStage.deliveryImported,
    );
    journey.clock.value = DateTime.utc(2026, 8, 15, 10, 16);

    expect(
      (await journey.invitee.resumePairing()).disposition,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady,
    );
  });

  test('delivery intent permits exact retry after its expiry', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviteeTransactionReplaceFailureStage:
          AtlasVaultPairingStage.deliveryImported,
      inviteeTransactionReplaceFailures: 1,
    );
    addTearDown(journey.stop);
    await _exchangeDelivery(journey);
    final deliveryBytes = Uint8List.fromList(journey.mailbox.bytes!);
    addTearDown(() => deliveryBytes.fillRange(0, deliveryBytes.length, 0));

    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviteeTransactions.value?.stage,
      AtlasVaultPairingStage.offerConsumed,
    );
    expect(journey.inviteeTransactions.value?.deliverySha256, isNotNull);
    expect(
      journey.inviteeStage.values,
      contains(AtlasVaultPairingArtifactKind.delivery),
    );
    journey.clock.value = DateTime.utc(2026, 8, 15, 10, 16);
    journey.mailbox.bytes = Uint8List.fromList(deliveryBytes);

    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady,
    );
  });

  test('expired delivery is not handed to document transport', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);
    await journey.inviter.confirmCodesMatch();
    await journey.invitee.confirmCodesMatch();
    journey.clock.value = DateTime.utc(2026, 8, 15, 10, 16);

    final result = await journey.inviter.saveKeyDelivery();

    expect(
      result.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviterTransactions.value?.stage,
      AtlasVaultPairingStage.deliveryCreated,
    );
    expect(journey.mailbox.bytes, isNull);
  });

  test('delivery expiring during save is not marked saved', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);
    await journey.inviter.confirmCodesMatch();
    await journey.invitee.confirmCodesMatch();
    journey.inviterTransport.beforeSave = (_) async {
      journey.clock.value = DateTime.utc(2026, 8, 15, 10, 16);
    };

    final result = await journey.inviter.saveKeyDelivery();

    expect(
      result.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviterTransactions.value?.stage,
      AtlasVaultPairingStage.deliveryExportStarted,
    );
  });

  test('pairing uses the vault epoch instead of the identity epoch', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviterIdentityKeyEpoch: 7,
    );
    addTearDown(journey.stop);

    await journey.inviter.createPairingOffer();

    final secretBytes = await journey.inviterIdentity.loadPrimaryIdentity();
    final secret = AtlasVaultDeviceIdentitySecret.fromJson(
      atlasVaultObject(jsonDecode(utf8.decode(secretBytes!))),
    );
    addTearDown(secret.destroy);
    expect(secret.keyEpoch, 7);
    expect(journey.inviterTransactions.value?.keyEpoch, 1);
  });

  test('full inviter registry rejects acceptance before persistence', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    journey.inviterRegistry.value = await _trustedRegistry(
      localDeviceId:
          atlasVaultObject(vector['inviter'])['device_id']! as String,
      vaultId: journey.vaultId,
      peerCount: 64,
    );
    await _prepareAcceptance(journey);

    final result = await journey.inviter.importPairingAcceptance();

    expect(
      result.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviterTransactions.value?.stage,
      AtlasVaultPairingStage.offerSaved,
    );
    expect(
      journey.inviterStage.values,
      isNot(contains(AtlasVaultPairingArtifactKind.acceptance)),
    );
  });

  test(
    'already-trusted invitee rejects acceptance before persistence',
    () async {
      final journey = await _PairingJourney.create(vector);
      addTearDown(journey.stop);
      final invitee = await _identityFromVector(vector, 'invitee');
      try {
        journey.inviterRegistry.value = await _trustedRegistry(
          localDeviceId:
              atlasVaultObject(vector['inviter'])['device_id']! as String,
          vaultId: journey.vaultId,
          identities: <AtlasVaultDeviceIdentity>[invitee],
        );
        await _prepareAcceptance(journey);

        final result = await journey.inviter.importPairingAcceptance();

        expect(
          result.disposition,
          AtlasVaultTrustedPairingDisposition.recoveryRequired,
        );
        expect(
          journey.inviterTransactions.value?.stage,
          AtlasVaultPairingStage.offerSaved,
        );
        expect(
          journey.inviterStage.values,
          isNot(contains(AtlasVaultPairingArtifactKind.acceptance)),
        );
      } finally {
        invitee.destroy();
      }
    },
  );

  test('full invitee registry rejects offer before persistence', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    journey.inviteeRegistry.value = await _trustedRegistry(
      localDeviceId:
          atlasVaultObject(vector['invitee'])['device_id']! as String,
      vaultId: journey.vaultId,
      peerCount: 64,
    );
    await journey.inviter.createPairingOffer();
    await journey.inviter.savePairingOffer();

    final result = await journey.invitee.importPairingOffer();

    expect(
      result.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(journey.inviteeTransactions.value, isNull);
    expect(journey.inviteeStage.values, isEmpty);
  });

  test(
    'mismatched invitee registry rejects offer before persistence',
    () async {
      final journey = await _PairingJourney.create(vector);
      addTearDown(journey.stop);
      journey.inviteeRegistry.value = await _trustedRegistry(
        localDeviceId:
            atlasVaultObject(vector['inviter'])['device_id']! as String,
        vaultId: journey.vaultId,
      );
      await journey.inviter.createPairingOffer();
      await journey.inviter.savePairingOffer();

      final result = await journey.invitee.importPairingOffer();

      expect(
        result.disposition,
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
      );
      expect(journey.inviteeTransactions.value, isNull);
      expect(journey.inviteeStage.values, isEmpty);
    },
  );

  test('already-trusted inviter rejects offer before persistence', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    final inviter = await _identityFromVector(vector, 'inviter');
    try {
      journey.inviteeRegistry.value = await _trustedRegistry(
        localDeviceId:
            atlasVaultObject(vector['invitee'])['device_id']! as String,
        vaultId: journey.vaultId,
        identities: <AtlasVaultDeviceIdentity>[inviter],
      );
      await journey.inviter.createPairingOffer();
      await journey.inviter.savePairingOffer();

      final result = await journey.invitee.importPairingOffer();

      expect(
        result.disposition,
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
      );
      expect(journey.inviteeTransactions.value, isNull);
      expect(journey.inviteeStage.values, isEmpty);
    } finally {
      inviter.destroy();
    }
  });

  test('offer expiry derives from one captured issue time', () async {
    final samples = <DateTime>[
      DateTime.utc(2026, 8, 15, 10, 5, 0, 900),
      DateTime.utc(2026, 8, 15, 10, 5, 1, 100),
    ];
    var calls = 0;
    final journey = await _PairingJourney.create(
      vector,
      inviterNow: () {
        final index = calls < samples.length ? calls : samples.length - 1;
        calls += 1;
        return samples[index];
      },
    );
    addTearDown(journey.stop);

    final result = await journey.inviter.createPairingOffer();

    expect(result.disposition, AtlasVaultTrustedPairingDisposition.offerReady);
    expect(calls, 1);
    expect(result.expiresAt, '2026-08-15T10:15:00Z');
  });

  test('inviter requires an active encrypted vault', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await journey.inviterRuntime.deactivate();

    final result = await journey.inviter.createPairingOffer();

    expect(result.disposition, AtlasVaultTrustedPairingDisposition.unavailable);
    expect(journey.inviterTransactions.value, isNull);
    expect(journey.mailbox.bytes, isNull);
  });

  test('offer creation uses the shared transaction admission', () async {
    final admission = _RejectingPairingTransactionAdmission();
    final journey = await _PairingJourney.create(
      vector,
      inviterTransactionAdmission: admission,
    );
    addTearDown(journey.stop);

    await expectLater(
      journey.inviter.createPairingOffer(),
      throwsA(isA<StateError>()),
    );

    expect(admission.calls, 1);
    expect(journey.inviterTransactions.value, isNull);
    expect(journey.inviterStage.values, isEmpty);
  });

  test('initial artifacts are recoverable when staging fails', () async {
    final inviterFailure = await _PairingJourney.create(
      vector,
      inviterStageFailure: AtlasVaultPairingArtifactKind.offer,
    );
    addTearDown(inviterFailure.stop);

    expect(
      (await inviterFailure.inviter.createPairingOffer()).disposition,
      AtlasVaultTrustedPairingDisposition.unavailable,
    );
    expect(inviterFailure.inviterTransactions.value, isNotNull);
    expect(inviterFailure.inviterStage.values, isEmpty);
    expect(
      (await inviterFailure.inviter.discardPairing()).disposition,
      AtlasVaultTrustedPairingDisposition.identityReady,
    );

    final inviteeFailure = await _PairingJourney.create(
      vector,
      inviteeStageFailure: AtlasVaultPairingArtifactKind.acceptance,
    );
    addTearDown(inviteeFailure.stop);
    await inviteeFailure.inviter.createPairingOffer();
    await inviteeFailure.inviter.savePairingOffer();

    expect(
      (await inviteeFailure.invitee.importPairingOffer()).disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(inviteeFailure.inviteeTransactions.value, isNotNull);
    expect(
      inviteeFailure.inviteeStage.values.keys,
      contains(AtlasVaultPairingArtifactKind.offer),
    );
    expect(
      (await inviteeFailure.invitee.discardPairing()).disposition,
      AtlasVaultTrustedPairingDisposition.identityReady,
    );
    expect(inviteeFailure.inviteeStage.values, isEmpty);
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

  test('expired key request fails before delivery creation', () async {
    final journey = await _PairingJourney.create(vector);
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);
    journey.clock.value = journey.clock.value.add(const Duration(minutes: 11));

    final result = await journey.inviter.confirmCodesMatch();

    expect(
      result.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviterStage.values,
      isNot(contains(AtlasVaultPairingArtifactKind.delivery)),
    );
  });

  test(
    'selection clears ephemeral key and resume uses the protected vault key',
    () async {
      final journey = await _PairingJourney.create(
        vector,
        inviteeActivationFailures: 1,
      );
      addTearDown(journey.stop);
      await _exchangeAcceptance(journey);
      await journey.inviter.confirmCodesMatch();
      await journey.invitee.confirmCodesMatch();
      await journey.inviter.saveKeyDelivery();
      journey.clock.value = DateTime.utc(2026, 8, 15, 10, 9);

      expect(
        (await journey.invitee.importKeyDelivery()).disposition,
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
      );
      expect(journey.inviteeTransactions.value?.ephemeralPrivateKey, isNull);

      expect(
        (await journey.invitee.resumePairing()).disposition,
        AtlasVaultTrustedPairingDisposition.acknowledgementReady,
      );

      expect(journey.inviteeTransactions.value?.ephemeralPrivateKey, isNull);
      final acknowledgementArtifact =
          AtlasVaultPairingArtifact.fromCanonicalBytes(
            journey.inviteeStage.values[AtlasVaultPairingArtifactKind
                .acknowledgement]!,
          );
      final signed = AtlasVaultSignedPairingAcknowledgement.fromJson(
        atlasVaultObject(
          acknowledgementArtifact.payload['signed_acknowledgement'],
        ),
      );
      expect(signed.acknowledgement.installedAt, '2026-08-15T10:09:00Z');
    },
  );

  test('matching unjournaled selection resumes without recreation', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviteeTransactionReplaceFailureStage:
          AtlasVaultPairingStage.selectionCommitted,
      inviteeTransactionReplaceFailures: 1,
    );
    addTearDown(journey.stop);
    await _exchangeDelivery(journey);

    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviteeTransactions.value?.stage,
      AtlasVaultPairingStage.keyCreated,
    );
    expect(journey.inviteeSelected.value, journey.vaultId);

    expect(
      (await journey.invitee.resumePairing()).disposition,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady,
    );
    expect(
      journey.inviteeEvents.where((event) => event == 'selection.create'),
      hasLength(1),
    );
  });

  for (final failure
      in <(AtlasVaultPairingStage, AtlasVaultPairingStage, String)>[
        (
          AtlasVaultPairingStage.storeCreated,
          AtlasVaultPairingStage.deliveryImported,
          'store',
        ),
        (
          AtlasVaultPairingStage.keyCreated,
          AtlasVaultPairingStage.storeCreated,
          'key',
        ),
      ]) {
    test(
      '${failure.$3} create intent survives journal advance interruption',
      () async {
        final journey = await _PairingJourney.create(
          vector,
          inviteeTransactionReplaceFailureStage: failure.$1,
          inviteeTransactionReplaceFailures: 1,
        );
        addTearDown(journey.stop);
        await _exchangeDelivery(journey);

        final interrupted = await journey.invitee.importKeyDelivery();
        final transaction = journey.inviteeTransactions.value;

        expect(
          interrupted.disposition,
          AtlasVaultTrustedPairingDisposition.recoveryRequired,
        );
        expect(transaction?.stage, failure.$2);
        if (failure.$3 == 'store') {
          expect(transaction?.storeSha256, isNotNull);
        } else {
          expect(transaction?.vaultKeySha256, isNotNull);
        }
        expect(
          (await journey.invitee.resumePairing()).disposition,
          AtlasVaultTrustedPairingDisposition.acknowledgementReady,
        );
        expect(
          journey.inviteeEvents.where(
            (event) => event == '${failure.$3}.create',
          ),
          hasLength(1),
        );
      },
    );
  }

  test('orphaned generated delivery is authenticated and resumed', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviterTransactionReplaceFailureStage:
          AtlasVaultPairingStage.deliveryCreated,
      inviterTransactionReplaceFailures: 1,
    );
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);

    final interrupted = await journey.inviter.confirmCodesMatch();

    expect(
      interrupted.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviterTransactions.value?.stage,
      AtlasVaultPairingStage.sasConfirmed,
    );
    expect(
      journey.inviterStage.values,
      contains(AtlasVaultPairingArtifactKind.delivery),
    );
    expect(
      (await journey.inviter.resumePairing()).disposition,
      AtlasVaultTrustedPairingDisposition.deliveryReady,
    );
    expect(
      journey.inviterEvents.where((event) => event == 'stage.create:delivery'),
      hasLength(1),
    );
  });

  test('generated delivery intent is journaled before staging', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviterStageFailure: AtlasVaultPairingArtifactKind.delivery,
    );
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);

    final interrupted = await journey.inviter.confirmCodesMatch();
    final transaction = journey.inviterTransactions.value;

    expect(
      interrupted.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(transaction?.stage, AtlasVaultPairingStage.sasConfirmed);
    expect(transaction?.deliverySha256, isNotNull);
    expect(
      transaction?.stagedArtifacts.map((artifact) => artifact.kind),
      contains(AtlasVaultPairingArtifactKind.delivery),
    );
    expect(
      journey.inviterStage.values,
      isNot(contains(AtlasVaultPairingArtifactKind.delivery)),
    );
    expect(
      (await journey.inviter.discardPairing()).disposition,
      AtlasVaultTrustedPairingDisposition.identityReady,
    );
    expect(journey.inviterTransactions.value, isNull);
  });

  test('delivery export is resume-only before its save is journaled', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviterTransactionReplaceFailureStage:
          AtlasVaultPairingStage.deliverySaved,
      inviterTransactionReplaceFailures: 1,
    );
    addTearDown(journey.stop);
    await _exchangeAcceptance(journey);
    await journey.inviter.confirmCodesMatch();
    await journey.invitee.confirmCodesMatch();

    final interrupted = await journey.inviter.saveKeyDelivery();
    final discard = await journey.inviter.discardPairing();

    expect(
      interrupted.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      discard.disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(journey.inviterTransactions.value, isNotNull);
    expect(
      (await journey.inviter.resumePairing()).disposition,
      AtlasVaultTrustedPairingDisposition.deliveryReady,
    );
  });

  test(
    'invitee resumes replay consumption after SAS journal advance',
    () async {
      final journey = await _PairingJourney.create(
        vector,
        inviteeTransactionReplaceFailureStage:
            AtlasVaultPairingStage.offerConsumed,
        inviteeTransactionReplaceFailures: 1,
      );
      addTearDown(journey.stop);
      await _exchangeAcceptance(journey);

      final interrupted = await journey.invitee.confirmCodesMatch();

      expect(
        interrupted.disposition,
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
      );
      expect(
        journey.inviteeTransactions.value?.stage,
        AtlasVaultPairingStage.sasConfirmed,
      );
      expect(journey.inviteeReplay.value?.entries, hasLength(1));

      final resumed = await journey.invitee.resumePairing();

      expect(
        resumed.disposition,
        AtlasVaultTrustedPairingDisposition.codesConfirmed,
      );
      expect(
        journey.inviteeTransactions.value?.stage,
        AtlasVaultPairingStage.offerConsumed,
      );
      expect(journey.inviteeReplay.value?.entries, hasLength(1));
    },
  );

  test(
    'presentation cancellation prevents a post-await pairing mutation',
    () async {
      final cleanInstallEntered = Completer<void>();
      final releaseCleanInstall = Completer<void>();
      final journey = await _PairingJourney.create(
        vector,
        inviteeCleanInstallProbe: () async {
          cleanInstallEntered.complete();
          await releaseCleanInstall.future;
          return AtlasVaultPairingCleanInstallDisposition.clean;
        },
      );
      final owner = AtlasVaultTrustedPairingPresentationOwner(
        coordinator: journey.invitee,
      );
      addTearDown(() async {
        if (!releaseCleanInstall.isCompleted) {
          releaseCleanInstall.complete();
        }
        await owner.stopAndDrain();
        owner.dispose();
        await journey.inviter.stop();
      });
      await journey.inviter.createPairingOffer();
      await journey.inviter.savePairingOffer();

      final importOperation = owner.importPairingOffer();
      await cleanInstallEntered.future;
      owner.clearSensitiveInput();
      releaseCleanInstall.complete();
      await importOperation;

      expect(journey.inviteeTransactions.value, isNull);
      expect(journey.inviteeStage.values, isEmpty);
    },
  );

  test('unjournaled invitee selection blocks destructive discard', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviteeTransactionReplaceFailureStage:
          AtlasVaultPairingStage.selectionCommitted,
      inviteeTransactionReplaceFailures: 1,
    );
    addTearDown(journey.stop);
    await _exchangeDelivery(journey);
    await journey.invitee.importKeyDelivery();

    expect(
      (await journey.invitee.discardPairing()).disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(journey.inviteeSelected.value, journey.vaultId);
    expect(journey.inviteeLocal.values, contains(journey.vaultId));
    expect(journey.inviteeKeys.values, contains(journey.vaultId));
    expect(journey.inviteeTransactions.value, isNotNull);
  });

  test(
    'matching active runtime resumes after activation journal failure',
    () async {
      final journey = await _PairingJourney.create(
        vector,
        inviteeTransactionReplaceFailureStage:
            AtlasVaultPairingStage.runtimeActivated,
        inviteeTransactionReplaceFailures: 1,
      );
      addTearDown(journey.stop);
      await _exchangeDelivery(journey);

      final interrupted = await journey.invitee.importKeyDelivery();

      expect(
        interrupted.disposition,
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
      );
      expect(
        journey.inviteeTransactions.value?.stage,
        AtlasVaultPairingStage.selectionCommitted,
      );
      expect(
        journey.inviteeEvents.where((event) => event == 'runtime.activate'),
        hasLength(1),
      );

      expect(
        (await journey.invitee.resumePairing()).disposition,
        AtlasVaultTrustedPairingDisposition.acknowledgementReady,
      );
      expect(
        journey.inviteeEvents.where((event) => event == 'runtime.activate'),
        hasLength(1),
      );
    },
  );

  test(
    'active store mutation resumes after activation journal failure',
    () async {
      final journey = await _PairingJourney.create(
        vector,
        inviteeTransactionReplaceFailureStage:
            AtlasVaultPairingStage.runtimeActivated,
        inviteeTransactionReplaceFailures: 1,
      );
      addTearDown(journey.stop);
      await _exchangeDelivery(journey);

      expect(
        (await journey.invitee.importKeyDelivery()).disposition,
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
      );
      expect(
        journey.inviteeTransactions.value?.stage,
        AtlasVaultPairingStage.selectionCommitted,
      );
      await journey.inviteeRuntime.saveSearch(
        app.AtlasSavedSearch(
          name: 'Mutation after activation',
          request: const app.AtlasSearchRequest(text: 'recovery'),
          createdAt: '2026-08-15T10:09:00Z',
          updatedAt: '2026-08-15T10:09:00Z',
        ),
      );

      expect(
        (await journey.invitee.resumePairing()).disposition,
        AtlasVaultTrustedPairingDisposition.acknowledgementReady,
      );
      final snapshot = await journey.inviteeRuntime.read();
      expect(
        snapshot.savedSearches.map((search) => search.name),
        contains('Mutation after activation'),
      );
    },
  );

  test(
    'locked store mutation resumes after activation journal failure',
    () async {
      final journey = await _PairingJourney.create(
        vector,
        inviteeTransactionReplaceFailureStage:
            AtlasVaultPairingStage.runtimeActivated,
        inviteeTransactionReplaceFailures: 1,
      );
      addTearDown(journey.stop);
      await _exchangeDelivery(journey);

      expect(
        (await journey.invitee.importKeyDelivery()).disposition,
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
      );
      await journey.inviteeRuntime.saveSearch(
        app.AtlasSavedSearch(
          name: 'Mutation before restart',
          request: const app.AtlasSearchRequest(text: 'locked recovery'),
          createdAt: '2026-08-15T10:09:00Z',
          updatedAt: '2026-08-15T10:09:00Z',
        ),
      );
      await journey.inviteeRuntime.deactivate();

      expect(
        (await journey.invitee.resumePairing()).disposition,
        AtlasVaultTrustedPairingDisposition.acknowledgementReady,
      );
      expect(journey.inviteeRuntime.isActiveVault(journey.vaultId), isTrue);
      final snapshot = await journey.inviteeRuntime.read();
      expect(
        snapshot.savedSearches.map((search) => search.name),
        contains('Mutation before restart'),
      );
    },
  );

  test('invitee trust retry uses the journaled installation time', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviteeTransactionReplaceFailureStage:
          AtlasVaultPairingStage.trustCommitted,
      inviteeTransactionReplaceFailures: 1,
    );
    addTearDown(journey.stop);
    await _exchangeDelivery(journey);
    journey.clock.value = DateTime.utc(2026, 8, 15, 10, 9);

    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    );
    expect(
      journey.inviteeTransactions.value?.stage,
      AtlasVaultPairingStage.runtimeActivated,
    );
    final committed = journey.inviteeRegistry.value!.devices.single;
    expect(committed.linkedAt, '2026-08-15T10:09:00Z');
    journey.clock.value = DateTime.utc(2026, 8, 15, 10, 10);

    expect(
      (await journey.invitee.resumePairing()).disposition,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady,
    );
    expect(journey.inviteeRegistry.value!.devices.single, committed);
  });

  test('acknowledgement-saved resume completes cleanup idempotently', () async {
    final journey = await _PairingJourney.create(
      vector,
      inviteeTransactionDeleteFailures: 1,
    );
    addTearDown(journey.stop);
    await _exchangeDelivery(journey);
    expect(
      (await journey.invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady,
    );

    await expectLater(
      journey.invitee.savePairingAcknowledgement(),
      throwsA(isA<StateError>()),
    );
    expect(
      journey.inviteeTransactions.value?.stage,
      AtlasVaultPairingStage.acknowledgementSaved,
    );
    expect(journey.inviteeStage.values, isEmpty);

    expect(
      (await journey.invitee.resumePairing()).disposition,
      AtlasVaultTrustedPairingDisposition.completed,
    );
    expect(journey.inviteeTransactions.value, isNull);
  });

  test(
    'inviter trust-committed resume completes cleanup idempotently',
    () async {
      final journey = await _PairingJourney.create(
        vector,
        inviterTransactionDeleteFailures: 1,
      );
      addTearDown(journey.stop);
      await _exchangeDelivery(journey);
      expect(
        (await journey.invitee.importKeyDelivery()).disposition,
        AtlasVaultTrustedPairingDisposition.acknowledgementReady,
      );
      expect(
        (await journey.invitee.savePairingAcknowledgement()).disposition,
        AtlasVaultTrustedPairingDisposition.completed,
      );

      await expectLater(
        journey.inviter.importPairingAcknowledgement(),
        throwsA(isA<StateError>()),
      );
      expect(
        journey.inviterTransactions.value?.stage,
        AtlasVaultPairingStage.trustCommitted,
      );
      expect(journey.inviterStage.values, isEmpty);
      expect(
        (await journey.inviter.resumePairing()).disposition,
        AtlasVaultTrustedPairingDisposition.completed,
      );
      expect(journey.inviterTransactions.value, isNull);
    },
  );

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

  test('pairing transaction accepts the full signed key epoch range', () {
    final transaction = AtlasVaultPairingTransaction.fromJson(<String, Object?>{
      ..._transactionJson(vector),
      'key_epoch': atlasVaultMaximumDeviceKeyEpoch,
    });

    expect(transaction.keyEpoch, atlasVaultMaximumDeviceKeyEpoch);
    expect(
      () => AtlasVaultPairingTransaction.fromJson(<String, Object?>{
        ..._transactionJson(vector),
        'key_epoch': atlasVaultMaximumDeviceKeyEpoch + 1,
      }),
      throwsA(isA<AtlasVaultPairingTransactionException>()),
    );
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
    expect(android, contains('val restored = readEncryptedDocument(uri)'));
    expect(android, contains('MessageDigest.isEqual(bytes, restored)'));
    expect(android, contains('restored.fill(0)'));
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
    required this.inviterReplay,
    required this.inviteeReplay,
    required this.inviterTransport,
    required this.mailbox,
    required this.inviterRuntime,
    required this.inviteeRuntime,
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
  final AtlasVaultPairingMemoryReplayStore inviterReplay;
  final AtlasVaultPairingMemoryReplayStore inviteeReplay;
  final AtlasVaultPairingMemoryTransport inviterTransport;
  final AtlasVaultPairingMailbox mailbox;
  final AtlasVaultPrivateStateRuntime inviterRuntime;
  final AtlasVaultPrivateStateRuntime inviteeRuntime;
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
    AtlasVaultPairingArtifactKind? inviterStageFailure,
    AtlasVaultPairingArtifactKind? inviteeStageFailure,
    int inviteeActivationFailures = 0,
    AtlasVaultPairingStage? inviterTransactionReplaceFailureStage,
    int inviterTransactionReplaceFailures = 0,
    int inviterTransactionDeleteFailures = 0,
    AtlasVaultPairingStage? inviteeTransactionReplaceFailureStage,
    int inviteeTransactionReplaceFailures = 0,
    int inviteeTransactionDeleteFailures = 0,
    int inviterDeterminismSeed = 10,
    int inviteeDeterminismSeed = 500,
    Future<AtlasVaultPairingCleanInstallDisposition> Function()?
    inviteeCleanInstallProbe,
    DateTime Function()? inviterNow,
    int inviterIdentityKeyEpoch = 1,
    AtlasVaultTrustedPairingTransactionAdmission? inviterTransactionAdmission,
  }) async {
    final inviterEvents = <String>[];
    final inviteeEvents = <String>[];
    final inviterIdentity = AtlasVaultPairingMemoryIdentityStore(
      await _identitySecret(
        vector,
        'inviter',
        keyEpoch: inviterIdentityKeyEpoch,
      ),
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
      failReplaceStage: inviterTransactionReplaceFailureStage,
      failReplaceCount: inviterTransactionReplaceFailures,
      failDeleteCount: inviterTransactionDeleteFailures,
    );
    final inviteeTransactions = AtlasVaultPairingMemoryTransactionStore(
      events: inviteeEvents,
      failReplaceStage: inviteeTransactionReplaceFailureStage,
      failReplaceCount: inviteeTransactionReplaceFailures,
      failDeleteCount: inviteeTransactionDeleteFailures,
    );
    final inviterStage = AtlasVaultPairingMemoryStageStore(
      events: inviterEvents,
      failCreateKind: inviterStageFailure,
    );
    final inviteeStage = AtlasVaultPairingMemoryStageStore(
      events: inviteeEvents,
      failCreateKind: inviteeStageFailure,
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
    final inviterDeterminism = AtlasVaultPairingDeterminism(
      seed: inviterDeterminismSeed,
    );
    final inviteeDeterminism = AtlasVaultPairingDeterminism(
      seed: inviteeDeterminismSeed,
    );
    final clock = _PairingClock(DateTime.utc(2026, 8, 15, 10, 5));
    var activationFailuresRemaining = inviteeActivationFailures;

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
      now: inviterNow ?? (() => clock.value),
      transactionAdmission: inviterTransactionAdmission,
    );
    final invitee = AtlasVaultTrustedPairingCoordinator(
      identityStore: inviteeIdentity,
      registryStore: inviteeRegistry,
      replayStore: inviteeReplay,
      transactionStore: inviteeTransactions,
      stageStore: inviteeStage,
      artifactTransport: inviteeTransport,
      runtime: inviteeRuntime,
      cleanInstallProbe:
          inviteeCleanInstallProbe ?? (() async => inviteeCleanDisposition),
      secureKeyStore: inviteeKeys,
      localStoreIO: inviteeLocal,
      selectedVaultStore: inviteeSelected,
      activateInstalledVault: (candidate) async {
        inviteeEvents.add('runtime.activate');
        if (activationFailuresRemaining > 0) {
          activationFailuresRemaining -= 1;
          return false;
        }
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
      inviterReplay: inviterReplay,
      inviteeReplay: inviteeReplay,
      inviterTransport: inviterTransport,
      mailbox: mailbox,
      inviterRuntime: inviterRuntime,
      inviteeRuntime: inviteeRuntime,
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
  await _prepareAcceptance(journey);
  await journey.inviter.importPairingAcceptance();
}

Future<void> _prepareAcceptance(_PairingJourney journey) async {
  await journey.inviter.createPairingOffer();
  await journey.inviter.savePairingOffer();
  await journey.invitee.importPairingOffer();
  await journey.invitee.savePairingAcceptance();
}

Future<void> _exchangeDelivery(_PairingJourney journey) async {
  await _exchangeAcceptance(journey);
  await journey.inviter.confirmCodesMatch();
  await journey.invitee.confirmCodesMatch();
  await journey.inviter.saveKeyDelivery();
}

Future<Uint8List> _identitySecret(
  Map<String, Object?> vector,
  String name, {
  int? keyEpoch,
}) async {
  final identity = await _identityFromVector(vector, name, keyEpoch: keyEpoch);
  final secret = identity.secretBundle();
  try {
    return secret.canonicalBytes();
  } finally {
    secret.destroy();
    identity.destroy();
  }
}

Future<AtlasVaultDeviceIdentity> _identityFromVector(
  Map<String, Object?> vector,
  String name, {
  int? keyEpoch,
}) async {
  final data = atlasVaultObject(vector[name]);
  return AtlasVaultDeviceIdentity.fromPrivateKeys(
    signingPrivateSeed: Uint8List.fromList(
      base64Decode(data['signing_private_seed_b64']! as String),
    ),
    agreementPrivateKey: Uint8List.fromList(
      base64Decode(data['agreement_private_key_b64']! as String),
    ),
    createdAt: data['created_at']! as String,
    keyEpoch: keyEpoch ?? data['key_epoch']! as int,
    expectedDeviceId: data['device_id']! as String,
  );
}

Future<AtlasVaultTrustedDeviceRegistry> _trustedRegistry({
  required String localDeviceId,
  required String vaultId,
  int peerCount = 0,
  List<AtlasVaultDeviceIdentity> identities =
      const <AtlasVaultDeviceIdentity>[],
}) async {
  final generated = <AtlasVaultDeviceIdentity>[];
  final all = <AtlasVaultDeviceIdentity>[...identities];
  try {
    for (var index = 0; index < peerCount; index += 1) {
      final signing = Uint8List.fromList(
        List<int>.generate(
          32,
          (offset) => ((index + 1) * 37 + offset * 11) & 0xff,
        ),
      );
      final agreement = Uint8List.fromList(
        List<int>.generate(
          32,
          (offset) => ((index + 1) * 53 + offset * 17 + 1) & 0xff,
        ),
      );
      final identity = await AtlasVaultDeviceIdentity.fromPrivateKeys(
        signingPrivateSeed: signing,
        agreementPrivateKey: agreement,
        createdAt: '2026-08-15T10:00:00Z',
      );
      signing.fillRange(0, signing.length, 0);
      agreement.fillRange(0, agreement.length, 0);
      generated.add(identity);
      all.add(identity);
    }
    final peers = <AtlasVaultTrustedDevicePeer>[];
    for (var index = 0; index < all.length; index += 1) {
      final identity = all[index];
      if (identity.deviceId == localDeviceId) {
        throw StateError('test identity collision');
      }
      peers.add(
        AtlasVaultTrustedDevicePeer.fromJson(<String, Object?>{
          'peer_device_id': identity.deviceId,
          'peer_descriptor': (await identity.signDescriptor()).toJson(),
          'pairing_transcript_sha256': 'a' * 64,
          'linked_at': '2026-08-15T10:00:00Z',
          'role': 'inviter',
          'vault_id': vaultId,
          'key_epoch': 1,
          'delivery_id':
              '65000000-0000-4000-8000-${(index + 1).toString().padLeft(12, '0')}',
          'acknowledgement_sha256': 'b' * 64,
        }),
      );
    }
    peers.sort(
      (left, right) => left.peerDeviceId.compareTo(right.peerDeviceId),
    );
    return AtlasVaultTrustedDeviceRegistry.fromJson(<String, Object?>{
      'format': 'atlasvault-trusted-device-registry',
      'version': 1,
      'local_device_id': localDeviceId,
      'revision': '65000000-0000-4000-8000-000000000099',
      'parent_revision': null,
      'created_at': '2026-08-15T10:00:00Z',
      'updated_at': '2026-08-15T10:00:00Z',
      'devices': <Object?>[for (final peer in peers) peer.toJson()],
    });
  } finally {
    for (final identity in generated) {
      identity.destroy();
    }
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

final class _RejectingPairingTransactionAdmission
    implements AtlasVaultTrustedPairingTransactionAdmission {
  int calls = 0;

  @override
  Future<T> runTrustedPairingTransaction<T>(
    Future<T> Function() operation,
  ) async {
    calls += 1;
    throw StateError('pairing admission rejected');
  }
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
    'installed_at': null,
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
