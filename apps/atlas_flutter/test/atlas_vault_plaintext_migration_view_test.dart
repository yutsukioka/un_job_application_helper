import 'dart:async';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault_android.dart';
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

  test('selected encrypted authority requires explicit activation', () async {
    final coordinator = _FakeMigrationCoordinator(
      authorityState:
          AtlasVaultPlaintextAuthorityState.encryptedSelectedInactive,
    );
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);

    await owner.bootstrapAuthority();

    expect(
      owner.status,
      AtlasVaultPlaintextMigrationPresentationStatus.activationRequired,
    );
    expect(coordinator.calls, <String>['inspect-authority']);

    await owner.activateEncryptedPrivateData();

    expect(coordinator.calls, <String>[
      'inspect-authority',
      'activate-selected',
    ]);
    expect(owner.status, AtlasVaultPlaintextMigrationPresentationStatus.active);
  });

  test('late retained inventory cannot republish after hide', () async {
    final entered = Completer<void>();
    final release = Completer<void>();
    final coordinator = _FakeMigrationCoordinator(
      inventoryEntered: entered,
      releaseInventory: release,
    );
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);

    final first = owner.reviewInventory();
    await entered.future;
    final coalesced = owner.reviewInventory();
    expect(identical(first, coalesced), isTrue);
    await expectLater(
      owner.prepareEncryptedMigration(),
      throwsA(isA<AtlasVaultPlaintextMigrationException>()),
    );

    owner.hide();
    release.complete();
    await first;

    expect(coordinator.calls, <String>['inventory']);
    expect(owner.status, AtlasVaultPlaintextMigrationPresentationStatus.hidden);
    expect(owner.savedSearchCount, 0);
    expect(owner.trackerRecordCount, 0);
  });

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
    'Windows confirmations describe DPAPI and the rollback boundary',
    (tester) async {
      final coordinator = _FakeMigrationCoordinator();
      final owner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: coordinator,
      );
      addTearDown(owner.dispose);

      await owner.reviewInventory();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: AtlasVaultPlaintextMigrationPanel(
              owner: owner,
              platform:
                  AtlasVaultPlaintextMigrationPresentationPlatform.windows,
            ),
          ),
        ),
      );

      await tester.tap(find.text('Prepare Encrypted Migration'));
      await tester.pumpAndSettle();
      expect(find.textContaining('current-user Windows DPAPI'), findsOneWidget);
      expect(
        find.textContaining('Plaintext data remains unchanged'),
        findsOneWidget,
      );
      expect(find.textContaining('Android'), findsNothing);

      await tester.tap(find.widgetWithText(TextButton, 'Prepare'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Remove Plaintext & Activate AtlasVault'));
      await tester.pumpAndSettle();

      expect(
        find.textContaining('encrypted read-back is complete'),
        findsOneWidget,
      );
      expect(
        find.textContaining('plaintext deletion will begin'),
        findsOneWidget,
      );
      expect(
        find.textContaining('rollback becomes unavailable'),
        findsOneWidget,
      );
      expect(find.textContaining('resume after interruption'), findsOneWidget);
      expect(find.textContaining('current-user Windows DPAPI'), findsOneWidget);
      expect(
        find.textContaining(
          'Windows encrypted import/export is not yet implemented',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('Android'), findsNothing);
      expect(find.text('PRIVATE_QUERY'), findsNothing);
      expect(find.text('private tracker notes'), findsNothing);
    },
  );

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

  testWidgets('discard publishes a rollback-specific busy state', (
    tester,
  ) async {
    final discardEntered = Completer<void>();
    final releaseDiscard = Completer<void>();
    addTearDown(() {
      if (!releaseDiscard.isCompleted) {
        releaseDiscard.complete();
      }
    });
    final coordinator = _FakeMigrationCoordinator(
      discardEntered: discardEntered,
      releaseDiscard: releaseDiscard,
    );
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
      legacyPrivateStateRestorer: _FakeLegacyPrivateStateRestorer(),
    );
    addTearDown(owner.dispose);
    await owner.reviewInventory();
    await owner.prepareEncryptedMigration();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultPlaintextMigrationPanel(owner: owner)),
      ),
    );

    final discard = owner.discardPreparedMigration();
    await discardEntered.future;
    await tester.pump();

    expect(
      owner.status,
      AtlasVaultPlaintextMigrationPresentationStatus.discarding,
    );
    expect(find.text('Discarding prepared migration...'), findsOneWidget);
    expect(find.text('Preparing encrypted copy...'), findsNothing);
    expect(owner.blocksLegacyPrivateAuthority, isTrue);

    releaseDiscard.complete();
    await discard;
    await tester.pump();
    expect(
      owner.status,
      AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
    );
  });

  test(
    'restart rollback restores legacy state before publishing authority',
    () async {
      final restoreEntered = Completer<void>();
      final releaseRestore = Completer<void>();
      addTearDown(() {
        if (!releaseRestore.isCompleted) {
          releaseRestore.complete();
        }
      });
      final coordinator = _FakeMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
        durableRollbackPending: true,
      )..resumeStage = AtlasVaultPlaintextMigrationStage.encryptedVerified;
      final restorer = _FakeLegacyPrivateStateRestorer(
        entered: restoreEntered,
        release: releaseRestore,
      );
      final owner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: coordinator,
        legacyPrivateStateRestorer: restorer,
      );
      addTearDown(owner.dispose);
      await owner.bootstrapAuthority();
      await owner.resumeMigration();
      expect(
        owner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.prepared,
      );

      final discard = owner.discardPreparedMigration();
      await Future.any<void>(<Future<void>>[
        restoreEntered.future,
        discard.then<void>((_) {
          throw TestFailure(
            'rollback completed without durable legacy restoration',
          );
        }),
      ]);

      expect(restorer.calls, <String>['restore']);
      expect(
        owner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.restoringLegacy,
      );
      expect(owner.blocksLegacyPrivateAuthority, isTrue);

      releaseRestore.complete();
      await discard;

      expect(
        owner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
      );
      expect(owner.blocksLegacyPrivateAuthority, isFalse);
      expect(coordinator.calls, <String>[
        'inspect-authority',
        'resume',
        'discard',
        'inspect-authority',
      ]);
    },
  );

  test(
    'rollback restoration failure never publishes legacy authority',
    () async {
      final coordinator = _FakeMigrationCoordinator(
        authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
        durableRollbackPending: true,
      )..resumeStage = AtlasVaultPlaintextMigrationStage.encryptedVerified;
      final restorer = _FakeLegacyPrivateStateRestorer(fail: true);
      final owner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: coordinator,
        legacyPrivateStateRestorer: restorer,
      );
      addTearDown(owner.dispose);
      await owner.bootstrapAuthority();
      await owner.resumeMigration();

      await owner.discardPreparedMigration();

      expect(restorer.calls, <String>['restore']);
      expect(
        owner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired,
      );
      expect(
        owner.status,
        isNot(AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable),
      );
      expect(owner.blocksLegacyPrivateAuthority, isTrue);
      expect(owner.blocksPersistedCacheWrites, isTrue);
      expect(owner.savedSearchCount, 0);
      expect(owner.trackerRecordCount, 0);
    },
  );

  testWidgets('partially prepared resume failure exposes explicit discard', (
    tester,
  ) async {
    final coordinator =
        _FakeMigrationCoordinator(
            authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
          )
          ..resumeStage = AtlasVaultPlaintextMigrationStage.prepared
          ..resumeFailureAuthority =
              AtlasVaultPlaintextAuthorityState.migrationPending
          ..preparedRollbackAvailable = true;
    final restorer = _FakeLegacyPrivateStateRestorer();
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
      legacyPrivateStateRestorer: restorer,
    );
    addTearDown(owner.dispose);

    await owner.bootstrapAuthority();
    await owner.resumeMigration();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultPlaintextMigrationPanel(owner: owner)),
      ),
    );

    expect(
      owner.status,
      AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired,
    );
    expect(find.text('Resume Migration'), findsOneWidget);
    expect(find.text('Discard Prepared Migration'), findsOneWidget);

    await tester.tap(find.text('Discard Prepared Migration'));
    await tester.pumpAndSettle();

    expect(coordinator.calls, contains('discard'));
    expect(restorer.calls, <String>['restore']);
    expect(
      owner.status,
      AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
    );
  });

  test('failed authority inspection cannot republish after hide', () async {
    final inspectEntered = Completer<void>();
    final releaseInspect = Completer<void>();
    addTearDown(() {
      if (!releaseInspect.isCompleted) {
        releaseInspect.complete();
      }
    });
    final coordinator =
        _FakeMigrationCoordinator(
            authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
            inspectEntered: inspectEntered,
            releaseInspect: releaseInspect,
            gatedInspectCall: 2,
            failGatedInspect: true,
          )
          ..resumeFailureAuthority =
              AtlasVaultPlaintextAuthorityState.migrationPending;
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);
    await owner.bootstrapAuthority();
    var notifications = 0;
    owner.addListener(() => notifications += 1);

    final resume = owner.resumeMigration();
    await inspectEntered.future;
    owner.hide();
    final notificationsAfterHide = notifications;
    releaseInspect.complete();
    await resume;

    expect(owner.status, AtlasVaultPlaintextMigrationPresentationStatus.hidden);
    expect(notifications, notificationsAfterHide);
  });

  test('failed completion inspection cannot notify after disposal', () async {
    final inspectEntered = Completer<void>();
    final releaseInspect = Completer<void>();
    addTearDown(() {
      if (!releaseInspect.isCompleted) {
        releaseInspect.complete();
      }
    });
    final coordinator = _FakeMigrationCoordinator(
      authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
      inspectEntered: inspectEntered,
      releaseInspect: releaseInspect,
      gatedInspectCall: 2,
      failGatedInspect: true,
    )..resumeCompletesDurableRollback = true;
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
    );
    await owner.bootstrapAuthority();

    final resume = owner.resumeMigration();
    await inspectEntered.future;
    owner.dispose();
    releaseInspect.complete();

    await expectLater(resume, completes);
  });

  testWidgets('post-commit finalization failure exposes explicit resume', (
    tester,
  ) async {
    final coordinator = _FakeMigrationCoordinator()..failFinalize = true;
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);
    await owner.reviewInventory();
    await owner.prepareEncryptedMigration();

    await owner.finalizeMigration();

    expect(
      owner.status,
      AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired,
    );
    expect(coordinator.calls, <String>[
      'inventory',
      'prepare',
      'finalize',
      'inspect-authority',
    ]);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultPlaintextMigrationPanel(owner: owner)),
      ),
    );
    expect(find.text('Resume Migration'), findsOneWidget);
    expect(find.textContaining('requires recovery'), findsNothing);
  });

  test('completed rollback resume returns owner to legacy authority', () async {
    final coordinator = _FakeMigrationCoordinator(
      authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
    )..resumeCompletesDurableRollback = true;
    final restorer = _FakeLegacyPrivateStateRestorer();
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
      legacyPrivateStateRestorer: restorer,
    );
    addTearDown(owner.dispose);
    await owner.bootstrapAuthority();

    await owner.resumeMigration();

    expect(coordinator.calls, <String>[
      'inspect-authority',
      'resume',
      'inspect-authority',
    ]);
    expect(
      owner.status,
      AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
    );
    expect(restorer.calls, <String>['restore']);
    expect(owner.savedSearchCount, 0);
    expect(owner.trackerRecordCount, 0);
    expect(owner.blocksLegacyPrivateAuthority, isFalse);
  });

  testWidgets(
    'transient resume failure preserves explicit resume while pending',
    (tester) async {
      final coordinator =
          _FakeMigrationCoordinator(
              authorityState:
                  AtlasVaultPlaintextAuthorityState.migrationPending,
            )
            ..resumeFailureAuthority =
                AtlasVaultPlaintextAuthorityState.migrationPending;
      final owner = AtlasVaultPlaintextMigrationPresentationOwner(
        coordinator: coordinator,
      );
      addTearDown(owner.dispose);
      await owner.bootstrapAuthority();

      await owner.resumeMigration();

      expect(coordinator.calls, <String>[
        'inspect-authority',
        'resume',
        'inspect-authority',
      ]);
      expect(
        owner.status,
        AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired,
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: AtlasVaultPlaintextMigrationPanel(owner: owner)),
        ),
      );
      expect(find.text('Resume Migration'), findsOneWidget);
      expect(find.textContaining('requires recovery'), findsNothing);
    },
  );

  test('failed resume adopts authoritative completed activation', () async {
    final coordinator =
        _FakeMigrationCoordinator(
            authorityState: AtlasVaultPlaintextAuthorityState.migrationPending,
          )
          ..resumeFailureAuthority =
              AtlasVaultPlaintextAuthorityState.encryptedActive;
    final owner = AtlasVaultPlaintextMigrationPresentationOwner(
      coordinator: coordinator,
    );
    addTearDown(owner.dispose);
    await owner.bootstrapAuthority();

    await owner.resumeMigration();

    expect(coordinator.calls, <String>[
      'inspect-authority',
      'resume',
      'inspect-authority',
    ]);
    expect(owner.status, AtlasVaultPlaintextMigrationPresentationStatus.active);
    expect(owner.blocksLegacyPrivateAuthority, isTrue);
  });
}

