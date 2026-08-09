import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('runner owns the dedicated Windows AtlasVault bridge', () {
    final header = File('windows/runner/flutter_window.h').readAsStringSync();
    final source = File('windows/runner/flutter_window.cpp').readAsStringSync();

    expect(header, contains('AtlasVaultWindowsStorage'));
    expect(header, contains('atlas_vault_storage_'));
    expect(source, contains('atlas/vault_windows'));
    expect(source, contains('RegisterPlugins'));
    expect(
      source.indexOf('RegisterPlugins'),
      lessThan(source.indexOf('atlas_vault_storage_')),
    );
    expect(source, contains('atlas_vault_storage_.reset()'));
    expect(
      source.indexOf('atlas_vault_storage_.reset()'),
      lessThan(source.indexOf('flutter_controller_ = nullptr')),
    );
  });

  test('native bridge enforces current-user DPAPI and key verification', () {
    final source = File(
      'windows/runner/atlas_vault_windows_storage.cpp',
    ).readAsStringSync();

    for (final required in <String>[
      'CryptProtectData',
      'CryptUnprotectData',
      'CRYPTPROTECT_UI_FORBIDDEN',
      'SecureZeroMemory',
      'LocalFree',
      'BCryptOpenAlgorithmProvider',
      'BCRYPT_SHA256_ALGORITHM',
      'atlasvault-windows-dpapi-v1:',
      'UNApplications:',
      'AtlasVault:',
      'AVWKEY01',
      'ConstantTimeEquals',
      'std::holds_alternative<std::monostate>',
    ]) {
      expect(source, contains(required));
    }
    expect(source, isNot(contains('CRYPTPROTECT_LOCAL_MACHINE')));
  });

  test('capabilities are derived from real storage probes', () {
    final source = File(
      'windows/runner/atlas_vault_windows_storage.cpp',
    ).readAsStringSync();

    for (final required in <String>[
      'ProbeStorageCapabilities',
      'ProbeCurrentUserDpapi',
      'ProbeAtomicReplacement',
    ]) {
      expect(source, contains(required));
    }
    final capabilitiesStart = source.indexOf('if (method == "capabilities")');
    final recognizedStart = source.indexOf(
      'const bool recognized',
      capabilitiesStart,
    );
    expect(capabilitiesStart, isNonNegative);
    expect(recognizedStart, greaterThan(capabilitiesStart));
    final handler = source.substring(capabilitiesStart, recognizedStart);
    expect(handler, contains('ProbeStorageCapabilities()'));
    expect(handler, isNot(contains('EncodableValue(true)')));
  });

  test('native bridge owns Local AppData paths and file safety', () {
    final source = File(
      'windows/runner/atlas_vault_windows_storage.cpp',
    ).readAsStringSync();

    for (final required in <String>[
      'SHGetKnownFolderPath',
      'FOLDERID_LocalAppData',
      'LockFileEx',
      'LOCKFILE_FAIL_IMMEDIATELY',
      'UnlockFileEx',
      'FlushFileBuffers',
      'MoveFileExW',
      'MOVEFILE_WRITE_THROUGH',
      'MOVEFILE_REPLACE_EXISTING',
      'FILE_FLAG_OPEN_REPARSE_POINT',
      'BCryptGenRandom',
      'CREATE_NEW',
      'OPEN_ALWAYS',
    ]) {
      expect(source, contains(required));
    }
    for (final forbidden in <String>[
      'FOLDERID_RoamingAppData',
      'RegSetValue',
      'CredentialWrite',
      'SharedPreferences',
      'std::ofstream',
      'OutputDebugString',
      'std::cout',
      'std::cerr',
    ]) {
      expect(source, isNot(contains(forbidden)));
    }
  });

  test('native storage operations run on a retained serial worker', () {
    final header = File(
      'windows/runner/atlas_vault_windows_storage.h',
    ).readAsStringSync();
    final source = File(
      'windows/runner/atlas_vault_windows_storage.cpp',
    ).readAsStringSync();

    for (final required in <String>[
      'AtlasVaultWindowsStorageWorker',
      'ExecuteMethodCall',
      'worker_->Enqueue',
      'worker_->StopAndDrain',
      'std::thread',
      'std::condition_variable',
      'std::deque',
    ]) {
      expect('$header\n$source', contains(required));
    }

    final handlerStart = source.indexOf(
      'void AtlasVaultWindowsStorage::HandleMethodCall',
    );
    final executorStart = source.indexOf(
      'void AtlasVaultWindowsStorage::ExecuteMethodCall',
    );
    expect(handlerStart, isNonNegative);
    expect(executorStart, greaterThan(handlerStart));
    final handler = source.substring(handlerStart, executorStart);
    expect(handler, contains('worker_->Enqueue'));
    expect(handler, isNot(contains('BeginOperation')));
    expect(handler, isNot(contains('ProbeStorageCapabilities')));
    expect(handler, isNot(contains('AtomicReplace')));

    final destructorStart = source.indexOf(
      'AtlasVaultWindowsStorage::~AtlasVaultWindowsStorage',
    );
    expect(destructorStart, isNonNegative);
    final destructor = source.substring(destructorStart, handlerStart);
    expect(
      destructor.indexOf('SetMethodCallHandler(nullptr)'),
      lessThan(destructor.indexOf('worker_->StopAndDrain')),
    );
  });

  test('native encrypted save dialog is owned, atomic, and path-free', () {
    final runner = File('windows/runner/flutter_window.cpp').readAsStringSync();
    final header = File(
      'windows/runner/atlas_vault_windows_storage.h',
    ).readAsStringSync();
    final source = File(
      'windows/runner/atlas_vault_windows_storage.cpp',
    ).readAsStringSync();
    final combined = '$header\n$source';

    expect(runner, contains('GetHandle()'));
    expect(combined, contains('HWND'));
    for (final token in <String>[
      'saveEncryptedExport',
      'IFileSaveDialog',
      'FOS_FORCEFILESYSTEM',
      'FOS_PATHMUSTEXIST',
      'FOS_DONTADDTORECENT',
      'FOS_NOCHANGEDIR',
      'FOS_OVERWRITEPROMPT',
      'AtlasVault-Encrypted-Backup.atlasvault',
      'FlushFileBuffers',
      'MoveFileExW',
      'MOVEFILE_REPLACE_EXISTING',
      'MOVEFILE_WRITE_THROUGH',
    ]) {
      expect(combined, contains(token), reason: token);
    }
    expect(combined, isNot(contains('SHAddToRecentDocs')));
    expect(combined, isNot(contains('SIGDN_FILESYSPATH.*result')));
  });

  test('native encrypted picker is owned, bounded, and path-free', () {
    final header = File(
      'windows/runner/atlas_vault_windows_storage.h',
    ).readAsStringSync();
    final source = File(
      'windows/runner/atlas_vault_windows_storage.cpp',
    ).readAsStringSync();
    final combined = '$header\n$source';

    for (final token in <String>[
      'pickEncryptedExport',
      'IFileOpenDialog',
      'FOS_FORCEFILESYSTEM',
      'FOS_FILEMUSTEXIST',
      'FOS_PATHMUSTEXIST',
      'FOS_DONTADDTORECENT',
      'FOS_NOCHANGEDIR',
      'FILE_FLAG_OPEN_REPARSE_POINT',
    ]) {
      expect(combined, contains(token), reason: token);
    }
    expect(combined, isNot(contains('SHAddToRecentDocs')));
  });

  test('native recovery-import journal is current-user protected', () {
    final source = File(
      'windows/runner/atlas_vault_windows_storage.cpp',
    ).readAsStringSync();

    for (final token in <String>[
      'recovery-import',
      'AVWBLB01',
      'readRecoveryImportJournal',
      'createRecoveryImportJournal',
      'replaceRecoveryImportJournal',
      'deleteRecoveryImportJournal',
      'CryptProtectData',
      'CryptUnprotectData',
      'CRYPTPROTECT_UI_FORBIDDEN',
    ]) {
      expect(source, contains(token), reason: token);
    }
    expect(source, isNot(contains('CRYPTPROTECT_LOCAL_MACHINE')));
  });

  test('runner build links only the required Windows libraries', () {
    final cmake = File('windows/runner/CMakeLists.txt').readAsStringSync();

    expect(cmake, contains('atlas_vault_windows_storage.cpp'));
    for (final library in <String>[
      'crypt32.lib',
      'bcrypt.lib',
      'shell32.lib',
      'ole32.lib',
    ]) {
      expect(cmake, contains(library));
    }
  });

  test('generated plugin files remain generated and storage-free', () {
    final registrant = File(
      'windows/flutter/generated_plugin_registrant.cc',
    ).readAsStringSync();
    final plugins = File(
      'windows/flutter/generated_plugins.cmake',
    ).readAsStringSync();

    expect(registrant, contains('Generated file. Do not edit.'));
    expect(registrant, isNot(contains('AtlasVaultWindowsStorage')));
    expect(plugins, isNot(contains('atlas_vault_windows_storage')));
  });
}
