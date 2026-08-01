import 'dart:async';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

const atlasLocalCacheFileName = 'atlas-local-cache-v1.json';
const atlasApplicationSupportDirectoryName = 'Atlas';
const atlasLegacyTemporaryDirectoryName = 'atlas_flutter';
const atlasLegacyImportRetiredFileName =
    '$atlasLocalCacheFileName.legacy-import-retired';

typedef AtlasApplicationSupportDirectoryProvider = Future<Directory> Function();

Future<void> _migrationQueue = Future.value();

bool isAtlasPersistentDesktopCachePlatform({String? operatingSystem}) {
  final value = operatingSystem ?? Platform.operatingSystem;
  return value == 'linux' || value == 'macos' || value == 'windows';
}

bool isAtlasLegacyTemporaryCachePlatform({String? operatingSystem}) {
  final value = operatingSystem ?? Platform.operatingSystem;
  return value == 'ios';
}

File resolveAtlasLegacyTemporaryCacheFile({
  Directory? systemTemporaryDirectory,
}) {
  final legacyRoot = systemTemporaryDirectory ?? Directory.systemTemp;
  return File(
    _joinPath(
      _joinPath(legacyRoot.path, atlasLegacyTemporaryDirectoryName),
      atlasLocalCacheFileName,
    ),
  );
}

final class AtlasPersistentCacheLocation {
  AtlasPersistentCacheLocation({
    required this.cacheFile,
    required this.legacyFile,
    required this.legacyImportRetiredFile,
  });

  final File cacheFile;
  final File legacyFile;
  final File legacyImportRetiredFile;

  Future<void> coordinateMutation(Future<void> Function() operation) {
    return _withMigrationLocks(targetFile: cacheFile, operation: operation);
  }

  Future<void> prepareForClearUnderMutationLock() async {
    if (await legacyImportRetiredFile.exists()) {
      return;
    }
    await legacyImportRetiredFile.writeAsString(
      'Legacy temporary cache import retired by explicit local-cache clear.\n',
      flush: true,
    );
  }
}

/// Resolves Atlas's durable local-cache file and imports the legacy temporary
/// cache when no durable cache exists yet.
///
/// The legacy file is deliberately retained after a successful import. The OS
/// may remove it at any time, but Atlas does not need to destroy the only
/// rollback copy while adopting the persistent location.
Future<File> resolveAtlasPersistentCacheFile({
  AtlasApplicationSupportDirectoryProvider? applicationSupportDirectoryProvider,
  Directory? legacySystemTemporaryDirectory,
}) async {
  final location = await resolveAtlasPersistentCacheLocation(
    applicationSupportDirectoryProvider: applicationSupportDirectoryProvider,
    legacySystemTemporaryDirectory: legacySystemTemporaryDirectory,
  );
  return location.cacheFile;
}

Future<AtlasPersistentCacheLocation> resolveAtlasPersistentCacheLocation({
  AtlasApplicationSupportDirectoryProvider? applicationSupportDirectoryProvider,
  Directory? legacySystemTemporaryDirectory,
}) async {
  final supportDirectory =
      await (applicationSupportDirectoryProvider ??
          getApplicationSupportDirectory)();
  if (supportDirectory.path.trim().isEmpty) {
    throw const FileSystemException(
      'Application support directory has an empty path.',
    );
  }

  final targetDirectory = Directory(
    _joinPath(supportDirectory.path, atlasApplicationSupportDirectoryName),
  );
  final targetFile = File(
    _joinPath(targetDirectory.path, atlasLocalCacheFileName),
  );
  final legacyImportRetiredFile = File(
    _joinPath(targetDirectory.path, atlasLegacyImportRetiredFileName),
  );
  final legacyFile = resolveAtlasLegacyTemporaryCacheFile(
    systemTemporaryDirectory: legacySystemTemporaryDirectory,
  );

  await _copyLegacyCacheIfNeeded(
    legacyFile: legacyFile,
    targetFile: targetFile,
    legacyImportRetiredFile: legacyImportRetiredFile,
  );
  return AtlasPersistentCacheLocation(
    cacheFile: targetFile,
    legacyFile: legacyFile,
    legacyImportRetiredFile: legacyImportRetiredFile,
  );
}

