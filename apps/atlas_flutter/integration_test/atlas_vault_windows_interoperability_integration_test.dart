import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/support/atlas_vault_vector_loader.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Windows exports the canonical three-platform artifact', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    final vector = _WindowsExportVector.load();
    final keyStore = AtlasWindowsVaultSecureKeyStore();
    final localStore = AtlasWindowsVaultLocalStoreIO();
    final selected = AtlasWindowsSelectedVaultStore();
    final migrationJournal = AtlasWindowsProtectedMigrationJournalStore();
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
    );
    final transport = _ArtifactDocumentTransport();

    await _cleanup(
      vector.vaultId,
      runtime: runtime,
      keyStore: keyStore,
      localStore: localStore,
      selected: selected,
    );
    addTearDown(() async {
      await _cleanup(
        vector.vaultId,
        runtime: runtime,
        keyStore: keyStore,
        localStore: localStore,
        selected: selected,
      );
      vector.vaultKey.fillRange(0, vector.vaultKey.length, 0);
    });

    expect(await migrationJournal.read(), isNull);
    await keyStore.createVaultKey(vector.vaultId, vector.vaultKey);
    await localStore.create(vector.vaultId, vector.localStore);
    await selected.create(vector.vaultId);
    expect(
      await runtime.activateExisting(vector.vaultId),
      AtlasVaultActivationResult.activated,
    );

    final coordinator = AtlasVaultInteroperabilityCoordinator(
      runtime: runtime,
      selectedVaultStore: selected,
      migrationJournalStore: migrationJournal,
      recoveryImportPending: () async => false,
      documentTransport: transport,
      now: () => DateTime.parse(vector.export.createdAt),
      uuidProvider: () => vector.export.exportId,
    );
    final prepared = await coordinator.prepareExistingRecoveryExport(
      vector.recoveryText,
    );
    expect(
      prepared.disposition,
      AtlasVaultRecoveryExportDisposition.exportReady,
    );
    final saved = await coordinator.savePreparedExport();
    expect(saved.disposition, AtlasVaultRecoveryExportDisposition.saved);
    expect(transport.savedBytes, orderedEquals(vector.exportBytes));
    expect(
      await atlasVaultSha256Hex(transport.savedBytes!),
      vector.exportSha256,
    );
    for (final sentinel in vector.privateSentinels) {
      expect(utf8.decode(transport.savedBytes!), isNot(contains(sentinel)));
    }
    tester.printToConsole(
      'Windows AtlasVault canonical recovery export passed.',
    );
  });
}

final class _WindowsExportVector {
  _WindowsExportVector({
    required this.vaultId,
    required this.recoveryText,
    required this.vaultKey,
    required this.exportBytes,
    required this.exportSha256,
    required this.export,
    required this.localStore,
    required this.privateSentinels,
  });

  final String vaultId;
  final String recoveryText;
  final Uint8List vaultKey;
  final Uint8List exportBytes;
  final String exportSha256;
  final AtlasVaultEncryptedExport export;
  final AtlasVaultLocalStore localStore;
  final List<String> privateSentinels;

  factory _WindowsExportVector.load() {
    final root = loadAtlasVaultVector(
      'atlasvault_windows_interop_vectors_v1.json',
    );
    final value = atlasVaultObject(root['windows_to_apple_android']);
    final exportBytes = Uint8List.fromList(
      base64Decode(value['canonical_encrypted_export_b64']! as String),
    );
    final export = AtlasVaultEncryptedExport.decodeJson(
      utf8.decode(exportBytes),
    );
    final payloadValues = atlasVaultObject(value['expected_payload_values']);
    return _WindowsExportVector(
      vaultId: value['vault_id']! as String,
      recoveryText: value['test_only_recovery_key_text']! as String,
      vaultKey: Uint8List.fromList(
        base64Decode(value['test_only_vault_key_b64']! as String),
      ),
      exportBytes: exportBytes,
      exportSha256: value['canonical_encrypted_export_sha256']! as String,
      export: export,
      localStore: AtlasVaultLocalStore.fromJson(<String, Object?>{
        'format': 'atlasvault-local-store',
        'version': 1,
        'store_id': value['local_source_store_id']! as String,
        'created_at': export.createdAt,
        'updated_at': export.createdAt,
        'vault_metadata': export.vaultMetadata.toJson(),
        'records': <Object?>[
          for (final record in export.records) record.toJson(),
        ],
      }),
      privateSentinels: <String>[
        for (final entry in payloadValues.entries)
          if (!entry.key.endsWith('_record_id') && entry.value is String)
            entry.value! as String,
      ],
    );
  }
}

final class _ArtifactDocumentTransport
    implements AtlasVaultEncryptedDocumentTransport {
  Uint8List? savedBytes;

  @override
  Future<Uint8List?> pickEncryptedExport() async => null;

  @override
  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes) async {
    savedBytes = Uint8List.fromList(canonicalExportBytes);
    final artifactDirectory =
        Platform.environment['ATLAS_INTEROP_ARTIFACT_DIR'];
    if (artifactDirectory != null && artifactDirectory.isNotEmpty) {
      final directory = Directory(artifactDirectory);
      await directory.create(recursive: true);
      final artifact = File(
        '${directory.path}${Platform.pathSeparator}'
        'windows-to-apple-android.atlasvault',
      );
      await artifact.writeAsBytes(savedBytes!, flush: true);
      final digest = await atlasVaultSha256Hex(savedBytes!);
      await File(
        '${artifact.path}.sha256',
      ).writeAsString('$digest\n', flush: true);
    }
    return true;
  }
}

Future<void> _cleanup(
  String vaultId, {
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasWindowsVaultSecureKeyStore keyStore,
  required AtlasWindowsVaultLocalStoreIO localStore,
  required AtlasWindowsSelectedVaultStore selected,
}) async {
  if (runtime.isActive) {
    await runtime.deactivate();
  }
  final selectedVault = await selected.read();
  if (selectedVault == vaultId) {
    await selected.clear(vaultId);
  } else if (selectedVault != null) {
    throw StateError('Unexpected selected vault in test storage.');
  }
  if (await localStore.read(vaultId) != null) {
    await localStore.delete(vaultId);
  }
  if (await keyStore.containsVaultKey(vaultId)) {
    await keyStore.deleteVaultKey(vaultId);
  }
}
