import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_android_fakes.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AtlasVaultAndroidMethodCallRecorder recorder;
  late AtlasAndroidVaultLocalStoreIO storage;

  setUp(() {
    recorder = AtlasVaultAndroidMethodCallRecorder(
      channelName: atlasVaultAndroidMethodChannelName,
    )..install();
    storage = AtlasAndroidVaultLocalStoreIO(channel: recorder.channel);
  });

  tearDown(() {
    recorder.uninstall();
  });

  test('construction performs no platform call', () {
    expect(recorder.calls, isEmpty);
  });

  test('read strictly decodes exact canonical bytes', () async {
    final expected = testAtlasVaultLocalStore('vault-alpha');
    recorder.handler = (call) async {
      expect(call.method, 'readLocalStore');
      expect(call.arguments, <String, Object?>{'vault_id': 'vault-alpha'});
      return expected.canonicalBytes();
    };

    final restored = await storage.read('vault-alpha');

    expect(restored, expected);
  });

  test('read returns null only when the platform reports absence', () async {
    recorder.handler = (_) async => null;

    expect(await storage.read('vault-alpha'), isNull);
  });

  test('read rejects noncanonical and corrupt bytes', () async {
    final expected = testAtlasVaultLocalStore('vault-alpha');
    final noncanonical = Uint8List.fromList(
      utf8.encode(
        const JsonEncoder.withIndent('  ').convert(expected.toJson()),
      ),
    );
    recorder.handler = (_) async => noncanonical;

    await expectLater(
      storage.read('vault-alpha'),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );

    recorder.handler = (_) async => Uint8List.fromList(utf8.encode('{bad'));
    await expectLater(
      storage.read('vault-alpha'),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );
  });

  test('read rejects a store for another vault', () async {
    recorder.handler = (_) async =>
        testAtlasVaultLocalStore('vault-beta').canonicalBytes();

    await expectLater(
      storage.read('vault-alpha'),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );
  });

  test('create sends only canonical bytes through create operation', () async {
    final store = testAtlasVaultLocalStore('vault-alpha');
    recorder.handler = (call) async {
      expect(call.method, 'createLocalStore');
      final arguments = call.arguments! as Map<Object?, Object?>;
      expect(arguments.keys, <Object?>['vault_id', 'store_bytes']);
      expect(arguments['vault_id'], 'vault-alpha');
      expect(arguments['store_bytes'], orderedEquals(store.canonicalBytes()));
      return null;
    };

    await storage.create('vault-alpha', store);

    expect(recorder.calls.single.method, 'createLocalStore');
  });

  test('create rejects a store whose metadata vault does not match', () async {
    final store = testAtlasVaultLocalStore('vault-beta');

    await expectLater(
      storage.create('vault-alpha', store),
      throwsA(isA<AtlasVaultAndroidStorageException>()),
    );

    expect(recorder.calls, isEmpty);
  });

  test(
    'replace requires canonical expected SHA-256 and exact arguments',
    () async {
      final store = testAtlasVaultLocalStore(
        'vault-alpha',
        updatedAt: '2026-07-28T00:00:01Z',
      );
      final expectedDigest = 'a' * 64;
      recorder.handler = (call) async {
        expect(call.method, 'replaceLocalStore');
        final arguments = call.arguments! as Map<Object?, Object?>;
        expect(arguments.keys, <Object?>[
          'vault_id',
          'store_bytes',
          'expected_sha256',
        ]);
        expect(arguments['expected_sha256'], expectedDigest);
        expect(arguments['store_bytes'], orderedEquals(store.canonicalBytes()));
        return null;
      };

      await storage.replace(
        'vault-alpha',
        store,
        expectedSha256: expectedDigest,
      );

      expect(recorder.calls.single.method, 'replaceLocalStore');
    },
  );

  test('invalid expected digest causes no replacement', () async {
    final store = testAtlasVaultLocalStore('vault-alpha');

    for (final digest in <String>['', 'A' * 64, '0' * 63, 'g' * 64]) {
      await expectLater(
        storage.replace('vault-alpha', store, expectedSha256: digest),
        throwsA(isA<AtlasVaultAndroidStorageException>()),
      );
    }

    expect(recorder.calls, isEmpty);
  });

  test('delete uses exact method and redacts native path errors', () async {
    recorder.handler = (call) async {
      expect(call.method, 'deleteLocalStore');
      throw PlatformException(
        code: 'stale_digest',
        message: '/data/user/0/private-vault',
      );
    };

    Object? failure;
    try {
      await storage.delete('vault-alpha');
    } catch (error) {
      failure = error;
    }

    expect(failure, isA<AtlasVaultAndroidStorageException>());
    expect(failure.toString(), 'AtlasVault Android storage operation failed.');
    expect(failure.toString(), isNot(contains('/data/user')));
  });

  test('maximum local-store size remains fixed at 128 MiB', () {
    expect(
      AtlasAndroidVaultLocalStoreIO.maximumStoreByteCount,
      128 * 1024 * 1024,
    );
  });
}
