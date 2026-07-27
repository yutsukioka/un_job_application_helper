import 'dart:convert';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:pointycastle/export.dart'
    show Argon2BytesGenerator, Argon2Parameters;

import 'canonical_json.dart';
import 'models.dart';
import 'strict_values.dart';

final class AtlasVaultCryptoException implements Exception {
  const AtlasVaultCryptoException();

  @override
  String toString() => 'AtlasVault cryptographic operation failed.';
}

const _vaultKeyByteCount = 32;
const _nonceByteCount = 12;
const _tagByteCount = 16;

Future<Uint8List> deriveAtlasVaultRecordKey({
  required Uint8List vaultKey,
  required String vaultId,
  required String recordId,
}) async {
  _requireLength(vaultKey, _vaultKeyByteCount);
  _requireVaultId(vaultId);
  _requireRecordId(recordId);
  return atlasVaultDeriveHkdfSha256Internal(
    inputKeyMaterial: vaultKey,
    salt: utf8.encode('${AtlasVaultMetadata.format}:v1:$vaultId'),
    info: utf8.encode('record:$recordId'),
  );
}

Uint8List atlasVaultRecordAad({
  required String vaultId,
  required AtlasVaultEncryptedRecord record,
}) {
  _requireVaultId(vaultId);
  return encodeCanonicalJson(<String, Object?>{
    'vault_format': AtlasVaultMetadata.format,
    'vault_version': AtlasVaultMetadata.version,
    'vault_id': vaultId,
    'record_id': record.id,
    'record_schema_version': record.schemaVersion,
    'revision': record.revision,
    'parent_revision': record.parentRevision,
    'deleted': record.deleted,
    'key_id': record.keyId,
  });
}

Future<Uint8List> openAtlasVaultRecord({
  required Uint8List vaultKey,
  required String vaultId,
  required AtlasVaultEncryptedRecord record,
}) async {
  Uint8List? recordKey;
  try {
    recordKey = await deriveAtlasVaultRecordKey(
      vaultKey: vaultKey,
      vaultId: vaultId,
      recordId: record.id,
    );
    return await atlasVaultOpenAes256GcmInternal(
      ciphertextAndTag: record.ciphertext,
      key: recordKey,
      nonce: record.nonce,
      aad: atlasVaultRecordAad(vaultId: vaultId, record: record),
    );
  } on AtlasVaultCryptoException {
    rethrow;
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    atlasVaultWipeBytesInternal(recordKey);
  }
}

Future<AtlasVaultEncryptedRecord> sealAtlasVaultRecord({
  required Uint8List plaintext,
  required Uint8List vaultKey,
  required String vaultId,
  required AtlasVaultEncryptedRecord record,
}) async {
  Uint8List? recordKey;
  try {
    recordKey = await deriveAtlasVaultRecordKey(
      vaultKey: vaultKey,
      vaultId: vaultId,
      recordId: record.id,
    );
    final ciphertext = await atlasVaultSealAes256GcmInternal(
      plaintext: plaintext,
      key: recordKey,
      nonce: record.nonce,
      aad: atlasVaultRecordAad(vaultId: vaultId, record: record),
    );
    return AtlasVaultEncryptedRecord.fromJson(<String, Object?>{
      ...record.toJson(),
      'ciphertext': base64Encode(ciphertext),
    });
  } on AtlasVaultCryptoException {
    rethrow;
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    atlasVaultWipeBytesInternal(recordKey);
  }
}

Uint8List atlasVaultPassphraseWrapV1Aad(AtlasVaultPassphraseKeyWrapV1 wrap) {
  return encodeCanonicalJson(<String, Object?>{
    'format': 'atlas-vault-key-wrap',
    'version': 1,
    'id': wrap.id,
    'type': AtlasVaultPassphraseKeyWrapV1.type,
    'kdf': wrap.kdf.toJson(),
  });
}

