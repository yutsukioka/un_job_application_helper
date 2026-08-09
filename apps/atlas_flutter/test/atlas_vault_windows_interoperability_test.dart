import 'dart:convert';
import 'dart:io';

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
    expect(assembly, contains('AtlasWindowsEncryptedDocumentTransport()'));
    expect(assembly, contains('AtlasVaultInteroperabilityCoordinator('));
    expect(assembly, contains('AtlasVaultInteroperabilityPresentationOwner('));
    expect(assembly, contains('attachInteroperabilityContext('));
    expect(assembly, isNot(contains('prepareRecoveryImport(')));
    expect(assembly, isNot(contains('beginRecoverySetup(')));
    expect(assembly, isNot(contains('activateExistingAtlasVault(')));
  });

  test('Windows interoperability vector has the strict fake outer schema', () {
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
    for (final name in <String>[
      'apple_to_windows',
      'android_to_windows',
      'windows_to_apple_android',
    ]) {
      final value = atlasVaultObject(vector[name]);
      final bytes = base64Decode(
        value['canonical_encrypted_export_b64']! as String,
      );
      expect(bytes, isNotEmpty);
      expect(value['canonical_encrypted_export_sha256'], hasLength(64));
      expect(value.toString(), isNot(contains('REAL USER')));
    }
  });
}
