import 'dart:io';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_apple.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

/// Production Dart adapters with an in-memory native-channel fake. This does
/// not prove Keychain persistence, entitlements, device lock, or key deletion.
final class RuntimeNativeHarness {
  RuntimeNativeHarness(Directory directory)
    : binding = AtlasVaultRuntimeBinding(
        root: directory,
        loadKey: AtlasAppleVaultSecureKeyStore().loadVaultKey,
        createKey: AtlasAppleVaultSecureKeyStore().createVaultKey,
      );

  final AtlasVaultRuntimeBinding binding;
  final keyStore = AtlasAppleVaultSecureKeyStore();
  final localStore = AtlasAppleVaultLocalStoreIO();
  final keys = <String, Uint8List>{};
  final stores = <String, Uint8List>{};
  final calls = <({String method, String id})>[];

  void install() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel(atlasVaultAppleMethodChannelName),
          (call) async {
            final args = Map<String, Object?>.from(call.arguments! as Map);
            final id = args['vault_id']! as String;
            calls.add((method: call.method, id: id));
            switch (call.method) {
              case 'loadVaultKey':
                return keys[id] == null ? null : Uint8List.fromList(keys[id]!);
              case 'containsVaultKey':
                return keys.containsKey(id);
              case 'createVaultKey':
                if (keys.containsKey(id)) {
                  throw PlatformException(code: 'EXISTS');
                }
                keys[id] = Uint8List.fromList(args['vault_key']! as Uint8List);
                return null;
              case 'deleteVaultKey':
                keys.remove(id)?.fillRange(0, 32, 0);
                return null;
              case 'readLocalStore':
                return stores[id] == null
                    ? null
                    : Uint8List.fromList(stores[id]!);
              case 'createLocalStore':
                if (stores.containsKey(id)) {
                  throw PlatformException(code: 'EXISTS');
                }
                stores[id] = Uint8List.fromList(
                  args['store_bytes']! as Uint8List,
                );
                return null;
              case 'replaceLocalStore':
                if (stores[id] == null ||
                    await atlasVaultSha256Hex(stores[id]!) !=
                        args['expected_sha256']) {
                  throw PlatformException(code: 'STALE');
                }
                stores[id] = Uint8List.fromList(
                  args['store_bytes']! as Uint8List,
                );
                return null;
              case 'deleteLocalStore':
                stores.remove(id);
                return null;
              default:
                throw PlatformException(code: 'UNEXPECTED');
            }
          },
        );
  }

  AtlasVaultPrivateStateRuntime runtime() => AtlasVaultPrivateStateRuntime(
    secureKeyStore: keyStore,
    localStoreIO: localStore,
    epochSessionFactory: binding.open,
  );

  void dispose() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel(atlasVaultAppleMethodChannelName),
          null,
        );
    for (final key in keys.values) {
      key.fillRange(0, key.length, 0);
    }
    keys.clear();
    stores.clear();
    calls.clear();
  }

  @override
  String toString() => 'RuntimeNativeHarness(<redacted>)';
}

AtlasVaultLocalStore runtimeEmptyLegacyStore(String vaultID) =>
    AtlasVaultLocalStore.fromJson({
      'format': 'atlasvault-local-store',
      'version': 1,
      'store_id': '20000000-0000-4000-8000-000000000001',
      'created_at': '2026-09-05T00:00:00Z',
      'updated_at': '2026-09-05T00:00:00Z',
      'vault_metadata': {
        'format': 'atlas-vault',
        'version': 1,
        'vault_id': vaultID,
        'crypto': {
          'record_aead': 'AES-256-GCM',
          'kdf': 'Argon2id',
          'subkey_kdf': 'HKDF-SHA256',
          'key_wrap_aead': 'AES-256-GCM',
        },
        'key_wraps': <Object?>[],
      },
      'records': <Object?>[],
    });
