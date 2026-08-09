import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:atlas/features/app_shell/atlas_cache_location.dart';
import 'package:atlas/src/atlas_vault/plaintext_migration.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/support/atlas_vault_vector_loader.dart';

const _processStageEnvironment = 'ATLAS_WINDOWS_INTEROP_RECOVERY_PROCESS_STAGE';
const _processVaultEnvironment =
    'ATLAS_WINDOWS_INTEROP_RECOVERY_PROCESS_VAULT_ID';
const _defaultProcessVaultId = 'windows_recovery_import_cross_process_test';

String? get _processStage => Platform.environment[_processStageEnvironment];

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Windows recovery-import journal is DPAPI and CAS protected', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    if (_processStage != null) {
      return;
    }
    final store = AtlasWindowsProtectedRecoveryImportJournalStore();
    final journal = _preparedJournal(1);
    await _requireAvailableTestJournal(store, journal.vaultId);
    final preparedBytes = journal.canonicalBytes();
    try {
      await store.create(preparedBytes);
      final restored = await store.read();
      expect(restored, orderedEquals(preparedBytes));
      restored?.fillRange(0, restored.length, 0);

      final localAppData = Platform.environment['LOCALAPPDATA'];
      expect(localAppData, isNotNull);
      final envelopeFile = File(
        '$localAppData${Platform.pathSeparator}UNApplications'
        '${Platform.pathSeparator}AtlasVault${Platform.pathSeparator}v1'
        '${Platform.pathSeparator}imports${Platform.pathSeparator}'
        'recovery-import.bin',
      );
      expect(await envelopeFile.exists(), isTrue);
      expect(
        envelopeFile.absolute.path.startsWith(Directory.current.absolute.path),
        isFalse,
      );
      final protectedBytes = await envelopeFile.readAsBytes();
      try {
        expect(protectedBytes.take(8), orderedEquals(ascii.encode('AVWBLB01')));
        final protectedText = utf8.decode(protectedBytes, allowMalformed: true);
        expect(
          protectedText,
          isNot(contains('atlasvault-windows-recovery-import')),
        );
        expect(protectedText, isNot(contains(journal.vaultId)));
        expect(protectedText, isNot(contains(journal.exportId)));
      } finally {
        protectedBytes.fillRange(0, protectedBytes.length, 0);
      }

      final next = journal.transitionedTo(
        AtlasVaultRecoveryImportStage.storeCreated,
      );
      final nextBytes = next.canonicalBytes();
      try {
        await store.replace(
          nextBytes,
          expectedSha256: await atlasVaultSha256Hex(preparedBytes),
        );
        await expectLater(
          store.replace(nextBytes, expectedSha256: 'f' * 64),
          throwsA(isA<AtlasVaultWindowsStorageException>()),
        );
        final afterStale = await store.read();
        expect(afterStale, orderedEquals(nextBytes));
        afterStale?.fillRange(0, afterStale.length, 0);
        await store.delete(
          expectedSha256: await atlasVaultSha256Hex(nextBytes),
        );
        expect(await store.read(), isNull);
      } finally {
        nextBytes.fillRange(0, nextBytes.length, 0);
      }
    } finally {
      preparedBytes.fillRange(0, preparedBytes.length, 0);
      await _deleteTestJournalIfPresent(store, journal.vaultId);
    }
    tester.printToConsole(
      'Windows DPAPI recovery-import journal CAS verification passed.',
    );
  });

  testWidgets('Windows resumes every real recovery-import stage', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    if (_processStage != null) {
      return;
    }
    final vector = _RecoveryVector.load();
    var scenario = 10;
    try {
      for (final interruptedStage in AtlasVaultRecoveryImportStage.values) {
        final harness = await _RecoveryHarness.create(
          vector: vector,
          scenario: scenario,
          failAfterStage: interruptedStage,
        );
        scenario += 1;
        try {
          final prepared = await harness.coordinator.prepareRecoveryImport();
          expect(
            prepared.disposition,
            AtlasVaultRecoveryImportDisposition.importPrepared,
            reason: interruptedStage.wireName,
          );
          final interrupted = await harness.coordinator.confirmRecoveryImport(
            vector.recoveryText,
          );
          expect(interrupted.pendingImport, isTrue);
          final journal = await harness.readJournal();
          expect(journal?.stage, interruptedStage);
          if (harness.runtime.isActive) {
            await harness.runtime.deactivate();
          }

          final resumed = harness.buildCoordinator(
            journalStore: harness.importJournal,
          );
          expect(
            (await resumed.prepareRecoveryImport()).disposition,
            AtlasVaultRecoveryImportDisposition.importPrepared,
          );
          final result = await resumed.confirmRecoveryImport(
            vector.recoveryText,
          );
          expect(
            result.disposition,
            AtlasVaultRecoveryImportDisposition.importedAndActive,
            reason: interruptedStage.wireName,
          );
          expect(await harness.importJournal.read(), isNull);
          expect(await harness.selected.read(), vector.vaultId);
          expect(harness.runtime.isActiveVault(vector.vaultId), isTrue);
          await resumed.stop();
        } finally {
          await harness.dispose();
        }
      }
    } finally {
      vector.destroy();
    }
    tester.printToConsole(
      'Windows recovery import resumed every protected journal stage.',
    );
  });

  testWidgets('Windows reset is pre-selection and hash bound', (tester) async {
    if (!Platform.isWindows) {
      return;
    }
    if (_processStage != null) {
      return;
    }
    final vector = _RecoveryVector.load();
    var scenario = 30;
    try {
      for (final resetStage in <AtlasVaultRecoveryImportStage>[
        AtlasVaultRecoveryImportStage.prepared,
        AtlasVaultRecoveryImportStage.storeCreated,
        AtlasVaultRecoveryImportStage.keyCreated,
      ]) {
        final harness = await _RecoveryHarness.create(
          vector: vector,
          scenario: scenario,
          failAfterStage: resetStage,
        );
        scenario += 1;
        try {
          await harness.coordinator.prepareRecoveryImport();
          final interrupted = await harness.coordinator.confirmRecoveryImport(
            vector.recoveryText,
          );
          expect(interrupted.pendingImport, isTrue);
          final blockedCalls = <String>[];
          await expectLater(
            harness.authorityAdmission.runLegacyPrivateOperation(() async {
              blockedCalls.add('legacy-write');
            }),
            throwsA(isA<AtlasVaultPlaintextAuthorityAdmissionException>()),
          );
          expect(blockedCalls, isEmpty);

          final reset = await harness.coordinator.discardPendingImport();
          expect(
            reset.disposition,
            AtlasVaultRecoveryImportDisposition.cancelled,
            reason: resetStage.wireName,
          );
          expect(await harness.importJournal.read(), isNull);
          expect(await harness.localStore.read(vector.vaultId), isNull);
          expect(
            await harness.keyStore.containsVaultKey(vector.vaultId),
            false,
          );
          expect(await harness.selected.read(), isNull);
          await harness.authorityAdmission.runLegacyPrivateOperation(() async {
            blockedCalls.add('legacy-write');
          });
          expect(blockedCalls, <String>['legacy-write']);
        } finally {
          await harness.dispose();
        }
      }

      final committed = await _RecoveryHarness.create(
        vector: vector,
        scenario: scenario,
        failAfterStage: AtlasVaultRecoveryImportStage.selectionCommitted,
      );
      try {
        await committed.coordinator.prepareRecoveryImport();
        final interrupted = await committed.coordinator.confirmRecoveryImport(
          vector.recoveryText,
        );
        expect(interrupted.pendingImport, isTrue);
        final rejected = await committed.coordinator.discardPendingImport();
        expect(
          rejected.disposition,
          AtlasVaultRecoveryImportDisposition.recoveryRequired,
        );
        expect(await committed.selected.read(), vector.vaultId);
        expect(await committed.localStore.read(vector.vaultId), isNotNull);
        expect(
          await committed.keyStore.containsVaultKey(vector.vaultId),
          isTrue,
        );
      } finally {
        await committed.dispose();
      }
    } finally {
      vector.destroy();
    }
    tester.printToConsole(
      'Windows import reset stayed hash-bound and pre-selection only.',
    );
  });

  testWidgets('Windows import admission is authoritative across processes', (
    tester,
  ) async {
    if (!Platform.isWindows) {
      return;
    }
    final stage = _processStage;
    if (stage == null) {
      return;
    }
    final vaultId =
        Platform.environment[_processVaultEnvironment] ??
        _defaultProcessVaultId;
    final scenario = _CrossProcessRecoveryScenario(vaultId);
    switch (stage) {
      case 'admission-waiter':
        await scenario.runLegacyAdmissionWaiter();
        tester.printToConsole(
          'An already-running Windows legacy process was blocked by the '
          'recovery-import journal and reopened only after reset completed.',
        );
        return;
      case 'admission-prepare':
        await scenario.prepareAdmissionFence();
        tester.printToConsole(
          'The Windows recovery-import journal was created under the shared '
          'cross-process admission lock.',
        );
        return;
      case 'admission-reset':
        await scenario.resetAdmissionFence();
        tester.printToConsole(
          'Windows import reset removed the protected journal before '
          'reopening legacy admission.',
        );
        return;
      case 'selection-waiter':
        await scenario.runSelectionWaiter();
        tester.printToConsole(
          'A concurrent Windows legacy process remained excluded through '
          'selected-vault commitment.',
        );
        return;
      case 'selection-run':
        await scenario.runSelectionFence();
        tester.printToConsole(
          'Windows import held cross-process admission continuously through '
          'selection read-back and journal deletion.',
        );
        return;
      case 'crash-holder':
        await scenario.holdAdmissionUntilProcessTermination();
        return;
      case 'crash-verify':
        await scenario.verifyAdmissionAfterProcessCrash();
        tester.printToConsole(
          'Windows released recovery-import admission after holder process '
          'termination.',
        );
        return;
      case 'cleanup':
        await scenario.cleanTestResources();
        tester.printToConsole(
          'Windows recovery-import cross-process resources were cleaned.',
        );
        return;
      default:
        fail('Unsupported fixed Windows recovery-import process stage.');
    }
  });
}

