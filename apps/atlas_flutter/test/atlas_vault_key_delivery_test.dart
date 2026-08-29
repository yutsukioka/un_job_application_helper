import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/src/atlas_vault/key_delivery.dart'
    show createAtlasVaultKeyDeliveryForTesting;
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> root;
  late AtlasVaultDeviceIdentity inviter;
  late AtlasVaultDeviceIdentity invitee;

  setUpAll(() async {
    root = loadAtlasVaultVector(
      'atlasvault_trusted_pairing_delivery_vectors_v1.json',
    );
    inviter = await _identity(root, 'inviter');
    invitee = await _identity(root, 'invitee');
  });

  test('SAS and authenticated delivery match exact shared bytes', () async {
    final transcript = _hex(root['transcript_sha256']! as String);
    expect(
      await deriveAtlasVaultPairingSas(
        _bytes(root['pairing_session_key_b64']),
        transcript,
      ),
      root['sas'],
    );

    final request = await createAtlasVaultPairingKeyRequest(
      invitee: invitee,
      requestId: root['request_id']! as String,
      transcriptSha256: transcript,
      inviterDeviceId: inviter.deviceId,
      inviteeEphemeralPublicKey: _bytes(
        root['invitee_ephemeral_public_key_b64'],
      ),
      nonce: _bytes(root['request_nonce_b64']),
      issuedAt: root['request_issued_at']! as String,
      expiresAt: root['request_expires_at']! as String,
    );
    expect(request.toJson(), atlasVaultObject(root['signed_key_request']));
    expect(
      request.canonicalBytes(),
      _bytes(root['signed_key_request_canonical_b64']),
    );

    final bootstrap = AtlasVaultPairingBootstrap.fromJson(
      atlasVaultObject(root['bootstrap']),
    );
    final delivery = await createAtlasVaultKeyDeliveryForTesting(
      inviter: inviter,
      keyRequest: request,
      transcriptSha256: transcript,
      bootstrap: bootstrap,
      vaultKey: _bytes(root['test_only_vault_key_b64']),
      inviterEphemeralPrivateKey: _bytes(
        root['inviter_ephemeral_private_key_b64'],
      ),
      nonce: _bytes(root['delivery_nonce_b64']),
      deliveryId: root['delivery_id']! as String,
      keyEpoch: root['vault_key_epoch']! as int,
      expiresAt: root['delivery_expires_at']! as String,
    );
    expect(delivery.toJson(), atlasVaultObject(root['signed_delivery']));

    final opened = await openAtlasVaultKeyDelivery(
      delivery,
      keyRequest: request,
      inviteeEphemeralPrivateKey: _bytes(
        root['invitee_ephemeral_private_key_b64'],
      ),
      bootstrap: bootstrap,
      transcriptSha256: transcript,
      currentTime: root['verification_time']! as String,
    );
    expect(opened, _bytes(root['test_only_vault_key_b64']));
  });

  test('acknowledgement verifies and artifact bytes are canonical', () async {
    final delivery = AtlasVaultSignedVaultKeyDelivery.fromJson(
      atlasVaultObject(root['signed_delivery']),
    );
    final acknowledgement = await createAtlasVaultPairingAcknowledgement(
      invitee: invitee,
      acknowledgementId: root['acknowledgement_id']! as String,
      delivery: delivery,
      installedAt: root['installed_at']! as String,
    );
    expect(
      acknowledgement.toJson(),
      atlasVaultObject(root['signed_acknowledgement']),
    );
    await verifyAtlasVaultPairingAcknowledgement(
      acknowledgement,
      delivery: delivery,
      inviterDeviceId: inviter.deviceId,
      inviteeDeviceId: invitee.deviceId,
    );

    for (final kind in <String>[
      'offer',
      'acceptance',
      'delivery',
      'acknowledgement',
    ]) {
      final value = atlasVaultObject(atlasVaultObject(root['artifacts'])[kind]);
      final bytes = _bytes(value['canonical_b64']);
      expect(
        AtlasVaultPairingArtifact.fromCanonicalBytes(bytes).canonicalBytes(),
        bytes,
      );
    }
  });

  test('validated artifact payload cannot mutate canonical bytes', () {
    final value = atlasVaultObject(
      atlasVaultObject(root['artifacts'])['delivery'],
    );
    final encoded = _bytes(value['canonical_b64']);
    final artifact = AtlasVaultPairingArtifact.fromCanonicalBytes(encoded);

    artifact.payload['bootstrap'] = <String, Object?>{};
    final nested = atlasVaultObject(artifact.payload['bootstrap']);
    nested['snapshot_id'] = 'changed';

    expect(artifact.canonicalBytes(), encoded);
  });

  test('pairing bootstrap rejects non-ASCII authenticated metadata', () {
    for (final value in <String>[
      'record-e\u0301',
      'record-\u{1F512}',
      'record-\nline',
    ]) {
      final bootstrap = <String, Object?>{
        ...atlasVaultObject(root['bootstrap']),
      };
      final records = <Object?>[
        for (final record in atlasVaultList(bootstrap['records']))
          <String, Object?>{...atlasVaultObject(record)},
      ];
      (records.first as Map<String, Object?>)['key_id'] = value;
      bootstrap['records'] = records;

      expect(
        () => AtlasVaultPairingBootstrap.fromJson(bootstrap),
        throwsA(isA<AtlasVaultKeyDeliveryException>()),
        reason: value,
      );
    }
  });

  test(
    'wrong key material and expired delivery fail without secrets',
    () async {
      final secret = root['test_only_vault_key_b64']! as String;
      expect(
        () => openAtlasVaultKeyDelivery(
          AtlasVaultSignedVaultKeyDelivery.fromJson(
            atlasVaultObject(root['signed_delivery']),
          ),
          keyRequest: AtlasVaultSignedPairingKeyRequest.fromJson(
            atlasVaultObject(root['signed_key_request']),
          ),
          inviteeEphemeralPrivateKey: Uint8List(32),
          bootstrap: AtlasVaultPairingBootstrap.fromJson(
            atlasVaultObject(root['bootstrap']),
          ),
          transcriptSha256: _hex(root['transcript_sha256']! as String),
          currentTime: root['expired_verification_time']! as String,
        ),
        throwsA(
          isA<AtlasVaultKeyDeliveryException>().having(
            (error) => error.toString(),
            'redacted',
            isNot(contains(secret)),
          ),
        ),
      );
    },
  );

  test(
    'production delivery owns entropy across revision stress and crash retry',
    () async {
      final deliveries = <AtlasVaultSignedVaultKeyDelivery>[
        for (var index = 0; index < 96; index += 1)
          await _productionDelivery(root),
      ];
      expect(deliveries.map(_deliveryNonce).toSet(), hasLength(96));
      expect(
        deliveries.map(_deliveryEphemeralPublicKey).toSet(),
        hasLength(96),
      );

      final abandoned = await _productionDelivery(root);
      final recovered = await _productionDelivery(root);
      expect(_deliveryNonce(recovered), isNot(_deliveryNonce(abandoned)));
      expect(
        _deliveryEphemeralPublicKey(recovered),
        isNot(_deliveryEphemeralPublicKey(abandoned)),
      );
    },
  );

  test('production delivery owns entropy across concurrent attempts', () async {
    final deliveries = await Future.wait(
      <Future<AtlasVaultSignedVaultKeyDelivery>>[
        for (var index = 0; index < 48; index += 1) _productionDelivery(root),
      ],
    );
    expect(deliveries.map(_deliveryNonce).toSet(), hasLength(48));
    expect(deliveries.map(_deliveryEphemeralPublicKey).toSet(), hasLength(48));
  });
}

