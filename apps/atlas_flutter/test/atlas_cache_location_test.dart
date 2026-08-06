import 'dart:io';

import 'package:atlas/atlas.dart';
import 'package:atlas/features/app_shell/atlas_cache_location.dart';
import 'package:atlas/src/cache_file_replacement.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('isAtlasPersistentDesktopCachePlatform', () {
    test('accepts desktop operating systems only', () {
      expect(
        isAtlasPersistentDesktopCachePlatform(operatingSystem: 'linux'),
        isTrue,
      );
      expect(
        isAtlasPersistentDesktopCachePlatform(operatingSystem: 'macos'),
        isTrue,
      );
      expect(
        isAtlasPersistentDesktopCachePlatform(operatingSystem: 'windows'),
        isTrue,
      );
      expect(
        isAtlasPersistentDesktopCachePlatform(operatingSystem: 'android'),
        isFalse,
      );
      expect(
        isAtlasPersistentDesktopCachePlatform(operatingSystem: 'ios'),
        isFalse,
      );
    });

    test('keeps only iOS on the pre-existing temporary cache path', () {
      expect(
        isAtlasLegacyTemporaryCachePlatform(operatingSystem: 'ios'),
        isTrue,
      );
      for (final operatingSystem in <String>[
        'android',
        'linux',
        'macos',
        'windows',
      ]) {
        expect(
          isAtlasLegacyTemporaryCachePlatform(operatingSystem: operatingSystem),
          isFalse,
        );
      }
    });

    test('resolves the pre-existing temporary cache path', () {
      final temporaryDirectory = Directory('/temporary-root');

      final cacheFile = resolveAtlasLegacyTemporaryCacheFile(
        systemTemporaryDirectory: temporaryDirectory,
      );

      expect(
        cacheFile.path,
        File(
          _joinTestPath(
            _joinTestPath(
              temporaryDirectory.path,
              atlasLegacyTemporaryDirectoryName,
            ),
            atlasLocalCacheFileName,
          ),
        ).path,
      );
    });
  });

  group('resolveAtlasPersistentCacheFile', () {
    late Directory sandbox;
    late Directory supportDirectory;
    late Directory legacySystemTemporaryDirectory;

    setUp(() async {
      sandbox = await Directory.systemTemp.createTemp(
        'atlas_cache_location_test_',
      );
      supportDirectory = Directory(
        _joinTestPath(sandbox.path, 'application-support'),
      );
      legacySystemTemporaryDirectory = Directory(
        _joinTestPath(sandbox.path, 'legacy-system-temp'),
      );
    });

    tearDown(() async {
      if (await sandbox.exists()) {
        await sandbox.delete(recursive: true);
      }
    });

    test('uses the application-support Atlas directory', () async {
      final cacheFile = await resolveAtlasPersistentCacheFile(
        applicationSupportDirectoryProvider: () async => supportDirectory,
        legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
      );

      expect(
        cacheFile.path,
        File(
          _joinTestPath(
            _joinTestPath(supportDirectory.path, 'Atlas'),
            atlasLocalCacheFileName,
          ),
        ).path,
      );
      expect(await cacheFile.exists(), isFalse);
    });

    test('imports a legacy temporary cache without deleting it', () async {
      final legacyFile = File(
        _joinTestPath(
          _joinTestPath(
            legacySystemTemporaryDirectory.path,
            atlasLegacyTemporaryDirectoryName,
          ),
          atlasLocalCacheFileName,
        ),
      );
      await legacyFile.parent.create(recursive: true);
      await legacyFile.writeAsString('{"historical_jobs":42}', flush: true);

      final cacheFile = await resolveAtlasPersistentCacheFile(
        applicationSupportDirectoryProvider: () async => supportDirectory,
        legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
      );

      expect(await cacheFile.readAsString(), '{"historical_jobs":42}');
      expect(await legacyFile.readAsString(), '{"historical_jobs":42}');
      expect(await File('${cacheFile.path}.migrating-$pid').exists(), isFalse);
    });

    test(
      'recovers a committed replacement before considering legacy import',
      () async {
        final targetFile = File(
          _joinTestPath(
            _joinTestPath(supportDirectory.path, 'Atlas'),
            atlasLocalCacheFileName,
          ),
        );
        final legacyFile = resolveAtlasLegacyTemporaryCacheFile(
          systemTemporaryDirectory: legacySystemTemporaryDirectory,
        );
        await targetFile.parent.create(recursive: true);
        await legacyFile.parent.create(recursive: true);
        await legacyFile.writeAsString('legacy', flush: true);
        await cacheReplacementTemporaryFile(
          targetFile,
        ).writeAsString('new durable cache', flush: true);
        await cacheReplacementPreviousFile(
          targetFile,
        ).writeAsString('old durable cache', flush: true);
        await cacheReplacementIntentFile(
          targetFile,
        ).writeAsString('atlas-cache-replace-v1:17\n', flush: true);

        final cacheFile = await resolveAtlasPersistentCacheFile(
          applicationSupportDirectoryProvider: () async => supportDirectory,
          legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
        );

        expect(await cacheFile.readAsString(), 'new durable cache');
        expect(await legacyFile.readAsString(), 'legacy');
        expect(await hasCacheReplacementArtifacts(targetFile), isFalse);
      },
    );

    test('rejects a marked stage with a mismatched length', () async {
      final targetFile = File(
        _joinTestPath(
          _joinTestPath(supportDirectory.path, 'Atlas'),
          atlasLocalCacheFileName,
        ),
      );
      final legacyFile = resolveAtlasLegacyTemporaryCacheFile(
        systemTemporaryDirectory: legacySystemTemporaryDirectory,
      );
      await targetFile.parent.create(recursive: true);
      await legacyFile.parent.create(recursive: true);
      await legacyFile.writeAsString('legacy', flush: true);
      await cacheReplacementTemporaryFile(
        targetFile,
      ).writeAsString('truncated', flush: true);
      await cacheReplacementPreviousFile(
        targetFile,
      ).writeAsString('old durable cache', flush: true);
      await cacheReplacementIntentFile(
        targetFile,
      ).writeAsString('atlas-cache-replace-v1:100\n', flush: true);

      final cacheFile = await resolveAtlasPersistentCacheFile(
        applicationSupportDirectoryProvider: () async => supportDirectory,
        legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
      );

      expect(await cacheFile.readAsString(), 'old durable cache');
      expect(await legacyFile.readAsString(), 'legacy');
      expect(await hasCacheReplacementArtifacts(targetFile), isFalse);
    });

    test('discards an uncommitted staged file before legacy import', () async {
      final targetFile = File(
        _joinTestPath(
          _joinTestPath(supportDirectory.path, 'Atlas'),
          atlasLocalCacheFileName,
        ),
      );
      final legacyFile = resolveAtlasLegacyTemporaryCacheFile(
        systemTemporaryDirectory: legacySystemTemporaryDirectory,
      );
      await targetFile.parent.create(recursive: true);
      await legacyFile.parent.create(recursive: true);
      await cacheReplacementTemporaryFile(
        targetFile,
      ).writeAsString('partial', flush: true);
      await legacyFile.writeAsString('legacy', flush: true);

      final cacheFile = await resolveAtlasPersistentCacheFile(
        applicationSupportDirectoryProvider: () async => supportDirectory,
        legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
      );

      expect(await cacheFile.readAsString(), 'legacy');
      expect(await hasCacheReplacementArtifacts(targetFile), isFalse);
    });

    test(
      'explicit clear keeps rollback data but permanently retires its import',
      () async {
        final legacyFile = File(
          _joinTestPath(
            _joinTestPath(
              legacySystemTemporaryDirectory.path,
              atlasLegacyTemporaryDirectoryName,
            ),
            atlasLocalCacheFileName,
          ),
        );
        await legacyFile.parent.create(recursive: true);
        await legacyFile.writeAsString('legacy', flush: true);
        final location = await resolveAtlasPersistentCacheLocation(
          applicationSupportDirectoryProvider: () async => supportDirectory,
          legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
        );
        final store = AtlasLocalCacheStore(
          file: location.cacheFile,
          prepareForClear: location.prepareForClearUnderMutationLock,
          mutationCoordinator: location.coordinateMutation,
        );

        await store.clear();
        final nextLocation = await resolveAtlasPersistentCacheLocation(
          applicationSupportDirectoryProvider: () async => supportDirectory,
          legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
        );

        expect(await legacyFile.readAsString(), 'legacy');
        expect(await location.legacyImportRetiredFile.exists(), isTrue);
        expect(await nextLocation.cacheFile.exists(), isFalse);
      },
    );

    test('never overwrites an existing persistent cache', () async {
      final persistentFile = File(
        _joinTestPath(
          _joinTestPath(supportDirectory.path, 'Atlas'),
          atlasLocalCacheFileName,
        ),
      );
      await persistentFile.parent.create(recursive: true);
      await persistentFile.writeAsString('persistent', flush: true);
      final legacyFile = File(
        _joinTestPath(
          _joinTestPath(
            legacySystemTemporaryDirectory.path,
            atlasLegacyTemporaryDirectoryName,
          ),
          atlasLocalCacheFileName,
        ),
      );
      await legacyFile.parent.create(recursive: true);
      await legacyFile.writeAsString('legacy', flush: true);

      final cacheFile = await resolveAtlasPersistentCacheFile(
        applicationSupportDirectoryProvider: () async => supportDirectory,
        legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
      );

      expect(cacheFile.path, persistentFile.path);
      expect(await cacheFile.readAsString(), 'persistent');
      expect(await legacyFile.readAsString(), 'legacy');
    });

    test('removes stale migration files left by a crashed process', () async {
      final persistentFile = await resolveAtlasPersistentCacheFile(
        applicationSupportDirectoryProvider: () async => supportDirectory,
        legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
      );
      await persistentFile.parent.create(recursive: true);
      await persistentFile.writeAsString('persistent', flush: true);
      final staleMigrationFile = File(
        '${persistentFile.path}.migrating-999999',
      );
      await staleMigrationFile.writeAsString('partial', flush: true);

      final resolvedFile = await resolveAtlasPersistentCacheFile(
        applicationSupportDirectoryProvider: () async => supportDirectory,
        legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
      );

      expect(await resolvedFile.readAsString(), 'persistent');
      expect(await staleMigrationFile.exists(), isFalse);
    });

    test('serializes concurrent legacy imports', () async {
      final legacyFile = File(
        _joinTestPath(
          _joinTestPath(
            legacySystemTemporaryDirectory.path,
            atlasLegacyTemporaryDirectoryName,
          ),
          atlasLocalCacheFileName,
        ),
      );
      await legacyFile.parent.create(recursive: true);
      await legacyFile.writeAsString('legacy', flush: true);

      final cacheFiles = await Future.wait(
        List.generate(
          2,
          (_) => resolveAtlasPersistentCacheFile(
            applicationSupportDirectoryProvider: () async => supportDirectory,
            legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
          ),
        ),
      );

      expect(cacheFiles[0].path, cacheFiles[1].path);
      expect(await cacheFiles[0].readAsString(), 'legacy');
      expect(await legacyFile.readAsString(), 'legacy');
    });

    test('propagates migration failures so import remains retryable', () async {
      final legacyFile = File(
        _joinTestPath(
          _joinTestPath(
            legacySystemTemporaryDirectory.path,
            atlasLegacyTemporaryDirectoryName,
          ),
          atlasLocalCacheFileName,
        ),
      );
      await legacyFile.parent.create(recursive: true);
      await legacyFile.writeAsString('legacy', flush: true);
      final blockedTargetDirectory = File(
        _joinTestPath(supportDirectory.path, 'Atlas'),
      );
      await blockedTargetDirectory.parent.create(recursive: true);
      await blockedTargetDirectory.writeAsString('not a directory');

      await expectLater(
        resolveAtlasPersistentCacheFile(
          applicationSupportDirectoryProvider: () async => supportDirectory,
          legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
        ),
        throwsA(isA<FileSystemException>()),
      );
      expect(await legacyFile.readAsString(), 'legacy');
    });

    test('does not fall back when application support resolution fails', () {
      expect(
        () => resolveAtlasPersistentCacheFile(
          applicationSupportDirectoryProvider: () async {
            throw StateError('application support unavailable');
          },
          legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
        ),
        throwsStateError,
      );
    });
  });

  group('AtlasWindowsDesktopCacheMigrationSource', () {
    late Directory sandbox;
    late Directory supportDirectory;
    late Directory legacySystemTemporaryDirectory;
    late AtlasPersistentCacheLocation location;

    setUp(() async {
      sandbox = await Directory.systemTemp.createTemp(
        'atlas_windows_cache_migration_test_',
      );
      supportDirectory = Directory(
        _joinTestPath(sandbox.path, 'application-support'),
      );
      legacySystemTemporaryDirectory = Directory(
        _joinTestPath(sandbox.path, 'legacy-system-temp'),
      );
      location = await resolveAtlasPersistentCacheLocation(
        applicationSupportDirectoryProvider: () async => supportDirectory,
        legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
      );
    });

    tearDown(() async {
      if (await sandbox.exists()) {
        await sandbox.delete(recursive: true);
      }
    });

    test(
      'inventories durable and retained legacy cache as one read-only authority',
      () async {
        final snapshot = _migrationCacheSnapshot();
        await AtlasLocalCacheStore(file: location.cacheFile).write(snapshot);
        await AtlasLocalCacheStore(file: location.legacyFile).write(snapshot);
        final durableBefore = await location.cacheFile.readAsBytes();
        final legacyBefore = await location.legacyFile.readAsBytes();
        final source = AtlasWindowsDesktopCacheMigrationSource(location);

        final state = await source.readPrivateStateForMigration();

        expect(state.savedSearches, hasLength(1));
        expect(state.trackerRecords, hasLength(1));
        expect(state.durablePrivateSha256, isNotNull);
        expect(state.legacyPrivateSha256, isNotNull);
        expect(state.privateSha256, isNotNull);
        expect(state.retainedLegacyCachePresent, isTrue);
        expect(state.cacheCleanupPending, isFalse);
        expect(state.cacheCleanupComplete, isFalse);
        expect(await location.cacheFile.readAsBytes(), durableBefore);
        expect(await location.legacyFile.readAsBytes(), legacyBefore);
        expect(await location.privateMigrationIntentFile.exists(), isFalse);
      },
    );

    test(
      'fails closed when durable and retained legacy values conflict',
      () async {
        await AtlasLocalCacheStore(
          file: location.cacheFile,
        ).write(_migrationCacheSnapshot(requestText: 'DURABLE_PRIVATE_QUERY'));
        await AtlasLocalCacheStore(
          file: location.legacyFile,
        ).write(_migrationCacheSnapshot(requestText: 'LEGACY_PRIVATE_QUERY'));
        final source = AtlasWindowsDesktopCacheMigrationSource(location);

        await expectLater(
          source.readPrivateStateForMigration(),
          throwsA(isA<AtlasLocalCacheMigrationException>()),
        );

        expect(await location.privateMigrationIntentFile.exists(), isFalse);
        expect(await location.legacyImportRetiredFile.exists(), isFalse);
      },
    );

    test('fails closed on malformed retained legacy cache', () async {
      await AtlasLocalCacheStore(
        file: location.cacheFile,
      ).write(_migrationCacheSnapshot());
      await location.legacyFile.parent.create(recursive: true);
      await location.legacyFile.writeAsString(
        '{"saved_searches":[',
        flush: true,
      );
      final source = AtlasWindowsDesktopCacheMigrationSource(location);

      await expectLater(
        source.readPrivateStateForMigration(),
        throwsA(isA<AtlasLocalCacheMigrationException>()),
      );

      expect(await location.privateMigrationIntentFile.exists(), isFalse);
    });

    test(
      'cleanup preserves durable public data and retires retained legacy data',
      () async {
        final snapshot = _migrationCacheSnapshot();
        await AtlasLocalCacheStore(file: location.cacheFile).write(snapshot);
        await AtlasLocalCacheStore(file: location.legacyFile).write(snapshot);
        final source = AtlasWindowsDesktopCacheMigrationSource(location);
        final inventory = await source.readPrivateStateForMigration();

        await source.removePrivateStateForMigration(
          expectedPrivateSha256: inventory.privateSha256!,
        );

        final durable = await AtlasLocalCacheStore(
          file: location.cacheFile,
          now: () => DateTime.utc(2025, 1, 3),
        ).read();
        final completed = await source.readPrivateStateForMigration();
        expect(durable, isNotNull);
        expect(durable!.baseURL, Uri.parse('http://atlas.test:8765'));
        expect(durable.searchResponse.total, 0);
        expect(durable.savedSearches, isEmpty);
        expect(durable.trackerRecords, isEmpty);
        expect(await location.legacyImportRetiredFile.exists(), isTrue);
        expect(await location.legacyFile.exists(), isFalse);
        expect(await location.privateMigrationIntentFile.exists(), isFalse);
        expect(completed.savedSearches, isEmpty);
        expect(completed.trackerRecords, isEmpty);
        expect(completed.privateSha256, isNull);
        expect(completed.durablePrivateSha256, isNull);
        expect(completed.legacyPrivateSha256, isNull);
        expect(completed.retainedLegacyCachePresent, isFalse);
        expect(completed.cacheCleanupPending, isFalse);
        expect(completed.cacheCleanupComplete, isTrue);
      },
    );

    test('digest mismatch starts no cache cleanup transaction', () async {
      final snapshot = _migrationCacheSnapshot();
      await AtlasLocalCacheStore(file: location.cacheFile).write(snapshot);
      await AtlasLocalCacheStore(file: location.legacyFile).write(snapshot);
      final durableBefore = await location.cacheFile.readAsBytes();
      final legacyBefore = await location.legacyFile.readAsBytes();
      final source = AtlasWindowsDesktopCacheMigrationSource(location);

      await expectLater(
        source.removePrivateStateForMigration(expectedPrivateSha256: '0' * 64),
        throwsA(isA<AtlasLocalCacheMigrationException>()),
      );

      expect(await location.cacheFile.readAsBytes(), durableBefore);
      expect(await location.legacyFile.readAsBytes(), legacyBefore);
      expect(await location.privateMigrationIntentFile.exists(), isFalse);
      expect(await location.legacyImportRetiredFile.exists(), isFalse);
    });
  });
}

