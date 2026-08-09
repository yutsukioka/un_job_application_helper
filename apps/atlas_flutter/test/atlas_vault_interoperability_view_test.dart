import 'dart:async';

import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'Windows profile presents fixed device-local recovery wording',
    () async {
      final coordinator = _FakeCoordinator(
        availability: const AtlasVaultRecoveryExportAvailability(
          available: true,
          encryptedRecordCount: 3,
          recoveryWrapPresent: true,
        ),
      );
      final owner = AtlasVaultInteroperabilityPresentationOwner(
        coordinator: coordinator,
        platformProfile: AtlasVaultInteroperabilityPlatformProfile.windows,
      );

      await owner.present();

      expect(owner.message, contains('Windows current-user'));
      expect(owner.message, isNot(contains('vault-')));
      expect(coordinator.calls, <String>['inspect-import', 'inspect']);
      owner.dispose();
    },
  );

  test('owner construction creates no operation', () {
    final coordinator = _FakeCoordinator();

    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );

    expect(coordinator.calls, isEmpty);
    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.hidden);
    expect(owner.encryptedRecordCount, 0);
    expect(owner.toString(), contains('<redacted>'));
    owner.dispose();
  });

  test('present publishes only fixed export availability', () async {
    final coordinator = _FakeCoordinator(
      availability: const AtlasVaultRecoveryExportAvailability(
        available: true,
        encryptedRecordCount: 3,
        recoveryWrapPresent: false,
      ),
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );

    await owner.present();

    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.ready);
    expect(owner.encryptedRecordCount, 3);
    expect(owner.recoveryWrapPresent, isFalse);
    expect(owner.message, isNot(contains('vault-')));
    owner.dispose();
  });

  test(
    'generated recovery key is returned but never retained by owner',
    () async {
      const recoveryText =
          'AVRK1-TEST-ONLY-RECOVERY-CODE-MUST-NOT-BE-OBSERVABLE';
      final coordinator = _FakeCoordinator(
        handle: _FakeDisplayHandle(recoveryText),
      );
      final owner = AtlasVaultInteroperabilityPresentationOwner(
        coordinator: coordinator,
      );
      await owner.present();

      final handle = await owner.beginRecoverySetup();

      expect(handle.take(), recoveryText);
      expect(handle.take(), isNull);
      expect(
        owner.status,
        AtlasVaultInteroperabilityPresentationStatus
            .awaitingRecoveryConfirmation,
      );
      expect(owner.toString(), isNot(contains(recoveryText)));
      expect(owner.message, isNot(contains(recoveryText)));
      owner.hide();
      expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.hidden);
      expect(owner.encryptedRecordCount, 0);
      owner.dispose();
    },
  );

  test('late export completion cannot republish after hide', () async {
    final gate = _Gate<AtlasVaultRecoveryExportResult>();
    final coordinator = _FakeCoordinator(confirmGate: gate);
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();
    await owner.beginRecoverySetup();

    final confirmation = owner.confirmRecoverySetup('AVRK1-TEST');
    owner.hide();
    gate.complete(
      const AtlasVaultRecoveryExportResult(
        disposition: AtlasVaultRecoveryExportDisposition.exportReady,
        encryptedRecordCount: 3,
        recoveryWrapPresent: true,
      ),
    );
    await confirmation;

    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.hidden);
    expect(owner.encryptedRecordCount, 0);
    owner.dispose();
  });

  test('late recovery generation is destroyed after hide', () async {
    final gate = _Gate<AtlasVaultRecoveryDisplayHandle>();
    final handle = _FakeDisplayHandle('AVRK1-STALE-TEST-ONLY');
    final coordinator = _FakeCoordinator(beginGate: gate);
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();

    final generation = owner.beginRecoverySetup();
    await Future<void>.value();
    owner.hide();
    gate.complete(handle);

    await expectLater(
      generation,
      throwsA(isA<AtlasVaultInteroperabilityException>()),
    );
    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.hidden);
    expect(owner.encryptedRecordCount, 0);
    expect(handle.take(), isNull);
    owner.dispose();
  });

  testWidgets('panel requires explicit setup confirmation and save actions', (
    tester,
  ) async {
    const recoveryText = 'AVRK1-TEST-ONLY-RECOVERY-CODE-MUST-NOT-BE-OBSERVABLE';
    final coordinator = _FakeCoordinator(
      handle: _FakeDisplayHandle(recoveryText),
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );

    expect(find.text('Encrypted Interoperability'), findsOneWidget);
    expect(find.text('Set Up Recovery Export'), findsOneWidget);
    expect(find.text('Save Encrypted Backup'), findsNothing);

    await tester.tap(find.text('Set Up Recovery Export'));
    await tester.pump();
    expect(find.text(recoveryText), findsOneWidget);
    expect(find.text('Confirm Recovery Key'), findsOneWidget);
    expect(coordinator.calls, <String>[
      'inspect-import',
      'inspect',
      'begin-recovery',
    ]);

    await tester.enterText(find.byType(TextField), recoveryText);
    await tester.tap(find.text('Confirm Recovery Key'));
    await tester.pump();
    await tester.pump();

    expect(find.text(recoveryText), findsNothing);
    expect(find.text('Save Encrypted Backup'), findsOneWidget);
    expect(coordinator.calls, contains('confirm-recovery'));

    await tester.tap(find.text('Save Encrypted Backup'));
    await tester.pump();
    await tester.pump();
    expect(coordinator.calls, contains('save-export'));
    expect(find.text('Encrypted backup saved.'), findsOneWidget);

    owner.dispose();
  });

  testWidgets('cancel clears local recovery fields without mutation', (
    tester,
  ) async {
    const recoveryText = 'AVRK1-TEST-ONLY-RECOVERY-CODE-MUST-NOT-BE-OBSERVABLE';
    final coordinator = _FakeCoordinator(
      handle: _FakeDisplayHandle(recoveryText),
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );

    await tester.tap(find.text('Set Up Recovery Export'));
    await tester.pump();
    await tester.enterText(find.byType(TextField), recoveryText);
    await tester.tap(find.text('Cancel'));
    await tester.pump();

    expect(find.text(recoveryText), findsNothing);
    expect(coordinator.calls, isNot(contains('confirm-recovery')));
    owner.dispose();
  });

  testWidgets('terminal export states offer an explicit retry to ready', (
    tester,
  ) async {
    for (final status in <AtlasVaultInteroperabilityPresentationStatus>[
      AtlasVaultInteroperabilityPresentationStatus.cancelled,
      AtlasVaultInteroperabilityPresentationStatus.failed,
      AtlasVaultInteroperabilityPresentationStatus.recoveryRequired,
    ]) {
      final coordinator = _FakeCoordinator(
        availability: const AtlasVaultRecoveryExportAvailability(
          available: true,
          encryptedRecordCount: 3,
          recoveryWrapPresent: true,
        ),
      );
      final owner =
          AtlasVaultInteroperabilityPresentationOwner(coordinator: coordinator)
            ..status = status
            ..recoveryWrapPresent = true
            ..importAvailable = false
            ..pendingImport = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
        ),
      );

      expect(
        find.text('Retry Recovery Export'),
        findsOneWidget,
        reason: '$status',
      );
      await tester.tap(find.text('Retry Recovery Export'));
      await tester.pumpAndSettle();

      expect(
        owner.status,
        AtlasVaultInteroperabilityPresentationStatus.ready,
        reason: '$status',
      );
      expect(find.text('Prepare Encrypted Backup'), findsOneWidget);
      owner.dispose();
      await tester.pumpWidget(const SizedBox.shrink());
    }
  });

  testWidgets('encrypted import requires explicit pick and recovery submit', (
    tester,
  ) async {
    final coordinator = _FakeCoordinator(
      importResult: const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.importPrepared,
        encryptedRecordCount: 4,
        pendingImport: false,
      ),
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );

    expect(find.text('Import Encrypted Backup'), findsOneWidget);
    await tester.tap(find.text('Import Encrypted Backup'));
    await tester.pump();
    await tester.pump();

    expect(coordinator.calls, contains('prepare-import'));
    expect(find.text('Recovery Key'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'AVRK1-TEST');
    await tester.tap(find.text('Import and Activate'));
    await tester.pump();
    await tester.pump();

    expect(coordinator.calls, contains('confirm-import'));
    expect(find.text('AVRK1-TEST'), findsNothing);
    owner.dispose();
  });

  testWidgets('Windows modal picker focus loss preserves pending import', (
    tester,
  ) async {
    final gate = _Gate<AtlasVaultRecoveryImportResult>();
    final coordinator = _FakeCoordinator(
      importResult: const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.importPrepared,
        encryptedRecordCount: 4,
        pendingImport: false,
      ),
      importPrepareGate: gate,
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
      platformProfile: AtlasVaultInteroperabilityPlatformProfile.windows,
    );
    addTearDown(() {
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      owner.dispose();
    });
    await owner.present();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );

    await tester.tap(find.text('Import Encrypted Backup'));
    await tester.pump();
    expect(
      owner.status,
      AtlasVaultInteroperabilityPresentationStatus.pickingImport,
    );

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    await tester.pump();

    expect(
      owner.status,
      AtlasVaultInteroperabilityPresentationStatus.pickingImport,
    );
    expect(coordinator.calls, isNot(contains('discard-pending')));

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();

    gate.complete(
      const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.importPrepared,
        encryptedRecordCount: 4,
        pendingImport: false,
      ),
    );
    await tester.pumpAndSettle();

    expect(
      owner.status,
      AtlasVaultInteroperabilityPresentationStatus.awaitingImportRecoveryKey,
    );
    expect(find.text('Recovery Key'), findsOneWidget);
  });

  testWidgets('Windows modal save focus loss preserves prepared export', (
    tester,
  ) async {
    final gate = _Gate<AtlasVaultRecoveryExportResult>();
    final coordinator = _FakeCoordinator(saveGate: gate);
    final owner =
        AtlasVaultInteroperabilityPresentationOwner(
            coordinator: coordinator,
            platformProfile: AtlasVaultInteroperabilityPlatformProfile.windows,
          )
          ..status = AtlasVaultInteroperabilityPresentationStatus.exportReady
          ..encryptedRecordCount = 4
          ..recoveryWrapPresent = true;
    addTearDown(() {
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      owner.dispose();
    });
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );

    await tester.tap(find.text('Save Encrypted Backup'));
    await tester.pump();
    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.saving);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    await tester.pump();

    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.saving);
    expect(coordinator.calls, isNot(contains('discard-pending')));

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();

    gate.complete(
      const AtlasVaultRecoveryExportResult(
        disposition: AtlasVaultRecoveryExportDisposition.saved,
        encryptedRecordCount: 4,
        recoveryWrapPresent: true,
      ),
    );
    await tester.pumpAndSettle();

    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.saved);
    expect(find.text('Encrypted backup saved.'), findsOneWidget);
  });

  testWidgets(
    'Windows picker completion while inactive discards prepared import',
    (tester) async {
      final gate = _Gate<AtlasVaultRecoveryImportResult>();
      final coordinator = _FakeCoordinator(
        importResult: const AtlasVaultRecoveryImportResult(
          disposition: AtlasVaultRecoveryImportDisposition.importPrepared,
          encryptedRecordCount: 4,
          pendingImport: false,
        ),
        importPrepareGate: gate,
      );
      final owner = AtlasVaultInteroperabilityPresentationOwner(
        coordinator: coordinator,
        platformProfile: AtlasVaultInteroperabilityPlatformProfile.windows,
      );
      addTearDown(() {
        tester.binding.handleAppLifecycleStateChanged(
          AppLifecycleState.resumed,
        );
        owner.dispose();
      });
      await owner.present();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
        ),
      );

      await tester.tap(find.text('Import Encrypted Backup'));
      await tester.pump();
      expect(
        owner.status,
        AtlasVaultInteroperabilityPresentationStatus.pickingImport,
      );

      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      await tester.pump();
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pump();

      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      await tester.pump();
      expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.hidden);

      gate.complete(
        const AtlasVaultRecoveryImportResult(
          disposition: AtlasVaultRecoveryImportDisposition.importPrepared,
          encryptedRecordCount: 4,
          pendingImport: false,
        ),
      );
      await tester.pumpAndSettle();

      expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.hidden);
      expect(find.text('Recovery Key'), findsNothing);
      expect(coordinator.calls, contains('discard-pending'));
    },
  );

  testWidgets('Windows lifecycle loss outside a dialog clears and hides', (
    tester,
  ) async {
    const recoveryText = 'AVRK1-LOCAL-TEST-ONLY-MUST-BE-CLEARED';
    final coordinator = _FakeCoordinator();
    final owner =
        AtlasVaultInteroperabilityPresentationOwner(
            coordinator: coordinator,
            platformProfile: AtlasVaultInteroperabilityPlatformProfile.windows,
          )
          ..status = AtlasVaultInteroperabilityPresentationStatus
              .awaitingImportRecoveryKey
          ..importAvailable = true;
    addTearDown(() {
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      owner.dispose();
    });
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );
    await tester.enterText(find.byType(TextField), recoveryText);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    await tester.pump();

    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.hidden);
    expect(find.text(recoveryText), findsNothing);
    expect(coordinator.calls, contains('discard-pending'));
  });

  testWidgets('pending import resume advances to explicit recovery submit', (
    tester,
  ) async {
    final coordinator = _FakeCoordinator(
      importInspection: const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.resumeRequired,
        encryptedRecordCount: 4,
        pendingImport: true,
      ),
      importResult: const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.importPrepared,
        encryptedRecordCount: 4,
        pendingImport: true,
      ),
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );

    expect(find.text('Resume Recovery Import'), findsOneWidget);
    expect(find.text('Discard Pending Import'), findsOneWidget);
    expect(find.text('Import and Activate'), findsNothing);
    expect(coordinator.calls, <String>['inspect-import']);

    await tester.tap(find.text('Resume Recovery Import'));
    await tester.pumpAndSettle();

    expect(coordinator.calls, <String>['inspect-import', 'prepare-import']);
    expect(find.text('Recovery Key'), findsOneWidget);
    expect(find.text('Import and Activate'), findsOneWidget);
    owner.dispose();
  });

  testWidgets('cancelled backup reselection keeps pending import controls', (
    tester,
  ) async {
    final coordinator = _FakeCoordinator(
      importInspection: const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.resumeRequired,
        encryptedRecordCount: 4,
        pendingImport: true,
      ),
      importResult: const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.cancelled,
        encryptedRecordCount: 0,
        pendingImport: true,
      ),
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );

    await tester.tap(find.text('Resume Recovery Import'));
    await tester.pumpAndSettle();

    expect(
      owner.status,
      AtlasVaultInteroperabilityPresentationStatus.cancelled,
    );
    expect(owner.pendingImport, isTrue);
    expect(find.text('Resume Recovery Import'), findsOneWidget);
    expect(find.text('Discard Pending Import'), findsOneWidget);
    expect(find.text('Import Encrypted Backup'), findsNothing);
    owner.dispose();
  });

  testWidgets('discard pending import requires explicit confirmation', (
    tester,
  ) async {
    final coordinator = _FakeCoordinator(
      importInspection: const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.resumeRequired,
        encryptedRecordCount: 4,
        pendingImport: true,
      ),
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AtlasVaultInteroperabilityPanel(owner: owner)),
      ),
    );

    await tester.tap(find.text('Discard Pending Import'));
    await tester.pumpAndSettle();
    expect(find.text('Discard Pending Import?'), findsOneWidget);
    expect(coordinator.calls, isNot(contains('discard-import')));

    await tester.tap(find.text('Discard'));
    await tester.pumpAndSettle();
    expect(coordinator.calls, contains('discard-import'));
    expect(owner.pendingImport, isFalse);
    owner.dispose();
  });

  test('late import completion cannot republish after hide', () async {
    final gate = _Gate<AtlasVaultRecoveryImportResult>();
    final coordinator = _FakeCoordinator(
      importResult: const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.importPrepared,
        encryptedRecordCount: 4,
        pendingImport: false,
      ),
      importConfirmGate: gate,
    );
    final owner = AtlasVaultInteroperabilityPresentationOwner(
      coordinator: coordinator,
    );
    await owner.present();
    await owner.prepareRecoveryImport();

    final confirmation = owner.confirmRecoveryImport('AVRK1-TEST');
    owner.hide();
    gate.complete(
      const AtlasVaultRecoveryImportResult(
        disposition: AtlasVaultRecoveryImportDisposition.importedAndActive,
        encryptedRecordCount: 4,
        pendingImport: false,
      ),
    );
    await confirmation;

    expect(owner.status, AtlasVaultInteroperabilityPresentationStatus.hidden);
    expect(owner.encryptedRecordCount, 0);
    expect(owner.pendingImport, isFalse);
    owner.dispose();
  });
}

