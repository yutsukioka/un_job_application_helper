part of 'pairing_transaction.dart';

Map<String, Object?> _pairingEnrollmentContext(Object? value) {
  final c = requireAtlasVaultObject(value, context: 'Enrollment context');
  requireAtlasVaultExactKeys(
    c,
    requiredKeys: const {
      'account_id',
      'vault_id',
      'key_epoch',
      'registry_generation',
      'state_root',
      'activation_id',
    },
    context: 'Enrollment context',
  );
  if (encodeCanonicalJson(c).length > 1024 ||
      c['account_id'] is! String ||
      c['vault_id'] is! String ||
      c['key_epoch'] is! int ||
      (c['key_epoch']! as int) < 1 ||
      c['registry_generation'] is! int ||
      (c['registry_generation']! as int) < 1 ||
      !RegExp(
        r'^[0-9a-f]{64}$',
      ).hasMatch(c['state_root'] is String ? c['state_root']! as String : '') ||
      !RegExp(r'^[0-9a-f]{64}$').hasMatch(
        c['activation_id'] is String ? c['activation_id']! as String : '',
      )) {
    throw const AtlasVaultPairingTransactionException();
  }
  return Map.unmodifiable(c);
}

bool _isEpochDelivery(AtlasVaultPairingArtifact artifact) =>
    artifact.kind == AtlasVaultPairingArtifactKind.delivery &&
    artifact.payload.containsKey('enrollment_delivery');

Map<String, Object?> _epochPacket(AtlasVaultPairingArtifact artifact) =>
    requireAtlasVaultObject(
      artifact.payload['enrollment_delivery'],
      context: 'Enrollment delivery',
    );

extension _EpochPairingTransaction on AtlasVaultTrustedPairingCoordinator {
  Future<Map<String, Object?>> _verifyEpochCeremony(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultPairingArtifact artifact,
    AtlasVaultDeviceIdentity identity,
  ) async {
    final offer = _signedOffer(
      await _requireStaged(AtlasVaultPairingArtifactKind.offer, transaction),
    );
    final acceptance = _signedAcceptance(
      await _requireStaged(
        AtlasVaultPairingArtifactKind.acceptance,
        transaction,
      ),
    );
    final transcript = await atlasVaultPairingTranscriptSha256(
      offer,
      acceptance,
    );
    if (transaction.role != AtlasVaultPairingRole.invitee ||
        transaction.peerDeviceId != offer.offer.inviter.descriptor.deviceId ||
        transaction.localDeviceId != identity.deviceId ||
        acceptance.acceptance.invitee.descriptor.deviceId !=
            identity.deviceId ||
        transaction.transcriptSha256 != _hexBytes(transcript)) {
      throw const AtlasVaultPairingTransactionException();
    }
    final key = await _sessionKeyFor(transaction, identity);
    final supplied = _proof(artifact, 'inviter_proof');
    try {
      final proof = await deriveAtlasVaultPairingProofs(
        sessionKey: key,
        transcriptSha256: transcript,
      );
      try {
        if (!_constantBytes(supplied, proof.inviter)) {
          throw const AtlasVaultPairingTransactionException();
        }
      } finally {
        proof.inviter.fillRange(0, proof.inviter.length, 0);
        proof.invitee.fillRange(0, proof.invitee.length, 0);
      }
    } finally {
      key.fillRange(0, key.length, 0);
      supplied.fillRange(0, supplied.length, 0);
    }
    return sync.AtlasVaultEnrollmentDelivery.ceremonyPins(
      _epochPacket(artifact),
      recipient: identity,
      peer: offer.offer.inviter.descriptor,
      transcript: _hexBytes(transcript),
    );
  }

  Future<AtlasVaultTrustedPairingResult> _importEpochDelivery(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultPairingArtifact artifact,
  ) async {
    if (!_runtime.usesEpochComposition) {
      throw const AtlasVaultPairingTransactionException();
    }
    await _requireLivePairingDeadlineFor(transaction);
    final blocked = _cleanInstallResult(await _cleanInstallProbe());
    if (blocked != null) return blocked;
    final identity = await _requireIdentity();
    try {
      final pins = await _verifyEpochCeremony(transaction, artifact, identity);
      final context = pins['current_context']! as Map;
      _authorizeSensitiveMutation();
      final intent = await _advance(transaction, transaction.stage, {
        'delivery_sha256': await atlasVaultSha256Hex(artifact.canonicalBytes()),
        'bootstrap_sha256': pins['anchor_root'],
        'vault_id': context['vault_id'],
        'key_epoch': context['key_epoch'],
        'staged_artifacts': await _mergedStagedJson(transaction, artifact),
      });
      await _createStaged(artifact);
      return _installEpochInvitee(
        await _advance(intent, AtlasVaultPairingStage.deliveryImported),
      );
    } finally {
      identity.destroy();
    }
  }