final class _CrossProcessRecoveryScenario {
  _CrossProcessRecoveryScenario(this.vaultId)
    : root = Directory(
        '${Directory.systemTemp.path}${Platform.pathSeparator}'
        'atlas_windows_import_cross_process_'
        '${vaultId.replaceAll('-', '')}',
      ) {
    location = _cacheLocation(root);
    admission = AtlasWindowsPlaintextAuthorityAdmission(
      locationProvider: () async => location,
      journalStore: migrationJournal,
      recoveryImportJournalStore: importJournal,
      selectedVaultStore: selected,
    );
  }

  final String vaultId;
  final Directory root;
  late final AtlasPersistentCacheLocation location;
  final AtlasWindowsProtectedMigrationJournalStore migrationJournal =
      AtlasWindowsProtectedMigrationJournalStore();
  final AtlasWindowsProtectedRecoveryImportJournalStore importJournal =
      AtlasWindowsProtectedRecoveryImportJournalStore();
  final AtlasWindowsSelectedVaultStore selected =
      AtlasWindowsSelectedVaultStore();
  late final AtlasWindowsPlaintextAuthorityAdmission admission;

  AtlasVaultRecoveryImportJournal get journal =>
      AtlasVaultRecoveryImportJournal.prepared(
        profile: AtlasVaultRecoveryImportProfile.windows,
        importId: '79000000-0000-4000-8000-000000000079',
        exportId: '80000000-0000-4000-8000-000000000080',
        vaultId: vaultId,
        storeId: '81000000-0000-4000-8000-000000000081',
        createdAt: '2026-08-09T04:05:06Z',
        exportSha256: '1' * 64,
        localStoreSha256: '2' * 64,
        vaultKeySha256: '3' * 64,
      );

