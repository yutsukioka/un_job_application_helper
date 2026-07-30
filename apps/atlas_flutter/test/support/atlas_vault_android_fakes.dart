import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'dart:typed_data';

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

final class TestAtlasVaultPlaintextMigrationPrivateAuthority
    implements AtlasVaultPlaintextMigrationPrivateAuthority {
  TestAtlasVaultPlaintextMigrationPrivateAuthority({
    required this.events,
    required void Function() onHide,
    required Future<AtlasVaultPlaintextPrivateState> Function()
    readEncryptedState,
  }) : // Keep public fake labels readable at call sites.
       // ignore: prefer_initializing_formals
       _onHide = onHide,
       // ignore: prefer_initializing_formals
       _readEncryptedState = readEncryptedState;

  final List<String> events;
  final void Function() _onHide;
  final Future<AtlasVaultPlaintextPrivateState> Function() _readEncryptedState;

  @override
  bool isEncryptedPrivateStateActive = false;
  int activateCalls = 0;
  bool failAfterNextActivation = false;

  @override
  void hideLegacyPrivateState() {
    _onHide();
    events.add('private-authority.hide');
  }

  @override
  Future<bool> activateEncryptedPrivateState(String vaultId) async {
    activateCalls += 1;
    isEncryptedPrivateStateActive = true;
    events.add('private-authority.activate');
    if (failAfterNextActivation) {
      failAfterNextActivation = false;
      throw StateError('interrupted');
    }
    return true;
  }

  @override
  Future<AtlasVaultPlaintextPrivateState> readEncryptedPrivateState() {
    return _readEncryptedState();
  }

  @override
  String toString() =>
      'TestAtlasVaultPlaintextMigrationPrivateAuthority(<redacted>)';
}

final class InteropMemorySecureKeyStore implements AtlasVaultSecureKeyStore {
  InteropMemorySecureKeyStore({Uint8List? key, this.events})
    : _key = key == null ? null : Uint8List.fromList(key);

  Uint8List? _key;
  final List<String>? events;
  final List<String> calls = <String>[];

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    calls.add('key.create');
    events?.add('key.create');
    if (_key != null || vaultKey.length != 32) {
      throw const AtlasVaultAndroidStorageException();
    }
    _key = Uint8List.fromList(vaultKey);
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async {
    calls.add('key.load');
    events?.add('key.load');
    final key = _key;
    return key == null ? null : Uint8List.fromList(key);
  }

  @override
  Future<bool> containsVaultKey(String vaultId) async {
    calls.add('key.contains');
    events?.add('key.contains');
    return _key != null;
  }

  @override
  Future<void> deleteVaultKey(String vaultId) async {
    calls.add('key.delete');
    events?.add('key.delete');
    _key?.fillRange(0, _key!.length, 0);
    _key = null;
  }
}

final class InteropMemoryLocalStoreIO implements AtlasVaultLocalStoreIO {
  InteropMemoryLocalStoreIO({AtlasVaultLocalStore? store, this.events})
    : _store = store;

  AtlasVaultLocalStore? _store;
  final List<String>? events;
  final List<String> calls = <String>[];
  String? lastExpectedSha256;

  AtlasVaultLocalStore? get current => _store;

  @override
  Future<AtlasVaultLocalStore?> read(String vaultId) async {
    calls.add('store.read');
    events?.add('store.read');
    final store = _store;
    if (store == null) {
      return null;
    }
    return AtlasVaultLocalStore.fromJson(store.toJson());
  }

  @override
  Future<void> create(String vaultId, AtlasVaultLocalStore store) async {
    calls.add('store.create');
    events?.add('store.create');
    if (_store != null || store.vaultMetadata.vaultId != vaultId) {
      throw const AtlasVaultAndroidStorageException();
    }
    _store = AtlasVaultLocalStore.fromJson(store.toJson());
  }

  @override
  Future<void> replace(
    String vaultId,
    AtlasVaultLocalStore store, {
    required String expectedSha256,
  }) async {
    calls.add('store.replace');
    events?.add('store.replace');
    final current = _store;
    if (current == null ||
        current.vaultMetadata.vaultId != vaultId ||
        await atlasVaultSha256Hex(current.canonicalBytes()) != expectedSha256) {
      throw const AtlasVaultAndroidStorageException();
    }
    lastExpectedSha256 = expectedSha256;
    _store = AtlasVaultLocalStore.fromJson(store.toJson());
  }

  @override
  Future<void> delete(String vaultId) async {
    calls.add('store.delete');
    events?.add('store.delete');
    _store = null;
  }
}

final class InteropMemorySelectedVaultStore
    implements AtlasVaultSelectedVaultStore {
  InteropMemorySelectedVaultStore({String? value, this.events})
    : _value = value;

  String? _value;
  final List<String>? events;
  final List<String> calls = <String>[];

  @override
  Future<String?> read() async {
    calls.add('selection.read');
    events?.add('selection.read');
    return _value;
  }

  @override
  Future<void> create(String vaultId) async {
    calls.add('selection.create');
    events?.add('selection.create');
    if (_value != null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    _value = vaultId;
  }

  @override
  Future<void> clear(String expectedVaultId) async {
    calls.add('selection.clear');
    events?.add('selection.clear');
    if (_value != expectedVaultId) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    _value = null;
  }
}

final class InteropMemoryMigrationJournalStore
    implements AtlasVaultProtectedMigrationJournalStore {
  InteropMemoryMigrationJournalStore({Uint8List? bytes, this.events})
    : _bytes = bytes == null ? null : Uint8List.fromList(bytes);

  Uint8List? _bytes;
  final List<String>? events;
  final List<String> calls = <String>[];

  @override
  Future<Uint8List?> read() async {
    calls.add('migration-journal.read');
    events?.add('migration-journal.read');
    final bytes = _bytes;
    return bytes == null ? null : Uint8List.fromList(bytes);
  }

  @override
  Future<void> create(Uint8List canonicalBytes) async {
    calls.add('migration-journal.create');
    events?.add('migration-journal.create');
    if (_bytes != null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    _bytes = Uint8List.fromList(canonicalBytes);
  }

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) async {
    calls.add('migration-journal.replace');
    events?.add('migration-journal.replace');
    final current = _bytes;
    if (current == null ||
        await atlasVaultSha256Hex(current) != expectedSha256) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    _bytes = Uint8List.fromList(canonicalBytes);
  }

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) async {
    calls.add('migration-journal.delete');
    events?.add('migration-journal.delete');
    final current = _bytes;
    if (current == null) {
      if (allowAbsent) {
        return;
      }
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (await atlasVaultSha256Hex(current) != expectedSha256) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    _bytes = null;
  }
}
