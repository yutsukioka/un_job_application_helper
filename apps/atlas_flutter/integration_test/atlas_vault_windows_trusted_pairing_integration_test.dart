import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/support/atlas_vault_pairing_fakes.dart';
import '../test/support/atlas_vault_vector_loader.dart';

const _stageEnvironment = 'ATLAS_WINDOWS_TRUSTED_PAIRING_STAGE';
const _tamperEnvironment = 'ATLAS_WINDOWS_TRUSTED_PAIRING_EXPECT_TAMPER';
const _artifactDirectoryEnvironment = 'ATLAS_PAIRING_ARTIFACT_DIR';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Windows protected pairing state persists across processes', (
    tester,
  ) async {
    if (!Platform.isWindows) return;
    const configuredStage = String.fromEnvironment(_stageEnvironment);
    const configuredTamper = bool.fromEnvironment(_tamperEnvironment);
    final stage = Platform.environment[_stageEnvironment] ?? configuredStage;
    final expectTamper =
        Platform.environment[_tamperEnvironment] == 'true' || configuredTamper;
    if (stage != 'prepare' && stage != 'verify' && stage != 'journey') {
      fail('Windows pairing integration environment is invalid.');
    }

    final vector = loadAtlasVaultVector(
      'atlasvault_trusted_pairing_delivery_vectors_v1.json',
    );
    final scenario = _WindowsPairingScenario(vector);
    if (stage == 'journey') {
      final journey = await scenario.runExplicitRoleCycle();
      await _exchangePairingRing(
        vector,
        runtimeArtifacts: journey.artifacts,
        producer: 'windows-to-apple',
        consumer: 'android-to-windows',
      );
      tester.printToConsole(
        'Windows explicit inviter/invitee pairing and artifact exchange passed.',
      );
      return;
    }
    if (stage == 'prepare') {
      await scenario.prepare();
      tester.printToConsole(
        'Windows pairing prepare passed with current-user DPAPI state.',
      );
      return;
    }
    if (expectTamper) {
      await scenario.verifyTamperRejected();
      tester.printToConsole('Windows pairing transaction tamper rejected.');
      return;
    }
    await scenario.verifyAndCleanStagedSecrets();
    tester.printToConsole(
      'Windows pairing fresh-process verification and staged cleanup passed.',
    );
  });
}

final class _WindowsPairingScenario {
  _WindowsPairingScenario(this.vector)
    : registry = AtlasVaultTrustedDeviceRegistry.fromJson(
        atlasVaultObject(vector['trusted_registry']),
      ),
      replay = AtlasVaultPairingReplayStore.fromJson(
        atlasVaultObject(vector['replay_store']),
      ),
      transaction = AtlasVaultPairingTransaction.fromJson(
        _transactionJson(vector),
      ),
      artifact = AtlasVaultPairingArtifact.fromCanonicalBytes(
        _artifactBytes(vector, 'offer'),
      );

  final Map<String, Object?> vector;
  final AtlasVaultTrustedDeviceRegistry registry;
  final AtlasVaultPairingReplayStore replay;
  final AtlasVaultPairingTransaction transaction;
  final AtlasVaultPairingArtifact artifact;
  final registryStore = AtlasWindowsTrustedDeviceRegistryStore();
  final replayStore = AtlasWindowsPairingReplayStore();
  final transactionStore = AtlasWindowsPairingTransactionStore();
  final stageStore = AtlasWindowsPairingArtifactStageStore();

