import 'dart:io';

import 'package:atlas/features/app_shell/atlas_cache_location.dart';
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
}

String _joinTestPath(String parent, String child) {
  if (parent.endsWith(Platform.pathSeparator)) {
    return '$parent$child';
  }
  return '$parent${Platform.pathSeparator}$child';
}
