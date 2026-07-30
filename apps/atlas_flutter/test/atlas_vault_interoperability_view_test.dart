import 'dart:async';

import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
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
    expect(coordinator.calls, <String>['inspect', 'begin-recovery']);

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
}

final class _FakeCoordinator implements AtlasVaultInteroperabilityCoordinating {
  _FakeCoordinator({
    this.availability = const AtlasVaultRecoveryExportAvailability(
      available: true,
      encryptedRecordCount: 3,
      recoveryWrapPresent: false,
    ),
    AtlasVaultRecoveryDisplayHandle? handle,
    this.confirmGate,
    this.importResult,
  }) : handle = handle ?? _FakeDisplayHandle('AVRK1-TEST');

  final AtlasVaultRecoveryExportAvailability availability;
  final AtlasVaultRecoveryDisplayHandle handle;
  final _Gate<AtlasVaultRecoveryExportResult>? confirmGate;
  final AtlasVaultRecoveryImportResult? importResult;
  final List<String> calls = <String>[];

  @override
  Future<AtlasVaultRecoveryExportAvailability> inspectRecoveryExport() async {
    calls.add('inspect');
    return availability;
  }

  @override
  Future<AtlasVaultRecoveryDisplayHandle> beginRecoverySetup() async {
    calls.add('begin-recovery');
    return handle;
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
    return const AtlasVaultRecoveryExportResult(
      disposition: AtlasVaultRecoveryExportDisposition.saved,
      encryptedRecordCount: 3,
      recoveryWrapPresent: true,
    );
  }

  @override
  Future<AtlasVaultRecoveryImportResult> inspectRecoveryImport() async {
    calls.add('inspect-import');
    return (importResult ??
        const AtlasVaultRecoveryImportResult(
          disposition: AtlasVaultRecoveryImportDisposition.unavailable,
          encryptedRecordCount: 0,
          pendingImport: false,
        ));
  }

  @override
  Future<AtlasVaultRecoveryImportResult> prepareRecoveryImport() async {
    calls.add('prepare-import');
    return importResult!;
  }

  @override
  Future<AtlasVaultRecoveryImportResult> confirmRecoveryImport(
    String recoveryKeyText,
  ) async {
    calls.add('confirm-import');
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
