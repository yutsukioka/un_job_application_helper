import 'dart:async';

import 'package:flutter/material.dart';

import 'interoperability.dart';

enum AtlasVaultInteroperabilityPresentationStatus {
  hidden,
  ready,
  pickingImport,
  awaitingImportRecoveryKey,
  importing,
  resumeRequired,
  completionPending,
  importedActive,
  migrationRequired,
  existingVault,
  generatingRecoveryKey,
  awaitingRecoveryConfirmation,
  preparingExport,
  exportReady,
  saving,
  saved,
  cancelled,
  failed,
  recoveryRequired,
  unavailable,
}

enum AtlasVaultInteroperabilityPlatformProfile { android, windows }

final class AtlasVaultInteroperabilityContext {
  const AtlasVaultInteroperabilityContext({required this.owner});

  final AtlasVaultInteroperabilityPresentationOwner owner;

  @override
  String toString() => 'AtlasVaultInteroperabilityContext(<redacted>)';
}

final class AtlasVaultInteroperabilityPresentationOwner extends ChangeNotifier {
  AtlasVaultInteroperabilityPresentationOwner({
    required AtlasVaultInteroperabilityCoordinating coordinator,
    this.platformProfile = AtlasVaultInteroperabilityPlatformProfile.android,
  }) : // Keep the public dependency label readable.
       // ignore: prefer_initializing_formals
       _coordinator = coordinator;

  final AtlasVaultInteroperabilityCoordinating _coordinator;
  final AtlasVaultInteroperabilityPlatformProfile platformProfile;

  AtlasVaultInteroperabilityPresentationStatus status =
      AtlasVaultInteroperabilityPresentationStatus.hidden;
  String message = 'Encrypted interoperability is hidden.';
  int encryptedRecordCount = 0;
  bool recoveryWrapPresent = false;
  bool importAvailable = false;
  bool pendingImport = false;

  Future<void>? _operation;
  int _generation = 0;
  bool _disposed = false;

