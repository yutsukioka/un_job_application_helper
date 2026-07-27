import 'dart:typed_data';

import 'canonical_json.dart';
import 'models.dart';
import 'strict_values.dart';

final class AtlasVaultEncryptedExport {
  AtlasVaultEncryptedExport._({
    required this.exportId,
    required this.createdAt,
    required this.vaultMetadata,
    required this.records,
  });

  static const format = 'atlasvault-export';
  static const version = 1;

  final String exportId;
  final String createdAt;
  final AtlasVaultMetadata vaultMetadata;
  final List<AtlasVaultEncryptedRecord> records;

  factory AtlasVaultEncryptedExport.fromJson(Map<String, Object?> source) {
    final value = Map<String, Object?>.from(source);
    requireAtlasVaultExactKeys(
      value,
      requiredKeys: const <String>{
        'format',
        'version',
        'export_id',
        'created_at',
        'vault_metadata',
        'records',
      },
      context: 'Encrypted-export envelope',
    );
    final parsedVersion = requireAtlasVaultInt(
      value['version'],
      field: 'export.version',
    );
    if (value['format'] != format || parsedVersion != version) {
      throw const AtlasVaultFormatException(
        'Encrypted-export format or version is unsupported.',
      );
    }
    return AtlasVaultEncryptedExport._(
      exportId: requireAtlasVaultCanonicalUuid(
        value['export_id'],
        field: 'export.export_id',
      ),
      createdAt: requireAtlasVaultUtcSeconds(
        value['created_at'],
        field: 'export.created_at',
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
            field: 'export.records',
          ))
            AtlasVaultEncryptedRecord.fromJson(
              requireAtlasVaultObject(record, context: 'Encrypted record'),
            ),
        ],
      ),
    );
  }

  factory AtlasVaultEncryptedExport.decodeJson(String source) {
    return AtlasVaultEncryptedExport.fromJson(
      decodeAtlasVaultJsonObject(source, context: 'Encrypted-export envelope'),
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'format': format,
      'version': version,
      'export_id': exportId,
      'created_at': createdAt,
      'vault_metadata': vaultMetadata.toJson(),
      'records': <Object?>[for (final record in records) record.toJson()],
    };
  }

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  @override
  bool operator ==(Object other) {
    return other is AtlasVaultEncryptedExport &&
        canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());
  }

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasVaultEncryptedExport(<redacted>)';
}
