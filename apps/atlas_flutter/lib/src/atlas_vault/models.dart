import 'dart:convert';
import 'dart:typed_data';

import 'canonical_json.dart';
import 'strict_values.dart';

final class AtlasVaultEncryptedRecord {
  AtlasVaultEncryptedRecord._({
    required this.id,
    required this.schemaVersion,
    required this.revision,
    required this.parentRevision,
    required this.deleted,
    required this.keyId,
    required Uint8List nonce,
    required Uint8List ciphertext,
  }) : _nonce = Uint8List.fromList(nonce),
       _ciphertext = Uint8List.fromList(ciphertext);

  static const supportedSchemaVersion = 1;
  static const nonceByteCount = 12;
  static const gcmTagByteCount = 16;

  final String id;
  final int schemaVersion;
  final String revision;
  final String? parentRevision;
  final bool deleted;
  final String keyId;
  final Uint8List _nonce;
  final Uint8List _ciphertext;

  Uint8List get nonce => Uint8List.fromList(_nonce);
  Uint8List get ciphertext => Uint8List.fromList(_ciphertext);

  factory AtlasVaultEncryptedRecord.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'id',
        'schema_version',
        'revision',
        'parent_revision',
        'deleted',
        'key_id',
        'nonce',
        'ciphertext',
      },
      context: 'Encrypted record',
    );
    final schemaVersion = requireAtlasVaultInt(
      value['schema_version'],
      field: 'record.schema_version',
    );
    if (schemaVersion != supportedSchemaVersion) {
      throw const AtlasVaultFormatException(
        'Encrypted record schema version is unsupported.',
      );
    }
    final parentValue = value['parent_revision'];
    final String? parentRevision;
    if (parentValue == null) {
      parentRevision = null;
    } else {
      parentRevision = requireAtlasVaultString(
        parentValue,
        field: 'record.parent_revision',
        allowEmpty: false,
      );
    }
    return AtlasVaultEncryptedRecord._(
      id: requireAtlasVaultString(
        value['id'],
        field: 'record.id',
        allowEmpty: false,
      ),
      schemaVersion: schemaVersion,
      revision: requireAtlasVaultString(
        value['revision'],
        field: 'record.revision',
        allowEmpty: false,
      ),
      parentRevision: parentRevision,
      deleted: requireAtlasVaultBool(value['deleted'], field: 'record.deleted'),
      keyId: requireAtlasVaultString(
        value['key_id'],
        field: 'record.key_id',
        allowEmpty: false,
      ),
      nonce: requireAtlasVaultCanonicalBase64(
        value['nonce'],
        field: 'record.nonce',
        exactLength: nonceByteCount,
      ),
      ciphertext: requireAtlasVaultCanonicalBase64(
        value['ciphertext'],
        field: 'record.ciphertext',
        minimumLength: gcmTagByteCount,
      ),
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'id': id,
      'schema_version': schemaVersion,
      'revision': revision,
      'parent_revision': parentRevision,
      'deleted': deleted,
      'key_id': keyId,
      'nonce': base64Encode(_nonce),
      'ciphertext': base64Encode(_ciphertext),
    };
  }

  @override
  bool operator ==(Object other) {
    return other is AtlasVaultEncryptedRecord &&
        canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());
  }

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasVaultEncryptedRecord(<redacted>)';
}

final class AtlasVaultCryptoSuite {
  const AtlasVaultCryptoSuite._();

  static const recordAead = 'AES-256-GCM';
  static const kdf = 'Argon2id';
  static const subkeyKdf = 'HKDF-SHA256';
  static const keyWrapAead = 'AES-256-GCM';

  factory AtlasVaultCryptoSuite.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'record_aead',
        'kdf',
        'subkey_kdf',
        'key_wrap_aead',
      },
      context: 'Vault crypto suite',
    );
    if (value['record_aead'] != recordAead ||
        value['kdf'] != kdf ||
        value['subkey_kdf'] != subkeyKdf ||
        value['key_wrap_aead'] != keyWrapAead) {
      throw const AtlasVaultFormatException(
        'Vault cryptographic configuration is unsupported.',
      );
    }
    return const AtlasVaultCryptoSuite._();
  }

  Map<String, Object?> toJson() {
    return const <String, Object?>{
      'record_aead': recordAead,
      'kdf': kdf,
      'subkey_kdf': subkeyKdf,
      'key_wrap_aead': keyWrapAead,
    };
  }

  @override
  bool operator ==(Object other) => other is AtlasVaultCryptoSuite;

  @override
  int get hashCode => Object.hash(recordAead, kdf, subkeyKdf, keyWrapAead);

  @override
  String toString() => 'AtlasVaultCryptoSuite(<redacted>)';
}