final class _FakeLegacyPrivateStateRestorer
    implements AtlasVaultLegacyPrivateStateRestoring {
  _FakeLegacyPrivateStateRestorer({
    this.entered,
    this.release,
    this.fail = false,
  });

  final Completer<void>? entered;
  final Completer<void>? release;
  final bool fail;
  final List<String> calls = <String>[];
  final List<AtlasVaultPlaintextPrivateState> restoredStates =
      <AtlasVaultPlaintextPrivateState>[];

  @override
  Future<void> restoreLegacyPrivateStateAfterRollback(
    AtlasVaultPlaintextPrivateState reviewedState,
  ) async {
    calls.add('restore');
    restoredStates.add(reviewedState);
    entered?.complete();
    await release?.future;
    if (fail) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }
}

final class _FakeMigrationCoordinator
    implements AtlasVaultPlaintextMigrationCoordinating {
  _FakeMigrationCoordinator({
    this.authorityState = AtlasVaultPlaintextAuthorityState.legacy,
    this.inventoryEntered,
    this.releaseInventory,
    this.inspectEntered,
    this.releaseInspect,
    this.gatedInspectCall,
    this.failGatedInspect = false,
    this.discardEntered,
    this.releaseDiscard,
    this.durableRollbackPending = false,
  });

  final List<String> calls = <String>[];
  AtlasVaultPlaintextAuthorityState authorityState;
  final Completer<void>? inventoryEntered;
  final Completer<void>? releaseInventory;
  final Completer<void>? inspectEntered;
  final Completer<void>? releaseInspect;
  final int? gatedInspectCall;
  final bool failGatedInspect;
  final Completer<void>? discardEntered;
  final Completer<void>? releaseDiscard;
  final bool durableRollbackPending;
  int _inspectCalls = 0;
  AtlasVaultPlaintextMigrationStage resumeStage =
      AtlasVaultPlaintextMigrationStage.commitInProgress;
  bool preparedRollbackAvailable = false;
  bool failFinalize = false;
  bool resumeCompletesDurableRollback = false;
  AtlasVaultPlaintextAuthorityState? resumeFailureAuthority;

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
    _inspectCalls += 1;
    if (_inspectCalls == gatedInspectCall) {
      inspectEntered?.complete();
      await releaseInspect?.future;
      if (failGatedInspect) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    }
    return authorityState;
  }

  @override
  Future<bool> inspectPreparedRollbackAvailability() async =>
      preparedRollbackAvailable;

  @override
  Future<AtlasVaultPlaintextMigrationSummary> inventory() async {
    calls.add('inventory');
    inventoryEntered?.complete();
    await releaseInventory?.future;
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
    discardEntered?.complete();
    await releaseDiscard?.future;
    authorityState = durableRollbackPending
        ? AtlasVaultPlaintextAuthorityState.migrationPending
        : AtlasVaultPlaintextAuthorityState.legacy;
  }

  @override
  Future<void> restoreReviewedLegacyPrivateState(
    AtlasVaultLegacyPrivateStateRestoring restorer,
  ) async {
    await restorer.restoreLegacyPrivateStateAfterRollback(
      AtlasVaultPlaintextPrivateState(
        savedSearches: const <AtlasSavedSearch>[],
        trackerRecords: const <AtlasApplicationRecord>[],
        authorityBaseURL: Uri.parse('http://atlas.test:8765'),
      ),
    );
    authorityState = AtlasVaultPlaintextAuthorityState.legacy;
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> finalizeAndActivate() async {
    calls.add('finalize');
    if (failFinalize) {
      authorityState = AtlasVaultPlaintextAuthorityState.migrationPending;
      throw const AtlasVaultPlaintextMigrationException();
    }
    authorityState = AtlasVaultPlaintextAuthorityState.encryptedActive;
    return _inventorySummary;
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> resume() async {
    calls.add('resume');
    final failureAuthority = resumeFailureAuthority;
    if (failureAuthority != null) {
      authorityState = failureAuthority;
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (resumeCompletesDurableRollback) {
      authorityState = AtlasVaultPlaintextAuthorityState.migrationPending;
      return _inventorySummary;
    }
    return _stageSummary(resumeStage);
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> activateSelected() async {
    calls.add('activate-selected');
    authorityState = AtlasVaultPlaintextAuthorityState.encryptedActive;
    return _inventorySummary;
  }
}
