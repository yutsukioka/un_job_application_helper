import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/src/atlas_vault/hpke_key_delivery.dart'
    show sealAtlasVaultHPKEVaultKeyV2ForTesting;
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> vector;

  setUpAll(() {
    final root = loadAtlasVaultVector(
      'atlasvault_hpke_key_delivery_vectors_v2.json',
    );
    expect(root['format'], 'atlasvault-hpke-key-delivery-vectors');
    expect(root['version'], 2);
    vector = atlasVaultObject(root['single_shot']);
  });

  test('HPKE v2 matches the cross-language single-shot vector', () async {
    final sealed = await sealAtlasVaultHPKEVaultKeyV2ForTesting(
      recipientPublicKey: _bytes(vector, 'recipient_public_key_hex'),
      vaultKey: _bytes(vector, 'vault_key_hex'),
      context: _bytes(vector, 'context_hex'),
      ephemeralPrivateKey: _bytes(
        vector,
        'sender_ephemeral_private_key_hex',
      ),
    );

    expect(
      sealed.encapsulatedKey,
      _bytes(vector, 'encapsulated_key_hex'),
    );
    expect(sealed.ciphertext, _bytes(vector, 'ciphertext_hex'));
    expect(
      await openAtlasVaultHPKEVaultKeyV2(
        recipientPrivateKey: _bytes(vector, 'recipient_private_key_hex'),
        sealed: sealed,
        context: _bytes(vector, 'context_hex'),
      ),
      _bytes(vector, 'vault_key_hex'),
    );
  });

  test('HPKE v2 revision stress and crash retry own fresh entropy', () async {
    final attempts = <AtlasVaultHPKESealedVaultKeyV2>[];
    for (var revision = 0; revision < 96; revision += 1) {
      attempts.add(await _seal(vector));
    }
    expect(
      attempts.map((value) => _hex(value.encapsulatedKey)).toSet(),
      hasLength(96),
    );
    expect(
      attempts.map((value) => _hex(value.ciphertext)).toSet(),
      hasLength(96),
    );

    final abandoned = await _seal(vector);
    final recovered = await _seal(vector);
    expect(abandoned.encapsulatedKey, isNot(recovered.encapsulatedKey));
    expect(abandoned.ciphertext, isNot(recovered.ciphertext));
  });

  test('HPKE v2 concurrent attempts are unique and open', () async {
    final attempts = await Future.wait(
      List<Future<AtlasVaultHPKESealedVaultKeyV2>>.generate(
        48,
        (_) => _seal(vector),
      ),
    );
    expect(
      attempts.map((value) => _hex(value.encapsulatedKey)).toSet(),
      hasLength(48),
    );
    expect(
      attempts.map((value) => _hex(value.ciphertext)).toSet(),
      hasLength(48),
    );
    for (final sealed in attempts) {
      expect(
        await openAtlasVaultHPKEVaultKeyV2(
          recipientPrivateKey: _bytes(vector, 'recipient_private_key_hex'),
          sealed: sealed,
          context: _bytes(vector, 'context_hex'),
        ),
        _bytes(vector, 'vault_key_hex'),
      );
    }
  });

  test('HPKE v2 rejects wrong context and ciphertext tamper', () async {
    final sealed = await _seal(vector);
    await expectLater(
      openAtlasVaultHPKEVaultKeyV2(
        recipientPrivateKey: _bytes(vector, 'recipient_private_key_hex'),
        sealed: sealed,
        context: Uint8List.fromList('wrong-context'.codeUnits),
      ),
      throwsA(isA<AtlasVaultHPKEKeyDeliveryException>()),
    );

    final tamperedBytes = Uint8List.fromList(sealed.ciphertext);
    tamperedBytes[tamperedBytes.length - 1] ^= 1;
    final tampered = AtlasVaultHPKESealedVaultKeyV2(
      encapsulatedKey: sealed.encapsulatedKey,
      ciphertext: tamperedBytes,
    );
    await expectLater(
      openAtlasVaultHPKEVaultKeyV2(
        recipientPrivateKey: _bytes(vector, 'recipient_private_key_hex'),
        sealed: tampered,
        context: _bytes(vector, 'context_hex'),
      ),
      throwsA(isA<AtlasVaultHPKEKeyDeliveryException>()),
    );
  });
}

Future<AtlasVaultHPKESealedVaultKeyV2> _seal(
  Map<String, Object?> vector,
) {
  return sealAtlasVaultHPKEVaultKeyV2(
    recipientPublicKey: _bytes(vector, 'recipient_public_key_hex'),
    vaultKey: _bytes(vector, 'vault_key_hex'),
    context: _bytes(vector, 'context_hex'),
  );
}

Uint8List _bytes(Map<String, Object?> vector, String field) {
  return _decodeHex(vector[field]! as String);
}

Uint8List _decodeHex(String value) {
  if (value.length.isOdd) {
    throw StateError('Invalid hexadecimal vector value.');
  }
  return Uint8List.fromList(<int>[
    for (var index = 0; index < value.length; index += 2)
      int.parse(value.substring(index, index + 2), radix: 16),
  ]);
}

String _hex(List<int> value) {
  return value.map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();
}
