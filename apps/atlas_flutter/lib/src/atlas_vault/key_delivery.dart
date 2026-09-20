import 'dart:convert';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';

import 'canonical_json.dart';
import 'crypto.dart';
import 'device_identity.dart';
import 'models.dart';
import 'pairing.dart';
import 'protected_state_bounds.dart';
import 'strict_values.dart';

const _requestFormat = 'atlasvault-pairing-key-request';
const _signedRequestFormat = 'atlasvault-signed-pairing-key-request';
const _bootstrapFormat = 'atlasvault-pairing-bootstrap';
const _deliveryFormat = 'atlasvault-vault-key-delivery';
const _signedDeliveryFormat = 'atlasvault-signed-vault-key-delivery';
const _acknowledgementFormat = 'atlasvault-pairing-acknowledgement';
const _signedAcknowledgementFormat =
    'atlasvault-signed-pairing-acknowledgement';
const _artifactFormat = 'atlasvault-pairing-artifact';
const _deliveryAadFormat = 'atlasvault-vault-key-delivery-aad';
const _version = 1;
const _keyLength = 32;
const _requestNonceLength = 32;
const _aeadNonceLength = 12;
const _deliveryCiphertextLength = 48;
const _signatureLength = 64;
const _maximumRequestLifetimeSeconds = 1800;
const _maximumClockSkewSeconds = 120;
const _sasDomain = 'atlasvault-pairing-sas-v1:';
const _requestSignatureDomain = 'atlasvault-pairing-key-request-signature-v1:';
const _deliverySignatureDomain = 'atlasvault-vault-key-delivery-signature-v1:';
const _acknowledgementSignatureDomain =
    'atlasvault-pairing-acknowledgement-signature-v1:';
const _deliveryInfo = 'atlasvault-vault-key-delivery-v1';

final class AtlasVaultKeyDeliveryException implements Exception {
  const AtlasVaultKeyDeliveryException();

  @override
  String toString() => 'AtlasVault pairing key delivery verification failed.';
}

final class AtlasVaultPairingKeyRequest {
  AtlasVaultPairingKeyRequest._({
    required this.requestId,
    required this.transcriptSha256,
    required this.inviterDeviceId,
    required this.inviteeDeviceId,
    required Uint8List inviteeEphemeralPublicKey,
    required Uint8List nonce,
    required this.issuedAt,
    required this.expiresAt,
  }) : _inviteeEphemeralPublicKey = Uint8List.fromList(
         inviteeEphemeralPublicKey,
       ),
       _nonce = Uint8List.fromList(nonce);

  final String requestId;
  final String transcriptSha256;
  final String inviterDeviceId;
  final String inviteeDeviceId;
  final Uint8List _inviteeEphemeralPublicKey;
  final Uint8List _nonce;
  final String issuedAt;
  final String expiresAt;

  Uint8List get inviteeEphemeralPublicKey =>
      Uint8List.fromList(_inviteeEphemeralPublicKey);
  Uint8List get nonce => Uint8List.fromList(_nonce);

  factory AtlasVaultPairingKeyRequest.fromJson(Map<String, Object?> source) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'request_id',
          'transcript_sha256',
          'inviter_device_id',
          'invitee_device_id',
          'invitee_ephemeral_public_key',
          'nonce',
          'issued_at',
          'expires_at',
        },
        context: 'Pairing key request',
      );
      if (value['format'] != _requestFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _version) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final inviter = _deviceId(value['inviter_device_id']);
      final invitee = _deviceId(value['invitee_device_id']);
      final issuedAt = requireAtlasVaultUtcSeconds(
        value['issued_at'],
        field: 'issued_at',
      );
      final expiresAt = requireAtlasVaultUtcSeconds(
        value['expires_at'],
        field: 'expires_at',
      );
      final lifetime = _time(expiresAt).difference(_time(issuedAt)).inSeconds;
      if (_textEquals(inviter, invitee) ||
          lifetime <= 0 ||
          lifetime > _maximumRequestLifetimeSeconds) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return AtlasVaultPairingKeyRequest._(
        requestId: requireAtlasVaultCanonicalUuid(
          value['request_id'],
          field: 'request_id',
        ),
        transcriptSha256: _sha256(value['transcript_sha256']),
        inviterDeviceId: inviter,
        inviteeDeviceId: invitee,
        inviteeEphemeralPublicKey: requireAtlasVaultCanonicalBase64(
          value['invitee_ephemeral_public_key'],
          field: 'invitee_ephemeral_public_key',
          exactLength: _keyLength,
        ),
        nonce: requireAtlasVaultCanonicalBase64(
          value['nonce'],
          field: 'nonce',
          exactLength: _requestNonceLength,
        ),
        issuedAt: issuedAt,
        expiresAt: expiresAt,
      );
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _requestFormat,
    'version': _version,
    'request_id': requestId,
    'transcript_sha256': transcriptSha256,
    'inviter_device_id': inviterDeviceId,
    'invitee_device_id': inviteeDeviceId,
    'invitee_ephemeral_public_key': base64Encode(_inviteeEphemeralPublicKey),
    'nonce': base64Encode(_nonce),
    'issued_at': issuedAt,
    'expires_at': expiresAt,
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

final class AtlasVaultSignedPairingKeyRequest {
  AtlasVaultSignedPairingKeyRequest._({
    required this.request,
    required this.invitee,
    required Uint8List signature,
  }) : _signature = Uint8List.fromList(signature);

  final AtlasVaultPairingKeyRequest request;
  final AtlasVaultSignedDeviceDescriptor invitee;
  final Uint8List _signature;

