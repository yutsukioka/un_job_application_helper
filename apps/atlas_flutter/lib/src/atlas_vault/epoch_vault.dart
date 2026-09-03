part of 'sync_queue.dart';

Never _epochFail([String code = 'ATLAS_EPOCH_ROTATION_REJECTED']) =>
    throw rotation.AtlasVaultRotationException(code);
Map<String, Object?> _epochCopy(Map<String, Object?> s) =>
    _object(jsonDecode(jsonEncode(s)));
List<Map<String, Object?>> _epochRows(Object? value) =>
    (value! as List).map(_object).toList();

final class _EpochComponentFile extends _EncryptedQueueFile {
  _EpochComponentFile(this.owner, this.name)
    : super(owner._file.file, owner._key, kind: 'epoch-component');
  final AtlasVaultEpochVault owner;
  final String name;
  @override
  Future<Map<String, Object?>> read(Map<String, Object?> fallback) async =>
      _epochCopy(
        _object(_object((await owner._load())['components'])[name] ?? fallback),
      );
  @override
  Future<void> write(
    Map<String, Object?> value, {
    FutureOr<void> Function()? beforeReplace,
  }) async {
    final s = await owner._load();
    _object(s['components']);
    (s['components']! as Map)[name] = _epochCopy(value);
    if (name == 'history' && value['status'] != 'ACTIVE') {
      s['status'] = 'RECOVERY_PENDING';
    }
    await owner._file.write(s, beforeReplace: beforeReplace);
  }
}

/// Single-owner D087 journal. The encrypted commit contains all active components.
final class AtlasVaultEpochVault {
  AtlasVaultEpochVault(
    Directory directory, {
    required Uint8List storageKey,
    required String deviceID,
    required List<Map<String, Object?>> registry,
    required String accountID,
    required String vaultID,
    required int keyEpoch,
    required String stateRoot,
  }) : _key = Uint8List.fromList(storageKey),
       _registry = _epochRows(jsonDecode(jsonEncode(registry))),
       _context = {
         'account_id': _commitmentIdentifier(accountID),
         'vault_id': _commitmentIdentifier(vaultID),
         'device_id': _commitmentIdentifier(deviceID),
         'key_epoch': _commitmentSequence(keyEpoch),
         'state_root': _commitmentHex(stateRoot),
         'registry_root': AtlasVaultRevocation.registryRoot(registry),
       },
       _file = _EpochPublication(
         File('${directory.path}/activation'),
         storageKey,
       ) {
    if (!registry.any((e) => e['device_id'] == deviceID)) _epochFail();
  }
  final Uint8List _key;
  final List<Map<String, Object?>> _registry;
  final Map<String, Object?> _context;
  final _EpochPublication _file;
  bool _busy = false;
  Future<T> _run<T>(Future<T> Function() action) async {
    if (_busy) _epochFail();
    _busy = true;
    try {
      return await action();
    } on rotation.AtlasVaultRotationException {
      rethrow;
    } catch (_) {
      _epochFail();
    } finally {
      _busy = false;
    }
  }

  Future<Map<String, Object?>> _verify(Map<String, Object?> proof) =>
      rotation.AtlasVaultEpochRotation.verify(
        proof,
        registry: _registry,
        accountID: _context['account_id']! as String,
        vaultID: _context['vault_id']! as String,
        previousEpoch: _context['key_epoch']! as int,
        stateRoot: _context['state_root']! as String,
      );
  Future<Map<String, Object?>> _record(Map<String, Object?> record) async {
    _exact(record, {'format', 'version', 'status', 'transition_id', 'proof'});
    if (record['format'] != 'atlasvault-activation-record' ||
        record['version'] is! int ||
        record['version'] != 1 ||
        record['status'] != 'ACTIVATION_ACCEPTED' ||
        record['transition_id'] != _object(record['proof'])['root']) {
      _epochFail();
    }
    return _verify(_object(record['proof']));
  }

  AtlasVaultKeyEpochRing _ring(Map<String, Object?> s) {
    final keys = _object(s['keys']);
    if (keys.isEmpty || keys.length > 32) _epochFail();
    return AtlasVaultKeyEpochRing.fromEntries(
      currentKeyEpoch: s['epoch']! as int,
      keys: {
        for (final e in keys.entries)
          int.parse(e.key): _base64(e.value, exactLength: 32),
      },
    );
  }

