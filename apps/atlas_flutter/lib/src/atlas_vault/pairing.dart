import 'dart:convert';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';

import 'canonical_json.dart';
import 'device_identity.dart';
import 'strict_values.dart';

const _offerFormat = 'atlasvault-pairing-offer';
const _signedOfferFormat = 'atlasvault-signed-pairing-offer';
const _acceptanceFormat = 'atlasvault-pairing-acceptance';
const _signedAcceptanceFormat = 'atlasvault-signed-pairing-acceptance';
const _pairingVersion = 1;
const _nonceLength = 32;
const _signatureLength = 64;
const _maximumLifetimeSeconds = 600;
const _maximumClockSkewSeconds = 120;
const _offerSignatureDomain = 'atlasvault-pairing-offer-signature-v1:';
const _acceptanceSignatureDomain =
    'atlasvault-pairing-acceptance-signature-v1:';
const _transcriptDomain = 'atlasvault-pairing-transcript-v1:';
const _sessionInfo = 'atlasvault-pairing-session-v1';
const _inviterProofDomain = 'atlasvault-pairing-confirm-inviter-v1:';
const _inviteeProofDomain = 'atlasvault-pairing-confirm-invitee-v1:';

final class AtlasVaultPairingException implements Exception {
  const AtlasVaultPairingException();

  @override
  String toString() => 'AtlasVault pairing verification failed.';
}

final class AtlasVaultPairingOffer {
  AtlasVaultPairingOffer._({
    required this.offerId,
    required this.inviter,
    required Uint8List nonce,
    required this.issuedAt,
    required this.expiresAt,
  }) : _nonce = Uint8List.fromList(nonce);

  final String offerId;
  final AtlasVaultSignedDeviceDescriptor inviter;
  final Uint8List _nonce;
  final String issuedAt;
  final String expiresAt;

  Uint8List get nonce => Uint8List.fromList(_nonce);

