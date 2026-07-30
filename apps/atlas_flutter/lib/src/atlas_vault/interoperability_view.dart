import 'dart:async';

import 'package:flutter/material.dart';

import 'interoperability.dart';

enum AtlasVaultInteroperabilityPresentationStatus {
  hidden,
  ready,
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

final class AtlasVaultInteroperabilityPresentationOwner extends ChangeNotifier {
  AtlasVaultInteroperabilityPresentationOwner({
    required AtlasVaultInteroperabilityCoordinating coordinator,
  }) : // Keep the public dependency label readable.
       // ignore: prefer_initializing_formals
       _coordinator = coordinator;

  final AtlasVaultInteroperabilityCoordinating _coordinator;

  AtlasVaultInteroperabilityPresentationStatus status =
      AtlasVaultInteroperabilityPresentationStatus.hidden;
  String message = 'Encrypted interoperability is hidden.';
  int encryptedRecordCount = 0;
  bool recoveryWrapPresent = false;

  Future<void>? _operation;
  int _generation = 0;
  bool _disposed = false;

  Future<void> present() async {
    final generation = _generation;
    try {
      final availability = await _retain(_coordinator.inspectRecoveryExport);
      if (!_isCurrent(generation)) {
        return;
      }
      if (!availability.available) {
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
        availability.recoveryWrapPresent
            ? 'Recovery export is available.'
            : 'Recovery export setup is required.',
        count: availability.encryptedRecordCount,
        wrapPresent: availability.recoveryWrapPresent,
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
      _publish(
        AtlasVaultInteroperabilityPresentationStatus.failed,
        'Recovery export setup failed.',
      );
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

  void _publish(
    AtlasVaultInteroperabilityPresentationStatus next,
    String fixedMessage, {
    int? count,
    bool? wrapPresent,
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
  String? _displayRecoveryText;

  @override
  void initState() {
    super.initState();
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
    _clearLocalRecoveryState();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) {
      _clearLocalRecoveryState();
      widget.owner.hide();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    widget.owner.removeListener(_handleOwnerChanged);
    _clearLocalRecoveryState(notify: false);
    _recoveryInput.dispose();
    super.dispose();
  }

  void _handleOwnerChanged() {
    if (!mounted) {
      return;
    }
    if (widget.owner.status ==
        AtlasVaultInteroperabilityPresentationStatus.hidden) {
      _clearLocalRecoveryState(notify: false);
    }
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final owner = widget.owner;
    final busy = switch (owner.status) {
      AtlasVaultInteroperabilityPresentationStatus.generatingRecoveryKey ||
      AtlasVaultInteroperabilityPresentationStatus.preparingExport ||
      AtlasVaultInteroperabilityPresentationStatus.saving => true,
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
      final value = handle.take();
      handle.destroy();
      if (!mounted || value == null) {
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

  void _clearLocalRecoveryState({bool notify = true}) {
    _recoveryInput.clear();
    _displayRecoveryText = null;
    if (notify && mounted) {
      setState(() {});
    }
  }
}
