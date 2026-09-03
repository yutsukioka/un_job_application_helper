part of 'sync_queue.dart';

/// Atomic admission, terminal anchors and manual recovery, with one owner per file.
final class AtlasVaultGuardedSyncState {
  AtlasVaultGuardedSyncState({
    required File file,
    required Uint8List encryptionKey,
    required String accountId,
    required String vaultId,
    required String collectionId,
    required int keyEpoch,
    required Uint8List trustedSigner,
    this._rotationRegistry,
  }) : _store = _EncryptedQueueFile(
         file,
         encryptionKey,
         kind: 'guarded-sync-state-v1',
       ),
       _public = Uint8List.fromList(trustedSigner),
       _context = {
         'account_id': _commitmentIdentifier(accountId),
         'vault_id': _commitmentIdentifier(vaultId),
         'collection_id': _commitmentIdentifier(collectionId),
         'key_epoch': _commitmentSequence(keyEpoch),
         'signing_public_b64': base64Encode(trustedSigner),
       } {
    if (trustedSigner.length != 32) _viewFail();
  }
  _EncryptedQueueFile _store;
  final List<Map<String, Object?>>? _rotationRegistry;
  final Uint8List _public;
  final Map<String, Object?> _context;
  bool _busy = false;
  Future<T> _run<T>(Future<T> Function() operation) async {
    if (_busy) _viewFail();
    _busy = true;
    try {
      return await operation();
    } on AtlasVaultStateViewException {
      rethrow;
    } catch (_) {
      _viewFail();
    } finally {
      _busy = false;
    }
  }

  Future<T> _checked<T>(Future<T> Function() operation) async {
    try {
      return await operation();
    } on AtlasVaultStateViewException {
      rethrow;
    } catch (_) {
      _viewFail();
    }
  }

  Future<List<Map<String, Object?>>> _chain(
    List<Map<String, Object?>> raw, [
    List<Map<String, Object?>> proofs = const [],
  ]) async {
    if (raw.length > _viewLimit) _viewFail('ATLAS_HISTORY_LIMIT');
    final views = <Map<String, Object?>>[];
    var previous = _zeroRoot, registry = _emptyRegistryRoot;
    var epoch = _context['key_epoch']! as int, public = _public;
    Map<String, Object?>? plan;
    for (var i = 0; i < raw.length; i++) {
      for (final proof in proofs) {
        final candidate = _object(proof['plan']);
        if (previous == candidate['state_root'] &&
            epoch == candidate['previous_epoch']) {
          plan = candidate;
          epoch = candidate['new_epoch'] as int;
          public = _bridgePublic(proof);
        }
      }
      final v = await AtlasVaultAuthenticatedStateView._verify(raw[i], public);
      if (['account_id', 'vault_id'].any((k) => v[k] != _context[k]) ||
          v['key_epoch'] != epoch) {
        _viewFail();
      }
      if (plan != null &&
          epoch == plan['new_epoch'] &&
          v['registry_root'] != plan['resulting_registry_root']) {
        _viewFail();
      }
      if (v['sequence'] != i + 1 ||
          v['previous_root'] != previous ||
          v['previous_registry_root'] != registry) {
        _viewFail();
      }
      views.add(v);
      previous = v['root']! as String;
      registry = v['registry_root']! as String;
    }
    return views;
  }

  List<Map<String, Object?>> _views(Object? value) =>
      (value! as List).map(_object).toList();
  Uint8List _bridgePublic(Map<String, Object?> proof) => base64Decode(
    _views(proof['registry']).firstWhere(
          (e) => e['device_id'] == proof['rotation_signer_device_id'],
        )['signing_public_b64']!
        as String,
  );
  Future<List<Map<String, Object?>>> _bridge(Map<String, Object?> state) async {
    final records = _epochBridgeRecords(state);
    if (records.isNotEmpty && _rotationRegistry == null) _viewFail();
    final proofs = await _verifyEpochBridges(
      records,
      _rotationRegistry ?? [],
      _context,
    );
    final roots = _views(state['views']).map((v) => v['root']).toList();
    var position = -1;
    for (final proof in proofs) {
      final found = roots.indexOf(_object(proof['plan'])['state_root']);
      if (found < 0 || found < position) _viewFail();
      position = found;
    }
    return proofs;
  }

