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