final class _FakeCoordinator implements AtlasVaultInteroperabilityCoordinating {
  _FakeCoordinator({
    this.availability = const AtlasVaultRecoveryExportAvailability(
      available: true,
      encryptedRecordCount: 3,
      recoveryWrapPresent: false,
    ),
    AtlasVaultRecoveryDisplayHandle? handle,
    this.beginGate,
    this.confirmGate,
    this.importResult,
    this.importInspection,
    this.importPrepareGate,
    this.importConfirmGate,
    this.saveGate,
  }) : handle = handle ?? _FakeDisplayHandle('AVRK1-TEST');

  final AtlasVaultRecoveryExportAvailability availability;
  final AtlasVaultRecoveryDisplayHandle handle;
  final _Gate<AtlasVaultRecoveryDisplayHandle>? beginGate;
  final _Gate<AtlasVaultRecoveryExportResult>? confirmGate;
  final AtlasVaultRecoveryImportResult? importResult;
  final AtlasVaultRecoveryImportResult? importInspection;
  final _Gate<AtlasVaultRecoveryImportResult>? importPrepareGate;
  final _Gate<AtlasVaultRecoveryImportResult>? importConfirmGate;
  final _Gate<AtlasVaultRecoveryExportResult>? saveGate;
  final List<String> calls = <String>[];

