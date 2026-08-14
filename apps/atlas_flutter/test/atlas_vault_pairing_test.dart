import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> root;
  late Map<String, Object?> pairing;
  late AtlasVaultDeviceIdentity inviter;
  late AtlasVaultDeviceIdentity invitee;

  setUpAll(() async {
    root = loadAtlasVaultVector(
      'atlasvault_device_identity_pairing_vectors_v1.json',
    );
    pairing = atlasVaultObject(root['pairing']);
    inviter = await _identity(root, 'device_a');
    invitee = await _identity(root, 'device_b');
  });

  test(
    'offer and acceptance match exact vector bytes and signatures',
    () async {
      final offer = await createAtlasVaultPairingOffer(
        inviter: inviter,
        offerId: pairing['offer_id']! as String,
        nonce: _bytes(pairing['offer_nonce']),
        issuedAt: pairing['issued_at']! as String,
        expiresAt: pairing['expires_at']! as String,
      );
      final acceptance = await createAtlasVaultPairingAcceptance(
        invitee: invitee,
        signedOffer: offer,
        nonce: _bytes(pairing['acceptance_nonce']),
        acceptedAt: pairing['accepted_at']! as String,
        currentTime: pairing['verification_time']! as String,
      );

      expect(offer.toJson(), atlasVaultObject(pairing['signed_offer']));
      expect(offer.signature, _bytes(pairing['offer_signature']));
      expect(
        offer.canonicalBytes(),
        _bytes(pairing['signed_offer_canonical_json_b64']),
      );
      expect(await offer.sha256Hex(), pairing['offer_sha256']);
      expect(
        acceptance.toJson(),
        atlasVaultObject(pairing['signed_acceptance']),
      );
      expect(acceptance.signature, _bytes(pairing['acceptance_signature']));
      expect(
        acceptance.canonicalBytes(),
        _bytes(pairing['signed_acceptance_canonical_json_b64']),
      );
    },
  );

  test('shared secret, transcript, session, and proofs match vector', () async {
    final offer = AtlasVaultSignedPairingOffer.fromJson(
      atlasVaultObject(pairing['signed_offer']),
    );
    final acceptance = AtlasVaultSignedPairingAcceptance.fromJson(
      atlasVaultObject(pairing['signed_acceptance']),
    );
    final transcript = await atlasVaultPairingTranscriptSha256(
      offer,
      acceptance,
    );
    final inviterSession = await deriveAtlasVaultPairingSessionKey(
      localIdentity: inviter,
      signedOffer: offer,
      signedAcceptance: acceptance,
    );
    final inviteeSession = await deriveAtlasVaultPairingSessionKey(
      localIdentity: invitee,
      signedOffer: offer,
      signedAcceptance: acceptance,
    );
    final proofs = await deriveAtlasVaultPairingProofs(
      sessionKey: inviterSession,
      transcriptSha256: transcript,
    );

    expect(transcript, _hexBytes(pairing['transcript_sha256']! as String));
    expect(inviterSession, inviteeSession);
    expect(inviterSession, _bytes(pairing['hkdf_session_key']));
    expect(proofs.inviter, _bytes(pairing['inviter_proof']));
    expect(proofs.invitee, _bytes(pairing['invitee_proof']));
  });

  test('replay guard consumes only after proof verification', () async {
    final offer = AtlasVaultSignedPairingOffer.fromJson(
      atlasVaultObject(pairing['signed_offer']),
    );
    final acceptance = AtlasVaultSignedPairingAcceptance.fromJson(
      atlasVaultObject(pairing['signed_acceptance']),
    );
    final proofs = AtlasVaultPairingProofs(
      inviter: _bytes(pairing['inviter_proof']),
      invitee: _bytes(pairing['invitee_proof']),
    );
    final guard = _ReplayGuard();

    final verified = await verifyAtlasVaultPairingTranscript(
      localIdentity: inviter,
      signedOffer: offer,
      signedAcceptance: acceptance,
      proofs: proofs,
      currentTime: pairing['verification_time']! as String,
      replayGuard: guard,
    );
    expect(
      verified.transcriptSha256,
      _hexBytes(pairing['transcript_sha256']! as String),
    );
    expect(guard.consumedCount, 1);

    await expectLater(
      verifyAtlasVaultPairingTranscript(
        localIdentity: inviter,
        signedOffer: offer,
        signedAcceptance: acceptance,
        proofs: proofs,
        currentTime: pairing['verification_time']! as String,
        replayGuard: guard,
      ),
      throwsA(isA<AtlasVaultPairingException>()),
    );

    final freshGuard = _ReplayGuard();
    await expectLater(
      verifyAtlasVaultPairingTranscript(
        localIdentity: inviter,
        signedOffer: offer,
        signedAcceptance: acceptance,
        proofs: AtlasVaultPairingProofs(
          inviter: proofs.invitee,
          invitee: proofs.inviter,
        ),
        currentTime: pairing['verification_time']! as String,
        replayGuard: freshGuard,
      ),
      throwsA(isA<AtlasVaultPairingException>()),
    );
    expect(freshGuard.consumedCount, 0);
  });

  test('offer lifetime over 600 seconds is rejected at creation', () async {
    await expectLater(
      createAtlasVaultPairingOffer(
        inviter: inviter,
        offerId: pairing['offer_id']! as String,
        nonce: _bytes(pairing['offer_nonce']),
        issuedAt: '2026-01-15T12:05:00Z',
        expiresAt: '2026-01-15T12:15:01Z',
      ),
      throwsA(isA<AtlasVaultPairingException>()),
    );
  });

  test('expiry and future issue are rejected during verification', () async {
    final cases = <(String, String, String)>[
      ('2026-01-15T12:05:00Z', '2026-01-15T12:15:00Z', '2026-01-15T12:15:00Z'),
      ('2026-01-15T12:09:01Z', '2026-01-15T12:15:00Z', '2026-01-15T12:07:00Z'),
    ];

    for (final item in cases) {
      final offer = await createAtlasVaultPairingOffer(
        inviter: inviter,
        offerId: pairing['offer_id']! as String,
        nonce: _bytes(pairing['offer_nonce']),
        issuedAt: item.$1,
        expiresAt: item.$2,
      );
      await expectLater(
        createAtlasVaultPairingAcceptance(
          invitee: invitee,
          signedOffer: offer,
          nonce: _bytes(pairing['acceptance_nonce']),
          acceptedAt: pairing['accepted_at']! as String,
          currentTime: item.$3,
        ),
        throwsA(isA<AtlasVaultPairingException>()),
      );
    }
  });

  test(
    'offer and acceptance tampering fail before replay consumption',
    () async {
      final badOffer = _clone(atlasVaultObject(pairing['signed_offer']));
      final offerSignature = _bytes(badOffer['signature'])..[0] ^= 1;
      badOffer['signature'] = base64Encode(offerSignature);

      final badAcceptance = _clone(
        atlasVaultObject(pairing['signed_acceptance']),
      );
      atlasVaultObject(badAcceptance['acceptance'])['offer_sha256'] = ''
          .padLeft(64, '0');

      for (final pair in <(Map<String, Object?>, Map<String, Object?>)>[
        (badOffer, atlasVaultObject(pairing['signed_acceptance'])),
        (atlasVaultObject(pairing['signed_offer']), badAcceptance),
      ]) {
        final guard = _ReplayGuard();
        await expectLater(
          verifyAtlasVaultPairingTranscript(
            localIdentity: inviter,
            signedOffer: AtlasVaultSignedPairingOffer.fromJson(pair.$1),
            signedAcceptance: AtlasVaultSignedPairingAcceptance.fromJson(
              pair.$2,
            ),
            proofs: AtlasVaultPairingProofs(
              inviter: _bytes(pairing['inviter_proof']),
              invitee: _bytes(pairing['invitee_proof']),
            ),
            currentTime: pairing['verification_time']! as String,
            replayGuard: guard,
          ),
          throwsA(isA<AtlasVaultPairingException>()),
        );
        expect(guard.consumedCount, 0);
      }
    },
  );

  test(
    'all-zero shared secret and invalid-case manifest fail closed',
    () async {
      await expectLater(
        deriveAtlasVaultPairingSessionKeyFromSharedSecret(
          sharedSecret: Uint8List(32),
          transcriptSha256: _hexBytes(pairing['transcript_sha256']! as String),
        ),
        throwsA(isA<AtlasVaultPairingException>()),
      );

      final expected = <String>{
        'descriptor_signature_tamper',
        'descriptor_device_id_mismatch',
        'signing_key_substitution',
        'agreement_key_substitution',
        'offer_signature_tamper',
        'offer_nonce_tamper',
        'offer_id_tamper',
        'expired_offer',
        'excessive_lifetime',
        'future_issue_time',
        'acceptance_offer_hash_mismatch',
        'acceptance_signature_tamper',
        'same_inviter_invitee_identity',
        'all_zero_shared_secret',
        'transcript_tamper',
        'swapped_proof',
        'replay_consumption_duplicate',
      };
      expect(
        atlasVaultList(
          root['invalid_cases'],
        ).map((item) => atlasVaultObject(item)['case_id']).toSet(),
        expected,
      );
    },
  );

  test('Dart verifies the public Swift runtime signature artifact', () async {
    final directory =
        Platform.environment['ATLAS_DEVICE_IDENTITY_RUNTIME_VECTOR_DIR'];
    if (directory == null) {
      return;
    }
    final artifact = atlasVaultObject(
      jsonDecode(
        await File(
          '$directory/swift-generated-signed-transcript.json',
        ).readAsString(),
      ),
    );
    expect(artifact.keys.toSet(), <String>{
      '_warning',
      'format',
      'version',
      'device_a_id',
      'device_b_id',
      'signed_descriptor_a',
      'signed_descriptor_a_canonical_json_b64',
      'signed_descriptor_b',
      'signed_descriptor_b_canonical_json_b64',
      'signed_offer',
      'signed_offer_canonical_json_b64',
      'signed_acceptance',
      'signed_acceptance_canonical_json_b64',
      'verification_time',
      'transcript_sha256',
      'inviter_proof',
      'invitee_proof',
    });
    expect(
      artifact['_warning'],
      'FAKE TEST DATA ONLY - PUBLIC SIGNED ARTIFACT',
    );
    expect(artifact['format'], 'atlasvault-swift-runtime-signed-transcript-v1');
    expect(artifact['version'], 1);

    final descriptorA = AtlasVaultSignedDeviceDescriptor.fromJson(
      atlasVaultObject(artifact['signed_descriptor_a']),
    );
    final descriptorB = AtlasVaultSignedDeviceDescriptor.fromJson(
      atlasVaultObject(artifact['signed_descriptor_b']),
    );
    expect(
      descriptorA.canonicalBytes(),
      _bytes(artifact['signed_descriptor_a_canonical_json_b64']),
    );
    expect(
      descriptorB.canonicalBytes(),
      _bytes(artifact['signed_descriptor_b_canonical_json_b64']),
    );
    expect(
      (await verifyAtlasVaultSignedDeviceDescriptor(descriptorA)).deviceId,
      artifact['device_a_id'],
    );
    expect(
      (await verifyAtlasVaultSignedDeviceDescriptor(descriptorB)).deviceId,
      artifact['device_b_id'],
    );

    final offer = AtlasVaultSignedPairingOffer.fromJson(
      atlasVaultObject(artifact['signed_offer']),
    );
    final acceptance = AtlasVaultSignedPairingAcceptance.fromJson(
      atlasVaultObject(artifact['signed_acceptance']),
    );
    expect(
      offer.canonicalBytes(),
      _bytes(artifact['signed_offer_canonical_json_b64']),
    );
    expect(
      acceptance.canonicalBytes(),
      _bytes(artifact['signed_acceptance_canonical_json_b64']),
    );
    expect(offer.offer.inviter.toJson(), descriptorA.toJson());
    expect(acceptance.acceptance.invitee.toJson(), descriptorB.toJson());
    expect(await offer.sha256Hex(), acceptance.acceptance.offerSha256);
    await verifyAtlasVaultPairingOffer(
      offer,
      currentTime: artifact['verification_time']! as String,
    );

    final transcript = await atlasVaultPairingTranscriptSha256(
      offer,
      acceptance,
    );
    final sessionKey = await deriveAtlasVaultPairingSessionKey(
      localIdentity: inviter,
      signedOffer: offer,
      signedAcceptance: acceptance,
    );
    final runtimeProofs = await deriveAtlasVaultPairingProofs(
      sessionKey: sessionKey,
      transcriptSha256: transcript,
    );
    expect(transcript, _hexBytes(artifact['transcript_sha256']! as String));
    expect(runtimeProofs.inviter, _bytes(artifact['inviter_proof']));
    expect(runtimeProofs.invitee, _bytes(artifact['invitee_proof']));
    await verifyAtlasVaultPairingTranscript(
      localIdentity: inviter,
      signedOffer: offer,
      signedAcceptance: acceptance,
      proofs: AtlasVaultPairingProofs(
        inviter: _bytes(artifact['inviter_proof']),
        invitee: _bytes(artifact['invitee_proof']),
      ),
      currentTime: artifact['verification_time']! as String,
      replayGuard: _ReplayGuard(),
    );

    final badSignature = _clone(atlasVaultObject(artifact['signed_offer']));
    final changedSignature = _bytes(badSignature['signature'])..[0] ^= 1;
    badSignature['signature'] = base64Encode(changedSignature);
    await expectLater(
      verifyAtlasVaultPairingOffer(
        AtlasVaultSignedPairingOffer.fromJson(badSignature),
        currentTime: artifact['verification_time']! as String,
      ),
      throwsA(isA<AtlasVaultPairingException>()),
    );

    final badOffer = _clone(atlasVaultObject(artifact['signed_offer']));
    final offerPayload = atlasVaultObject(badOffer['offer']);
    final changedNonce = _bytes(offerPayload['nonce'])..[0] ^= 1;
    offerPayload['nonce'] = base64Encode(changedNonce);
    await expectLater(
      verifyAtlasVaultPairingOffer(
        AtlasVaultSignedPairingOffer.fromJson(badOffer),
        currentTime: artifact['verification_time']! as String,
      ),
      throwsA(isA<AtlasVaultPairingException>()),
    );

    final badAcceptance = _clone(
      atlasVaultObject(artifact['signed_acceptance']),
    );
    atlasVaultObject(badAcceptance['acceptance'])['offer_sha256'] = ''.padLeft(
      64,
      '0',
    );
    await expectLater(
      verifyAtlasVaultPairingTranscript(
        localIdentity: inviter,
        signedOffer: offer,
        signedAcceptance: AtlasVaultSignedPairingAcceptance.fromJson(
          badAcceptance,
        ),
        proofs: runtimeProofs,
        currentTime: artifact['verification_time']! as String,
        replayGuard: _ReplayGuard(),
      ),
      throwsA(isA<AtlasVaultPairingException>()),
    );
  });
}

