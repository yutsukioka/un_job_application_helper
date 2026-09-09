part of 'sync_queue.dart';

/// Reopens a provisioned P7 owner. Missing enrollment never creates new authority.
final class AtlasVaultRuntimeBinding {
  AtlasVaultRuntimeBinding({
    required this.root,
    required this.loadKey,
    required this.createKey,
  });
  final Directory root;
  final Future<Uint8List?> Function(String) loadKey;
  final Future<void> Function(String, Uint8List) createKey;
  String _slot(String vaultID, String kind) =>
      'runtime-$kind-${_sha256Hex(Uint8List.fromList(utf8.encode(vaultID)))}';
  Directory directory(String vaultID) => Directory(
    '${root.path}/${_sha256Hex(Uint8List.fromList(utf8.encode(_identifier(vaultID))))}',
  );

  /// Called only with an already initialized, authenticated P7 publication.
  /// Installation never calls initialize(), creates an account, or signs a root.
  Future<void> provision({
    required AtlasVaultEpochVault owner,
    required Uint8List signingSeed,
    List<Map<String, Object?>>? authenticatedRegistry,
  }) async {
    final s = await owner._run(() => owner._load());
    await owner._active(s);
    final vaultID = owner._context['vault_id']! as String;
    if (owner._file.file.parent.absolute.path !=
            directory(vaultID).absolute.path ||
        signingSeed.length != 32) {
      _epochFail('ATLAS_RUNTIME_BINDING_REJECTED');
    }
    await _checkSigner(owner, signingSeed, s);
    final publicationRegistry = await owner._publicationRegistry(
      s,
      authenticatedRegistry,
    );
    final history = owner._history(s);
    final originalRegistry =
        owner._historyOrigin == null &&
            (await history._bridge(await history._load())).isEmpty
        ? publicationRegistry
        : null;
    final slot = _slot(vaultID, 'binding');
    final file = File('${directory(vaultID).path}/runtime-binding');
    if (await file.exists()) _epochFail('ATLAS_RUNTIME_BINDING_EXISTS');
    Uint8List? existingStorage, existingSigning, bindingKey;
    try {
      existingStorage = await loadKey(_slot(vaultID, 'storage'));
      existingSigning = await loadKey(_slot(vaultID, 'signing'));
      bindingKey = await loadKey(slot);
      // Only an explicitly repeated enrollment of the same authenticated owner
      // may finish interrupted slot creation. Existing conflicting keys are never replaced.
      if ((existingStorage != null &&
              !_sameRuntimeKey(existingStorage, owner._key)) ||
          (existingSigning != null &&
              !_sameRuntimeKey(existingSigning, signingSeed)) ||
          (bindingKey != null && bindingKey.length != 32)) {
        _epochFail('ATLAS_RUNTIME_BINDING_REJECTED');
      }
      if (existingStorage == null) {
        await createKey(_slot(vaultID, 'storage'), owner._key);
      }
      if (existingSigning == null) {
        await createKey(_slot(vaultID, 'signing'), signingSeed);
      }
      if (bindingKey == null) {
        bindingKey = Uint8List.fromList(SecretKeyData.random(length: 32).bytes);
        await createKey(slot, bindingKey);
      }
      await _EncryptedQueueFile(
        file,
        bindingKey,
        kind: 'runtime-binding-v1:$vaultID',
      ).write({
        'format': 'atlasvault-runtime-binding',
        'version': 1,
        'context': owner._context,
        'registry': owner._registry,
        'history_registry': ?originalRegistry,
        'history_origin': ?owner._historyOrigin,
      });
    } finally {
      for (final value in [existingStorage, existingSigning, bindingKey]) {
        value?.fillRange(0, value.length, 0);
      }
    }
  }

