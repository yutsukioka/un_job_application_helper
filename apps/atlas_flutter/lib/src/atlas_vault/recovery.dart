import 'dart:convert';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';

import 'canonical_json.dart';
import 'crypto.dart';
import 'export.dart';
import 'models.dart';
import 'strict_values.dart';

final class AtlasVaultRecoveryKey {
  AtlasVaultRecoveryKey._(Uint8List bytes) : _bytes = Uint8List.fromList(bytes);

  static const rawByteCount = 32;
  static const checksumByteCount = 5;
  static const prefix = 'AVRK1';
  static const _symbolCount = 60;
  static const _alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  static final Uint8List _checksumDomain = Uint8List.fromList(
    utf8.encode('atlasvault-recovery-key-v1:'),
  );

  final Uint8List _bytes;

  factory AtlasVaultRecoveryKey.fromBytes(Uint8List bytes) {
    if (bytes.length != rawByteCount) {
      throw const AtlasVaultCryptoException();
    }
    return AtlasVaultRecoveryKey._(bytes);
  }

  factory AtlasVaultRecoveryKey.generate() {
    final secret = SecretKeyData.random(
      length: rawByteCount,
      debugLabel: 'AtlasVault recovery key',
    );
    try {
      return AtlasVaultRecoveryKey.fromBytes(Uint8List.fromList(secret.bytes));
    } finally {
      secret.destroy();
    }
  }

  factory AtlasVaultRecoveryKey.parse(String value) {
    Uint8List? decoded;
    Uint8List? actualChecksum;
    try {
      if (value.codeUnits.any((unit) => unit > 0x7f)) {
        throw const AtlasVaultCryptoException();
      }
      final normalized = _trimAsciiWhitespace(value).toUpperCase();
      if (normalized.contains('=')) {
        throw const AtlasVaultCryptoException();
      }
      final parts = normalized.split(RegExp(r'[- ]+'));
      if (parts.length != 16 ||
          parts.first != prefix ||
          parts.skip(1).any((part) => part.length != 4)) {
        throw const AtlasVaultCryptoException();
      }
      final symbols = parts.skip(1).join();
      if (symbols.length != _symbolCount ||
          symbols.codeUnits.any(
            (unit) => !_alphabet.codeUnits.contains(unit),
          )) {
        throw const AtlasVaultCryptoException();
      }
      decoded = _decodeBase32(symbols);
      if (decoded.length != rawByteCount + checksumByteCount ||
          _encodeBase32(decoded) != symbols) {
        throw const AtlasVaultCryptoException();
      }
      final raw = Uint8List.fromList(decoded.sublist(0, rawByteCount));
      actualChecksum = _checksum(raw);
      final suppliedChecksum = decoded.sublist(rawByteCount);
      if (!_constantTimeEqual(actualChecksum, suppliedChecksum)) {
        _wipe(raw);
        throw const AtlasVaultCryptoException();
      }
      final result = AtlasVaultRecoveryKey.fromBytes(raw);
      _wipe(raw);
      return result;
    } on AtlasVaultCryptoException {
      rethrow;
    } catch (_) {
      throw const AtlasVaultCryptoException();
    } finally {
      _wipe(decoded);
      _wipe(actualChecksum);
    }
  }

  String get canonicalText {
    final checksum = _checksum(_bytes);
    final combined = Uint8List(rawByteCount + checksumByteCount)
      ..setAll(0, _bytes)
      ..setAll(rawByteCount, checksum);
    try {
      final symbols = _encodeBase32(combined);
      final groups = <String>[
        for (var offset = 0; offset < symbols.length; offset += 4)
          symbols.substring(offset, offset + 4),
      ];
      return '$prefix-${groups.join('-')}';
    } finally {
      _wipe(checksum);
      _wipe(combined);
    }
  }

  Uint8List copyBytes() => Uint8List.fromList(_bytes);

  @override
  String toString() => 'AtlasVaultRecoveryKey(<redacted>)';

  static Uint8List _checksum(Uint8List bytes) {
    final input = Uint8List(_checksumDomain.length + bytes.length)
      ..setAll(0, _checksumDomain)
      ..setAll(_checksumDomain.length, bytes);
    try {
      final digest = Sha256().toSync().hashSync(input);
      return Uint8List.fromList(digest.bytes.sublist(0, checksumByteCount));
    } finally {
      _wipe(input);
    }
  }

