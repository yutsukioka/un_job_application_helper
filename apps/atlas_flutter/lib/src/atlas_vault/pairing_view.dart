import 'dart:async';

import 'package:flutter/material.dart';

import 'pairing_transaction.dart';

enum AtlasVaultTrustedPairingPresentationStatus {
  hidden,
  ready,
  identityReady,
  offerReady,
  offerSaved,
  acceptanceReady,
  acceptanceSaved,
  codesReady,
  codesConfirmed,
  deliveryReady,
  deliverySaved,
  acknowledgementReady,
  acknowledgementSaved,
  completed,
  cancelled,
  migrationRequired,
  existingVault,
  unavailable,
  recoveryRequired,
  failed,
}

final class AtlasVaultTrustedPairingContext {
  const AtlasVaultTrustedPairingContext({required this.owner});

  final AtlasVaultTrustedPairingPresentationOwner owner;

  @override
  String toString() => 'AtlasVaultTrustedPairingContext(<redacted>)';
}

final class AtlasVaultTrustedPairingPresentationOwner extends ChangeNotifier {
  AtlasVaultTrustedPairingPresentationOwner({
    required AtlasVaultTrustedPairingCoordinating coordinator,
  }) : // Keep the public dependency label explicit at composition sites.
       // ignore: prefer_initializing_formals
       _coordinator = coordinator;

  final AtlasVaultTrustedPairingCoordinating _coordinator;

  AtlasVaultTrustedPairingPresentationStatus status =
      AtlasVaultTrustedPairingPresentationStatus.hidden;
  AtlasVaultPairingRole? role;
  AtlasVaultPairingStage? stage;
  String? localFingerprint;
  String? peerFingerprint;
  String? sas;
  String? expiresAt;
  bool trusted = false;
  bool pendingTransaction = false;

  Future<void>? _operation;
  int _generation = 0;
  bool _disposed = false;

  bool get isBusy => _operation != null;

  bool get blocksLegacyPrivateAuthority => pendingTransaction;

  bool get blocksPersistedCacheWrites => pendingTransaction;

  Future<void> inspect() => _run(_coordinator.inspect);

  Future<bool> refreshPrivateAuthorityState() async {
    if (_disposed || _operation != null) {
      throw const AtlasVaultPairingTransactionException();
    }
    final generation = _generation;
    AtlasVaultTrustedPairingResult? inspected;
    late final Future<void> retained;
    retained = () async {
      final result = await _coordinator.inspect();
      if (!_isCurrent(generation)) {
        throw const AtlasVaultPairingTransactionException();
      }
      inspected = result;
      _publish(result);
    }();
    _operation = retained;
    notifyListeners();
    try {
      await retained;
      return inspected!.pendingTransaction;
    } finally {
      if (identical(_operation, retained)) {
        _operation = null;
        if (_isCurrent(generation)) {
          notifyListeners();
        }
      }
    }
  }

  Future<void> createDeviceIdentity() =>
      _run(_coordinator.createDeviceIdentity);

  Future<void> createPairingOffer() => _run(_coordinator.createPairingOffer);

  Future<void> savePairingOffer() => _run(_coordinator.savePairingOffer);

  Future<void> importPairingOffer() => _run(_coordinator.importPairingOffer);

  Future<void> savePairingAcceptance() =>
      _run(_coordinator.savePairingAcceptance);

  Future<void> importPairingAcceptance() =>
      _run(_coordinator.importPairingAcceptance);

  Future<void> confirmCodesMatch() => _run(_coordinator.confirmCodesMatch);

  Future<void> saveKeyDelivery() => _run(_coordinator.saveKeyDelivery);

  Future<void> importKeyDelivery() => _run(_coordinator.importKeyDelivery);

  Future<void> savePairingAcknowledgement() =>
      _run(_coordinator.savePairingAcknowledgement);

  Future<void> importPairingAcknowledgement() =>
      _run(_coordinator.importPairingAcknowledgement);