  Future<AtlasVaultPairingPlatformJourneyEvidence>
  runExplicitRoleCycle() async {
    final stores = AtlasVaultPairingPlatformStores(
      identity: AtlasWindowsDeviceIdentitySecretStore(),
      registry: registryStore,
      replay: replayStore,
      transaction: transactionStore,
      staging: stageStore,
      secureKey: AtlasWindowsVaultSecureKeyStore(),
      localStore: AtlasWindowsVaultLocalStoreIO(),
      selectedVault: AtlasWindowsSelectedVaultStore(),
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
    expect(invitee.sas, isNotEmpty);
    expect(inviter.sas, isNotEmpty);
    expect(invitee.installedRecordCount, 2);
    expect(inviter.installedRecordCount, 2);
    expect(invitee.tombstoneCount, 1);
    expect(inviter.tombstoneCount, 1);
    expect(invitee.artifacts.keys, AtlasVaultPairingArtifactKind.values);
    expect(inviter.artifacts.keys, AtlasVaultPairingArtifactKind.values);
    return inviter;
  }

  Future<void> prepare() async {
    if (await registryStore.read() != null ||
        await replayStore.read() != null ||
        await transactionStore.read() != null ||
        await stageStore.read(artifact.kind) != null) {
      fail('Windows pairing integration state is not empty.');
    }

    await registryStore.create(registry);
    await expectLater(
      registryStore.create(registry),
      throwsA(isA<AtlasVaultPairingStorageException>()),
    );
    await replayStore.create(replay);
    await expectLater(
      replayStore.create(replay),
      throwsA(isA<AtlasVaultPairingStorageException>()),
    );
    await transactionStore.create(transaction);
    await expectLater(
      transactionStore.create(transaction),
      throwsA(isA<AtlasVaultPairingStorageException>()),
    );
    await stageStore.create(artifact);
    await expectLater(
      stageStore.create(artifact),
      throwsA(isA<AtlasVaultPairingStorageException>()),
    );

    await registryStore.replace(
      registry,
      expectedSha256: await _digest(registry.canonicalBytes()),
    );
    await replayStore.replace(
      replay,
      expectedSha256: await _digest(replay.canonicalBytes()),
    );
    await transactionStore.replace(
      transaction,
      expectedSha256: await _digest(transaction.canonicalBytes()),
    );
    await expectLater(
      transactionStore.replace(transaction, expectedSha256: '0' * 64),
      throwsA(isA<AtlasVaultPairingStorageException>()),
    );
  }

  Future<void> verifyTamperRejected() async {
    expect((await registryStore.read())?.toJson(), registry.toJson());
    expect((await replayStore.read())?.toJson(), replay.toJson());
    await expectLater(
      transactionStore.read(),
      throwsA(isA<AtlasVaultPairingStorageException>()),
    );
  }

  Future<void> verifyAndCleanStagedSecrets() async {
    expect((await registryStore.read())?.toJson(), registry.toJson());
    final loadedReplay = await replayStore.read();
    expect(loadedReplay?.toJson(), replay.toJson());
    final duplicate = consumeAtlasVaultPairingReplay(
      loadedReplay!,
      loadedReplay.entries.first,
      revision: vector['unused_revision']! as String,
      updatedAt: vector['later_timestamp']! as String,
      currentTime: vector['verification_time']! as String,
    );
    expect(duplicate.outcome, AtlasVaultReplayConsumeOutcome.alreadyConsumed);
    expect((await transactionStore.read())?.toJson(), transaction.toJson());
    expect(
      (await stageStore.read(artifact.kind))?.canonicalBytes(),
      artifact.canonicalBytes(),
    );

    await stageStore.delete(
      artifact.kind,
      expectedSha256: await _digest(artifact.canonicalBytes()),
    );
    await transactionStore.delete(
      expectedSha256: await _digest(transaction.canonicalBytes()),
    );
    expect(await stageStore.read(artifact.kind), isNull);
    expect(await transactionStore.read(), isNull);
    transaction.destroy();
  }
}

Future<String> _digest(Uint8List bytes) async {
  try {
    return await atlasVaultSha256Hex(bytes);
  } finally {
    bytes.fillRange(0, bytes.length, 0);
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
    'staged_artifacts': <Object?>[],
  };
}

Uint8List _artifactBytes(Map<String, Object?> vector, String kind) {
  final artifact = atlasVaultObject(
    atlasVaultObject(vector['artifacts'])[kind],
  );
  return Uint8List.fromList(base64Decode(artifact['canonical_b64']! as String));
}

Future<void> _exchangePairingRing(
  Map<String, Object?> vector, {
  required Map<AtlasVaultPairingArtifactKind, Uint8List> runtimeArtifacts,
  required String producer,
  required String consumer,
}) async {
  const configuredDirectory = String.fromEnvironment(
    _artifactDirectoryEnvironment,
  );
  final directoryPath =
      Platform.environment[_artifactDirectoryEnvironment] ??
      configuredDirectory;
  if (directoryPath.isEmpty) return;
  final directory = Directory(directoryPath);
  await directory.create(recursive: true);
  final sentinel =
      atlasVaultObject(
            vector['expected_payloads'],
          )['unsupported_private_sentinel']!
          as String;
  await _verifyPairingArtifactSet(runtimeArtifacts, vector);
  for (final kind in AtlasVaultPairingArtifactKind.values) {
    final runtimeBytes = runtimeArtifacts[kind];
    expect(runtimeBytes, isNotNull);
    final bytes = Uint8List.fromList(runtimeBytes!);
    final digest = await atlasVaultSha256Hex(bytes);
    final runtimeArtifact = AtlasVaultPairingArtifact.fromCanonicalBytes(
      Uint8List.fromList(bytes),
    );
    expect(runtimeArtifact.kind, kind);
    expect(runtimeArtifact.canonicalBytes(), bytes);
    final produced = File(
      '${directory.path}/$producer-${kind.encoded}.atlaspair',
    );
    await produced.writeAsBytes(bytes, flush: true);
    await File(
      '${directory.path}/$producer-${kind.encoded}.sha256',
    ).writeAsString('$digest\n', flush: true);
    final producedReadBack = await produced.readAsBytes();
    expect(producedReadBack, bytes);
    expect(await atlasVaultSha256Hex(producedReadBack), digest);

    final tampered = Uint8List.fromList(bytes)..[0] ^= 1;
    expect(
      () => AtlasVaultPairingArtifact.fromCanonicalBytes(tampered),
      throwsA(isA<AtlasVaultKeyDeliveryException>()),
    );
    tampered.fillRange(0, tampered.length, 0);
  }

  final consumedArtifacts = <AtlasVaultPairingArtifactKind, Uint8List>{};
  for (final kind in AtlasVaultPairingArtifactKind.values) {
    final consumed = File(
      '${directory.path}/$consumer-${kind.encoded}.atlaspair',
    );
    expect(await consumed.exists(), isTrue);
    final consumedBytes = await consumed.readAsBytes();
    final artifact = AtlasVaultPairingArtifact.fromCanonicalBytes(
      Uint8List.fromList(consumedBytes),
    );
    expect(artifact.kind, kind);
    expect(artifact.canonicalBytes(), consumedBytes);
    final consumedDigest = File(
      '${directory.path}/$consumer-${kind.encoded}.sha256',
    );
    expect(await consumedDigest.exists(), isTrue);
    final consumerRecordedDigest = (await consumedDigest.readAsString()).trim();
    expect(consumerRecordedDigest, matches(RegExp(r'^[0-9a-f]{64}$')));
    final consumerCalculatedDigest = await atlasVaultSha256Hex(consumedBytes);
    expect(consumerCalculatedDigest, consumerRecordedDigest);
    final text = utf8.decode(consumedBytes);
    expect(text, isNot(contains(sentinel)));
    expect(text, isNot(contains('"vault_key"')));
    expect(text, isNot(contains('"private_key"')));
    consumedArtifacts[kind] = Uint8List.fromList(consumedBytes);

    final tampered = Uint8List.fromList(consumedBytes)..[0] ^= 1;
    expect(
      () => AtlasVaultPairingArtifact.fromCanonicalBytes(tampered),
      throwsA(isA<AtlasVaultKeyDeliveryException>()),
    );
    tampered.fillRange(0, tampered.length, 0);
  }
  await _verifyPairingArtifactSet(consumedArtifacts, vector);
}

Future<void> _verifyPairingArtifactSet(
  Map<AtlasVaultPairingArtifactKind, Uint8List> artifacts,
  Map<String, Object?> vector,
) async {
  expect(artifacts.keys, unorderedEquals(AtlasVaultPairingArtifactKind.values));
  final parsed = <AtlasVaultPairingArtifactKind, AtlasVaultPairingArtifact>{};
  for (final kind in AtlasVaultPairingArtifactKind.values) {
    final bytes = Uint8List.fromList(artifacts[kind]!);
    final artifact = AtlasVaultPairingArtifact.fromCanonicalBytes(bytes);
    expect(artifact.kind, kind);
    expect(artifact.canonicalBytes(), bytes);
    parsed[kind] = artifact;
  }

  final signedOffer = AtlasVaultSignedPairingOffer.fromJson(
    atlasVaultObject(
      parsed[AtlasVaultPairingArtifactKind.offer]!.payload['signed_offer'],
    ),
  );
  final acceptancePayload =
      parsed[AtlasVaultPairingArtifactKind.acceptance]!.payload;
  final signedAcceptance = AtlasVaultSignedPairingAcceptance.fromJson(
    atlasVaultObject(acceptancePayload['signed_acceptance']),
  );
  final signedRequest = AtlasVaultSignedPairingKeyRequest.fromJson(
    atlasVaultObject(acceptancePayload['signed_key_request']),
  );
  final deliveryPayload =
      parsed[AtlasVaultPairingArtifactKind.delivery]!.payload;
  final signedDelivery = AtlasVaultSignedVaultKeyDelivery.fromJson(
    atlasVaultObject(deliveryPayload['signed_delivery']),
  );
  final bootstrap = AtlasVaultPairingBootstrap.fromJson(
    atlasVaultObject(deliveryPayload['bootstrap']),
  );
  final signedAcknowledgement = AtlasVaultSignedPairingAcknowledgement.fromJson(
    atlasVaultObject(
      parsed[AtlasVaultPairingArtifactKind.acknowledgement]!
          .payload['signed_acknowledgement'],
    ),
  );

  final transcript = await atlasVaultPairingTranscriptSha256(
    signedOffer,
    signedAcceptance,
  );
  final inviteeProof = Uint8List.fromList(
    base64Decode(acceptancePayload['invitee_proof']! as String),
  );
  final inviterProof = Uint8List.fromList(
    base64Decode(deliveryPayload['inviter_proof']! as String),
  );
  AtlasVaultDeviceIdentity? identity;
  AtlasVaultPairingSession? session;
  try {
    identity = await _identityForDevice(
      vector,
      signedOffer.offer.inviter.descriptor.deviceId,
    );
    session = await verifyAtlasVaultPairingTranscript(
      localIdentity: identity,
      signedOffer: signedOffer,
      signedAcceptance: signedAcceptance,
      proofs: AtlasVaultPairingProofs(
        inviter: inviterProof,
        invitee: inviteeProof,
      ),
      currentTime: signedAcceptance.acceptance.acceptedAt,
      replayGuard: _RuntimeRingReplayGuard(),
    );
    expect(_hex(transcript), signedRequest.request.transcriptSha256);
    await verifyAtlasVaultPairingKeyRequest(
      signedRequest,
      transcriptSha256: transcript,
      inviterDeviceId: signedOffer.offer.inviter.descriptor.deviceId,
      inviteeDeviceId: signedAcceptance.acceptance.invitee.descriptor.deviceId,
      currentTime: signedRequest.request.issuedAt,
    );
    final delivery = await verifyAtlasVaultSignedVaultKeyDelivery(
      signedDelivery,
    );
    expect(delivery.transcriptSha256, _hex(transcript));
    expect(
      delivery.requestSha256,
      await atlasVaultSha256Hex(signedRequest.canonicalBytes()),
    );
    expect(
      delivery.bootstrapSha256,
      await atlasVaultSha256Hex(bootstrap.canonicalBytes()),
    );
    expect(delivery.inviterDeviceId, signedRequest.request.inviterDeviceId);
    expect(delivery.inviteeDeviceId, signedRequest.request.inviteeDeviceId);
    expect(delivery.expiresAt, signedRequest.request.expiresAt);
    await verifyAtlasVaultPairingAcknowledgement(
      signedAcknowledgement,
      delivery: signedDelivery,
      inviterDeviceId: delivery.inviterDeviceId,
      inviteeDeviceId: delivery.inviteeDeviceId,
    );
  } finally {
    session?.destroy();
    identity?.destroy();
    transcript.fillRange(0, transcript.length, 0);
    inviteeProof.fillRange(0, inviteeProof.length, 0);
    inviterProof.fillRange(0, inviterProof.length, 0);
  }
}

Future<AtlasVaultDeviceIdentity> _identityForDevice(
  Map<String, Object?> vector,
  String deviceId,
) async {
  for (final name in const <String>['inviter', 'invitee']) {
    final bytes = await atlasVaultPairingIdentitySecret(vector, name);
    AtlasVaultDeviceIdentitySecret? secret;
    try {
      secret = AtlasVaultDeviceIdentitySecret.fromJson(
        atlasVaultObject(jsonDecode(utf8.decode(bytes))),
      );
      if (secret.deviceId == deviceId) return await secret.loadIdentity();
    } finally {
      secret?.destroy();
      bytes.fillRange(0, bytes.length, 0);
    }
  }
  throw StateError('runtime pairing identity is not in the fake vector');
}

String _hex(Uint8List bytes) =>
    bytes.map((value) => value.toRadixString(16).padLeft(2, '0')).join();

final class _RuntimeRingReplayGuard implements AtlasVaultPairingReplayGuard {
  var _consumed = false;

  @override
  Future<AtlasVaultPairingReplayOutcome> consume({
    required String offerId,
    required Uint8List transcriptSha256,
    required String expiresAt,
  }) async {
    if (_consumed) return AtlasVaultPairingReplayOutcome.alreadyConsumed;
    _consumed = true;
    return AtlasVaultPairingReplayOutcome.accepted;
  }
}
