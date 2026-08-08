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
typedef AtlasPersistentCacheLocationProvider =
    Future<AtlasPersistentCacheLocation> Function();

Future<void> _migrationQueue = Future.value();
final Object _migrationLeaseZoneKey = Object();

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

final class AtlasWindowsPlaintextAuthorityAdmission
    implements AtlasVaultPlaintextAuthorityAdmission {
  AtlasWindowsPlaintextAuthorityAdmission({
    required AtlasPersistentCacheLocationProvider locationProvider,
    required AtlasVaultProtectedMigrationJournalStore journalStore,
    required AtlasVaultSelectedVaultStore selectedVaultStore,
  }) : // Keep public constructor parameter names stable.
       // ignore: prefer_initializing_formals
       _locationProvider = locationProvider,
       // ignore: prefer_initializing_formals
       _journalStore = journalStore,
       // ignore: prefer_initializing_formals
       _selectedVaultStore = selectedVaultStore;

  final AtlasPersistentCacheLocationProvider _locationProvider;
  final AtlasVaultProtectedMigrationJournalStore _journalStore;
  final AtlasVaultSelectedVaultStore _selectedVaultStore;
  Future<AtlasPersistentCacheLocation>? _location;

  Future<AtlasPersistentCacheLocation> _resolveLocation() {
    return _location ??= _locationProvider();
  }

  @override
  Future<T> runLegacyPrivateOperation<T>(Future<T> Function() operation) async {
    final AtlasPersistentCacheLocation location;
    try {
      location = await _resolveLocation();
    } catch (_) {
      throw const AtlasVaultPlaintextAuthorityAdmissionException();
    }
    try {
      return await location.coordinateMutation(() async {
        Uint8List? journalBytes;
        String? selectedVault;
        try {
          journalBytes = await _journalStore.read();
          selectedVault = await _selectedVaultStore.read();
        } catch (_) {
          throw const AtlasVaultPlaintextAuthorityAdmissionException();
        } finally {
          journalBytes?.fillRange(0, journalBytes.length, 0);
        }
        if (journalBytes != null || selectedVault != null) {
          throw const AtlasVaultPlaintextAuthorityAdmissionException();
        }
        return operation();
      });
    } on AtlasVaultPlaintextAuthorityAdmissionException {
      rethrow;
    } on FileSystemException {
      throw const AtlasVaultPlaintextAuthorityAdmissionException();
    }
  }

  @override
  Future<T> runMigrationTransaction<T>(Future<T> Function() operation) async {
    final AtlasPersistentCacheLocation location;
    try {
      location = await _resolveLocation();
    } catch (_) {
      throw const AtlasVaultPlaintextAuthorityAdmissionException();
    }
    try {
      return await location.coordinateMutation(operation);
    } on AtlasVaultPlaintextAuthorityAdmissionException {
      rethrow;
    } on FileSystemException {
      throw const AtlasVaultPlaintextAuthorityAdmissionException();
    }
  }

  @override
  String toString() => 'AtlasWindowsPlaintextAuthorityAdmission(<redacted>)';
}

