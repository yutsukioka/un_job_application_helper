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
  });
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
