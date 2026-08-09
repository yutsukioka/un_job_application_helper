import 'dart:convert';
import 'dart:io';

import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';
import 'support/atlas_vault_windows_fakes.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Windows interoperability construction performs no platform call', () {
    final platform = FakeAtlasVaultWindowsPlatform()..install();
    addTearDown(platform.uninstall);

    final keyStore = AtlasWindowsVaultSecureKeyStore(
      channel: platform.recorder.channel,
    );
    final localStore = AtlasWindowsVaultLocalStoreIO(
      channel: platform.recorder.channel,
    );
    final selected = AtlasWindowsSelectedVaultStore(
      channel: platform.recorder.channel,
    );
    final migration = AtlasWindowsProtectedMigrationJournalStore(
      channel: platform.recorder.channel,
    );
    final transport = AtlasWindowsEncryptedDocumentTransport(
      channel: platform.recorder.channel,
    );
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
    );

    AtlasVaultInteroperabilityCoordinator(
      runtime: runtime,
      selectedVaultStore: selected,
      migrationJournalStore: migration,
      recoveryImportPending: () async => false,
      documentTransport: transport,
    );

    expect(platform.calls, isEmpty);
  });

  test('Windows production assembly attaches explicit recovery export', () {
    final source = File(
      'lib/features/app_shell/atlas_app.dart',
    ).readAsStringSync();
    final windowsStart = source.indexOf('if (Platform.isWindows) {');
    final fallbackStart = source.indexOf(
      'if (!Platform.isAndroid)',
      windowsStart < 0 ? 0 : windowsStart,
    );

    expect(windowsStart, isNonNegative);
    expect(fallbackStart, greaterThan(windowsStart));
    final assembly = source.substring(windowsStart, fallbackStart);
    expect(assembly, contains('_attachWindowsEncryptedBackup('));
    expect(assembly, isNot(contains('AtlasVaultInteroperability')));
    expect(assembly, isNot(contains('prepareRecoveryImport(')));
    expect(assembly, isNot(contains('beginRecoverySetup(')));
    expect(assembly, isNot(contains('activateExistingAtlasVault(')));

    final helperStart = source.indexOf(
      'AtlasVaultInteroperabilityPresentationOwner '
      '_attachWindowsEncryptedBackup',
    );
    final helperEnd = source.indexOf(
      '_AtlasDefaultControllerAssembly _buildDefaultControllerAssembly()',
      helperStart < 0 ? 0 : helperStart,
    );
    expect(helperStart, isNonNegative);
    expect(helperEnd, greaterThan(helperStart));
    final helper = source.substring(helperStart, helperEnd);
    expect(helper, contains('AtlasWindowsEncryptedDocumentTransport()'));
    expect(helper, contains('AtlasVaultInteroperabilityCoordinator('));
    expect(helper, contains('AtlasVaultInteroperabilityPresentationOwner('));
    expect(helper, contains('attachInteroperabilityContext('));
  });

  test(
    'Windows interoperability vector is strict and cryptographically exact',
    () async {
      final vector = loadAtlasVaultVector(
        'atlasvault_windows_interop_vectors_v1.json',
      );

      expect(vector.keys.toSet(), <String>{
        '_warning',
        'format',
        'version',
        'apple_to_windows',
        'android_to_windows',
        'windows_to_apple_android',
      });
      expect(vector['_warning'], 'FAKE TEST DATA ONLY');
      expect(vector['format'], 'atlasvault-windows-interop-v1');
      expect(vector['version'], 1);

      const caseKeys = <String>{
        'case_id',
        'test_only_recovery_key_text',
        'test_only_vault_key_b64',
        'vault_id',
        'export_id',
        'export_timestamp',
        'local_source_store_id',
        'wrap_salt_b64',
        'wrap_nonce_b64',
        'record_nonces_b64',
        'canonical_encrypted_export_b64',
        'canonical_encrypted_export_sha256',
        'expected_encrypted_record_count',
        'expected_active_saved_search_count',
        'expected_active_tracker_saved_job_count',
        'expected_preserved_other_private_record_count',
        'expected_tombstone_count',
        'expected_payload_values',
      };
      for (final name in <String>[
        'apple_to_windows',
        'android_to_windows',
        'windows_to_apple_android',
      ]) {
        final value = atlasVaultObject(vector[name]);
        final expectedKeys = <String>{...caseKeys};
        if (name == 'windows_to_apple_android') {
          expectedKeys.addAll(<String>{
            'test_only_passphrase',
            'v1_wrap_salt_b64',
            'v1_wrap_nonce_b64',
          });
        }
        expect(value.keys.toSet(), expectedKeys, reason: name);

        final bytes = base64Decode(
          value['canonical_encrypted_export_b64']! as String,
        );
        final decoded = vault.AtlasVaultEncryptedExport.decodeJson(
          utf8.decode(bytes),
        );
        final vaultKey = base64Decode(
          value['test_only_vault_key_b64']! as String,
        );
        final recoveryKey = vault.AtlasVaultRecoveryKey.parse(
          value['test_only_recovery_key_text']! as String,
        );
        addTearDown(recoveryKey.destroy);
        final unwrapped = await vault.unwrapAtlasVaultExportVaultKey(
          export: decoded,
          recoveryKey: recoveryKey,
        );

        expect(decoded.canonicalBytes(), bytes, reason: name);
        expect(
          await vault.atlasVaultSha256Hex(bytes),
          value['canonical_encrypted_export_sha256'],
          reason: name,
        );
        expect(decoded.exportId, value['export_id'], reason: name);
        expect(decoded.createdAt, value['export_timestamp'], reason: name);
        expect(decoded.vaultMetadata.vaultId, value['vault_id'], reason: name);
        expect(
          decoded.records,
          hasLength(value['expected_encrypted_record_count']! as int),
          reason: name,
        );
        expect(unwrapped, vaultKey, reason: name);
        expect(
          decoded.records.map((record) => base64Encode(record.nonce)).toList(),
          atlasVaultList(value['record_nonces_b64']).cast<String>(),
          reason: name,
        );
        expect(
          decoded.records.where((record) => record.deleted),
          hasLength(value['expected_tombstone_count']! as int),
          reason: name,
        );

        var savedSearchCount = 0;
        var savedJobCount = 0;
        var otherPrivateCount = 0;
        for (final record in decoded.records.where(
          (record) => !record.deleted,
        )) {
          final plaintext = await vault.openAtlasVaultRecord(
            vaultKey: vaultKey,
            vaultId: decoded.vaultMetadata.vaultId,
            record: record,
          );
          final payload = vault.AtlasVaultPayloadEnvelope.decodeJson(
            utf8.decode(plaintext),
          );
          switch (payload.type) {
            case vault.AtlasVaultPayloadType.savedSearch:
              savedSearchCount += 1;
            case vault.AtlasVaultPayloadType.savedJob:
              savedJobCount += 1;
            default:
              otherPrivateCount += 1;
          }
        }
        expect(
          savedSearchCount,
          value['expected_active_saved_search_count'],
          reason: name,
        );
        expect(
          savedJobCount,
          value['expected_active_tracker_saved_job_count'],
          reason: name,
        );
        expect(
          otherPrivateCount,
          value['expected_preserved_other_private_record_count'],
          reason: name,
        );

        final canonicalText = utf8.decode(bytes);
        expect(
          canonicalText,
          isNot(contains(value['local_source_store_id']! as String)),
          reason: name,
        );
        expect(
          canonicalText,
          isNot(contains(value['test_only_recovery_key_text']! as String)),
          reason: name,
        );
        expect(
          canonicalText,
          isNot(contains(base64Encode(vaultKey))),
          reason: name,
        );
        for (final entry in atlasVaultObject(
          value['expected_payload_values'],
        ).entries) {
          final privateValue = entry.value;
          if (entry.key != 'tombstone_record_id' && privateValue is String) {
            expect(canonicalText, isNot(contains(privateValue)), reason: name);
          }
        }

        final recoveryWraps = decoded.vaultMetadata.keyWraps
            .whereType<vault.AtlasVaultRecoveryKeyWrapV2>()
            .toList(growable: false);
        expect(recoveryWraps, hasLength(1), reason: name);
        if (name == 'windows_to_apple_android') {
          final passphraseWraps = decoded.vaultMetadata.keyWraps
              .whereType<vault.AtlasVaultPassphraseKeyWrapV1>()
              .toList(growable: false);
          expect(passphraseWraps, hasLength(1));
          expect(
            await vault.unwrapAtlasVaultPassphraseWrapV1(
              wrap: passphraseWraps.single,
              passphrase: value['test_only_passphrase']! as String,
            ),
            vaultKey,
          );
        }
        expect(value.toString(), isNot(contains('REAL USER')));
      }
    },
  );
}
