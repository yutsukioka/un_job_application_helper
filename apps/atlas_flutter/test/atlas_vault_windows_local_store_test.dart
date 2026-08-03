import 'dart:convert';

import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_windows_fakes.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AtlasVaultWindowsMethodCallRecorder recorder;
  late AtlasWindowsVaultLocalStoreIO storage;

  setUp(() {
    recorder = AtlasVaultWindowsMethodCallRecorder(
      channelName: atlasVaultWindowsMethodChannelName,
    )..install();
    storage = AtlasWindowsVaultLocalStoreIO(channel: recorder.channel);
  });

  tearDown(() {
    recorder.uninstall();
  });

  test('construction performs no platform call', () {
    expect(recorder.calls, isEmpty);
  });

  test(
    'read strictly accepts canonical bytes for the requested vault',
    () async {
      final expected = testWindowsAtlasVaultLocalStore('vault-alpha');
      recorder.handler = (call) async {
        expect(call.method, 'readLocalStore');
        expect(call.arguments, <String, Object?>{'vault_id': 'vault-alpha'});
        return expected.canonicalBytes();
      };

      expect(await storage.read('vault-alpha'), expected);
    },
  );

  test('read returns null only for platform absence', () async {
    recorder.handler = (_) async => null;
    expect(await storage.read('vault-alpha'), isNull);
  });

  test('read rejects noncanonical corrupt and wrong-vault bytes', () async {
    final expected = testWindowsAtlasVaultLocalStore('vault-alpha');
    recorder.handler = (_) async => Uint8List.fromList(
      utf8.encode(
        const JsonEncoder.withIndent('  ').convert(expected.toJson()),
      ),
    );
    await expectLater(
      storage.read('vault-alpha'),
      throwsA(isA<AtlasVaultWindowsStorageException>()),
    );

    recorder.handler = (_) async => Uint8List.fromList(utf8.encode('{bad'));
    await expectLater(
      storage.read('vault-alpha'),
      throwsA(isA<AtlasVaultWindowsStorageException>()),
    );

    recorder.handler = (_) async =>
        testWindowsAtlasVaultLocalStore('vault-beta').canonicalBytes();
    await expectLater(
      storage.read('vault-alpha'),
      throwsA(isA<AtlasVaultWindowsStorageException>()),
    );
  });

  test('create uses exact canonical bytes and never overwrites', () async {
    final expected = testWindowsAtlasVaultLocalStore('vault-alpha');
    recorder.handler = (call) async {
      expect(call.method, 'createLocalStore');
      final arguments = call.arguments! as Map<Object?, Object?>;
      expect(arguments.keys, <Object?>['vault_id', 'store_bytes']);
      expect(arguments['vault_id'], 'vault-alpha');
      expect(
        arguments['store_bytes'],
        orderedEquals(expected.canonicalBytes()),
      );
      return null;
    };

    await storage.create('vault-alpha', expected);

    recorder.handler = (_) async {
      throw PlatformException(code: 'already_exists');
    };
    await expectLater(
      storage.create('vault-alpha', expected),
      throwsA(isA<AtlasVaultWindowsStorageException>()),
    );
  });

  test(
    'create rejects mismatched vault metadata before platform access',
    () async {
      await expectLater(
        storage.create(
          'vault-alpha',
          testWindowsAtlasVaultLocalStore('vault-beta'),
        ),
        throwsA(isA<AtlasVaultWindowsStorageException>()),
      );
      expect(recorder.calls, isEmpty);
    },
  );

  test('replace requires lowercase SHA-256 and exact arguments', () async {
    final replacement = testWindowsAtlasVaultLocalStore(
      'vault-alpha',
      updatedAt: '2026-07-31T00:00:01Z',
    );
    recorder.handler = (call) async {
      expect(call.method, 'replaceLocalStore');
      final arguments = call.arguments! as Map<Object?, Object?>;
      expect(arguments.keys, <Object?>[
        'vault_id',
        'store_bytes',
        'expected_sha256',
      ]);
      expect(arguments['vault_id'], 'vault-alpha');
      expect(
        arguments['store_bytes'],
        orderedEquals(replacement.canonicalBytes()),
      );
      expect(arguments['expected_sha256'], 'a' * 64);
      return null;
    };

    await storage.replace('vault-alpha', replacement, expectedSha256: 'a' * 64);

    expect(recorder.calls, hasLength(1));
  });

  test('invalid digest and stale replacement fail fixed', () async {
    final store = testWindowsAtlasVaultLocalStore('vault-alpha');
    for (final digest in <String>['', 'A' * 64, '0' * 63, 'g' * 64]) {
      await expectLater(
        storage.replace('vault-alpha', store, expectedSha256: digest),
        throwsA(isA<AtlasVaultWindowsStorageException>()),
      );
    }
    expect(recorder.calls, isEmpty);

    recorder.handler = (_) async {
      throw PlatformException(code: 'stale_digest');
    };
    await expectLater(
      storage.replace('vault-alpha', store, expectedSha256: '0' * 64),
      throwsA(isA<AtlasVaultWindowsStorageException>()),
    );
  });

  test('size bound is enforced before any channel call', () async {
    expect(
      AtlasWindowsVaultLocalStoreIO.maximumStoreByteCount,
      128 * 1024 * 1024,
    );
    final oversized = testWindowsAtlasVaultLocalStore(
      'vault-alpha',
      records: <AtlasVaultEncryptedRecord>[
        AtlasVaultEncryptedRecord.fromJson(<String, Object?>{
          'id': 'record',
          'schema_version': 1,
          'revision': 'revision',
          'parent_revision': null,
          'deleted': false,
          'key_id': 'key',
          'nonce': base64Encode(Uint8List(12)),
          'ciphertext': base64Encode(
            Uint8List(
              (AtlasWindowsVaultLocalStoreIO.maximumStoreByteCount * 3 ~/ 4) +
                  1,
            ),
          ),
        }),
      ],
    );

    await expectLater(
      storage.create('vault-alpha', oversized),
      throwsA(isA<AtlasVaultWindowsStorageException>()),
    );
    expect(recorder.calls, isEmpty);
  });

  test('delete uses the exact method and redacts platform paths', () async {
    recorder.handler = (call) async {
      expect(call.method, 'deleteLocalStore');
      throw PlatformException(
        code: 'native_failure',
        message: r'C:\Users\private\AppData\Local\vault.json',
      );
    };

    Object? failure;
    try {
      await storage.delete('vault-alpha');
    } catch (error) {
      failure = error;
    }
    expect(failure, isA<AtlasVaultWindowsStorageException>());
    expect(failure.toString(), isNot(contains(r'C:\Users')));
  });
}
