import 'dart:convert';

import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_android_fakes.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AtlasVaultAndroidMethodCallRecorder recorder;

  setUp(() {
    recorder = AtlasVaultAndroidMethodCallRecorder(
      channelName: atlasVaultAndroidMethodChannelName,
    )..install();
  });

  tearDown(() {
    recorder.uninstall();
  });

  test('construction performs no platform call', () {
    AtlasAndroidVaultSecureKeyStore(channel: recorder.channel);
    AtlasAndroidEncryptedDocumentTransport(channel: recorder.channel);

    expect(recorder.calls, isEmpty);
  });

  test('encrypted-document save is explicit, bounded, and path-free', () async {
    final bytes = Uint8List.fromList(utf8.encode('{"encrypted":true}'));
    recorder.handler = (call) async {
      expect(call.method, 'saveEncryptedExport');
      expect(call.arguments, <String, Object?>{'export_bytes': bytes});
      return true;
    };
    final transport = AtlasAndroidEncryptedDocumentTransport(
      channel: recorder.channel,
    );

    expect(await transport.saveEncryptedExport(bytes), isTrue);

    expect(recorder.calls, hasLength(1));
    expect(recorder.calls.single.arguments.toString(), isNot(contains('path')));
    expect(recorder.calls.single.arguments.toString(), isNot(contains('uri')));
  });

  test('encrypted-document save cancellation is fixed', () async {
    recorder.handler = (call) async {
      expect(call.method, 'saveEncryptedExport');
      return false;
    };
    final transport = AtlasAndroidEncryptedDocumentTransport(
      channel: recorder.channel,
    );

    expect(
      await transport.saveEncryptedExport(
        Uint8List.fromList(utf8.encode('{"encrypted":true}')),
      ),
      isFalse,
    );
  });

  test(
    'encrypted-document picker is explicit, bounded, and path-free',
    () async {
      final bytes = Uint8List.fromList(utf8.encode('{"encrypted":true}'));
      recorder.handler = (call) async {
        expect(call.method, 'pickEncryptedExport');
        expect(call.arguments, isNull);
        return bytes;
      };
      final transport = AtlasAndroidEncryptedDocumentTransport(
        channel: recorder.channel,
      );

      final picked = await transport.pickEncryptedExport();

      expect(picked, orderedEquals(bytes));
      expect(identical(picked, bytes), isFalse);
      expect(recorder.calls, hasLength(1));
      expect(recorder.calls.single.toString(), isNot(contains('content://')));
      expect(recorder.calls.single.toString(), isNot(contains('path')));
    },
  );

  test('protected recovery-import journal uses exact CAS methods', () async {
    final journal = AtlasAndroidProtectedRecoveryImportJournalStore(
      channel: recorder.channel,
    );
    final bytes = Uint8List.fromList(
      utf8.encode(
        '{"format":"atlasvault-android-recovery-import","version":1}',
      ),
    );
    recorder.handler = (call) async {
      switch (call.method) {
        case 'readRecoveryImportJournal':
          expect(call.arguments, isNull);
          return bytes;
        case 'createRecoveryImportJournal':
          expect(call.arguments, <String, Object?>{'journal_bytes': bytes});
          return null;
        case 'replaceRecoveryImportJournal':
          expect(call.arguments, <String, Object?>{
            'journal_bytes': bytes,
            'expected_sha256': 'a' * 64,
          });
          return null;
        case 'deleteRecoveryImportJournal':
          expect(call.arguments, <String, Object?>{
            'expected_sha256': 'b' * 64,
            'allow_absent': false,
          });
          return null;
      }
      fail('Unexpected recovery-import journal method.');
    };

    expect(await journal.read(), orderedEquals(bytes));
    await journal.create(bytes);
    await journal.replace(bytes, expectedSha256: 'a' * 64);
    await journal.delete(expectedSha256: 'b' * 64);

    expect(recorder.calls.map((call) => call.method), <String>[
      'readRecoveryImportJournal',
      'createRecoveryImportJournal',
      'replaceRecoveryImportJournal',
      'deleteRecoveryImportJournal',
    ]);
  });

  test('invalid encrypted-document sizes make no platform call', () async {
    final transport = AtlasAndroidEncryptedDocumentTransport(
      channel: recorder.channel,
    );

    await expectLater(
      transport.saveEncryptedExport(Uint8List(0)),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );
    await expectLater(
      transport.saveEncryptedExport(
        Uint8List(
          AtlasAndroidEncryptedDocumentTransport.maximumDocumentByteCount + 1,
        ),
      ),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );

    expect(recorder.calls, isEmpty);
  });

  test('capabilities use the exact method and fixed response keys', () async {
    recorder.handler = (call) async {
      expect(call.method, 'capabilities');
      expect(call.arguments, isNull);
      return <String, Object?>{
        'api_level': 35,
        'secure_boundary_available': true,
        'aes_gcm_keystore_available': true,
        'hardware_backed': false,
        'strongbox_backed': false,
        'no_backup_storage_available': true,
      };
    };
    final storage = AtlasAndroidVaultSecureKeyStore(channel: recorder.channel);

    final capabilities = await storage.capabilities();

    expect(capabilities.apiLevel, 35);
    expect(capabilities.secureBoundaryAvailable, isTrue);
    expect(capabilities.aesGcmKeystoreAvailable, isTrue);
    expect(capabilities.hardwareBacked, isFalse);
    expect(capabilities.strongBoxBacked, isFalse);
    expect(capabilities.noBackupStorageAvailable, isTrue);
  });

  test('invalid vault IDs cause no platform call', () async {
    final storage = AtlasAndroidVaultSecureKeyStore(channel: recorder.channel);

    for (final vaultId in <String>[
      '',
      '.',
      '..',
      'contains space',
      'contains/slash',
      'saved_search',
      'SAVED_JOB',
      'application_note',
      'profile_snippet',
      'draft_metadata',
      'x' * 97,
    ]) {
      await expectLater(
        storage.containsVaultKey(vaultId),
        throwsA(isA<AtlasVaultAndroidStorageException>()),
      );
    }

    expect(recorder.calls, isEmpty);
  });

  test('create validates a 32-byte key and uses create-only method', () async {
    final storage = AtlasAndroidVaultSecureKeyStore(channel: recorder.channel);
    final key = deterministicVaultKey();
    recorder.handler = (call) async {
      expect(call.method, 'createVaultKey');
      final arguments = call.arguments! as Map<Object?, Object?>;
      expect(arguments.keys, <Object?>['vault_id', 'vault_key']);
      expect(arguments['vault_id'], 'vault-alpha');
      expect(arguments['vault_key'], orderedEquals(key));
      return null;
    };

    await storage.createVaultKey('vault-alpha', key);

    expect(recorder.calls.map((call) => call.method), <String>[
      'createVaultKey',
    ]);
  });

  test('invalid key length is rejected before the platform call', () async {
    final storage = AtlasAndroidVaultSecureKeyStore(channel: recorder.channel);

    await expectLater(
      storage.createVaultKey('vault-alpha', Uint8List(31)),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );

    expect(recorder.calls, isEmpty);
  });

  test('load returns a defensive copy and uses exact arguments', () async {
    final platformKey = deterministicVaultKey();
    recorder.handler = (call) async {
      expect(call.method, 'loadVaultKey');
      expect(call.arguments, <String, Object?>{'vault_id': 'vault-alpha'});
      return platformKey;
    };
    final storage = AtlasAndroidVaultSecureKeyStore(channel: recorder.channel);

    final loaded = await storage.loadVaultKey('vault-alpha');

    expect(loaded, orderedEquals(platformKey));
    expect(identical(loaded, platformKey), isFalse);
    loaded![0] ^= 0xff;
    expect(platformKey.first, 1);
  });

  test(
    'contains and delete use exact methods and delete is repeatable',
    () async {
      recorder.handler = (call) async {
        if (call.method == 'containsVaultKey') {
          return true;
        }
        return null;
      };
      final storage = AtlasAndroidVaultSecureKeyStore(
        channel: recorder.channel,
      );

      expect(await storage.containsVaultKey('vault-alpha'), isTrue);
      await storage.deleteVaultKey('vault-alpha');
      await storage.deleteVaultKey('vault-alpha');

      expect(recorder.calls.map((call) => call.method), <String>[
        'containsVaultKey',
        'deleteVaultKey',
        'deleteVaultKey',
      ]);
    },
  );

  test('platform errors are fixed and redact platform details', () async {
    recorder.handler = (_) async {
      throw PlatformException(
        code: 'native-secret',
        message: '/private/path/FAKE_PRIVATE_VALUE',
      );
    };
    final storage = AtlasAndroidVaultSecureKeyStore(channel: recorder.channel);

    Object? failure;
    try {
      await storage.loadVaultKey('vault-alpha');
    } catch (error) {
      failure = error;
    }

    expect(failure, isA<AtlasVaultAndroidStorageException>());
    expect(failure.toString(), 'AtlasVault Android storage operation failed.');
    expect(failure.toString(), isNot(contains('native-secret')));
    expect(failure.toString(), isNot(contains('/private/path')));
  });

  test('protected migration journal uses exact CAS channel methods', () async {
    final journal = AtlasAndroidProtectedMigrationJournalStore(
      channel: recorder.channel,
    );
    final bytes = Uint8List.fromList(utf8.encode('{"canonical":true}'));
    recorder.handler = (call) async {
      switch (call.method) {
        case 'readPlaintextMigrationJournal':
          expect(call.arguments, isNull);
          return bytes;
        case 'createPlaintextMigrationJournal':
          expect(call.arguments, <String, Object?>{'journal_bytes': bytes});
          return null;
        case 'replacePlaintextMigrationJournal':
          expect(call.arguments, <String, Object?>{
            'journal_bytes': bytes,
            'expected_sha256': 'a' * 64,
          });
          return null;
        case 'deletePlaintextMigrationJournal':
          expect(call.arguments, <String, Object?>{
            'expected_sha256': 'b' * 64,
            'allow_absent': false,
          });
          return null;
      }
      fail('Unexpected migration journal method.');
    };

    final restored = await journal.read();
    expect(restored, orderedEquals(bytes));
    expect(identical(restored, bytes), isFalse);
    await journal.create(bytes);
    await journal.replace(bytes, expectedSha256: 'a' * 64);
    await journal.delete(expectedSha256: 'b' * 64);

    expect(recorder.calls.map((call) => call.method), <String>[
      'readPlaintextMigrationJournal',
      'createPlaintextMigrationJournal',
      'replacePlaintextMigrationJournal',
      'deletePlaintextMigrationJournal',
    ]);
  });

  test(
    'selected vault registry is create-only and expected-ID cleared',
    () async {
      final selection = AtlasAndroidSelectedVaultStore(
        channel: recorder.channel,
      );
      recorder.handler = (call) async {
        switch (call.method) {
          case 'readSelectedVault':
            expect(call.arguments, isNull);
            return 'vault-alpha';
          case 'createSelectedVault':
            expect(call.arguments, <String, Object?>{
              'vault_id': 'vault-alpha',
            });
            return null;
          case 'clearSelectedVault':
            expect(call.arguments, <String, Object?>{
              'expected_vault_id': 'vault-alpha',
            });
            return null;
        }
        fail('Unexpected selected-vault method.');
      };

      expect(await selection.read(), 'vault-alpha');
      await selection.create('vault-alpha');
      await selection.clear('vault-alpha');

      expect(recorder.calls.map((call) => call.method), <String>[
        'readSelectedVault',
        'createSelectedVault',
        'clearSelectedVault',
      ]);
    },
  );
}