final class AtlasVaultArgon2idParameters {
  AtlasVaultArgon2idParameters._({
    required Uint8List salt,
    required this.memoryKib,
    required this.iterations,
    required this.parallelism,
  }) : _salt = Uint8List.fromList(salt);

  static const algorithm = 'Argon2id';
  static const maximumMemoryKib = 65536;
  static const maximumIterations = 3;
  static const maximumParallelism = 4;

  final Uint8List _salt;
  final int memoryKib;
  final int iterations;
  final int parallelism;

  Uint8List get salt => Uint8List.fromList(_salt);

  factory AtlasVaultArgon2idParameters.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'algorithm',
        'salt',
        'memory_kib',
        'iterations',
        'parallelism',
      },
      context: 'Argon2id parameters',
    );
    if (value['algorithm'] != algorithm) {
      throw const AtlasVaultFormatException(
        'Passphrase key-wrap KDF is unsupported.',
      );
    }
    final memoryKib = requireAtlasVaultInt(
      value['memory_kib'],
      field: 'kdf.memory_kib',
    );
    final iterations = requireAtlasVaultInt(
      value['iterations'],
      field: 'kdf.iterations',
    );
    final parallelism = requireAtlasVaultInt(
      value['parallelism'],
      field: 'kdf.parallelism',
    );
    if (memoryKib <= 0 ||
        memoryKib > maximumMemoryKib ||
        iterations <= 0 ||
        iterations > maximumIterations ||
        parallelism <= 0 ||
        parallelism > maximumParallelism) {
      throw const AtlasVaultFormatException(
        'Argon2id parameters are outside the supported profile.',
      );
    }
    return AtlasVaultArgon2idParameters._(
      salt: requireAtlasVaultCanonicalBase64(
        value['salt'],
        field: 'kdf.salt',
        minimumLength: 16,
      ),
      memoryKib: memoryKib,
      iterations: iterations,
      parallelism: parallelism,
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'algorithm': algorithm,
      'salt': base64Encode(_salt),
      'memory_kib': memoryKib,
      'iterations': iterations,
      'parallelism': parallelism,
    };
  }

  @override
  String toString() => 'AtlasVaultArgon2idParameters(<redacted>)';
}

final class AtlasVaultRecoveryWrapKdfParameters {
  AtlasVaultRecoveryWrapKdfParameters._({required Uint8List salt})
    : _salt = Uint8List.fromList(salt);

  static const algorithm = 'HKDF-SHA256';
  static const info = 'atlas-vault-recovery-wrap-v2';
  static const saltByteCount = 32;

  final Uint8List _salt;

  Uint8List get salt => Uint8List.fromList(_salt);

  factory AtlasVaultRecoveryWrapKdfParameters.fromJson(
    Map<String, Object?> source,
  ) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{'algorithm', 'salt', 'info'},
      context: 'Recovery key-wrap KDF',
    );
    if (value['algorithm'] != algorithm || value['info'] != info) {
      throw const AtlasVaultFormatException(
        'Recovery key-wrap KDF is unsupported.',
      );
    }
    return AtlasVaultRecoveryWrapKdfParameters._(
      salt: requireAtlasVaultCanonicalBase64(
        value['salt'],
        field: 'kdf.salt',
        exactLength: saltByteCount,
      ),
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'algorithm': algorithm,
      'salt': base64Encode(_salt),
      'info': info,
    };
  }

  @override
  String toString() => 'AtlasVaultRecoveryWrapKdfParameters(<redacted>)';
}

sealed class AtlasVaultKeyWrap {
  const AtlasVaultKeyWrap();

  String get id;
  Map<String, Object?> toJson();

  static AtlasVaultKeyWrap fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    final type = requireAtlasVaultString(
      value['type'],
      field: 'key_wrap.type',
      allowEmpty: false,
    );
    if (type == AtlasVaultPassphraseKeyWrapV1.type &&
        !value.containsKey('wrap_version')) {
      return AtlasVaultPassphraseKeyWrapV1.fromJson(value);
    }
    if (type == AtlasVaultRecoveryKeyWrapV2.type) {
      final version = requireAtlasVaultInt(
        value['wrap_version'],
        field: 'key_wrap.wrap_version',
      );
      if (version == AtlasVaultRecoveryKeyWrapV2.wrapVersion) {
        return AtlasVaultRecoveryKeyWrapV2.fromJson(value);
      }
    }
    throw const AtlasVaultFormatException(
      'Key-wrap type or version is unsupported.',
    );
  }

  @override
  bool operator ==(Object other) {
    return other.runtimeType == runtimeType &&
        other is AtlasVaultKeyWrap &&
        canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());
  }

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;
}

