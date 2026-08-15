import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

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
  });

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
    expect(windows, contains('CryptProtectData'));
    expect(windows, contains('CRYPTPROTECT_UI_FORBIDDEN'));
    expect(windows, isNot(contains('CRYPTPROTECT_LOCAL_MACHINE')));
    expect(windows, contains('FOS_DONTADDTORECENT'));
    expect(windows, contains('*.atlaspair'));
  });
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
