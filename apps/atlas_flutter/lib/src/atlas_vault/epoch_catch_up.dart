part of 'sync_queue.dart';

List<Map<String, Object?>> _epochBridgeRecords(Map<String, Object?> s) {
  if (s.containsKey('epoch_bridges') && s.containsKey('epoch_bridge'))
    _epochFail();
  final result = s.containsKey('epoch_bridges')
      ? _epochRows(s['epoch_bridges'])
      : s['epoch_bridge'] == null
      ? <Map<String, Object?>>[]
      : [_object(s['epoch_bridge'])];
  if (result.length > 32) _epochFail();
  return result;
}

Future<List<Map<String, Object?>>> _verifyEpochBridges(
  List<Map<String, Object?>> records,
  List<Map<String, Object?>> registry,
  Map<String, Object?> context,
) async {
  var epoch = context['key_epoch'] as int;
  final result = <Map<String, Object?>>[];
  for (final raw in records) {
    final selective = raw.containsKey('wrapper'),
        p = raw.containsKey('wrapper') ? _object(raw['proof']) : raw;
    final plan = _object(p['plan']);
    final verified = selective
        ? await delivery.AtlasVaultDeviceDelivery.verify(
            raw,
            registry: registry,
            accountID: context['account_id'] as String,
            vaultID: context['vault_id'] as String,
            previousEpoch: epoch,
            stateRoot: plan['state_root'] as String,
            activationID: p['activation_id'] as String,
            recipientDeviceID: p['recipient_device_id'] as String,
          )
        : await rotation.AtlasVaultEpochRotation.verify(
            raw,
            registry: registry,
            accountID: context['account_id'] as String,
            vaultID: context['vault_id'] as String,
            previousEpoch: epoch,
            stateRoot: plan['state_root'] as String,
          );
    result.add({
      'plan': plan,
      'registry': p['registry'],
      'rotation_signer_device_id': p['rotation_signer_device_id'],
    });
    registry = _epochRows(verified['registry']);
    epoch = verified['new_epoch'] as int;
  }
  return result;
}

final class _EpochPublication extends _EncryptedQueueFile {
  _EpochPublication(File file, Uint8List key)
    : anchor = _EncryptedQueueFile(
        File('${file.parent.path}/activation-recovery'),
        key,
        kind: 'epoch-recovery-v1',
      ),
      super(file, key, kind: 'epoch-activation-v1');
  final _EncryptedQueueFile anchor;
  String digest(Map<String, Object?> state) =>
      _commitmentDigest(_canonicalJsonBytes(state));
  Future<Map<String, Object?>> record() async {
    final r = await anchor.read({});
    _exact(r, {'state', 'sha256'});
    final s = _object(r['state']);
    if (digest(s) != r['sha256'])
      _epochFail('ATLAS_PUBLICATION_RECOVERY_REQUIRED');
    return s;
  }

  Future<void> enable() async {
    if (!await anchor.file.exists()) {
      final s = await super.read({});
      await anchor.write({'state': s, 'sha256': digest(s)});
    }
  }

  @override
  Future<Map<String, Object?>> read(Map<String, Object?> fallback) async {
    final s = await super.read(fallback);
    if (await anchor.file.exists() && digest(s) != digest(await record()))
      _epochFail('ATLAS_PUBLICATION_RECOVERY_REQUIRED');
    return s;
  }

  @override
  Future<void> write(
    Map<String, Object?> state, {
    FutureOr<void> Function()? beforeReplace,
  }) async {
    if (await anchor.file.exists()) {
      await anchor.write({
        'state': state,
        'sha256': digest(state),
      }, beforeReplace: beforeReplace);
      await super.write(state);
    } else {
      await super.write(state, beforeReplace: beforeReplace);
    }
  }

  Future<void> recover() async => super.write(await record());
}

