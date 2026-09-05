part of 'sync_queue.dart';

extension AtlasVaultEpochEnrollment on AtlasVaultEpochVault {
  Map<String, Object?> _enrollmentContext(Map<String, Object?> s) {
    final history = _object(_object(s['components'])['history']);
    String? activationID;
    var generation = s['epoch'] as int;
    for (final raw in _epochBridgeRecords(history)) {
      if (raw['format'] == 'atlasvault-enrollment-bridge') {
        generation =
            _object(raw['enrollment'])['next_registry_generation'] as int;
      } else {
        final p = _bridgeProof(raw);
        activationID =
            (raw.containsKey('wrapper') ? p['activation_id'] : p['root'])
                as String;
        generation = _object(p['plan'])['new_epoch'] as int;
      }
    }
    if (activationID == null) _epochFail('ATLAS_RUNTIME_PROVISIONING_REQUIRED');
    return {
      'account_id': _context['account_id'],
      'vault_id': _context['vault_id'],
      'key_epoch': s['epoch'],
      'registry_generation': generation,
      'state_root': _stateRoot(s),
      'activation_id': activationID,
    };
  }

  Future<Map<String, Object?>> enrollmentContext() => _run(() async {
    final s = await _load();
    await _active(s);
    return _enrollmentContext(s);
  });

  Future<List<Map<String, Object?>>> enrollmentRegistry() => _run(() async {
    final s = await _load();
    await _active(s);
    return _epochRows(_epochCopy(s)['registry']);
  });

  Future<bool> acceptEnrollment(
    Map<String, Object?> proof, {
    required String confirmedTranscript,
  }) => _run(() async {
    final s = await _load();
    await _active(s);
    final records = _epochBridgeRecords(
      _object(_object(s['components'])['history']),
    );
    for (final raw in records) {
      if (raw['format'] != 'atlasvault-enrollment-bridge') continue;
      final prior = _object(raw['enrollment']);
      if (prior['root'] == proof['root']) {
        if (jsonEncode(_canonicalValue(prior)) !=
                jsonEncode(_canonicalValue(proof)) ||
            prior['transcript_sha256'] != confirmedTranscript) {
          _epochFail('ATLAS_ENROLLMENT_REJECTED');
        }
        return false;
      }
    }
    final registry = await enrollment.AtlasVaultDeviceEnrollment.verify(
      proof,
      registry: _epochRows(s['registry']),
      context: _enrollmentContext(s),
      confirmedTranscript: confirmedTranscript,
      status: s['status'] as String,
    );
    final history = await _history(s)._stageEpoch({
      'format': 'atlasvault-enrollment-bridge',
      'version': 1,
      'enrollment': _epochCopy(proof),
    });
    (s['components'] as Map)['history'] = history;
    s['registry'] = registry;
    s['recipients'] =
        registry
            .where((d) => d['state'] == 'ACTIVE')
            .map((d) => d['device_id'] as String)
            .toList()
          ..sort();
    s['generation'] = (s['generation'] as int) + 1;
    await _file.write(s);
    return true;
  });
}
