import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> vector;
  late AtlasVaultRecoveryKey recoveryKey;
  late Uint8List vaultKey;
  late AtlasVaultRecoveryKeyWrapV2 wrap;
  late String vaultId;

  setUp(() {
    final root = loadAtlasVaultVector(
      'atlasvault_recovery_export_vectors_v2.json',
    );
    vector = atlasVaultObject(atlasVaultList(root['vectors']).single);
    recoveryKey = AtlasVaultRecoveryKey.fromBytes(
      Uint8List.fromList(
        base64Decode(vector['test_only_recovery_key_b64']! as String),
      ),
    );
    vaultKey = Uint8List.fromList(
      base64Decode(vector['test_only_vault_key_b64']! as String),
    );
    wrap = AtlasVaultRecoveryKeyWrapV2.fromJson(
      atlasVaultObject(vector['recovery_wrap']),
    );
    vaultId = vector['vault_id']! as String;
  });

  test('recovery-key text codec matches and normalizes only allowed text', () {
    final canonical = vector['canonical_recovery_text']! as String;
    final normalized = '  ${canonical.toLowerCase().replaceAll('-', ' ')}  ';

    expect(recoveryKey.canonicalText, canonical);
    expect(
      AtlasVaultRecoveryKey.parse(canonical).copyBytes(),
      recoveryKey.copyBytes(),
    );
    expect(
      AtlasVaultRecoveryKey.parse(normalized).copyBytes(),
      recoveryKey.copyBytes(),
    );
    expect(recoveryKey.toString(), 'AtlasVaultRecoveryKey(<redacted>)');
  });

  test(
    'recovery-key parser rejects checksum, alphabet, prefix, and aliases',
    () {
      final canonical = vector['canonical_recovery_text']! as String;
      final wrongChecksum = '${canonical.substring(0, canonical.length - 1)}A';
      final invalidAlphabet = canonical.replaceFirst('A', '0', 6);
      final ambiguousOne = canonical.replaceFirst('A', '1', 6);
      final ambiguousEight = canonical.replaceFirst('A', '8', 6);
      final unsupportedPrefix = canonical.replaceFirst('AVRK1', 'AVRK2');
      final padded = '$canonical=';
      final unusedBitAlias = '${canonical.substring(0, canonical.length - 1)}R';

      for (final invalid in <String>[
        wrongChecksum,
        invalidAlphabet,
        ambiguousOne,
        ambiguousEight,
        unsupportedPrefix,
        padded,
        unusedBitAlias,
        'AVRK1-AAAA',
      ]) {
        expect(
          () => AtlasVaultRecoveryKey.parse(invalid),
          throwsA(isA<AtlasVaultCryptoException>()),
        );
      }
    },
  );

  test('destroy invalidates retained recovery-key material', () async {
    final disposable = AtlasVaultRecoveryKey.fromBytes(recoveryKey.copyBytes());

    disposable.destroy();
    disposable.destroy();

    expect(
      () => disposable.copyBytes(),
      throwsA(isA<AtlasVaultCryptoException>()),
    );
    expect(
      () => disposable.canonicalText,
      throwsA(isA<AtlasVaultCryptoException>()),
    );
    await expectLater(
      wrapAtlasVaultKeyWithRecoveryV2(
        vaultKey: vaultKey,
        recoveryKey: disposable,
        vaultId: vaultId,
      ),
      throwsA(isA<AtlasVaultCryptoException>()),
    );
    expect(disposable.toString(), 'AtlasVaultRecoveryKey(<redacted>)');
  });

  test('recovery v2 AAD, wrap, and unwrap match exactly', () async {
    final aad = atlasVaultRecoveryWrapV2Aad(vaultId: vaultId, wrap: wrap);
    final sealed = await wrapAtlasVaultKeyWithRecoveryV2(
      vaultKey: vaultKey,
      recoveryKey: recoveryKey,
      vaultId: vaultId,
      salt: base64Decode(vector['salt_b64']! as String),
      nonce: base64Decode(vector['nonce_b64']! as String),
    );
    final opened = await unwrapAtlasVaultRecoveryWrapV2(
      wrap: wrap,
      recoveryKey: recoveryKey,
      vaultId: vaultId,
    );

    expect(aad, base64Decode(vector['key_wrap_aad_b64']! as String));
    expect(
      jsonDecode(utf8.decode(aad)),
      atlasVaultObject(vector['key_wrap_aad_json']),
    );
    expect(sealed.toJson(), atlasVaultObject(vector['recovery_wrap']));
    expect(opened, vaultKey);
  });

  test('wrong recovery material and every authenticated field fail', () async {
    final wrongRecovery = AtlasVaultRecoveryKey.parse(
      vector['wrong_canonical_recovery_text']! as String,
    );
    final changedSalt = _tamperNestedBase64(
      wrap.toJson(),
      parent: 'kdf',
      field: 'salt',
    );
    final changedNonce = _tamperBase64(wrap.toJson(), 'nonce');
    final changedCiphertext = _tamperBase64(wrap.toJson(), 'ciphertext');
    final changedTag = _tamperBase64(
      wrap.toJson(),
      'ciphertext',
      fromEnd: true,
    );

    final attempts = <Future<Uint8List>>[
      unwrapAtlasVaultRecoveryWrapV2(
        wrap: wrap,
        recoveryKey: wrongRecovery,
        vaultId: vaultId,
      ),
      unwrapAtlasVaultRecoveryWrapV2(
        wrap: wrap,
        recoveryKey: recoveryKey,
        vaultId: 'different-vault',
      ),
      unwrapAtlasVaultRecoveryWrapV2(
        wrap: AtlasVaultRecoveryKeyWrapV2.fromJson(changedSalt),
        recoveryKey: recoveryKey,
        vaultId: vaultId,
      ),
      unwrapAtlasVaultRecoveryWrapV2(
        wrap: AtlasVaultRecoveryKeyWrapV2.fromJson(changedNonce),
        recoveryKey: recoveryKey,
        vaultId: vaultId,
      ),
      unwrapAtlasVaultRecoveryWrapV2(
        wrap: AtlasVaultRecoveryKeyWrapV2.fromJson(changedCiphertext),
        recoveryKey: recoveryKey,
        vaultId: vaultId,
      ),
      unwrapAtlasVaultRecoveryWrapV2(
        wrap: AtlasVaultRecoveryKeyWrapV2.fromJson(changedTag),
        recoveryKey: recoveryKey,
        vaultId: vaultId,
      ),
    ];

    for (final attempt in attempts) {
      await expectLater(attempt, throwsA(isA<AtlasVaultCryptoException>()));
    }
  });

  test('recovery v2 rejects changed fixed identity and KDF info', () {
    final changedId = _clone(wrap.toJson())..['id'] = 'other-recovery-wrap';
    final changedInfo = _clone(wrap.toJson());
    atlasVaultObject(changedInfo['kdf'])['info'] = 'other-info';

    expect(
      () => AtlasVaultRecoveryKeyWrapV2.fromJson(changedId),
      throwsA(isA<AtlasVaultFormatException>()),
    );
    expect(
      () => AtlasVaultRecoveryKeyWrapV2.fromJson(changedInfo),
      throwsA(isA<AtlasVaultFormatException>()),
    );
  });

  test('canonical encrypted export bytes and SHA-256 match exactly', () async {
    final export = AtlasVaultEncryptedExport.fromJson(
      atlasVaultObject(vector['export']),
    );
    final canonicalBytes = export.canonicalBytes();
    final unwrapped = await unwrapAtlasVaultExportVaultKey(
      export: export,
      recoveryKey: recoveryKey,
    );

    expect(
      canonicalBytes,
      base64Decode(vector['canonical_export_json_b64']! as String),
    );
    expect(
      await atlasVaultSha256Hex(canonicalBytes),
      vector['canonical_export_sha256'],
    );
    expect(unwrapped, vaultKey);
    expect(
      AtlasVaultEncryptedExport.decodeJson(utf8.decode(canonicalBytes)),
      export,
    );
  });

  test(
    'wrong key and tampered export never verify or expose secrets',
    () async {
      final exportJson = _clone(atlasVaultObject(vector['export']));
      final metadata = atlasVaultObject(exportJson['vault_metadata']);
      final wrapJson = atlasVaultObject(
        atlasVaultList(metadata['key_wraps']).single,
      );
      final tamperedCiphertext = base64Decode(wrapJson['ciphertext']! as String)
        ..[0] ^= 0x01;
      wrapJson['ciphertext'] = base64Encode(tamperedCiphertext);
      final tampered = AtlasVaultEncryptedExport.fromJson(exportJson);
      final wrongRecovery = AtlasVaultRecoveryKey.parse(
        vector['wrong_canonical_recovery_text']! as String,
      );

      await expectLater(
        unwrapAtlasVaultExportVaultKey(
          export: AtlasVaultEncryptedExport.fromJson(
            atlasVaultObject(vector['export']),
          ),
          recoveryKey: wrongRecovery,
        ),
        throwsA(isA<AtlasVaultCryptoException>()),
      );
      await expectLater(
        unwrapAtlasVaultExportVaultKey(
          export: tampered,
          recoveryKey: recoveryKey,
        ),
        throwsA(isA<AtlasVaultCryptoException>()),
      );

      final serialized = utf8.decode(
        AtlasVaultEncryptedExport.fromJson(
          atlasVaultObject(vector['export']),
        ).canonicalBytes(),
      );
      expect(
        serialized,
        isNot(contains(vector['test_only_recovery_key_b64']! as String)),
      );
      expect(
        serialized,
        isNot(contains(vector['test_only_vault_key_b64']! as String)),
      );
    },
  );
}

Map<String, Object?> _tamperBase64(
  Map<String, Object?> source,
  String field, {
  bool fromEnd = false,
}) {
  final result = _clone(source);
  final bytes = base64Decode(result[field]! as String);
  bytes[fromEnd ? bytes.length - 1 : 0] ^= 0x01;
  result[field] = base64Encode(bytes);
  return result;
}

Map<String, Object?> _tamperNestedBase64(
  Map<String, Object?> source, {
  required String parent,
  required String field,
}) {
  final result = _clone(source);
  final nested = atlasVaultObject(result[parent]);
  final bytes = base64Decode(nested[field]! as String)..[0] ^= 0x01;
  nested[field] = base64Encode(bytes);
  return result;
}

Map<String, Object?> _clone(Map<String, Object?> value) {
  return atlasVaultObject(jsonDecode(jsonEncode(value)));
}