  Future<Map<String, Object?>> _stageEpoch(Map<String, Object?> proof) async {
    final s = await _load();
    await _active(s);
    if (s['epoch_bridge'] != null ||
        _views(s['views']).isEmpty ||
        _views(s['views']).last['root'] !=
            _object(proof['plan'])['state_root']) {
      _viewFail('ATLAS_RECOVERY_PENDING');
    }
    s['epoch_bridge'] = proof;
    await _bridge(s);
    await _chain(_views(s['views']), await _bridge(s));
    return s;
  }

  Future<Map<String, Object?>> _load() async {
    final s = await _store.read({});
    _exact(s, {
      'context',
      'views',
      'records',
      'cases',
      'status',
      if (s.containsKey('epoch_bridge')) 'epoch_bridge',
      if (s.containsKey('epoch_bridges')) 'epoch_bridges',
    });
    final context = _object(s['context']);
    _exact(context, _context.keys.toSet());
    if (_context.entries.any((e) => context[e.key] != e.value) ||
        ![
          'ACTIVE',
          'MANUAL_REQUIRED',
          'RECOVERY_PENDING',
        ].contains(s['status']) ||
        (s['cases']! as List).length > 8 ||
        _object(s['records']).length > _viewLimit) {
      _viewFail();
    }
    s['views'] = await _chain(_views(s['views']), await _bridge(s));
    return s;
  }

  Future<void> initialize() => _run(() async {
    if (await _store.file.exists()) _viewFail();
    await _store.write({
      'context': _context,
      'views': [],
      'records': <String, Object?>{},
      'cases': [],
      'status': 'ACTIVE',
    });
  });
  Future<void> _active(Map<String, Object?> s) async {
    if (s['status'] != 'ACTIVE') _viewFail('ATLAS_RECOVERY_PENDING');
    if ((s['cases']! as List).length == 8) {
      s['status'] = 'RECOVERY_PENDING';
      await _store.write(s);
      _viewFail('ATLAS_RECOVERY_PENDING');
    }
  }

  Future<T> automaticSync<T>(Future<T> Function() operation) => _run(() async {
    await _active(await _load());
    return operation();
  });
  Future<Map<String, Object?>> checkpoint() => _run(() async {
    final s = await _load(),
        views = _views(s['views']),
        records = _object(s['records']),
        keys = records.keys.toList()..sort();
    return {
      'sequence': views.length,
      'cursor': views.isEmpty ? _zeroRoot : views.last['root'],
      'records': [for (final k in keys) records[k]],
    };
  });
  Future<List<Map<String, Object?>>> exportEvidence() =>
      _run(() async => _views((await _load())['views']));
  Future<Never> _alarm(
    Map<String, Object?> s,
    String reason,
    List<Map<String, Object?>> peer, [
    String? registry,
  ]) async {
    (s['cases']! as List).add({
      'reason': reason,
      'local': _views(s['views']),
      'peer': peer,
      'presented_registry_root': registry,
      'disposition': null,
      'rejected_branch': null,
    });
    s['status'] = 'MANUAL_REQUIRED';
    await _store.write(s);
    _viewFail(reason);
  }