  Future<void> runLegacyAdmissionWaiter() async {
    await _signal('legacy-ready');
    await _waitForSignal('journal-ready');
    var privateWrites = 0;
    await expectLater(
      admission.runLegacyPrivateOperation(() async {
        privateWrites += 1;
      }),
      throwsA(isA<AtlasVaultPlaintextAuthorityAdmissionException>()),
    );
    expect(privateWrites, 0);
    await _signal('legacy-blocked');

    await _waitForSignal('reset-complete');
    await admission.runLegacyPrivateOperation(() async {
      privateWrites += 1;
    });
    expect(privateWrites, 1);
    await _signal('legacy-reopened');
  }

  Future<void> prepareAdmissionFence() async {
    await _waitForSignal('legacy-ready');
    await _requireNoSelection();
    final bytes = journal.canonicalBytes();
    try {
      await admission.runRecoveryImportTransaction(() async {
        expect(await importJournal.read(), isNull);
        await importJournal.create(bytes);
        final restored = await importJournal.read();
        expect(restored, orderedEquals(bytes));
        restored?.fillRange(0, restored.length, 0);
      });
    } finally {
      bytes.fillRange(0, bytes.length, 0);
    }
    await _signal('journal-ready');
    await _waitForSignal('legacy-blocked');
  }

