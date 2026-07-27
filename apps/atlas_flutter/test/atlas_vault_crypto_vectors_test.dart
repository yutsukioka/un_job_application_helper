import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> vector;
  late Map<String, Object?> recordJson;
  late AtlasVaultEncryptedRecord record;
  late Uint8List vaultKey;
  late Uint8List plaintext;
  late String vaultId;

  setUp(() {
    final root = loadAtlasVaultVector('atlasvault_crypto_vectors_v1.json');
    vector = atlasVaultObject(atlasVaultList(root['vectors']).single);
    recordJson = _clone(atlasVaultObject(vector['record']));
    record = AtlasVaultEncryptedRecord.fromJson(recordJson);
    vaultKey = Uint8List.fromList(
      base64Decode(vector['test_only_vault_key_b64']! as String),
    );
    plaintext = Uint8List.fromList(
      base64Decode(vector['plaintext_json_b64']! as String),
    );
    vaultId = atlasVaultObject(vector['vault'])['vault_id']! as String;
  });

  test('record HKDF and AAD match the shared vector exactly', () async {
    final recordKey = await deriveAtlasVaultRecordKey(
      vaultKey: vaultKey,
      vaultId: vaultId,
      recordId: record.id,
    );
    final aad = atlasVaultRecordAad(vaultId: vaultId, record: record);

    expect(recordKey, base64Decode(vector['record_key_b64']! as String));
    expect(aad, base64Decode(vector['aad_b64']! as String));
    expect(jsonDecode(utf8.decode(aad)), atlasVaultObject(vector['aad_json']));
  });

  test('record AES-GCM opens and seals to exact vector bytes', () async {
    final opened = await openAtlasVaultRecord(
      vaultKey: vaultKey,
      vaultId: vaultId,
      record: record,
    );
    final sealed = await sealAtlasVaultRecord(
      plaintext: plaintext,
      vaultKey: vaultKey,
      vaultId: vaultId,
      record: record,
    );

    expect(opened, plaintext);
    expect(sealed.toJson(), recordJson);
  });

  test('record HKDF rejects malformed UTF-16 IDs before encoding', () async {
    final malformed = String.fromCharCode(0xd800);

    await expectLater(
      deriveAtlasVaultRecordKey(
        vaultKey: vaultKey,
        vaultId: vaultId,
        recordId: malformed,
      ),
      throwsA(isA<AtlasVaultCryptoException>()),
    );
    expect(
      () => AtlasVaultEncryptedRecord.fromJson(
        _clone(recordJson)..['id'] = malformed,
      ),
      throwsA(isA<AtlasVaultFormatException>()),
    );

    final replacementCharacterKey = await deriveAtlasVaultRecordKey(
      vaultKey: vaultKey,
      vaultId: vaultId,
      recordId: '\ufffd',
    );
    expect(replacementCharacterKey, hasLength(32));
    replacementCharacterKey.fillRange(0, replacementCharacterKey.length, 0);
  });

  test('wrong keys, AAD changes, nonce changes, and ciphertext fail', () async {
    final wrongKey = Uint8List.fromList(vaultKey)..[0] ^= 0xff;
    final wrongRecordId = _clone(recordJson)..['id'] = 'different-record';
    final wrongRevision = _clone(recordJson)
      ..['revision'] = 'different-revision';
    final wrongParent = _clone(recordJson)
      ..['parent_revision'] = 'different-parent';
    final wrongDeleted = _clone(recordJson)..['deleted'] = true;
    final wrongKeyId = _clone(recordJson)..['key_id'] = 'different-key';
    final wrongNonce = _tamperBase64(recordJson, 'nonce', 0);
    final wrongCiphertext = _tamperBase64(recordJson, 'ciphertext', 0);
    final wrongTag = _tamperBase64(
      recordJson,
      'ciphertext',
      record.ciphertext.length - 1,
    );

    final attempts = <Future<Uint8List>>[
      openAtlasVaultRecord(
        vaultKey: wrongKey,
        vaultId: vaultId,
        record: record,
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: 'different-vault',
        record: record,
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: vaultId,
        record: AtlasVaultEncryptedRecord.fromJson(wrongRecordId),
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: vaultId,
        record: AtlasVaultEncryptedRecord.fromJson(wrongRevision),
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: vaultId,
        record: AtlasVaultEncryptedRecord.fromJson(wrongParent),
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: vaultId,
        record: AtlasVaultEncryptedRecord.fromJson(wrongDeleted),
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: vaultId,
        record: AtlasVaultEncryptedRecord.fromJson(wrongKeyId),
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: vaultId,
        record: AtlasVaultEncryptedRecord.fromJson(wrongNonce),
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: vaultId,
        record: AtlasVaultEncryptedRecord.fromJson(wrongCiphertext),
      ),
      openAtlasVaultRecord(
        vaultKey: vaultKey,
        vaultId: vaultId,
        record: AtlasVaultEncryptedRecord.fromJson(wrongTag),
      ),
    ];

    for (final attempt in attempts) {
      await expectLater(attempt, throwsA(isA<AtlasVaultCryptoException>()));
    }
  });

  test('encrypted record metadata contains no forbidden payload sentinel', () {
    final serialized = jsonEncode(record.toJson());

    for (final value in atlasVaultList(vector['forbidden_plaintext_strings'])) {
      expect(serialized, isNot(contains(value! as String)));
    }
    expect(record.toString(), 'AtlasVaultEncryptedRecord(<redacted>)');
  });
}

Map<String, Object?> _tamperBase64(
  Map<String, Object?> source,
  String field,
  int index,
) {
  final result = _clone(source);
  final bytes = base64Decode(result[field]! as String);
  bytes[index] ^= 0x01;
  result[field] = base64Encode(bytes);
  return result;
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