Future<AtlasVaultSignedVaultKeyDelivery> _productionDelivery(
  Map<String, Object?> root,
) async {
  return createAtlasVaultKeyDelivery(
    inviter: await _identity(root, 'inviter'),
    keyRequest: AtlasVaultSignedPairingKeyRequest.fromJson(
      atlasVaultObject(root['signed_key_request']),
    ),
    transcriptSha256: _hex(root['transcript_sha256']! as String),
    bootstrap: AtlasVaultPairingBootstrap.fromJson(
      atlasVaultObject(root['bootstrap']),
    ),
    vaultKey: _bytes(root['test_only_vault_key_b64']),
    deliveryId: root['delivery_id']! as String,
    keyEpoch: root['vault_key_epoch']! as int,
    expiresAt: root['delivery_expires_at']! as String,
  );
}

String _deliveryNonce(AtlasVaultSignedVaultKeyDelivery delivery) =>
    base64Encode(delivery.delivery.nonce);

String _deliveryEphemeralPublicKey(AtlasVaultSignedVaultKeyDelivery delivery) =>
    base64Encode(delivery.delivery.inviterEphemeralPublicKey);

Future<AtlasVaultDeviceIdentity> _identity(
  Map<String, Object?> root,
  String name,
) {
  final value = atlasVaultObject(root[name]);
  return AtlasVaultDeviceIdentity.fromPrivateKeys(
    signingPrivateSeed: _bytes(value['signing_private_seed_b64']),
    agreementPrivateKey: _bytes(value['agreement_private_key_b64']),
    createdAt: value['created_at']! as String,
    keyEpoch: value['key_epoch']! as int,
  );
}

Uint8List _bytes(Object? value) =>
    Uint8List.fromList(base64Decode(value! as String));

Uint8List _hex(String value) => Uint8List.fromList(<int>[
  for (var index = 0; index < value.length; index += 2)
    int.parse(value.substring(index, index + 2), radix: 16),
]);
