import 'dart:convert';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:cryptography/dart.dart';

import 'canonical_json.dart';
import 'strict_values.dart';

const _descriptorFormat = 'atlasvault-device-descriptor';
const _signedDescriptorFormat = 'atlasvault-signed-device-descriptor';
const _secretFormat = 'atlasvault-device-identity-secret';
const _identityVersion = 1;
const _keyLength = 32;
const _signatureLength = 64;
const _deviceIdDomain = 'atlasvault-device-id-v1:';
const _descriptorSignatureDomain = 'atlasvault-device-descriptor-signature-v1:';

final class AtlasVaultDeviceIdentityException implements Exception {
  const AtlasVaultDeviceIdentityException();

  @override
  String toString() => 'AtlasVault device identity is invalid.';
}

final class AtlasVaultDeviceDescriptor {
  AtlasVaultDeviceDescriptor._({
    required this.deviceId,
    required Uint8List signingPublicKey,
    required Uint8List agreementPublicKey,
    required this.createdAt,
    required this.keyEpoch,
  }) : _signingPublicKey = Uint8List.fromList(signingPublicKey),
       _agreementPublicKey = Uint8List.fromList(agreementPublicKey);

  final String deviceId;
  final Uint8List _signingPublicKey;
  final Uint8List _agreementPublicKey;
  final String createdAt;
  final int keyEpoch;

  Uint8List get signingPublicKey => Uint8List.fromList(_signingPublicKey);
  Uint8List get agreementPublicKey => Uint8List.fromList(_agreementPublicKey);

  factory AtlasVaultDeviceDescriptor.fromJson(Map<String, Object?> value) {
    try {
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'device_id',
          'signing_public_key',
          'agreement_public_key',
          'created_at',
          'key_epoch',
        },
        context: 'Device descriptor',
      );
      if (value['format'] != _descriptorFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _identityVersion) {
        throw const AtlasVaultDeviceIdentityException();
      }
      final signing = requireAtlasVaultCanonicalBase64(
        value['signing_public_key'],
        field: 'signing_public_key',
        exactLength: _keyLength,
      );
      final agreement = requireAtlasVaultCanonicalBase64(
        value['agreement_public_key'],
        field: 'agreement_public_key',
        exactLength: _keyLength,
      );
      final deviceId = requireAtlasVaultString(
        value['device_id'],
        field: 'device_id',
        allowEmpty: false,
      );
      if (!_constantTimeTextEquals(
        deviceId,
        _deriveDeviceIdSync(signing, agreement),
      )) {
        throw const AtlasVaultDeviceIdentityException();
      }
      final keyEpoch = requireAtlasVaultInt(
        value['key_epoch'],
        field: 'key_epoch',
      );
      if (keyEpoch <= 0) {
        throw const AtlasVaultDeviceIdentityException();
      }
      return AtlasVaultDeviceDescriptor._(
        deviceId: deviceId,
        signingPublicKey: signing,
        agreementPublicKey: agreement,
        createdAt: requireAtlasVaultUtcSeconds(
          value['created_at'],
          field: 'created_at',
        ),
        keyEpoch: keyEpoch,
      );
    } catch (_) {
      throw const AtlasVaultDeviceIdentityException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _descriptorFormat,
    'version': _identityVersion,
    'device_id': deviceId,
    'signing_public_key': base64Encode(_signingPublicKey),
    'agreement_public_key': base64Encode(_agreementPublicKey),
    'created_at': createdAt,
    'key_epoch': keyEpoch,
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  @override
  bool operator ==(Object other) {
    return other is AtlasVaultDeviceDescriptor &&
        deviceId == other.deviceId &&
        createdAt == other.createdAt &&
        keyEpoch == other.keyEpoch &&
        _bytesEqual(_signingPublicKey, other._signingPublicKey) &&
        _bytesEqual(_agreementPublicKey, other._agreementPublicKey);
  }

  @override
  int get hashCode => Object.hash(deviceId, createdAt, keyEpoch);

  @override
  String toString() => 'AtlasVaultDeviceDescriptor($deviceId)';
}

final class AtlasVaultSignedDeviceDescriptor {
  AtlasVaultSignedDeviceDescriptor._({
    required this.descriptor,
    required Uint8List signature,
  }) : _signature = Uint8List.fromList(signature);

  final AtlasVaultDeviceDescriptor descriptor;
  final Uint8List _signature;