  Future<Map<String, Object?>> _load() async {
    final s = await _file.read({});
    _exact(s, {
      'context',
      'status',
      'epoch',
      'registry',
      'recipients',
      'keys',
      'components',
      'journal',
      'generation',
    });
    if (jsonEncode(_canonicalValue(s['context'])) !=
            jsonEncode(_canonicalValue(_context)) ||
        ![
          'ACTIVE',
          'ACTIVATION_PENDING',
          'REVOKED',
          'RECOVERY_PENDING',
          'CATCH_UP_PENDING',
          'CLEANUP_PENDING',
        ].contains(s['status'])) {
      _epochFail();
    }
    AtlasVaultRevocation.registryRoot(_epochRows(s['registry']));
    _ring(s);
    _exact(_object(s['components']), {'history', 'outbox', 'inbox'});
    if (s['journal'] != null && _object(s['journal'])['kind'] == 'CATCH_UP') {
      final j = _object(s['journal']);
      if (!['ACTIVE', 'CATCH_UP_PENDING'].contains(j['phase'])) _epochFail();
      final bridges = await _verifyEpochBridges(
        _epochBridgeRecords(_object(_object(s['components'])['history'])),
        _registry,
        _context,
      );
      if (j['phase'] == 'ACTIVE') {
        if (bridges.isEmpty) _epochFail();
        final plan = _object(bridges.last['plan']);
        if (s['epoch'] != plan['new_epoch'] ||
            AtlasVaultRevocation.registryRoot(_epochRows(s['registry'])) !=
                plan['resulting_registry_root'] ||
            jsonEncode(s['recipients']) != jsonEncode(plan['recipients']))
          _epochFail();
      }
      return s;
    }
    if (s['journal'] != null) {
      final j = _object(s['journal']),
          proof = _object(_object(s['journal'])['proof']);
      if (![
        'PREPARED',
        'BACKEND_SUBMITTED',
        'BACKEND_ACCEPTED',
        'LOCAL_PUBLISHING',
        'ACTIVE',
        'RECOVERY_PENDING',
      ].contains(j['phase'])) {
        _epochFail();
      }
      final v = await _verify(proof);
      if (j['record'] != null) {
        await _record(_object(j['record']));
        if (jsonEncode(_canonicalValue(_object(j['record'])['proof'])) !=
            jsonEncode(_canonicalValue(proof))) {
          _epochFail();
        }
      }
      if (j['phase'] == 'ACTIVE' &&
          (s['epoch'] != v['new_epoch'] ||
              jsonEncode(_canonicalValue(s['registry'])) !=
                  jsonEncode(_canonicalValue(v['registry'])) ||
              jsonEncode(s['recipients']) != jsonEncode(v['recipients']) ||
              jsonEncode(
                    _canonicalValue(
                      _object(
                        _object(s['components'])['history'],
                      )['epoch_bridge'],
                    ),
                  ) !=
                  jsonEncode(_canonicalValue(proof)))) {
        _epochFail();
      }
    }
    return s;
  }

  AtlasVaultGuardedSyncState _history(Map<String, Object?> s) {
    final c = _object(_object(_object(s['components'])['history'])['context']);
    if ([
      'account_id',
      'vault_id',
      'key_epoch',
    ].any((k) => c[k] != _context[k])) {
      _epochFail();
    }
    return AtlasVaultGuardedSyncState(
      file: _file.file,
      encryptionKey: _key,
      accountId: c['account_id']! as String,
      vaultId: c['vault_id']! as String,
      collectionId: c['collection_id']! as String,
      keyEpoch: c['key_epoch']! as int,
      trustedSigner: _base64(c['signing_public_b64'], exactLength: 32),
      rotationRegistry: _registry,
    ).._store = _EpochComponentFile(this, 'history');
  }

  Future<void> _active(Map<String, Object?> s) async {
    if (['CATCH_UP_PENDING', 'CLEANUP_PENDING'].contains(s['status']))
      _epochFail('ATLAS_${s['status']}');
    if (s['status'] == 'REVOKED') _epochFail('ATLAS_DEVICE_REVOKED');
    if (s['status'] == 'ACTIVATION_PENDING') {
      _epochFail('ATLAS_ACTIVATION_PENDING');
    }
    if (s['status'] != 'ACTIVE' ||
        (await _history(s).recovery())['status'] != 'ACTIVE') {
      _epochFail('ATLAS_RECOVERY_PENDING');
    }
  }

