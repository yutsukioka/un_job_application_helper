import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_android.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Android encrypted private-state activation and mutation', (
    tester,
  ) async {
    final suffix = DateTime.now().microsecondsSinceEpoch;
    final vaultId = 'private-integration-$suffix';
    const searchSentinel = 'PRIVATE_ANDROID_SEARCH_SENTINEL';
    const noteSentinel = 'PRIVATE_ANDROID_TRACKER_SENTINEL';
    final key = Uint8List.fromList(
      List<int>.generate(32, (index) => (index + 41) & 0xff),
    );
    final keyStore = AtlasAndroidVaultSecureKeyStore();
    final localStore = AtlasAndroidVaultLocalStoreIO();
    final cacheDirectory = await Directory.systemTemp.createTemp(
      'atlas_private_integration_',
    );
    final cacheFile = File('${cacheDirectory.path}/atlas-local-cache.json');
    final identifiers = <String>[
      '10000000-0000-4000-8000-000000000101',
      '20000000-0000-4000-8000-000000000101',
      '10000000-0000-4000-8000-000000000102',
      '20000000-0000-4000-8000-000000000102',
    ];
    var nonceSeed = 61;
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      now: () => DateTime.utc(2026, 7, 28, 12),
      uuidProvider: () => identifiers.removeAt(0),
      nonceProvider: () => Uint8List(12)..fillRange(0, 12, nonceSeed++),
    );
    final cacheStore = AtlasLocalCacheStore(
      file: cacheFile,
      now: () => DateTime.utc(2026, 7, 28, 12),
      privateStateProtectionActive: () => runtime.isActive,
    );
    final transport = _PrivateIntegrationTransport();
    final controller = AtlasAppController(
      initialBaseURL: Uri.parse('http://127.0.0.1:8765'),
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
      localCacheStore: cacheStore,
      privateStatePersistence: runtime,
      now: () => DateTime.utc(2026, 7, 28, 12),
    );

    addTearDown(() async {
      controller.dispose();
      await runtime.deactivate();
      await localStore.delete(vaultId);
      await keyStore.deleteVaultKey(vaultId);
      key.fillRange(0, key.length, 0);
      if (await cacheDirectory.exists()) {
        await cacheDirectory.delete(recursive: true);
      }
    });

    await keyStore.createVaultKey(vaultId, key);
    await localStore.create(vaultId, _emptyStore(vaultId));
    await controller.saveAndReload(Uri.parse('http://127.0.0.1:8765'));
    transport.resetPrivateCalls();

    expect(
      await controller.activateExistingAtlasVault(vaultId),
      AtlasVaultActivationResult.activated,
    );
    controller.updateQuery(searchSentinel);
    await controller.saveCurrentSearch();
    final trackerSnapshot = await runtime.saveTrackerRecord(
      AtlasApplicationRecord(
        id: 'fixture-application-$suffix',
        jobKey: 'fixture:$suffix',
        status: 'saved',
        notes: noteSentinel,
        updatedAt: '2026-07-28T12:00:00Z',
      ),
    );

    expect(transport.privateReadCalls, 0);
    expect(transport.privateWriteCalls, 0);
    expect(controller.savedSearches.single.request.text, searchSentinel);
    expect(trackerSnapshot.trackerRecords.single.jobKey, 'fixture:$suffix');

    final encryptedStore = await localStore.read(vaultId);
    expect(encryptedStore, isNotNull);
    final rawStore = utf8.decode(encryptedStore!.canonicalBytes());
    expect(rawStore, isNot(contains(searchSentinel)));
    expect(rawStore, isNot(contains(noteSentinel)));
    expect(rawStore, isNot(contains('saved_search')));
    expect(rawStore, isNot(contains('saved_job')));

    final payloadTypes = <vault.AtlasVaultPayloadType>[];
    for (final record in encryptedStore.records) {
      final plaintext = await vault.openAtlasVaultRecord(
        vaultKey: key,
        vaultId: vaultId,
        record: record,
      );
      try {
        payloadTypes.add(
          vault.AtlasVaultPayloadEnvelope.decodeJson(
            utf8.decode(plaintext),
          ).type,
        );
      } finally {
        plaintext.fillRange(0, plaintext.length, 0);
      }
    }
    expect(
      payloadTypes,
      containsAll(<vault.AtlasVaultPayloadType>[
        vault.AtlasVaultPayloadType.savedSearch,
        vault.AtlasVaultPayloadType.savedJob,
      ]),
    );

    final rawCache = await cacheFile.readAsString();
    expect(rawCache, isNot(contains(searchSentinel)));
    expect(rawCache, isNot(contains(noteSentinel)));
    final cacheJson = jsonDecode(rawCache) as Map<String, Object?>;
    expect(cacheJson['saved_searches'], isEmpty);
    expect(cacheJson['tracker_records'], isEmpty);

    await controller.deactivateAtlasVault();
    expect(runtime.isActive, isFalse);
    expect(controller.savedSearches, isEmpty);
    expect(controller.trackerRecords, isEmpty);
    tester.printToConsole(
      'AtlasVault Android private-state integration passed on fake data.',
    );
  });
}

vault.AtlasVaultLocalStore _emptyStore(String vaultId) {
  return vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': 'atlasvault-local-store',
    'version': 1,
    'store_id': '99999999-8888-4777-8666-555555555555',
    'created_at': '2026-07-28T12:00:00Z',
    'updated_at': '2026-07-28T12:00:00Z',
    'vault_metadata': <String, Object?>{
      'format': 'atlas-vault',
      'version': 1,
      'vault_id': vaultId,
      'crypto': <String, Object?>{
        'record_aead': 'AES-256-GCM',
        'kdf': 'Argon2id',
        'subkey_kdf': 'HKDF-SHA256',
        'key_wrap_aead': 'AES-256-GCM',
      },
      'key_wraps': <Object?>[],
    },
    'records': <Object?>[],
  });
}

final class _PrivateIntegrationTransport implements AtlasTransport {
  int privateReadCalls = 0;
  int privateWriteCalls = 0;

  void resetPrivateCalls() {
    privateReadCalls = 0;
    privateWriteCalls = 0;
  }

  @override
  Future<Object?> send(AtlasRequest request) async {
    switch (request.path) {
      case 'api/health':
        return <String, Object?>{
          'status': 'ok',
          'open_jobs': 1,
          'enabled_sources': 1,
        };
      case 'api/search':
        return <String, Object?>{
          'total': 1,
          'limit': 1,
          'offset': 0,
          'facets': <String, Object?>{},
          'facet_labels': <String, Object?>{},
          'unclassified_count': 0,
          'results': <Object?>[
            <String, Object?>{
              'job_key': 'fixture:public',
              'title': 'Public fixture',
              'organization': 'Fixture organization',
              'source_id': 'fixture',
              'status': 'open',
            },
          ],
        };
      case 'api/updates':
        return <String, Object?>{'recent_source_runs': <Object?>[]};
      case 'api/sources':
        return <String, Object?>{'sources': <Object?>[]};
      case 'api/saved-searches':
        if (request.method == 'GET') {
          privateReadCalls += 1;
          return <Object?>[];
        }
        privateWriteCalls += 1;
        throw StateError('Private compatibility write was not expected.');
      case 'api/tracker':
        privateReadCalls += 1;
        return <Object?>[];
      default:
        if (request.path.startsWith('api/tracker/jobs/')) {
          privateWriteCalls += 1;
          throw StateError('Private compatibility write was not expected.');
        }
        throw StateError('Unexpected public integration request.');
    }
  }
}
