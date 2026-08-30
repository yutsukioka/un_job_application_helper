import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/src/atlas_vault/hpke_key_delivery.dart'
    show
        deriveAtlasVaultHPKEConformanceForTesting,
        sealAtlasVaultHPKEVaultKeyV2ForTesting;
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> vector;
  late Map<String, Object?> official;

  setUpAll(() {
    final root = loadAtlasVaultVector(
      'atlasvault_hpke_key_delivery_vectors_v2.json',
    );
    expect(root['format'], 'atlasvault-hpke-key-delivery-vectors');
    expect(root['version'], 2);
    vector = atlasVaultObject(root['single_shot']);
    official = atlasVaultObject(root['official_rfc9180']);
  });

  test('HPKE v2 matches the cross-language single-shot vector', () async {
    final sealed = await sealAtlasVaultHPKEVaultKeyV2ForTesting(
      recipientPublicKey: _bytes(vector, 'recipient_public_key_hex'),
      vaultKey: _bytes(vector, 'vault_key_hex'),
      context: _bytes(vector, 'context_hex'),
      ephemeralPrivateKey: _bytes(vector, 'sender_ephemeral_private_key_hex'),
    );

    expect(sealed.encapsulatedKey, _bytes(vector, 'encapsulated_key_hex'));
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

  test('HPKE v2 matches the official RFC 9180 vector byte-exact', () async {
    final result = await deriveAtlasVaultHPKEConformanceForTesting(
      recipientPublicKey: _bytes(official, 'recipient_public_key_hex'),
      ephemeralPrivateKey: _bytes(official, 'sender_ephemeral_private_key_hex'),
      info: _bytes(official, 'info_hex'),
      plaintext: _bytes(official, 'plaintext_hex'),
      aad: _bytes(official, 'aad_hex'),
    );

    expect(result.encapsulatedKey, _bytes(official, 'encapsulated_key_hex'));
    expect(result.sharedSecret, _bytes(official, 'shared_secret_hex'));
    expect(result.key, _bytes(official, 'key_hex'));
    expect(result.baseNonce, _bytes(official, 'base_nonce_hex'));
    expect(result.ciphertext, _bytes(official, 'ciphertext_hex'));
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

  test('HPKE HKDF expansion wipes intermediate key material', () {
    final source = File(
      'lib/src/atlas_vault/hpke_key_delivery.dart',
    ).readAsStringSync();

    expect(source, contains('final output = Uint8List(length);'));
    expect(source, contains('atlasVaultWipeBytesInternal(previous);'));
    expect(source, contains('atlasVaultWipeBytesInternal(output);'));
    expect(source, contains('atlasVaultWipeBytesInternal(hmacInput);'));
    expect(source, contains('atlasVaultWipeBytesInternal(labeledInput);'));
    expect(source, isNot(contains('inputKeyMaterial: _copyExact(dh')));
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

  test('HPKE v2 property round-trips deterministic inputs', () async {
    for (var index = 0; index < 32; index += 1) {
      final recipientPrivate = await _seed('recipient-$index');
      final recipientPair = await X25519().newKeyPairFromSeed(recipientPrivate);
      final recipientPublic = await recipientPair.extractPublicKey();
      final vaultKey = await _seed('vault-key-$index');
      final ephemeralPrivate = await _seed('ephemeral-$index');
      final contextSeed = await _seed('context-$index');
      final context = Uint8List.sublistView(contextSeed, 0, index + 1);

      final sealed = await sealAtlasVaultHPKEVaultKeyV2ForTesting(
        recipientPublicKey: Uint8List.fromList(recipientPublic.bytes),
        vaultKey: vaultKey,
        context: context,
        ephemeralPrivateKey: ephemeralPrivate,
      );
      expect(
        await openAtlasVaultHPKEVaultKeyV2(
          recipientPrivateKey: recipientPrivate,
          sealed: sealed,
          context: context,
        ),
        vaultKey,
      );
    }
  });

  test('HPKE v2 rejects compound mutations and wrong recipient', () async {
    final sealed = await sealAtlasVaultHPKEVaultKeyV2ForTesting(
      recipientPublicKey: _bytes(vector, 'recipient_public_key_hex'),
      vaultKey: _bytes(vector, 'vault_key_hex'),
      context: _bytes(vector, 'context_hex'),
      ephemeralPrivateKey: _bytes(vector, 'sender_ephemeral_private_key_hex'),
    );
    final recipient = _bytes(vector, 'recipient_private_key_hex');
    final context = _bytes(vector, 'context_hex');

    for (var index = 0; index < 64; index += 1) {
      final payload = Uint8List.fromList(sealed.ciphertext);
      payload[index % payload.length] ^= 1 << (index % 8);
      payload[(index * 13 + 7) % payload.length] ^= 1 << ((index + 3) % 8);
      await expectLater(
        openAtlasVaultHPKEVaultKeyV2(
          recipientPrivateKey: recipient,
          sealed: AtlasVaultHPKESealedVaultKeyV2(
            encapsulatedKey: sealed.encapsulatedKey,
            ciphertext: payload,
          ),
          context: context,
        ),
        throwsA(isA<AtlasVaultHPKEKeyDeliveryException>()),
      );
    }

    for (var index = 0; index < 32; index += 1) {
      final encapsulated = Uint8List.fromList(sealed.encapsulatedKey);
      encapsulated[index] ^= 1 << (index % 8);
      await expectLater(
        openAtlasVaultHPKEVaultKeyV2(
          recipientPrivateKey: recipient,
          sealed: AtlasVaultHPKESealedVaultKeyV2(
            encapsulatedKey: encapsulated,
            ciphertext: sealed.ciphertext,
          ),
          context: context,
        ),
        throwsA(isA<AtlasVaultHPKEKeyDeliveryException>()),
      );
    }

    await expectLater(
      openAtlasVaultHPKEVaultKeyV2(
        recipientPrivateKey: await _seed('wrong-recipient'),
        sealed: sealed,
        context: context,
      ),
      throwsA(isA<AtlasVaultHPKEKeyDeliveryException>()),
    );
  });

  test('HPKE v2 rejects malformed lengths and context boundaries', () async {
    final recipientPublic = _bytes(vector, 'recipient_public_key_hex');
    final recipientPrivate = _bytes(vector, 'recipient_private_key_hex');
    final vaultKey = _bytes(vector, 'vault_key_hex');

    for (final length in <int>[0, 1, 31, 33]) {
      await expectLater(
        sealAtlasVaultHPKEVaultKeyV2(
          recipientPublicKey: Uint8List(length),
          vaultKey: vaultKey,
          context: Uint8List.fromList(<int>[1]),
        ),
        throwsA(isA<AtlasVaultHPKEKeyDeliveryException>()),
      );
      await expectLater(
        sealAtlasVaultHPKEVaultKeyV2(
          recipientPublicKey: recipientPublic,
          vaultKey: Uint8List(length),
          context: Uint8List.fromList(<int>[1]),
        ),
        throwsA(isA<AtlasVaultHPKEKeyDeliveryException>()),
      );
    }

    for (final context in <Uint8List>[Uint8List(0), Uint8List(4097)]) {
      await expectLater(
        sealAtlasVaultHPKEVaultKeyV2(
          recipientPublicKey: recipientPublic,
          vaultKey: vaultKey,
          context: context,
        ),
        throwsA(isA<AtlasVaultHPKEKeyDeliveryException>()),
      );
    }

    final boundary = await sealAtlasVaultHPKEVaultKeyV2(
      recipientPublicKey: recipientPublic,
      vaultKey: vaultKey,
      context: Uint8List(4096),
    );
    expect(
      await openAtlasVaultHPKEVaultKeyV2(
        recipientPrivateKey: recipientPrivate,
        sealed: boundary,
        context: Uint8List(4096),
      ),
      vaultKey,
    );
  });
}

Future<AtlasVaultHPKESealedVaultKeyV2> _seal(Map<String, Object?> vector) {
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

Future<Uint8List> _seed(String label) async {
  final digest = await Sha256().hash(ascii.encode(label));
  return Uint8List.fromList(digest.bytes);
}