  factory AtlasVaultPairingOffer.fromJson(Map<String, Object?> value) {
    try {
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'offer_id',
          'inviter',
          'nonce',
          'issued_at',
          'expires_at',
        },
        context: 'Pairing offer',
      );
      if (value['format'] != _offerFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _pairingVersion) {
        throw const AtlasVaultPairingException();
      }
      return _validatedOffer(
        offerId: requireAtlasVaultCanonicalUuid(
          value['offer_id'],
          field: 'offer_id',
        ),
        inviter: AtlasVaultSignedDeviceDescriptor.fromJson(
          requireAtlasVaultObject(value['inviter'], context: 'inviter'),
        ),
        nonce: requireAtlasVaultCanonicalBase64(
          value['nonce'],
          field: 'nonce',
          exactLength: _nonceLength,
        ),
        issuedAt: requireAtlasVaultUtcSeconds(
          value['issued_at'],
          field: 'issued_at',
        ),
        expiresAt: requireAtlasVaultUtcSeconds(
          value['expires_at'],
          field: 'expires_at',
        ),
      );
    } catch (_) {
      throw const AtlasVaultPairingException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _offerFormat,
    'version': _pairingVersion,
    'offer_id': offerId,
    'inviter': inviter.toJson(),
    'nonce': base64Encode(_nonce),
    'issued_at': issuedAt,
    'expires_at': expiresAt,
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

AtlasVaultPairingOffer _validatedOffer({
  required String offerId,
  required AtlasVaultSignedDeviceDescriptor inviter,
  required Uint8List nonce,
  required String issuedAt,
  required String expiresAt,
}) {
  final issue = _time(issuedAt);
  final expiry = _time(expiresAt);
  final lifetime = expiry.difference(issue).inSeconds;
  if (lifetime <= 0 || lifetime > _maximumLifetimeSeconds) {
    throw const AtlasVaultPairingException();
  }
  return AtlasVaultPairingOffer._(
    offerId: offerId,
    inviter: inviter,
    nonce: nonce,
    issuedAt: issuedAt,
    expiresAt: expiresAt,
  );
}

final class AtlasVaultSignedPairingOffer {
  AtlasVaultSignedPairingOffer._({
    required this.offer,
    required Uint8List signature,
  }) : _signature = Uint8List.fromList(signature);

  final AtlasVaultPairingOffer offer;
  final Uint8List _signature;

  Uint8List get signature => Uint8List.fromList(_signature);

  factory AtlasVaultSignedPairingOffer.fromJson(Map<String, Object?> value) {
    try {
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{'format', 'version', 'offer', 'signature'},
        context: 'Signed pairing offer',
      );
      if (value['format'] != _signedOfferFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _pairingVersion) {
        throw const AtlasVaultPairingException();
      }
      return AtlasVaultSignedPairingOffer._(
        offer: AtlasVaultPairingOffer.fromJson(
          requireAtlasVaultObject(value['offer'], context: 'offer'),
        ),
        signature: requireAtlasVaultCanonicalBase64(
          value['signature'],
          field: 'signature',
          exactLength: _signatureLength,
        ),
      );
    } catch (_) {
      throw const AtlasVaultPairingException();
    }
  }

  factory AtlasVaultSignedPairingOffer.fromCanonicalBytes(Uint8List value) {
    try {
      final input = Uint8List.fromList(value);
      final decoded = AtlasVaultSignedPairingOffer.fromJson(
        _decodeCanonicalPairingObject(input),
      );
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultPairingException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultPairingException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _signedOfferFormat,
    'version': _pairingVersion,
    'offer': offer.toJson(),
    'signature': base64Encode(_signature),
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  Future<String> sha256Hex() async {
    final digest = await Sha256().hash(canonicalBytes());
    return digest.bytes
        .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
        .join();
  }
}

final class AtlasVaultPairingAcceptance {
  AtlasVaultPairingAcceptance._({
    required this.offerId,
    required this.offerSha256,
    required this.invitee,
    required Uint8List nonce,
    required this.acceptedAt,
  }) : _nonce = Uint8List.fromList(nonce);

  final String offerId;
  final String offerSha256;
  final AtlasVaultSignedDeviceDescriptor invitee;
  final Uint8List _nonce;
  final String acceptedAt;

  factory AtlasVaultPairingAcceptance.fromJson(Map<String, Object?> value) {
    try {
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'offer_id',
          'offer_sha256',
          'invitee',
          'nonce',
          'accepted_at',
        },
        context: 'Pairing acceptance',
      );
      if (value['format'] != _acceptanceFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _pairingVersion) {
        throw const AtlasVaultPairingException();
      }
      final hash = requireAtlasVaultString(
        value['offer_sha256'],
        field: 'offer_sha256',
        allowEmpty: false,
      );
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(hash)) {
        throw const AtlasVaultPairingException();
      }
      return AtlasVaultPairingAcceptance._(
        offerId: requireAtlasVaultCanonicalUuid(
          value['offer_id'],
          field: 'offer_id',
        ),
        offerSha256: hash,
        invitee: AtlasVaultSignedDeviceDescriptor.fromJson(
          requireAtlasVaultObject(value['invitee'], context: 'invitee'),
        ),
        nonce: requireAtlasVaultCanonicalBase64(
          value['nonce'],
          field: 'nonce',
          exactLength: _nonceLength,
        ),
        acceptedAt: requireAtlasVaultUtcSeconds(
          value['accepted_at'],
          field: 'accepted_at',
        ),
      );
    } catch (_) {
      throw const AtlasVaultPairingException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _acceptanceFormat,
    'version': _pairingVersion,
    'offer_id': offerId,
    'offer_sha256': offerSha256,
    'invitee': invitee.toJson(),
    'nonce': base64Encode(_nonce),
    'accepted_at': acceptedAt,
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

final class AtlasVaultSignedPairingAcceptance {
  AtlasVaultSignedPairingAcceptance._({
    required this.acceptance,
    required Uint8List signature,
  }) : _signature = Uint8List.fromList(signature);

  final AtlasVaultPairingAcceptance acceptance;
  final Uint8List _signature;

  Uint8List get signature => Uint8List.fromList(_signature);

  factory AtlasVaultSignedPairingAcceptance.fromJson(
    Map<String, Object?> value,
  ) {
    try {
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'acceptance',
          'signature',
        },
        context: 'Signed pairing acceptance',
      );
      if (value['format'] != _signedAcceptanceFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _pairingVersion) {
        throw const AtlasVaultPairingException();
      }
      return AtlasVaultSignedPairingAcceptance._(
        acceptance: AtlasVaultPairingAcceptance.fromJson(
          requireAtlasVaultObject(value['acceptance'], context: 'acceptance'),
        ),
        signature: requireAtlasVaultCanonicalBase64(
          value['signature'],
          field: 'signature',
          exactLength: _signatureLength,
        ),
      );
    } catch (_) {
      throw const AtlasVaultPairingException();
    }
  }

  factory AtlasVaultSignedPairingAcceptance.fromCanonicalBytes(
    Uint8List value,
  ) {
    try {
      final input = Uint8List.fromList(value);
      final decoded = AtlasVaultSignedPairingAcceptance.fromJson(
        _decodeCanonicalPairingObject(input),
      );
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultPairingException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultPairingException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _signedAcceptanceFormat,
    'version': _pairingVersion,
    'acceptance': acceptance.toJson(),
    'signature': base64Encode(_signature),
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());
}

final class AtlasVaultPairingProofs {
  AtlasVaultPairingProofs({
    required Uint8List inviter,
    required Uint8List invitee,
  }) : _inviter = _copy32(inviter),
       _invitee = _copy32(invitee);

  final Uint8List _inviter;
  final Uint8List _invitee;

  Uint8List get inviter => Uint8List.fromList(_inviter);
  Uint8List get invitee => Uint8List.fromList(_invitee);

  @override
  String toString() => 'AtlasVaultPairingProofs(<redacted>)';
}

final class AtlasVaultPairingSession {
  AtlasVaultPairingSession._({
    required Uint8List transcriptSha256,
    required Uint8List sessionKey,
  }) : _transcriptSha256 = _copy32(transcriptSha256),
       _sessionKey = _copy32(sessionKey);

  final Uint8List _transcriptSha256;
  final Uint8List _sessionKey;

  Uint8List get transcriptSha256 => Uint8List.fromList(_transcriptSha256);
  Uint8List get sessionKey => Uint8List.fromList(_sessionKey);

  void destroy() {
    _sessionKey.fillRange(0, _sessionKey.length, 0);
  }

  @override
  String toString() => 'AtlasVaultPairingSession(<redacted>)';
}

String atlasVaultPairingDeviceFingerprint(String deviceId) {
  if (!RegExp(r'^avd1-[0-9a-f]{64}$').hasMatch(deviceId)) {
    throw const AtlasVaultPairingException();
  }
  final prefix = deviceId.substring(5, 21).toUpperCase();
  return '${prefix.substring(0, 4)}-${prefix.substring(4, 8)}-'
      '${prefix.substring(8, 12)}-${prefix.substring(12, 16)}';
}

enum AtlasVaultPairingReplayOutcome { accepted, alreadyConsumed }

abstract interface class AtlasVaultPairingReplayGuard {
  Future<AtlasVaultPairingReplayOutcome> consume({
    required String offerId,
    required Uint8List transcriptSha256,
    required String expiresAt,
  });
}

Future<AtlasVaultSignedPairingOffer> createAtlasVaultPairingOffer({
  required AtlasVaultDeviceIdentity inviter,
  required String offerId,
  required Uint8List nonce,
  required String issuedAt,
  required String expiresAt,
}) async {
  try {
    requireAtlasVaultCanonicalUuid(offerId, field: 'offer_id');
    requireAtlasVaultUtcSeconds(issuedAt, field: 'issued_at');
    requireAtlasVaultUtcSeconds(expiresAt, field: 'expires_at');
    if (nonce.length != _nonceLength) {
      throw const AtlasVaultPairingException();
    }
    final offer = _validatedOffer(
      offerId: offerId,
      inviter: await inviter.signDescriptor(),
      nonce: nonce,
      issuedAt: issuedAt,
      expiresAt: expiresAt,
    );
    return AtlasVaultSignedPairingOffer._(
      offer: offer,
      signature: await inviter.signBytes(
        _concat(<List<int>>[
          utf8.encode(_offerSignatureDomain),
          offer.canonicalBytes(),
        ]),
      ),
    );
  } catch (_) {
    throw const AtlasVaultPairingException();
  }
}

Future<void> _verifyOfferSignature(AtlasVaultSignedPairingOffer signed) async {
  try {
    final descriptor = await verifyAtlasVaultSignedDeviceDescriptor(
      signed.offer.inviter,
    );
    final valid = await Ed25519().verify(
      _concat(<List<int>>[
        utf8.encode(_offerSignatureDomain),
        signed.offer.canonicalBytes(),
      ]),
      signature: Signature(
        signed._signature,
        publicKey: SimplePublicKey(
          descriptor.signingPublicKey,
          type: KeyPairType.ed25519,
        ),
      ),
    );
    if (!valid) {
      throw const AtlasVaultPairingException();
    }
  } catch (_) {
    throw const AtlasVaultPairingException();
  }
}

Future<AtlasVaultPairingOffer> verifyAtlasVaultPairingOffer(
  AtlasVaultSignedPairingOffer signed, {
  required String currentTime,
}) async {
  try {
    await _verifyOfferSignature(signed);
    final now = _time(currentTime);
    final issue = _time(signed.offer.issuedAt);
    final expiry = _time(signed.offer.expiresAt);
    if (!now.isBefore(expiry) ||
        issue.isAfter(
          now.add(const Duration(seconds: _maximumClockSkewSeconds)),
        )) {
      throw const AtlasVaultPairingException();
    }
    return signed.offer;
  } catch (_) {
    throw const AtlasVaultPairingException();
  }
}

Future<AtlasVaultSignedPairingAcceptance> createAtlasVaultPairingAcceptance({
  required AtlasVaultDeviceIdentity invitee,
  required AtlasVaultSignedPairingOffer signedOffer,
  required Uint8List nonce,
  required String acceptedAt,
  required String currentTime,
}) async {
  try {
    final offer = await verifyAtlasVaultPairingOffer(
      signedOffer,
      currentTime: currentTime,
    );
    if (nonce.length != _nonceLength) {
      throw const AtlasVaultPairingException();
    }
    requireAtlasVaultUtcSeconds(acceptedAt, field: 'accepted_at');
    final acceptance = AtlasVaultPairingAcceptance._(
      offerId: offer.offerId,
      offerSha256: await signedOffer.sha256Hex(),
      invitee: await invitee.signDescriptor(),
      nonce: nonce,
      acceptedAt: acceptedAt,
    );
    final signed = AtlasVaultSignedPairingAcceptance._(
      acceptance: acceptance,
      signature: await invitee.signBytes(
        _concat(<List<int>>[
          utf8.encode(_acceptanceSignatureDomain),
          acceptance.canonicalBytes(),
        ]),
      ),
    );
    await _verifyAcceptanceRelation(signedOffer, signed);
    return signed;
  } catch (_) {
    throw const AtlasVaultPairingException();
  }
}

Future<void> _verifyAcceptanceSignature(
  AtlasVaultSignedPairingAcceptance signed,
) async {
  try {
    final descriptor = await verifyAtlasVaultSignedDeviceDescriptor(
      signed.acceptance.invitee,
    );
    final valid = await Ed25519().verify(
      _concat(<List<int>>[
        utf8.encode(_acceptanceSignatureDomain),
        signed.acceptance.canonicalBytes(),
      ]),
      signature: Signature(
        signed._signature,
        publicKey: SimplePublicKey(
          descriptor.signingPublicKey,
          type: KeyPairType.ed25519,
        ),
      ),
    );
    if (!valid) {
      throw const AtlasVaultPairingException();
    }
  } catch (_) {
    throw const AtlasVaultPairingException();
  }
}

Future<void> _verifyAcceptanceRelation(
  AtlasVaultSignedPairingOffer offer,
  AtlasVaultSignedPairingAcceptance acceptance,
) async {
  final accepted = acceptance.acceptance;
  if (accepted.offerId != offer.offer.offerId ||
      !_constantTimeTextEquals(accepted.offerSha256, await offer.sha256Hex()) ||
      accepted.invitee.descriptor.deviceId ==
          offer.offer.inviter.descriptor.deviceId) {
    throw const AtlasVaultPairingException();
  }
  final acceptedTime = _time(accepted.acceptedAt);
  final lower = _time(
    offer.offer.issuedAt,
  ).subtract(const Duration(seconds: _maximumClockSkewSeconds));
  final upper = _time(
    offer.offer.expiresAt,
  ).add(const Duration(seconds: _maximumClockSkewSeconds));
  if (acceptedTime.isBefore(lower) || acceptedTime.isAfter(upper)) {
    throw const AtlasVaultPairingException();
  }
}

Future<Uint8List> atlasVaultPairingTranscriptSha256(
  AtlasVaultSignedPairingOffer offer,
  AtlasVaultSignedPairingAcceptance acceptance,
) async {
  try {
    final offerBytes = offer.canonicalBytes();
    final acceptanceBytes = acceptance.canonicalBytes();
    final digest = await Sha256().hash(
      _concat(<List<int>>[
        utf8.encode(_transcriptDomain),
        _uint64(offerBytes.length),
        offerBytes,
        _uint64(acceptanceBytes.length),
        acceptanceBytes,
      ]),
    );
    return Uint8List.fromList(digest.bytes);
  } catch (_) {
    throw const AtlasVaultPairingException();
  }
}

Future<Uint8List> deriveAtlasVaultPairingSessionKeyFromSharedSecret({
  required Uint8List sharedSecret,
  required Uint8List transcriptSha256,
}) async {
  SecretKeyData? secret;
  SecretKey? derived;
  try {
    if (sharedSecret.length != 32 ||
        transcriptSha256.length != 32 ||
        _isAllZero(sharedSecret)) {
      throw const AtlasVaultPairingException();
    }
    secret = SecretKeyData(
      Uint8List.fromList(sharedSecret),
      overwriteWhenDestroyed: true,
    );
    derived = await Hkdf(hmac: Hmac.sha256(), outputLength: 32).deriveKey(
      secretKey: secret,
      nonce: transcriptSha256,
      info: utf8.encode(_sessionInfo),
    );
    return Uint8List.fromList(await derived.extractBytes());
  } catch (_) {
    throw const AtlasVaultPairingException();
  } finally {
    secret?.destroy();
    derived?.destroy();
  }
}

Future<Uint8List> deriveAtlasVaultPairingSessionKey({
  required AtlasVaultDeviceIdentity localIdentity,
  required AtlasVaultSignedPairingOffer signedOffer,
  required AtlasVaultSignedPairingAcceptance signedAcceptance,
}) async {
  Uint8List? shared;
  try {
    await _verifyOfferSignature(signedOffer);
    await _verifyAcceptanceSignature(signedAcceptance);
    await _verifyAcceptanceRelation(signedOffer, signedAcceptance);
    final inviter = signedOffer.offer.inviter.descriptor;
    final invitee = signedAcceptance.acceptance.invitee.descriptor;
    final Uint8List remotePublicKey;
    if (localIdentity.deviceId == inviter.deviceId) {
      remotePublicKey = invitee.agreementPublicKey;
    } else if (localIdentity.deviceId == invitee.deviceId) {
      remotePublicKey = inviter.agreementPublicKey;
    } else {
      throw const AtlasVaultPairingException();
    }
    shared = await localIdentity.sharedSecretFor(remotePublicKey);
    return deriveAtlasVaultPairingSessionKeyFromSharedSecret(
      sharedSecret: shared,
      transcriptSha256: await atlasVaultPairingTranscriptSha256(
        signedOffer,
        signedAcceptance,
      ),
    );
  } catch (_) {
    throw const AtlasVaultPairingException();
  } finally {
    shared?.fillRange(0, shared.length, 0);
  }
}

Future<AtlasVaultPairingProofs> deriveAtlasVaultPairingProofs({
  required Uint8List sessionKey,
  required Uint8List transcriptSha256,
}) async {
  SecretKeyData? key;
  try {
    if (sessionKey.length != 32 || transcriptSha256.length != 32) {
      throw const AtlasVaultPairingException();
    }
    key = SecretKeyData(
      Uint8List.fromList(sessionKey),
      overwriteWhenDestroyed: true,
    );
    final inviter = await Hmac.sha256().calculateMac(
      _concat(<List<int>>[utf8.encode(_inviterProofDomain), transcriptSha256]),
      secretKey: key,
    );
    final invitee = await Hmac.sha256().calculateMac(
      _concat(<List<int>>[utf8.encode(_inviteeProofDomain), transcriptSha256]),
      secretKey: key,
    );
    return AtlasVaultPairingProofs(
      inviter: Uint8List.fromList(inviter.bytes),
      invitee: Uint8List.fromList(invitee.bytes),
    );
  } catch (_) {
    throw const AtlasVaultPairingException();
  } finally {
    key?.destroy();
  }
}

Future<AtlasVaultPairingSession> verifyAtlasVaultPairingTranscript({
  required AtlasVaultDeviceIdentity localIdentity,
  required AtlasVaultSignedPairingOffer signedOffer,
  required AtlasVaultSignedPairingAcceptance signedAcceptance,
  required AtlasVaultPairingProofs proofs,
  required String currentTime,
  required AtlasVaultPairingReplayGuard replayGuard,
}) async {
  Uint8List? session;
  try {
    await verifyAtlasVaultPairingOffer(signedOffer, currentTime: currentTime);
    await _verifyAcceptanceSignature(signedAcceptance);
    await _verifyAcceptanceRelation(signedOffer, signedAcceptance);
    final transcript = await atlasVaultPairingTranscriptSha256(
      signedOffer,
      signedAcceptance,
    );
    session = await deriveAtlasVaultPairingSessionKey(
      localIdentity: localIdentity,
      signedOffer: signedOffer,
      signedAcceptance: signedAcceptance,
    );
    final expected = await deriveAtlasVaultPairingProofs(
      sessionKey: session,
      transcriptSha256: transcript,
    );
    if (!_bytesEqual(expected._inviter, proofs._inviter) ||
        !_bytesEqual(expected._invitee, proofs._invitee)) {
      throw const AtlasVaultPairingException();
    }
    final outcome = await replayGuard.consume(
      offerId: signedOffer.offer.offerId,
      transcriptSha256: Uint8List.fromList(transcript),
      expiresAt: signedOffer.offer.expiresAt,
    );
    if (outcome != AtlasVaultPairingReplayOutcome.accepted) {
      throw const AtlasVaultPairingException();
    }
    return AtlasVaultPairingSession._(
      transcriptSha256: transcript,
      sessionKey: session,
    );
  } catch (_) {
    throw const AtlasVaultPairingException();
  } finally {
    session?.fillRange(0, session.length, 0);
  }
}

DateTime _time(String value) {
  try {
    requireAtlasVaultUtcSeconds(value, field: 'timestamp');
    return DateTime.parse(value).toUtc();
  } catch (_) {
    throw const AtlasVaultPairingException();
  }
}

Uint8List _uint64(int value) {
  final output = Uint8List(8);
  ByteData.sublistView(output).setUint64(0, value, Endian.big);
  return output;
}

Uint8List _copy32(Uint8List value) {
  if (value.length != 32) {
    throw const AtlasVaultPairingException();
  }
  return Uint8List.fromList(value);
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

Map<String, Object?> _decodeCanonicalPairingObject(Uint8List value) {
  try {
    if (value.isEmpty) {
      throw const AtlasVaultPairingException();
    }
    final decoded = jsonDecode(utf8.decode(value, allowMalformed: false));
    if (decoded is! Map<String, dynamic>) {
      throw const AtlasVaultPairingException();
    }
    return decoded.cast<String, Object?>();
  } catch (_) {
    throw const AtlasVaultPairingException();
  }
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
