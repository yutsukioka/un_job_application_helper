import 'dart:convert';

import 'package:flutter/services.dart';

import '../../atlas_vault.dart';
import 'android_storage.dart' show AtlasVaultSecureKeyStore;
import 'canonical_json.dart';
import 'interoperability.dart';
import 'local_store_io.dart';
import 'plaintext_migration.dart';

const String atlasVaultWindowsMethodChannelName = 'atlas/vault_windows';

const MethodChannel _defaultAtlasVaultWindowsChannel = MethodChannel(
  atlasVaultWindowsMethodChannelName,
);

final class AtlasVaultWindowsStorageException implements Exception {
  const AtlasVaultWindowsStorageException();

  @override
  String toString() => 'AtlasVault Windows storage operation failed.';
}

final class AtlasVaultWindowsCapabilities {
  const AtlasVaultWindowsCapabilities._({
    required this.secureBoundaryAvailable,
    required this.dpapiAvailable,
    required this.currentUserScope,
    required this.localAppDataAvailable,
    required this.atomicReplaceAvailable,
    required this.hardwareBackedGuaranteed,
  });

  final bool secureBoundaryAvailable;
  final bool dpapiAvailable;
  final bool currentUserScope;
  final bool localAppDataAvailable;
  final bool atomicReplaceAvailable;
  final bool hardwareBackedGuaranteed;

  factory AtlasVaultWindowsCapabilities.fromPlatform(
    Map<Object?, Object?> value,
  ) {
    const expectedKeys = <String>{
      'secure_boundary_available',
      'dpapi_available',
      'current_user_scope',
      'local_app_data_available',
      'atomic_replace_available',
      'hardware_backed_guaranteed',
    };
    if (value.keys.length != expectedKeys.length ||
        !value.keys.every(expectedKeys.contains)) {
      throw const AtlasVaultWindowsStorageException();
    }
    final secureBoundaryAvailable = value['secure_boundary_available'];
    final dpapiAvailable = value['dpapi_available'];
    final currentUserScope = value['current_user_scope'];
    final localAppDataAvailable = value['local_app_data_available'];
    final atomicReplaceAvailable = value['atomic_replace_available'];
    final hardwareBackedGuaranteed = value['hardware_backed_guaranteed'];
    if (secureBoundaryAvailable is! bool ||
        dpapiAvailable is! bool ||
        currentUserScope is! bool ||
        localAppDataAvailable is! bool ||
        atomicReplaceAvailable is! bool ||
        hardwareBackedGuaranteed is! bool) {
      throw const AtlasVaultWindowsStorageException();
    }
    return AtlasVaultWindowsCapabilities._(
      secureBoundaryAvailable: secureBoundaryAvailable,
      dpapiAvailable: dpapiAvailable,
      currentUserScope: currentUserScope,
      localAppDataAvailable: localAppDataAvailable,
      atomicReplaceAvailable: atomicReplaceAvailable,
      hardwareBackedGuaranteed: hardwareBackedGuaranteed,
    );
  }

  @override
  String toString() => 'AtlasVaultWindowsCapabilities(<redacted>)';
}

final class AtlasWindowsVaultSecureKeyStore
    implements AtlasVaultSecureKeyStore {
  AtlasWindowsVaultSecureKeyStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultWindowsChannel;

  final MethodChannel _channel;

  Future<AtlasVaultWindowsCapabilities> capabilities() async {
    try {
      final value = await _channel.invokeMethod<Object?>('capabilities');
      if (value is! Map<Object?, Object?>) {
        throw const AtlasVaultWindowsStorageException();
      }
      return AtlasVaultWindowsCapabilities.fromPlatform(value);
    } on AtlasVaultWindowsStorageException {
      rethrow;
    } catch (_) {
      throw const AtlasVaultWindowsStorageException();
    }
  }

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    _validateVaultId(vaultId);
    if (vaultKey.length != 32) {
      throw const AtlasVaultWindowsStorageException();
    }
    final keyCopy = Uint8List.fromList(vaultKey);
    try {
      await _invoke<void>(_channel, 'createVaultKey', <String, Object?>{
        'vault_id': vaultId,
        'vault_key': keyCopy,
      });
    } finally {
      _wipe(keyCopy);
    }
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async {
    _validateVaultId(vaultId);
    final value = await _invoke<Object?>(
      _channel,
      'loadVaultKey',
      <String, Object?>{'vault_id': vaultId},
    );
    if (value == null) {
      return null;
    }
    final bytes = _copyBytes(value);
    if (bytes.length != 32) {
      _wipe(bytes);
      throw const AtlasVaultWindowsStorageException();
    }
    return bytes;
  }

  @override
  Future<bool> containsVaultKey(String vaultId) async {
    _validateVaultId(vaultId);
    final value = await _invoke<Object?>(
      _channel,
      'containsVaultKey',
      <String, Object?>{'vault_id': vaultId},
    );
    if (value is! bool) {
      throw const AtlasVaultWindowsStorageException();
    }
    return value;
  }

  @override
  Future<void> deleteVaultKey(String vaultId) async {
    _validateVaultId(vaultId);
    await _invoke<void>(_channel, 'deleteVaultKey', <String, Object?>{
      'vault_id': vaultId,
    });
  }

  @override
  String toString() => 'AtlasWindowsVaultSecureKeyStore(<redacted>)';
}

