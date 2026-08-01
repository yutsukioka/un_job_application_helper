import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

final class AtlasVaultWindowsMethodCallRecorder {
  AtlasVaultWindowsMethodCallRecorder({required String channelName})
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

final class FakeAtlasVaultWindowsPlatform {
  FakeAtlasVaultWindowsPlatform({
    String channelName = atlasVaultWindowsMethodChannelName,
  }) : recorder = AtlasVaultWindowsMethodCallRecorder(channelName: channelName);

  final AtlasVaultWindowsMethodCallRecorder recorder;
  Uint8List? _vaultKey;
  Uint8List? _localStoreBytes;

  List<MethodCall> get calls => List<MethodCall>.unmodifiable(recorder.calls);

  Uint8List? get localStoreBytes =>
      _localStoreBytes == null ? null : Uint8List.fromList(_localStoreBytes!);

  void seedVaultKey(Uint8List value) {
    _vaultKey = Uint8List.fromList(value);
  }

  void seedLocalStore(AtlasVaultLocalStore value) {
    _localStoreBytes = Uint8List.fromList(value.canonicalBytes());
  }

  void resetCalls() => recorder.calls.clear();

  void install() {
    recorder.handler = _handle;
    recorder.install();
  }

  void uninstall() {
    recorder.uninstall();
    _vaultKey?.fillRange(0, _vaultKey!.length, 0);
    _vaultKey = null;
    _localStoreBytes = null;
  }

  Future<Object?> _handle(MethodCall call) async {
    final arguments = call.arguments == null
        ? const <Object?, Object?>{}
        : Map<Object?, Object?>.from(call.arguments! as Map);
    switch (call.method) {
      case 'capabilities':
        return <String, Object?>{
          'secure_boundary_available': true,
          'dpapi_available': true,
          'current_user_scope': true,
          'local_app_data_available': true,
          'atomic_replace_available': true,
          'hardware_backed_guaranteed': false,
        };
      case 'createVaultKey':
        if (_vaultKey != null) {
          throw PlatformException(code: 'already_exists');
        }
        _vaultKey = Uint8List.fromList(arguments['vault_key']! as Uint8List);
        return null;
      case 'loadVaultKey':
        return _vaultKey == null ? null : Uint8List.fromList(_vaultKey!);
      case 'containsVaultKey':
        return _vaultKey != null;
      case 'deleteVaultKey':
        _vaultKey?.fillRange(0, _vaultKey!.length, 0);
        _vaultKey = null;
        return null;
      case 'readLocalStore':
        return _localStoreBytes == null
            ? null
            : Uint8List.fromList(_localStoreBytes!);
      case 'createLocalStore':
        if (_localStoreBytes != null) {
          throw PlatformException(code: 'already_exists');
        }
        _localStoreBytes = Uint8List.fromList(
          arguments['store_bytes']! as Uint8List,
        );
        return null;
      case 'replaceLocalStore':
        final current = _localStoreBytes;
        if (current == null) {
          throw PlatformException(code: 'storage_failed');
        }
        if (arguments['expected_sha256'] !=
            await atlasVaultSha256Hex(current)) {
          throw PlatformException(code: 'stale_digest');
        }
        _localStoreBytes = Uint8List.fromList(
          arguments['store_bytes']! as Uint8List,
        );
        return null;
      case 'deleteLocalStore':
        _localStoreBytes = null;
        return null;
      default:
        throw PlatformException(code: 'not_implemented');
    }
  }
}

AtlasVaultLocalStore testWindowsAtlasVaultLocalStore(
  String vaultId, {
  String updatedAt = '2026-07-31T00:00:00Z',
  List<AtlasVaultEncryptedRecord> records = const <AtlasVaultEncryptedRecord>[],
}) {
  return AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': 'atlasvault-local-store',
    'version': 1,
    'store_id': '77777777-8888-4999-8aaa-bbbbbbbbbbbb',
    'created_at': '2026-07-31T00:00:00Z',
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

Uint8List deterministicWindowsVaultKey() {
  return Uint8List.fromList(
    List<int>.generate(32, (index) => (index + 73) & 0xff),
  );
}
