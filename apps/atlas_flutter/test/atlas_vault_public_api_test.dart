import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  test('public barrel exposes reviewed format models', () {
    final payloadVectors = loadAtlasVaultVector(
      'atlasvault_payload_vectors_v1.json',
    );
    final payloads = atlasVaultObject(payloadVectors['payloads']);
    final payload = AtlasVaultPayloadEnvelope.fromJson(
      atlasVaultObject(payloads['saved_search']),
    );
    final recoveryVectors = loadAtlasVaultVector(
      'atlasvault_recovery_export_vectors_v2.json',
    );
    final recoveryVector = atlasVaultObject(
      atlasVaultList(recoveryVectors['vectors']).single,
    );
    final export = AtlasVaultEncryptedExport.fromJson(
      atlasVaultObject(recoveryVector['export']),
    );

    expect(payload, isA<AtlasVaultPayloadEnvelope>());
    expect(payload.payload, isA<AtlasSavedSearchPayload>());
    expect(export.vaultMetadata, isA<AtlasVaultMetadata>());
    expect(
      export.vaultMetadata.keyWraps.single,
      isA<AtlasVaultRecoveryKeyWrapV2>(),
    );
  });

  test('public model descriptions are fixed and redacted', () {
    final vectors = loadAtlasVaultVector(
      'atlasvault_recovery_export_vectors_v2.json',
    );
    final vector = atlasVaultObject(atlasVaultList(vectors['vectors']).single);
    final export = AtlasVaultEncryptedExport.fromJson(
      atlasVaultObject(vector['export']),
    );

    expect(export.toString(), 'AtlasVaultEncryptedExport(<redacted>)');
    expect(export.vaultMetadata.toString(), 'AtlasVaultMetadata(<redacted>)');
    expect(
      export.vaultMetadata.keyWraps.single.toString(),
      'AtlasVaultRecoveryKeyWrapV2(<redacted>)',
    );
    expect(export.toString(), isNot(contains(export.vaultMetadata.vaultId)));
  });
}
