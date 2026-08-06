import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:path_provider/path_provider.dart';

import '../../src/cache_file_replacement.dart';
import '../../atlas.dart';
import '../../atlas_vault.dart' show atlasVaultSha256Hex;
import '../../src/atlas_vault/canonical_json.dart';
import '../../src/atlas_vault/plaintext_migration.dart';

const atlasLocalCacheFileName = 'atlas-local-cache-v1.json';
const atlasApplicationSupportDirectoryName = 'Atlas';
const atlasLegacyTemporaryDirectoryName = 'atlas_flutter';
const atlasLegacyImportRetiredFileName =
    '$atlasLocalCacheFileName.legacy-import-retired';
const atlasPrivateMigrationIntentFileName =
    '$atlasLocalCacheFileName.private-migration.intent';

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

  File get privateMigrationIntentFile => File(
    _joinPath(cacheFile.parent.path, atlasPrivateMigrationIntentFileName),
  );

  Future<T> coordinateMutation<T>(Future<T> Function() operation) {
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

final class AtlasWindowsDesktopCacheMigrationSource
    implements AtlasLocalCacheMigrationSource {
  const AtlasWindowsDesktopCacheMigrationSource(this.location);

  final AtlasPersistentCacheLocation location;

  @override
  Future<AtlasLocalCacheMigrationPrivateState> readPrivateStateForMigration() {
    return location.coordinateMutation(() async {
      try {
        await recoverInterruptedCacheReplacement(location.cacheFile);
        final durable = await AtlasLocalCacheStore(
          file: location.cacheFile,
        ).readPrivateStateForMigration();
        final legacy = await AtlasLocalCacheStore(
          file: location.legacyFile,
        ).readPrivateStateForMigration();
        final intentPresent = await _validateCleanupIntentIfPresent(
          location.privateMigrationIntentFile,
        );
        final merged = _mergeCachePrivateState(durable, legacy);
        final combinedDigest = await _combinedCachePrivateDigest(
          durable.privateSha256,
          legacy.privateSha256,
        );
        final cleanupComplete =
            await location.legacyImportRetiredFile.exists() &&
            !legacy.cachePresent &&
            !durable.containsPrivateState &&
            durable.privateSha256 == null &&
            !intentPresent;
        return AtlasLocalCacheMigrationPrivateState(
          savedSearches: merged.savedSearches,
          trackerRecords: merged.trackerRecords,
          privateSha256: combinedDigest,
          durablePrivateSha256: durable.privateSha256,
          legacyPrivateSha256: legacy.privateSha256,
          retainedLegacyCachePresent: legacy.cachePresent,
          cacheCleanupPending: intentPresent,
          cacheCleanupComplete: cleanupComplete,
          authorityBaseURL: merged.authorityBaseURL,
          cachePresent: durable.cachePresent || legacy.cachePresent,
        );
      } on AtlasLocalCacheMigrationException {
        rethrow;
      } catch (_) {
        throw const AtlasLocalCacheMigrationException();
      }
    });
  }

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) {
    return Future<void>.error(const AtlasLocalCacheMigrationException());
  }

  @override
  String toString() => 'AtlasWindowsDesktopCacheMigrationSource(<redacted>)';
}

AtlasLocalCacheMigrationPrivateState _mergeCachePrivateState(
  AtlasLocalCacheMigrationPrivateState durable,
  AtlasLocalCacheMigrationPrivateState legacy,
) {
  final savedSearches = <String, AtlasSavedSearch>{};
  final trackerRecords = <String, AtlasApplicationRecord>{};
  for (final state in <AtlasLocalCacheMigrationPrivateState>[durable, legacy]) {
    for (final value in state.savedSearches) {
      final current = savedSearches[value.name];
      if (current != null &&
          !_cacheJsonEqual(current.toJson(), value.toJson())) {
        throw const AtlasLocalCacheMigrationException();
      }
      savedSearches[value.name] = value;
    }
    for (final value in state.trackerRecords) {
      final current = trackerRecords[value.jobKey];
      if (current != null &&
          !_cacheJsonEqual(current.toJson(), value.toJson())) {
        throw const AtlasLocalCacheMigrationException();
      }
      trackerRecords[value.jobKey] = value;
    }
  }
  final durableHasPrivate = durable.containsPrivateState;
  final legacyHasPrivate = legacy.containsPrivateState;
  final durableAuthority = durableHasPrivate ? durable.authorityBaseURL : null;
  final legacyAuthority = legacyHasPrivate ? legacy.authorityBaseURL : null;
  if ((durableHasPrivate && durableAuthority == null) ||
      (legacyHasPrivate && legacyAuthority == null) ||
      (durableAuthority != null &&
          legacyAuthority != null &&
          durableAuthority.toString() != legacyAuthority.toString())) {
    throw const AtlasLocalCacheMigrationException();
  }
  final sortedSearches = savedSearches.values.toList(growable: false)
    ..sort((left, right) => left.name.compareTo(right.name));
  final sortedTrackers = trackerRecords.values.toList(growable: false)
    ..sort((left, right) => left.jobKey.compareTo(right.jobKey));
  return AtlasLocalCacheMigrationPrivateState(
    savedSearches: sortedSearches,
    trackerRecords: sortedTrackers,
    privateSha256: null,
    authorityBaseURL: durableAuthority ?? legacyAuthority,
  );
}

