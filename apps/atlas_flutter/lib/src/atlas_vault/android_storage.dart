import 'package:flutter/services.dart';

const String atlasVaultAndroidMethodChannelName = 'atlas/vault_android';

const MethodChannel _defaultAtlasVaultAndroidChannel = MethodChannel(
  atlasVaultAndroidMethodChannelName,
);

final class AtlasVaultAndroidStorageException implements Exception {
  const AtlasVaultAndroidStorageException();

  @override
  String toString() => 'AtlasVault Android storage operation failed.';
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

abstract interface class AtlasVaultSecureKeyStore {
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey);

  Future<Uint8List?> loadVaultKey(String vaultId);

  Future<bool> containsVaultKey(String vaultId);

  Future<void> deleteVaultKey(String vaultId);
}

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