  Future<void> resumePairing() => _run(_coordinator.resumePairing);

  Future<void> discardPairing() => _run(_coordinator.discardPairing);

  Future<void> _run(
    Future<AtlasVaultTrustedPairingResult> Function() operation,
  ) {
    if (_disposed || _operation != null) {
      return Future<void>.value();
    }
    final generation = _generation;
    late final Future<void> retained;
    retained = () async {
      try {
        final result = await operation();
        if (_isCurrent(generation)) {
          _publish(result);
        }
      } catch (_) {
        if (_isCurrent(generation)) {
          _clearSafeDetails();
          status = AtlasVaultTrustedPairingPresentationStatus.failed;
          notifyListeners();
        }
      } finally {
        if (identical(_operation, retained)) {
          _operation = null;
          if (_isCurrent(generation)) {
            notifyListeners();
          }
        }
      }
    }();
    _operation = retained;
    notifyListeners();
    return retained;
  }

  void _publish(AtlasVaultTrustedPairingResult result) {
    role = result.role;
    stage = result.stage;
    localFingerprint = result.localFingerprint;
    peerFingerprint = result.peerFingerprint;
    sas = result.sas;
    expiresAt = result.expiresAt;
    trusted = result.trusted;
    pendingTransaction = result.pendingTransaction;
    status = switch (result.disposition) {
      AtlasVaultTrustedPairingDisposition.ready =>
        AtlasVaultTrustedPairingPresentationStatus.ready,
      AtlasVaultTrustedPairingDisposition.identityReady =>
        AtlasVaultTrustedPairingPresentationStatus.identityReady,
      AtlasVaultTrustedPairingDisposition.offerReady =>
        AtlasVaultTrustedPairingPresentationStatus.offerReady,
      AtlasVaultTrustedPairingDisposition.offerSaved =>
        AtlasVaultTrustedPairingPresentationStatus.offerSaved,
      AtlasVaultTrustedPairingDisposition.acceptanceReady =>
        AtlasVaultTrustedPairingPresentationStatus.acceptanceReady,
      AtlasVaultTrustedPairingDisposition.acceptanceSaved =>
        AtlasVaultTrustedPairingPresentationStatus.acceptanceSaved,
      AtlasVaultTrustedPairingDisposition.codesReady =>
        AtlasVaultTrustedPairingPresentationStatus.codesReady,
      AtlasVaultTrustedPairingDisposition.codesConfirmed =>
        AtlasVaultTrustedPairingPresentationStatus.codesConfirmed,
      AtlasVaultTrustedPairingDisposition.deliveryReady =>
        AtlasVaultTrustedPairingPresentationStatus.deliveryReady,
      AtlasVaultTrustedPairingDisposition.deliverySaved =>
        AtlasVaultTrustedPairingPresentationStatus.deliverySaved,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady =>
        AtlasVaultTrustedPairingPresentationStatus.acknowledgementReady,
      AtlasVaultTrustedPairingDisposition.acknowledgementSaved =>
        AtlasVaultTrustedPairingPresentationStatus.acknowledgementSaved,
      AtlasVaultTrustedPairingDisposition.completed =>
        AtlasVaultTrustedPairingPresentationStatus.completed,
      AtlasVaultTrustedPairingDisposition.cancelled =>
        AtlasVaultTrustedPairingPresentationStatus.cancelled,
      AtlasVaultTrustedPairingDisposition.migrationRequired =>
        AtlasVaultTrustedPairingPresentationStatus.migrationRequired,
      AtlasVaultTrustedPairingDisposition.existingVault =>
        AtlasVaultTrustedPairingPresentationStatus.existingVault,
      AtlasVaultTrustedPairingDisposition.unavailable =>
        AtlasVaultTrustedPairingPresentationStatus.unavailable,
      AtlasVaultTrustedPairingDisposition.recoveryRequired =>
        AtlasVaultTrustedPairingPresentationStatus.recoveryRequired,
      AtlasVaultTrustedPairingDisposition.failed =>
        AtlasVaultTrustedPairingPresentationStatus.failed,
    };
    notifyListeners();
  }