final class AtlasVaultPassphraseKeyWrapV1 extends AtlasVaultKeyWrap {
  AtlasVaultPassphraseKeyWrapV1._({
    required this.id,
    required this.kdf,
    required Uint8List nonce,
    required Uint8List ciphertext,
  }) : _nonce = Uint8List.fromList(nonce),
       _ciphertext = Uint8List.fromList(ciphertext);

  static const type = 'passphrase';
  static const nonceByteCount = 12;
  static const ciphertextAndTagByteCount = 48;

  @override
  final String id;
  final AtlasVaultArgon2idParameters kdf;
  final Uint8List _nonce;
  final Uint8List _ciphertext;

  Uint8List get nonce => Uint8List.fromList(_nonce);
  Uint8List get ciphertext => Uint8List.fromList(_ciphertext);

  factory AtlasVaultPassphraseKeyWrapV1.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{'id', 'type', 'kdf', 'nonce', 'ciphertext'},
      context: 'Passphrase key-wrap',
    );
    if (value['type'] != type) {
      throw const AtlasVaultFormatException(
        'Passphrase key-wrap type is unsupported.',
      );
    }
    return AtlasVaultPassphraseKeyWrapV1._(
      id: requireAtlasVaultString(
        value['id'],
        field: 'key_wrap.id',
        allowEmpty: false,
      ),
      kdf: AtlasVaultArgon2idParameters.fromJson(
        requireAtlasVaultObject(value['kdf'], context: 'Argon2id parameters'),
      ),
      nonce: requireAtlasVaultCanonicalBase64(
        value['nonce'],
        field: 'key_wrap.nonce',
        exactLength: nonceByteCount,
      ),
      ciphertext: requireAtlasVaultCanonicalBase64(
        value['ciphertext'],
        field: 'key_wrap.ciphertext',
        exactLength: ciphertextAndTagByteCount,
      ),
    );
  }

  @override
  Map<String, Object?> toJson() {
    return <String, Object?>{
      'id': id,
      'type': type,
      'kdf': kdf.toJson(),
      'nonce': base64Encode(_nonce),
      'ciphertext': base64Encode(_ciphertext),
    };
  }

  @override
  String toString() => 'AtlasVaultPassphraseKeyWrapV1(<redacted>)';
}

final class AtlasVaultRecoveryKeyWrapV2 extends AtlasVaultKeyWrap {
  AtlasVaultRecoveryKeyWrapV2._({
    required this.kdf,
    required Uint8List nonce,
    required Uint8List ciphertext,
  }) : _nonce = Uint8List.fromList(nonce),
       _ciphertext = Uint8List.fromList(ciphertext);

  static const supportedId = 'primary-recovery-v2';
  static const type = 'recovery_key';
  static const wrapVersion = 2;
  static const nonceByteCount = 12;
  static const ciphertextAndTagByteCount = 48;

  @override
  String get id => supportedId;

  final AtlasVaultRecoveryWrapKdfParameters kdf;
  final Uint8List _nonce;
  final Uint8List _ciphertext;

  Uint8List get nonce => Uint8List.fromList(_nonce);
  Uint8List get ciphertext => Uint8List.fromList(_ciphertext);

  factory AtlasVaultRecoveryKeyWrapV2.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'id',
        'type',
        'wrap_version',
        'kdf',
        'nonce',
        'ciphertext',
      },
      context: 'Recovery key-wrap',
    );
    final version = requireAtlasVaultInt(
      value['wrap_version'],
      field: 'key_wrap.wrap_version',
    );
    if (value['id'] != supportedId ||
        value['type'] != type ||
        version != wrapVersion) {
      throw const AtlasVaultFormatException(
        'Recovery key-wrap is unsupported.',
      );
    }
    return AtlasVaultRecoveryKeyWrapV2._(
      kdf: AtlasVaultRecoveryWrapKdfParameters.fromJson(
        requireAtlasVaultObject(value['kdf'], context: 'Recovery key-wrap KDF'),
      ),
      nonce: requireAtlasVaultCanonicalBase64(
        value['nonce'],
        field: 'key_wrap.nonce',
        exactLength: nonceByteCount,
      ),
      ciphertext: requireAtlasVaultCanonicalBase64(
        value['ciphertext'],
        field: 'key_wrap.ciphertext',
        exactLength: ciphertextAndTagByteCount,
      ),
    );
  }

  @override
  Map<String, Object?> toJson() {
    return <String, Object?>{
      'id': supportedId,
      'type': type,
      'wrap_version': wrapVersion,
      'kdf': kdf.toJson(),
      'nonce': base64Encode(_nonce),
      'ciphertext': base64Encode(_ciphertext),
    };
  }

  @override
  String toString() => 'AtlasVaultRecoveryKeyWrapV2(<redacted>)';
}