Future<Uint8List> deriveAtlasVaultPassphraseWrappingKeyV1({
  required String passphrase,
  required AtlasVaultArgon2idParameters parameters,
}) async {
  if (passphrase.isEmpty) {
    throw const AtlasVaultCryptoException();
  }
  Uint8List? passphraseBytes;
  Uint8List? salt;
  try {
    requireAtlasVaultWellFormedUtf16(passphrase, field: 'passphrase');
    passphraseBytes = Uint8List.fromList(utf8.encode(passphrase));
    salt = parameters.salt;
    final pointyParameters = Argon2Parameters(
      Argon2Parameters.ARGON2_id,
      salt,
      desiredKeyLength: _vaultKeyByteCount,
      iterations: parameters.iterations,
      memory: parameters.memoryKib,
      lanes: parameters.parallelism,
      version: Argon2Parameters.ARGON2_VERSION_13,
    );
    final generator = Argon2BytesGenerator()..init(pointyParameters);
    final derivedBytes = generator.process(passphraseBytes);
    return atlasVaultCopyAndWipeBytesInternal(derivedBytes);
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    atlasVaultWipeBytesInternal(passphraseBytes);
    atlasVaultWipeBytesInternal(salt);
  }
}

Future<AtlasVaultPassphraseKeyWrapV1> wrapAtlasVaultKeyWithPassphraseV1({
  required Uint8List vaultKey,
  required String passphrase,
  required String keyId,
  required AtlasVaultArgon2idParameters parameters,
  Uint8List? nonce,
}) async {
  _requireLength(vaultKey, _vaultKeyByteCount);
  if (keyId.isEmpty) {
    throw const AtlasVaultCryptoException();
  }
  final wrapNonce = nonce == null
      ? Uint8List.fromList(AesGcm.with256bits().newNonce())
      : Uint8List.fromList(nonce);
  _requireLength(wrapNonce, _nonceByteCount);
  Uint8List? wrappingKey;
  try {
    final shell = AtlasVaultPassphraseKeyWrapV1.fromJson(<String, Object?>{
      'id': keyId,
      'type': AtlasVaultPassphraseKeyWrapV1.type,
      'kdf': parameters.toJson(),
      'nonce': base64Encode(wrapNonce),
      'ciphertext': base64Encode(Uint8List(48)),
    });
    wrappingKey = await deriveAtlasVaultPassphraseWrappingKeyV1(
      passphrase: passphrase,
      parameters: parameters,
    );
    final ciphertext = await atlasVaultSealAes256GcmInternal(
      plaintext: vaultKey,
      key: wrappingKey,
      nonce: wrapNonce,
      aad: atlasVaultPassphraseWrapV1Aad(shell),
    );
    return AtlasVaultPassphraseKeyWrapV1.fromJson(<String, Object?>{
      ...shell.toJson(),
      'ciphertext': base64Encode(ciphertext),
    });
  } on AtlasVaultCryptoException {
    rethrow;
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    atlasVaultWipeBytesInternal(wrappingKey);
  }
}

Future<Uint8List> unwrapAtlasVaultPassphraseWrapV1({
  required AtlasVaultPassphraseKeyWrapV1 wrap,
  required String passphrase,
}) async {
  Uint8List? wrappingKey;
  Uint8List? plaintext;
  try {
    wrappingKey = await deriveAtlasVaultPassphraseWrappingKeyV1(
      passphrase: passphrase,
      parameters: wrap.kdf,
    );
    plaintext = await atlasVaultOpenAes256GcmInternal(
      ciphertextAndTag: wrap.ciphertext,
      key: wrappingKey,
      nonce: wrap.nonce,
      aad: atlasVaultPassphraseWrapV1Aad(wrap),
    );
    _requireLength(plaintext, _vaultKeyByteCount);
    return Uint8List.fromList(plaintext);
  } on AtlasVaultCryptoException {
    rethrow;
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    atlasVaultWipeBytesInternal(wrappingKey);
    atlasVaultWipeBytesInternal(plaintext);
  }
}

