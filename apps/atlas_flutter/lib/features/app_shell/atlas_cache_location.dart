import 'dart:async';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

const atlasLocalCacheFileName = 'atlas-local-cache-v1.json';
const atlasApplicationSupportDirectoryName = 'Atlas';
const atlasLegacyTemporaryDirectoryName = 'atlas_flutter';

typedef AtlasApplicationSupportDirectoryProvider = Future<Directory> Function();

Future<void> _migrationQueue = Future.value();

bool isAtlasPersistentDesktopCachePlatform({String? operatingSystem}) {
  final value = operatingSystem ?? Platform.operatingSystem;
  return value == 'linux' || value == 'macos' || value == 'windows';
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
  final legacyRoot = legacySystemTemporaryDirectory ?? Directory.systemTemp;
  final legacyFile = File(
    _joinPath(
      _joinPath(legacyRoot.path, atlasLegacyTemporaryDirectoryName),
      atlasLocalCacheFileName,
    ),
  );

  await _copyLegacyCacheIfNeeded(
    legacyFile: legacyFile,
    targetFile: targetFile,
  );
  return targetFile;
}

Future<void> _copyLegacyCacheIfNeeded({
  required File legacyFile,
  required File targetFile,
}) async {
  if (legacyFile.path == targetFile.path || await targetFile.exists()) {
    return;
  }
  if (!await legacyFile.exists()) {
    return;
  }

  await _withInProcessMigrationLock(() async {
    if (await targetFile.exists() || !await legacyFile.exists()) {
      return;
    }
    await targetFile.parent.create(recursive: true);
    final migrationLock = await File(
      '${targetFile.path}.migration.lock',
    ).open(mode: FileMode.append);
    try {
      await migrationLock.lock(FileLock.exclusive);
      if (await targetFile.exists() || !await legacyFile.exists()) {
        return;
      }
      await _copyLegacyCacheUnderLock(
        legacyFile: legacyFile,
        targetFile: targetFile,
      );
    } finally {
      try {
        await migrationLock.unlock();
      } finally {
        await migrationLock.close();
      }
    }
  });
}

Future<void> _withInProcessMigrationLock(
  Future<void> Function() operation,
) async {
  final previous = _migrationQueue;
  final release = Completer<void>();
  _migrationQueue = release.future;
  await previous;
  try {
    await operation();
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
