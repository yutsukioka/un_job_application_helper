import 'dart:convert';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart' as android;
import 'package:atlas/atlas_vault_windows.dart' as windows;
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> root;

  setUpAll(() {
    root = loadAtlasVaultVector(
      'atlasvault_device_identity_pairing_vectors_v1.json',
    );
  });

  for (final deviceName in <String>['device_a', 'device_b']) {
    test('$deviceName rederives public identity and exact signature', () async {
      final vector = atlasVaultObject(root[deviceName]);
      final descriptorJson = atlasVaultObject(vector['descriptor']);
      final identity = await AtlasVaultDeviceIdentity.fromPrivateKeys(
        signingPrivateSeed: _bytes(vector['signing_private_seed']),
        agreementPrivateKey: _bytes(vector['agreement_private_key']),
        createdAt: descriptorJson['created_at']! as String,
        keyEpoch: descriptorJson['key_epoch']! as int,
      );

      expect(identity.signingPublicKey, _bytes(vector['signing_public_key']));
      expect(
        identity.agreementPublicKey,
        _bytes(vector['agreement_public_key']),
      );
      expect(identity.deviceId, vector['device_id']);
      expect(identity.descriptor.toJson(), descriptorJson);
      expect(
        identity.descriptor.canonicalBytes(),
        _bytes(vector['descriptor_canonical_json_b64']),
      );

      final signed = await identity.signDescriptor();
      expect(signed.signature, _bytes(vector['descriptor_signature']));
      expect(signed.toJson(), atlasVaultObject(vector['signed_descriptor']));
      expect(
        signed.canonicalBytes(),
        _bytes(vector['signed_descriptor_canonical_json_b64']),
      );
      expect(
        await verifyAtlasVaultSignedDeviceDescriptor(signed),
        identity.descriptor,
      );
    });

    test(
      '$deviceName secret bundle is strict and rederives identity',
      () async {
        final vector = atlasVaultObject(root[deviceName]);
        final secret = AtlasVaultDeviceIdentitySecret.fromJson(
          atlasVaultObject(vector['secret_bundle']),
        );
        final identity = await secret.loadIdentity();

        expect(
          secret.canonicalBytes(),
          _bytes(vector['secret_bundle_canonical_json_b64']),
        );
        expect(secret.toJson(), atlasVaultObject(vector['secret_bundle']));
        expect(
          identity.descriptor.toJson(),
          atlasVaultObject(vector['descriptor']),
        );

        final extra = _clone(atlasVaultObject(vector['secret_bundle']))
          ..['platform'] = 'test';
        expect(
          () => AtlasVaultDeviceIdentitySecret.fromJson(extra),
          throwsA(isA<AtlasVaultDeviceIdentityException>()),
        );
      },
    );
  }

  test('device ID derivation is domain separated and ordered', () async {
    final vector = atlasVaultObject(root['device_a']);
    final signing = _bytes(vector['signing_public_key']);
    final agreement = _bytes(vector['agreement_public_key']);

    expect(
      await deriveAtlasVaultDeviceId(signing, agreement),
      vector['device_id'],
    );
    expect(
      await deriveAtlasVaultDeviceId(agreement, signing),
      isNot(vector['device_id']),
    );
  });

  test(
    'descriptor rejects mismatch, unknown fields, and noncanonical values',
    () {
      final vector = atlasVaultObject(root['device_a']);
      final other = atlasVaultObject(root['device_b']);
      final invalid = <Map<String, Object?>>[];

      invalid.add(
        _clone(atlasVaultObject(vector['descriptor']))
          ..['device_id'] = other['device_id'],
      );
      invalid.add(
        _clone(atlasVaultObject(vector['descriptor']))
          ..['device_label'] = 'private label',
      );
      invalid.add(
        _clone(atlasVaultObject(vector['descriptor']))..['key_epoch'] = true,
      );
      invalid.add(
        _clone(atlasVaultObject(vector['descriptor']))
          ..['created_at'] = '2026-01-15T12:00:00.000Z',
      );
      invalid.add(
        _clone(atlasVaultObject(vector['descriptor']))
          ..['signing_public_key'] = (vector['signing_public_key']! as String)
              .replaceAll('=', ''),
      );

      for (final item in invalid) {
        expect(
          () => AtlasVaultDeviceDescriptor.fromJson(item),
          throwsA(isA<AtlasVaultDeviceIdentityException>()),
        );
      }
    },
  );

  test('signature and signing-key substitution fail closed', () async {
    final vector = atlasVaultObject(root['device_a']);
    final other = atlasVaultObject(root['device_b']);
    final tampered = _clone(atlasVaultObject(vector['signed_descriptor']));
    final signature = _bytes(tampered['signature'])..[0] ^= 1;
    tampered['signature'] = base64Encode(signature);

    final substituted = _clone(atlasVaultObject(vector['signed_descriptor']));
    final descriptor = atlasVaultObject(substituted['descriptor']);
    descriptor['signing_public_key'] = other['signing_public_key'];
    descriptor['device_id'] = await deriveAtlasVaultDeviceId(
      _bytes(other['signing_public_key']),
      _bytes(descriptor['agreement_public_key']),
    );

    for (final item in <Map<String, Object?>>[tampered, substituted]) {
      await expectLater(
        verifyAtlasVaultSignedDeviceDescriptor(
          AtlasVaultSignedDeviceDescriptor.fromJson(item),
        ),
        throwsA(isA<AtlasVaultDeviceIdentityException>()),
      );
    }
  });

  test('secret descriptions and errors reveal no private values', () async {
    final vector = atlasVaultObject(root['device_a']);
    final secret = AtlasVaultDeviceIdentitySecret.fromJson(
      atlasVaultObject(vector['secret_bundle']),
    );
    final identity = await secret.loadIdentity();
    final forbidden = <String>[
      vector['signing_private_seed']! as String,
      vector['agreement_private_key']! as String,
    ];

    for (final rendered in <String>[secret.toString(), identity.toString()]) {
      for (final value in forbidden) {
        expect(rendered, isNot(contains(value)));
      }
    }
  });

  group('explicit device identity custody', () {
    test('construction performs no call and generation is explicit', () async {
      final store = _FakeDeviceIdentitySecretStore();
      var generationCount = 0;
      final custody = AtlasDeviceIdentityCustody(
        store,
        identityGenerator: () async {
          generationCount += 1;
          return _identityFromVector(root, 'device_a');
        },
      );

      expect(store.callCount, 0);
      expect(generationCount, 0);
      expect(custody.toString(), isNot(contains('signing_private')));

      final descriptor = await custody.createPrimaryIdentity();

      expect(generationCount, 1);
      expect(store.createCount, 1);
      expect(store.loadCount, 1);
      expect(
        store.createdBytes,
        loadAtlasVaultDeviceIdentitySecretBytes(root, 'device_a'),
      );
      expect(
        descriptor.toJson(),
        atlasVaultObject(atlasVaultObject(root['device_a'])['descriptor']),
      );
    });

    test('create-only collision preserves the installed identity', () async {
      final installed = loadAtlasVaultDeviceIdentitySecretBytes(
        root,
        'device_a',
      );
      final store = _FakeDeviceIdentitySecretStore(initialValue: installed);
      final custody = AtlasDeviceIdentityCustody(
        store,
        identityGenerator: () => _identityFromVector(root, 'device_b'),
      );

      await expectLater(
        custody.createPrimaryIdentity(),
        throwsA(isA<AtlasVaultDeviceIdentityCustodyException>()),
      );
      expect(await store.loadPrimaryIdentity(), installed);
    });

    test('load strictly rederives both keys and the device ID', () async {
      final installed = loadAtlasVaultDeviceIdentitySecretBytes(
        root,
        'device_a',
      );
      final store = _FakeDeviceIdentitySecretStore(initialValue: installed);
      final custody = AtlasDeviceIdentityCustody(store);

      final descriptor = await custody.loadPrimaryIdentity();
      expect(
        descriptor?.toJson(),
        atlasVaultObject(atlasVaultObject(root['device_a'])['descriptor']),
      );

      installed.fillRange(0, installed.length, 0);
      final second = await custody.loadPrimaryIdentity();
      expect(second?.deviceId, descriptor?.deviceId);
    });

    test('mismatched loaded device ID fails with a redacted error', () async {
      final secret = _clone(
        atlasVaultObject(atlasVaultObject(root['device_a'])['secret_bundle']),
      )..['device_id'] = atlasVaultObject(root['device_b'])['device_id'];
      final privateSentinel = secret['signing_private_key']! as String;
      final store = _FakeDeviceIdentitySecretStore(
        initialValue: Uint8List.fromList(utf8.encode(jsonEncode(secret))),
      );
      final custody = AtlasDeviceIdentityCustody(store);

      await expectLater(
        custody.loadPrimaryIdentity(),
        throwsA(
          isA<AtlasVaultDeviceIdentityCustodyException>().having(
            (error) => error.toString(),
            'description',
            isNot(contains(privateSentinel)),
          ),
        ),
      );
    });

    test('invalid private-key lengths fail closed', () async {
      final original = atlasVaultObject(
        atlasVaultObject(root['device_a'])['secret_bundle'],
      );
      for (final field in <String>[
        'signing_private_key',
        'agreement_private_key',
      ]) {
        final secret = _clone(original)..[field] = base64Encode(Uint8List(31));
        final store = _FakeDeviceIdentitySecretStore(
          initialValue: Uint8List.fromList(utf8.encode(jsonEncode(secret))),
        );
        await expectLater(
          AtlasDeviceIdentityCustody(store).loadPrimaryIdentity(),
          throwsA(isA<AtlasVaultDeviceIdentityCustodyException>()),
        );
      }
    });

    test('delete is idempotent and contains delegates explicitly', () async {
      final store = _FakeDeviceIdentitySecretStore(
        initialValue: loadAtlasVaultDeviceIdentitySecretBytes(root, 'device_a'),
      );
      final custody = AtlasDeviceIdentityCustody(store);

      expect(await custody.containsPrimaryIdentity(), isTrue);
      await custody.deletePrimaryIdentity();
      await custody.deletePrimaryIdentity();
      expect(await custody.containsPrimaryIdentity(), isFalse);
      expect(store.deleteCount, 2);
    });
  });

  group('platform device identity adapters', () {
    TestWidgetsFlutterBinding.ensureInitialized();

    test(
      'Android adapter uses the exact create/load/contains/delete methods',
      () async {
        await _expectPlatformAdapterContract(
          channelName: android.atlasVaultAndroidMethodChannelName,
          createStore: (channel) =>
              android.AtlasAndroidDeviceIdentitySecretStore(channel: channel),
          expectedException: isA<android.AtlasVaultAndroidStorageException>(),
          canonicalBytes: loadAtlasVaultDeviceIdentitySecretBytes(
            root,
            'device_a',
          ),
        );
      },
    );

    test(
      'Windows adapter uses the exact create/load/contains/delete methods',
      () async {
        await _expectPlatformAdapterContract(
          channelName: windows.atlasVaultWindowsMethodChannelName,
          createStore: (channel) =>
              windows.AtlasWindowsDeviceIdentitySecretStore(channel: channel),
          expectedException: isA<windows.AtlasVaultWindowsStorageException>(),
          canonicalBytes: loadAtlasVaultDeviceIdentitySecretBytes(
            root,
            'device_a',
          ),
        );
      },
    );
  });
}