  Future<Map<String, Object?>> evidence() => _run(() async {
    final s = await _load(), cases = s['cases']! as List;
    final c = cases.isEmpty
        ? {'local': s['views'], 'peer': []}
        : _object(cases.last);
    return {'local': c['local'], 'peer': c['peer']};
  });
  Future<Map<String, Object?>> recovery() => _run(() async {
    final s = await _load(),
        cases = s['cases']! as List,
        c = cases.isEmpty ? null : _object(cases.last);
    List<Map<String, Object?>> metadata(Object? v) => _views(v)
        .map(
          (v) => {
            for (final k in ['sequence', 'root', 'registry_root', 'key_epoch'])
              k: v[k],
          },
        )
        .toList();
    return {
      'status': s['status'],
      'reason': c?['reason'],
      'local': metadata(c?['local'] ?? s['views']),
      'peer': metadata(c?['peer'] ?? []),
      'disposition': c?['disposition'],
      'rejected_branch': c?['rejected_branch'],
      'presented_registry_root': c?['presented_registry_root'],
    };
  });
  Future<String> resolve(
    String disposition,
    String localRoot,
    String peerRoot,
  ) => _run(() async {
    final s = await _load();
    if (s['status'] != 'MANUAL_REQUIRED' ||
        ![
          'retain_accepted',
          'select_peer',
          'keep_blocked',
        ].contains(disposition)) {
      _viewFail();
    }
    final cases = s['cases']! as List,
        c = _object(cases.last),
        local = _views(c['local']),
        peer = _views(c['peer']);
    if (localRoot != (local.isEmpty ? _zeroRoot : local.last['root']) ||
        peerRoot != (peer.isEmpty ? _zeroRoot : peer.last['root'])) {
      _viewFail();
    }
    final safe =
        c['reason'] == 'ATLAS_ROLLBACK_REJECTED' &&
        peer.isNotEmpty &&
        peer.every(
          (v) =>
              (v['sequence']! as int) <= local.length &&
              v['root'] == local[(v['sequence']! as int) - 1]['root'],
        );
    c['disposition'] = disposition;
    c['rejected_branch'] = {
      'retain_accepted': 'peer',
      'select_peer': 'local',
      'keep_blocked': null,
    }[disposition];
    cases[cases.length - 1] = c;
    s['status'] = disposition == 'retain_accepted' && safe
        ? 'ACTIVE'
        : 'RECOVERY_PENDING';
    await _store.write(s);
    return s['status']! as String;
  });
  Future<int> compareEvidence(List<Map<String, Object?>> rawPeer) {
    final copied = rawPeer
        .take(_viewLimit + 1)
        .map((v) => Map<String, Object?>.of(v))
        .toList();
    return _run(() async {
      final s = await _load();
      await _active(s);
      var signed = <Map<String, Object?>>[];
      try {
        return await _checked(() async {
          if (copied.length > _viewLimit) _viewFail('ATLAS_HISTORY_LIMIT');
          final bridge = await _bridge(s);
          final authorities = {
            _context['key_epoch']: _public,
            for (final p in bridge)
              _object(p['plan'])['new_epoch']: _bridgePublic(p),
          };
          for (final v in copied) {
            signed.add(
              await AtlasVaultAuthenticatedStateView._verify(
                v,
                authorities[v['key_epoch']] ?? _public,
              ),
            );
          }
          final peer = await _chain(signed, bridge), local = _views(s['views']);
          if (peer.isEmpty || local.isEmpty) {
            _viewFail('ATLAS_CHECKPOINT_REQUIRED');
          }
          final common = local.length < peer.length
              ? local.length
              : peer.length;
          for (var i = 0; i < common; i++) {
            if (local[i]['root'] != peer[i]['root']) {
              _viewFail('ATLAS_STATE_EQUIVOCATION');
            }
          }
          return common;
        });
      } on AtlasVaultStateViewException catch (e) {
        return _alarm(s, e.code, signed);
      }
    });
  }