  Uint8List get signature => Uint8List.fromList(_signature);

  factory AtlasVaultSignedDeviceDescriptor.fromJson(
    Map<String, Object?> value,
  ) {
    try {
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'descriptor',
          'signature',
        },
        context: 'Signed device descriptor',
      );
      if (value['format'] != _signedDescriptorFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _identityVersion) {
        throw const AtlasVaultDeviceIdentityException();
      }
      return AtlasVaultSignedDeviceDescriptor._(
        descriptor: AtlasVaultDeviceDescriptor.fromJson(
          requireAtlasVaultObject(
            value['descriptor'],
            context: 'Signed device descriptor descriptor',
          ),
        ),
        signature: requireAtlasVaultCanonicalBase64(
          value['signature'],
          field: 'signature',
          exactLength: _signatureLength,
        ),
      );
    } catch (_) {
      throw const AtlasVaultDeviceIdentityException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _signedDescriptorFormat,
    'version': _identityVersion,
    'descriptor': descriptor.toJson(),
    'signature': base64Encode(_signature),
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  @override
  String toString() => 'AtlasVaultSignedDeviceDescriptor(<redacted>)';
}

Future<AtlasVaultDeviceDescriptor> verifyAtlasVaultSignedDeviceDescriptor(
  AtlasVaultSignedDeviceDescriptor signed,
) async {
  try {
    final verified = await Ed25519().verify(
      _concat(<List<int>>[
        utf8.encode(_descriptorSignatureDomain),
        signed.descriptor.canonicalBytes(),
      ]),
      signature: Signature(
        signed._signature,
        publicKey: SimplePublicKey(
          signed.descriptor._signingPublicKey,
          type: KeyPairType.ed25519,
        ),
      ),
    );
    if (!verified) {
      throw const AtlasVaultDeviceIdentityException();
    }
    return signed.descriptor;
  } catch (_) {
    throw const AtlasVaultDeviceIdentityException();
  }
}

final class AtlasVaultDeviceIdentitySecret {
  AtlasVaultDeviceIdentitySecret._({
    required this.deviceId,
    required this.createdAt,
    required this.keyEpoch,
    required Uint8List signingPrivateKey,
    required Uint8List agreementPrivateKey,
  }) : _signingPrivateKey = Uint8List.fromList(signingPrivateKey),
       _agreementPrivateKey = Uint8List.fromList(agreementPrivateKey);

  final String deviceId;
  final String createdAt;
  final int keyEpoch;
  final Uint8List _signingPrivateKey;
  final Uint8List _agreementPrivateKey;

  factory AtlasVaultDeviceIdentitySecret.fromJson(Map<String, Object?> value) {
    try {
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'device_id',
          'created_at',
          'key_epoch',
          'signing_private_key',
          'agreement_private_key',
        },
        context: 'Device identity secret',
      );
      if (value['format'] != _secretFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _identityVersion) {
        throw const AtlasVaultDeviceIdentityException();
      }
      final epoch = requireAtlasVaultInt(
        value['key_epoch'],
        field: 'key_epoch',
      );
      if (epoch <= 0) {
        throw const AtlasVaultDeviceIdentityException();
      }
      return AtlasVaultDeviceIdentitySecret._(
        deviceId: requireAtlasVaultString(
          value['device_id'],
          field: 'device_id',
          allowEmpty: false,
        ),
        createdAt: requireAtlasVaultUtcSeconds(
          value['created_at'],
          field: 'created_at',
        ),
        keyEpoch: epoch,
        signingPrivateKey: requireAtlasVaultCanonicalBase64(
          value['signing_private_key'],
          field: 'signing_private_key',
          exactLength: _keyLength,
        ),
        agreementPrivateKey: requireAtlasVaultCanonicalBase64(
          value['agreement_private_key'],
          field: 'agreement_private_key',
          exactLength: _keyLength,
        ),
      );
    } catch (_) {
      throw const AtlasVaultDeviceIdentityException();
    }
  }

  Future<AtlasVaultDeviceIdentity> loadIdentity() {
    return AtlasVaultDeviceIdentity.fromPrivateKeys(
      signingPrivateSeed: _signingPrivateKey,
      agreementPrivateKey: _agreementPrivateKey,
      createdAt: createdAt,
      keyEpoch: keyEpoch,
      expectedDeviceId: deviceId,
    );
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _secretFormat,
    'version': _identityVersion,
    'device_id': deviceId,
    'created_at': createdAt,
    'key_epoch': keyEpoch,
    'signing_private_key': base64Encode(_signingPrivateKey),
    'agreement_private_key': base64Encode(_agreementPrivateKey),
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  void destroy() {
    _signingPrivateKey.fillRange(0, _signingPrivateKey.length, 0);
    _agreementPrivateKey.fillRange(0, _agreementPrivateKey.length, 0);
  }

  @override
  String toString() => 'AtlasVaultDeviceIdentitySecret(<redacted>)';
}

