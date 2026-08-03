import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_windows_fakes.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AtlasVaultWindowsMethodCallRecorder recorder;
  late AtlasWindowsVaultSecureKeyStore storage;

  setUp(() {
    recorder = AtlasVaultWindowsMethodCallRecorder(
      channelName: atlasVaultWindowsMethodChannelName,
    )..install();
    storage = AtlasWindowsVaultSecureKeyStore(channel: recorder.channel);
  });

  tearDown(() {
    recorder.uninstall();
  });

  test('construction performs no platform call', () {
    expect(recorder.calls, isEmpty);
  });

  test('invalid vault IDs make no platform call', () async {
    for (final value in <String>[
      '',
      '.',
      '..',
      'contains space',
      r'contains\separator',
      'contains/separator',
      'saved_search',
      'SAVED_JOB',
      'application_note',
      'profile_snippet',
      'draft_metadata',
      'x' * 97,
    ]) {
      await expectLater(
        storage.containsVaultKey(value),
        throwsA(isA<AtlasVaultWindowsStorageException>()),
      );
    }
    expect(recorder.calls, isEmpty);
  });

  test('capabilities require the exact fixed response', () async {
    recorder.handler = (call) async {
      expect(call.method, 'capabilities');
      expect(call.arguments, isNull);
      return <String, Object?>{
        'secure_boundary_available': true,
        'dpapi_available': true,
        'current_user_scope': true,
        'local_app_data_available': true,
        'atomic_replace_available': true,
        'hardware_backed_guaranteed': false,
      };
    };

    final capabilities = await storage.capabilities();

    expect(capabilities.secureBoundaryAvailable, isTrue);
    expect(capabilities.dpapiAvailable, isTrue);
    expect(capabilities.currentUserScope, isTrue);
    expect(capabilities.localAppDataAvailable, isTrue);
    expect(capabilities.atomicReplaceAvailable, isTrue);
    expect(capabilities.hardwareBackedGuaranteed, isFalse);
    expect(
      capabilities.toString(),
      'AtlasVaultWindowsCapabilities(<redacted>)',
    );
  });

  test('capabilities reject missing extra and mistyped fields', () async {
    final invalid = <Map<String, Object?>>[
      <String, Object?>{},
      <String, Object?>{
        'secure_boundary_available': true,
        'dpapi_available': true,
        'current_user_scope': true,
        'local_app_data_available': true,
        'atomic_replace_available': true,
        'hardware_backed_guaranteed': false,
        'path': r'C:\private',
      },
      <String, Object?>{
        'secure_boundary_available': true,
        'dpapi_available': 1,
        'current_user_scope': true,
        'local_app_data_available': true,
        'atomic_replace_available': true,
        'hardware_backed_guaranteed': false,
      },
    ];

    for (final value in invalid) {
      recorder.handler = (_) async => value;
      await expectLater(
        storage.capabilities(),
        throwsA(isA<AtlasVaultWindowsStorageException>()),
      );
    }
  });

  test('create validates key length and uses exact arguments', () async {
    final key = deterministicWindowsVaultKey();
    recorder.handler = (call) async {
      expect(call.method, 'createVaultKey');
      final arguments = call.arguments! as Map<Object?, Object?>;
      expect(arguments.keys, <Object?>['vault_id', 'vault_key']);
      expect(arguments['vault_id'], 'vault-alpha');
      expect(arguments['vault_key'], orderedEquals(key));
      return null;
    };

    await storage.createVaultKey('vault-alpha', key);

    expect(recorder.calls, hasLength(1));
  });

  test('invalid key lengths are rejected before platform access', () async {
    for (final value in <Uint8List>[
      Uint8List(0),
      Uint8List(31),
      Uint8List(33),
    ]) {
      await expectLater(
        storage.createVaultKey('vault-alpha', value),
        throwsA(isA<AtlasVaultWindowsStorageException>()),
      );
    }
    expect(recorder.calls, isEmpty);
  });

  test('duplicate create maps to a fixed redacted failure', () async {
    recorder.handler = (_) async {
      throw PlatformException(
        code: 'already_exists',
        message: r'C:\Users\private\key.bin',
      );
    };

    await expectLater(
      storage.createVaultKey('vault-alpha', deterministicWindowsVaultKey()),
      throwsA(
        isA<AtlasVaultWindowsStorageException>().having(
          (value) => value.toString(),
          'description',
          'AtlasVault Windows storage operation failed.',
        ),
      ),
    );
  });

  test('load absent returns null and loaded key is copied', () async {
    recorder.handler = (_) async => null;
    expect(await storage.loadVaultKey('vault-alpha'), isNull);

    final platformKey = deterministicWindowsVaultKey();
    recorder.handler = (_) async => platformKey;
    final loaded = await storage.loadVaultKey('vault-alpha');

    expect(loaded, orderedEquals(platformKey));
    expect(identical(loaded, platformKey), isFalse);
    loaded![0] ^= 0xff;
    expect(platformKey.first, 73);
  });

  test('invalid loaded key length fails fixed', () async {
    recorder.handler = (_) async => Uint8List(31);
    await expectLater(
      storage.loadVaultKey('vault-alpha'),
      throwsA(isA<AtlasVaultWindowsStorageException>()),
    );
  });

  test('contains requires a Boolean result', () async {
    recorder.handler = (_) async => true;
    expect(await storage.containsVaultKey('vault-alpha'), isTrue);
    recorder.handler = (_) async => 'true';
    await expectLater(
      storage.containsVaultKey('vault-alpha'),
      throwsA(isA<AtlasVaultWindowsStorageException>()),
    );
  });

  test('delete is repeatable at the adapter boundary', () async {
    recorder.handler = (call) async {
      expect(call.method, 'deleteVaultKey');
      expect(call.arguments, <String, Object?>{'vault_id': 'vault-alpha'});
      return null;
    };

    await storage.deleteVaultKey('vault-alpha');
    await storage.deleteVaultKey('vault-alpha');

    expect(recorder.calls.map((call) => call.method), <String>[
      'deleteVaultKey',
      'deleteVaultKey',
    ]);
  });

  test('platform details and secure paths remain redacted', () async {
    recorder.handler = (_) async {
      throw PlatformException(
        code: 'win32_5',
        message: r'C:\Users\private\AppData\Local\UNApplications\AtlasVault',
        details: <String, Object?>{'sid': 'S-1-5-21-private'},
      );
    };

    Object? failure;
    try {
      await storage.loadVaultKey('vault-alpha');
    } catch (error) {
      failure = error;
    }

    expect(failure, isA<AtlasVaultWindowsStorageException>());
    expect(failure.toString(), 'AtlasVault Windows storage operation failed.');
    expect(failure.toString(), isNot(contains('AppData')));
    expect(failure.toString(), isNot(contains('S-1-5')));
  });
}