  Future<void> resetAdmissionFence() async {
    await _waitForSignal('legacy-blocked');
    await admission.runRecoveryImportTransaction(() async {
      await _deleteExpectedJournal();
      expect(await importJournal.read(), isNull);
      await _requireNoSelection();
    });
    await _signal('reset-complete');
    await _waitForSignal('legacy-reopened');
  }

  Future<void> runSelectionWaiter() async {
    await _signal('selection-waiter-ready');
    await _waitForSignal('selection-transaction-held');
    var privateWrites = 0;
    await _signal('selection-write-attempted');
    await expectLater(
      admission.runLegacyPrivateOperation(() async {
        privateWrites += 1;
      }),
      throwsA(isA<AtlasVaultPlaintextAuthorityAdmissionException>()),
    );
    expect(privateWrites, 0);
    expect(await selected.read(), vaultId);
    expect(await importJournal.read(), isNull);
    await _signal('selection-write-blocked');
  }

  Future<void> runSelectionFence() async {
    await _waitForSignal('selection-waiter-ready');
    await _requireNoSelection();
    final bytes = journal.canonicalBytes();
    try {
      await admission.runRecoveryImportTransaction(() async {
        expect(await importJournal.read(), isNull);
        await importJournal.create(bytes);
        await _signal('selection-transaction-held');
        await _waitForSignal('selection-write-attempted');
        await selected.create(vaultId);
        expect(await selected.read(), vaultId);
        await _deleteExpectedJournal();
        expect(await importJournal.read(), isNull);
      });
    } finally {
      bytes.fillRange(0, bytes.length, 0);
    }
    await _waitForSignal('selection-write-blocked');
  }

  Future<void> holdAdmissionUntilProcessTermination() async {
    await admission.runRecoveryImportTransaction(() async {
      await _signal('crash-lock-held', value: '$pid');
      await Completer<void>().future;
    });
  }

  Future<void> verifyAdmissionAfterProcessCrash() async {
    await _waitForSignal('crash-lock-held');
    await _signal('crash-waiter-ready');
    var acquired = false;
    await admission.runRecoveryImportTransaction(() async {
      acquired = true;
      await _signal('crash-lock-reacquired');
    });
    expect(acquired, isTrue);
  }

