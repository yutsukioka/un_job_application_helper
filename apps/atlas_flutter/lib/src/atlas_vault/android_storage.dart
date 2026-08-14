import 'dart:convert';

import 'package:flutter/services.dart';

import '../../atlas_vault.dart';
import 'canonical_json.dart';
import 'interoperability.dart';
import 'local_store_io.dart';
import 'plaintext_migration.dart';
import 'strict_values.dart';

const String atlasVaultAndroidMethodChannelName = 'atlas/vault_android';

const MethodChannel _defaultAtlasVaultAndroidChannel = MethodChannel(
  atlasVaultAndroidMethodChannelName,
);

final class AtlasVaultAndroidStorageException implements Exception {
  const AtlasVaultAndroidStorageException();

  @override
  String toString() => 'AtlasVault Android storage operation failed.';
}

final class AtlasAndroidDeviceIdentitySecretStore
    implements AtlasDeviceIdentitySecretStore {
  AtlasAndroidDeviceIdentitySecretStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultAndroidChannel;

  static const int maximumSecretByteCount = 16 * 1024;

  final MethodChannel _channel;

  @override
  Future<void> createPrimaryIdentity(Uint8List canonicalSecretBundle) async {
    final bytes = await _validatedSecretCopy(canonicalSecretBundle);
    try {
      await invokeAtlasVaultAndroidMethodInternal<void>(
        _channel,
        'createDeviceIdentitySecret',
        <String, Object?>{'secret_bytes': bytes},
      );
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<Uint8List?> loadPrimaryIdentity() async {
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'loadDeviceIdentitySecret',
      null,
    );
    if (value == null) {
      return null;
    }
    final bytes = copyAtlasVaultAndroidBytesInternal(value);
    try {
      return await _validatedSecretCopy(bytes);
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<bool> containsPrimaryIdentity() async {
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'containsDeviceIdentitySecret',
      null,
    );
    if (value is! bool) {
      throw const AtlasVaultAndroidStorageException();
    }
    return value;
  }

  @override
  Future<void> deletePrimaryIdentity() async {
    await invokeAtlasVaultAndroidMethodInternal<void>(
      _channel,
      'deleteDeviceIdentitySecret',
      null,
    );
  }

  void _validateSecretBytes(Uint8List value) {
    if (value.isEmpty || value.length > maximumSecretByteCount) {
      throw const AtlasVaultAndroidStorageException();
    }
  }

  Future<Uint8List> _validatedSecretCopy(Uint8List source) async {
    _validateSecretBytes(source);
    final copy = Uint8List.fromList(source);
    AtlasVaultDeviceIdentitySecret? secret;
    AtlasVaultDeviceIdentity? identity;
    Uint8List? canonical;
    var accepted = false;
    try {
      final decoded = jsonDecode(utf8.decode(copy));
      if (decoded is! Map) {
        throw const AtlasVaultAndroidStorageException();
      }
      final object = <String, Object?>{};
      for (final entry in decoded.entries) {
        if (entry.key is! String) {
          throw const AtlasVaultAndroidStorageException();
        }
        object[entry.key as String] = entry.value;
      }
      secret = AtlasVaultDeviceIdentitySecret.fromJson(object);
      canonical = secret.canonicalBytes();
      if (!_constantTimeEquals(copy, canonical)) {
        throw const AtlasVaultAndroidStorageException();
      }
      identity = await secret.loadIdentity();
      accepted = true;
      return copy;
    } catch (_) {
      throw const AtlasVaultAndroidStorageException();
    } finally {
      identity?.destroy();
      secret?.destroy();
      if (canonical != null) {
        wipeAtlasVaultAndroidBytesInternal(canonical);
      }
      if (!accepted) {
        wipeAtlasVaultAndroidBytesInternal(copy);
      }
    }
  }

  @override
  String toString() => 'AtlasAndroidDeviceIdentitySecretStore(<redacted>)';
}

final class AtlasAndroidEncryptedDocumentTransport
    implements AtlasVaultEncryptedDocumentTransport {
  AtlasAndroidEncryptedDocumentTransport({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultAndroidChannel;

  static const int maximumDocumentByteCount = 128 * 1024 * 1024;

  final MethodChannel _channel;

  @override
  Future<Uint8List?> pickEncryptedExport() async {
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'pickEncryptedExport',
      null,
    );
    if (value == null) {
      return null;
    }
    final bytes = copyAtlasVaultAndroidBytesInternal(value);
    if (bytes.isEmpty || bytes.length > maximumDocumentByteCount) {
      wipeAtlasVaultAndroidBytesInternal(bytes);
      throw const AtlasVaultAndroidStorageException();
    }
    return bytes;
  }

  @override
  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes) async {
    if (canonicalExportBytes.isEmpty ||
        canonicalExportBytes.length > maximumDocumentByteCount) {
      throw const AtlasVaultAndroidStorageException();
    }
    final bytes = Uint8List.fromList(canonicalExportBytes);
    try {
      final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
        _channel,
        'saveEncryptedExport',
        <String, Object?>{'export_bytes': bytes},
      );
      if (value is! bool) {
        throw const AtlasVaultAndroidStorageException();
      }
      return value;
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  String toString() => 'AtlasAndroidEncryptedDocumentTransport(<redacted>)';
}

final class AtlasVaultAndroidCapabilities {
  const AtlasVaultAndroidCapabilities._({
    required this.apiLevel,
    required this.secureBoundaryAvailable,
    required this.aesGcmKeystoreAvailable,
    required this.hardwareBacked,
    required this.strongBoxBacked,
    required this.noBackupStorageAvailable,
  });

  final int apiLevel;
  final bool secureBoundaryAvailable;
  final bool aesGcmKeystoreAvailable;
  final bool hardwareBacked;
  final bool strongBoxBacked;
  final bool noBackupStorageAvailable;

  factory AtlasVaultAndroidCapabilities.fromPlatform(
    Map<Object?, Object?> value,
  ) {
    const expectedKeys = <String>{
      'api_level',
      'secure_boundary_available',
      'aes_gcm_keystore_available',
      'hardware_backed',
      'strongbox_backed',
      'no_backup_storage_available',
    };
    if (value.keys.length != expectedKeys.length ||
        !value.keys.every(expectedKeys.contains)) {
      throw const AtlasVaultAndroidStorageException();
    }
    final apiLevel = value['api_level'];
    final secureBoundaryAvailable = value['secure_boundary_available'];
    final aesGcmKeystoreAvailable = value['aes_gcm_keystore_available'];
    final hardwareBacked = value['hardware_backed'];
    final strongBoxBacked = value['strongbox_backed'];
    final noBackupStorageAvailable = value['no_backup_storage_available'];
    if (apiLevel is! int ||
        secureBoundaryAvailable is! bool ||
        aesGcmKeystoreAvailable is! bool ||
        hardwareBacked is! bool ||
        strongBoxBacked is! bool ||
        noBackupStorageAvailable is! bool) {
      throw const AtlasVaultAndroidStorageException();
    }
    return AtlasVaultAndroidCapabilities._(
      apiLevel: apiLevel,
      secureBoundaryAvailable: secureBoundaryAvailable,
      aesGcmKeystoreAvailable: aesGcmKeystoreAvailable,
      hardwareBacked: hardwareBacked,
      strongBoxBacked: strongBoxBacked,
      noBackupStorageAvailable: noBackupStorageAvailable,
    );
  }

  @override
  String toString() => 'AtlasVaultAndroidCapabilities(<redacted>)';
}

abstract interface class AtlasVaultSecureKeyStore
    implements AtlasVaultMigrationSecureKeyStore {}

final class AtlasAndroidVaultSecureKeyStore
    implements AtlasVaultSecureKeyStore {
  AtlasAndroidVaultSecureKeyStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultAndroidChannel;

  final MethodChannel _channel;

  Future<AtlasVaultAndroidCapabilities> capabilities() async {
    try {
      final value = await _channel.invokeMethod<Object?>('capabilities');
      if (value is! Map<Object?, Object?>) {
        throw const AtlasVaultAndroidStorageException();
      }
      return AtlasVaultAndroidCapabilities.fromPlatform(value);
    } on AtlasVaultAndroidStorageException {
      rethrow;
    } catch (_) {
      throw const AtlasVaultAndroidStorageException();
    }
  }

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    validateAtlasVaultAndroidVaultIdInternal(vaultId);
    if (vaultKey.length != 32) {
      throw const AtlasVaultAndroidStorageException();
    }
    final keyCopy = Uint8List.fromList(vaultKey);
    try {
      await invokeAtlasVaultAndroidMethodInternal<void>(
        _channel,
        'createVaultKey',
        <String, Object?>{'vault_id': vaultId, 'vault_key': keyCopy},
      );
    } finally {
      wipeAtlasVaultAndroidBytesInternal(keyCopy);
    }
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async {
    validateAtlasVaultAndroidVaultIdInternal(vaultId);
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'loadVaultKey',
      <String, Object?>{'vault_id': vaultId},
    );
    if (value == null) {
      return null;
    }
    final bytes = copyAtlasVaultAndroidBytesInternal(value);
    if (bytes.length != 32) {
      wipeAtlasVaultAndroidBytesInternal(bytes);
      throw const AtlasVaultAndroidStorageException();
    }
    return bytes;
  }

  @override
  Future<bool> containsVaultKey(String vaultId) async {
    validateAtlasVaultAndroidVaultIdInternal(vaultId);
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'containsVaultKey',
      <String, Object?>{'vault_id': vaultId},
    );
    if (value is! bool) {
      throw const AtlasVaultAndroidStorageException();
    }
    return value;
  }

  @override
  Future<void> deleteVaultKey(String vaultId) async {
    validateAtlasVaultAndroidVaultIdInternal(vaultId);
    await invokeAtlasVaultAndroidMethodInternal<void>(
      _channel,
      'deleteVaultKey',
      <String, Object?>{'vault_id': vaultId},
    );
  }

  @override
  String toString() => 'AtlasAndroidVaultSecureKeyStore(<redacted>)';
}

final class AtlasAndroidVaultLocalStoreIO implements AtlasVaultLocalStoreIO {
  AtlasAndroidVaultLocalStoreIO({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultAndroidChannel;

  static const int maximumStoreByteCount = 128 * 1024 * 1024;

  final MethodChannel _channel;

  @override
  Future<AtlasVaultLocalStore?> read(String vaultId) async {
    validateAtlasVaultAndroidVaultIdInternal(vaultId);
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'readLocalStore',
      <String, Object?>{'vault_id': vaultId},
    );
    if (value == null) {
      return null;
    }
    final bytes = copyAtlasVaultAndroidBytesInternal(value);
    try {
      _validateSize(bytes);
      final source = utf8.decode(bytes, allowMalformed: false);
      final store = AtlasVaultLocalStore.decodeJson(source);
      final canonical = store.canonicalBytes();
      try {
        if (!_constantTimeEquals(bytes, canonical) ||
            store.vaultMetadata.vaultId != vaultId) {
          throw const AtlasVaultAndroidStorageException();
        }
      } finally {
        wipeAtlasVaultAndroidBytesInternal(canonical);
      }
      return store;
    } on AtlasVaultAndroidStorageException {
      rethrow;
    } catch (_) {
      throw const AtlasVaultAndroidStorageException();
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<void> create(String vaultId, AtlasVaultLocalStore store) async {
    final bytes = _validatedCanonicalBytes(vaultId, store);
    try {
      await invokeAtlasVaultAndroidMethodInternal<void>(
        _channel,
        'createLocalStore',
        <String, Object?>{'vault_id': vaultId, 'store_bytes': bytes},
      );
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<void> replace(
    String vaultId,
    AtlasVaultLocalStore store, {
    required String expectedSha256,
  }) async {
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(expectedSha256)) {
      throw const AtlasVaultAndroidStorageException();
    }
    final bytes = _validatedCanonicalBytes(vaultId, store);
    try {
      await invokeAtlasVaultAndroidMethodInternal<void>(
        _channel,
        'replaceLocalStore',
        <String, Object?>{
          'vault_id': vaultId,
          'store_bytes': bytes,
          'expected_sha256': expectedSha256,
        },
      );
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<void> delete(String vaultId) async {
    validateAtlasVaultAndroidVaultIdInternal(vaultId);
    await invokeAtlasVaultAndroidMethodInternal<void>(
      _channel,
      'deleteLocalStore',
      <String, Object?>{'vault_id': vaultId},
    );
  }

  Uint8List _validatedCanonicalBytes(
    String vaultId,
    AtlasVaultLocalStore store,
  ) {
    validateAtlasVaultAndroidVaultIdInternal(vaultId);
    if (store.vaultMetadata.vaultId != vaultId) {
      throw const AtlasVaultAndroidStorageException();
    }
    final bytes = store.canonicalBytes();
    try {
      _validateSize(bytes);
      return Uint8List.fromList(bytes);
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  void _validateSize(Uint8List bytes) {
    if (bytes.isEmpty || bytes.length > maximumStoreByteCount) {
      throw const AtlasVaultAndroidStorageException();
    }
  }

  bool _constantTimeEquals(Uint8List left, Uint8List right) {
    var difference = left.length ^ right.length;
    final maximum = left.length > right.length ? left.length : right.length;
    for (var index = 0; index < maximum; index += 1) {
      final leftByte = index < left.length ? left[index] : 0;
      final rightByte = index < right.length ? right[index] : 0;
      difference |= leftByte ^ rightByte;
    }
    return difference == 0;
  }

  @override
  String toString() => 'AtlasAndroidVaultLocalStoreIO(<redacted>)';
}

final class AtlasAndroidProtectedMigrationJournalStore
    implements AtlasVaultProtectedMigrationJournalStore {
  AtlasAndroidProtectedMigrationJournalStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultAndroidChannel;

  static const int maximumJournalByteCount = 16 * 1024 * 1024;

  final MethodChannel _channel;

  @override
  Future<Uint8List?> read() async {
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'readPlaintextMigrationJournal',
      null,
    );
    if (value == null) {
      return null;
    }
    final bytes = copyAtlasVaultAndroidBytesInternal(value);
    if (bytes.isEmpty || bytes.length > maximumJournalByteCount) {
      wipeAtlasVaultAndroidBytesInternal(bytes);
      throw const AtlasVaultAndroidStorageException();
    }
    return bytes;
  }

  @override
  Future<void> create(Uint8List canonicalBytes) async {
    final bytes = _validatedJournalBytes(canonicalBytes);
    try {
      await invokeAtlasVaultAndroidMethodInternal<void>(
        _channel,
        'createPlaintextMigrationJournal',
        <String, Object?>{'journal_bytes': bytes},
      );
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) async {
    _validateSha256(expectedSha256);
    final bytes = _validatedJournalBytes(canonicalBytes);
    try {
      await invokeAtlasVaultAndroidMethodInternal<void>(
        _channel,
        'replacePlaintextMigrationJournal',
        <String, Object?>{
          'journal_bytes': bytes,
          'expected_sha256': expectedSha256,
        },
      );
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) async {
    _validateSha256(expectedSha256);
    await invokeAtlasVaultAndroidMethodInternal<void>(
      _channel,
      'deletePlaintextMigrationJournal',
      <String, Object?>{
        'expected_sha256': expectedSha256,
        'allow_absent': allowAbsent,
      },
    );
  }

  Uint8List _validatedJournalBytes(Uint8List source) {
    if (source.isEmpty || source.length > maximumJournalByteCount) {
      throw const AtlasVaultAndroidStorageException();
    }
    Uint8List? canonical;
    try {
      final value = decodeAtlasVaultJsonObject(
        utf8.decode(source, allowMalformed: false),
        context: 'Migration journal',
      );
      canonical = encodeCanonicalJson(value);
      if (!_constantTimeEquals(source, canonical)) {
        throw const AtlasVaultAndroidStorageException();
      }
      return Uint8List.fromList(canonical);
    } catch (_) {
      throw const AtlasVaultAndroidStorageException();
    } finally {
      if (canonical != null) {
        wipeAtlasVaultAndroidBytesInternal(canonical);
      }
    }
  }

  @override
  String toString() => 'AtlasAndroidProtectedMigrationJournalStore(<redacted>)';
}

final class AtlasAndroidProtectedRecoveryImportJournalStore
    implements AtlasVaultProtectedRecoveryImportJournalStore {
  AtlasAndroidProtectedRecoveryImportJournalStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultAndroidChannel;

  static const int maximumJournalByteCount = 64 * 1024;

  final MethodChannel _channel;

  @override
  Future<Uint8List?> read() async {
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'readRecoveryImportJournal',
      null,
    );
    if (value == null) {
      return null;
    }
    final bytes = copyAtlasVaultAndroidBytesInternal(value);
    try {
      _validate(bytes);
      return Uint8List.fromList(bytes);
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<void> create(Uint8List canonicalBytes) async {
    final bytes = _validatedCopy(canonicalBytes);
    try {
      await invokeAtlasVaultAndroidMethodInternal<void>(
        _channel,
        'createRecoveryImportJournal',
        <String, Object?>{'journal_bytes': bytes},
      );
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) async {
    _validateSha256(expectedSha256);
    final bytes = _validatedCopy(canonicalBytes);
    try {
      await invokeAtlasVaultAndroidMethodInternal<void>(
        _channel,
        'replaceRecoveryImportJournal',
        <String, Object?>{
          'journal_bytes': bytes,
          'expected_sha256': expectedSha256,
        },
      );
    } finally {
      wipeAtlasVaultAndroidBytesInternal(bytes);
    }
  }

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) async {
    _validateSha256(expectedSha256);
    await invokeAtlasVaultAndroidMethodInternal<void>(
      _channel,
      'deleteRecoveryImportJournal',
      <String, Object?>{
        'expected_sha256': expectedSha256,
        'allow_absent': allowAbsent,
      },
    );
  }

  Uint8List _validatedCopy(Uint8List source) {
    _validate(source);
    return Uint8List.fromList(source);
  }

  void _validate(Uint8List source) {
    if (source.isEmpty || source.length > maximumJournalByteCount) {
      throw const AtlasVaultAndroidStorageException();
    }
    Uint8List? canonical;
    try {
      final journal = AtlasVaultRecoveryImportJournal.decodeBytes(source);
      canonical = journal.canonicalBytes();
      if (!_constantTimeEquals(source, canonical)) {
        throw const AtlasVaultAndroidStorageException();
      }
    } catch (_) {
      throw const AtlasVaultAndroidStorageException();
    } finally {
      if (canonical != null) {
        wipeAtlasVaultAndroidBytesInternal(canonical);
      }
    }
  }

  @override
  String toString() =>
      'AtlasAndroidProtectedRecoveryImportJournalStore(<redacted>)';
}

final class AtlasAndroidSelectedVaultStore
    implements AtlasVaultSelectedVaultStore {
  AtlasAndroidSelectedVaultStore({MethodChannel? channel})
    : _channel = channel ?? _defaultAtlasVaultAndroidChannel;

  final MethodChannel _channel;

  @override
  Future<String?> read() async {
    final value = await invokeAtlasVaultAndroidMethodInternal<Object?>(
      _channel,
      'readSelectedVault',
      null,
    );
    if (value == null) {
      return null;
    }
    if (value is! String) {
      throw const AtlasVaultAndroidStorageException();
    }
    validateAtlasVaultAndroidVaultIdInternal(value);
    return value;
  }

  @override
  Future<void> create(String vaultId) async {
    validateAtlasVaultAndroidVaultIdInternal(vaultId);
    await invokeAtlasVaultAndroidMethodInternal<void>(
      _channel,
      'createSelectedVault',
      <String, Object?>{'vault_id': vaultId},
    );
  }

  @override
  Future<void> clear(String expectedVaultId) async {
    validateAtlasVaultAndroidVaultIdInternal(expectedVaultId);
    await invokeAtlasVaultAndroidMethodInternal<void>(
      _channel,
      'clearSelectedVault',
      <String, Object?>{'expected_vault_id': expectedVaultId},
    );
  }

  @override
  String toString() => 'AtlasAndroidSelectedVaultStore(<redacted>)';
}

void _validateSha256(String value) {
  if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
    throw const AtlasVaultAndroidStorageException();
  }
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

void validateAtlasVaultAndroidVaultIdInternal(String value) {
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
    throw const AtlasVaultAndroidStorageException();
  }
}

Future<T?> invokeAtlasVaultAndroidMethodInternal<T>(
  MethodChannel channel,
  String method,
  Object? arguments,
) async {
  try {
    return await channel.invokeMethod<T>(method, arguments);
  } on AtlasVaultAndroidStorageException {
    rethrow;
  } catch (_) {
    throw const AtlasVaultAndroidStorageException();
  }
}

Uint8List copyAtlasVaultAndroidBytesInternal(Object value) {
  if (value is Uint8List) {
    return Uint8List.fromList(value);
  }
  if (value is List<int>) {
    return Uint8List.fromList(value);
  }
  throw const AtlasVaultAndroidStorageException();
}

void wipeAtlasVaultAndroidBytesInternal(Uint8List value) {
  value.fillRange(0, value.length, 0);
}
