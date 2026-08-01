import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_windows_fakes.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('production assembly owns one side-effect-free Windows runtime', () {
    final source = File(
      'lib/features/app_shell/atlas_app.dart',
    ).readAsStringSync();

    expect(
      source,
      contains("import 'package:atlas/atlas_vault_windows.dart';"),
    );
    final windowsStart = source.indexOf('if (Platform.isWindows) {');
    final fallbackStart = source.indexOf(
      'if (!Platform.isAndroid)',
      windowsStart < 0 ? 0 : windowsStart,
    );
    expect(windowsStart, isNonNegative);
    expect(fallbackStart, greaterThan(windowsStart));
    final windowsAssembly = source.substring(windowsStart, fallbackStart);
    expect(
      'AtlasWindowsVaultSecureKeyStore'.allMatches(windowsAssembly),
      hasLength(1),
    );
    expect(
      'AtlasWindowsVaultLocalStoreIO'.allMatches(windowsAssembly),
      hasLength(1),
    );
    expect(
      'AtlasVaultPrivateStateRuntime('.allMatches(windowsAssembly),
      hasLength(1),
    );
    expect(windowsAssembly, contains('privateStatePersistence: runtime'));
    expect(
      windowsAssembly,
      contains('localCacheStoreFactory: _defaultCacheStore'),
    );
    expect(windowsAssembly, isNot(contains('activateExistingAtlasVault')));
    expect(windowsAssembly, isNot(contains('AtlasVaultPlaintextMigration')));
    expect(windowsAssembly, isNot(contains('AtlasVaultInteroperability')));
  });

  test(
    'Windows runtime activates explicitly and commits private state',
    () async {
      const vaultId = 'windows-runtime-test';
      const searchSentinel = 'WINDOWS_PRIVATE_SEARCH_SENTINEL';
      const trackerSentinel = 'windows-private-tracker-sentinel';
      final platform = FakeAtlasVaultWindowsPlatform();
      final key = deterministicWindowsVaultKey();
      platform
        ..seedVaultKey(key)
        ..seedLocalStore(testWindowsAtlasVaultLocalStore(vaultId))
        ..install();
      addTearDown(platform.uninstall);
      final identifiers = <String>[
        '10000000-0000-4000-8000-000000000201',
        '20000000-0000-4000-8000-000000000201',
        '10000000-0000-4000-8000-000000000202',
        '20000000-0000-4000-8000-000000000202',
      ];
      var nonceSeed = 91;
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: AtlasWindowsVaultSecureKeyStore(),
        localStoreIO: AtlasWindowsVaultLocalStoreIO(),
        now: () => DateTime.utc(2026, 8, 1, 12),
        uuidProvider: () => identifiers.removeAt(0),
        nonceProvider: () => Uint8List(12)..fillRange(0, 12, nonceSeed++),
      );
      final cacheDirectory = await Directory.systemTemp.createTemp(
        'atlas_windows_runtime_',
      );
      final cacheFile = File('${cacheDirectory.path}/atlas-local-cache.json');
      final cache = AtlasLocalCacheStore(
        file: cacheFile,
        now: () => DateTime.utc(2026, 8, 1, 12),
        privateStateProtectionActive: () => runtime.isActive,
      );
      final transport = _WindowsPrivateTransport();
      final controller = AtlasAppController(
        initialBaseURL: Uri.parse('http://127.0.0.1:8765'),
        clientFactory: (baseURL) =>
            AtlasAPIClient(baseURL: baseURL, transport: transport),
        localCacheStore: cache,
        privateStatePersistence: runtime,
        now: () => DateTime.utc(2026, 8, 1, 12),
      );
      addTearDown(() async {
        controller.dispose();
        await runtime.deactivate();
        key.fillRange(0, key.length, 0);
        if (await cacheDirectory.exists()) {
          await cacheDirectory.delete(recursive: true);
        }
      });

      expect(platform.calls, isEmpty);
      expect(
        await controller.activateExistingAtlasVault(vaultId),
        AtlasVaultActivationResult.activated,
      );
      controller.cacheSavedAt = DateTime.utc(2026, 8, 1, 12);
      platform.resetCalls();

      controller.updateQuery(searchSentinel);
      await controller.saveCurrentSearch();
      await controller.saveJob(_job(trackerSentinel));

      expect(controller.savedSearches.single.request.text, searchSentinel);
      expect(controller.trackerRecords.single.jobKey, trackerSentinel);
      expect(transport.privateReadCalls, 0);
      expect(transport.privateWriteCalls, 0);
      final encrypted = utf8.decode(platform.localStoreBytes!);
      expect(encrypted, isNot(contains(searchSentinel)));
      expect(encrypted, isNot(contains(trackerSentinel)));
      final cached = await cacheFile.readAsString();
      expect(cached, isNot(contains(searchSentinel)));
      expect(cached, isNot(contains(trackerSentinel)));
      final cacheJson = jsonDecode(cached) as Map<String, Object?>;
      expect(cacheJson['saved_searches'], isEmpty);
      expect(cacheJson['tracker_records'], isEmpty);

      await controller.deactivateAtlasVault();
      expect(runtime.isActive, isFalse);
      expect(controller.savedSearches, isEmpty);
      expect(controller.trackerRecords, isEmpty);
    },
  );

  test('Windows plaintext preflight makes no storage call', () async {
    final platform = FakeAtlasVaultWindowsPlatform()..install();
    addTearDown(platform.uninstall);
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: AtlasWindowsVaultSecureKeyStore(),
      localStoreIO: AtlasWindowsVaultLocalStoreIO(),
    );
    final controller = AtlasAppController(privateStatePersistence: runtime)
      ..savedSearches = <AtlasSavedSearch>[
        AtlasSavedSearch(
          name: 'Legacy private search',
          request: const AtlasSearchRequest(text: 'legacy'),
        ),
      ];
    addTearDown(() async {
      controller.dispose();
      await runtime.deactivate();
    });

    expect(
      await controller.activateExistingAtlasVault('windows-runtime-test'),
      AtlasVaultActivationResult.migrationRequired,
    );
    expect(platform.calls, isEmpty);
    expect(controller.savedSearches.single.name, 'Legacy private search');
  });

  test('Windows activation fails closed for missing device state', () async {
    final platform = FakeAtlasVaultWindowsPlatform()..install();
    addTearDown(platform.uninstall);
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: AtlasWindowsVaultSecureKeyStore(),
      localStoreIO: AtlasWindowsVaultLocalStoreIO(),
    );
    final controller = AtlasAppController(privateStatePersistence: runtime);
    addTearDown(() async {
      controller.dispose();
      await runtime.deactivate();
    });

    expect(
      await controller.activateExistingAtlasVault('windows-runtime-test'),
      AtlasVaultActivationResult.failed,
    );
    expect(runtime.isActive, isFalse);
    expect(controller.savedSearches, isEmpty);
    expect(controller.trackerRecords, isEmpty);
  });
}

JobSearchResult _job(String jobKey) {
  return JobSearchResult(
    jobKey: jobKey,
    title: 'Windows fixture',
    organization: 'Fixture organization',
    sourceID: 'fixture',
    dutyStation: 'Remote',
    gradeCode: 'P-3',
    contractLabel: 'Fixed term',
    workModality: 'Remote',
    closingDate: DateTime.utc(2026, 9, 1),
    needsReview: false,
    scoreReasons: const <String>[],
    matchSummary: 'Fixture',
    description: 'Fixture',
  );
}

final class _WindowsPrivateTransport implements AtlasTransport {
  int privateReadCalls = 0;
  int privateWriteCalls = 0;

  @override
  Future<Object?> send(AtlasRequest request) async {
    if (request.path == 'api/saved-searches' || request.path == 'api/tracker') {
      privateReadCalls += 1;
      return <Object?>[];
    }
    if (request.path.startsWith('api/tracker/jobs/')) {
      privateWriteCalls += 1;
      throw StateError('Compatibility private write was not expected.');
    }
    throw StateError('Network access was not expected.');
  }
}