  Future<void> present() async {
    final generation = _generation;
    try {
      final importAvailability = await _retain(
        _coordinator.inspectRecoveryImport,
      );
      if (!_isCurrent(generation)) {
        return;
      }
      switch (importAvailability.disposition) {
        case AtlasVaultRecoveryImportDisposition.resumeRequired:
        case AtlasVaultRecoveryImportDisposition.completionPending:
          _publishImportResult(importAvailability);
          return;
        case AtlasVaultRecoveryImportDisposition.ready:
          importAvailable = true;
          pendingImport = false;
        case AtlasVaultRecoveryImportDisposition.migrationRequired:
        case AtlasVaultRecoveryImportDisposition.existingVault:
        case AtlasVaultRecoveryImportDisposition.unavailable:
          importAvailable = false;
          pendingImport = false;
        case AtlasVaultRecoveryImportDisposition.importPrepared:
        case AtlasVaultRecoveryImportDisposition.importedAndActive:
        case AtlasVaultRecoveryImportDisposition.cancelled:
        case AtlasVaultRecoveryImportDisposition.failed:
        case AtlasVaultRecoveryImportDisposition.recoveryRequired:
          throw const AtlasVaultInteroperabilityException();
      }
      final availability = await _retain(_coordinator.inspectRecoveryExport);
      if (!_isCurrent(generation)) {
        return;
      }
      if (!availability.available) {
        if (importAvailable) {
          _publish(
            AtlasVaultInteroperabilityPresentationStatus.ready,
            'Encrypted backup import is available.',
            count: 0,
            wrapPresent: false,
            importReady: true,
          );
          return;
        }
        if (importAvailability.disposition ==
            AtlasVaultRecoveryImportDisposition.migrationRequired) {
          _publish(
            AtlasVaultInteroperabilityPresentationStatus.migrationRequired,
            'Plaintext private data must be resolved before import.',
          );
          return;
        }
        if (importAvailability.disposition ==
            AtlasVaultRecoveryImportDisposition.existingVault) {
          _publish(
            AtlasVaultInteroperabilityPresentationStatus.existingVault,
            'Encrypted import requires a clean installation.',
          );
          return;
        }
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.unavailable,
          'Encrypted interoperability is unavailable.',
          count: 0,
          wrapPresent: false,
        );
        return;
      }
      _publish(
        AtlasVaultInteroperabilityPresentationStatus.ready,
        _recoveryExportMessage(availability.recoveryWrapPresent),
        count: availability.encryptedRecordCount,
        wrapPresent: availability.recoveryWrapPresent,
        importReady: importAvailable,
      );
    } catch (_) {
      if (_isCurrent(generation)) {
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.unavailable,
          'Encrypted interoperability is unavailable.',
        );
      }
    }
  }

  String _recoveryExportMessage(bool recoveryWrapPresent) {
    if (platformProfile == AtlasVaultInteroperabilityPlatformProfile.windows) {
      return recoveryWrapPresent
          ? 'Encrypted backup is available with Windows current-user device-local protection.'
          : 'Windows current-user device-local recovery export setup is required.';
    }
    return recoveryWrapPresent
        ? 'Recovery export is available.'
        : 'Recovery export setup is required.';
  }

  Future<void> prepareRecoveryImport() async {
    final generation = _generation;
    _publish(
      AtlasVaultInteroperabilityPresentationStatus.pickingImport,
      'Select an encrypted AtlasVault backup.',
    );
    try {
      final result = await _retain(_coordinator.prepareRecoveryImport);
      if (_isCurrent(generation)) {
        _publishImportResult(result);
      }
    } catch (_) {
      if (_isCurrent(generation)) {
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.failed,
          'Encrypted backup import failed.',
        );
      }
    }
  }

  Future<void> confirmRecoveryImport(String recoveryKeyText) async {
    final generation = _generation;
    _publish(
      AtlasVaultInteroperabilityPresentationStatus.importing,
      'Verifying and importing encrypted backup.',
    );
    try {
      final result = await _retain(
        () => _coordinator.confirmRecoveryImport(recoveryKeyText),
      );
      if (_isCurrent(generation)) {
        _publishImportResult(result);
      }
    } catch (_) {
      if (_isCurrent(generation)) {
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.failed,
          'Encrypted backup import failed.',
        );
      }
    }
  }

  Future<void> discardPendingImport() async {
    final generation = _generation;
    _publish(
      AtlasVaultInteroperabilityPresentationStatus.importing,
      'Discarding pending encrypted import.',
    );
    try {
      final result = await _retain(_coordinator.discardPendingImport);
      if (_isCurrent(generation)) {
        _publishImportResult(result);
      }
    } catch (_) {
      if (_isCurrent(generation)) {
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.recoveryRequired,
          'Pending encrypted import requires recovery.',
          pending: true,
        );
      }
    }
  }

  Future<AtlasVaultRecoveryDisplayHandle> beginRecoverySetup() async {
    final generation = _generation;
    _publish(
      AtlasVaultInteroperabilityPresentationStatus.generatingRecoveryKey,
      'Generating a recovery key.',
    );
    try {
      final handle = await _retain(_coordinator.beginRecoverySetup);
      if (!_isCurrent(generation)) {
        handle.destroy();
        throw const AtlasVaultInteroperabilityException();
      }
      _publish(
        AtlasVaultInteroperabilityPresentationStatus
            .awaitingRecoveryConfirmation,
        'Confirm the recovery key to prepare an encrypted backup.',
      );
      return handle;
    } catch (_) {
      if (_isCurrent(generation)) {
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.failed,
          'Recovery export setup failed.',
        );
      }
      rethrow;
    }
  }

  Future<void> confirmRecoverySetup(String recoveryKeyText) async {
    final generation = _generation;
    _publish(
      AtlasVaultInteroperabilityPresentationStatus.preparingExport,
      'Preparing encrypted backup.',
    );
    try {
      final result = await _retain(
        () => _coordinator.confirmRecoverySetup(recoveryKeyText),
      );
      if (_isCurrent(generation)) {
        _publishResult(result);
      }
    } catch (_) {
      if (_isCurrent(generation)) {
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.failed,
          'Encrypted backup operation failed.',
        );
      }
    }
  }

  Future<void> prepareExistingRecoveryExport(String recoveryKeyText) async {
    final generation = _generation;
    _publish(
      AtlasVaultInteroperabilityPresentationStatus.preparingExport,
      'Preparing encrypted backup.',
    );
    try {
      final result = await _retain(
        () => _coordinator.prepareExistingRecoveryExport(recoveryKeyText),
      );
      if (_isCurrent(generation)) {
        _publishResult(result);
      }
    } catch (_) {
      if (_isCurrent(generation)) {
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.failed,
          'Encrypted backup operation failed.',
        );
      }
    }
  }

  Future<void> savePreparedExport() async {
    final generation = _generation;
    _publish(
      AtlasVaultInteroperabilityPresentationStatus.saving,
      'Saving encrypted backup.',
    );
    try {
      final result = await _retain(_coordinator.savePreparedExport);
      if (_isCurrent(generation)) {
        _publishResult(result);
      }
    } catch (_) {
      if (_isCurrent(generation)) {
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.failed,
          'Encrypted backup operation failed.',
        );
      }
    }
  }

  void hide() {
    _generation += 1;
    _coordinator.discardPendingRecovery();
    status = AtlasVaultInteroperabilityPresentationStatus.hidden;
    message = 'Encrypted interoperability is hidden.';
    encryptedRecordCount = 0;
    recoveryWrapPresent = false;
    importAvailable = false;
    pendingImport = false;
    if (!_disposed) {
      notifyListeners();
    }
  }

  Future<void> stopAndDrain() async {
    hide();
    await _operation;
    await _coordinator.stop();
  }

  Future<T> _retain<T>(Future<T> Function() body) {
    if (_disposed || _operation != null) {
      return Future<T>.error(const AtlasVaultInteroperabilityException());
    }
    final completer = Completer<T>();
    final start = Completer<void>();
    late final Future<void> retained;
    retained = start.future.then((_) async {
      try {
        final value = await body();
        completer.complete(value);
      } catch (_) {
        if (!completer.isCompleted) {
          completer.completeError(const AtlasVaultInteroperabilityException());
        }
      } finally {
        if (identical(_operation, retained)) {
          _operation = null;
        }
      }
    });
    _operation = retained;
    start.complete();
    return completer.future;
  }

  bool _isCurrent(int generation) {
    return !_disposed && generation == _generation;
  }

  void _publishResult(AtlasVaultRecoveryExportResult result) {
    switch (result.disposition) {
      case AtlasVaultRecoveryExportDisposition.exportReady:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.exportReady,
          'Encrypted backup is ready to save.',
          count: result.encryptedRecordCount,
          wrapPresent: result.recoveryWrapPresent,
        );
      case AtlasVaultRecoveryExportDisposition.saved:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.saved,
          'Encrypted backup saved.',
          count: result.encryptedRecordCount,
          wrapPresent: result.recoveryWrapPresent,
        );
      case AtlasVaultRecoveryExportDisposition.cancelled:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.cancelled,
          'Encrypted backup save cancelled.',
          count: result.encryptedRecordCount,
          wrapPresent: result.recoveryWrapPresent,
        );
      case AtlasVaultRecoveryExportDisposition.recoveryRequired:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.recoveryRequired,
          'A valid recovery key is required.',
          count: result.encryptedRecordCount,
          wrapPresent: result.recoveryWrapPresent,
        );
      case AtlasVaultRecoveryExportDisposition.unavailable:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.unavailable,
          'Encrypted interoperability is unavailable.',
        );
      case AtlasVaultRecoveryExportDisposition.failed:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.failed,
          'Encrypted backup operation failed.',
          count: result.encryptedRecordCount,
          wrapPresent: result.recoveryWrapPresent,
        );
    }
  }

  void _publishImportResult(AtlasVaultRecoveryImportResult result) {
    switch (result.disposition) {
      case AtlasVaultRecoveryImportDisposition.ready:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.ready,
          'Encrypted backup import is available.',
          count: result.encryptedRecordCount,
          importReady: true,
          pending: false,
        );
      case AtlasVaultRecoveryImportDisposition.importPrepared:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus
              .awaitingImportRecoveryKey,
          'Enter the recovery key to verify and import the backup.',
          count: result.encryptedRecordCount,
          importReady: true,
          pending: result.pendingImport,
        );
      case AtlasVaultRecoveryImportDisposition.importedAndActive:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.importedActive,
          'Encrypted backup imported and activated.',
          count: result.encryptedRecordCount,
          importReady: false,
          pending: false,
        );
      case AtlasVaultRecoveryImportDisposition.cancelled:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.cancelled,
          'Encrypted backup import cancelled.',
          count: 0,
          importReady: true,
          pending: result.pendingImport,
        );
      case AtlasVaultRecoveryImportDisposition.migrationRequired:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.migrationRequired,
          'Plaintext private data must be resolved before import.',
          count: 0,
          importReady: false,
          pending: result.pendingImport,
        );
      case AtlasVaultRecoveryImportDisposition.existingVault:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.existingVault,
          'Encrypted import requires a clean installation.',
          count: 0,
          importReady: false,
          pending: result.pendingImport,
        );
      case AtlasVaultRecoveryImportDisposition.resumeRequired:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.resumeRequired,
          'A pending encrypted import must be resumed or discarded.',
          count: result.encryptedRecordCount,
          importReady: true,
          pending: true,
        );
      case AtlasVaultRecoveryImportDisposition.completionPending:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.completionPending,
          'Encrypted import completion must be verified.',
          count: result.encryptedRecordCount,
          importReady: true,
          pending: true,
        );
      case AtlasVaultRecoveryImportDisposition.failed:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.failed,
          'Encrypted backup import failed.',
          count: result.encryptedRecordCount,
          pending: result.pendingImport,
        );
      case AtlasVaultRecoveryImportDisposition.recoveryRequired:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.recoveryRequired,
          'A valid recovery key or matching backup is required.',
          count: result.encryptedRecordCount,
          pending: result.pendingImport,
        );
      case AtlasVaultRecoveryImportDisposition.unavailable:
        _publish(
          AtlasVaultInteroperabilityPresentationStatus.unavailable,
          'Encrypted interoperability is unavailable.',
          count: 0,
          importReady: false,
          pending: result.pendingImport,
        );
    }
  }

  void _publish(
    AtlasVaultInteroperabilityPresentationStatus next,
    String fixedMessage, {
    int? count,
    bool? wrapPresent,
    bool? importReady,
    bool? pending,
  }) {
    if (_disposed) {
      return;
    }
    status = next;
    message = fixedMessage;
    if (count != null) {
      encryptedRecordCount = count;
    }
    if (wrapPresent != null) {
      recoveryWrapPresent = wrapPresent;
    }
    if (importReady != null) {
      importAvailable = importReady;
    }
    if (pending != null) {
      pendingImport = pending;
    }
    notifyListeners();
  }

  @override
  void dispose() {
    if (_disposed) {
      return;
    }
    _generation += 1;
    _coordinator.discardPendingRecovery();
    status = AtlasVaultInteroperabilityPresentationStatus.hidden;
    message = 'Encrypted interoperability is hidden.';
    encryptedRecordCount = 0;
    recoveryWrapPresent = false;
    importAvailable = false;
    pendingImport = false;
    _disposed = true;
    super.dispose();
  }

  @override
  String toString() =>
      'AtlasVaultInteroperabilityPresentationOwner(<redacted>)';
}

