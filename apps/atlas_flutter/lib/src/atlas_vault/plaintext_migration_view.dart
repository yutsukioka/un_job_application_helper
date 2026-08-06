import 'package:flutter/material.dart';

import 'plaintext_migration.dart';

enum AtlasVaultPlaintextMigrationPresentationStatus {
  hidden,
  legacyAvailable,
  inventorying,
  inventoryReady,
  preparing,
  prepared,
  discarding,
  restoringLegacy,
  finalizing,
  resumeRequired,
  completionPending,
  activationRequired,
  activating,
  active,
  failed,
  conflictDetected,
  recoveryRequired,
  unsupported,
}

enum AtlasVaultPlaintextMigrationPresentationPlatform { android, windows }

final class AtlasVaultPlaintextMigrationContext {
  const AtlasVaultPlaintextMigrationContext({
    required this.owner,
    this.platform = AtlasVaultPlaintextMigrationPresentationPlatform.android,
  });

  final AtlasVaultPlaintextMigrationPresentationOwner owner;
  final AtlasVaultPlaintextMigrationPresentationPlatform platform;

  @override
  String toString() => 'AtlasVaultPlaintextMigrationContext(<redacted>)';
}

final class AtlasVaultPlaintextMigrationPresentationOwner
    extends ChangeNotifier {
  AtlasVaultPlaintextMigrationPresentationOwner({
    required AtlasVaultPlaintextMigrationCoordinating coordinator,
    AtlasVaultLegacyPrivateStateRestoring? legacyPrivateStateRestorer,
  }) : // Keep the public constructor label descriptive.
       // ignore: prefer_initializing_formals
       _coordinator = coordinator,
       _legacyPrivateStateRestorer =
           legacyPrivateStateRestorer ??
           const _UnavailableLegacyPrivateStateRestorer();

  final AtlasVaultPlaintextMigrationCoordinating _coordinator;
  final AtlasVaultLegacyPrivateStateRestoring _legacyPrivateStateRestorer;

  AtlasVaultPlaintextMigrationPresentationStatus status =
      AtlasVaultPlaintextMigrationPresentationStatus.hidden;
  int savedSearchCount = 0;
  int trackerRecordCount = 0;
  bool localCachePrivatePresent = false;
  bool compatibilityPrivatePresent = false;
  AtlasVaultPlaintextMigrationStage? stage;

  Future<void>? _operation;
  String? _operationKind;
  int _revision = 0;
  bool _disposed = false;
  bool _preparedRollbackAvailable = false;

  bool get blocksLegacyPrivateAuthority {
    return switch (status) {
      AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable ||
      AtlasVaultPlaintextMigrationPresentationStatus.inventorying ||
      AtlasVaultPlaintextMigrationPresentationStatus.inventoryReady ||
      AtlasVaultPlaintextMigrationPresentationStatus.failed ||
      AtlasVaultPlaintextMigrationPresentationStatus.conflictDetected => false,
      _ => true,
    };
  }

  bool get blocksPersistedCacheWrites {
    return switch (status) {
      AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable ||
      AtlasVaultPlaintextMigrationPresentationStatus.inventorying ||
      AtlasVaultPlaintextMigrationPresentationStatus.inventoryReady ||
      AtlasVaultPlaintextMigrationPresentationStatus.activationRequired ||
      AtlasVaultPlaintextMigrationPresentationStatus.activating ||
      AtlasVaultPlaintextMigrationPresentationStatus.active ||
      AtlasVaultPlaintextMigrationPresentationStatus.failed ||
      AtlasVaultPlaintextMigrationPresentationStatus.conflictDetected => false,
      _ => true,
    };
  }

  bool get isBusy {
    return switch (status) {
      AtlasVaultPlaintextMigrationPresentationStatus.inventorying ||
      AtlasVaultPlaintextMigrationPresentationStatus.preparing ||
      AtlasVaultPlaintextMigrationPresentationStatus.discarding ||
      AtlasVaultPlaintextMigrationPresentationStatus.restoringLegacy ||
      AtlasVaultPlaintextMigrationPresentationStatus.finalizing ||
      AtlasVaultPlaintextMigrationPresentationStatus.activating => true,
      _ => false,
    };
  }

  bool get canDiscardPreparedMigration {
    return status == AtlasVaultPlaintextMigrationPresentationStatus.prepared ||
        (status ==
                AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired &&
            _preparedRollbackAvailable);
  }

  Future<void> bootstrapAuthority() {
    return _run('bootstrap', (revision) async {
      try {
        final authority = await _coordinator.inspectAuthority();
        if (!_isCurrent(revision)) {
          return;
        }
        var rollbackAvailable = false;
        if (authority == AtlasVaultPlaintextAuthorityState.migrationPending) {
          rollbackAvailable = await _coordinator
              .inspectPreparedRollbackAvailability();
          if (!_isCurrent(revision)) {
            return;
          }
        }
        _clearSummary();
        _preparedRollbackAvailable = rollbackAvailable;
        status = switch (authority) {
          AtlasVaultPlaintextAuthorityState.unresolved =>
            AtlasVaultPlaintextMigrationPresentationStatus.hidden,
          AtlasVaultPlaintextAuthorityState.legacy =>
            AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
          AtlasVaultPlaintextAuthorityState.migrationPending =>
            AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired,
          AtlasVaultPlaintextAuthorityState.encryptedSelectedInactive =>
            AtlasVaultPlaintextMigrationPresentationStatus.activationRequired,
          AtlasVaultPlaintextAuthorityState.encryptedActive =>
            AtlasVaultPlaintextMigrationPresentationStatus.active,
          AtlasVaultPlaintextAuthorityState.recoveryRequired =>
            AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired,
          AtlasVaultPlaintextAuthorityState.unsupported =>
            AtlasVaultPlaintextMigrationPresentationStatus.unsupported,
        };
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        _clearSummary();
        status =
            AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired;
      }
      if (!_isCurrent(revision)) {
        return;
      }
      if (_isCurrent(revision)) {
        notifyListeners();
      }
    });
  }

  Future<void> reviewInventory() {
    return _run('inventory', (revision) async {
      status = AtlasVaultPlaintextMigrationPresentationStatus.inventorying;
      if (_isCurrent(revision)) {
        notifyListeners();
      }
      try {
        final summary = await _coordinator.inventory();
        if (!_isCurrent(revision)) {
          return;
        }
        _installSummary(summary);
        status = AtlasVaultPlaintextMigrationPresentationStatus.inventoryReady;
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        _clearSummary();
        status =
            AtlasVaultPlaintextMigrationPresentationStatus.conflictDetected;
      }
      if (_isCurrent(revision)) {
        notifyListeners();
      }
    });
  }

  Future<void> prepareEncryptedMigration() {
    return _run('prepare', (revision) async {
      if (status !=
          AtlasVaultPlaintextMigrationPresentationStatus.inventoryReady) {
        return;
      }
      status = AtlasVaultPlaintextMigrationPresentationStatus.preparing;
      if (_isCurrent(revision)) {
        notifyListeners();
      }
      try {
        final summary = await _coordinator.prepare();
        if (!_isCurrent(revision)) {
          return;
        }
        _installSummary(summary);
        status =
            summary.stage == AtlasVaultPlaintextMigrationStage.encryptedVerified
            ? AtlasVaultPlaintextMigrationPresentationStatus.prepared
            : AtlasVaultPlaintextMigrationPresentationStatus.failed;
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        status = await _statusAfterMigrationFailure(revision);
      }
      if (_isCurrent(revision)) {
        notifyListeners();
      }
    });
  }

  Future<void> discardPreparedMigration() {
    return _run('discard', (revision) async {
      if (!canDiscardPreparedMigration) {
        return;
      }
      _preparedRollbackAvailable = false;
      status = AtlasVaultPlaintextMigrationPresentationStatus.discarding;
      if (_isCurrent(revision)) {
        notifyListeners();
      }
      var rollbackCompleted = false;
      try {
        await _coordinator.discardPrepared();
        rollbackCompleted = true;
        if (!_isCurrent(revision)) {
          return;
        }
        final authority = await _coordinator.inspectAuthority();
        if (!_isCurrent(revision)) {
          return;
        }
        if (authority != AtlasVaultPlaintextAuthorityState.legacy &&
            authority != AtlasVaultPlaintextAuthorityState.migrationPending) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        status = await _restoreLegacyPrivateState(revision);
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        status = rollbackCompleted
            ? AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired
            : await _statusAfterMigrationFailure(
                revision,
                legacyStatus: AtlasVaultPlaintextMigrationPresentationStatus
                    .recoveryRequired,
              );
      }
      if (_isCurrent(revision)) {
        notifyListeners();
      }
    });
  }

  Future<void> finalizeMigration() {
    return _run('finalize', (revision) async {
      if (status != AtlasVaultPlaintextMigrationPresentationStatus.prepared) {
        return;
      }
      _preparedRollbackAvailable = false;
      status = AtlasVaultPlaintextMigrationPresentationStatus.finalizing;
      if (_isCurrent(revision)) {
        notifyListeners();
      }
      try {
        final summary = await _coordinator.finalizeAndActivate();
        if (!_isCurrent(revision)) {
          return;
        }
        _installSummary(summary);
        status = summary.stage == null
            ? AtlasVaultPlaintextMigrationPresentationStatus.active
            : AtlasVaultPlaintextMigrationPresentationStatus.completionPending;
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        status = await _statusAfterMigrationFailure(revision);
      }
      if (_isCurrent(revision)) {
        notifyListeners();
      }
    });
  }

  Future<void> resumeMigration() {
    return _run('resume', (revision) async {
      _preparedRollbackAvailable = false;
      status = AtlasVaultPlaintextMigrationPresentationStatus.finalizing;
      if (_isCurrent(revision)) {
        notifyListeners();
      }
      try {
        final summary = await _coordinator.resume();
        if (!_isCurrent(revision)) {
          return;
        }
        _installSummary(summary);
        if (summary.stage == null) {
          status = await _statusAfterJournalCompletion(revision);
        } else {
          status = switch (summary.stage) {
            AtlasVaultPlaintextMigrationStage.prepared ||
            AtlasVaultPlaintextMigrationStage.encryptedVerified =>
              AtlasVaultPlaintextMigrationPresentationStatus.prepared,
            AtlasVaultPlaintextMigrationStage.completionPending =>
              AtlasVaultPlaintextMigrationPresentationStatus.completionPending,
            _ => AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired,
          };
        }
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        status = await _statusAfterMigrationFailure(revision);
      }
      if (_isCurrent(revision)) {
        notifyListeners();
      }
    });
  }

  Future<void> activateEncryptedPrivateData() {
    return _run('activate', (revision) async {
      if (status !=
          AtlasVaultPlaintextMigrationPresentationStatus.activationRequired) {
        return;
      }
      _preparedRollbackAvailable = false;
      status = AtlasVaultPlaintextMigrationPresentationStatus.activating;
      if (_isCurrent(revision)) {
        notifyListeners();
      }
      try {
        final summary = await _coordinator.activateSelected();
        if (!_isCurrent(revision)) {
          return;
        }
        _installSummary(summary);
        status = summary.stage == null
            ? AtlasVaultPlaintextMigrationPresentationStatus.active
            : AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired;
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        status =
            AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired;
      }
      if (_isCurrent(revision)) {
        notifyListeners();
      }
    });
  }

  void hide() {
    _revision += 1;
    _clearSummary();
    status = AtlasVaultPlaintextMigrationPresentationStatus.hidden;
    if (!_disposed) {
      notifyListeners();
    }
  }

  Future<void> _run(String kind, Future<void> Function(int revision) body) {
    final current = _operation;
    if (current != null) {
      if (_operationKind == kind) {
        return current;
      }
      return Future<void>.error(const AtlasVaultPlaintextMigrationException());
    }
    final revision = ++_revision;
    late final Future<void> operation;
    operation = body(revision).whenComplete(() {
      if (identical(_operation, operation)) {
        _operation = null;
        _operationKind = null;
      }
    });
    _operation = operation;
    _operationKind = kind;
    return operation;
  }

  bool _isCurrent(int revision) => !_disposed && revision == _revision;

  Future<AtlasVaultPlaintextMigrationPresentationStatus>
  _statusAfterMigrationFailure(
    int revision, {
    AtlasVaultPlaintextMigrationPresentationStatus legacyStatus =
        AtlasVaultPlaintextMigrationPresentationStatus.failed,
  }) async {
    try {
      final authority = await _coordinator.inspectAuthority();
      if (!_isCurrent(revision)) {
        return AtlasVaultPlaintextMigrationPresentationStatus.hidden;
      }
      if (authority == AtlasVaultPlaintextAuthorityState.migrationPending) {
        final rollbackAvailable = await _coordinator
            .inspectPreparedRollbackAvailability();
        if (!_isCurrent(revision)) {
          return AtlasVaultPlaintextMigrationPresentationStatus.hidden;
        }
        _preparedRollbackAvailable = rollbackAvailable;
        return AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired;
      }
      _preparedRollbackAvailable = false;
      return switch (authority) {
        AtlasVaultPlaintextAuthorityState.legacy => legacyStatus,
        AtlasVaultPlaintextAuthorityState.migrationPending =>
          AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired,
        AtlasVaultPlaintextAuthorityState.encryptedSelectedInactive =>
          AtlasVaultPlaintextMigrationPresentationStatus.activationRequired,
        AtlasVaultPlaintextAuthorityState.encryptedActive =>
          AtlasVaultPlaintextMigrationPresentationStatus.active,
        AtlasVaultPlaintextAuthorityState.unsupported =>
          AtlasVaultPlaintextMigrationPresentationStatus.unsupported,
        _ => AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired,
      };
    } catch (_) {
      if (!_isCurrent(revision)) {
        return AtlasVaultPlaintextMigrationPresentationStatus.hidden;
      }
      _preparedRollbackAvailable = false;
      return AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired;
    }
  }

  Future<AtlasVaultPlaintextMigrationPresentationStatus>
  _statusAfterJournalCompletion(int revision) async {
    try {
      final authority = await _coordinator.inspectAuthority();
      if (!_isCurrent(revision)) {
        return AtlasVaultPlaintextMigrationPresentationStatus.hidden;
      }
      _preparedRollbackAvailable = false;
      if (authority == AtlasVaultPlaintextAuthorityState.legacy ||
          authority == AtlasVaultPlaintextAuthorityState.migrationPending) {
        return _restoreLegacyPrivateState(revision);
      }
      return switch (authority) {
        AtlasVaultPlaintextAuthorityState.legacy =>
          AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable,
        AtlasVaultPlaintextAuthorityState.migrationPending =>
          AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired,
        AtlasVaultPlaintextAuthorityState.encryptedSelectedInactive =>
          AtlasVaultPlaintextMigrationPresentationStatus.activationRequired,
        AtlasVaultPlaintextAuthorityState.encryptedActive =>
          AtlasVaultPlaintextMigrationPresentationStatus.active,
        AtlasVaultPlaintextAuthorityState.unsupported =>
          AtlasVaultPlaintextMigrationPresentationStatus.unsupported,
        _ => AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired,
      };
    } catch (_) {
      if (!_isCurrent(revision)) {
        return AtlasVaultPlaintextMigrationPresentationStatus.hidden;
      }
      _preparedRollbackAvailable = false;
      return AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired;
    }
  }

  Future<AtlasVaultPlaintextMigrationPresentationStatus>
  _restoreLegacyPrivateState(int revision) async {
    if (!_isCurrent(revision)) {
      return AtlasVaultPlaintextMigrationPresentationStatus.hidden;
    }
    _clearSummary();
    status = AtlasVaultPlaintextMigrationPresentationStatus.restoringLegacy;
    notifyListeners();
    await _coordinator.restoreReviewedLegacyPrivateState(
      _legacyPrivateStateRestorer,
    );
    if (!_isCurrent(revision)) {
      return AtlasVaultPlaintextMigrationPresentationStatus.hidden;
    }
    _clearSummary();
    return AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable;
  }

  void _installSummary(AtlasVaultPlaintextMigrationSummary summary) {
    savedSearchCount = summary.savedSearchCount;
    trackerRecordCount = summary.trackerRecordCount;
    localCachePrivatePresent = summary.localCachePrivatePresent;
    compatibilityPrivatePresent = summary.compatibilityPrivatePresent;
    stage = summary.stage;
  }

  void _clearSummary() {
    savedSearchCount = 0;
    trackerRecordCount = 0;
    localCachePrivatePresent = false;
    compatibilityPrivatePresent = false;
    stage = null;
    _preparedRollbackAvailable = false;
  }

  @override
  void dispose() {
    _disposed = true;
    _revision += 1;
    _clearSummary();
    super.dispose();
  }

  @override
  String toString() =>
      'AtlasVaultPlaintextMigrationPresentationOwner(<redacted>)';
}

