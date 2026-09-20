part of 'sync_queue.dart';

const _viewFields = [
  'account_id',
  'vault_id',
  'sequence',
  'previous_root',
  'collection_root',
  'registry_root',
  'previous_registry_root',
  'key_epoch',
];
const _viewFormat = 'atlasvault-authenticated-state-view';
const _viewLimit = 256;
final _emptyRegistryRoot = _sha256Hex(
  ascii.encode('atlasvault-registry-root-v1\n'),
);

final class AtlasVaultStateViewException implements Exception {
  const AtlasVaultStateViewException(this.code);
  final String code;
  @override
  String toString() => code;
}

Never _viewFail([String code = 'ATLAS_STATE_VIEW_REJECTED']) =>
    throw AtlasVaultStateViewException(code);

final class AtlasVaultAuthenticatedStateView {
  static String registryRoot(List<Map<String, Object?>> entries) {
    try {
      if (entries.isEmpty || entries.length > _viewLimit) {
        _viewFail('ATLAS_REGISTRY_SUBSTITUTION');
      }
      final checked = <String, String>{};
      for (final e in entries) {
        _exact(e, {'device_id', 'descriptor_sha256'});
        final id = _commitmentHex(e['device_id']),
            descriptor = _commitmentHex(e['descriptor_sha256']);
        if (checked.containsKey(id)) _viewFail('ATLAS_REGISTRY_SUBSTITUTION');
        checked[id] = descriptor;
      }
      final ids = checked.keys.toList()..sort();
      return _sha256Hex(
        ascii.encode(
          'atlasvault-registry-root-v1\n${ids.map((id) => '$id:${checked[id]}\n').join()}',
        ),
      );
    } on AtlasVaultStateViewException {
      rethrow;
    } catch (_) {
      _viewFail();
    }
  }

  static String _root(Map<String, Object?> unsigned) {
    _exact(unsigned, {'format', 'version', ..._viewFields});
    if (unsigned['format'] != _viewFormat ||
        unsigned['version'] is! int ||
        unsigned['version'] != 2) {
      _viewFail();
    }
    for (final f in _viewFields) {
      if (f == 'account_id' || f == 'vault_id') {
        _commitmentIdentifier(unsigned[f]);
      } else if (f == 'sequence' || f == 'key_epoch') {
        _commitmentSequence(unsigned[f]);
      } else {
        _commitmentHex(unsigned[f]);
      }
    }
    return _sha256Hex(
      ascii.encode(
        'atlasvault-authenticated-state-view-v2\n${_viewFields.map((f) => '${unsigned[f]}\n').join()}',
      ),
    );
  }

  static Uint8List _message(String root) => Uint8List.fromList([
    ...ascii.encode('atlasvault-state-view-signature-v2\x00'),
    for (var i = 0; i < 64; i += 2)
      int.parse(root.substring(i, i + 2), radix: 16),
  ]);

  static Future<Map<String, Object?>> _verify(
    Map<String, Object?> view,
    Uint8List public,
  ) async {
    _exact(view, {
      'format',
      'version',
      ..._viewFields,
      'root',
      'signature_b64',
    });
    final value = Map<String, Object?>.of(view);
    final unsigned = Map<String, Object?>.of(value)
      ..remove('root')
      ..remove('signature_b64');
    final root = _commitmentHex(value['root']);
    if (root != _root(unsigned) ||
        value['signature_b64'] is! String ||
        (value['signature_b64']! as String).length != 88) {
      _viewFail();
    }
    final valid = await Ed25519().verify(
      _message(root),
      signature: Signature(
        _base64(value['signature_b64'], exactLength: 64),
        publicKey: SimplePublicKey(public, type: KeyPairType.ed25519),
      ),
    );
    if (!valid) _viewFail();
    return value;
  }

  static Future<Map<String, Object?>> sign(
    Map<String, Object?> unsigned,
    SimpleKeyPair signingKey,
  ) async {
    try {
      final fields = Map<String, Object?>.of(unsigned), root = _root(unsigned);
      final signature = await Ed25519().sign(
        _message(root),
        keyPair: signingKey,
      );
      return await _verify({
        ...fields,
        'root': root,
        'signature_b64': base64Encode(signature.bytes),
      }, Uint8List.fromList((await signingKey.extractPublicKey()).bytes));
    } on AtlasVaultStateViewException {
      rethrow;
    } catch (_) {
      _viewFail();
    }
  }
}