  static String _encodeBase32(List<int> bytes) {
    final output = StringBuffer();
    var buffer = 0;
    var bitCount = 0;
    for (final byte in bytes) {
      buffer = (buffer << 8) | byte;
      bitCount += 8;
      while (bitCount >= 5) {
        bitCount -= 5;
        output.write(_alphabet[(buffer >> bitCount) & 0x1f]);
      }
      buffer = bitCount == 0 ? 0 : buffer & ((1 << bitCount) - 1);
    }
    if (bitCount > 0) {
      output.write(_alphabet[(buffer << (5 - bitCount)) & 0x1f]);
    }
    return output.toString();
  }

  static Uint8List _decodeBase32(String symbols) {
    final output = BytesBuilder(copy: false);
    var buffer = 0;
    var bitCount = 0;
    for (final symbol in symbols.codeUnits) {
      final index = _alphabet.codeUnits.indexOf(symbol);
      if (index < 0) {
        throw const AtlasVaultCryptoException();
      }
      buffer = (buffer << 5) | index;
      bitCount += 5;
      while (bitCount >= 8) {
        bitCount -= 8;
        output.addByte((buffer >> bitCount) & 0xff);
      }
      buffer = bitCount == 0 ? 0 : buffer & ((1 << bitCount) - 1);
    }
    if (bitCount != 4 || buffer != 0) {
      throw const AtlasVaultCryptoException();
    }
    return output.toBytes();
  }
}

Uint8List atlasVaultRecoveryWrapV2Aad({
  required String vaultId,
  required AtlasVaultRecoveryKeyWrapV2 wrap,
}) {
  _requireRecoveryVaultId(vaultId);
  return encodeCanonicalJson(<String, Object?>{
    'format': 'atlas-vault-key-wrap',
    'version': AtlasVaultRecoveryKeyWrapV2.wrapVersion,
    'vault_id': vaultId,
    'id': AtlasVaultRecoveryKeyWrapV2.supportedId,
    'type': AtlasVaultRecoveryKeyWrapV2.type,
    'key_wrap_aead': AtlasVaultCryptoSuite.keyWrapAead,
    'kdf': wrap.kdf.toJson(),
  });
}

Future<AtlasVaultRecoveryKeyWrapV2> wrapAtlasVaultKeyWithRecoveryV2({
  required Uint8List vaultKey,
  required AtlasVaultRecoveryKey recoveryKey,
  required String vaultId,
  Uint8List? salt,
  Uint8List? nonce,
}) async {
  if (vaultKey.length != 32) {
    throw const AtlasVaultCryptoException();
  }
  _requireRecoveryVaultId(vaultId);
  final wrapSalt = salt == null
      ? _secureRandomBytes(AtlasVaultRecoveryWrapKdfParameters.saltByteCount)
      : Uint8List.fromList(salt);
  final wrapNonce = nonce == null
      ? Uint8List.fromList(AesGcm.with256bits().newNonce())
      : Uint8List.fromList(nonce);
  if (wrapSalt.length != AtlasVaultRecoveryWrapKdfParameters.saltByteCount ||
      wrapNonce.length != AtlasVaultRecoveryKeyWrapV2.nonceByteCount) {
    throw const AtlasVaultCryptoException();
  }
  Uint8List? wrappingKey;
  Uint8List? recoveryBytes;
  try {
    final kdf = AtlasVaultRecoveryWrapKdfParameters.fromJson(<String, Object?>{
      'algorithm': AtlasVaultRecoveryWrapKdfParameters.algorithm,
      'salt': base64Encode(wrapSalt),
      'info': AtlasVaultRecoveryWrapKdfParameters.info,
    });
    final shell = AtlasVaultRecoveryKeyWrapV2.fromJson(<String, Object?>{
      'id': AtlasVaultRecoveryKeyWrapV2.supportedId,
      'type': AtlasVaultRecoveryKeyWrapV2.type,
      'wrap_version': AtlasVaultRecoveryKeyWrapV2.wrapVersion,
      'kdf': kdf.toJson(),
      'nonce': base64Encode(wrapNonce),
      'ciphertext': base64Encode(Uint8List(48)),
    });
    recoveryBytes = recoveryKey.copyBytes();
    wrappingKey = await atlasVaultDeriveHkdfSha256Internal(
      inputKeyMaterial: recoveryBytes,
      salt: kdf.salt,
      info: utf8.encode(AtlasVaultRecoveryWrapKdfParameters.info),
    );
    final ciphertext = await atlasVaultSealAes256GcmInternal(
      plaintext: vaultKey,
      key: wrappingKey,
      nonce: wrapNonce,
      aad: atlasVaultRecoveryWrapV2Aad(vaultId: vaultId, wrap: shell),
    );
    return AtlasVaultRecoveryKeyWrapV2.fromJson(<String, Object?>{
      ...shell.toJson(),
      'ciphertext': base64Encode(ciphertext),
    });
  } on AtlasVaultCryptoException {
    rethrow;
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    _wipe(wrappingKey);
    _wipe(recoveryBytes);
  }
}