Future<AtlasVaultDeviceIdentity> _identityFromVector(
  Map<String, Object?> root,
  String deviceName,
) {
  final vector = atlasVaultObject(root[deviceName]);
  final descriptor = atlasVaultObject(vector['descriptor']);
  return AtlasVaultDeviceIdentity.fromPrivateKeys(
    signingPrivateSeed: _bytes(vector['signing_private_seed']),
    agreementPrivateKey: _bytes(vector['agreement_private_key']),
    createdAt: descriptor['created_at']! as String,
    keyEpoch: descriptor['key_epoch']! as int,
  );
}

Future<void> _expectPlatformAdapterContract({
  required String channelName,
  required AtlasDeviceIdentitySecretStore Function(MethodChannel channel)
  createStore,
  required Matcher expectedException,
  required Uint8List canonicalBytes,
}) async {
  final channel = MethodChannel(channelName);
  final calls = <MethodCall>[];
  final platformBytes = Uint8List.fromList(canonicalBytes);
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  messenger.setMockMethodCallHandler(channel, (call) async {
    calls.add(call);
    switch (call.method) {
      case 'createDeviceIdentitySecret':
      case 'deleteDeviceIdentitySecret':
        return null;
      case 'loadDeviceIdentitySecret':
        return platformBytes;
      case 'containsDeviceIdentitySecret':
        return true;
    }
    throw PlatformException(code: 'unexpected');
  });
  addTearDown(() => messenger.setMockMethodCallHandler(channel, null));
  final store = createStore(channel);
  expect(calls, isEmpty);

  await store.createPrimaryIdentity(canonicalBytes);
  final loaded = await store.loadPrimaryIdentity();
  expect(loaded, canonicalBytes);
  platformBytes.fillRange(0, platformBytes.length, 0);
  expect(loaded, canonicalBytes);
  expect(await store.containsPrimaryIdentity(), isTrue);
  await store.deletePrimaryIdentity();

  expect(calls.map((call) => call.method), <String>[
    'createDeviceIdentitySecret',
    'loadDeviceIdentitySecret',
    'containsDeviceIdentitySecret',
    'deleteDeviceIdentitySecret',
  ]);
  expect((calls.first.arguments! as Map<Object?, Object?>).keys, <Object?>[
    'secret_bytes',
  ]);
  expect(calls[1].arguments, isNull);
  expect(calls[2].arguments, isNull);
  expect(calls[3].arguments, isNull);

  final oversized = Uint8List(16 * 1024 + 1);
  await expectLater(
    store.createPrimaryIdentity(oversized),
    throwsA(expectedException),
  );
  expect(calls, hasLength(4));

  const privateSentinel = 'FAKE_PRIVATE_DEVICE_IDENTITY_SENTINEL';
  messenger.setMockMethodCallHandler(
    channel,
    (_) async => throw PlatformException(
      code: privateSentinel,
      message: privateSentinel,
    ),
  );
  Object? platformError;
  try {
    await store.containsPrimaryIdentity();
  } catch (error) {
    platformError = error;
  }
  expect(platformError, expectedException);
  expect(platformError.toString(), isNot(contains(privateSentinel)));
}