final class AtlasWindowsVaultLocalStoreIO implements AtlasVaultLocalStoreIO {
  AtlasWindowsVaultLocalStoreIO({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultWindowsChannel;

  static const int maximumStoreByteCount = 128 * 1024 * 1024;

  final MethodChannel _channel;

  @override
  Future<AtlasVaultLocalStore?> read(String vaultId) async {
    _validateVaultId(vaultId);
    final value = await _invoke<Object?>(
      _channel,
      'readLocalStore',
      <String, Object?>{'vault_id': vaultId},
    );
    if (value == null) {
      return null;
    }
    final bytes = _copyBytes(value);
    try {
      _validateSize(bytes);
      final source = utf8.decode(bytes, allowMalformed: false);
      final store = AtlasVaultLocalStore.decodeJson(source);
      final canonical = store.canonicalBytes();
      try {
        if (!_constantTimeEquals(bytes, canonical) ||
            store.vaultMetadata.vaultId != vaultId) {
          throw const AtlasVaultWindowsStorageException();
        }
      } finally {
        _wipe(canonical);
      }
      return store;
    } on AtlasVaultWindowsStorageException {
      rethrow;
    } catch (_) {
      throw const AtlasVaultWindowsStorageException();
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> create(String vaultId, AtlasVaultLocalStore store) async {
    final bytes = _validatedCanonicalBytes(vaultId, store);
    try {
      await _invoke<void>(_channel, 'createLocalStore', <String, Object?>{
        'vault_id': vaultId,
        'store_bytes': bytes,
      });
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> replace(
    String vaultId,
    AtlasVaultLocalStore store, {
    required String expectedSha256,
  }) async {
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(expectedSha256)) {
      throw const AtlasVaultWindowsStorageException();
    }
    final bytes = _validatedCanonicalBytes(vaultId, store);
    try {
      await _invoke<void>(_channel, 'replaceLocalStore', <String, Object?>{
        'vault_id': vaultId,
        'store_bytes': bytes,
        'expected_sha256': expectedSha256,
      });
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> delete(String vaultId) async {
    _validateVaultId(vaultId);
    await _invoke<void>(_channel, 'deleteLocalStore', <String, Object?>{
      'vault_id': vaultId,
    });
  }

  Uint8List _validatedCanonicalBytes(
    String vaultId,
    AtlasVaultLocalStore store,
  ) {
    _validateVaultId(vaultId);
    if (store.vaultMetadata.vaultId != vaultId) {
      throw const AtlasVaultWindowsStorageException();
    }
    final source = store.canonicalBytes();
    try {
      _validateSize(source);
      return Uint8List.fromList(source);
    } finally {
      _wipe(source);
    }
  }

  void _validateSize(Uint8List value) {
    if (value.isEmpty || value.length > maximumStoreByteCount) {
      throw const AtlasVaultWindowsStorageException();
    }
  }

  @override
  String toString() => 'AtlasWindowsVaultLocalStoreIO(<redacted>)';
}

final class AtlasWindowsEncryptedDocumentTransport
    implements AtlasVaultEncryptedDocumentTransport {
  AtlasWindowsEncryptedDocumentTransport({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultWindowsChannel;

  static const int maximumDocumentByteCount = 128 * 1024 * 1024;

  final MethodChannel _channel;

  @override
  Future<Uint8List?> pickEncryptedExport() async {
    final value = await _invoke<Object?>(_channel, 'pickEncryptedExport', null);
    if (value == null) {
      return null;
    }
    final bytes = _copyBytes(value);
    try {
      if (bytes.isEmpty || bytes.length > maximumDocumentByteCount) {
        throw const AtlasVaultWindowsStorageException();
      }
      return Uint8List.fromList(bytes);
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes) async {
    if (canonicalExportBytes.isEmpty ||
        canonicalExportBytes.length > maximumDocumentByteCount) {
      throw const AtlasVaultWindowsStorageException();
    }
    final bytes = Uint8List.fromList(canonicalExportBytes);
    try {
      final value = await _invoke<Object?>(
        _channel,
        'saveEncryptedExport',
        <String, Object?>{'export_bytes': bytes},
      );
      if (value is! bool) {
        throw const AtlasVaultWindowsStorageException();
      }
      return value;
    } finally {
      _wipe(bytes);
    }
  }

  @override
  String toString() => 'AtlasWindowsEncryptedDocumentTransport(<redacted>)';
}

final class AtlasWindowsProtectedMigrationJournalStore
    implements AtlasVaultProtectedMigrationJournalStore {
  AtlasWindowsProtectedMigrationJournalStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultWindowsChannel;

  static const int maximumJournalByteCount = 16 * 1024 * 1024;

  final MethodChannel _channel;

  @override
  Future<Uint8List?> read() async {
    final value = await _invoke<Object?>(
      _channel,
      'readPlaintextMigrationJournal',
      null,
    );
    if (value == null) {
      return null;
    }
    final bytes = _copyBytes(value);
    try {
      _validateJournalBytes(bytes);
      return Uint8List.fromList(bytes);
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> create(Uint8List canonicalBytes) async {
    final bytes = _validatedJournalCopy(canonicalBytes);
    try {
      await _invoke<void>(
        _channel,
        'createPlaintextMigrationJournal',
        <String, Object?>{'journal_bytes': bytes},
      );
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) async {
    _validateSha256(expectedSha256);
    final bytes = _validatedJournalCopy(canonicalBytes);
    try {
      await _invoke<void>(
        _channel,
        'replacePlaintextMigrationJournal',
        <String, Object?>{
          'journal_bytes': bytes,
          'expected_sha256': expectedSha256,
        },
      );
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) async {
    _validateSha256(expectedSha256);
    await _invoke<void>(
      _channel,
      'deletePlaintextMigrationJournal',
      <String, Object?>{
        'expected_sha256': expectedSha256,
        'allow_absent': allowAbsent,
      },
    );
  }

  Uint8List _validatedJournalCopy(Uint8List source) {
    _validateJournalBytes(source);
    return Uint8List.fromList(source);
  }

  void _validateJournalBytes(Uint8List source) {
    if (source.isEmpty || source.length > maximumJournalByteCount) {
      throw const AtlasVaultWindowsStorageException();
    }
    Uint8List? canonical;
    try {
      final decoded = jsonDecode(utf8.decode(source, allowMalformed: false));
      if (decoded is! Map) {
        throw const AtlasVaultWindowsStorageException();
      }
      final value = <String, Object?>{};
      for (final entry in decoded.entries) {
        if (entry.key is! String) {
          throw const AtlasVaultWindowsStorageException();
        }
        value[entry.key as String] = entry.value;
      }
      canonical = encodeCanonicalJson(value);
      if (!_constantTimeEquals(source, canonical)) {
        throw const AtlasVaultWindowsStorageException();
      }
    } catch (_) {
      throw const AtlasVaultWindowsStorageException();
    } finally {
      if (canonical != null) {
        _wipe(canonical);
      }
    }
  }

  @override
  String toString() => 'AtlasWindowsProtectedMigrationJournalStore(<redacted>)';
}

final class AtlasWindowsProtectedRecoveryImportJournalStore
    implements AtlasVaultProtectedRecoveryImportJournalStore {
  AtlasWindowsProtectedRecoveryImportJournalStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultWindowsChannel;

  static const int maximumJournalByteCount = 16 * 1024 * 1024;

  final MethodChannel _channel;

  @override
  Future<Uint8List?> read() async {
    final value = await _invoke<Object?>(
      _channel,
      'readRecoveryImportJournal',
      null,
    );
    if (value == null) {
      return null;
    }
    final bytes = _copyBytes(value);
    try {
      _validateJournalBytes(bytes);
      return Uint8List.fromList(bytes);
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> create(Uint8List canonicalBytes) async {
    final bytes = _validatedJournalCopy(canonicalBytes);
    try {
      await _invoke<void>(
        _channel,
        'createRecoveryImportJournal',
        <String, Object?>{'journal_bytes': bytes},
      );
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) async {
    _validateSha256(expectedSha256);
    final bytes = _validatedJournalCopy(canonicalBytes);
    try {
      await _invoke<void>(
        _channel,
        'replaceRecoveryImportJournal',
        <String, Object?>{
          'journal_bytes': bytes,
          'expected_sha256': expectedSha256,
        },
      );
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) async {
    _validateSha256(expectedSha256);
    await _invoke<void>(
      _channel,
      'deleteRecoveryImportJournal',
      <String, Object?>{
        'expected_sha256': expectedSha256,
        'allow_absent': allowAbsent,
      },
    );
  }

  Uint8List _validatedJournalCopy(Uint8List source) {
    _validateJournalBytes(source);
    return Uint8List.fromList(source);
  }

  void _validateJournalBytes(Uint8List source) {
    if (source.isEmpty || source.length > maximumJournalByteCount) {
      throw const AtlasVaultWindowsStorageException();
    }
    Uint8List? canonical;
    try {
      final journal = AtlasVaultRecoveryImportJournal.decodeBytes(
        source,
        profile: AtlasVaultRecoveryImportProfile.windows,
      );
      canonical = journal.canonicalBytes();
      if (!_constantTimeEquals(source, canonical)) {
        throw const AtlasVaultWindowsStorageException();
      }
    } catch (_) {
      throw const AtlasVaultWindowsStorageException();
    } finally {
      if (canonical != null) {
        _wipe(canonical);
      }
    }
  }

  @override
  String toString() =>
      'AtlasWindowsProtectedRecoveryImportJournalStore(<redacted>)';
}

final class AtlasWindowsSelectedVaultStore
    implements AtlasVaultSelectedVaultStore {
  AtlasWindowsSelectedVaultStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultWindowsChannel;

  final MethodChannel _channel;

  @override
  Future<String?> read() async {
    final value = await _invoke<Object?>(_channel, 'readSelectedVault', null);
    if (value == null) {
      return null;
    }
    if (value is! String) {
      throw const AtlasVaultWindowsStorageException();
    }
    _validateVaultId(value);
    return value;
  }

  @override
  Future<void> create(String vaultId) async {
    _validateVaultId(vaultId);
    await _invoke<void>(_channel, 'createSelectedVault', <String, Object?>{
      'vault_id': vaultId,
    });
  }

  @override
  Future<void> clear(String expectedVaultId) async {
    _validateVaultId(expectedVaultId);
    await _invoke<void>(_channel, 'clearSelectedVault', <String, Object?>{
      'expected_vault_id': expectedVaultId,
    });
  }

  @override
  String toString() => 'AtlasWindowsSelectedVaultStore(<redacted>)';
}

void _validateVaultId(String value) {
  const reserved = <String>{
    'saved_search',
    'saved_job',
    'application_note',
    'profile_snippet',
    'draft_metadata',
  };
  if (value.isEmpty ||
      value.length > 96 ||
      !RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(value) ||
      reserved.contains(value.toLowerCase())) {
    throw const AtlasVaultWindowsStorageException();
  }
}

void _validateSha256(String value) {
  if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
    throw const AtlasVaultWindowsStorageException();
  }
}

Future<T?> _invoke<T>(
  MethodChannel channel,
  String method,
  Object? arguments,
) async {
  try {
    return await channel.invokeMethod<T>(method, arguments);
  } on AtlasVaultWindowsStorageException {
    rethrow;
  } catch (_) {
    throw const AtlasVaultWindowsStorageException();
  }
}

Uint8List _copyBytes(Object value) {
  if (value is Uint8List) {
    return Uint8List.fromList(value);
  }
  if (value is List<int>) {
    return Uint8List.fromList(value);
  }
  throw const AtlasVaultWindowsStorageException();
}

bool _constantTimeEquals(Uint8List left, Uint8List right) {
  var difference = left.length ^ right.length;
  final maximum = left.length > right.length ? left.length : right.length;
  for (var index = 0; index < maximum; index += 1) {
    difference |=
        (index < left.length ? left[index] : 0) ^
        (index < right.length ? right[index] : 0);
  }
  return difference == 0;
}

void _wipe(Uint8List value) {
  value.fillRange(0, value.length, 0);
}
