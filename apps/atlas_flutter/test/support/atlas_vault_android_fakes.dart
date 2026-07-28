import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

final class AtlasVaultAndroidMethodCallRecorder {
  AtlasVaultAndroidMethodCallRecorder({required String channelName})
    : channel = MethodChannel(channelName);

  final MethodChannel channel;
  final List<MethodCall> calls = <MethodCall>[];
  Future<Object?> Function(MethodCall call)? handler;

  void install() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          calls.add(call);
          return handler?.call(call);
        });
  }

  void uninstall() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  }
}

AtlasVaultLocalStore testAtlasVaultLocalStore(
  String vaultId, {
  String updatedAt = '2026-07-28T00:00:00Z',
  List<AtlasVaultEncryptedRecord> records = const <AtlasVaultEncryptedRecord>[],
}) {
  return AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': 'atlasvault-local-store',
    'version': 1,
    'store_id': '99999999-8888-4777-8666-555555555555',
    'created_at': '2026-07-28T00:00:00Z',
    'updated_at': updatedAt,
    'vault_metadata': <String, Object?>{
      'format': 'atlas-vault',
      'version': 1,
      'vault_id': vaultId,
      'crypto': <String, Object?>{
        'record_aead': 'AES-256-GCM',
        'kdf': 'Argon2id',
        'subkey_kdf': 'HKDF-SHA256',
        'key_wrap_aead': 'AES-256-GCM',
      },
      'key_wraps': <Object?>[],
    },
    'records': <Object?>[for (final record in records) record.toJson()],
  });
}

Uint8List deterministicVaultKey() {
  return Uint8List.fromList(
    List<int>.generate(32, (index) => (index + 1) & 0xff),
  );
}