final class AtlasVaultMetadata {
  AtlasVaultMetadata._({
    required this.vaultId,
    required this.crypto,
    required this.keyWraps,
  });

  static const format = 'atlas-vault';
  static const version = 1;

  final String vaultId;
  final AtlasVaultCryptoSuite crypto;
  final List<AtlasVaultKeyWrap> keyWraps;

  factory AtlasVaultMetadata.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'format',
        'version',
        'vault_id',
        'crypto',
        'key_wraps',
      },
      context: 'Vault metadata',
    );
    final parsedVersion = requireAtlasVaultInt(
      value['version'],
      field: 'vault.version',
    );
    if (value['format'] != format || parsedVersion != version) {
      throw const AtlasVaultFormatException(
        'Vault format or version is unsupported.',
      );
    }
    final wraps = <AtlasVaultKeyWrap>[
      for (final item in requireAtlasVaultList(
        value['key_wraps'],
        field: 'vault.key_wraps',
      ))
        AtlasVaultKeyWrap.fromJson(
          requireAtlasVaultObject(item, context: 'Key-wrap'),
        ),
    ];
    final ids = wraps.map((wrap) => wrap.id).toSet();
    if (ids.length != wraps.length) {
      throw const AtlasVaultFormatException(
        'Vault metadata contains duplicate key-wrap identifiers.',
      );
    }
    return AtlasVaultMetadata._(
      vaultId: requireAtlasVaultVaultId(value['vault_id']),
      crypto: AtlasVaultCryptoSuite.fromJson(
        requireAtlasVaultObject(value['crypto'], context: 'Vault crypto suite'),
      ),
      keyWraps: List<AtlasVaultKeyWrap>.unmodifiable(wraps),
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'format': format,
      'version': version,
      'vault_id': vaultId,
      'crypto': crypto.toJson(),
      'key_wraps': <Object?>[for (final wrap in keyWraps) wrap.toJson()],
    };
  }

  @override
  bool operator ==(Object other) {
    return other is AtlasVaultMetadata &&
        canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());
  }

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasVaultMetadata(<redacted>)';
}

final class AtlasVaultLocalStore {
  AtlasVaultLocalStore._({
    required this.storeId,
    required this.createdAt,
    required this.updatedAt,
    required this.vaultMetadata,
    required this.records,
  });

  static const format = 'atlasvault-local-store';
  static const version = 1;

  final String storeId;
  final String createdAt;
  final String updatedAt;
  final AtlasVaultMetadata vaultMetadata;
  final List<AtlasVaultEncryptedRecord> records;

  factory AtlasVaultLocalStore.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'format',
        'version',
        'store_id',
        'created_at',
        'updated_at',
        'vault_metadata',
        'records',
      },
      context: 'Local-store envelope',
    );
    final parsedVersion = requireAtlasVaultInt(
      value['version'],
      field: 'local_store.version',
    );
    if (value['format'] != format || parsedVersion != version) {
      throw const AtlasVaultFormatException(
        'Local-store format or version is unsupported.',
      );
    }
    return AtlasVaultLocalStore._(
      storeId: requireAtlasVaultString(
        value['store_id'],
        field: 'local_store.store_id',
        allowEmpty: false,
      ),
      createdAt: requireAtlasVaultUtcSeconds(
        value['created_at'],
        field: 'local_store.created_at',
      ),
      updatedAt: requireAtlasVaultUtcSeconds(
        value['updated_at'],
        field: 'local_store.updated_at',
      ),
      vaultMetadata: AtlasVaultMetadata.fromJson(
        requireAtlasVaultObject(
          value['vault_metadata'],
          context: 'Vault metadata',
        ),
      ),
      records: List<AtlasVaultEncryptedRecord>.unmodifiable(
        <AtlasVaultEncryptedRecord>[
          for (final record in requireAtlasVaultList(
            value['records'],
            field: 'local_store.records',
          ))
            AtlasVaultEncryptedRecord.fromJson(
              requireAtlasVaultObject(record, context: 'Encrypted record'),
            ),
        ],
      ),
    );
  }

  factory AtlasVaultLocalStore.decodeJson(String source) {
    return AtlasVaultLocalStore.fromJson(
      decodeAtlasVaultJsonObject(source, context: 'Local-store envelope'),
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'format': format,
      'version': version,
      'store_id': storeId,
      'created_at': createdAt,
      'updated_at': updatedAt,
      'vault_metadata': vaultMetadata.toJson(),
      'records': <Object?>[for (final record in records) record.toJson()],
    };
  }

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  @override
  bool operator ==(Object other) {
    return other is AtlasVaultLocalStore &&
        canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());
  }

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasVaultLocalStore(<redacted>)';
}