  Future<void> initialize(
    Map<int, Uint8List> keys, {
    required AtlasVaultGuardedSyncState history,
    AtlasVaultDurableEncryptedOutbox? outbox,
    AtlasVaultDurableEncryptedInbox? inbox,
  }) => _run(() async {
    if (await _file.file.exists()) _epochFail();
    final h = await history._load(),
        views = _epochRows((await history._load())['views']);
    if (h['status'] != 'ACTIVE' ||
        views.isEmpty ||
        views.last['root'] != _context['state_root'] ||
        [
          'account_id',
          'vault_id',
          'key_epoch',
        ].any((k) => _object(h['context'])[k] != _context[k])) {
      _epochFail('ATLAS_RECOVERY_PENDING');
    }
    AtlasVaultKeyEpochRing.fromEntries(
      currentKeyEpoch: _context['key_epoch']! as int,
      keys: keys,
    );
    await outbox?.pendingOperations();
    await inbox?.pendingOperations();
    await _file.write({
      'context': _context,
      'status': 'ACTIVE',
      'epoch': _context['key_epoch'],
      'registry': _registry,
      'recipients':
          _registry
              .where((e) => e['state'] == 'ACTIVE')
              .map((e) => e['device_id']! as String)
              .toList()
            ..sort(),
      'keys': {for (final e in keys.entries) '${e.key}': base64Encode(e.value)},
      'components': {
        'history': h,
        'outbox':
            await outbox?._store.read(_outboxDefault()) ?? _outboxDefault(),
        'inbox': await inbox?._store.read(_inboxDefault()) ?? _inboxDefault(),
      },
      'journal': null,
      'generation': 1,
    });
  });
  Future<Map<String, Object?>> observation() => _run(() async {
    final s = await _load(), h = await _history(await _load()).checkpoint();
    return {
      'status': s['status'],
      'key_epoch': s['epoch'],
      'registry_root': AtlasVaultRevocation.registryRoot(
        _epochRows(s['registry']),
      ),
      'recipients': s['recipients'],
      'state_root': h['cursor'],
      'sequence': h['sequence'],
      'generation': s['generation'],
      'journal_phase': s['journal'] == null
          ? null
          : _object(s['journal'])['phase'],
      'recipient_commitment': _sha256Hex(
        Uint8List.fromList([
          ...ascii.encode('atlasvault-active-recipients-v1\n'),
          ...rotation.rotationCanonical({'recipients': s['recipients']}),
        ]),
      ),
    };
  });
  Future<void> _prepare(Map<String, Object?> proof) async {
    final s = await _load();
    await _active(s);
    await _verify(proof);
    if ((await _history(s).checkpoint())['cursor'] !=
        _object(proof['plan'])['state_root']) {
      _epochFail('ATLAS_RECOVERY_PENDING');
    }
    if (s['journal'] != null &&
        _object(_object(s['journal'])['proof'])['root'] != proof['root']) {
      _epochFail('ATLAS_EPOCH_CONFLICT');
    }
    s['journal'] = {
      'phase': 'PREPARED',
      'proof': _epochCopy(proof),
      'record': null,
    };
    await _file.write(s);
  }