final class AtlasVaultDeviceIdentity {
  AtlasVaultDeviceIdentity._({
    required Uint8List signingPrivateSeed,
    required Uint8List agreementPrivateKey,
    required this.descriptor,
  }) : _signingPrivateSeed = Uint8List.fromList(signingPrivateSeed),
       _agreementPrivateKey = Uint8List.fromList(agreementPrivateKey);

  final Uint8List _signingPrivateSeed;
  final Uint8List _agreementPrivateKey;
  final AtlasVaultDeviceDescriptor descriptor;

  String get deviceId => descriptor.deviceId;
  Uint8List get signingPublicKey => descriptor.signingPublicKey;
  Uint8List get agreementPublicKey => descriptor.agreementPublicKey;

  static Future<AtlasVaultDeviceIdentity> fromPrivateKeys({
    required Uint8List signingPrivateSeed,
    required Uint8List agreementPrivateKey,
    required String createdAt,
    int keyEpoch = 1,
    String? expectedDeviceId,
  }) async {
    final signingCopy = Uint8List.fromList(signingPrivateSeed);
    final agreementCopy = Uint8List.fromList(agreementPrivateKey);
    try {
      if (signingCopy.length != _keyLength ||
          agreementCopy.length != _keyLength ||
          keyEpoch <= 0) {
        throw const AtlasVaultDeviceIdentityException();
      }
      requireAtlasVaultUtcSeconds(createdAt, field: 'created_at');
      final signingPair = await Ed25519().newKeyPairFromSeed(signingCopy);
      final agreementPair = await X25519().newKeyPairFromSeed(agreementCopy);
      final signingPublic = await signingPair.extractPublicKey();
      final agreementPublic = await agreementPair.extractPublicKey();
      final deviceId = _deriveDeviceIdSync(
        Uint8List.fromList(signingPublic.bytes),
        Uint8List.fromList(agreementPublic.bytes),
      );
      if (expectedDeviceId != null &&
          !_constantTimeTextEquals(deviceId, expectedDeviceId)) {
        throw const AtlasVaultDeviceIdentityException();
      }
      return AtlasVaultDeviceIdentity._(
        signingPrivateSeed: signingCopy,
        agreementPrivateKey: agreementCopy,
        descriptor: AtlasVaultDeviceDescriptor._(
          deviceId: deviceId,
          signingPublicKey: Uint8List.fromList(signingPublic.bytes),
          agreementPublicKey: Uint8List.fromList(agreementPublic.bytes),
          createdAt: createdAt,
          keyEpoch: keyEpoch,
        ),
      );
    } catch (_) {
      throw const AtlasVaultDeviceIdentityException();
    } finally {
      signingCopy.fillRange(0, signingCopy.length, 0);
      agreementCopy.fillRange(0, agreementCopy.length, 0);
    }
  }

  Future<Uint8List> signBytes(List<int> message) async {
    try {
      final keyPair = await Ed25519().newKeyPairFromSeed(_signingPrivateSeed);
      final signature = await Ed25519().sign(message, keyPair: keyPair);
      return Uint8List.fromList(signature.bytes);
    } catch (_) {
      throw const AtlasVaultDeviceIdentityException();
    }
  }

  Future<AtlasVaultSignedDeviceDescriptor> signDescriptor() async {
    return AtlasVaultSignedDeviceDescriptor._(
      descriptor: descriptor,
      signature: await signBytes(
        _concat(<List<int>>[
          utf8.encode(_descriptorSignatureDomain),
          descriptor.canonicalBytes(),
        ]),
      ),
    );
  }

