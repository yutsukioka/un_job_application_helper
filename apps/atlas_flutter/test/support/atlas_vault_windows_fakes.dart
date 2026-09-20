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
  Uint8List? _migrationJournalBytes;
  Uint8List? _recoveryImportJournalBytes;
  String? _selectedVaultId;
  Uint8List? pickedEncryptedExportBytes;

  List<MethodCall> get calls => List<MethodCall>.unmodifiable(recorder.calls);

  Uint8List? get localStoreBytes =>
      _localStoreBytes == null ? null : Uint8List.fromList(_localStoreBytes!);

  Uint8List? get migrationJournalBytes => _migrationJournalBytes == null
      ? null
      : Uint8List.fromList(_migrationJournalBytes!);

  Uint8List? get recoveryImportJournalBytes =>
      _recoveryImportJournalBytes == null
      ? null
      : Uint8List.fromList(_recoveryImportJournalBytes!);

  String? get selectedVaultId => _selectedVaultId;

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
    _migrationJournalBytes?.fillRange(0, _migrationJournalBytes!.length, 0);
    _migrationJournalBytes = null;
    _recoveryImportJournalBytes?.fillRange(
      0,
      _recoveryImportJournalBytes!.length,
      0,
    );
    _recoveryImportJournalBytes = null;
    pickedEncryptedExportBytes?.fillRange(
      0,
      pickedEncryptedExportBytes!.length,
      0,
    );
    pickedEncryptedExportBytes = null;
    _selectedVaultId = null;
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
      case 'readPlaintextMigrationJournal':
        return _migrationJournalBytes == null
            ? null
            : Uint8List.fromList(_migrationJournalBytes!);
      case 'createPlaintextMigrationJournal':
        if (_migrationJournalBytes != null) {
          throw PlatformException(code: 'already_exists');
        }
        _migrationJournalBytes = Uint8List.fromList(
          arguments['journal_bytes']! as Uint8List,
        );
        return null;
      case 'replacePlaintextMigrationJournal':
        final current = _migrationJournalBytes;
        if (current == null ||
            arguments['expected_sha256'] !=
                await atlasVaultSha256Hex(current)) {
          throw PlatformException(code: 'stale_digest');
        }
        _migrationJournalBytes = Uint8List.fromList(
          arguments['journal_bytes']! as Uint8List,
        );
        return null;
      case 'deletePlaintextMigrationJournal':
        final current = _migrationJournalBytes;
        if (current == null) {
          if (arguments['allow_absent'] == true) {
            return null;
          }
          throw PlatformException(code: 'storage_failed');
        }
        if (arguments['expected_sha256'] !=
            await atlasVaultSha256Hex(current)) {
          throw PlatformException(code: 'stale_digest');
        }
        current.fillRange(0, current.length, 0);
        _migrationJournalBytes = null;
        return null;
      case 'readRecoveryImportJournal':
        return _recoveryImportJournalBytes == null
            ? null
            : Uint8List.fromList(_recoveryImportJournalBytes!);
      case 'createRecoveryImportJournal':
        if (_recoveryImportJournalBytes != null) {
          throw PlatformException(code: 'already_exists');
        }
        _recoveryImportJournalBytes = Uint8List.fromList(
          arguments['journal_bytes']! as Uint8List,
        );
        return null;
      case 'replaceRecoveryImportJournal':
        final current = _recoveryImportJournalBytes;
        if (current == null ||
            arguments['expected_sha256'] !=
                await atlasVaultSha256Hex(current)) {
          throw PlatformException(code: 'stale_digest');
        }
        _recoveryImportJournalBytes = Uint8List.fromList(
          arguments['journal_bytes']! as Uint8List,
        );
        return null;
      case 'deleteRecoveryImportJournal':
        final current = _recoveryImportJournalBytes;
        if (current == null) {
          if (arguments['allow_absent'] == true) {
            return null;
          }
          throw PlatformException(code: 'storage_failed');
        }
        if (arguments['expected_sha256'] !=
            await atlasVaultSha256Hex(current)) {
          throw PlatformException(code: 'stale_digest');
        }
        current.fillRange(0, current.length, 0);
        _recoveryImportJournalBytes = null;
        return null;
      case 'pickEncryptedExport':
        return pickedEncryptedExportBytes == null
            ? null
            : Uint8List.fromList(pickedEncryptedExportBytes!);
      case 'readSelectedVault':
        return _selectedVaultId;
      case 'createSelectedVault':
        if (_selectedVaultId != null) {
          throw PlatformException(code: 'already_exists');
        }
        _selectedVaultId = arguments['vault_id']! as String;
        return null;
      case 'clearSelectedVault':
        if (_selectedVaultId != arguments['expected_vault_id']) {
          throw PlatformException(code: 'storage_failed');
        }
        _selectedVaultId = null;
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