  Uint8List get signature => Uint8List.fromList(_signature);

  factory AtlasVaultSignedPairingKeyRequest.fromJson(
    Map<String, Object?> source,
  ) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'request',
          'invitee',
          'signature',
        },
        context: 'Signed pairing key request',
      );
      if (value['format'] != _signedRequestFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _version) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final request = AtlasVaultPairingKeyRequest.fromJson(
        requireAtlasVaultObject(value['request'], context: 'request'),
      );
      final invitee = AtlasVaultSignedDeviceDescriptor.fromJson(
        requireAtlasVaultObject(value['invitee'], context: 'invitee'),
      );
      if (!_textEquals(request.inviteeDeviceId, invitee.descriptor.deviceId)) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return AtlasVaultSignedPairingKeyRequest._(
        request: request,
        invitee: invitee,
        signature: requireAtlasVaultCanonicalBase64(
          value['signature'],
          field: 'signature',
          exactLength: _signatureLength,
        ),
      );
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  factory AtlasVaultSignedPairingKeyRequest.fromCanonicalBytes(
    Uint8List bytes,
  ) {
    try {
      final input = Uint8List.fromList(bytes);
      final decoded = AtlasVaultSignedPairingKeyRequest.fromJson(
        _decodeObject(input),
      );
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _signedRequestFormat,
    'version': _version,
    'request': request.toJson(),
    'invitee': invitee.toJson(),
    'signature': base64Encode(_signature),
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

Future<AtlasVaultSignedPairingKeyRequest> createAtlasVaultPairingKeyRequest({
  required AtlasVaultDeviceIdentity invitee,
  required String requestId,
  required Uint8List transcriptSha256,
  required String inviterDeviceId,
  required Uint8List inviteeEphemeralPublicKey,
  required Uint8List nonce,
  required String issuedAt,
  required String expiresAt,
}) async {
  try {
    final request = AtlasVaultPairingKeyRequest.fromJson(<String, Object?>{
      'format': _requestFormat,
      'version': _version,
      'request_id': requestId,
      'transcript_sha256': _hex(_copyExact(transcriptSha256, _keyLength)),
      'inviter_device_id': inviterDeviceId,
      'invitee_device_id': invitee.deviceId,
      'invitee_ephemeral_public_key': base64Encode(
        _copyExact(inviteeEphemeralPublicKey, _keyLength),
      ),
      'nonce': base64Encode(_copyExact(nonce, _requestNonceLength)),
      'issued_at': issuedAt,
      'expires_at': expiresAt,
    });
    return AtlasVaultSignedPairingKeyRequest._(
      request: request,
      invitee: await invitee.signDescriptor(),
      signature: await invitee.signBytes(<int>[
        ...utf8.encode(_requestSignatureDomain),
        ...request.canonicalBytes(),
      ]),
    );
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  }
}

Future<AtlasVaultPairingKeyRequest> verifyAtlasVaultPairingKeyRequest(
  AtlasVaultSignedPairingKeyRequest signed, {
  required Uint8List transcriptSha256,
  required String inviterDeviceId,
  required String inviteeDeviceId,
  required String currentTime,
}) async {
  try {
    await _verifySignedDescriptorPayload(
      signed.invitee,
      signed.signature,
      _requestSignatureDomain,
      signed.request.canonicalBytes(),
    );
    final request = signed.request;
    final now = _time(
      requireAtlasVaultUtcSeconds(currentTime, field: 'current_time'),
    );
    if (!_textEquals(request.transcriptSha256, _hex(transcriptSha256)) ||
        !_textEquals(request.inviterDeviceId, _deviceId(inviterDeviceId)) ||
        !_textEquals(request.inviteeDeviceId, _deviceId(inviteeDeviceId)) ||
        !_textEquals(
          request.inviteeDeviceId,
          signed.invitee.descriptor.deviceId,
        ) ||
        !now.isBefore(_time(request.expiresAt)) ||
        _time(
          request.issuedAt,
        ).isAfter(now.add(const Duration(seconds: _maximumClockSkewSeconds)))) {
      throw const AtlasVaultKeyDeliveryException();
    }
    return request;
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  }
}

final class AtlasVaultPairingBootstrap {
  AtlasVaultPairingBootstrap._({
    required this.snapshotId,
    required this.createdAt,
    required this.vaultMetadata,
    required List<AtlasVaultEncryptedRecord> records,
  }) : records = List<AtlasVaultEncryptedRecord>.unmodifiable(records);

  final String snapshotId;
  final String createdAt;
  final AtlasVaultMetadata vaultMetadata;
  final List<AtlasVaultEncryptedRecord> records;

  factory AtlasVaultPairingBootstrap.fromJson(Map<String, Object?> source) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'snapshot_id',
          'created_at',
          'vault_metadata',
          'records',
        },
        context: 'Pairing bootstrap',
      );
      if (value['format'] != _bootstrapFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _version) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final records = <AtlasVaultEncryptedRecord>[
        for (final item in requireAtlasVaultList(
          value['records'],
          field: 'records',
        ))
          AtlasVaultEncryptedRecord.fromJson(
            requireAtlasVaultObject(item, context: 'encrypted record'),
          ),
      ];
      if (records.map((record) => record.id).toSet().length != records.length) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final bootstrap = AtlasVaultPairingBootstrap._(
        snapshotId: requireAtlasVaultCanonicalUuid(
          value['snapshot_id'],
          field: 'snapshot_id',
        ),
        createdAt: requireAtlasVaultUtcSeconds(
          value['created_at'],
          field: 'created_at',
        ),
        vaultMetadata: AtlasVaultMetadata.fromJson(
          requireAtlasVaultObject(
            value['vault_metadata'],
            context: 'vault metadata',
          ),
        ),
        records: records,
      );
      _requirePrintableAsciiJson(bootstrap.toJson());
      return bootstrap;
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  factory AtlasVaultPairingBootstrap.fromCanonicalBytes(Uint8List bytes) {
    try {
      requireAtlasVaultProtectedStateByteCount(
        AtlasVaultProtectedStateCategory.pairingBootstrap,
        bytes.length,
      );
      final input = Uint8List.fromList(bytes);
      final decoded = AtlasVaultPairingBootstrap.fromJson(_decodeObject(input));
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _bootstrapFormat,
    'version': _version,
    'snapshot_id': snapshotId,
    'created_at': createdAt,
    'vault_metadata': vaultMetadata.toJson(),
    'records': <Object?>[for (final record in records) record.toJson()],
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

final class AtlasVaultVaultKeyDelivery {
  AtlasVaultVaultKeyDelivery._({
    required this.deliveryId,
    required this.transcriptSha256,
    required this.inviterDeviceId,
    required this.inviteeDeviceId,
    required this.requestSha256,
    required this.vaultId,
    required this.keyEpoch,
    required this.bootstrapSha256,
    required Uint8List inviterEphemeralPublicKey,
    required Uint8List nonce,
    required Uint8List ciphertext,
    required this.expiresAt,
  }) : _inviterEphemeralPublicKey = Uint8List.fromList(
         inviterEphemeralPublicKey,
       ),
       _nonce = Uint8List.fromList(nonce),
       _ciphertext = Uint8List.fromList(ciphertext);

  final String deliveryId;
  final String transcriptSha256;
  final String inviterDeviceId;
  final String inviteeDeviceId;
  final String requestSha256;
  final String vaultId;
  final int keyEpoch;
  final String bootstrapSha256;
  final Uint8List _inviterEphemeralPublicKey;
  final Uint8List _nonce;
  final Uint8List _ciphertext;
  final String expiresAt;

  Uint8List get inviterEphemeralPublicKey =>
      Uint8List.fromList(_inviterEphemeralPublicKey);
  Uint8List get nonce => Uint8List.fromList(_nonce);
  Uint8List get ciphertext => Uint8List.fromList(_ciphertext);

  factory AtlasVaultVaultKeyDelivery.fromJson(Map<String, Object?> source) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'delivery_id',
          'transcript_sha256',
          'inviter_device_id',
          'invitee_device_id',
          'request_sha256',
          'vault_id',
          'key_epoch',
          'bootstrap_sha256',
          'inviter_ephemeral_public_key',
          'nonce',
          'ciphertext',
          'expires_at',
        },
        context: 'Vault-key delivery',
      );
      if (value['format'] != _deliveryFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _version) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final inviter = _deviceId(value['inviter_device_id']);
      final invitee = _deviceId(value['invitee_device_id']);
      final epoch = requireAtlasVaultInt(
        value['key_epoch'],
        field: 'key_epoch',
      );
      if (_textEquals(inviter, invitee) ||
          epoch <= 0 ||
          epoch > atlasVaultMaximumDeviceKeyEpoch) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return AtlasVaultVaultKeyDelivery._(
        deliveryId: requireAtlasVaultCanonicalUuid(
          value['delivery_id'],
          field: 'delivery_id',
        ),
        transcriptSha256: _sha256(value['transcript_sha256']),
        inviterDeviceId: inviter,
        inviteeDeviceId: invitee,
        requestSha256: _sha256(value['request_sha256']),
        vaultId: requireAtlasVaultVaultId(value['vault_id']),
        keyEpoch: epoch,
        bootstrapSha256: _sha256(value['bootstrap_sha256']),
        inviterEphemeralPublicKey: requireAtlasVaultCanonicalBase64(
          value['inviter_ephemeral_public_key'],
          field: 'inviter_ephemeral_public_key',
          exactLength: _keyLength,
        ),
        nonce: requireAtlasVaultCanonicalBase64(
          value['nonce'],
          field: 'nonce',
          exactLength: _aeadNonceLength,
        ),
        ciphertext: requireAtlasVaultCanonicalBase64(
          value['ciphertext'],
          field: 'ciphertext',
          exactLength: _deliveryCiphertextLength,
        ),
        expiresAt: requireAtlasVaultUtcSeconds(
          value['expires_at'],
          field: 'expires_at',
        ),
      );
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _deliveryFormat,
    'version': _version,
    'delivery_id': deliveryId,
    'transcript_sha256': transcriptSha256,
    'inviter_device_id': inviterDeviceId,
    'invitee_device_id': inviteeDeviceId,
    'request_sha256': requestSha256,
    'vault_id': vaultId,
    'key_epoch': keyEpoch,
    'bootstrap_sha256': bootstrapSha256,
    'inviter_ephemeral_public_key': base64Encode(_inviterEphemeralPublicKey),
    'nonce': base64Encode(_nonce),
    'ciphertext': base64Encode(_ciphertext),
    'expires_at': expiresAt,
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  Uint8List aad() => encodeCanonicalJson(<String, Object?>{
    'format': _deliveryAadFormat,
    'version': _version,
    'delivery_id': deliveryId,
    'transcript_sha256': transcriptSha256,
    'inviter_device_id': inviterDeviceId,
    'invitee_device_id': inviteeDeviceId,
    'request_sha256': requestSha256,
    'vault_id': vaultId,
    'key_epoch': keyEpoch,
    'bootstrap_sha256': bootstrapSha256,
    'expires_at': expiresAt,
  });
}

final class AtlasVaultSignedVaultKeyDelivery {
  AtlasVaultSignedVaultKeyDelivery._({
    required this.delivery,
    required this.inviter,
    required Uint8List signature,
  }) : _signature = Uint8List.fromList(signature);

  final AtlasVaultVaultKeyDelivery delivery;
  final AtlasVaultSignedDeviceDescriptor inviter;
  final Uint8List _signature;

  Uint8List get signature => Uint8List.fromList(_signature);

  factory AtlasVaultSignedVaultKeyDelivery.fromJson(
    Map<String, Object?> source,
  ) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'delivery',
          'inviter',
          'signature',
        },
        context: 'Signed vault-key delivery',
      );
      if (value['format'] != _signedDeliveryFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _version) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final delivery = AtlasVaultVaultKeyDelivery.fromJson(
        requireAtlasVaultObject(value['delivery'], context: 'delivery'),
      );
      final inviter = AtlasVaultSignedDeviceDescriptor.fromJson(
        requireAtlasVaultObject(value['inviter'], context: 'inviter'),
      );
      if (!_textEquals(delivery.inviterDeviceId, inviter.descriptor.deviceId)) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return AtlasVaultSignedVaultKeyDelivery._(
        delivery: delivery,
        inviter: inviter,
        signature: requireAtlasVaultCanonicalBase64(
          value['signature'],
          field: 'signature',
          exactLength: _signatureLength,
        ),
      );
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  factory AtlasVaultSignedVaultKeyDelivery.fromCanonicalBytes(Uint8List bytes) {
    try {
      final input = Uint8List.fromList(bytes);
      final decoded = AtlasVaultSignedVaultKeyDelivery.fromJson(
        _decodeObject(input),
      );
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _signedDeliveryFormat,
    'version': _version,
    'delivery': delivery.toJson(),
    'inviter': inviter.toJson(),
    'signature': base64Encode(_signature),
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

Future<AtlasVaultVaultKeyDelivery> verifyAtlasVaultSignedVaultKeyDelivery(
  AtlasVaultSignedVaultKeyDelivery signed,
) async {
  try {
    await _verifySignedDescriptorPayload(
      signed.inviter,
      signed.signature,
      _deliverySignatureDomain,
      signed.delivery.canonicalBytes(),
    );
    return signed.delivery;
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  }
}

Future<String> deriveAtlasVaultPairingSas(
  Uint8List pairingSessionKey,
  Uint8List transcriptSha256,
) async {
  SecretKeyData? key;
  try {
    key = SecretKeyData(
      _copyExact(pairingSessionKey, _keyLength),
      overwriteWhenDestroyed: true,
    );
    final mac = await Hmac.sha256().calculateMac(<int>[
      ...utf8.encode(_sasDomain),
      ..._copyExact(transcriptSha256, 32),
    ], secretKey: key);
    final rendered = _hex(
      Uint8List.fromList(mac.bytes.sublist(0, 6)),
    ).toUpperCase();
    return '${rendered.substring(0, 4)}-${rendered.substring(4, 8)}-'
        '${rendered.substring(8, 12)}';
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  } finally {
    key?.destroy();
  }
}

Future<AtlasVaultSignedVaultKeyDelivery> createAtlasVaultKeyDelivery({
  required AtlasVaultDeviceIdentity inviter,
  required AtlasVaultSignedPairingKeyRequest keyRequest,
  required Uint8List transcriptSha256,
  required AtlasVaultPairingBootstrap bootstrap,
  required Uint8List vaultKey,
  required String deliveryId,
  required int keyEpoch,
  required String expiresAt,
}) async {
  try {
    return _createAtlasVaultKeyDelivery(
      inviter: inviter,
      keyRequest: keyRequest,
      transcriptSha256: transcriptSha256,
      bootstrap: bootstrap,
      vaultKey: vaultKey,
      inviterEphemeralPrivateKey: await X25519().newKeyPair(),
      nonce: Uint8List.fromList(AesGcm.with256bits().newNonce()),
      deliveryId: deliveryId,
      keyEpoch: keyEpoch,
      expiresAt: expiresAt,
    );
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  }
}

Future<AtlasVaultSignedVaultKeyDelivery> createAtlasVaultKeyDeliveryForTesting({
  required AtlasVaultDeviceIdentity inviter,
  required AtlasVaultSignedPairingKeyRequest keyRequest,
  required Uint8List transcriptSha256,
  required AtlasVaultPairingBootstrap bootstrap,
  required Uint8List vaultKey,
  required Uint8List inviterEphemeralPrivateKey,
  required Uint8List nonce,
  required String deliveryId,
  required int keyEpoch,
  required String expiresAt,
}) async {
  Uint8List? ephemeralPrivateKey;
  try {
    ephemeralPrivateKey = _copyExact(inviterEphemeralPrivateKey, _keyLength);
    return _createAtlasVaultKeyDelivery(
      inviter: inviter,
      keyRequest: keyRequest,
      transcriptSha256: transcriptSha256,
      bootstrap: bootstrap,
      vaultKey: vaultKey,
      inviterEphemeralPrivateKey: await X25519().newKeyPairFromSeed(
        ephemeralPrivateKey,
      ),
      nonce: nonce,
      deliveryId: deliveryId,
      keyEpoch: keyEpoch,
      expiresAt: expiresAt,
    );
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  } finally {
    atlasVaultWipeBytesInternal(ephemeralPrivateKey);
  }
}

Future<AtlasVaultSignedVaultKeyDelivery> _createAtlasVaultKeyDelivery({
  required AtlasVaultDeviceIdentity inviter,
  required AtlasVaultSignedPairingKeyRequest keyRequest,
  required Uint8List transcriptSha256,
  required AtlasVaultPairingBootstrap bootstrap,
  required Uint8List vaultKey,
  required SimpleKeyPair inviterEphemeralPrivateKey,
  required Uint8List nonce,
  required String deliveryId,
  required int keyEpoch,
  required String expiresAt,
}) async {
  Uint8List? shared;
  Uint8List? deliveryKey;
  Uint8List? vaultKeyCopy;
  try {
    final transcript = _copyExact(transcriptSha256, _keyLength);
    final request = keyRequest.request;
    if (!_textEquals(request.transcriptSha256, _hex(transcript)) ||
        !_textEquals(request.inviterDeviceId, inviter.deviceId) ||
        !_textEquals(expiresAt, request.expiresAt)) {
      throw const AtlasVaultKeyDeliveryException();
    }
    await _verifySignedDescriptorPayload(
      keyRequest.invitee,
      keyRequest.signature,
      _requestSignatureDomain,
      request.canonicalBytes(),
    );
    final privateKey = inviterEphemeralPrivateKey;
    final publicKey = await privateKey.extractPublicKey();
    final secret = await X25519().sharedSecretKey(
      keyPair: privateKey,
      remotePublicKey: SimplePublicKey(
        request.inviteeEphemeralPublicKey,
        type: KeyPairType.x25519,
      ),
    );
    shared = Uint8List.fromList(await secret.extractBytes());
    secret.destroy();
    deliveryKey = await _deriveDeliveryKey(shared, transcript);
    final requestHash = await atlasVaultSha256Hex(keyRequest.canonicalBytes());
    final bootstrapHash = await atlasVaultSha256Hex(bootstrap.canonicalBytes());
    final template = AtlasVaultVaultKeyDelivery.fromJson(<String, Object?>{
      'format': _deliveryFormat,
      'version': _version,
      'delivery_id': deliveryId,
      'transcript_sha256': _hex(transcript),
      'inviter_device_id': inviter.deviceId,
      'invitee_device_id': request.inviteeDeviceId,
      'request_sha256': requestHash,
      'vault_id': bootstrap.vaultMetadata.vaultId,
      'key_epoch': keyEpoch,
      'bootstrap_sha256': bootstrapHash,
      'inviter_ephemeral_public_key': base64Encode(publicKey.bytes),
      'nonce': base64Encode(_copyExact(nonce, _aeadNonceLength)),
      'ciphertext': base64Encode(Uint8List(_deliveryCiphertextLength)),
      'expires_at': expiresAt,
    });
    vaultKeyCopy = _copyExact(vaultKey, _keyLength);
    final ciphertext = await atlasVaultSealAes256GcmInternal(
      plaintext: vaultKeyCopy,
      key: deliveryKey,
      nonce: template.nonce,
      aad: template.aad(),
    );
    final delivery = AtlasVaultVaultKeyDelivery.fromJson(<String, Object?>{
      ...template.toJson(),
      'ciphertext': base64Encode(ciphertext),
    });
    return AtlasVaultSignedVaultKeyDelivery._(
      delivery: delivery,
      inviter: await inviter.signDescriptor(),
      signature: await inviter.signBytes(<int>[
        ...utf8.encode(_deliverySignatureDomain),
        ...delivery.canonicalBytes(),
      ]),
    );
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  } finally {
    atlasVaultWipeBytesInternal(shared);
    atlasVaultWipeBytesInternal(deliveryKey);
    atlasVaultWipeBytesInternal(vaultKeyCopy);
  }
}

Future<Uint8List> openAtlasVaultKeyDelivery(
  AtlasVaultSignedVaultKeyDelivery signed, {
  required AtlasVaultSignedPairingKeyRequest keyRequest,
  required Uint8List inviteeEphemeralPrivateKey,
  required AtlasVaultPairingBootstrap bootstrap,
  required Uint8List transcriptSha256,
  required String currentTime,
}) async {
  Uint8List? shared;
  Uint8List? deliveryKey;
  Uint8List? plaintext;
  Uint8List? ephemeralPrivateKey;
  try {
    final value = await verifyAtlasVaultSignedVaultKeyDelivery(signed);
    final transcript = _copyExact(transcriptSha256, _keyLength);
    final request = await verifyAtlasVaultPairingKeyRequest(
      keyRequest,
      transcriptSha256: transcript,
      inviterDeviceId: value.inviterDeviceId,
      inviteeDeviceId: value.inviteeDeviceId,
      currentTime: currentTime,
    );
    if (!_time(currentTime).isBefore(_time(value.expiresAt)) ||
        !_textEquals(value.expiresAt, request.expiresAt) ||
        !_textEquals(value.transcriptSha256, _hex(transcript)) ||
        !_textEquals(
          value.requestSha256,
          await atlasVaultSha256Hex(keyRequest.canonicalBytes()),
        ) ||
        !_textEquals(value.vaultId, bootstrap.vaultMetadata.vaultId) ||
        !_textEquals(
          value.bootstrapSha256,
          await atlasVaultSha256Hex(bootstrap.canonicalBytes()),
        )) {
      throw const AtlasVaultKeyDeliveryException();
    }
    ephemeralPrivateKey = _copyExact(inviteeEphemeralPrivateKey, _keyLength);
    final privateKey = await X25519().newKeyPairFromSeed(ephemeralPrivateKey);
    final secret = await X25519().sharedSecretKey(
      keyPair: privateKey,
      remotePublicKey: SimplePublicKey(
        value.inviterEphemeralPublicKey,
        type: KeyPairType.x25519,
      ),
    );
    shared = Uint8List.fromList(await secret.extractBytes());
    secret.destroy();
    deliveryKey = await _deriveDeliveryKey(shared, transcript);
    plaintext = await atlasVaultOpenAes256GcmInternal(
      ciphertextAndTag: value.ciphertext,
      key: deliveryKey,
      nonce: value.nonce,
      aad: value.aad(),
    );
    return Uint8List.fromList(_copyExact(plaintext, _keyLength));
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  } finally {
    atlasVaultWipeBytesInternal(shared);
    atlasVaultWipeBytesInternal(deliveryKey);
    atlasVaultWipeBytesInternal(plaintext);
    atlasVaultWipeBytesInternal(ephemeralPrivateKey);
  }
}

void _requirePrintableAsciiJson(Object? value) {
  if (value == null || value is bool || value is int) {
    return;
  }
  if (value is String) {
    if (value.isEmpty ||
        value.codeUnits.any((unit) => unit < 0x20 || unit > 0x7e)) {
      throw const AtlasVaultKeyDeliveryException();
    }
    return;
  }
  if (value is List<Object?>) {
    for (final item in value) {
      _requirePrintableAsciiJson(item);
    }
    return;
  }
  if (value is Map<String, Object?>) {
    for (final entry in value.entries) {
      _requirePrintableAsciiJson(entry.key);
      _requirePrintableAsciiJson(entry.value);
    }
    return;
  }
  throw const AtlasVaultKeyDeliveryException();
}

final class AtlasVaultPairingAcknowledgement {
  AtlasVaultPairingAcknowledgement._({
    required this.acknowledgementId,
    required this.deliveryId,
    required this.transcriptSha256,
    required this.inviterDeviceId,
    required this.inviteeDeviceId,
    required this.vaultId,
    required this.keyEpoch,
    required this.bootstrapSha256,
    required this.installedAt,
  });

  final String acknowledgementId;
  final String deliveryId;
  final String transcriptSha256;
  final String inviterDeviceId;
  final String inviteeDeviceId;
  final String vaultId;
  final int keyEpoch;
  final String bootstrapSha256;
  final String installedAt;

  factory AtlasVaultPairingAcknowledgement.fromJson(
    Map<String, Object?> source,
  ) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'acknowledgement_id',
          'delivery_id',
          'transcript_sha256',
          'inviter_device_id',
          'invitee_device_id',
          'vault_id',
          'key_epoch',
          'bootstrap_sha256',
          'installed_at',
        },
        context: 'Pairing acknowledgement',
      );
      if (value['format'] != _acknowledgementFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _version) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final inviter = _deviceId(value['inviter_device_id']);
      final invitee = _deviceId(value['invitee_device_id']);
      final epoch = requireAtlasVaultInt(
        value['key_epoch'],
        field: 'key_epoch',
      );
      if (_textEquals(inviter, invitee) ||
          epoch <= 0 ||
          epoch > atlasVaultMaximumDeviceKeyEpoch) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return AtlasVaultPairingAcknowledgement._(
        acknowledgementId: requireAtlasVaultCanonicalUuid(
          value['acknowledgement_id'],
          field: 'acknowledgement_id',
        ),
        deliveryId: requireAtlasVaultCanonicalUuid(
          value['delivery_id'],
          field: 'delivery_id',
        ),
        transcriptSha256: _sha256(value['transcript_sha256']),
        inviterDeviceId: inviter,
        inviteeDeviceId: invitee,
        vaultId: requireAtlasVaultVaultId(value['vault_id']),
        keyEpoch: epoch,
        bootstrapSha256: _sha256(value['bootstrap_sha256']),
        installedAt: requireAtlasVaultUtcSeconds(
          value['installed_at'],
          field: 'installed_at',
        ),
      );
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _acknowledgementFormat,
    'version': _version,
    'acknowledgement_id': acknowledgementId,
    'delivery_id': deliveryId,
    'transcript_sha256': transcriptSha256,
    'inviter_device_id': inviterDeviceId,
    'invitee_device_id': inviteeDeviceId,
    'vault_id': vaultId,
    'key_epoch': keyEpoch,
    'bootstrap_sha256': bootstrapSha256,
    'installed_at': installedAt,
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

final class AtlasVaultSignedPairingAcknowledgement {
  AtlasVaultSignedPairingAcknowledgement._({
    required this.acknowledgement,
    required this.invitee,
    required Uint8List signature,
  }) : _signature = Uint8List.fromList(signature);

  final AtlasVaultPairingAcknowledgement acknowledgement;
  final AtlasVaultSignedDeviceDescriptor invitee;
  final Uint8List _signature;

  Uint8List get signature => Uint8List.fromList(_signature);

  factory AtlasVaultSignedPairingAcknowledgement.fromJson(
    Map<String, Object?> source,
  ) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'acknowledgement',
          'invitee',
          'signature',
        },
        context: 'Signed pairing acknowledgement',
      );
      if (value['format'] != _signedAcknowledgementFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _version) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final acknowledgement = AtlasVaultPairingAcknowledgement.fromJson(
        requireAtlasVaultObject(
          value['acknowledgement'],
          context: 'acknowledgement',
        ),
      );
      final invitee = AtlasVaultSignedDeviceDescriptor.fromJson(
        requireAtlasVaultObject(value['invitee'], context: 'invitee'),
      );
      if (!_textEquals(
        acknowledgement.inviteeDeviceId,
        invitee.descriptor.deviceId,
      )) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return AtlasVaultSignedPairingAcknowledgement._(
        acknowledgement: acknowledgement,
        invitee: invitee,
        signature: requireAtlasVaultCanonicalBase64(
          value['signature'],
          field: 'signature',
          exactLength: _signatureLength,
        ),
      );
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  factory AtlasVaultSignedPairingAcknowledgement.fromCanonicalBytes(
    Uint8List bytes,
  ) {
    try {
      final input = Uint8List.fromList(bytes);
      final decoded = AtlasVaultSignedPairingAcknowledgement.fromJson(
        _decodeObject(input),
      );
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _signedAcknowledgementFormat,
    'version': _version,
    'acknowledgement': acknowledgement.toJson(),
    'invitee': invitee.toJson(),
    'signature': base64Encode(_signature),
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

Future<AtlasVaultSignedPairingAcknowledgement>
createAtlasVaultPairingAcknowledgement({
  required AtlasVaultDeviceIdentity invitee,
  required String acknowledgementId,
  required AtlasVaultSignedVaultKeyDelivery delivery,
  required String installedAt,
}) async {
  try {
    final value = delivery.delivery;
    if (!_textEquals(invitee.deviceId, value.inviteeDeviceId)) {
      throw const AtlasVaultKeyDeliveryException();
    }
    final acknowledgement =
        AtlasVaultPairingAcknowledgement.fromJson(<String, Object?>{
          'format': _acknowledgementFormat,
          'version': _version,
          'acknowledgement_id': acknowledgementId,
          'delivery_id': value.deliveryId,
          'transcript_sha256': value.transcriptSha256,
          'inviter_device_id': value.inviterDeviceId,
          'invitee_device_id': value.inviteeDeviceId,
          'vault_id': value.vaultId,
          'key_epoch': value.keyEpoch,
          'bootstrap_sha256': value.bootstrapSha256,
          'installed_at': installedAt,
        });
    return AtlasVaultSignedPairingAcknowledgement._(
      acknowledgement: acknowledgement,
      invitee: await invitee.signDescriptor(),
      signature: await invitee.signBytes(<int>[
        ...utf8.encode(_acknowledgementSignatureDomain),
        ...acknowledgement.canonicalBytes(),
      ]),
    );
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  }
}

Future<AtlasVaultPairingAcknowledgement> verifyAtlasVaultPairingAcknowledgement(
  AtlasVaultSignedPairingAcknowledgement signed, {
  required AtlasVaultSignedVaultKeyDelivery delivery,
  required String inviterDeviceId,
  required String inviteeDeviceId,
}) async {
  try {
    await _verifySignedDescriptorPayload(
      signed.invitee,
      signed.signature,
      _acknowledgementSignatureDomain,
      signed.acknowledgement.canonicalBytes(),
    );
    final acknowledgement = signed.acknowledgement;
    final value = delivery.delivery;
    if (!_textEquals(acknowledgement.deliveryId, value.deliveryId) ||
        !_textEquals(
          acknowledgement.transcriptSha256,
          value.transcriptSha256,
        ) ||
        !_textEquals(
          acknowledgement.inviterDeviceId,
          _deviceId(inviterDeviceId),
        ) ||
        !_textEquals(
          acknowledgement.inviteeDeviceId,
          _deviceId(inviteeDeviceId),
        ) ||
        !_textEquals(acknowledgement.vaultId, value.vaultId) ||
        acknowledgement.keyEpoch != value.keyEpoch ||
        !_textEquals(acknowledgement.bootstrapSha256, value.bootstrapSha256)) {
      throw const AtlasVaultKeyDeliveryException();
    }
    return acknowledgement;
  } catch (_) {
    throw const AtlasVaultKeyDeliveryException();
  }
}

enum AtlasVaultPairingArtifactKind {
  offer,
  acceptance,
  delivery,
  acknowledgement;

  String get encoded => name;
}

final class AtlasVaultPairingArtifact {
  AtlasVaultPairingArtifact._({
    required this.kind,
    required Map<String, Object?> payload,
  }) : _payloadBytes = encodeCanonicalJson(payload);

  final AtlasVaultPairingArtifactKind kind;
  final Uint8List _payloadBytes;

  Map<String, Object?> get payload => _decodeObject(_payloadBytes);

  factory AtlasVaultPairingArtifact.fromJson(Map<String, Object?> source) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{'format', 'version', 'kind', 'payload'},
        context: 'Pairing artifact',
      );
      if (value['format'] != _artifactFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _version) {
        throw const AtlasVaultKeyDeliveryException();
      }
      final kindText = requireAtlasVaultString(
        value['kind'],
        field: 'kind',
        allowEmpty: false,
      );
      final kind = AtlasVaultPairingArtifactKind.values.singleWhere(
        (candidate) => candidate.encoded == kindText,
      );
      final payload = Map<String, Object?>.from(
        requireAtlasVaultObject(value['payload'], context: 'payload'),
      );
      _validateArtifactPayload(kind, payload);
      return AtlasVaultPairingArtifact._(kind: kind, payload: payload);
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  factory AtlasVaultPairingArtifact.fromCanonicalBytes(Uint8List bytes) {
    try {
      requireAtlasVaultStagedPairingArtifactByteCounts(<int>[bytes.length]);
      final input = Uint8List.fromList(bytes);
      final decoded = AtlasVaultPairingArtifact.fromJson(_decodeObject(input));
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultKeyDeliveryException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultKeyDeliveryException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _artifactFormat,
    'version': _version,
    'kind': kind.encoded,
    'payload': _decodeObject(_payloadBytes),
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

void _validateArtifactPayload(
  AtlasVaultPairingArtifactKind kind,
  Map<String, Object?> payload,
) {
  switch (kind) {
    case AtlasVaultPairingArtifactKind.offer:
      requireAtlasVaultExactKeys(
        payload,
        requiredKeys: const <String>{'signed_offer'},
        context: 'Offer artifact',
      );
      AtlasVaultSignedPairingOffer.fromJson(
        requireAtlasVaultObject(
          payload['signed_offer'],
          context: 'signed offer',
        ),
      );
    case AtlasVaultPairingArtifactKind.acceptance:
      requireAtlasVaultExactKeys(
        payload,
        requiredKeys: const <String>{
          'signed_acceptance',
          'signed_key_request',
          'invitee_proof',
        },
        context: 'Acceptance artifact',
      );
      AtlasVaultSignedPairingAcceptance.fromJson(
        requireAtlasVaultObject(
          payload['signed_acceptance'],
          context: 'signed acceptance',
        ),
      );
      AtlasVaultSignedPairingKeyRequest.fromJson(
        requireAtlasVaultObject(
          payload['signed_key_request'],
          context: 'signed key request',
        ),
      );
      requireAtlasVaultCanonicalBase64(
        payload['invitee_proof'],
        field: 'invitee_proof',
        exactLength: 32,
      );
    case AtlasVaultPairingArtifactKind.delivery:
      requireAtlasVaultExactKeys(
        payload,
        requiredKeys: const <String>{
          'signed_delivery',
          'bootstrap',
          'inviter_proof',
        },
        context: 'Delivery artifact',
      );
      AtlasVaultSignedVaultKeyDelivery.fromJson(
        requireAtlasVaultObject(
          payload['signed_delivery'],
          context: 'signed delivery',
        ),
      );
      AtlasVaultPairingBootstrap.fromJson(
        requireAtlasVaultObject(payload['bootstrap'], context: 'bootstrap'),
      );
      requireAtlasVaultCanonicalBase64(
        payload['inviter_proof'],
        field: 'inviter_proof',
        exactLength: 32,
      );
    case AtlasVaultPairingArtifactKind.acknowledgement:
      requireAtlasVaultExactKeys(
        payload,
        requiredKeys: const <String>{'signed_acknowledgement'},
        context: 'Acknowledgement artifact',
      );
      AtlasVaultSignedPairingAcknowledgement.fromJson(
        requireAtlasVaultObject(
          payload['signed_acknowledgement'],
          context: 'signed acknowledgement',
        ),
      );
  }
}

Future<Uint8List> _deriveDeliveryKey(
  Uint8List sharedSecret,
  Uint8List transcriptSha256,
) async {
  if (sharedSecret.length != _keyLength || _isAllZero(sharedSecret)) {
    throw const AtlasVaultKeyDeliveryException();
  }
  return atlasVaultDeriveHkdfSha256Internal(
    inputKeyMaterial: sharedSecret,
    salt: transcriptSha256,
    info: utf8.encode(_deliveryInfo),
  );
}

Future<void> _verifySignedDescriptorPayload(
  AtlasVaultSignedDeviceDescriptor signedDescriptor,
  Uint8List signature,
  String domain,
  Uint8List payload,
) async {
  final descriptor = await verifyAtlasVaultSignedDeviceDescriptor(
    signedDescriptor,
  );
  final verified = await Ed25519().verify(
    <int>[...utf8.encode(domain), ...payload],
    signature: Signature(
      signature,
      publicKey: SimplePublicKey(
        descriptor.signingPublicKey,
        type: KeyPairType.ed25519,
      ),
    ),
  );
  if (!verified) {
    throw const AtlasVaultKeyDeliveryException();
  }
}

Map<String, Object?> _decodeObject(Uint8List bytes) {
  final value = jsonDecode(utf8.decode(bytes, allowMalformed: false));
  return requireAtlasVaultObject(value, context: 'Pairing delivery object');
}

String _deviceId(Object? value) {
  final result = requireAtlasVaultString(
    value,
    field: 'device_id',
    allowEmpty: false,
  );
  if (!RegExp(r'^avd1-[0-9a-f]{64}$').hasMatch(result)) {
    throw const AtlasVaultKeyDeliveryException();
  }
  return result;
}

String _sha256(Object? value) {
  final result = requireAtlasVaultString(
    value,
    field: 'sha256',
    allowEmpty: false,
  );
  if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(result)) {
    throw const AtlasVaultKeyDeliveryException();
  }
  return result;
}

Uint8List _copyExact(List<int> value, int length) {
  if (value.length != length) {
    throw const AtlasVaultKeyDeliveryException();
  }
  return Uint8List.fromList(value);
}

String _hex(List<int> bytes) =>
    bytes.map((value) => value.toRadixString(16).padLeft(2, '0')).join();

DateTime _time(String value) => DateTime.parse(value);

bool _isAllZero(List<int> bytes) {
  var aggregate = 0;
  for (final byte in bytes) {
    aggregate |= byte;
  }
  return aggregate == 0;
}

bool _bytesEqual(List<int> left, List<int> right) {
  if (left.length != right.length) return false;
  var difference = 0;
  for (var index = 0; index < left.length; index++) {
    difference |= left[index] ^ right[index];
  }
  return difference == 0;
}

bool _textEquals(String left, String right) =>
    _bytesEqual(utf8.encode(left), utf8.encode(right));