final class AtlasWindowsDesktopCacheMigrationSource
    implements
        AtlasLocalCacheMigrationSource,
        AtlasLocalCacheMigrationCleanupSource {
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
        final intent = await _readCleanupIntentIfPresent(
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
            intent == null;
        return AtlasLocalCacheMigrationPrivateState(
          savedSearches: merged.savedSearches,
          trackerRecords: merged.trackerRecords,
          privateSha256: combinedDigest,
          durablePrivateSha256: durable.privateSha256,
          legacyPrivateSha256: legacy.privateSha256,
          retainedLegacyCachePresent: legacy.cachePresent,
          cacheCleanupPending: intent != null,
          cacheCleanupComplete: cleanupComplete,
          requiresPhysicalCleanup: !cleanupComplete,
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
    if (!_isSha256(expectedPrivateSha256)) {
      return Future<void>.error(const AtlasLocalCacheMigrationException());
    }
    return completePrivateStateCleanupForMigration(
      expectedPrivateSha256: expectedPrivateSha256,
    );
  }

  @override
  Future<void> completePrivateStateCleanupForMigration({
    required String? expectedPrivateSha256,
  }) {
    return location.coordinateMutation(() async {
      try {
        if (!_nullableSha256(expectedPrivateSha256)) {
          throw const AtlasLocalCacheMigrationException();
        }
        await recoverInterruptedCacheReplacement(location.cacheFile);
        var intent = await _readCleanupIntentIfPresent(
          location.privateMigrationIntentFile,
        );
        if (intent == null) {
          final durable = await _readMigrationCache(location.cacheFile);
          final legacy = await _readMigrationCache(location.legacyFile);
          _mergeCachePrivateState(durable, legacy);
          final combined = await _combinedCachePrivateDigest(
            durable.privateSha256,
            legacy.privateSha256,
          );
          if (combined != expectedPrivateSha256) {
            throw const AtlasLocalCacheMigrationException();
          }
          intent = _CachePrivateMigrationIntent(
            expectedCombinedPrivateSha256: expectedPrivateSha256,
            durablePrivateSha256: durable.privateSha256,
            legacyPrivateSha256: legacy.privateSha256,
          );
          await _writeCleanupIntent(
            location.privateMigrationIntentFile,
            intent,
            createOnly: true,
          );
        } else if (intent.expectedCombinedPrivateSha256 !=
            expectedPrivateSha256) {
          throw const AtlasLocalCacheMigrationException();
        }

        if (!intent.durableCleared) {
          final durable = await _readMigrationCache(location.cacheFile);
          if (durable.privateSha256 != null) {
            if (durable.privateSha256 != intent.durablePrivateSha256) {
              throw const AtlasLocalCacheMigrationException();
            }
            await AtlasLocalCacheStore(
              file: location.cacheFile,
            ).removePrivateStateForMigration(
              expectedPrivateSha256: durable.privateSha256!,
            );
          } else if (intent.durablePrivateSha256 != null &&
              !durable.cachePresent) {
            throw const AtlasLocalCacheMigrationException();
          }
          final verified = await _readMigrationCache(location.cacheFile);
          if (verified.privateSha256 != null ||
              verified.containsPrivateState ||
              (intent.durablePrivateSha256 != null && !verified.cachePresent)) {
            throw const AtlasLocalCacheMigrationException();
          }
          intent = intent.copyWith(durableCleared: true);
          await _writeCleanupIntent(
            location.privateMigrationIntentFile,
            intent,
          );
        } else {
          final durable = await _readMigrationCache(location.cacheFile);
          if (durable.privateSha256 != null || durable.containsPrivateState) {
            throw const AtlasLocalCacheMigrationException();
          }
        }

        if (!intent.legacyRetired) {
          await location.prepareForClearUnderMutationLock();
          if (!await location.legacyImportRetiredFile.exists()) {
            throw const AtlasLocalCacheMigrationException();
          }
          intent = intent.copyWith(legacyRetired: true);
          await _writeCleanupIntent(
            location.privateMigrationIntentFile,
            intent,
          );
        } else if (!await location.legacyImportRetiredFile.exists()) {
          throw const AtlasLocalCacheMigrationException();
        }

        if (!intent.legacyDeleted) {
          final legacy = await _readMigrationCache(location.legacyFile);
          if (legacy.cachePresent &&
              legacy.privateSha256 != intent.legacyPrivateSha256) {
            throw const AtlasLocalCacheMigrationException();
          }
          if (await location.legacyFile.exists()) {
            await location.legacyFile.delete();
          }
          if (await location.legacyFile.exists()) {
            throw const AtlasLocalCacheMigrationException();
          }
          intent = intent.copyWith(legacyDeleted: true);
          await _writeCleanupIntent(
            location.privateMigrationIntentFile,
            intent,
          );
        } else if (await location.legacyFile.exists()) {
          throw const AtlasLocalCacheMigrationException();
        }

        final durable = await _readMigrationCache(location.cacheFile);
        if (durable.privateSha256 != null ||
            durable.containsPrivateState ||
            !await location.legacyImportRetiredFile.exists() ||
            await location.legacyFile.exists()) {
          throw const AtlasLocalCacheMigrationException();
        }
        await location.privateMigrationIntentFile.delete();
        if (await location.privateMigrationIntentFile.exists()) {
          throw const AtlasLocalCacheMigrationException();
        }
      } on AtlasLocalCacheMigrationException {
        rethrow;
      } catch (_) {
        throw const AtlasLocalCacheMigrationException();
      }
    });
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

Future<AtlasLocalCacheMigrationPrivateState> _readMigrationCache(File file) {
  return AtlasLocalCacheStore(file: file).readPrivateStateForMigration();
}

final class _CachePrivateMigrationIntent {
  const _CachePrivateMigrationIntent({
    required this.expectedCombinedPrivateSha256,
    required this.durablePrivateSha256,
    required this.legacyPrivateSha256,
    this.durableCleared = false,
    this.legacyRetired = false,
    this.legacyDeleted = false,
  });

  static const format = 'atlasvault-windows-cache-private-migration';
  static const version = 1;

  final String? expectedCombinedPrivateSha256;
  final String? durablePrivateSha256;
  final String? legacyPrivateSha256;
  final bool durableCleared;
  final bool legacyRetired;
  final bool legacyDeleted;

  _CachePrivateMigrationIntent copyWith({
    bool? durableCleared,
    bool? legacyRetired,
    bool? legacyDeleted,
  }) {
    return _CachePrivateMigrationIntent(
      expectedCombinedPrivateSha256: expectedCombinedPrivateSha256,
      durablePrivateSha256: durablePrivateSha256,
      legacyPrivateSha256: legacyPrivateSha256,
      durableCleared: durableCleared ?? this.durableCleared,
      legacyRetired: legacyRetired ?? this.legacyRetired,
      legacyDeleted: legacyDeleted ?? this.legacyDeleted,
    );
  }

  Uint8List canonicalBytes() {
    return encodeCanonicalJson(<String, Object?>{
      'format': format,
      'version': version,
      'expected_combined_private_sha256': expectedCombinedPrivateSha256,
      'durable_private_sha256': durablePrivateSha256,
      'legacy_private_sha256': legacyPrivateSha256,
      'durable_cleared': durableCleared,
      'legacy_retired': legacyRetired,
      'legacy_deleted': legacyDeleted,
    });
  }

  static _CachePrivateMigrationIntent decode(Uint8List bytes) {
    try {
      final decoded = jsonDecode(utf8.decode(bytes, allowMalformed: false));
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
          decoded['format'] != format ||
          decoded['version'] != version ||
          !_nullableSha256(decoded['expected_combined_private_sha256']) ||
          !_nullableSha256(decoded['durable_private_sha256']) ||
          !_nullableSha256(decoded['legacy_private_sha256']) ||
          decoded['durable_cleared'] is! bool ||
          decoded['legacy_retired'] is! bool ||
          decoded['legacy_deleted'] is! bool) {
        throw const AtlasLocalCacheMigrationException();
      }
      final intent = _CachePrivateMigrationIntent(
        expectedCombinedPrivateSha256:
            decoded['expected_combined_private_sha256'] as String?,
        durablePrivateSha256: decoded['durable_private_sha256'] as String?,
        legacyPrivateSha256: decoded['legacy_private_sha256'] as String?,
        durableCleared: decoded['durable_cleared']! as bool,
        legacyRetired: decoded['legacy_retired']! as bool,
        legacyDeleted: decoded['legacy_deleted']! as bool,
      );
      if ((intent.legacyRetired && !intent.durableCleared) ||
          (intent.legacyDeleted && !intent.legacyRetired)) {
        throw const AtlasLocalCacheMigrationException();
      }
      final canonical = intent.canonicalBytes();
      try {
        if (!_cacheBytesEqual(canonical, bytes)) {
          throw const AtlasLocalCacheMigrationException();
        }
      } finally {
        canonical.fillRange(0, canonical.length, 0);
      }
      return intent;
    } on AtlasLocalCacheMigrationException {
      rethrow;
    } catch (_) {
      throw const AtlasLocalCacheMigrationException();
    }
  }
}

Future<_CachePrivateMigrationIntent?> _readCleanupIntentIfPresent(
  File file,
) async {
  await recoverInterruptedCacheReplacement(file);
  if (!await file.exists()) {
    return null;
  }
  final bytes = await file.readAsBytes();
  try {
    return _CachePrivateMigrationIntent.decode(bytes);
  } finally {
    bytes.fillRange(0, bytes.length, 0);
  }
}

Future<void> _writeCleanupIntent(
  File target,
  _CachePrivateMigrationIntent intent, {
  bool createOnly = false,
}) async {
  await recoverInterruptedCacheReplacement(target);
  if (createOnly && await target.exists()) {
    throw const AtlasLocalCacheMigrationException();
  }
  final staged = cacheReplacementTemporaryFile(target);
  final bytes = intent.canonicalBytes();
  try {
    await target.parent.create(recursive: true);
    await staged.writeAsBytes(bytes, flush: true);
    if (createOnly && await target.exists()) {
      throw const AtlasLocalCacheMigrationException();
    }
    await replaceCacheFile(
      targetFile: target,
      stagedFile: staged,
      useWindowsRecoveryProtocol: true,
    );
    final restored = await target.readAsBytes();
    try {
      if (!_cacheBytesEqual(bytes, restored)) {
        throw const AtlasLocalCacheMigrationException();
      }
    } finally {
      restored.fillRange(0, restored.length, 0);
    }
  } finally {
    bytes.fillRange(0, bytes.length, 0);
    if (await staged.exists()) {
      await staged.delete();
    }
  }
}

bool _nullableSha256(Object? value) {
  return value == null || _isSha256(value);
}

bool _isSha256(Object? value) =>
    value is String && RegExp(r'^[0-9a-f]{64}$').hasMatch(value);

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
  bool importLegacyCache = true,
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

  if (importLegacyCache) {
    await _copyLegacyCacheIfNeeded(
      legacyFile: legacyFile,
      targetFile: targetFile,
      legacyImportRetiredFile: legacyImportRetiredFile,
    );
  }
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
  final pathIdentity = _mutationPathIdentity(targetFile);
  final inheritedLease = Zone.current[_migrationLeaseZoneKey];
  if (inheritedLease is _CacheMutationLease &&
      inheritedLease.active &&
      inheritedLease.pathIdentity == pathIdentity) {
    return operation();
  }
  return _withInProcessMigrationLock(() async {
    await targetFile.parent.create(recursive: true);
    final migrationLock = await File(
      '${targetFile.path}.migration.lock',
    ).open(mode: FileMode.append);
    var lockAcquired = false;
    final lease = _CacheMutationLease(pathIdentity);
    try {
      await migrationLock.lock(FileLock.blockingExclusive);
      lockAcquired = true;
      return await runZoned(
        operation,
        zoneValues: <Object, Object>{_migrationLeaseZoneKey: lease},
      );
    } finally {
      lease.active = false;
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

final class _CacheMutationLease {
  _CacheMutationLease(this.pathIdentity);

  final String pathIdentity;
  bool active = true;
}

String _mutationPathIdentity(File targetFile) {
  final path = targetFile.absolute.path;
  return Platform.isWindows ? path.toLowerCase() : path;
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