  Future<bool> ingest(
    Map<String, Object?> rawView,
    List<Map<String, Object?>> rawRegistry,
    Map<String, Object?> rawCollection,
    Uint8List rawState,
  ) {
    final view = Map<String, Object?>.of(rawView),
        collection = Map<String, Object?>.of(rawCollection),
        registry = rawRegistry
            .take(_viewLimit + 1)
            .map((r) => Map<String, Object?>.of(r))
            .toList();
    final oversized = rawState.length > 1024 * 1024,
        bytes = oversized ? Uint8List(0) : Uint8List.fromList(rawState);
    return _run(() async {
      final s = await _load();
      await _active(s);
      var peer = <Map<String, Object?>>[];
      String? registryDigest;
      try {
        final duplicate = await _checked(() async {
          final bridges = await _bridge(s);
          final bridge = bridges.isEmpty ? null : bridges.last;
          final epoch = bridge == null
              ? _context['key_epoch']
              : _object(bridge['plan'])['new_epoch'];
          final public = bridge == null ? _public : _bridgePublic(bridge);
          final v = await AtlasVaultAuthenticatedStateView._verify(
            view,
            public,
          );
          peer = [v];
          if (['account_id', 'vault_id'].any((k) => v[k] != _context[k]) ||
              v['key_epoch'] != epoch) {
            _viewFail();
          }
          registryDigest = bridge == null
              ? AtlasVaultAuthenticatedStateView.registryRoot(registry)
              : AtlasVaultRevocation.registryRoot(registry);
          if (v['registry_root'] != registryDigest) {
            _viewFail('ATLAS_REGISTRY_SUBSTITUTION');
          }
          if (oversized) _viewFail('ATLAS_HISTORY_LIMIT');
          final c = AtlasVaultSignedStateCommitment.fromJson(collection);
          if (c.collectionId != _context['collection_id'] ||
              c.sequence != v['sequence'] ||
              c.root != v['collection_root'] ||
              c._value['state_sha256'] != _commitmentDigest(bytes)) {
            _viewFail();
          }
          if (!await Ed25519().verify(
            _rootSignatureMessage(c.root),
            signature: Signature(
              _base64(c._value['signature_b64'], exactLength: 64),
              publicKey: SimplePublicKey(public, type: KeyPairType.ed25519),
            ),
          )) {
            _viewFail();
          }
          final n = v['sequence']! as int, views = _views(s['views']);
          if (n <= views.length && v['root'] != views[n - 1]['root']) {
            _viewFail('ATLAS_STATE_EQUIVOCATION');
          }
          if (n < views.length) _viewFail('ATLAS_ROLLBACK_REJECTED');
          if (n == views.length) return true;
          if (n > _viewLimit) _viewFail('ATLAS_HISTORY_LIMIT');
          final previous = views.isEmpty ? null : views.last;
          if (n != views.length + 1 ||
              v['previous_root'] != (previous?['root'] ?? _zeroRoot) ||
              v['previous_registry_root'] !=
                  (previous?['registry_root'] ?? _emptyRegistryRoot) ||
              c.previousRoot != (previous?['collection_root'] ?? _zeroRoot)) {
            _viewFail();
          }
          final body = _object(jsonDecode(utf8.decode(bytes)));
          _exact(body, {'format', 'version', 'route', 'records'});
          if (body['format'] != 'atlasvault-guarded-collection' ||
              body['version'] is! int ||
              body['version'] != 1 ||
              !['patch', 'snapshot', 'compaction'].contains(body['route'])) {
            _viewFail();
          }
          final raw = body['records']! as List;
          if (raw.length > _viewLimit) _viewFail('ATLAS_HISTORY_LIMIT');
          final records = <String, Object?>{};
          for (final item in raw) {
            final r = AtlasVaultOpaqueCiphertextEnvelope.fromJson(
              _object(item),
            );
            if (r.version != 1 ||
                records.containsKey(r.objectId) ||
                (r.keyEpoch != v['key_epoch'] &&
                    (_object(s['records'])[r.objectId] == null ||
                        _object(
                              _object(s['records'])[r.objectId],
                            )['envelope_sha256'] !=
                            _sha256Hex(_canonicalJsonBytes(r.toJson()))))) {
              _viewFail();
            }
            records[r.objectId] = {
              'object_id': r.objectId,
              'revision': r.revision,
              'content_sha256': r.contentSha256,
              'envelope_sha256': _sha256Hex(_canonicalJsonBytes(r.toJson())),
              'tombstone': r.tombstone,
            };
          }
          for (final entry in _object(s['records']).entries) {
            final old = _object(entry.value), candidate = records[entry.key];
            if (old['tombstone'] == true &&
                jsonEncode(_canonicalValue(old)) !=
                    jsonEncode(_canonicalValue(candidate))) {
              _viewFail('ATLAS_TOMBSTONE_RESURRECTION');
            }
            if (candidate == null) _viewFail('ATLAS_STALE_STATE');
          }
          s['records'] = records;
          s['views'] = [...views, v];
          return false;
        });
        if (duplicate) return false;
      } on AtlasVaultStateViewException catch (e) {
        return _alarm(s, e.code, peer, registryDigest);
      }
      await _store.write(s);
      return true;
    });
  }
}