final class _ReplayGuard implements AtlasVaultPairingReplayGuard {
  final Set<String> _consumed = <String>{};

  int get consumedCount => _consumed.length;

  @override
  Future<AtlasVaultPairingReplayOutcome> consume({
    required String offerId,
    required Uint8List transcriptSha256,
    required String expiresAt,
  }) async {
    final key = '$offerId:${base64Encode(transcriptSha256)}:$expiresAt';
    if (!_consumed.add(key)) {
      return AtlasVaultPairingReplayOutcome.alreadyConsumed;
    }
    return AtlasVaultPairingReplayOutcome.accepted;
  }
}

Future<AtlasVaultDeviceIdentity> _identity(
  Map<String, Object?> root,
  String name,
) {
  final vector = atlasVaultObject(root[name]);
  final descriptor = atlasVaultObject(vector['descriptor']);
  return AtlasVaultDeviceIdentity.fromPrivateKeys(
    signingPrivateSeed: _bytes(vector['signing_private_seed']),
    agreementPrivateKey: _bytes(vector['agreement_private_key']),
    createdAt: descriptor['created_at']! as String,
    keyEpoch: descriptor['key_epoch']! as int,
  );
}

Uint8List _bytes(Object? value) {
  return Uint8List.fromList(base64Decode(value! as String));
}

Uint8List _hexBytes(String value) {
  return Uint8List.fromList(<int>[
    for (var index = 0; index < value.length; index += 2)
      int.parse(value.substring(index, index + 2), radix: 16),
  ]);
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
