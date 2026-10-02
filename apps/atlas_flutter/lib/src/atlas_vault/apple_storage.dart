import 'dart:convert';

import 'package:flutter/services.dart';

import '../../atlas_vault.dart';
import 'android_storage.dart' show AtlasVaultSecureKeyStore;
import 'local_store_io.dart';
import 'plaintext_migration.dart' show AtlasVaultSelectedVaultStore;

const atlasVaultAppleMethodChannelName = 'atlas/vault/apple';
const MethodChannel _defaultChannel = MethodChannel(
  atlasVaultAppleMethodChannelName,
);

final class AtlasVaultAppleStorageException implements Exception {
  const AtlasVaultAppleStorageException();

  @override
  String toString() => 'AtlasVault Apple storage operation failed.';
}

final class AtlasAppleVaultSecureKeyStore implements AtlasVaultSecureKeyStore {
  AtlasAppleVaultSecureKeyStore({MethodChannel? channel})
    : _channel = channel ?? _defaultChannel;

  final MethodChannel _channel;

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    _validateVaultId(vaultId);
    if (vaultKey.length != 32) throw const AtlasVaultAppleStorageException();
    final copy = Uint8List.fromList(vaultKey);
    try {
      await _invoke<void>(_channel, 'createVaultKey', <String, Object?>{
        'vault_id': vaultId,
        'vault_key': copy,
      });
    } finally {
      _wipe(copy);
    }
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async {
    _validateVaultId(vaultId);
    final value = await _invoke<Object?>(_channel, 'loadVaultKey', {
      'vault_id': vaultId,
    });
    if (value == null) return null;
    final bytes = _copyBytes(value);
    if (bytes.length != 32) {
      _wipe(bytes);
      throw const AtlasVaultAppleStorageException();
    }
    return bytes;
  }

  @override
  Future<bool> containsVaultKey(String vaultId) async {
    _validateVaultId(vaultId);
    final value = await _invoke<Object?>(_channel, 'containsVaultKey', {
      'vault_id': vaultId,
    });
    if (value is! bool) throw const AtlasVaultAppleStorageException();
    return value;
  }

  @override
  Future<void> deleteVaultKey(String vaultId) async {
    _validateVaultId(vaultId);
    await _invoke<void>(_channel, 'deleteVaultKey', {'vault_id': vaultId});
  }

  @override
  String toString() => 'AtlasAppleVaultSecureKeyStore(<redacted>)';
}

final class AtlasAppleVaultLocalStoreIO implements AtlasVaultLocalStoreIO {
  AtlasAppleVaultLocalStoreIO({MethodChannel? channel})
    : _channel = channel ?? _defaultChannel;

  static const maximumStoreByteCount = 128 * 1024 * 1024;
  final MethodChannel _channel;

  @override
  Future<AtlasVaultLocalStore?> read(String vaultId) async {
    _validateVaultId(vaultId);
    final value = await _invoke<Object?>(_channel, 'readLocalStore', {
      'vault_id': vaultId,
    });
    if (value == null) return null;
    final bytes = _copyBytes(value);
    try {
      _validateSize(bytes);
      final store = AtlasVaultLocalStore.decodeJson(
        utf8.decode(bytes, allowMalformed: false),
      );
      final canonical = store.canonicalBytes();
      try {
        if (!_constantTimeEquals(bytes, canonical) ||
            store.vaultMetadata.vaultId != vaultId) {
          throw const AtlasVaultAppleStorageException();
        }
      } finally {
        _wipe(canonical);
      }
      return store;
    } on AtlasVaultAppleStorageException {
      rethrow;
    } catch (_) {
      throw const AtlasVaultAppleStorageException();
    } finally {
      _wipe(bytes);
    }
  }

  @override
  Future<void> create(String vaultId, AtlasVaultLocalStore store) async {
    final bytes = _canonicalBytes(vaultId, store);
    try {
      await _invoke<void>(_channel, 'createLocalStore', {
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
      throw const AtlasVaultAppleStorageException();
    }
    final bytes = _canonicalBytes(vaultId, store);
    try {
      await _invoke<void>(_channel, 'replaceLocalStore', {
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
    await _invoke<void>(_channel, 'deleteLocalStore', {'vault_id': vaultId});
  }

  Uint8List _canonicalBytes(String vaultId, AtlasVaultLocalStore store) {
    _validateVaultId(vaultId);
    if (store.vaultMetadata.vaultId != vaultId) {
      throw const AtlasVaultAppleStorageException();
    }
    final bytes = store.canonicalBytes();
    try {
      _validateSize(bytes);
      return bytes;
    } catch (_) {
      _wipe(bytes);
      rethrow;
    }
  }

  static void _validateSize(Uint8List bytes) {
    if (bytes.isEmpty || bytes.length > maximumStoreByteCount) {
      throw const AtlasVaultAppleStorageException();
    }
  }

  @override
  String toString() => 'AtlasAppleVaultLocalStoreIO(<redacted>)';
}

final class AtlasAppleSelectedVaultStore
    implements AtlasVaultSelectedVaultStore {
  AtlasAppleSelectedVaultStore({MethodChannel? channel})
    : _channel = channel ?? _defaultChannel;

  final MethodChannel _channel;

  @override
  Future<String?> read() async {
    final value = await _invoke<Object?>(_channel, 'readSelectedVault', null);
    if (value == null) return null;
    if (value is! String) throw const AtlasVaultAppleStorageException();
    _validateVaultId(value);
    return value;
  }

  @override
  Future<void> create(String vaultId) async {
    _validateVaultId(vaultId);
    await _invoke<void>(_channel, 'createSelectedVault', {'vault_id': vaultId});
  }

  @override
  Future<void> clear(String expectedVaultId) async {
    _validateVaultId(expectedVaultId);
    await _invoke<void>(_channel, 'clearSelectedVault', {
      'expected_vault_id': expectedVaultId,
    });
  }

  @override
  String toString() => 'AtlasAppleSelectedVaultStore(<redacted>)';
}

Future<T?> _invoke<T>(
  MethodChannel channel,
  String method,
  Object? arguments,
) async {
  try {
    return await channel.invokeMethod<T>(method, arguments);
  } catch (_) {
    throw const AtlasVaultAppleStorageException();
  }
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
    throw const AtlasVaultAppleStorageException();
  }
}

Uint8List _copyBytes(Object? value) {
  if (value is Uint8List) return Uint8List.fromList(value);
  if (value is ByteData) return Uint8List.fromList(value.buffer.asUint8List());
  if (value is List<int>) return Uint8List.fromList(value);
  throw const AtlasVaultAppleStorageException();
}

bool _constantTimeEquals(Uint8List left, Uint8List right) {
  var difference = left.length ^ right.length;
  final count = left.length < right.length ? left.length : right.length;
  for (var index = 0; index < count; index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference == 0;
}

void _wipe(Uint8List value) => value.fillRange(0, value.length, 0);
