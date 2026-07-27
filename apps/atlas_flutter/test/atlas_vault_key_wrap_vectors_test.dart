import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> vector;
  late Map<String, Object?> metadataJson;
  late AtlasVaultPassphraseKeyWrapV1 wrap;
  late Uint8List vaultKey;
  late String passphrase;
  late String wrongPassphrase;

  setUp(() {
    final root = loadAtlasVaultVector('atlasvault_key_wrap_vectors_v1.json');
    vector = atlasVaultObject(atlasVaultList(root['vectors']).single);
    metadataJson = atlasVaultObject(vector['vault_metadata']);
    final metadata = AtlasVaultMetadata.fromJson(metadataJson);
    wrap = metadata.keyWraps.single as AtlasVaultPassphraseKeyWrapV1;
    vaultKey = Uint8List.fromList(
      base64Decode(vector['test_only_vault_key_b64']! as String),
    );
    passphrase = utf8.decode(_byteList(vector['test_only_input_utf8']));
    wrongPassphrase = utf8.decode(
      _byteList(vector['wrong_test_only_input_utf8']),
    );
  });

  test('historical v1 AAD is exact and excludes vault identity', () {
    final aad = atlasVaultPassphraseWrapV1Aad(wrap);
    final vaultId = metadataJson['vault_id']! as String;

    expect(aad, base64Decode(vector['key_wrap_aad_b64']! as String));
    expect(
      jsonDecode(utf8.decode(aad)),
      atlasVaultObject(vector['key_wrap_aad_json']),
    );
    expect(utf8.decode(aad), isNot(contains(vaultId)));
  });

  test('Argon2id v1 wrap seals and unwraps exact vector bytes', () async {
    final wrappingKey = await deriveAtlasVaultPassphraseWrappingKeyV1(
      passphrase: passphrase,
      parameters: wrap.kdf,
    );
    final sealed = await wrapAtlasVaultKeyWithPassphraseV1(
      vaultKey: vaultKey,
      passphrase: passphrase,
      keyId: wrap.id,
      parameters: wrap.kdf,
      nonce: wrap.nonce,
    );
    final opened = await unwrapAtlasVaultPassphraseWrapV1(
      wrap: wrap,
      passphrase: passphrase,
    );

    expect(wrappingKey, hasLength(32));
    expect(sealed.toJson(), wrap.toJson());
    expect(opened, vaultKey);
  });

  test('wrong passphrase and tampered v1 wrap fail closed', () async {
    final tamperedCiphertext = _clone(wrap.toJson());
    final ciphertext = base64Decode(tamperedCiphertext['ciphertext']! as String)
      ..[0] ^= 0x01;
    tamperedCiphertext['ciphertext'] = base64Encode(ciphertext);
    final tamperedNonce = _clone(wrap.toJson());
    final nonce = base64Decode(tamperedNonce['nonce']! as String)..[0] ^= 0x01;
    tamperedNonce['nonce'] = base64Encode(nonce);
    final tamperedSalt = _clone(wrap.toJson());
    final kdf = atlasVaultObject(tamperedSalt['kdf']);
    final salt = base64Decode(kdf['salt']! as String)..[0] ^= 0x01;
    kdf['salt'] = base64Encode(salt);
    final changedIterations = _clone(wrap.toJson());
    atlasVaultObject(changedIterations['kdf'])['iterations'] = 1;

    await expectLater(
      unwrapAtlasVaultPassphraseWrapV1(wrap: wrap, passphrase: wrongPassphrase),
      throwsA(isA<AtlasVaultCryptoException>()),
    );
    for (final invalid in <Map<String, Object?>>[
      tamperedCiphertext,
      tamperedNonce,
      tamperedSalt,
      changedIterations,
    ]) {
      await expectLater(
        unwrapAtlasVaultPassphraseWrapV1(
          wrap: AtlasVaultPassphraseKeyWrapV1.fromJson(invalid),
          passphrase: passphrase,
        ),
        throwsA(isA<AtlasVaultCryptoException>()),
      );
    }
  });

  test('Argon2id work factors are bounded before derivation', () {
    final excessiveValues = <String, int>{
      'memory_kib': AtlasVaultArgon2idParameters.maximumMemoryKib + 1,
      'iterations': AtlasVaultArgon2idParameters.maximumIterations + 1,
      'parallelism': AtlasVaultArgon2idParameters.maximumParallelism + 1,
    };

    for (final entry in excessiveValues.entries) {
      final invalid = _clone(wrap.toJson());
      atlasVaultObject(invalid['kdf'])[entry.key] = entry.value;

      expect(
        () => AtlasVaultPassphraseKeyWrapV1.fromJson(invalid),
        throwsA(isA<AtlasVaultFormatException>()),
      );
    }
  });

  test('serialized v1 metadata contains no passphrase or raw vault key', () {
    final serialized = jsonEncode(metadataJson);

    expect(serialized, isNot(contains(passphrase)));
    expect(
      serialized,
      isNot(contains(vector['test_only_vault_key_b64']! as String)),
    );
    expect(wrap.toString(), 'AtlasVaultPassphraseKeyWrapV1(<redacted>)');
  });
}

Uint8List _byteList(Object? value) {
  return Uint8List.fromList(
    atlasVaultList(value).map((item) => item! as int).toList(),
  );
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