  Future<void> cleanTestResources() async {
    await root.create(recursive: true);
    final selectedVault = await selected.read();
    if (selectedVault == vaultId) {
      await selected.clear(vaultId);
    } else if (selectedVault != null) {
      throw StateError('Unrelated selected vault is present.');
    }
    await _deleteExpectedJournal(allowAbsent: true);
    if (await root.exists()) {
      await root.delete(recursive: true);
    }
  }

  Future<void> _deleteExpectedJournal({bool allowAbsent = false}) async {
    final bytes = await importJournal.read();
    if (bytes == null) {
      if (allowAbsent) {
        return;
      }
      fail('Expected Windows recovery-import journal is absent.');
    }
    try {
      final restored = AtlasVaultRecoveryImportJournal.decodeBytes(
        bytes,
        profile: AtlasVaultRecoveryImportProfile.windows,
      );
      if (restored.vaultId != vaultId) {
        throw StateError('Unrelated Windows recovery import is pending.');
      }
      await importJournal.delete(
        expectedSha256: await atlasVaultSha256Hex(bytes),
      );
    } finally {
      bytes.fillRange(0, bytes.length, 0);
    }
  }

  Future<void> _requireNoSelection() async {
    final value = await selected.read();
    if (value != null) {
      throw StateError('Unexpected selected vault in test storage.');
    }
  }

  File _signalFile(String name) =>
      File('${root.path}${Platform.pathSeparator}$name.ready');

  Future<void> _signal(String name, {String value = 'ready'}) async {
    await root.create(recursive: true);
    await _signalFile(name).writeAsString('$value\n', flush: true);
  }

  Future<void> _waitForSignal(String name) async {
    await root.create(recursive: true);
    final file = _signalFile(name);
    if (await file.exists()) {
      return;
    }
    final ready = Completer<void>();
    late final StreamSubscription<FileSystemEvent> subscription;
    subscription = root.watch().listen((_) {
      if (!ready.isCompleted && file.existsSync()) {
        ready.complete();
      }
    });
    if (await file.exists() && !ready.isCompleted) {
      ready.complete();
    }
    try {
      await ready.future;
    } finally {
      await subscription.cancel();
    }
  }
}

final class _RecoveryHarness {
  _RecoveryHarness._({
    required this.vector,
    required this.root,
    required this.location,
    required this.keyStore,
    required this.localStore,
    required this.selected,
    required this.migrationJournal,
    required this.importJournal,
    required this.runtime,
    required this.authorityAdmission,
    required this.transport,
    required this.importId,
    required this.storeId,
    required this.coordinator,
  });

  final _RecoveryVector vector;
  final Directory root;
  final AtlasPersistentCacheLocation location;
  final AtlasWindowsVaultSecureKeyStore keyStore;
  final AtlasWindowsVaultLocalStoreIO localStore;
  final AtlasWindowsSelectedVaultStore selected;
  final AtlasWindowsProtectedMigrationJournalStore migrationJournal;
  final AtlasWindowsProtectedRecoveryImportJournalStore importJournal;
  final AtlasVaultPrivateStateRuntime runtime;
  final AtlasWindowsPlaintextAuthorityAdmission authorityAdmission;
  final _PickedDocumentTransport transport;
  final String importId;
  final String storeId;
  final AtlasVaultInteroperabilityCoordinator coordinator;

