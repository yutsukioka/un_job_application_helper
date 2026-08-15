import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('pairing view exposes only explicit trusted-device actions', () {
    final sourceFile = File('lib/src/atlas_vault/pairing_view.dart');

    expect(sourceFile.existsSync(), isTrue);
    final source = sourceFile.readAsStringSync();
    for (final action in <String>[
      'Create Device Identity',
      'Create Pairing Offer',
      'Save Pairing Offer',
      'Import Pairing Offer',
      'Save Pairing Acceptance',
      'Import Pairing Acceptance',
      'Codes Match',
      'Save Key Delivery',
      'Import Key Delivery',
      'Save Pairing Acknowledgement',
      'Import Pairing Acknowledgement',
      'Resume Pairing',
      'Discard Pairing',
    ]) {
      expect(source, contains(action), reason: action);
    }
    for (final forbidden in <String>[
      'privateKey',
      'vaultKey',
      'sessionKey',
      'ephemeralPrivateKey',
      'backendCredential',
      '.onAppear',
      'initState() async',
    ]) {
      expect(source, isNot(contains(forbidden)), reason: forbidden);
    }
  });

  test('pairing owner is one retained explicit operation authority', () {
    final source = File(
      'lib/src/atlas_vault/pairing_view.dart',
    ).readAsStringSync();

    for (final required in <String>[
      'AtlasVaultTrustedPairingPresentationOwner',
      'AtlasVaultTrustedPairingContext',
      'AtlasVaultTrustedPairingCoordinating',
      'Future<void>? _operation',
      'createDeviceIdentity',
      'createPairingOffer',
      'confirmCodesMatch',
      'resumePairing',
      'discardPairing',
      'stopAndDrain',
    ]) {
      expect(source, contains(required), reason: required);
    }
  });

  test(
    'Flutter production assembly attaches one platform pairing authority',
    () {
      final source = File(
        'lib/features/app_shell/atlas_app.dart',
      ).readAsStringSync();

      for (final required in <String>[
        'AtlasVaultTrustedPairingContext',
        'attachTrustedPairingContext',
        '_attachAndroidTrustedPairing',
        '_attachWindowsTrustedPairing',
        'AtlasVaultTrustedPairingCoordinator',
        'AtlasVaultTrustedPairingPanel',
      ]) {
        expect(source, contains(required), reason: required);
      }
    },
  );
}