final class _FakeDeviceIdentitySecretStore
    implements AtlasDeviceIdentitySecretStore {
  _FakeDeviceIdentitySecretStore({Uint8List? initialValue})
    : _value = initialValue == null ? null : Uint8List.fromList(initialValue);

  Uint8List? _value;
  Uint8List? createdBytes;
  int createCount = 0;
  int loadCount = 0;
  int containsCount = 0;
  int deleteCount = 0;

  int get callCount => createCount + loadCount + containsCount + deleteCount;

  @override
  Future<void> createPrimaryIdentity(Uint8List canonicalSecretBundle) async {
    createCount += 1;
    if (_value != null) {
      throw const AtlasVaultDeviceIdentityCustodyException();
    }
    createdBytes = Uint8List.fromList(canonicalSecretBundle);
    _value = Uint8List.fromList(canonicalSecretBundle);
  }

  @override
  Future<Uint8List?> loadPrimaryIdentity() async {
    loadCount += 1;
    return _value == null ? null : Uint8List.fromList(_value!);
  }

  @override
  Future<bool> containsPrimaryIdentity() async {
    containsCount += 1;
    return _value != null;
  }

  @override
  Future<void> deletePrimaryIdentity() async {
    deleteCount += 1;
    _value?.fillRange(0, _value!.length, 0);
    _value = null;
  }
}

Uint8List _bytes(Object? value) {
  return Uint8List.fromList(base64Decode(value! as String));
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