  void clearSensitiveInput() {
    _coordinator.cancelActiveOperation();
    sas = null;
    _generation += 1;
    if (!_disposed) {
      notifyListeners();
    }
  }

  void hide() {
    clearSensitiveInput();
    _clearSafeDetails(preservePendingTransaction: true);
    status = AtlasVaultTrustedPairingPresentationStatus.hidden;
    if (!_disposed) {
      notifyListeners();
    }
  }

  Future<void> stopAndDrain() async {
    _generation += 1;
    clearSensitiveInput();
    final coordinatorStop = _coordinator.stop();
    await _operation;
    await coordinatorStop;
  }

  bool _isCurrent(int generation) => !_disposed && generation == _generation;

  void _clearSafeDetails({bool preservePendingTransaction = false}) {
    role = null;
    stage = null;
    localFingerprint = null;
    peerFingerprint = null;
    sas = null;
    expiresAt = null;
    trusted = false;
    if (!preservePendingTransaction) {
      pendingTransaction = false;
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _generation += 1;
    _coordinator.cancelActiveOperation();
    _clearSafeDetails();
    super.dispose();
  }

  @override
  String toString() => 'AtlasVaultTrustedPairingPresentationOwner(<redacted>)';
}

final class AtlasVaultTrustedPairingPanel extends StatelessWidget {
  const AtlasVaultTrustedPairingPanel({super.key, required this.owner});

  final AtlasVaultTrustedPairingPresentationOwner owner;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: owner,
      builder: (context, _) {
        final enabled = !owner.isBusy;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(_statusText(owner.status)),
            if (owner.localFingerprint case final value?)
              _PairingValue(label: 'This device', value: value),
            if (owner.peerFingerprint case final value?)
              _PairingValue(label: 'Other device', value: value),
            if (owner.sas case final value?)
              _PairingValue(label: 'Comparison code', value: value),
            if (owner.stage case final value?)
              _PairingValue(label: 'Stage', value: value.encoded),
            if (owner.expiresAt case final value?)
              _PairingValue(label: 'Expires', value: value),
            if (owner.trusted)
              const _PairingValue(label: 'Status', value: 'Trusted'),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                _PairingAction(
                  label: 'Create Device Identity',
                  enabled: enabled,
                  action: owner.createDeviceIdentity,
                ),
                _PairingAction(
                  label: 'Create Pairing Offer',
                  enabled: enabled,
                  action: owner.createPairingOffer,
                ),
                _PairingAction(
                  label: 'Save Pairing Offer',
                  enabled: enabled,
                  action: owner.savePairingOffer,
                ),
                _PairingAction(
                  label: 'Import Pairing Offer',
                  enabled: enabled,
                  action: owner.importPairingOffer,
                ),
                _PairingAction(
                  label: 'Save Pairing Acceptance',
                  enabled: enabled,
                  action: owner.savePairingAcceptance,
                ),
                _PairingAction(
                  label: 'Import Pairing Acceptance',
                  enabled: enabled,
                  action: owner.importPairingAcceptance,
                ),
                _PairingAction(
                  label: 'Codes Match',
                  enabled: enabled && owner.sas != null,
                  action: owner.confirmCodesMatch,
                ),
                _PairingAction(
                  label: 'Save Key Delivery',
                  enabled: enabled,
                  action: owner.saveKeyDelivery,
                ),
                _PairingAction(
                  label: 'Import Key Delivery',
                  enabled: enabled,
                  action: owner.importKeyDelivery,
                ),
                _PairingAction(
                  label: 'Save Pairing Acknowledgement',
                  enabled: enabled,
                  action: owner.savePairingAcknowledgement,
                ),
                _PairingAction(
                  label: 'Import Pairing Acknowledgement',
                  enabled: enabled,
                  action: owner.importPairingAcknowledgement,
                ),
                _PairingAction(
                  label: 'Resume Pairing',
                  enabled: enabled,
                  action: owner.resumePairing,
                ),
                _PairingAction(
                  label: 'Discard Pairing',
                  enabled: enabled && owner.pendingTransaction,
                  action: owner.discardPairing,
                ),
              ],
            ),
            const SizedBox(height: 12),
            const Text('Pairing files are transferred manually.'),
            const Text('Compare codes on both recognized devices.'),
            const Text('Ongoing synchronization is not available.'),
            const Text('Device revocation and key rotation are not available.'),
          ],
        );
      },
    );
  }
}

