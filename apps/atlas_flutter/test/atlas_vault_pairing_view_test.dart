import 'dart:async';
import 'dart:io';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('pairing presentation construction performs no operation', () async {
    final coordinator = _PairingViewCoordinator();
    final owner = AtlasVaultTrustedPairingPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);

    expect(coordinator.calls, isEmpty);
    expect(owner.status, AtlasVaultTrustedPairingPresentationStatus.hidden);

    await owner.createDeviceIdentity();
    expect(coordinator.calls, <String>['identity']);
    expect(
      owner.status,
      AtlasVaultTrustedPairingPresentationStatus.identityReady,
    );
    expect(owner.localFingerprint, 'AAAA-BBBB-CCCC-DDDD');
  });

  test('pairing presentation retains one operation and clears SAS', () async {
    final coordinator = _PairingViewCoordinator();
    final owner = AtlasVaultTrustedPairingPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);
    final blocked = Completer<AtlasVaultTrustedPairingResult>();
    coordinator.next = blocked.future;

    final first = owner.resumePairing();
    await owner.discardPairing();
    expect(coordinator.calls, <String>['resume']);
    blocked.complete(
      const AtlasVaultTrustedPairingResult(
        disposition: AtlasVaultTrustedPairingDisposition.codesReady,
        sas: 'ABCD-EF12-3456',
        pendingTransaction: true,
      ),
    );
    await first;
    expect(owner.sas, 'ABCD-EF12-3456');

    owner.clearSensitiveInput();
    expect(owner.sas, isNull);
    expect(coordinator.cancelCalls, 1);
  });

  test('pairing stop signals cancellation before draining', () async {
    final coordinator = _PairingViewCoordinator();
    final owner = AtlasVaultTrustedPairingPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);
    final blocked = Completer<AtlasVaultTrustedPairingResult>();
    coordinator.next = blocked.future;
    final operation = owner.resumePairing();

    final stopping = owner.stopAndDrain();
    await Future<void>.value();
    final cancellationObservedBeforeCompletion = coordinator.cancelCalls;
    blocked.complete(
      const AtlasVaultTrustedPairingResult(
        disposition: AtlasVaultTrustedPairingDisposition.cancelled,
      ),
    );
    await operation;
    await stopping;

    expect(cancellationObservedBeforeCompletion, 1);
  });

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

  test('Flutter shell cancels pairing on lifecycle and tab dismissal', () {
    final source = File(
      'lib/features/app_shell/atlas_app.dart',
    ).readAsStringSync();

    expect(source, contains('with WidgetsBindingObserver'));
    expect(source, contains('void didChangeAppLifecycleState('));
    expect(
      source,
      contains(
        '_controller.trustedPairingContext?.owner.clearSensitiveInput()',
      ),
    );
    expect(source, contains('_dismissPairingForTabChange('));
  });
}

final class _PairingViewCoordinator
    implements AtlasVaultTrustedPairingCoordinating {
  final List<String> calls = <String>[];
  Future<AtlasVaultTrustedPairingResult>? next;
  int cancelCalls = 0;

  void cancelActiveOperation() {
    cancelCalls += 1;
  }

  Future<AtlasVaultTrustedPairingResult> _result(String call) async {
    calls.add(call);
    final pending = next;
    next = null;
    if (pending != null) return pending;
    return const AtlasVaultTrustedPairingResult(
      disposition: AtlasVaultTrustedPairingDisposition.identityReady,
      localFingerprint: 'AAAA-BBBB-CCCC-DDDD',
    );
  }

  @override
  Future<AtlasVaultTrustedPairingResult> inspect() => _result('inspect');

  @override
  Future<AtlasVaultTrustedPairingResult> createDeviceIdentity() =>
      _result('identity');

  @override
  Future<AtlasVaultTrustedPairingResult> createPairingOffer() =>
      _result('createOffer');

  @override
  Future<AtlasVaultTrustedPairingResult> savePairingOffer() =>
      _result('saveOffer');

  @override
  Future<AtlasVaultTrustedPairingResult> importPairingOffer() =>
      _result('importOffer');

  @override
  Future<AtlasVaultTrustedPairingResult> savePairingAcceptance() =>
      _result('saveAcceptance');

  @override
  Future<AtlasVaultTrustedPairingResult> importPairingAcceptance() =>
      _result('importAcceptance');

  @override
  Future<AtlasVaultTrustedPairingResult> confirmCodesMatch() =>
      _result('confirm');

  @override
  Future<AtlasVaultTrustedPairingResult> saveKeyDelivery() =>
      _result('saveDelivery');

  @override
  Future<AtlasVaultTrustedPairingResult> importKeyDelivery() =>
      _result('importDelivery');

  @override
  Future<AtlasVaultTrustedPairingResult> savePairingAcknowledgement() =>
      _result('saveAcknowledgement');

  @override
  Future<AtlasVaultTrustedPairingResult> importPairingAcknowledgement() =>
      _result('importAcknowledgement');

  @override
  Future<AtlasVaultTrustedPairingResult> resumePairing() => _result('resume');

  @override
  Future<AtlasVaultTrustedPairingResult> discardPairing() => _result('discard');

  @override
  Future<void> stop() async => calls.add('stop');
}