extension AtlasVaultEpochCatchUp on AtlasVaultEpochVault {
  Future<bool> catchUp(
    List<Map<String, Object?>> packets, {
    required String currentActivationID,
    required Uint8List agreementPrivateKey,
  }) => catchUpForTesting(
    packets,
    currentActivationID: currentActivationID,
    agreementPrivateKey: agreementPrivateKey,
  );
  Future<bool> catchUpForTesting(
    List<Map<String, Object?>> packets, {
    required String currentActivationID,
    required Uint8List agreementPrivateKey,
    FutureOr<void> Function(String)? checkpoint,
  }) => _run(() async {
    final s = await _load();
    if ([
          'REVOKED',
          'RECOVERY_PENDING',
          'CLEANUP_PENDING',
        ].contains(s['status']) ||
        (await _history(s).recovery())['status'] != 'ACTIVE')
      _epochFail('ATLAS_RECOVERY_PENDING');
    final j = s['journal'] == null
        ? <String, Object?>{}
        : _object(s['journal']);
    if (j['kind'] == 'CATCH_UP' &&
        j['phase'] == 'ACTIVE' &&
        j['target_id'] == currentActivationID) {
      if (jsonEncode(_canonicalValue(j['packets'])) !=
          jsonEncode(_canonicalValue(packets)))
        _epochFail('ATLAS_EPOCH_CONFLICT');
      return false;
    }
    await _file.enable();
    final prior = j['kind'] == 'CATCH_UP' && j['phase'] != 'ACTIVE'
        ? j['prior_journal']
        : s['journal'];
    s['status'] = 'CATCH_UP_PENDING';
    s['journal'] = {
      'kind': 'CATCH_UP',
      'phase': 'CATCH_UP_PENDING',
      'target_id': currentActivationID,
      'packets': <Object?>[],
      'prior_journal': prior,
    };
    await _file.write(s);
    await checkpoint?.call('catch_up_pending');
    if (packets.isEmpty || packets.length > 32) _epochFail();
    final staged = _epochCopy(s),
        h = _object(_object(staged['components'])['history']);
    if (h['status'] != 'ACTIVE' || _epochRows(h['views']).isEmpty)
      _epochFail('ATLAS_RECOVERY_PENDING');
    var registry = _epochRows(s['registry']), epoch = s['epoch'] as int;
    final root = _epochRows(h['views']).last['root'] as String,
        bridges = _epochBridgeRecords(h);
    Map<String, Object?> verified = {};
    for (final packet in packets) {
      if (packet['format'] == 'atlasvault-activation-record')
        _epochFail('ATLAS_PER_DEVICE_PROOF_REQUIRED');
      final p = _object(packet['proof']),
          w = _object(packet['wrapper']),
          plan = _object(p['plan']);
      verified = await delivery.AtlasVaultDeviceDelivery.verify(
        packet,
        registry: registry,
        accountID: _context['account_id'] as String,
        vaultID: _context['vault_id'] as String,
        previousEpoch: epoch,
        stateRoot: root,
        activationID: p['activation_id'] as String,
        recipientDeviceID: _context['device_id'] as String,
      );
      final opened = await openAtlasVaultEpochHPKEV2(
        recipientPrivateKey: agreementPrivateKey,
        sealed: AtlasVaultEpochHPKESealedVaultKeyV2(
          keyEpoch: w['key_epoch'] as int,
          encapsulatedKey: _base64(w['encapsulated_key_b64'], exactLength: 32),
          ciphertext: _base64(w['ciphertext_b64'], exactLength: 48),
        ),
        context: Uint8List.fromList(
          ascii.encode(
            'atlasvault-rotation-delivery-v1:${rotation.AtlasVaultEpochRotation.binding(plan)}:${_context['device_id']}',
          ),
        ),
        minimumKeyEpoch: verified['new_epoch'] as int,
      );
      (staged['keys'] as Map)['${opened.keyEpoch}'] = base64Encode(
        opened.vaultKey,
      );
      bridges.add(_epochCopy(packet));
      registry = _epochRows(verified['registry']);
      epoch = verified['new_epoch'] as int;
      await checkpoint?.call('verified_epoch');
    }
    if (_object(packets.last['proof'])['activation_id'] != currentActivationID)
      _epochFail('ATLAS_EPOCH_CONFLICT');
    h.remove('epoch_bridge');
    h['epoch_bridges'] = bridges;
    (staged['components'] as Map)['history'] = h;
    await _verifyEpochBridges(bridges, _registry, _context);
    staged.addAll({
      'epoch': epoch,
      'registry': registry,
      'recipients': verified['recipients'],
      'generation': (s['generation'] as int) + 1,
      'status': 'ACTIVE',
      'journal': {
        'kind': 'CATCH_UP',
        'phase': 'ACTIVE',
        'target_id': currentActivationID,
        'packets': packets.map(_epochCopy).toList(),
        'prior_journal': null,
      },
    });
    _ring(staged);
    await _file.write(
      staged,
      beforeReplace: () => checkpoint?.call('before_local_commit'),
    );
    await checkpoint?.call('after_local_commit');
    return true;
  });
  Future<void> recoverPublication() => _run(() async {
    await _file.recover();
    await _history(await _load()).recovery();
  });
  Future<List<int>> availableEpochs() => _run(
    () async =>
        _object((await _load())['keys']).keys.map(int.parse).toList()..sort(),
  );
  Future<void> cleanupEpochs({
    required Set<int> retainEpochs,
    required Future<void> Function(int) deleteEpoch,
    required Future<bool> Function(int) containsEpoch,
  }) => cleanupEpochsForTesting(
    retainEpochs: retainEpochs,
    deleteEpoch: deleteEpoch,
    containsEpoch: containsEpoch,
  );
  Future<void> cleanupEpochsForTesting({
    required Set<int> retainEpochs,
    required Future<void> Function(int) deleteEpoch,
    required Future<bool> Function(int) containsEpoch,
    FutureOr<void> Function(String)? checkpoint,
  }) => _run(() async {
    final s = await _load();
    if (!['ACTIVE', 'CLEANUP_PENDING'].contains(s['status']) ||
        (await _history(s).recovery())['status'] != 'ACTIVE')
      _epochFail('ATLAS_RECOVERY_PENDING');
    final keys = _object(s['keys']),
        available = keys.keys.map(int.parse).toSet();
    if (!retainEpochs.contains(s['epoch']) ||
        !available.containsAll(retainEpochs))
      _epochFail();
    final outbox = AtlasVaultDurableEncryptedOutbox(
      _file.file,
      encryptionKey: _key,
    ).._store = _EpochComponentFile(this, 'outbox');
    if ((await outbox.pendingOperations()).any(
      (op) => !retainEpochs.contains(op.envelope.keyEpoch),
    ))
      _epochFail('ATLAS_CLEANUP_PENDING');
    await _file.enable();
    s['status'] = 'CLEANUP_PENDING';
    await _file.write(s);
    await checkpoint?.call('cleanup_pending');
    final removed = available.difference(retainEpochs).toList()..sort();
    for (final epoch in removed) {
      await deleteEpoch(epoch);
      if (await containsEpoch(epoch)) _epochFail('ATLAS_CLEANUP_PENDING');
      keys.remove('$epoch');
    }
    s['keys'] = keys;
    s['status'] = 'ACTIVE';
    await _file.write(s);
  });
}