Future<Uint8List> unwrapAtlasVaultRecoveryWrapV2({
  required AtlasVaultRecoveryKeyWrapV2 wrap,
  required AtlasVaultRecoveryKey recoveryKey,
  required String vaultId,
}) async {
  _requireRecoveryVaultId(vaultId);
  Uint8List? wrappingKey;
  Uint8List? recoveryBytes;
  Uint8List? plaintext;
  try {
    recoveryBytes = recoveryKey.copyBytes();
    wrappingKey = await atlasVaultDeriveHkdfSha256Internal(
      inputKeyMaterial: recoveryBytes,
      salt: wrap.kdf.salt,
      info: utf8.encode(AtlasVaultRecoveryWrapKdfParameters.info),
    );
    plaintext = await atlasVaultOpenAes256GcmInternal(
      ciphertextAndTag: wrap.ciphertext,
      key: wrappingKey,
      nonce: wrap.nonce,
      aad: atlasVaultRecoveryWrapV2Aad(vaultId: vaultId, wrap: wrap),
    );
    if (plaintext.length != 32) {
      throw const AtlasVaultCryptoException();
    }
    return Uint8List.fromList(plaintext);
  } on AtlasVaultCryptoException {
    rethrow;
  } catch (_) {
    throw const AtlasVaultCryptoException();
  } finally {
    _wipe(wrappingKey);
    _wipe(recoveryBytes);
    _wipe(plaintext);
  }
}

Future<Uint8List> unwrapAtlasVaultExportVaultKey({
  required AtlasVaultEncryptedExport export,
  required AtlasVaultRecoveryKey recoveryKey,
}) async {
  final wraps = export.vaultMetadata.keyWraps
      .whereType<AtlasVaultRecoveryKeyWrapV2>()
      .toList(growable: false);
  if (wraps.length != 1) {
    throw const AtlasVaultCryptoException();
  }
  return unwrapAtlasVaultRecoveryWrapV2(
    wrap: wraps.single,
    recoveryKey: recoveryKey,
    vaultId: export.vaultMetadata.vaultId,
  );
}

String _trimAsciiWhitespace(String value) {
  var start = 0;
  var end = value.length;
  while (start < end && _isAsciiWhitespace(value.codeUnitAt(start))) {
    start += 1;
  }
  while (end > start && _isAsciiWhitespace(value.codeUnitAt(end - 1))) {
    end -= 1;
  }
  return value.substring(start, end);
}

bool _isAsciiWhitespace(int value) {
  return value == 0x20 || value == 0x09 || value == 0x0a || value == 0x0d;
}

bool _constantTimeEqual(List<int> left, List<int> right) {
  var difference = left.length ^ right.length;
  final count = left.length > right.length ? left.length : right.length;
  for (var index = 0; index < count; index++) {
    final leftValue = index < left.length ? left[index] : 0;
    final rightValue = index < right.length ? right[index] : 0;
    difference |= leftValue ^ rightValue;
  }
  return difference == 0;
}

Uint8List _secureRandomBytes(int length) {
  final secret = SecretKeyData.random(
    length: length,
    debugLabel: 'AtlasVault random material',
  );
  try {
    return Uint8List.fromList(secret.bytes);
  } finally {
    secret.destroy();
  }
}

void _requireRecoveryVaultId(String value) {
  try {
    requireAtlasVaultVaultId(value);
  } on AtlasVaultFormatException {
    throw const AtlasVaultCryptoException();
  }
}

void _wipe(Uint8List? value) {
  value?.fillRange(0, value.length, 0);
}