  static Future<_RecoveryHarness> create({
    required _RecoveryVector vector,
    required int scenario,
    required AtlasVaultRecoveryImportStage failAfterStage,
  }) async {
    final root = await Directory.systemTemp.createTemp(
      'atlas_windows_import_recovery_$scenario',
    );
    final location = _cacheLocation(root);
    final keyStore = AtlasWindowsVaultSecureKeyStore();
    final localStore = AtlasWindowsVaultLocalStoreIO();
    final selected = AtlasWindowsSelectedVaultStore();
    final migrationJournal = AtlasWindowsProtectedMigrationJournalStore();
    final importJournal = AtlasWindowsProtectedRecoveryImportJournalStore();
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
    );
    final authorityAdmission = AtlasWindowsPlaintextAuthorityAdmission(
      locationProvider: () async => location,
      journalStore: migrationJournal,
      recoveryImportJournalStore: importJournal,
      selectedVaultStore: selected,
    );
    final transport = _PickedDocumentTransport(vector.exportBytes);
    final importId = _uuid(73, scenario);
    final storeId = _uuid(74, scenario);
    final interruptingStore = _InterruptingRecoveryJournalStore(
      delegate: importJournal,
      failAfterStage: failAfterStage,
    );
    late final _RecoveryHarness harness;
    final coordinator = _buildCoordinator(
      vector: vector,
      runtime: runtime,
      selected: selected,
      migrationJournal: migrationJournal,
      importJournal: interruptingStore,
      keyStore: keyStore,
      localStore: localStore,
      location: location,
      authorityAdmission: authorityAdmission,
      transport: transport,
      importId: importId,
      storeId: storeId,
    );
    harness = _RecoveryHarness._(
      vector: vector,
      root: root,
      location: location,
      keyStore: keyStore,
      localStore: localStore,
      selected: selected,
      migrationJournal: migrationJournal,
      importJournal: importJournal,
      runtime: runtime,
      authorityAdmission: authorityAdmission,
      transport: transport,
      importId: importId,
      storeId: storeId,
      coordinator: coordinator,
    );
    await harness._cleanTestResources();
    return harness;
  }

  AtlasVaultInteroperabilityCoordinator buildCoordinator({
    required AtlasVaultProtectedRecoveryImportJournalStore journalStore,
  }) {
    transport.reset();
    return _buildCoordinator(
      vector: vector,
      runtime: runtime,
      selected: selected,
      migrationJournal: migrationJournal,
      importJournal: journalStore,
      keyStore: keyStore,
      localStore: localStore,
      location: location,
      authorityAdmission: authorityAdmission,
      transport: transport,
      importId: importId,
      storeId: storeId,
    );
  }

  Future<AtlasVaultRecoveryImportJournal?> readJournal() async {
    final bytes = await importJournal.read();
    if (bytes == null) {
      return null;
    }
    try {
      return AtlasVaultRecoveryImportJournal.decodeBytes(
        bytes,
        profile: AtlasVaultRecoveryImportProfile.windows,
      );
    } finally {
      bytes.fillRange(0, bytes.length, 0);
    }
  }

  Future<void> _cleanTestResources() async {
    if (runtime.isActive) {
      await runtime.deactivate();
    }
    final selectedVault = await selected.read();
    if (selectedVault == vector.vaultId) {
      await selected.clear(vector.vaultId);
    } else if (selectedVault != null) {
      throw StateError('Unexpected selected vault in test storage.');
    }
    if (await localStore.read(vector.vaultId) != null) {
      await localStore.delete(vector.vaultId);
    }
    if (await keyStore.containsVaultKey(vector.vaultId)) {
      await keyStore.deleteVaultKey(vector.vaultId);
    }
    await _deleteTestJournalIfPresent(importJournal, vector.vaultId);
  }

  Future<void> dispose() async {
    await coordinator.stop();
    await _cleanTestResources();
    if (await root.exists()) {
      await root.delete(recursive: true);
    }
  }
}