  @override
  Future<AtlasVaultRecoveryExportAvailability> inspectRecoveryExport() async {
    calls.add('inspect');
    return availability;
  }

  @override
  Future<AtlasVaultRecoveryDisplayHandle> beginRecoverySetup() async {
    calls.add('begin-recovery');
    return beginGate?.future ?? handle;
  }

  @override
  Future<AtlasVaultRecoveryExportResult> confirmRecoverySetup(
    String recoveryKeyText,
  ) async {
    calls.add('confirm-recovery');
    final gate = confirmGate;
    if (gate != null) {
      return gate.future;
    }
    return const AtlasVaultRecoveryExportResult(
      disposition: AtlasVaultRecoveryExportDisposition.exportReady,
      encryptedRecordCount: 3,
      recoveryWrapPresent: true,
    );
  }

  @override
  Future<AtlasVaultRecoveryExportResult> prepareExistingRecoveryExport(
    String recoveryKeyText,
  ) async {
    calls.add('prepare-existing');
    return const AtlasVaultRecoveryExportResult(
      disposition: AtlasVaultRecoveryExportDisposition.exportReady,
      encryptedRecordCount: 3,
      recoveryWrapPresent: true,
    );
  }

  @override
  Future<AtlasVaultRecoveryExportResult> savePreparedExport() async {
    calls.add('save-export');
    final gate = saveGate;
    if (gate != null) {
      return gate.future;
    }
    return const AtlasVaultRecoveryExportResult(
      disposition: AtlasVaultRecoveryExportDisposition.saved,
      encryptedRecordCount: 3,
      recoveryWrapPresent: true,
    );
  }