final class _PairingAction extends StatelessWidget {
  const _PairingAction({
    required this.label,
    required this.enabled,
    required this.action,
  });

  final String label;
  final bool enabled;
  final Future<void> Function() action;

  @override
  Widget build(BuildContext context) {
    return OutlinedButton(
      onPressed: enabled ? () => unawaited(action()) : null,
      child: Text(label),
    );
  }
}

final class _PairingValue extends StatelessWidget {
  const _PairingValue({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SizedBox(width: 110, child: Text(label)),
          Expanded(child: SelectableText(value)),
        ],
      ),
    );
  }
}

String _statusText(AtlasVaultTrustedPairingPresentationStatus status) {
  return switch (status) {
    AtlasVaultTrustedPairingPresentationStatus.hidden =>
      'Trusted-device pairing is ready for an explicit action.',
    AtlasVaultTrustedPairingPresentationStatus.ready =>
      'Create this device identity to begin.',
    AtlasVaultTrustedPairingPresentationStatus.identityReady =>
      'This device identity is ready.',
    AtlasVaultTrustedPairingPresentationStatus.offerReady =>
      'The pairing offer is ready to save.',
    AtlasVaultTrustedPairingPresentationStatus.offerSaved =>
      'The pairing offer was saved.',
    AtlasVaultTrustedPairingPresentationStatus.acceptanceReady =>
      'The pairing acceptance is ready to save.',
    AtlasVaultTrustedPairingPresentationStatus.acceptanceSaved =>
      'The pairing acceptance was saved.',
    AtlasVaultTrustedPairingPresentationStatus.codesReady =>
      'Compare the code on both devices.',
    AtlasVaultTrustedPairingPresentationStatus.codesConfirmed =>
      'The comparison code was confirmed.',
    AtlasVaultTrustedPairingPresentationStatus.deliveryReady =>
      'Encrypted key delivery is ready to save.',
    AtlasVaultTrustedPairingPresentationStatus.deliverySaved =>
      'Encrypted key delivery was saved.',
    AtlasVaultTrustedPairingPresentationStatus.acknowledgementReady =>
      'The signed acknowledgement is ready to save.',
    AtlasVaultTrustedPairingPresentationStatus.acknowledgementSaved =>
      'The signed acknowledgement was saved.',
    AtlasVaultTrustedPairingPresentationStatus.completed =>
      'Trusted-device pairing completed.',
    AtlasVaultTrustedPairingPresentationStatus.cancelled =>
      'The pairing file operation was cancelled.',
    AtlasVaultTrustedPairingPresentationStatus.migrationRequired =>
      'Plaintext private data must be migrated before pairing.',
    AtlasVaultTrustedPairingPresentationStatus.existingVault =>
      'This device already has an AtlasVault.',
    AtlasVaultTrustedPairingPresentationStatus.unavailable =>
      'Trusted-device pairing is unavailable.',
    AtlasVaultTrustedPairingPresentationStatus.recoveryRequired =>
      'Trusted-device pairing requires recovery.',
    AtlasVaultTrustedPairingPresentationStatus.failed =>
      'Trusted-device pairing failed.',
  };
}