AtlasVaultInteroperabilityCoordinator _buildCoordinator({
  required _RecoveryVector vector,
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasWindowsSelectedVaultStore selected,
  required AtlasWindowsProtectedMigrationJournalStore migrationJournal,
  required AtlasVaultProtectedRecoveryImportJournalStore importJournal,
  required AtlasWindowsVaultSecureKeyStore keyStore,
  required AtlasWindowsVaultLocalStoreIO localStore,
  required AtlasPersistentCacheLocation location,
  required AtlasWindowsPlaintextAuthorityAdmission authorityAdmission,
  required _PickedDocumentTransport transport,
  required String importId,
  required String storeId,
}) {
  return AtlasVaultInteroperabilityCoordinator(
    runtime: runtime,
    selectedVaultStore: selected,
    migrationJournalStore: migrationJournal,
    recoveryImportPending: () => _journalExists(importJournal),
    documentTransport: transport,
    recoveryImportJournalStore: importJournal,
    secureKeyStore: keyStore,
    localStoreIO: localStore,
    inMemorySource: const _EmptyPlaintextSource(),
    compatibilitySource: _EmptyCompatibilitySource(),
    cacheSource: AtlasWindowsDesktopCacheMigrationSource(location),
    importTransactionAdmission: authorityAdmission,
    recoveryImportProfile: AtlasVaultRecoveryImportProfile.windows,
    now: () => DateTime.utc(2026, 8, 9, 4, 5, 6),
    importIdProvider: () => importId,
    importStoreIdProvider: () => storeId,
  );
}

final class _InterruptingRecoveryJournalStore
    implements AtlasVaultProtectedRecoveryImportJournalStore {
  _InterruptingRecoveryJournalStore({
    required this.delegate,
    required this.failAfterStage,
  });

  final AtlasVaultProtectedRecoveryImportJournalStore delegate;
  final AtlasVaultRecoveryImportStage failAfterStage;
  bool _failed = false;

  @override
  Future<Uint8List?> read() => delegate.read();

  @override
  Future<void> create(Uint8List canonicalBytes) async {
    await delegate.create(canonicalBytes);
    _interruptIfNeeded(canonicalBytes);
  }

  @override
  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  }) async {
    await delegate.replace(canonicalBytes, expectedSha256: expectedSha256);
    _interruptIfNeeded(canonicalBytes);
  }

  @override
  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  }) =>
      delegate.delete(expectedSha256: expectedSha256, allowAbsent: allowAbsent);

  void _interruptIfNeeded(Uint8List canonicalBytes) {
    if (_failed) {
      return;
    }
    final journal = AtlasVaultRecoveryImportJournal.decodeBytes(
      canonicalBytes,
      profile: AtlasVaultRecoveryImportProfile.windows,
    );
    if (journal.stage == failAfterStage) {
      _failed = true;
      throw StateError('Injected Windows recovery-import interruption.');
    }
  }
}

final class _RecoveryVector {
  _RecoveryVector({
    required this.vaultId,
    required this.recoveryText,
    required this.vaultKey,
    required this.exportBytes,
  });

  final String vaultId;
  final String recoveryText;
  final Uint8List vaultKey;
  final Uint8List exportBytes;

  factory _RecoveryVector.load() {
    final root = loadAtlasVaultVector(
      'atlasvault_windows_interop_vectors_v1.json',
    );
    final value = atlasVaultObject(root['apple_to_windows']);
    return _RecoveryVector(
      vaultId: value['vault_id']! as String,
      recoveryText: value['test_only_recovery_key_text']! as String,
      vaultKey: Uint8List.fromList(
        base64Decode(value['test_only_vault_key_b64']! as String),
      ),
      exportBytes: Uint8List.fromList(
        base64Decode(value['canonical_encrypted_export_b64']! as String),
      ),
    );
  }

  void destroy() {
    vaultKey.fillRange(0, vaultKey.length, 0);
    exportBytes.fillRange(0, exportBytes.length, 0);
  }
}

final class _PickedDocumentTransport
    implements AtlasVaultEncryptedDocumentTransport {
  _PickedDocumentTransport(this._bytes);

  final Uint8List _bytes;

  void reset() {}

  @override
  Future<Uint8List?> pickEncryptedExport() async => Uint8List.fromList(_bytes);

  @override
  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes) async =>
      false;
}