  Future<Uint8List> sharedSecretFor(Uint8List remotePublicKey) async {
    SecretKey? shared;
    try {
      if (remotePublicKey.length != _keyLength) {
        throw const AtlasVaultDeviceIdentityException();
      }
      final keyPair = await X25519().newKeyPairFromSeed(_agreementPrivateKey);
      shared = await X25519().sharedSecretKey(
        keyPair: keyPair,
        remotePublicKey: SimplePublicKey(
          remotePublicKey,
          type: KeyPairType.x25519,
        ),
      );
      final bytes = Uint8List.fromList(await shared.extractBytes());
      if (bytes.length != _keyLength || _isAllZero(bytes)) {
        bytes.fillRange(0, bytes.length, 0);
        throw const AtlasVaultDeviceIdentityException();
      }
      return bytes;
    } catch (_) {
      throw const AtlasVaultDeviceIdentityException();
    } finally {
      shared?.destroy();
    }
  }

  AtlasVaultDeviceIdentitySecret secretBundle() {
    return AtlasVaultDeviceIdentitySecret._(
      deviceId: deviceId,
      createdAt: descriptor.createdAt,
      keyEpoch: descriptor.keyEpoch,
      signingPrivateKey: _signingPrivateSeed,
      agreementPrivateKey: _agreementPrivateKey,
    );
  }

  void destroy() {
    _signingPrivateSeed.fillRange(0, _signingPrivateSeed.length, 0);
    _agreementPrivateKey.fillRange(0, _agreementPrivateKey.length, 0);
  }

  @override
  String toString() => 'AtlasVaultDeviceIdentity(<redacted>)';
}

Future<AtlasVaultDeviceIdentity> generateAtlasVaultDeviceIdentity({
  String? createdAt,
  int keyEpoch = 1,
}) async {
  Uint8List? signing;
  Uint8List? agreement;
  try {
    signing = Uint8List.fromList(
      await (await Ed25519().newKeyPair()).extractPrivateKeyBytes(),
    );
    agreement = Uint8List.fromList(
      await (await X25519().newKeyPair()).extractPrivateKeyBytes(),
    );
    return AtlasVaultDeviceIdentity.fromPrivateKeys(
      signingPrivateSeed: signing,
      agreementPrivateKey: agreement,
      createdAt: createdAt ?? _utcNowSeconds(),
      keyEpoch: keyEpoch,
    );
  } catch (_) {
    throw const AtlasVaultDeviceIdentityException();
  } finally {
    signing?.fillRange(0, signing.length, 0);
    agreement?.fillRange(0, agreement.length, 0);
  }
}

Future<String> deriveAtlasVaultDeviceId(
  Uint8List signingPublicKey,
  Uint8List agreementPublicKey,
) async {
  try {
    return _deriveDeviceIdSync(signingPublicKey, agreementPublicKey);
  } catch (_) {
    throw const AtlasVaultDeviceIdentityException();
  }
}

String _deriveDeviceIdSync(
  Uint8List signingPublicKey,
  Uint8List agreementPublicKey,
) {
  if (signingPublicKey.length != _keyLength ||
      agreementPublicKey.length != _keyLength) {
    throw const AtlasVaultDeviceIdentityException();
  }
  final digest = const DartSha256().hashSync(
    _concat(<List<int>>[
      utf8.encode(_deviceIdDomain),
      signingPublicKey,
      agreementPublicKey,
    ]),
  );
  final hex = digest.bytes
      .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
      .join();
  return 'avd1-$hex';
}

String _utcNowSeconds() {
  final value = DateTime.now().toUtc().toIso8601String();
  return '${value.substring(0, 19)}Z';
}

Uint8List _concat(List<List<int>> parts) {
  final length = parts.fold<int>(0, (sum, part) => sum + part.length);
  final output = Uint8List(length);
  var offset = 0;
  for (final part in parts) {
    output.setRange(offset, offset + part.length, part);
    offset += part.length;
  }
  return output;
}

bool _bytesEqual(List<int> left, List<int> right) {
  if (left.length != right.length) {
    return false;
  }
  var difference = 0;
  for (var index = 0; index < left.length; index++) {
    difference |= left[index] ^ right[index];
  }
  return difference == 0;
}

bool _constantTimeTextEquals(String left, String right) {
  return _bytesEqual(utf8.encode(left), utf8.encode(right));
}

bool _isAllZero(List<int> value) {
  var combined = 0;
  for (final byte in value) {
    combined |= byte;
  }
  return combined == 0;
}
