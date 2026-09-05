import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  AtlasVaultTrustedPairingResult live({
    String? transcript =
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    String expiry = '2026-01-01T00:00:02Z',
  }) => AtlasVaultTrustedPairingResult(
    disposition: AtlasVaultTrustedPairingDisposition.codesReady,
    stage: AtlasVaultPairingStage.acceptanceImported,
    transcriptSha256: transcript,
    sas: 'ABCD-EF12-3456',
    expiresAt: expiry,
    pendingTransaction: true,
  );

  test(
    'C30 live comparison confirmation is transcript-bound and one-shot',
    () async {
      final coordinator = C30Coordinator(live());
      final owner = AtlasVaultTrustedPairingPresentationOwner(
        coordinator: coordinator,
        now: () => DateTime.utc(2026, 1, 1),
      );
      addTearDown(owner.dispose);
      await owner.resumePairing();
      expect(owner.sas != null, isTrue);
      expect(owner.toString().contains(owner.sas!), isFalse);
      expect(coordinator.result.toString().contains(owner.sas!), isFalse);
      coordinator.result = const AtlasVaultTrustedPairingResult(
        disposition: AtlasVaultTrustedPairingDisposition.codesConfirmed,
      );
      await owner.confirmCodesMatch();
      expect(coordinator.confirmations, 1);
      expect(coordinator.confirmedTranscript == 'a' * 64, isTrue);
      expect(owner.sas == null, isTrue);
      await owner.confirmCodesMatch();
      expect(coordinator.confirmations, 1);
    },
  );

  for (final transcript in [null, '', 'wrong']) {
    test(
      'C30 missing or invalid transcript cannot present comparison: $transcript',
      () async {
        final owner = AtlasVaultTrustedPairingPresentationOwner(
          coordinator: C30Coordinator(live(transcript: transcript)),
          now: () => DateTime.utc(2026, 1, 1),
        );
        addTearDown(owner.dispose);
        await owner.resumePairing();
        expect(owner.sas == null, isTrue);
      },
    );
  }

  testWidgets(
    'C30 timer clears comparison even when wall clock does not advance',
    (tester) async {
      final coordinator = C30Coordinator(live());
      final owner = AtlasVaultTrustedPairingPresentationOwner(
        coordinator: coordinator,
        now: () => DateTime.utc(2026, 1, 1),
      );
      await owner.resumePairing();
      expect(owner.sas != null, isTrue);
      await tester.pump(const Duration(seconds: 3));
      expect(owner.sas == null, isTrue);
      await owner.confirmCodesMatch();
      expect(coordinator.confirmations, 0);
      owner.dispose();
    },
  );

  test('C30 dismiss clears comparison and blocks late confirmation', () async {
    final coordinator = C30Coordinator(live());
    final owner = AtlasVaultTrustedPairingPresentationOwner(
      coordinator: coordinator,
      now: () => DateTime.utc(2026, 1, 1),
    );
    addTearDown(owner.dispose);
    await owner.resumePairing();
    owner.hide();
    expect(owner.sas == null, isTrue);
    await owner.confirmCodesMatch();
    expect(coordinator.confirmations, 0);
  });

  for (final disposition in [
    AtlasVaultTrustedPairingDisposition.completed,
    AtlasVaultTrustedPairingDisposition.codesConfirmed,
    AtlasVaultTrustedPairingDisposition.failed,
    AtlasVaultTrustedPairingDisposition.cancelled,
  ]) {
    test(
      'C30 terminal or consumed comparison is cleared: ${disposition.name}',
      () async {
        final coordinator = C30Coordinator(
          AtlasVaultTrustedPairingResult(
            disposition: disposition,
            sas: 'ABCD-EF12-3456',
            expiresAt: '2099-01-01T00:00:00Z',
          ),
        );
        final owner = AtlasVaultTrustedPairingPresentationOwner(
          coordinator: coordinator,
        );
        addTearDown(owner.dispose);
        await owner.resumePairing();
        expect(owner.sas == null, isTrue);
      },
    );
  }

  test('C30 expired comparison is never displayed', () async {
    final coordinator = C30Coordinator(
      const AtlasVaultTrustedPairingResult(
        disposition: AtlasVaultTrustedPairingDisposition.codesReady,
        stage: AtlasVaultPairingStage.acceptanceImported,
        sas: 'ABCD-EF12-3456',
        expiresAt: '2000-01-01T00:00:00Z',
        pendingTransaction: true,
      ),
    );
    final owner = AtlasVaultTrustedPairingPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);
    await owner.resumePairing();
    expect(owner.sas == null, isTrue);
  });

  test(
    'C30 confirmation without a displayed ceremony cannot reach coordinator',
    () async {
      final coordinator = C30Coordinator(
        const AtlasVaultTrustedPairingResult(
          disposition: AtlasVaultTrustedPairingDisposition.ready,
        ),
      );
      final owner = AtlasVaultTrustedPairingPresentationOwner(
        coordinator: coordinator,
      );
      addTearDown(owner.dispose);
      await owner.confirmCodesMatch();
      expect(coordinator.confirmations, 0);
    },
  );
}

final class C30Coordinator implements AtlasVaultTrustedPairingCoordinating {
  C30Coordinator(this.result);
  AtlasVaultTrustedPairingResult result;
  int confirmations = 0;
  String? confirmedTranscript;
  @override
  void cancelActiveOperation() {}
  @override
  Future<void> stop() async {}
  @override
  dynamic noSuchMethod(Invocation invocation) {
    if (invocation.memberName == #confirmCodesMatch) {
      confirmations++;
      confirmedTranscript =
          invocation.namedArguments[#expectedTranscriptSha256] as String?;
    }
    return Future<AtlasVaultTrustedPairingResult>.value(result);
  }
}