class AtlasVaultInteroperabilityPanel extends StatefulWidget {
  const AtlasVaultInteroperabilityPanel({required this.owner, super.key});

  final AtlasVaultInteroperabilityPresentationOwner owner;

  @override
  State<AtlasVaultInteroperabilityPanel> createState() =>
      _AtlasVaultInteroperabilityPanelState();
}

class _AtlasVaultInteroperabilityPanelState
    extends State<AtlasVaultInteroperabilityPanel>
    with WidgetsBindingObserver {
  final TextEditingController _recoveryInput = TextEditingController();
  final TextEditingController _importRecoveryInput = TextEditingController();
  String? _displayRecoveryText;
  AppLifecycleState? _lifecycleState;
  AtlasVaultInteroperabilityPresentationStatus? _lastOwnerStatus;
  bool _windowsDocumentFocusLossAvailable = false;
  bool _windowsDocumentFocusLossActive = false;

  @override
  void initState() {
    super.initState();
    _lifecycleState = WidgetsBinding.instance.lifecycleState;
    _lastOwnerStatus = widget.owner.status;
    WidgetsBinding.instance.addObserver(this);
    widget.owner.addListener(_handleOwnerChanged);
  }

  @override
  void didUpdateWidget(covariant AtlasVaultInteroperabilityPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.owner == widget.owner) {
      return;
    }
    oldWidget.owner.removeListener(_handleOwnerChanged);
    widget.owner.addListener(_handleOwnerChanged);
    _lastOwnerStatus = widget.owner.status;
    _windowsDocumentFocusLossAvailable = false;
    _windowsDocumentFocusLossActive = false;
    _clearLocalRecoveryState();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _lifecycleState = state;
    if (state == AppLifecycleState.resumed) {
      _windowsDocumentFocusLossActive = false;
      return;
    }
    final preserveNativeDialog =
        state == AppLifecycleState.inactive &&
        _isWindowsDocumentOperation(widget.owner.status) &&
        (_windowsDocumentFocusLossAvailable || _windowsDocumentFocusLossActive);
    _clearLocalRecoveryState();
    if (preserveNativeDialog) {
      _windowsDocumentFocusLossAvailable = false;
      _windowsDocumentFocusLossActive = true;
      return;
    }
    _windowsDocumentFocusLossAvailable = false;
    _windowsDocumentFocusLossActive = false;
    widget.owner.hide();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    widget.owner.removeListener(_handleOwnerChanged);
    _clearLocalRecoveryState(notify: false);
    _recoveryInput.dispose();
    _importRecoveryInput.dispose();
    super.dispose();
  }

  void _handleOwnerChanged() {
    if (!mounted) {
      return;
    }
    final previousStatus = _lastOwnerStatus;
    final currentStatus = widget.owner.status;
    _lastOwnerStatus = currentStatus;
    final wasDocumentOperation = _isWindowsDocumentOperation(previousStatus);
    final isDocumentOperation = _isWindowsDocumentOperation(currentStatus);
    if (!wasDocumentOperation && isDocumentOperation) {
      _windowsDocumentFocusLossAvailable = true;
      _windowsDocumentFocusLossActive = false;
    } else if (wasDocumentOperation && !isDocumentOperation) {
      _windowsDocumentFocusLossAvailable = false;
      _windowsDocumentFocusLossActive = false;
      if (_lifecycleState != null &&
          _lifecycleState != AppLifecycleState.resumed &&
          currentStatus !=
              AtlasVaultInteroperabilityPresentationStatus.hidden) {
        _clearLocalRecoveryState(notify: false);
        widget.owner.hide();
        return;
      }
    }
    if (currentStatus == AtlasVaultInteroperabilityPresentationStatus.hidden) {
      _clearLocalRecoveryState(notify: false);
    }
    setState(() {});
  }

  bool _isWindowsDocumentOperation(
    AtlasVaultInteroperabilityPresentationStatus? status,
  ) =>
      widget.owner.platformProfile ==
          AtlasVaultInteroperabilityPlatformProfile.windows &&
      (status == AtlasVaultInteroperabilityPresentationStatus.pickingImport ||
          status == AtlasVaultInteroperabilityPresentationStatus.saving);

  @override
  Widget build(BuildContext context) {
    final owner = widget.owner;
    final busy = switch (owner.status) {
      AtlasVaultInteroperabilityPresentationStatus.generatingRecoveryKey ||
      AtlasVaultInteroperabilityPresentationStatus.preparingExport ||
      AtlasVaultInteroperabilityPresentationStatus.saving ||
      AtlasVaultInteroperabilityPresentationStatus.pickingImport ||
      AtlasVaultInteroperabilityPresentationStatus.importing => true,
      _ => false,
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Encrypted Interoperability',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 8),
        Text(owner.message),
        if (owner.encryptedRecordCount > 0) ...[
          const SizedBox(height: 6),
          Text('${owner.encryptedRecordCount} encrypted records'),
        ],
        const SizedBox(height: 12),
        if (owner.status == AtlasVaultInteroperabilityPresentationStatus.hidden)
          FilledButton(
            onPressed: () => unawaited(owner.present()),
            child: const Text('Open Encrypted Interoperability'),
          ),
        if (owner.status ==
                AtlasVaultInteroperabilityPresentationStatus.ready &&
            !owner.recoveryWrapPresent)
          FilledButton(
            onPressed: busy ? null : _beginRecoverySetup,
            child: const Text('Set Up Recovery Export'),
          ),
        if (owner.status ==
                AtlasVaultInteroperabilityPresentationStatus.ready &&
            owner.recoveryWrapPresent) ...[
          TextField(
            controller: _recoveryInput,
            obscureText: true,
            autocorrect: false,
            enableSuggestions: false,
            decoration: const InputDecoration(labelText: 'Recovery key'),
          ),
          const SizedBox(height: 8),
          FilledButton(
            onPressed: busy ? null : _prepareExistingExport,
            child: const Text('Prepare Encrypted Backup'),
          ),
        ],
        if (!owner.importAvailable &&
            !owner.pendingImport &&
            (owner.status ==
                    AtlasVaultInteroperabilityPresentationStatus.cancelled ||
                owner.status ==
                    AtlasVaultInteroperabilityPresentationStatus.failed ||
                owner.status ==
                    AtlasVaultInteroperabilityPresentationStatus
                        .recoveryRequired)) ...[
          OutlinedButton(
            onPressed: busy ? null : () => unawaited(owner.present()),
            child: const Text('Retry Recovery Export'),
          ),
        ],
        if (owner.importAvailable &&
            !owner.pendingImport &&
            (owner.status ==
                    AtlasVaultInteroperabilityPresentationStatus.ready ||
                owner.status ==
                    AtlasVaultInteroperabilityPresentationStatus.cancelled ||
                owner.status ==
                    AtlasVaultInteroperabilityPresentationStatus.failed ||
                owner.status ==
                    AtlasVaultInteroperabilityPresentationStatus
                        .recoveryRequired)) ...[
          const SizedBox(height: 8),
          OutlinedButton(
            onPressed: busy
                ? null
                : () => unawaited(owner.prepareRecoveryImport()),
            child: const Text('Import Encrypted Backup'),
          ),
        ],
        if (owner.pendingImport &&
            owner.status !=
                AtlasVaultInteroperabilityPresentationStatus.importing &&
            owner.status !=
                AtlasVaultInteroperabilityPresentationStatus.pickingImport) ...[
          FilledButton(
            onPressed: busy
                ? null
                : () => unawaited(owner.prepareRecoveryImport()),
            child: const Text('Resume Recovery Import'),
          ),
          const SizedBox(height: 8),
          TextButton(
            onPressed: busy ? null : _confirmDiscardPendingImport,
            child: const Text('Discard Pending Import'),
          ),
        ],
        if (owner.status ==
            AtlasVaultInteroperabilityPresentationStatus
                .awaitingImportRecoveryKey) ...[
          TextField(
            controller: _importRecoveryInput,
            obscureText: true,
            autocorrect: false,
            enableSuggestions: false,
            decoration: const InputDecoration(labelText: 'Recovery Key'),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: [
              FilledButton(
                onPressed: _confirmRecoveryImport,
                child: const Text('Import and Activate'),
              ),
              TextButton(
                onPressed: _cancelRecoveryImport,
                child: const Text('Cancel'),
              ),
            ],
          ),
        ],
        if (owner.status ==
            AtlasVaultInteroperabilityPresentationStatus
                .awaitingRecoveryConfirmation) ...[
          if (_displayRecoveryText case final value?) ...[
            SelectableText(value),
            const SizedBox(height: 8),
          ],
          TextField(
            controller: _recoveryInput,
            obscureText: true,
            autocorrect: false,
            enableSuggestions: false,
            decoration: const InputDecoration(
              labelText: 'Re-enter recovery key',
            ),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: [
              FilledButton(
                onPressed: _confirmRecoverySetup,
                child: const Text('Confirm Recovery Key'),
              ),
              TextButton(
                onPressed: _cancelRecoverySetup,
                child: const Text('Cancel'),
              ),
            ],
          ),
        ],
        if (owner.status ==
            AtlasVaultInteroperabilityPresentationStatus.exportReady)
          FilledButton(
            onPressed: busy
                ? null
                : () => unawaited(owner.savePreparedExport()),
            child: const Text('Save Encrypted Backup'),
          ),
        if (busy) const LinearProgressIndicator(),
      ],
    );
  }

  Future<void> _beginRecoverySetup() async {
    _clearLocalRecoveryState();
    try {
      final handle = await widget.owner.beginRecoverySetup();
      if (!mounted) {
        handle.destroy();
        return;
      }
      final value = handle.take();
      handle.destroy();
      if (value == null) {
        return;
      }
      setState(() {
        _displayRecoveryText = value;
      });
    } catch (_) {
      _clearLocalRecoveryState();
    }
  }

  void _confirmRecoverySetup() {
    final value = _recoveryInput.text;
    _clearLocalRecoveryState();
    unawaited(widget.owner.confirmRecoverySetup(value));
  }

  void _prepareExistingExport() {
    final value = _recoveryInput.text;
    _clearLocalRecoveryState();
    unawaited(widget.owner.prepareExistingRecoveryExport(value));
  }

  void _cancelRecoverySetup() {
    _clearLocalRecoveryState();
    widget.owner.hide();
  }

  void _confirmRecoveryImport() {
    final value = _importRecoveryInput.text;
    _clearLocalRecoveryState();
    unawaited(widget.owner.confirmRecoveryImport(value));
  }

  void _cancelRecoveryImport() {
    _clearLocalRecoveryState();
    widget.owner.hide();
  }

  Future<void> _confirmDiscardPendingImport() async {
    final shouldDiscard = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Discard Pending Import?'),
        content: const Text(
          'Only matching pre-selection import resources will be removed.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Discard'),
          ),
        ],
      ),
    );
    if (shouldDiscard == true && mounted) {
      _clearLocalRecoveryState();
      await widget.owner.discardPendingImport();
    }
  }

  void _clearLocalRecoveryState({bool notify = true}) {
    _recoveryInput.clear();
    _importRecoveryInput.clear();
    _displayRecoveryText = null;
    if (notify && mounted) {
      setState(() {});
    }
  }
}