  Future<void> prepareRotation(Map<String, Object?> proof) =>
      _run(() => _prepare(proof));
  Future<void> beginActivation(Map<String, Object?> proof) => _run(() async {
    await _prepare(proof);
    final s = await _load();
    s['status'] = 'ACTIVATION_PENDING';
    (s['journal']! as Map)['phase'] = 'BACKEND_SUBMITTED';
    await _file.write(s);
  });
  Future<bool> acceptRotation(
    Map<String, Object?> proof, {
    required Map<String, Object?> acceptedRecord,
    required Uint8List agreementPrivateKey,
  }) => acceptRotationForTesting(
    proof,
    acceptedRecord: acceptedRecord,
    agreementPrivateKey: agreementPrivateKey,
  );
  Future<bool> acceptRotationForTesting(
    Map<String, Object?> proof, {
    required Map<String, Object?> acceptedRecord,
    required Uint8List agreementPrivateKey,
    Future<void> Function(String)? checkpoint,
  }) => _run(() async {
    final s = await _load(), result = await _record(acceptedRecord);
    if (jsonEncode(_canonicalValue(acceptedRecord['proof'])) !=
        jsonEncode(_canonicalValue(proof))) {
      _epochFail();
    }
    if (s['status'] == 'REVOKED') _epochFail('ATLAS_DEVICE_REVOKED');
    if (s['status'] == 'RECOVERY_PENDING' ||
        (await _history(s).recovery())['status'] != 'ACTIVE') {
      _epochFail('ATLAS_RECOVERY_PENDING');
    }
    if (s['journal'] != null &&
        _object(_object(s['journal'])['proof'])['root'] != proof['root']) {
      _epochFail('ATLAS_EPOCH_CONFLICT');
    }
    if (s['journal'] != null && _object(s['journal'])['phase'] == 'ACTIVE') {
      return false;
    }
    s['status'] = 'ACTIVATION_PENDING';
    s['journal'] = {
      'phase': 'BACKEND_ACCEPTED',
      'proof': _epochCopy(proof),
      'record': _epochCopy(acceptedRecord),
    };
    await _file.write(s);
    await checkpoint?.call('backend_accepted');
    final device = _context['device_id']! as String;
    if (!(result['recipients']! as List).contains(device)) {
      s['status'] = 'REVOKED';
      await _file.write(s);
      _epochFail('ATLAS_DEVICE_REVOKED');
    }
    final d = _epochRows(
          proof['deliveries'],
        ).firstWhere((d) => d['device_id'] == device),
        plan = _object(proof['plan']);
    final opened = await openAtlasVaultEpochHPKEV2(
      recipientPrivateKey: agreementPrivateKey,
      sealed: AtlasVaultEpochHPKESealedVaultKeyV2(
        keyEpoch: d['key_epoch']! as int,
        encapsulatedKey: _base64(d['encapsulated_key_b64'], exactLength: 32),
        ciphertext: _base64(d['ciphertext_b64'], exactLength: 48),
      ),
      context: Uint8List.fromList(
        ascii.encode(
          'atlasvault-rotation-delivery-v1:${rotation.AtlasVaultEpochRotation.binding(plan)}:$device',
        ),
      ),
      minimumKeyEpoch: result['new_epoch']! as int,
    );
    final staged = _epochCopy(s);
    (staged['components']! as Map)['history'] = await _history(
      s,
    )._stageEpoch(proof);
    (staged['keys']! as Map)['${opened.keyEpoch}'] = base64Encode(
      opened.vaultKey,
    );
    staged.addAll({
      'epoch': result['new_epoch'],
      'registry': result['registry'],
      'recipients': result['recipients'],
      'generation': (s['generation']! as int) + 1,
      'status': 'ACTIVE',
    });
    _ring(staged);
    (staged['journal']! as Map)['phase'] = 'ACTIVE';
    (s['journal']! as Map)['phase'] = 'LOCAL_PUBLISHING';
    await _file.write(s);
    await checkpoint?.call('local_publishing');
    await _file.write(
      staged,
      beforeReplace: () => checkpoint?.call('before_local_commit'),
    );
    await checkpoint?.call('after_local_commit');
    return true;
  });
  Future<void> queueOperation(AtlasVaultEncryptedPatchOperation operation) =>
      _run(() async {
        final s = await _load();
        await _active(s);
        if (operation.envelope.keyEpoch != s['epoch'] ||
            operation.authorDeviceId != _context['device_id']) {
          _epochFail('ATLAS_EPOCH_WRITE_REJECTED');
        }
        final outbox = AtlasVaultDurableEncryptedOutbox(
          _file.file,
          encryptionKey: _key,
        ).._store = _EpochComponentFile(this, 'outbox');
        await outbox.enqueue(operation);
      });
  Future<List<AtlasVaultEncryptedPatchOperation>> pendingOperations() => _run(
    () async => (AtlasVaultDurableEncryptedOutbox(
      _file.file,
      encryptionKey: _key,
    ).._store = _EpochComponentFile(this, 'outbox')).pendingOperations(),
  );
  Future<Map<String, Object?>> delivery(String recipient) => _run(() async {
    final s = await _load();
    await _active(s);
    if (s['journal'] == null ||
        _object(s['journal'])['phase'] != 'ACTIVE' ||
        !(s['recipients']! as List).contains(recipient)) {
      _epochFail('ATLAS_DEVICE_REVOKED');
    }
    if (_object(s['journal'])['kind'] == 'CATCH_UP') {
      if (recipient != _context['device_id'])
        _epochFail('ATLAS_DEVICE_DELIVERY_REJECTED');
      return _epochCopy(
        _object(_epochRows(_object(s['journal'])['packets']).last['wrapper']),
      );
    }
    return _epochCopy(
      _epochRows(
        _object(_object(s['journal'])['proof'])['deliveries'],
      ).firstWhere((d) => d['device_id'] == recipient),
    );
  });
  Future<int> compareEvidence(List<Map<String, Object?>> peer) =>
      _run(() async => _history(await _load()).compareEvidence(peer));
  Future<Map<String, Object?>> recovery() => _run(() async {
    final s = await _load(), result = await _history(await _load()).recovery();
    if (result['status'] == 'ACTIVE' && s['status'] != 'ACTIVE')
      result.addAll({'status': s['status'], 'reason': 'ATLAS_${s['status']}'});
    return result;
  });
  Future<Map<String, Object?>?> pendingActivation() => _run(() async {
    final s = await _load();
    if (['REVOKED', 'RECOVERY_PENDING'].contains(s['status']) ||
        (await _history(s).recovery())['status'] != 'ACTIVE') {
      _epochFail('ATLAS_RECOVERY_PENDING');
    }
    return s['journal'] == null || _object(s['journal'])['phase'] == 'ACTIVE'
        ? null
        : _epochCopy(_object(_object(s['journal'])['proof']));
  });
  Future<Map<String, Object?>> createCommitment(
    Uint8List opaqueState, {
    required SimpleKeyPair signingKey,
  }) => _run(() async {
    final s = await _load();
    await _active(s);
    if (s['journal'] == null || _object(s['journal'])['phase'] != 'ACTIVE') {
      _epochFail();
    }
    final history = _history(s),
        prior = (await _history(s).exportEvidence()).last;
    final collection = await AtlasVaultSignedStateCommitment.sign(
      opaqueState,
      collectionId:
          _object(
                _object(_object(s['components'])['history'])['context'],
              )['collection_id']!
              as String,
      sequence: (prior['sequence']! as int) + 1,
      previousRoot: prior['collection_root']! as String,
      signingKey: signingKey,
    );
    final view = await AtlasVaultAuthenticatedStateView.sign({
      'format': 'atlasvault-authenticated-state-view',
      'version': 2,
      'account_id': _context['account_id'],
      'vault_id': _context['vault_id'],
      'sequence': collection.sequence,
      'previous_root': prior['root'],
      'collection_root': collection.root,
      'registry_root': AtlasVaultRevocation.registryRoot(
        _epochRows(s['registry']),
      ),
      'previous_registry_root': prior['registry_root'],
      'key_epoch': s['epoch'],
    }, signingKey);
    await history.ingest(
      view,
      _epochRows(s['registry']),
      collection.toJson(),
      opaqueState,
    );
    return {'view': view, 'collection': collection.toJson()};
  });
  Future<AtlasVaultOpaqueCiphertextEnvelope> seal(
    String kind,
    Uint8List plaintext, {
    required String objectID,
    required String revision,
    required SimpleKeyPair signingKey,
  }) => _run(() async {
    final s = await _load();
    await _active(s);
    final ring = _ring(s);
    if (!['patch', 'snapshot'].contains(kind) ||
        plaintext.length > 1024 * 1024) {
      _epochFail();
    }
    final metadata = {
      'format': 'atlasvault-epoch-ciphertext',
      'version': 1,
      'account_id': _context['account_id'],
      'vault_id': _context['vault_id'],
      'key_epoch': s['epoch'],
      'device_id': _context['device_id'],
      'kind': kind,
      'object_id': _identifier(objectID),
      'revision': _identifier(revision),
    };
    final aad = rotation.rotationCanonical(metadata),
        aead = AesGcm.with256bits(),
        nonce = aead.newNonce();
    final key = await ring.deriveRecordKey(
      keyEpoch: s['epoch']! as int,
      vaultId: _context['vault_id']! as String,
      recordId: objectID,
    );
    final box = await aead.encrypt(
          plaintext,
          secretKey: SecretKey(key),
          nonce: nonce,
          aad: aad,
        ),
        ciphertext = [...box.cipherText, ...box.mac.bytes];
    final message = [
      ...ascii.encode('atlasvault-epoch-ciphertext-signature-v1\u0000'),
      ...aad,
      ...nonce,
      ...ciphertext,
    ];
    final signature = await Ed25519().sign(message, keyPair: signingKey);
    final entry = _epochRows(
      s['registry'],
    ).firstWhere((e) => e['device_id'] == _context['device_id']);
    if (!await Ed25519().verify(
      message,
      signature: Signature(
        signature.bytes,
        publicKey: SimplePublicKey(
          _base64(entry['signing_public_b64'], exactLength: 32),
          type: KeyPairType.ed25519,
        ),
      ),
    )) {
      _epochFail();
    }
    return AtlasVaultOpaqueCiphertextEnvelope.fromJson({
      'format': 'atlasvault-opaque-ciphertext-envelope',
      'version': 1,
      'object_id': objectID,
      'revision': revision,
      'parent_revision': null,
      'key_epoch': s['epoch'],
      'nonce_b64': base64Encode(nonce),
      'ciphertext_b64': base64Encode(ciphertext),
      'aad_b64': base64Encode(aad),
      'signature_b64': base64Encode(signature.bytes),
      'tombstone': false,
      'content_sha256': _sha256Hex(Uint8List.fromList(ciphertext)),
    });
  });
  Future<Uint8List> open(AtlasVaultOpaqueCiphertextEnvelope envelope) =>
      _run(() async {
        final s = await _load(),
            ring = _ring(await _load()),
            raw = envelope.toJson();
        final aad = _base64(raw['aad_b64']),
            m = _object(jsonDecode(ascii.decode(_base64(raw['aad_b64']))));
        _exact(m, {
          'format',
          'version',
          'account_id',
          'vault_id',
          'key_epoch',
          'device_id',
          'kind',
          'object_id',
          'revision',
        });
        if (m['format'] != 'atlasvault-epoch-ciphertext' ||
            m['version'] is! int ||
            m['version'] != 1 ||
            !['patch', 'snapshot'].contains(m['kind']) ||
            ['account_id', 'vault_id'].any((k) => m[k] != _context[k]) ||
            m['key_epoch'] != envelope.keyEpoch ||
            m['object_id'] != envelope.objectId ||
            m['revision'] != envelope.revision ||
            base64Encode(rotation.rotationCanonical(m)) != base64Encode(aad)) {
          _epochFail();
        }
        if (envelope.keyEpoch > (_context['key_epoch']! as int) &&
            !(s['recipients']! as List).contains(m['device_id'])) {
          _epochFail('ATLAS_DEVICE_REVOKED');
        }
        final nonce = _base64(raw['nonce_b64'], exactLength: 12),
            ciphertext = _base64(raw['ciphertext_b64'], minimumLength: 16);
        final entry = _registry.firstWhere(
          (e) => e['device_id'] == m['device_id'],
        );
        if (!await Ed25519().verify(
          [
            ...ascii.encode('atlasvault-epoch-ciphertext-signature-v1\u0000'),
            ...aad,
            ...nonce,
            ...ciphertext,
          ],
          signature: Signature(
            _base64(raw['signature_b64'], exactLength: 64),
            publicKey: SimplePublicKey(
              _base64(entry['signing_public_b64'], exactLength: 32),
              type: KeyPairType.ed25519,
            ),
          ),
        )) {
          _epochFail();
        }
        final key = await ring.deriveRecordKey(
          keyEpoch: envelope.keyEpoch,
          vaultId: _context['vault_id']! as String,
          recordId: envelope.objectId,
        );
        return Uint8List.fromList(
          await AesGcm.with256bits().decrypt(
            SecretBox(
              ciphertext.sublist(0, ciphertext.length - 16),
              nonce: nonce,
              mac: Mac(ciphertext.sublist(ciphertext.length - 16)),
            ),
            secretKey: SecretKey(key),
            aad: aad,
          ),
        );
      });
}