Future<void> _copyLegacyCacheIfNeeded({
  required File legacyFile,
  required File targetFile,
  required File legacyImportRetiredFile,
}) async {
  if (legacyFile.path == targetFile.path) {
    return;
  }
  final hasStaleMigrationFiles = await _hasStaleMigrationFiles(targetFile);
  if (!hasStaleMigrationFiles &&
      (await targetFile.exists() ||
          await legacyImportRetiredFile.exists() ||
          !await legacyFile.exists())) {
    return;
  }

  await _withMigrationLocks(
    targetFile: targetFile,
    operation: () async {
      await _deleteStaleMigrationFilesUnderLock(targetFile);
      if (await targetFile.exists() ||
          await legacyImportRetiredFile.exists() ||
          !await legacyFile.exists()) {
        return;
      }
      await _copyLegacyCacheUnderLock(
        legacyFile: legacyFile,
        targetFile: targetFile,
      );
    },
  );
}

Future<bool> _hasStaleMigrationFiles(File targetFile) async {
  if (!await targetFile.parent.exists()) {
    return false;
  }
  final prefix = '${targetFile.path}.migrating-';
  await for (final entity in targetFile.parent.list(followLinks: false)) {
    if (entity is File && entity.path.startsWith(prefix)) {
      return true;
    }
  }
  return false;
}

Future<void> _deleteStaleMigrationFilesUnderLock(File targetFile) async {
  final prefix = '${targetFile.path}.migrating-';
  await for (final entity in targetFile.parent.list(followLinks: false)) {
    if (entity is File && entity.path.startsWith(prefix)) {
      await entity.delete();
    }
  }
}

Future<T> _withMigrationLocks<T>({
  required File targetFile,
  required Future<T> Function() operation,
}) {
  return _withInProcessMigrationLock(() async {
    await targetFile.parent.create(recursive: true);
    final migrationLock = await File(
      '${targetFile.path}.migration.lock',
    ).open(mode: FileMode.append);
    var lockAcquired = false;
    try {
      await migrationLock.lock(FileLock.exclusive);
      lockAcquired = true;
      return await operation();
    } finally {
      try {
        if (lockAcquired) {
          await migrationLock.unlock();
        }
      } finally {
        await migrationLock.close();
      }
    }
  });
}

Future<T> _withInProcessMigrationLock<T>(Future<T> Function() operation) async {
  final previous = _migrationQueue;
  final release = Completer<void>();
  _migrationQueue = release.future;
  await previous;
  try {
    return await operation();
  } finally {
    release.complete();
  }
}

Future<void> _copyLegacyCacheUnderLock({
  required File legacyFile,
  required File targetFile,
}) async {
  final stagingFile = File('${targetFile.path}.migrating-$pid');
  try {
    if (await stagingFile.exists()) {
      await stagingFile.delete();
    }

    final expectedLength = await legacyFile.length();
    await legacyFile.copy(stagingFile.path);
    if (await stagingFile.length() != expectedLength) {
      throw const FileSystemException('Legacy cache copy was incomplete.');
    }

    if (await targetFile.exists()) {
      await stagingFile.delete();
      return;
    }
    await stagingFile.rename(targetFile.path);
  } on FileSystemException {
    try {
      if (await stagingFile.exists()) {
        await stagingFile.delete();
      }
    } on FileSystemException {
      // The durable target remains authoritative even if staging cleanup fails.
    }
    rethrow;
  }
}

String _joinPath(String parent, String child) {
  if (parent.endsWith(Platform.pathSeparator)) {
    return '$parent$child';
  }
  return '$parent${Platform.pathSeparator}$child';
}
