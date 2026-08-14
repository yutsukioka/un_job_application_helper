import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
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
}

Uint8List _bytes(Object? value) {
  return Uint8List.fromList(base64Decode(value! as String));
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
