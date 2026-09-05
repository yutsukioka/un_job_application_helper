import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  for (final disposition in [
    AtlasVaultTrustedPairingDisposition.completed,
    AtlasVaultTrustedPairingDisposition.codesConfirmed,
    AtlasVaultTrustedPairingDisposition.failed,
    AtlasVaultTrustedPairingDisposition.cancelled,
  ]) {
    test('C30 terminal or consumed comparison is cleared: ${disposition.name}', () async {
      final coordinator = C30Coordinator(AtlasVaultTrustedPairingResult(
        disposition: disposition,
        sas: 'ABCD-EF12-3456',
        expiresAt: '2099-01-01T00:00:00Z',
      ));
      final owner = AtlasVaultTrustedPairingPresentationOwner(coordinator: coordinator);
      addTearDown(owner.dispose);
      await owner.resumePairing();
      expect(owner.sas == null, isTrue);
    });
  }

  test('C30 expired comparison is never displayed', () async {
    final coordinator = C30Coordinator(const AtlasVaultTrustedPairingResult(
      disposition: AtlasVaultTrustedPairingDisposition.codesReady,
      stage: AtlasVaultPairingStage.acceptanceImported,
      sas: 'ABCD-EF12-3456',
      expiresAt: '2000-01-01T00:00:00Z',
      pendingTransaction: true,
    ));
    final owner = AtlasVaultTrustedPairingPresentationOwner(coordinator: coordinator);
    addTearDown(owner.dispose);
    await owner.resumePairing();
    expect(owner.sas == null, isTrue);
  });

  test('C30 confirmation without a displayed ceremony cannot reach coordinator', () async {
    final coordinator = C30Coordinator(const AtlasVaultTrustedPairingResult(
      disposition: AtlasVaultTrustedPairingDisposition.ready,
    ));
    final owner = AtlasVaultTrustedPairingPresentationOwner(coordinator: coordinator);
    addTearDown(owner.dispose);
    await owner.confirmCodesMatch();
    expect(coordinator.confirmations, 0);
  });
}

final class C30Coordinator implements AtlasVaultTrustedPairingCoordinating {
  C30Coordinator(this.result);
  AtlasVaultTrustedPairingResult result;
  int confirmations = 0;
  @override
  void cancelActiveOperation() {}
  @override
  Future<void> stop() async {}
  @override
  dynamic noSuchMethod(Invocation invocation) {
    if (invocation.memberName == #confirmCodesMatch) confirmations++;
    return Future<AtlasVaultTrustedPairingResult>.value(result);
  }
}