AtlasLocalCacheSnapshot _migrationCacheSnapshot({
  String requestText = 'WINDOWS_PRIVATE_QUERY',
}) {
  return AtlasLocalCacheSnapshot(
    schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
    baseURL: Uri.parse('http://atlas.test:8765'),
    savedAt: DateTime.utc(2025, 1, 2, 3, 4, 5),
    searchRequest: const AtlasSearchRequest(),
    searchResponse: AtlasSearchResponse(
      total: 0,
      limit: 50,
      offset: 0,
      results: const <JobSearchResult>[],
      facets: const <String, Map<String, int>>{},
      facetLabels: const <String, Map<String, String>>{},
      unclassifiedCount: 0,
    ),
    savedSearches: <AtlasSavedSearch>[
      AtlasSavedSearch(
        name: 'Windows migration search',
        description: 'private description',
        request: AtlasSearchRequest(text: requestText),
        createdAt: '2026-08-01T00:00:00Z',
        updatedAt: '2026-08-02T00:00:00Z',
      ),
    ],
    trackerRecords: <AtlasApplicationRecord>[
      AtlasApplicationRecord(
        id: 'windows-tracker-record',
        jobKey: 'windows:private-job',
        status: 'saved',
        notes: 'WINDOWS_PRIVATE_NOTE',
        updatedAt: '2026-08-02T00:00:00Z',
      ),
    ],
  );
}

String _joinTestPath(String parent, String child) {
  if (parent.endsWith(Platform.pathSeparator)) {
    return '$parent$child';
  }
  return '$parent${Platform.pathSeparator}$child';
}