Future<String?> _combinedCachePrivateDigest(
  String? durableDigest,
  String? legacyDigest,
) async {
  if (durableDigest == null && legacyDigest == null) {
    return null;
  }
  final bytes = encodeCanonicalJson(<String, Object?>{
    'durable_private_sha256': durableDigest,
    'legacy_private_sha256': legacyDigest,
  });
  try {
    return await atlasVaultSha256Hex(bytes);
  } finally {
    bytes.fillRange(0, bytes.length, 0);
  }
}

bool _cacheJsonEqual(Object? left, Object? right) {
  Uint8List? leftBytes;
  Uint8List? rightBytes;
  try {
    leftBytes = encodeCanonicalJson(left);
    rightBytes = encodeCanonicalJson(right);
    if (leftBytes.length != rightBytes.length) {
      return false;
    }
    var difference = 0;
    for (var index = 0; index < leftBytes.length; index += 1) {
      difference |= leftBytes[index] ^ rightBytes[index];
    }
    return difference == 0;
  } finally {
    leftBytes?.fillRange(0, leftBytes.length, 0);
    rightBytes?.fillRange(0, rightBytes.length, 0);
  }
}

Future<bool> _validateCleanupIntentIfPresent(File file) async {
  if (!await file.exists()) {
    return false;
  }
  try {
    final decoded = jsonDecode(await file.readAsString());
    if (decoded is! Map<String, Object?>) {
      throw const AtlasLocalCacheMigrationException();
    }
    const expectedKeys = <String>{
      'format',
      'version',
      'expected_combined_private_sha256',
      'durable_private_sha256',
      'legacy_private_sha256',
      'durable_cleared',
      'legacy_retired',
      'legacy_deleted',
    };
    if (decoded.keys.length != expectedKeys.length ||
        !decoded.keys.every(expectedKeys.contains) ||
        decoded['format'] != 'atlasvault-windows-cache-private-migration' ||
        decoded['version'] != 1 ||
        !_nullableSha256(decoded['expected_combined_private_sha256']) ||
        !_nullableSha256(decoded['durable_private_sha256']) ||
        !_nullableSha256(decoded['legacy_private_sha256']) ||
        decoded['durable_cleared'] is! bool ||
        decoded['legacy_retired'] is! bool ||
        decoded['legacy_deleted'] is! bool) {
      throw const AtlasLocalCacheMigrationException();
    }
    final canonical = encodeCanonicalJson(decoded);
    final actual = await file.readAsBytes();
    try {
      if (!_cacheBytesEqual(canonical, actual)) {
        throw const AtlasLocalCacheMigrationException();
      }
    } finally {
      canonical.fillRange(0, canonical.length, 0);
      actual.fillRange(0, actual.length, 0);
    }
    return true;
  } on AtlasLocalCacheMigrationException {
    rethrow;
  } catch (_) {
    throw const AtlasLocalCacheMigrationException();
  }
}

bool _nullableSha256(Object? value) {
  return value == null ||
      (value is String && RegExp(r'^[0-9a-f]{64}$').hasMatch(value));
}

bool _cacheBytesEqual(Uint8List left, Uint8List right) {
  var difference = left.length ^ right.length;
  final maximum = left.length > right.length ? left.length : right.length;
  for (var index = 0; index < maximum; index += 1) {
    difference |=
        (index < left.length ? left[index] : 0) ^
        (index < right.length ? right[index] : 0);
  }
  return difference == 0;
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
  final hasReplacementArtifacts = await hasCacheReplacementArtifacts(
    targetFile,
  );
  if (!hasStaleMigrationFiles &&
      !hasReplacementArtifacts &&
      (await targetFile.exists() ||
          await legacyImportRetiredFile.exists() ||
          !await legacyFile.exists())) {
    return;
  }

  await _withMigrationLocks(
    targetFile: targetFile,
    operation: () async {
      await recoverInterruptedCacheReplacement(targetFile);
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