final class AtlasVaultPlaintextMigrationPanel extends StatelessWidget {
  const AtlasVaultPlaintextMigrationPanel({
    super.key,
    required this.owner,
    this.platform = AtlasVaultPlaintextMigrationPresentationPlatform.android,
  });

  final AtlasVaultPlaintextMigrationPresentationOwner owner;
  final AtlasVaultPlaintextMigrationPresentationPlatform platform;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: owner,
      builder: (context, child) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'Private Data Migration',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            ..._statusContent(context),
          ],
        );
      },
    );
  }

  List<Widget> _statusContent(BuildContext context) {
    switch (owner.status) {
      case AtlasVaultPlaintextMigrationPresentationStatus.hidden:
      case AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable:
      case AtlasVaultPlaintextMigrationPresentationStatus.failed:
      case AtlasVaultPlaintextMigrationPresentationStatus.conflictDetected:
        return <Widget>[
          if (owner.status ==
              AtlasVaultPlaintextMigrationPresentationStatus.conflictDetected)
            const Text('Private data authorities could not be reconciled.'),
          FilledButton(
            onPressed: owner.isBusy ? null : owner.reviewInventory,
            child: const Text('Review Private Data Migration'),
          ),
        ];
      case AtlasVaultPlaintextMigrationPresentationStatus.inventorying:
        return const <Widget>[Text('Reviewing private data counts...')];
      case AtlasVaultPlaintextMigrationPresentationStatus.inventoryReady:
        return <Widget>[
          ..._counts(),
          FilledButton(
            onPressed: () => _confirmPreparation(context),
            child: const Text('Prepare Encrypted Migration'),
          ),
        ];
      case AtlasVaultPlaintextMigrationPresentationStatus.preparing:
        return const <Widget>[Text('Preparing encrypted copy...')];
      case AtlasVaultPlaintextMigrationPresentationStatus.discarding:
        return const <Widget>[Text('Discarding prepared migration...')];
      case AtlasVaultPlaintextMigrationPresentationStatus.restoringLegacy:
        return const <Widget>[Text('Restoring legacy private data...')];
      case AtlasVaultPlaintextMigrationPresentationStatus.prepared:
        return <Widget>[
          ..._counts(),
          const Text('Encrypted copy verified'),
          OutlinedButton(
            onPressed: owner.discardPreparedMigration,
            child: const Text('Discard Prepared Migration'),
          ),
          FilledButton(
            onPressed: () => _confirmFinalization(context),
            child: const Text('Remove Plaintext & Activate AtlasVault'),
          ),
        ];
      case AtlasVaultPlaintextMigrationPresentationStatus.finalizing:
        return const <Widget>[
          Text('Removing plaintext and activating encrypted private data...'),
        ];
      case AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired:
      case AtlasVaultPlaintextMigrationPresentationStatus.completionPending:
        return <Widget>[
          const Text('Migration requires explicit resumption.'),
          if (owner.status ==
                  AtlasVaultPlaintextMigrationPresentationStatus
                      .resumeRequired &&
              owner.canDiscardPreparedMigration)
            OutlinedButton(
              onPressed: owner.discardPreparedMigration,
              child: const Text('Discard Prepared Migration'),
            ),
          FilledButton(
            onPressed: owner.resumeMigration,
            child: const Text('Resume Migration'),
          ),
        ];
      case AtlasVaultPlaintextMigrationPresentationStatus.activationRequired:
        return <Widget>[
          const Text('Encrypted private data is available on this device.'),
          FilledButton(
            onPressed: owner.activateEncryptedPrivateData,
            child: const Text('Activate Encrypted Private Data'),
          ),
        ];
      case AtlasVaultPlaintextMigrationPresentationStatus.activating:
        return const <Widget>[Text('Activating encrypted private data...')];
      case AtlasVaultPlaintextMigrationPresentationStatus.active:
        return const <Widget>[Text('Encrypted private data is active.')];
      case AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired:
        return const <Widget>[
          Text('Private data migration requires recovery.'),
        ];
      case AtlasVaultPlaintextMigrationPresentationStatus.unsupported:
        return const <Widget>[
          Text('Encrypted private data is unavailable on this device.'),
        ];
    }
  }

  List<Widget> _counts() {
    return <Widget>[
      Text(
        '${owner.savedSearchCount} '
        '${owner.savedSearchCount == 1 ? 'saved search' : 'saved searches'}',
      ),
      Text(
        '${owner.trackerRecordCount} '
        '${owner.trackerRecordCount == 1 ? 'tracker record' : 'tracker records'}',
      ),
      Text(
        owner.localCachePrivatePresent
            ? 'Local cache contains private data'
            : 'Local cache contains no private data',
      ),
      Text(
        owner.compatibilityPrivatePresent
            ? 'Compatibility services contain private data'
            : 'Compatibility services contain no private data',
      ),
    ];
  }

  Future<void> _confirmPreparation(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Prepare Encrypted Migration?'),
        content: Text(_preparationWarning),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Prepare'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await owner.prepareEncryptedMigration();
    }
  }

  Future<void> _confirmFinalization(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Remove Plaintext & Activate AtlasVault?'),
        content: Text(_finalizationWarning),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Remove & Activate'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await owner.finalizeMigration();
    }
  }

  String get _preparationWarning {
    return switch (platform) {
      AtlasVaultPlaintextMigrationPresentationPlatform.android =>
        'An encrypted Android AtlasVault copy will be created and verified. '
            'Plaintext data remains unchanged and the prepared migration may '
            'be discarded before finalization.',
      AtlasVaultPlaintextMigrationPresentationPlatform.windows =>
        'An encrypted AtlasVault copy protected by current-user Windows DPAPI '
            'will be created and verified. Plaintext data remains unchanged '
            'and the prepared migration may be discarded before finalization.',
    };
  }

  String get _finalizationWarning {
    return switch (platform) {
      AtlasVaultPlaintextMigrationPresentationPlatform.android =>
        'The encrypted copy has been verified. Plaintext deletion begins only '
            'after this confirmation, and rollback will no longer be '
            'available. An interrupted migration remains resumable. '
            'Protection is device-local Android protection. Flutter encrypted '
            'export/import interoperability is not implemented yet. Windows '
            'secure storage is not implemented yet.',
      AtlasVaultPlaintextMigrationPresentationPlatform.windows =>
        'The encrypted read-back is complete; plaintext deletion will begin '
            'after this confirmation and rollback becomes unavailable. The '
            'migration can resume after interruption. Protection uses '
            'current-user Windows DPAPI. Windows encrypted import/export is '
            'not yet implemented.',
    };
  }
}

final class _UnavailableLegacyPrivateStateRestorer
    implements AtlasVaultLegacyPrivateStateRestoring {
  const _UnavailableLegacyPrivateStateRestorer();

  @override
  Future<void> restoreLegacyPrivateStateAfterRollback(
    AtlasVaultPlaintextPrivateState reviewedState,
  ) {
    return Future<void>.error(const AtlasVaultPlaintextMigrationException());
  }
}
