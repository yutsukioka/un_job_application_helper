import 'dart:convert';

import 'package:flutter/services.dart';

import '../../atlas_vault.dart';
import 'android_storage.dart';

abstract interface class AtlasVaultLocalStoreIO {
  Future<AtlasVaultLocalStore?> read(String vaultId);

  Future<void> create(String vaultId, AtlasVaultLocalStore store);

  Future<void> replace(
    String vaultId,
    AtlasVaultLocalStore store, {
    required String expectedSha256,
  });

  Future<void> delete(String vaultId);
}

final class AtlasAndroidVaultLocalStoreIO implements AtlasVaultLocalStoreIO {
  AtlasAndroidVaultLocalStoreIO({MethodChannel? channel})
    : _channel =
          channel ?? const MethodChannel(atlasVaultAndroidMethodChannelName);

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
