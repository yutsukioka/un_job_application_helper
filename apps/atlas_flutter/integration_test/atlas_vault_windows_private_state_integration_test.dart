import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_windows.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Windows encrypted private state persists across processes', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    final stage = Platform.environment['ATLAS_WINDOWS_PRIVATE_TEST_STAGE'];
    final vaultId = Platform.environment['ATLAS_WINDOWS_PRIVATE_TEST_VAULT_ID'];
    if ((stage != 'prepare' && stage != 'verify') ||
        vaultId == null ||
        vaultId.isEmpty) {
      fail('Windows private-state integration environment is invalid.');
    }

    const searchSentinel = 'PRIVATE_WINDOWS_SEARCH_SENTINEL';
    const noteSentinel = 'PRIVATE_WINDOWS_TRACKER_NOTE_SENTINEL';
    const updatedNoteSentinel = 'PRIVATE_WINDOWS_TRACKER_UPDATED_SENTINEL';
    final key = Uint8List.fromList(
      List<int>.generate(32, (index) => (index + 151) & 0xff),
    );
    final keyStore = AtlasWindowsVaultSecureKeyStore();
    final localStore = AtlasWindowsVaultLocalStoreIO();
    final cacheFile = File(
      '${Directory.systemTemp.path}/atlas_phase2e5_${vaultId}_public.json',
    );
    final identifiers = stage == 'prepare'
        ? <String>[
            '10000000-0000-4000-8000-000000000301',
            '20000000-0000-4000-8000-000000000301',
            '10000000-0000-4000-8000-000000000302',
            '20000000-0000-4000-8000-000000000302',
          ]
        : <String>[
            '10000000-0000-4000-8000-000000000303',
            '20000000-0000-4000-8000-000000000303',
          ];
    var nonceSeed = stage == 'prepare' ? 111 : 121;
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      now: () => DateTime.utc(2026, 8, 1, stage == 'prepare' ? 12 : 13),
      uuidProvider: () => identifiers.removeAt(0),
      nonceProvider: () => Uint8List(12)..fillRange(0, 12, nonceSeed++),
    );
    final cacheStore = AtlasLocalCacheStore(
      file: cacheFile,
      now: () => DateTime.utc(2026, 8, 1, 13),
      privateStateProtectionActive: () => runtime.isActive,
    );
    final transport = _WindowsPrivateIntegrationTransport();
    final controller = AtlasAppController(
      initialBaseURL: Uri.parse('http://127.0.0.1:8765'),
      clientFactory: (baseURL) =>
          AtlasAPIClient(baseURL: baseURL, transport: transport),
      localCacheStore: cacheStore,
      privateStatePersistence: runtime,
      now: () => DateTime.utc(2026, 8, 1, 13),
    );

    try {
      if (stage == 'prepare') {
        await keyStore.createVaultKey(vaultId, key);
        await localStore.create(vaultId, _emptyStore(vaultId));
        controller.cacheSavedAt = DateTime.utc(2026, 8, 1, 12);
        expect(
          await controller.activateExistingAtlasVault(vaultId),
          AtlasVaultActivationResult.activated,
        );
        transport.resetPrivateCalls();
        controller.updateQuery(searchSentinel);
        await controller.saveCurrentSearch();
        await runtime.saveTrackerRecord(
          AtlasApplicationRecord(
            id: 'windows-private-record',
            jobKey: 'windows:private-job',
            status: 'saved',
            notes: noteSentinel,
            updatedAt: '2026-08-01T12:00:00Z',
          ),
        );
        await controller.testConnection(Uri.parse('http://127.0.0.1:8765'));
        expect(transport.privateReadCalls, 0);
        expect(transport.privateWriteCalls, 0);
        final encryptedStore = await localStore.read(vaultId);
        expect(encryptedStore, isNotNull);
        final rawStore = utf8.decode(encryptedStore!.canonicalBytes());
        expect(rawStore, isNot(contains(searchSentinel)));
        expect(rawStore, isNot(contains(noteSentinel)));
        final rawCache = await cacheFile.readAsString();
        expect(rawCache, isNot(contains(searchSentinel)));
        expect(rawCache, isNot(contains(noteSentinel)));
        final cacheJson = jsonDecode(rawCache) as Map<String, Object?>;
        expect(cacheJson['saved_searches'], isEmpty);
        expect(cacheJson['tracker_records'], isEmpty);
        tester.printToConsole('Windows private-state prepare passed.');
        return;
      }

      expect(
        await controller.activateExistingAtlasVault(vaultId),
        AtlasVaultActivationResult.activated,
      );
      expect(controller.savedSearches.single.request.text, searchSentinel);
      expect(controller.trackerRecords.single.notes, noteSentinel);
      transport.resetPrivateCalls();
      controller.cacheSavedAt = DateTime.utc(2026, 8, 1, 13);
      final committed = await runtime.saveTrackerRecord(
        AtlasApplicationRecord(
          id: 'windows-private-record',
          jobKey: 'windows:private-job',
          status: 'applied',
          notes: updatedNoteSentinel,
          updatedAt: '2026-08-01T13:00:00Z',
        ),
      );
      expect(committed.trackerRecords.single.notes, updatedNoteSentinel);
      await controller.testConnection(Uri.parse('http://127.0.0.1:8765'));
      expect(controller.trackerRecords.single.notes, updatedNoteSentinel);
      expect(transport.privateReadCalls, 0);
      expect(transport.privateWriteCalls, 0);
      final encryptedStore = await localStore.read(vaultId);
      expect(encryptedStore, isNotNull);
      final rawStore = utf8.decode(encryptedStore!.canonicalBytes());
      expect(rawStore, isNot(contains(searchSentinel)));
      expect(rawStore, isNot(contains(noteSentinel)));
      expect(rawStore, isNot(contains(updatedNoteSentinel)));
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
      expect(rawCache, isNot(contains(updatedNoteSentinel)));
      await controller.deactivateAtlasVault();
      expect(runtime.isActive, isFalse);
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
      await localStore.delete(vaultId);
      await keyStore.deleteVaultKey(vaultId);
      expect(await localStore.read(vaultId), isNull);
      expect(await keyStore.containsVaultKey(vaultId), isFalse);
      if (await cacheFile.exists()) {
        await cacheFile.delete();
      }
      tester.printToConsole('Windows private-state verify passed.');
    } finally {
      controller.dispose();
      await runtime.deactivate();
      key.fillRange(0, key.length, 0);
    }
  });
}

vault.AtlasVaultLocalStore _emptyStore(String vaultId) {
  return vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': 'atlasvault-local-store',
    'version': 1,
    'store_id': '99999999-8888-4777-8666-555555555555',
    'created_at': '2026-08-01T12:00:00Z',
    'updated_at': '2026-08-01T12:00:00Z',
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

final class _WindowsPrivateIntegrationTransport implements AtlasTransport {
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
