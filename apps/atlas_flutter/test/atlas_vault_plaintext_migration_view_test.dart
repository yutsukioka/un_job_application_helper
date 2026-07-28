import 'package:atlas/atlas_vault_android.dart';
import 'package:atlas/src/atlas_vault/plaintext_migration_view.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('owner construction performs no migration operation', () {
    final coordinator = _FakeMigrationCoordinator();

    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);

    expect(coordinator.calls, isEmpty);
    expect(owner.status, AtlasVaultPlaintextMigrationPresentationStatus.hidden);
    expect(owner.savedSearchCount, 0);
    expect(owner.trackerRecordCount, 0);
    expect(
      owner.toString(),
      'AtlasVaultPlaintextMigrationPresentationOwner(<redacted>)',
    );
  });

  test(
    'authority bootstrap is explicit and publishes no private value',
    () async {
      final coordinator = _FakeMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
      );
      final owner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: coordinator,
      );
      addTearDown(owner.dispose);

      await owner.bootstrapAuthority();

      expect(coordinator.calls, <String>['inspect-authority']);
      expect(
        owner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired,
      );
      expect(owner.blocksLegacyPrivateAuthority, isTrue);
      expect(owner.toString(), isNot(contains('PRIVATE_QUERY')));
    },
  );

  testWidgets('inventory and both migration confirmations are explicit', (
    tester,
  ) async {
    final coordinator = _FakeMigrationCoordinator();
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultPlaintextMigrationPanel(owner: owner)),
      ),
    );

    expect(find.text('Review Private Data Migration'), findsOneWidget);
    expect(coordinator.calls, isEmpty);

    await tester.tap(find.text('Review Private Data Migration'));
    await tester.pumpAndSettle();
    expect(coordinator.calls, <String>['inventory']);
    expect(find.text('1 saved search'), findsOneWidget);
    expect(find.text('1 tracker record'), findsOneWidget);
    expect(find.text('PRIVATE_QUERY'), findsNothing);
    expect(find.text('private tracker notes'), findsNothing);

    await tester.tap(find.text('Prepare Encrypted Migration'));
    await tester.pumpAndSettle();
    expect(find.text('Prepare Encrypted Migration?'), findsOneWidget);
    expect(coordinator.calls, <String>['inventory']);
    await tester.tap(find.widgetWithText(TextButton, 'Prepare'));
    await tester.pumpAndSettle();
    expect(coordinator.calls, <String>['inventory', 'prepare']);
    expect(find.text('Encrypted copy verified'), findsOneWidget);

    await tester.tap(find.text('Remove Plaintext & Activate AtlasVault'));
    await tester.pumpAndSettle();
    expect(
      find.text('Remove Plaintext & Activate AtlasVault?'),
      findsOneWidget,
    );
    expect(
      find.textContaining('rollback will no longer be available'),
      findsOneWidget,
    );
    expect(
      find.textContaining('device-local Android protection'),
      findsOneWidget,
    );
    expect(
      find.textContaining('Windows secure storage is not implemented'),
      findsOneWidget,
    );
    expect(coordinator.calls, <String>['inventory', 'prepare']);

    await tester.tap(find.widgetWithText(TextButton, 'Remove & Activate'));
    await tester.pumpAndSettle();
    expect(coordinator.calls, <String>['inventory', 'prepare', 'finalize']);
    expect(owner.status, AtlasVaultPlaintextMigrationPresentationStatus.active);
  });

  testWidgets(
    'prepared migration may be discarded but pending commit may not',
    (tester) async {
      final coordinator = _FakeMigrationCoordinator();
      final owner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: coordinator,
      );
      addTearDown(owner.dispose);
      await owner.reviewInventory();
      await owner.prepareEncryptedMigration();

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: AtlasVaultPlaintextMigrationPanel(owner: owner)),
        ),
      );
      expect(find.text('Discard Prepared Migration'), findsOneWidget);

      coordinator.authorityState =
          AtlasVaultPlaintextAuthorityState.migrationPending;
      coordinator.resumeStage =
          AtlasVaultPlaintextMigrationStage.commitInProgress;
      await owner.bootstrapAuthority();
      await tester.pump();

      expect(find.text('Resume Migration'), findsOneWidget);
      expect(find.text('Discard Prepared Migration'), findsNothing);
    },
  );
}

final class _FakeMigrationCoordinator
    implements AtlasVaultPlaintextMigrationCoordinating {
  _FakeMigrationCoordinator({
    this.authorityState = AtlasVaultPlaintextAuthorityState.legacy,
  });

  final List<String> calls = <String>[];
  AtlasVaultPlaintextAuthorityState authorityState;
  AtlasVaultPlaintextMigrationStage resumeStage =
      AtlasVaultPlaintextMigrationStage.commitInProgress;

  AtlasVaultPlaintextMigrationSummary get _inventorySummary =>
      const AtlasVaultPlaintextMigrationSummary(
        savedSearchCount: 1,
        trackerRecordCount: 1,
        localCachePrivatePresent: true,
        compatibilityPrivatePresent: true,
      );

  AtlasVaultPlaintextMigrationSummary _stageSummary(
    AtlasVaultPlaintextMigrationStage stage,
  ) {
    return AtlasVaultPlaintextMigrationSummary(
      savedSearchCount: 1,
      trackerRecordCount: 1,
      localCachePrivatePresent: true,
      compatibilityPrivatePresent: true,
      stage: stage,
    );
  }

  @override
  Future<AtlasVaultPlaintextAuthorityState> inspectAuthority() async {
    calls.add('inspect-authority');
    return authorityState;
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> inventory() async {
    calls.add('inventory');
    return _inventorySummary;
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> prepare() async {
    calls.add('prepare');
    authorityState = AtlasVaultPlaintextAuthorityState.migrationPending;
    return _stageSummary(AtlasVaultPlaintextMigrationStage.encryptedVerified);
  }

  @override
  Future<void> discardPrepared() async {
    calls.add('discard');
    authorityState = AtlasVaultPlaintextAuthorityState.legacy;
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> finalizeAndActivate() async {
    calls.add('finalize');
    authorityState = AtlasVaultPlaintextAuthorityState.encryptedActive;
    return _stageSummary(AtlasVaultPlaintextMigrationStage.completionPending);
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> resume() async {
    calls.add('resume');
    return _stageSummary(resumeStage);
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> activateSelected() async {
    calls.add('activate-selected');
    authorityState = AtlasVaultPlaintextAuthorityState.encryptedActive;
    return _stageSummary(AtlasVaultPlaintextMigrationStage.completionPending);
  }
}
