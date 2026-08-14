import 'dart:convert';
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

  test('lifetime, expiry, and future issue are rejected', () async {
    final cases = <(String, String, String)>[
      ('2026-01-15T12:05:00Z', '2026-01-15T12:15:01Z', '2026-01-15T12:07:00Z'),
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