final class _EmptyCompatibilitySource
    implements AtlasVaultCompatibilityPrivateSource {
  @override
  Uri get authorityBaseURL => Uri.parse('https://example.invalid/');

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async => _emptyPlaintextState();

  @override
  Future<bool> deleteSavedSearch(String name) async => false;

  @override
  Future<bool> deleteTrackerRecord(String recordId) async => false;
}

final class _EmptyPlaintextSource implements AtlasVaultPlaintextStateSource {
  const _EmptyPlaintextSource();

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async =>
      _emptyPlaintextState();
}

AtlasVaultPlaintextPrivateState _emptyPlaintextState() {
  return AtlasVaultPlaintextPrivateState(
    savedSearches: const <AtlasSavedSearch>[],
    trackerRecords: const <AtlasApplicationRecord>[],
  );
}

AtlasPersistentCacheLocation _cacheLocation(Directory root) {
  return AtlasPersistentCacheLocation(
    cacheFile: File(
      '${root.path}${Platform.pathSeparator}durable'
      '${Platform.pathSeparator}$atlasLocalCacheFileName',
    ),
    legacyFile: File(
      '${root.path}${Platform.pathSeparator}legacy'
      '${Platform.pathSeparator}$atlasLocalCacheFileName',
    ),
    legacyImportRetiredFile: File(
      '${root.path}${Platform.pathSeparator}durable'
      '${Platform.pathSeparator}$atlasLegacyImportRetiredFileName',
    ),
  );
}

AtlasVaultRecoveryImportJournal _preparedJournal(int scenario) {
  return AtlasVaultRecoveryImportJournal.prepared(
    profile: AtlasVaultRecoveryImportProfile.windows,
    importId: _uuid(75, scenario),
    exportId: _uuid(76, scenario),
    vaultId: 'windows_recovery_import_test_$scenario',
    storeId: _uuid(77, scenario),
    createdAt: '2026-08-09T04:05:06Z',
    exportSha256: '1' * 64,
    localStoreSha256: '2' * 64,
    vaultKeySha256: '3' * 64,
  );
}

String _uuid(int prefix, int scenario) {
  return '${prefix.toString().padLeft(2, '0')}000000-0000-4000-8000-'
      '${scenario.toString().padLeft(12, '0')}';
}

Future<bool> _journalExists(
  AtlasVaultProtectedRecoveryImportJournalStore store,
) async {
  final bytes = await store.read();
  try {
    return bytes != null;
  } finally {
    bytes?.fillRange(0, bytes.length, 0);
  }
}

Future<void> _requireAvailableTestJournal(
  AtlasWindowsProtectedRecoveryImportJournalStore store,
  String expectedVaultId,
) async {
  final bytes = await store.read();
  if (bytes == null) {
    return;
  }
  try {
    final journal = AtlasVaultRecoveryImportJournal.decodeBytes(
      bytes,
      profile: AtlasVaultRecoveryImportProfile.windows,
    );
    if (journal.vaultId != expectedVaultId) {
      throw StateError('Unrelated Windows recovery import is pending.');
    }
    await store.delete(expectedSha256: await atlasVaultSha256Hex(bytes));
  } finally {
    bytes.fillRange(0, bytes.length, 0);
  }
}

Future<void> _deleteTestJournalIfPresent(
  AtlasWindowsProtectedRecoveryImportJournalStore store,
  String expectedVaultId,
) async {
  final bytes = await store.read();
  if (bytes == null) {
    return;
  }
  try {
    final journal = AtlasVaultRecoveryImportJournal.decodeBytes(
      bytes,
      profile: AtlasVaultRecoveryImportProfile.windows,
    );
    if (journal.vaultId != expectedVaultId) {
      throw StateError('Unrelated Windows recovery import is pending.');
    }
    await store.delete(expectedSha256: await atlasVaultSha256Hex(bytes));
  } finally {
    bytes.fillRange(0, bytes.length, 0);
  }
}
