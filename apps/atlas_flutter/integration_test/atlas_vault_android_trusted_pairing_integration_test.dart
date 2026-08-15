import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/support/atlas_vault_vector_loader.dart';

const _stageEnvironment = 'ATLAS_ANDROID_TRUSTED_PAIRING_STAGE';
const _tamperEnvironment = 'ATLAS_ANDROID_TRUSTED_PAIRING_EXPECT_TAMPER';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Android protected pairing state persists across processes', (
    tester,
  ) async {
    if (!Platform.isAndroid) return;
    const configuredStage = String.fromEnvironment(_stageEnvironment);
    const configuredTamper = bool.fromEnvironment(_tamperEnvironment);
    final stage = Platform.environment[_stageEnvironment] ?? configuredStage;
    final expectTamper =
        Platform.environment[_tamperEnvironment] == 'true' || configuredTamper;
    if (stage != 'prepare' && stage != 'verify') {
      fail('Android pairing integration environment is invalid.');
    }

    final vector = loadAtlasVaultVector(
      'atlasvault_trusted_pairing_delivery_vectors_v1.json',
    );
    final scenario = _AndroidPairingScenario(vector);
    if (stage == 'prepare') {
      await scenario.prepare();
      tester.printToConsole(
        'Android pairing prepare passed with protected transaction state.',
      );
      return;
    }
    if (expectTamper) {
      await scenario.verifyTamperRejected();
      tester.printToConsole('Android pairing transaction tamper rejected.');
      return;
    }
    await scenario.verifyAndCleanStagedSecrets();
    tester.printToConsole(
      'Android pairing fresh-process verification and staged cleanup passed.',
    );
  });
}

final class _AndroidPairingScenario {
  _AndroidPairingScenario(this.vector)
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
  final registryStore = AtlasAndroidTrustedDeviceRegistryStore();
  final replayStore = AtlasAndroidPairingReplayStore();
  final transactionStore = AtlasAndroidPairingTransactionStore();
  final stageStore = AtlasAndroidPairingArtifactStageStore();

  Future<void> prepare() async {
    if (await registryStore.read() != null ||
        await replayStore.read() != null ||
        await transactionStore.read() != null ||
        await stageStore.read(artifact.kind) != null) {
      fail('Android pairing integration state is not empty.');
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
