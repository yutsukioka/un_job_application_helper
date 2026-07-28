import 'package:flutter/material.dart';

import 'plaintext_migration.dart';

enum AtlasVaultPlaintextMigrationPresentationStatus {
  hidden,
  legacyAvailable,
  inventorying,
  inventoryReady,
  preparing,
  prepared,
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

final class AtlasVaultPlaintextMigrationContext {
  const AtlasVaultPlaintextMigrationContext({required this.owner});

  final AtlasVaultPlaintextMigrationPresentationOwner owner;

  @override
  String toString() => 'AtlasVaultPlaintextMigrationContext(<redacted>)';
}

final class AtlasVaultPlaintextMigrationPresentationOwner
    extends ChangeNotifier {
  AtlasVaultPlaintextMigrationPresentationOwner({
    required AtlasVaultPlaintextMigrationCoordinating coordinator,
  }) : // Keep the public constructor label descriptive.
       // ignore: prefer_initializing_formals
       _coordinator = coordinator;

  final AtlasVaultPlaintextMigrationCoordinating _coordinator;

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

  bool get isBusy {
    return switch (status) {
      AtlasVaultPlaintextMigrationPresentationStatus.inventorying ||
      AtlasVaultPlaintextMigrationPresentationStatus.preparing ||
      AtlasVaultPlaintextMigrationPresentationStatus.finalizing ||
      AtlasVaultPlaintextMigrationPresentationStatus.activating => true,
      _ => false,
    };
  }

  Future<void> bootstrapAuthority() {
    return _run('bootstrap', (revision) async {
      final authority = await _coordinator.inspectAuthority();
      if (!_isCurrent(revision)) {
        return;
      }
      _clearSummary();
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
      notifyListeners();
    });
  }

  Future<void> reviewInventory() {
    return _run('inventory', (revision) async {
      status = AtlasVaultPlaintextMigrationPresentationStatus.inventorying;
      notifyListeners();
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
      notifyListeners();
    });
  }

  Future<void> prepareEncryptedMigration() {
    return _run('prepare', (revision) async {
      if (status !=
          AtlasVaultPlaintextMigrationPresentationStatus.inventoryReady) {
        return;
      }
      status = AtlasVaultPlaintextMigrationPresentationStatus.preparing;
      notifyListeners();
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
      notifyListeners();
    });
  }

  Future<void> discardPreparedMigration() {
    return _run('discard', (revision) async {
      if (status != AtlasVaultPlaintextMigrationPresentationStatus.prepared) {
        return;
      }
      status = AtlasVaultPlaintextMigrationPresentationStatus.preparing;
      notifyListeners();
      try {
        await _coordinator.discardPrepared();
        if (!_isCurrent(revision)) {
          return;
        }
        _clearSummary();
        status = AtlasVaultPlaintextMigrationPresentationStatus.legacyAvailable;
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        status = await _statusAfterMigrationFailure(revision);
      }
      notifyListeners();
    });
  }

  Future<void> finalizeMigration() {
    return _run('finalize', (revision) async {
      if (status != AtlasVaultPlaintextMigrationPresentationStatus.prepared) {
        return;
      }
      status = AtlasVaultPlaintextMigrationPresentationStatus.finalizing;
      notifyListeners();
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
        status =
            AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired;
      }
      notifyListeners();
    });
  }

  Future<void> resumeMigration() {
    return _run('resume', (revision) async {
      status = AtlasVaultPlaintextMigrationPresentationStatus.finalizing;
      notifyListeners();
      try {
        final summary = await _coordinator.resume();
        if (!_isCurrent(revision)) {
          return;
        }
        _installSummary(summary);
        status = switch (summary.stage) {
          AtlasVaultPlaintextMigrationStage.prepared ||
          AtlasVaultPlaintextMigrationStage.encryptedVerified =>
            AtlasVaultPlaintextMigrationPresentationStatus.prepared,
          AtlasVaultPlaintextMigrationStage.completionPending =>
            AtlasVaultPlaintextMigrationPresentationStatus.completionPending,
          null => AtlasVaultPlaintextMigrationPresentationStatus.active,
          _ => AtlasVaultPlaintextMigrationPresentationStatus.resumeRequired,
        };
      } catch (_) {
        if (!_isCurrent(revision)) {
          return;
        }
        status =
            AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired;
      }
      notifyListeners();
    });
  }

  Future<void> activateEncryptedPrivateData() {
    return _run('activate', (revision) async {
      if (status !=
          AtlasVaultPlaintextMigrationPresentationStatus.activationRequired) {
        return;
      }
      status = AtlasVaultPlaintextMigrationPresentationStatus.activating;
      notifyListeners();
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
      notifyListeners();
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
  _statusAfterMigrationFailure(int revision) async {
    try {
      final authority = await _coordinator.inspectAuthority();
      if (!_isCurrent(revision)) {
        return AtlasVaultPlaintextMigrationPresentationStatus.hidden;
      }
      return switch (authority) {
        AtlasVaultPlaintextAuthorityState.legacy =>
          AtlasVaultPlaintextMigrationPresentationStatus.failed,
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
      return AtlasVaultPlaintextMigrationPresentationStatus.recoveryRequired;
    }
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
  const AtlasVaultPlaintextMigrationPanel({super.key, required this.owner});

  final AtlasVaultPlaintextMigrationPresentationOwner owner;

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
        content: const Text(
          'An encrypted Android AtlasVault copy will be created and verified. '
          'Plaintext data remains unchanged and the prepared migration may be '
          'discarded before finalization.',
        ),
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
        content: const Text(
          'The encrypted copy has been verified. Plaintext deletion begins '
          'only after this confirmation, and rollback will no longer be '
          'available. An interrupted migration remains resumable. Protection '
          'is device-local Android protection. Flutter encrypted export/import '
          'interoperability is not implemented yet. Windows secure storage is '
          'not implemented yet.',
        ),
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
}