  Future<AtlasVaultRuntimeSession> open(String vaultID) async {
    Uint8List? bindingKey, storageKey, seed;
    try {
      bindingKey = await loadKey(_slot(vaultID, 'binding'));
      storageKey = await loadKey(_slot(vaultID, 'storage'));
      seed = await loadKey(_slot(vaultID, 'signing'));
      if (bindingKey?.length != 32 ||
          storageKey?.length != 32 ||
          seed?.length != 32) {
        _epochFail('ATLAS_RUNTIME_PROVISIONING_REQUIRED');
      }
      final record = await _EncryptedQueueFile(
        File('${directory(vaultID).path}/runtime-binding'),
        bindingKey!,
        kind: 'runtime-binding-v1:$vaultID',
      ).read({});
      _exact(record, {
        'format',
        'version',
        'context',
        'registry',
        if (record.containsKey('history_registry')) 'history_registry',
        if (record.containsKey('history_origin')) 'history_origin',
      });
      final c = _object(record['context']);
      _exact(c, {
        'account_id',
        'vault_id',
        'device_id',
        'key_epoch',
        'state_root',
        'registry_root',
        if (record.containsKey('history_origin')) 'history_origin_sha256',
      });
      if (record['format'] != 'atlasvault-runtime-binding' ||
          (record.containsKey('history_origin') &&
              record.containsKey('history_registry')) ||
          record['version'] is! int ||
          record['version'] != 1 ||
          c['vault_id'] != vaultID ||
          c['registry_root'] !=
              AtlasVaultRevocation.registryRoot(
                _epochRows(record['registry']),
              )) {
        _epochFail('ATLAS_RUNTIME_BINDING_REJECTED');
      }
      final owner = AtlasVaultEpochVault(
        directory(vaultID),
        storageKey: storageKey!,
        deviceID: c['device_id']! as String,
        registry: _epochRows(record['registry']),
        accountID: c['account_id']! as String,
        vaultID: vaultID,
        keyEpoch: c['key_epoch']! as int,
        stateRoot: c['state_root']! as String,
        historyOrigin: record['history_origin'] == null
            ? null
            : _object(record['history_origin']),
      );
      if (!_anchorEqual(owner._context, c)) {
        _epochFail('ATLAS_RUNTIME_BINDING_REJECTED');
      }
      final s = await owner._run(() => owner._load());
      if (record.containsKey('history_registry')) {
        owner._runtimePublicationRegistry = _epochRows(
          record['history_registry'],
        );
      }
      await _checkSigner(owner, seed!, s);
      await owner._publicationRegistry(s, null);
      return AtlasVaultRuntimeSession._(owner, Uint8List.fromList(seed));
    } on rotation.AtlasVaultRotationException {
      rethrow;
    } catch (_) {
      _epochFail('ATLAS_RUNTIME_BINDING_REJECTED');
    } finally {
      for (final key in [bindingKey, storageKey, seed]) {
        key?.fillRange(0, key.length, 0);
      }
    }
  }

  Future<void> _checkSigner(
    AtlasVaultEpochVault owner,
    Uint8List seed,
    Map<String, Object?> s,
  ) async {
    final signer = await Ed25519().newKeyPairFromSeed(seed);
    final public = base64Encode((await signer.extractPublicKey()).bytes);
    if (!_epochRows(s['registry']).any(
      (d) =>
          d['device_id'] == owner._context['device_id'] &&
          d['state'] == 'ACTIVE' &&
          d['signing_public_b64'] == public,
    )) {
      _epochFail('ATLAS_DEVICE_REVOKED');
    }
  }

  @override
  String toString() => 'AtlasVaultRuntimeBinding(<redacted>)';
}

bool _sameRuntimeKey(Uint8List a, Uint8List b) {
  if (a.length != 32 || b.length != 32) return false;
  var difference = 0;
  for (var i = 0; i < 32; i++) {
    difference |= a[i] ^ b[i];
  }
  return difference == 0;
}

final class AtlasVaultRuntimeSession {
  AtlasVaultRuntimeSession._(this.owner, this._seed);
  final AtlasVaultEpochVault owner;
  final Uint8List _seed;
  bool _closed = false;
  Future<Map<String, Object?>> enrollmentContext({
    required String deviceID,
    required Uint8List signingPublicKey,
    required Uint8List agreementPublicKey,
  }) => owner._run(() async {
    if (_closed || deviceID != owner._context['device_id']) {
      _epochFail('ATLAS_RUNTIME_LOCKED');
    }
    final s = await owner._load();
    await owner._active(s);
    if (_closed ||
        !_epochRows(s['registry']).any(
          (r) =>
              r['device_id'] == deviceID &&
              r['state'] == 'ACTIVE' &&
              r['signing_public_b64'] == base64Encode(signingPublicKey) &&
              r['agreement_public_b64'] == base64Encode(agreementPublicKey),
        )) {
      _epochFail('ATLAS_DEVICE_REVOKED');
    }
    return owner._enrollmentContext(s);
  });
  Future<List<AtlasVaultRuntimeRecord>> read() {
    if (_closed) _epochFail('ATLAS_RUNTIME_LOCKED');
    return owner.runtimeRecords();
  }

  Future<int> importLegacy({
    required legacy.AtlasVaultLocalStore store,
    required Uint8List vaultKey,
  }) async {
    if (_closed) _epochFail('ATLAS_RUNTIME_LOCKED');
    final signer = await Ed25519().newKeyPairFromSeed(_seed);
    if (_closed) _epochFail('ATLAS_RUNTIME_LOCKED');
    return owner.importLegacyRuntime(
      store: store,
      vaultKey: vaultKey,
      signingKey: signer,
    );
  }

  Future<AtlasVaultEncryptedPatchOperation> commit({
    required AtlasVaultPayloadEnvelope? payload,
    required String objectID,
    String? expectedRevision,
  }) async {
    if (_closed) _epochFail('ATLAS_RUNTIME_LOCKED');
    final signer = await Ed25519().newKeyPairFromSeed(_seed);
    if (_closed) _epochFail('ATLAS_RUNTIME_LOCKED');
    return owner.commitRuntimeRecord(
      payload: payload,
      objectID: objectID,
      expectedRevision: expectedRevision,
      signingKey: signer,
    );
  }

  void close() {
    _closed = true;
    for (final bytes in [
      _seed,
      owner._key,
      owner._file._key,
      owner._file.anchor._key,
    ]) {
      bytes.fillRange(0, bytes.length, 0);
    }
  }

  @override
  String toString() => 'AtlasVaultRuntimeSession(<redacted>)';
}
