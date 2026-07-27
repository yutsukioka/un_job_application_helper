import 'dart:convert';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> localStore;

  setUp(() {
    final recoveryVectors = loadAtlasVaultVector(
      'atlasvault_recovery_export_vectors_v2.json',
    );
    final recoveryVector = atlasVaultObject(
      atlasVaultList(recoveryVectors['vectors']).single,
    );
    final cryptoVectors = loadAtlasVaultVector(
      'atlasvault_crypto_vectors_v1.json',
    );
    final cryptoVector = atlasVaultObject(
      atlasVaultList(cryptoVectors['vectors']).single,
    );
    localStore = <String, Object?>{
      'format': 'atlasvault-local-store',
      'version': 1,
      'store_id': '99999999-8888-4777-8666-555555555555',
      'created_at': '2026-01-01T00:00:00Z',
      'updated_at': '2026-01-02T00:00:00Z',
      'vault_metadata': recoveryVector['vault_metadata'],
      'records': <Object?>[cryptoVector['record']],
    };
  });

  test('strict local store decodes and canonically round trips', () {
    final store = AtlasVaultLocalStore.decodeJson(
      const JsonEncoder.withIndent('  ').convert(localStore),
    );

    expect(store.toJson(), localStore);
    expect(
      AtlasVaultLocalStore.decodeJson(utf8.decode(store.canonicalBytes())),
      store,
    );
    expect(store.records, hasLength(1));
  });

  test('local store preserves encrypted record order', () {
    final second =
        _clone(atlasVaultObject(atlasVaultList(localStore['records']).single))
          ..['id'] = '00000000-0000-4000-8000-000000000202'
          ..['revision'] = '00000000-0000-4000-8001-000000000202';
    localStore['records'] = <Object?>[
      ...atlasVaultList(localStore['records']),
      second,
    ];

    final restored = AtlasVaultLocalStore.fromJson(localStore);

    expect(restored.records.map((record) => record.id), <String>[
      '00000000-0000-4000-8000-000000000201',
      '00000000-0000-4000-8000-000000000202',
    ]);
  });

  test('local store rejects unknown fields and malformed records', () {
    final unknown = _clone(localStore)..['path'] = '/private/value';
    final badNonce = _clone(localStore);
    atlasVaultObject(atlasVaultList(badNonce['records']).single)['nonce'] =
        'AA==';

    expect(
      () => AtlasVaultLocalStore.fromJson(unknown),
      throwsA(isA<AtlasVaultFormatException>()),
    );
    expect(
      () => AtlasVaultLocalStore.fromJson(badNonce),
      throwsA(isA<AtlasVaultFormatException>()),
    );
  });

  test('serialized local store contains no payload sentinel', () {
    final store = AtlasVaultLocalStore.fromJson(localStore);
    final serialized = utf8.decode(store.canonicalBytes());

    for (final forbidden in <String>[
      'saved_search',
      'FAKE_SAVED_SEARCH_NAME_DO_NOT_LEAK',
      'FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK',
      'TOP_SECRET_SENTINEL_DO_NOT_LEAK',
    ]) {
      expect(serialized, isNot(contains(forbidden)));
    }
  });
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
