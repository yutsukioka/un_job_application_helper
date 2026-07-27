import 'dart:convert';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  test('all shared payload envelopes decode and round trip semantically', () {
    final vectors = loadAtlasVaultVector('atlasvault_payload_vectors_v1.json');
    final payloads = atlasVaultObject(vectors['payloads']);

    expect(
      payloads.keys,
      containsAll(<String>[
        'saved_search',
        'saved_job',
        'application_note',
        'profile_snippet',
        'draft_metadata',
      ]),
    );

    for (final entry in payloads.entries) {
      final source = atlasVaultObject(entry.value);
      final envelope = AtlasVaultPayloadEnvelope.fromJson(source);

      expect(envelope.type.wireName, entry.key);
      expect(envelope.payloadSchema, 1);
      expect(envelope.toJson(), source);
      expect(
        AtlasVaultPayloadEnvelope.decodeJson(
          const JsonEncoder.withIndent('  ').convert(source),
        ),
        envelope,
      );
    }
  });

  test('saved-search canonical plaintext bytes match the crypto vector', () {
    final payloadVectors = loadAtlasVaultVector(
      'atlasvault_payload_vectors_v1.json',
    );
    final payloads = atlasVaultObject(payloadVectors['payloads']);
    final savedSearch = AtlasVaultPayloadEnvelope.fromJson(
      atlasVaultObject(payloads['saved_search']),
    );
    final cryptoVectors = loadAtlasVaultVector(
      'atlasvault_crypto_vectors_v1.json',
    );
    final vector = atlasVaultObject(
      atlasVaultList(cryptoVectors['vectors']).single,
    );

    expect(
      savedSearch.canonicalBytes(),
      base64Decode(vector['plaintext_json_b64']! as String),
    );
  });

  test('absent payload optional fields remain omitted', () {
    final vectors = loadAtlasVaultVector('atlasvault_payload_vectors_v1.json');
    final payloads = atlasVaultObject(vectors['payloads']);
    final source = atlasVaultObject(payloads['saved_job']);
    final payload = atlasVaultObject(source['payload'])
      ..remove('notes')
      ..remove('applied_at');
    final withoutOptionals = <String, Object?>{...source, 'payload': payload};

    final envelope = AtlasVaultPayloadEnvelope.fromJson(withoutOptionals);
    final encodedPayload = atlasVaultObject(envelope.toJson()['payload']);

    expect(encodedPayload, isNot(contains('notes')));
    expect(encodedPayload, isNot(contains('applied_at')));
  });
}