  @override
  Future<AtlasVaultRecoveryImportResult> inspectRecoveryImport() async {
    calls.add('inspect-import');
    final inspection = importInspection;
    if (inspection != null) {
      return inspection;
    }
    return AtlasVaultRecoveryImportResult(
      disposition: importResult == null
          ? AtlasVaultRecoveryImportDisposition.unavailable
          : AtlasVaultRecoveryImportDisposition.ready,
      encryptedRecordCount: 0,
      pendingImport: false,
    );
  }

  @override
  Future<AtlasVaultRecoveryImportResult> prepareRecoveryImport() async {
    calls.add('prepare-import');
    return importPrepareGate?.future ?? importResult!;
  }

  @override
  Future<AtlasVaultRecoveryImportResult> confirmRecoveryImport(
    String recoveryKeyText,
  ) async {
    calls.add('confirm-import');
    final gate = importConfirmGate;
    if (gate != null) {
      return gate.future;
    }
    return const AtlasVaultRecoveryImportResult(
      disposition: AtlasVaultRecoveryImportDisposition.importedAndActive,
      encryptedRecordCount: 4,
      pendingImport: false,
    );
  }

  @override
  Future<AtlasVaultRecoveryImportResult> discardPendingImport() async {
    calls.add('discard-import');
    return const AtlasVaultRecoveryImportResult(
      disposition: AtlasVaultRecoveryImportDisposition.cancelled,
      encryptedRecordCount: 0,
      pendingImport: false,
    );
  }

  @override
  void discardPendingRecovery() {
    calls.add('discard-pending');
  }

  @override
  Future<void> stop() async {
    calls.add('stop');
  }
}

final class _FakeDisplayHandle implements AtlasVaultRecoveryDisplayHandle {
  _FakeDisplayHandle(this._value);

  String? _value;

  @override
  String? take() {
    final value = _value;
    _value = null;
    return value;
  }

  @override
  void destroy() {
    _value = null;
  }

  @override
  String toString() => 'AtlasVaultRecoveryDisplayHandle(<redacted>)';
}

final class _Gate<T> {
  final _completer = Completer<T>();

  Future<T> get future => _completer.future;

  void complete(T value) {
    _completer.complete(value);
  }
}