Future<String> atlasVaultSha256Hex(Uint8List bytes) async {
  try {
    final digest = await Sha256().hash(bytes);
    return digest.bytes
        .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
        .join();
  } catch (_) {
    throw const AtlasVaultCryptoException();
  }
}

Future<Uint8List> atlasVaultDeriveHkdfSha256Internal({
  required Uint8List inputKeyMaterial,
  required List<int> salt,
  required List<int> info,
}) async {
  final inputKey = SecretKeyData(
    Uint8List.fromList(inputKeyMaterial),
    overwriteWhenDestroyed: true,
  );
  SecretKey? derivedKey;
  try {
    derivedKey = await Hkdf(
      hmac: Hmac.sha256(),
      outputLength: _vaultKeyByteCount,
    ).deriveKey(secretKey: inputKey, nonce: salt, info: info);
    final extractedBytes = await derivedKey.extractBytes();
    return atlasVaultCopyAndWipeBytesInternal(extractedBytes);
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    inputKey.destroy();
    derivedKey?.destroy();
  }
}

Future<Uint8List> atlasVaultSealAes256GcmInternal({
  required Uint8List plaintext,
  required Uint8List key,
  required Uint8List nonce,
  required Uint8List aad,
}) async {
  _requireLength(key, _vaultKeyByteCount);
  _requireLength(nonce, _nonceByteCount);
  final secretKey = SecretKeyData(
    Uint8List.fromList(key),
    overwriteWhenDestroyed: true,
  );
  try {
    final box = await AesGcm.with256bits().encrypt(
      plaintext,
      secretKey: secretKey,
      nonce: nonce,
      aad: aad,
    );
    return Uint8List.fromList(<int>[...box.cipherText, ...box.mac.bytes]);
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    secretKey.destroy();
  }
}

Future<Uint8List> atlasVaultOpenAes256GcmInternal({
  required Uint8List ciphertextAndTag,
  required Uint8List key,
  required Uint8List nonce,
  required Uint8List aad,
}) async {
  _requireLength(key, _vaultKeyByteCount);
  _requireLength(nonce, _nonceByteCount);
  if (ciphertextAndTag.length < _tagByteCount) {
    throw const AtlasVaultCryptoException();
  }
  final split = ciphertextAndTag.length - _tagByteCount;
  final secretKey = SecretKeyData(
    Uint8List.fromList(key),
    overwriteWhenDestroyed: true,
  );
  List<int>? opened;
  try {
    opened = await AesGcm.with256bits().decrypt(
      SecretBox(
        Uint8List.fromList(ciphertextAndTag.sublist(0, split)),
        nonce: Uint8List.fromList(nonce),
        mac: Mac(Uint8List.fromList(ciphertextAndTag.sublist(split))),
      ),
      secretKey: secretKey,
      aad: aad,
    );
    return Uint8List.fromList(opened);
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    secretKey.destroy();
    atlasVaultWipeBytesInternal(opened);
  }
}

void _requireLength(List<int> value, int expected) {
  if (value.length != expected) {
    throw const AtlasVaultCryptoException();
  }
}

void _requireVaultId(String vaultId) {
  try {
    requireAtlasVaultVaultId(vaultId);
  } on AtlasVaultFormatException {
    throw const AtlasVaultCryptoException();
  }
}

void _requireRecordId(String recordId) {
  try {
    requireAtlasVaultString(recordId, field: 'record.id', allowEmpty: false);
  } on AtlasVaultFormatException {
    throw const AtlasVaultCryptoException();
  }
}

void atlasVaultWipeBytesInternal(List<int>? value) {
  if (value == null) {
    return;
  }
  try {
    value.fillRange(0, value.length, 0);
  } catch (_) {
    // Dart collection implementations do not all guarantee mutability.
  }
}

Uint8List atlasVaultCopyAndWipeBytesInternal(List<int> value) {
  try {
    return Uint8List.fromList(value);
  } finally {
    atlasVaultWipeBytesInternal(value);
  }
}