  Future<AtlasVaultTrustedPairingResult> _installEpochInvitee(
    AtlasVaultPairingTransaction starting,
  ) async {
    final identity = await _requireIdentity();
    try {
      var transaction = starting;
      final artifact = await _requireStaged(
        AtlasVaultPairingArtifactKind.delivery,
        transaction,
      );
      final packet = _epochPacket(artifact);
      final pins = await _verifyEpochCeremony(transaction, artifact, identity);
      final offer = _signedOffer(
        await _requireStaged(AtlasVaultPairingArtifactKind.offer, transaction),
      );
      final context = pins['current_context']! as Map;
      if (transaction.bootstrapSha256 != pins['anchor_root'] ||
          transaction.vaultId != context['vault_id'] ||
          transaction.keyEpoch != context['key_epoch']) {
        throw const AtlasVaultPairingTransactionException();
      }
      if (!_stageAtLeast(
        transaction,
        AtlasVaultPairingStage.runtimeActivated,
      )) {
        if (_runtime.isActive) {
          // An interrupted activation can only resume the same verified native binding.
          await _runtime.deactivate();
        }
        await _runtime.installPairingEnrollment(
          packet,
          pins: pins,
          trustedSigner: offer.offer.inviter.descriptor.signingPublicKey,
          recipient: identity,
          beforePublish: () async {
            _authorizeSensitiveMutation();
          },
        );
        for (final stage in [
          AtlasVaultPairingStage.storeCreated,
          AtlasVaultPairingStage.keyCreated,
        ]) {
          if (!_stageAtLeast(transaction, stage)) {
            transaction = await _advance(transaction, stage);
          }
        }
        final selected = _selectedVaultStore;
        if (selected == null) {
          throw const AtlasVaultPairingTransactionException();
        }
        final current = await selected.read();
        if (current != null && current != transaction.vaultId) {
          throw const AtlasVaultPairingTransactionException();
        }
        if (current == null) {
          _authorizeSensitiveMutation();
          await selected.create(transaction.vaultId!);
        }
        if (!_stageAtLeast(
          transaction,
          AtlasVaultPairingStage.selectionCommitted,
        )) {
          transaction = await _advance(
            transaction,
            AtlasVaultPairingStage.selectionCommitted,
            {'selection_committed': true},
          );
        }
        _authorizeSensitiveMutation();
        if (!await _activateInstalledVault(transaction.vaultId!)) {
          throw const AtlasVaultPairingTransactionException();
        }
        transaction = await _advance(
          transaction,
          AtlasVaultPairingStage.runtimeActivated,
          {'installed_at': _utc(_now())},
        );
      }
      if (transaction.stage == AtlasVaultPairingStage.runtimeActivated) {
        // The authenticated epoch owner is the registry authority, not the legacy mirror.
        transaction = await _advance(
          transaction,
          AtlasVaultPairingStage.trustCommitted,
        );
      }
      if (transaction.stage == AtlasVaultPairingStage.trustCommitted) {
        final receipt = await sync.AtlasVaultEnrollmentDelivery.acknowledge(
          packet,
          transaction.deliverySha256!,
          identity,
        );
        final ack = _artifact(AtlasVaultPairingArtifactKind.acknowledgement, {
          'enrollment_acknowledgement': receipt,
        });
        final intent = await _advance(transaction, transaction.stage, {
          'acknowledgement_sha256': await atlasVaultSha256Hex(
            ack.canonicalBytes(),
          ),
          'staged_artifacts': await _mergedStagedJson(transaction, ack),
        });
        await _createStaged(ack);
        transaction = await _advance(
          intent,
          AtlasVaultPairingStage.acknowledgementCreated,
        );
      }
      return _result(
        AtlasVaultTrustedPairingDisposition.acknowledgementReady,
        transaction,
        local: identity,
        peerDeviceId: transaction.peerDeviceId,
        trusted: true,
      );
    } finally {
      identity.destroy();
    }
  }

  Future<void> _verifyEpochAcknowledgement(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultPairingArtifact delivery,
    AtlasVaultPairingArtifact ack,
  ) async {
    final acceptance = _signedAcceptance(
      await _requireStaged(
        AtlasVaultPairingArtifactKind.acceptance,
        transaction,
      ),
    );
    final peer = acceptance.acceptance.invitee.descriptor;
    if (peer.deviceId != transaction.peerDeviceId) {
      throw const AtlasVaultPairingTransactionException();
    }
    await sync.AtlasVaultEnrollmentDelivery.verifyAcknowledgement(
      _epochPacket(delivery),
      transaction.deliverySha256!,
      requireAtlasVaultObject(
        ack.payload['enrollment_acknowledgement'],
        context: 'Enrollment acknowledgement',
      ),
      peer,
    );
  }

  Future<AtlasVaultTrustedPairingResult> _completeEpochAcknowledgement(
    AtlasVaultPairingTransaction starting,
  ) async {
    var transaction = starting;
    final delivery = await _requireStaged(
      AtlasVaultPairingArtifactKind.delivery,
      transaction,
    );
    final ack = await _requireStaged(
      AtlasVaultPairingArtifactKind.acknowledgement,
      transaction,
    );
    await _verifyEpochAcknowledgement(transaction, delivery, ack);
    if (transaction.stage == AtlasVaultPairingStage.acknowledgementImported) {
      final offer = _signedOffer(
        await _requireStaged(AtlasVaultPairingArtifactKind.offer, transaction),
      );
      await _consumeReplay(
        transaction.localDeviceId,
        AtlasVaultPairingReplayEntry.fromJson({
          'kind': 'acknowledgement',
          'object_id': transaction.transactionId,
          'transcript_sha256': transaction.transcriptSha256,
          'consumed_at': _utc(_now()),
          'expires_at': offer.offer.expiresAt,
        }),
        acceptExactDuplicate: true,
      );
      transaction = await _advance(
        transaction,
        AtlasVaultPairingStage.acknowledgementConsumed,
      );
    }
    if (transaction.stage == AtlasVaultPairingStage.acknowledgementConsumed) {
      transaction = await _advance(
        transaction,
        AtlasVaultPairingStage.trustCommitted,
      );
    }
    await _clearTransaction(transaction);
    return const AtlasVaultTrustedPairingResult(
      disposition: AtlasVaultTrustedPairingDisposition.completed,
      role: AtlasVaultPairingRole.inviter,
      trusted: true,
    );
  }
}
