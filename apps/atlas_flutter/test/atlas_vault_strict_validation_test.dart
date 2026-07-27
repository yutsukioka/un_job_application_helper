import 'dart:convert';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/src/atlas_vault/canonical_json.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  group('canonical ASCII JSON', () {
    test('sorts recursively and preserves array order', () {
      final encoded = utf8.decode(
        encodeCanonicalJson(<String, Object?>{
          'z': <Object?>[3, 2, 1],
          'a': <String, Object?>{'z': true, 'a': null},
        }),
      );

      expect(encoded, '{"a":{"a":null,"z":true},"z":[3,2,1]}');
      expect(encoded.endsWith('\n'), isFalse);
    });

    test('uses Python-compatible lowercase Unicode escapes', () {
      final encoded = utf8.decode(
        encodeCanonicalJson(<String, Object?>{
          'text': 'quote" slash\\ tab\t line\n é 😀',
        }),
      );

      expect(
        encoded,
        r'{"text":"quote\" slash\\ tab\t line\n \u00e9 \ud83d\ude00"}',
      );
    });

    test('rejects unsupported values and non-string map keys', () {
      expect(
        () => encodeCanonicalJson(<Object?, Object?>{1: 'value'}),
        throwsA(isA<AtlasVaultFormatException>()),
      );
      expect(
        () => encodeCanonicalJson(1.5),
        throwsA(isA<AtlasVaultFormatException>()),
      );
    });
  });

  group('strict payload validation', () {
    late Map<String, Object?> savedSearch;

    setUp(() {
      final vectors = loadAtlasVaultVector(
        'atlasvault_payload_vectors_v1.json',
      );
      savedSearch = _clone(
        atlasVaultObject(atlasVaultObject(vectors['payloads'])['saved_search']),
      );
    });

    test(
      'rejects malformed JSON, fields, schema aliases, and null optionals',
      () {
        expect(
          () => AtlasVaultPayloadEnvelope.decodeJson('{'),
          throwsA(isA<AtlasVaultFormatException>()),
        );

        final missing = _clone(savedSearch)..remove('client_created_at');
        final unknown = _clone(savedSearch)..['unexpected'] = true;
        final booleanSchema = _clone(savedSearch)..['payload_schema'] = true;
        final floatingSchema = _clone(savedSearch)..['payload_schema'] = 1.0;
        final unsupportedSchema = _clone(savedSearch)..['payload_schema'] = 2;
        final unsupportedType = _clone(savedSearch)..['type'] = 'saved_text';
        final nullOptional = _clone(savedSearch);
        atlasVaultObject(nullOptional['payload'])['description'] = null;

        for (final invalid in <Map<String, Object?>>[
          missing,
          unknown,
          booleanSchema,
          floatingSchema,
          unsupportedSchema,
          unsupportedType,
          nullOptional,
        ]) {
          expect(
            () => AtlasVaultPayloadEnvelope.fromJson(invalid),
            throwsA(isA<AtlasVaultFormatException>()),
          );
        }
      },
    );

    test('rejects malformed date-only and timestamp values', () {
      final invalidDate = _clone(savedSearch);
      final request = atlasVaultObject(
        atlasVaultObject(invalidDate['payload'])['request'],
      );
      request['closing_date_to'] = '2026-02-30';

      final yearZero = _clone(savedSearch)
        ..['client_created_at'] = '0000-01-01T00:00:00Z';

      expect(
        () => AtlasVaultPayloadEnvelope.fromJson(invalidDate),
        throwsA(isA<AtlasVaultFormatException>()),
      );
      expect(
        () => AtlasVaultPayloadEnvelope.fromJson(yearZero),
        throwsA(isA<AtlasVaultFormatException>()),
      );
    });

    test('fixed validation errors do not echo private input', () {
      const sentinel = 'TOP_SECRET_VALIDATION_SENTINEL';
      final invalid = _clone(savedSearch)..[sentinel] = true;

      expect(
        () => AtlasVaultPayloadEnvelope.fromJson(invalid),
        throwsA(
          isA<AtlasVaultFormatException>().having(
            (error) => error.toString(),
            'redacted error',
            isNot(contains(sentinel)),
          ),
        ),
      );
    });
  });

  group('strict export validation', () {
    late Map<String, Object?> source;

    setUp(() {
      final vectors = loadAtlasVaultVector(
        'atlasvault_recovery_export_vectors_v2.json',
      );
      final vector = atlasVaultObject(
        atlasVaultList(vectors['vectors']).single,
      );
      source = _clone(atlasVaultObject(vector['export']));
    });

    test('accepts semantic whitespace and key reordering', () {
      final reordered = <String, Object?>{
        'records': source['records'],
        'vault_metadata': source['vault_metadata'],
        'created_at': source['created_at'],
        'export_id': source['export_id'],
        'version': source['version'],
        'format': source['format'],
      };

      final decoded = AtlasVaultEncryptedExport.decodeJson(
        const JsonEncoder.withIndent('  ').convert(reordered),
      );

      expect(decoded.toJson(), source);
    });

    test('preserves established opaque vault and record identifiers', () {
      final compatible = _clone(source);
      final metadata = atlasVaultObject(compatible['vault_metadata']);
      metadata['vault_id'] = 'vault_ABC-123';
      final cryptoVectors = loadAtlasVaultVector(
        'atlasvault_crypto_vectors_v1.json',
      );
      final cryptoVector = atlasVaultObject(
        atlasVaultList(cryptoVectors['vectors']).single,
      );
      compatible['records'] = <Object?>[
        _clone(atlasVaultObject(cryptoVector['record'])),
      ];
      final record = atlasVaultObject(
        atlasVaultList(compatible['records']).single,
      );
      record['id'] = 'record-one';
      record['revision'] = 'revision_one';
      record['parent_revision'] = 'revision-zero';

      final decoded = AtlasVaultEncryptedExport.fromJson(compatible);

      expect(decoded.vaultMetadata.vaultId, 'vault_ABC-123');
      expect(decoded.records.single.id, 'record-one');
      expect(decoded.records.single.revision, 'revision_one');
      expect(decoded.records.single.parentRevision, 'revision-zero');
    });

    test('rejects missing, unknown, null, Boolean, and floating fields', () {
      final missing = _clone(source)..remove('created_at');
      final unknown = _clone(source)..['unexpected'] = true;
      final nullOptional = _clone(source)..['created_at'] = null;
      final booleanVersion = _clone(source)..['version'] = true;
      final floatingVersion = _clone(source)..['version'] = 1.0;

      for (final invalid in <Map<String, Object?>>[
        missing,
        unknown,
        nullOptional,
        booleanVersion,
        floatingVersion,
      ]) {
        expect(
          () => AtlasVaultEncryptedExport.fromJson(invalid),
          throwsA(isA<AtlasVaultFormatException>()),
        );
      }
    });

    test('rejects malformed identifiers and UTC timestamps', () {
      for (final identifier in <String>[
        'AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE',
        'aaaaaaaabbbb4ccc8dddeeeeeeeeeeee',
        '{aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee}',
        ' aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
      ]) {
        final invalid = _clone(source)..['export_id'] = identifier;
        expect(
          () => AtlasVaultEncryptedExport.fromJson(invalid),
          throwsA(isA<AtlasVaultFormatException>()),
        );
      }

      for (final timestamp in <String>[
        '2026-01-02T03:04:05.000Z',
        '2026-01-02T03:04:05+00:00',
        '2026-01-02T03:04:05z',
        '2026-02-30T03:04:05Z',
      ]) {
        final invalid = _clone(source)..['created_at'] = timestamp;
        expect(
          () => AtlasVaultEncryptedExport.fromJson(invalid),
          throwsA(isA<AtlasVaultFormatException>()),
        );
      }
    });

    test('rejects noncanonical Base64 and duplicate wrap identifiers', () {
      final metadata = atlasVaultObject(source['vault_metadata']);
      final wraps = atlasVaultList(metadata['key_wraps']);
      final wrap = atlasVaultObject(wraps.single);

      for (final nonce in <String>[
        'AAECAwQFBgcICQoL====',
        'AAECAwQFBgcICQoL ',
        'AAECAwQFBgcICQo_',
        'AAECAwQFBgcICQo',
        'AAECAwQFBgcICQoL=',
        'AA==',
      ]) {
        final invalid = _clone(source);
        final invalidMetadata = atlasVaultObject(invalid['vault_metadata']);
        final invalidWrap = atlasVaultObject(
          atlasVaultList(invalidMetadata['key_wraps']).single,
        );
        invalidWrap['nonce'] = nonce;
        expect(
          () => AtlasVaultEncryptedExport.fromJson(invalid),
          throwsA(isA<AtlasVaultFormatException>()),
        );
      }

      final duplicate = _clone(source);
      final duplicateMetadata = atlasVaultObject(duplicate['vault_metadata']);
      duplicateMetadata['key_wraps'] = <Object?>[_clone(wrap), _clone(wrap)];
      expect(
        () => AtlasVaultEncryptedExport.fromJson(duplicate),
        throwsA(isA<AtlasVaultFormatException>()),
      );
    });

    test('dispatches passphrase v1 and recovery v2 wraps strictly', () {
      final keyWrapVectors = loadAtlasVaultVector(
        'atlasvault_key_wrap_vectors_v1.json',
      );
      final keyWrapVector = atlasVaultObject(
        atlasVaultList(keyWrapVectors['vectors']).single,
      );
      final passphraseMetadata = AtlasVaultMetadata.fromJson(
        atlasVaultObject(keyWrapVector['vault_metadata']),
      );
      final recoveryMetadata = AtlasVaultMetadata.fromJson(
        atlasVaultObject(source['vault_metadata']),
      );

      expect(
        passphraseMetadata.keyWraps.single,
        isA<AtlasVaultPassphraseKeyWrapV1>(),
      );
      expect(
        recoveryMetadata.keyWraps.single,
        isA<AtlasVaultRecoveryKeyWrapV2>(),
      );

      final invalidV1 = _clone(
        atlasVaultObject(keyWrapVector['vault_metadata']),
      );
      atlasVaultObject(
        atlasVaultList(invalidV1['key_wraps']).single,
      )['wrap_version'] = 1;
      final invalidV2 = _clone(source);
      atlasVaultObject(
        atlasVaultList(
          atlasVaultObject(invalidV2['vault_metadata'])['key_wraps'],
        ).single,
      )['wrap_version'] = true;

      expect(
        () => AtlasVaultMetadata.fromJson(invalidV1),
        throwsA(isA<AtlasVaultFormatException>()),
      );
      expect(
        () => AtlasVaultEncryptedExport.fromJson(invalidV2),
        throwsA(isA<AtlasVaultFormatException>()),
      );
    });
  });
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