/// Explicit, bounded evidence exchange. One owner per persisted history path.
final class AtlasVaultAuthenticatedHistory {
  AtlasVaultAuthenticatedHistory({
    required File file,
    required Uint8List encryptionKey,
    required String accountId,
    required String vaultId,
    required String collectionId,
    required int keyEpoch,
    required Uint8List trustedSigner,
  }) : _store = _EncryptedQueueFile(
         file,
         encryptionKey,
         kind: 'authenticated-state-history-v2',
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
  final _EncryptedQueueFile _store;
  final Uint8List _public;
  final Map<String, Object?> _context;
  bool _busy = false;

  Future<T> _exclusive<T>(Future<T> Function() work) async {
    if (_busy) _viewFail();
    _busy = true;
    try {
      return await work();
    } on AtlasVaultStateViewException {
      rethrow;
    } catch (_) {
      _viewFail();
    } finally {
      _busy = false;
    }
  }

  Future<List<Map<String, Object?>>> _chain(
    List<Map<String, Object?>> views,
  ) async {
    if (views.length > _viewLimit) _viewFail('ATLAS_HISTORY_LIMIT');
    final checked = <Map<String, Object?>>[];
    var previous = _zeroRoot, registry = _emptyRegistryRoot;
    for (var i = 0; i < views.length; i++) {
      final v = await AtlasVaultAuthenticatedStateView._verify(
        views[i],
        _public,
      );
      if ([
        'account_id',
        'vault_id',
        'key_epoch',
      ].any((f) => v[f] != _context[f])) {
        _viewFail();
      }
      if (v['sequence'] != i + 1 ||
          v['previous_root'] != previous ||
          v['previous_registry_root'] != registry) {
        _viewFail();
      }
      checked.add(v);
      previous = v['root']! as String;
      registry = v['registry_root']! as String;
    }
    return checked;
  }

  Future<({List<Map<String, Object?>> views, bool blocked})> _load() async {
    final s = await _store.read({});
    _exact(s, {'context', 'views', 'blocked'});
    final context = _object(s['context']);
    _exact(context, _context.keys.toSet());
    if (_context.entries.any((e) => context[e.key] != e.value) ||
        s['blocked'] is! bool ||
        s['views'] is! List) {
      _viewFail();
    }
    final raw = s['views']! as List;
    if (raw.length > _viewLimit) _viewFail('ATLAS_HISTORY_LIMIT');
    return (
      views: await _chain(raw.map(_object).toList()),
      blocked: s['blocked']! as bool,
    );
  }

  Future<void> _save(
    List<Map<String, Object?>> views, {
    bool blocked = false,
  }) => _store.write({'context': _context, 'views': views, 'blocked': blocked});
  Future<void> initialize() => _exclusive(() async {
    if ((await _store.read({})).isNotEmpty || await _store.file.exists()) {
      _viewFail();
    }
    await _save([]);
  });
  Future<List<Map<String, Object?>>> exportEvidence() =>
      _exclusive(() async => (await _load()).views);
  Future<Never> _fork(List<Map<String, Object?>> views) async {
    await _save(views, blocked: true);
    _viewFail('ATLAS_STATE_EQUIVOCATION');
  }

  Future<int> compareEvidence(List<Map<String, Object?>> peer) async {
    if (peer.length > _viewLimit) _viewFail('ATLAS_HISTORY_LIMIT');
    final copied = peer.map((v) => Map<String, Object?>.of(v)).toList();
    return _exclusive(() async {
      final local = await _load();
      if (local.blocked) _viewFail('ATLAS_STATE_EQUIVOCATION');
      final checked = await _chain(copied);
      if (local.views.isEmpty || checked.isEmpty) {
        _viewFail('ATLAS_CHECKPOINT_REQUIRED');
      }
      final common = local.views.length < checked.length
          ? local.views.length
          : checked.length;
      for (var i = 0; i < common; i++) {
        if (local.views[i]['root'] != checked[i]['root']) {
          return _fork(local.views);
        }
      }
      return common;
    });
  }

  Future<bool> observe(
    Map<String, Object?> rawView,
    List<Map<String, Object?>> registry,
    Map<String, Object?> rawCollection,
    Uint8List opaqueState,
  ) async {
    final view = Map<String, Object?>.of(rawView),
        collection = Map<String, Object?>.of(rawCollection);
    final registryDigest = AtlasVaultAuthenticatedStateView.registryRoot(
      registry,
    );
    if (opaqueState.length < 16 || opaqueState.length > _maximumQueueBytes) {
      _viewFail();
    }
    final stateDigest = _commitmentDigest(opaqueState);
    return _exclusive(() async {
      final local = await _load();
      if (local.blocked) _viewFail('ATLAS_STATE_EQUIVOCATION');
      final v = await AtlasVaultAuthenticatedStateView._verify(view, _public);
      if ([
        'account_id',
        'vault_id',
        'key_epoch',
      ].any((f) => v[f] != _context[f])) {
        _viewFail();
      }
      if (v['registry_root'] != registryDigest) {
        _viewFail('ATLAS_REGISTRY_SUBSTITUTION');
      }
      final c = AtlasVaultSignedStateCommitment.fromJson(collection);
      if (c.collectionId != _context['collection_id'] ||
          c.sequence != v['sequence'] ||
          c.root != v['collection_root'] ||
          c._value['state_sha256'] != stateDigest) {
        _viewFail();
      }
      if (!await Ed25519().verify(
        _rootSignatureMessage(c.root),
        signature: Signature(
          _base64(c._value['signature_b64'], exactLength: 64),
          publicKey: SimplePublicKey(_public, type: KeyPairType.ed25519),
        ),
      )) {
        _viewFail();
      }
      final sequence = v['sequence']! as int, views = local.views;
      if (sequence <= views.length &&
          v['root'] != views[sequence - 1]['root']) {
        return _fork(views);
      }
      if (sequence < views.length) _viewFail('ATLAS_ROLLBACK_REJECTED');
      if (sequence == views.length) return false;
      if (views.length == _viewLimit) _viewFail('ATLAS_HISTORY_LIMIT');
      final previous = views.isEmpty ? null : views.last;
      if (sequence != views.length + 1 ||
          v['previous_root'] != (previous?['root'] ?? _zeroRoot) ||
          v['previous_registry_root'] !=
              (previous?['registry_root'] ?? _emptyRegistryRoot) ||
          c.previousRoot != (previous?['collection_root'] ?? _zeroRoot)) {
        _viewFail();
      }
      await _save([...views, v]);
      return true;
    });
  }
}
