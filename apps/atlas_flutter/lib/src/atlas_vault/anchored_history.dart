part of 'sync_queue.dart';

final class AtlasVaultBootstrapException implements Exception {
  const AtlasVaultBootstrapException();
  @override
  String toString() => 'ATLAS_BOOTSTRAP_REJECTED';
}

Never _bootstrapFail() => throw const AtlasVaultBootstrapException();
Map<String, Object?> _anchorCopy(Map<String, Object?> value) =>
    _object(jsonDecode(jsonEncode(value)));
bool _anchorEqual(Object? a, Object? b) =>
    jsonEncode(_canonicalValue(a)) == jsonEncode(_canonicalValue(b));
List<Map<String, Object?>> _anchorRows(Object? value) =>
    (value! as List).map(_object).toList();

final class _AnchoredFile extends _EncryptedQueueFile {
  _AnchoredFile(
    super.file,
    super.key, {
    required this.owner,
    required this.actual,
  }) : super(kind: 'anchor-component');
  final AtlasVaultAnchoredSyncState owner;
  final _EncryptedQueueFile actual;
  Map<String, Object?>? anchor;
  @override
  Future<Map<String, Object?>> read(Map<String, Object?> fallback) async {
    final outer = await actual.read({});
    _exact(outer, {'anchor', 'state'});
    await owner._verifyAnchor(_object(outer['anchor']));
    anchor = _anchorCopy(_object(outer['anchor']));
    return _object(outer['state']);
  }

  @override
  Future<void> write(
    Map<String, Object?> state, {
    FutureOr<void> Function()? beforeReplace,
  }) {
    if (anchor == null) _viewFail();
    return actual.write({
      'anchor': anchor,
      'state': state,
    }, beforeReplace: beforeReplace);
  }
}

/// Recipient pins come from the confirmed ceremony, never from an incoming anchor.
final class AtlasVaultAnchoredSyncState extends AtlasVaultGuardedSyncState {
  AtlasVaultAnchoredSyncState({
    required File file,
    required Uint8List encryptionKey,
    required Map<String, Object?> trust,
  }) : _trust = _anchorCopy(trust),
       super(
         file: file,
         encryptionKey: encryptionKey,
         accountId: trust['account_id']! as String,
         vaultId: trust['vault_id']! as String,
         collectionId: trust['collection_id']! as String,
         keyEpoch: trust['key_epoch']! as int,
         trustedSigner: _base64(trust['trusted_signer_b64'], exactLength: 32),
         rotationRegistry: _anchorRows(_anchorCopy(trust)['registry']),
       ) {
    _exact(_trust, {
      'account_id',
      'vault_id',
      'collection_id',
      'key_epoch',
      'trusted_signer_b64',
      'registry',
      'anchor_root',
      'recipient_device_id',
      'confirmed_transcript',
      'current_context',
    });
    _store = _AnchoredFile(
      file,
      encryptionKey,
      owner: this,
      actual: _EncryptedQueueFile(
        file,
        encryptionKey,
        kind: 'anchored-history-v1:${_sha256Hex(_canonicalJsonBytes(_trust))}',
      ),
    );
  }
  final Map<String, Object?> _trust;
  Map<String, Object?>? _publicationAnchor;
  Future<Map<String, Object?>> publicationOrigin() => _run(() async {
    await _active(await _load());
    return _anchorCopy({
      'format': 'atlasvault-anchored-publication-origin',
      'version': 1,
      'context': _context,
      'pins': {
        for (final k in [
          'anchor_root',
          'recipient_device_id',
          'confirmed_transcript',
          'current_context',
        ])
          k: _trust[k],
      },
      'registry': _rotationRegistry,
      'anchor': _publicationAnchor,
    });
  });
  @override
  Map<String, Object?> _bridgeContext() => {
    ..._context,
    for (final k in [
      'registry_generation',
      'activation_id',
      'issuer_device_id',
    ])
      k: _object(_publicationAnchor!['checkpoint'])[k],
  };
  @override
  Future<void> initialize() async => _bootstrapFail();

  Future<void> _verifyAnchor(Map<String, Object?> anchor) async {
    _exact(anchor, {'checkpoint', 'enrollment', 'view'});
    final p = _object(anchor['checkpoint']), e = _object(anchor['enrollment']);
    _exact(p, {
      ...enrollment.AtlasVaultDeviceEnrollment.contextFields,
      'format',
      'version',
      'registry_root',
      'sequence',
      'collection_id',
      'collection_root',
      'collection_sha256',
      'enrollment_root',
      'recipient_device_id',
      'recipient_agreement_sha256',
      'transcript_sha256',
      'issuer_device_id',
      'signature_algorithm',
      'root',
      'signature_b64',
    });
    final context = _object(_trust['current_context']);
    _exact(context, enrollment.AtlasVaultDeviceEnrollment.contextFields);
    if (p['format'] != 'atlasvault-history-bootstrap' ||
        p['version'] is! int ||
        p['version'] != 1 ||
        p['signature_algorithm'] != 'Ed25519') {
      _bootstrapFail();
    }
    for (final k in context.keys) {
      if (p[k] != context[k] || (context[k] is int && p[k] is! int)) {
        _bootstrapFail();
      }
    }
    final registry = _rotationRegistry!;
    if (p['recipient_device_id'] != _trust['recipient_device_id'] ||
        p['recipient_device_id'] != e['target_device_id'] ||
        p['recipient_agreement_sha256'] != e['target_agreement_sha256'] ||
        p['transcript_sha256'] != _trust['confirmed_transcript'] ||
        p['transcript_sha256'] != e['transcript_sha256'] ||
        p['enrollment_root'] != e['root'] ||
        p['registry_generation'] != e['next_registry_generation'] ||
        p['key_epoch'] != e['key_epoch'] ||
        p['activation_id'] != e['activation_id'] ||
        p['registry_root'] != e['resulting_registry_root'] ||
        p['registry_root'] != AtlasVaultRevocation.registryRoot(registry)) {
      _bootstrapFail();
    }
    final prior = registry
        .where((r) => r['device_id'] != p['recipient_device_id'])
        .toList();
    final checked = await enrollment.AtlasVaultDeviceEnrollment.verify(
      e,
      registry: prior,
      context: {for (final k in context.keys) k: e[k]},
      confirmedTranscript: _trust['confirmed_transcript']! as String,
      status: 'ACTIVE',
    );
    final sorted = [...registry]
      ..sort(
        (a, b) =>
            (a['device_id']! as String).compareTo(b['device_id']! as String),
      );
    if (!_anchorEqual(checked, sorted)) _bootstrapFail();
    final issuer = prior.firstWhere(
      (r) => r['device_id'] == p['issuer_device_id'] && r['state'] == 'ACTIVE',
    );
    if (!_anchorEqual(
      _base64(issuer['signing_public_b64'], exactLength: 32),
      _public,
    )) {
      _bootstrapFail();
    }
    for (final k in [
      'state_root',
      'collection_root',
      'collection_sha256',
      'root',
    ]) {
      _commitmentHex(p[k]);
    }
    _commitmentSequence(p['sequence']);
    final unsigned = {...p}
      ..remove('root')
      ..remove('signature_b64');
    final root = _sha256Hex([
      ...ascii.encode('atlasvault-history-bootstrap-v1\n'),
      ..._canonicalJsonBytes(unsigned),
    ]);
    if (p['root'] != root ||
        root != _trust['anchor_root'] ||
        !await Ed25519().verify(
          [
            ...ascii.encode('atlasvault-history-bootstrap-signature-v1\x00'),
            for (var i = 0; i < 64; i += 2)
              int.parse(root.substring(i, i + 2), radix: 16),
          ],
          signature: Signature(
            _base64(p['signature_b64'], exactLength: 64),
            publicKey: SimplePublicKey(_public, type: KeyPairType.ed25519),
          ),
        )) {
      _bootstrapFail();
    }
    final view = await AtlasVaultAuthenticatedStateView._verify(
      _object(anchor['view']),
      _public,
    );
    for (final k in [
      'account_id',
      'vault_id',
      'key_epoch',
      'sequence',
      'registry_root',
      'collection_root',
    ]) {
      if (view[k] != p[k]) _bootstrapFail();
    }
    if (view['root'] != p['state_root'] ||
        [
          'account_id',
          'vault_id',
          'key_epoch',
          'collection_id',
        ].any((k) => p[k] != _context[k])) {
      _bootstrapFail();
    }
    _origin = _anchorCopy(view);
    _publicationAnchor = _anchorCopy(anchor);
  }

  Future<bool> bootstrap(Map<String, Object?> packet) async {
    try {
      final encoded = packet['opaque_b64'];
      if (encoded is! String || encoded.length > 1398104) _bootstrapFail();
      final args = _anchorCopy(packet);
      return await _run(() async {
        _exact(args, {
          'checkpoint',
          'enrollment',
          'registry',
          'current_context',
          'recipient_device_id',
          'confirmed_transcript',
          'view',
          'collection',
          'opaque_b64',
        });
        for (final k in [
          'registry',
          'current_context',
          'recipient_device_id',
          'confirmed_transcript',
        ]) {
          if (!_anchorEqual(args[k], _trust[k])) _bootstrapFail();
        }
        final anchor = {
          for (final k in ['checkpoint', 'enrollment', 'view']) k: args[k],
        };
        await _verifyAnchor(anchor);
        final p = _object(args['checkpoint']),
            c = AtlasVaultSignedStateCommitment.fromJson(
              _object(args['collection']),
            );
        final bytes = _base64(args['opaque_b64'], minimumLength: 16);
        if (bytes.length > 1024 * 1024 ||
            c.collectionId != p['collection_id'] ||
            c.sequence != p['sequence'] ||
            c.root != p['collection_root'] ||
            c._value['state_sha256'] != p['collection_sha256'] ||
            _commitmentDigest(bytes) != p['collection_sha256'] ||
            !await Ed25519().verify(
              _rootSignatureMessage(c.root),
              signature: Signature(
                _base64(c._value['signature_b64'], exactLength: 64),
                publicKey: SimplePublicKey(_public, type: KeyPairType.ed25519),
              ),
            )) {
          _bootstrapFail();
        }
        final body = _object(jsonDecode(utf8.decode(bytes)));
        _exact(body, {'format', 'version', 'route', 'records'});
        if (body['format'] != 'atlasvault-guarded-collection' ||
            body['version'] is! int ||
            body['version'] != 1 ||
            !['patch', 'snapshot', 'compaction'].contains(body['route'])) {
          _bootstrapFail();
        }
        final rows = body['records']! as List;
        if (rows.length > _viewLimit) _bootstrapFail();
        final records = <String, Object?>{};
        for (final raw in rows) {
          final r = AtlasVaultOpaqueCiphertextEnvelope.fromJson(_object(raw));
          if (r.version != 1 ||
              r.keyEpoch > (_context['key_epoch']! as int) ||
              records.containsKey(r.objectId)) {
            _bootstrapFail();
          }
          records[r.objectId] = {
            'object_id': r.objectId,
            'revision': r.revision,
            'content_sha256': r.contentSha256,
            'envelope_sha256': _sha256Hex(_canonicalJsonBytes(r.toJson())),
            'tombstone': r.tombstone,
          };
        }
        final store = _store as _AnchoredFile;
        if (await store.file.exists()) {
          await _active(await _load());
          if (!_anchorEqual(store.anchor, anchor)) _bootstrapFail();
          return false;
        }
        store.anchor = _anchorCopy(anchor);
        await store.write({
          'context': _context,
          'views': [anchor['view']],
          'records': records,
          'cases': [],
          'status': 'ACTIVE',
        });
        return true;
      });
    } catch (_) {
      _bootstrapFail();
    }
  }

  @override
  Future<Map<String, Object?>> recovery() async {
    final value = await super.recovery();
    if (value['status'] == 'MANUAL_REQUIRED') {
      value['status'] = 'RECOVERY_PENDING';
    }
    return value;
  }

  @override
  Future<String> resolve(
    String disposition,
    String localRoot,
    String peerRoot,
  ) async => _viewFail('ATLAS_RECOVERY_PENDING');
}

final class _AnchoredEpochComponent extends _EpochComponentFile {
  _AnchoredEpochComponent(AtlasVaultEpochVault owner, this.reader)
    : super(owner, 'history');
  final AtlasVaultAnchoredSyncState reader;
  @override
  Future<Map<String, Object?>> read(Map<String, Object?> fallback) async {
    await reader._verifyAnchor(_object(owner._historyOrigin!['anchor']));
    return super.read(fallback);
  }
}

AtlasVaultAnchoredSyncState _anchoredPublicationHistory(
  AtlasVaultEpochVault owner,
) {
  final o = owner._historyOrigin!;
  _exact(o, {'format', 'version', 'context', 'pins', 'registry', 'anchor'});
  if (o['format'] != 'atlasvault-anchored-publication-origin' ||
      o['version'] is! int ||
      o['version'] != 1) {
    _bootstrapFail();
  }
  final c = _object(o['context']), pins = _object(o['pins']);
  _exact(c, {
    'account_id',
    'vault_id',
    'collection_id',
    'key_epoch',
    'signing_public_b64',
  });
  _exact(pins, {
    'anchor_root',
    'recipient_device_id',
    'confirmed_transcript',
    'current_context',
  });
  if ([
        'account_id',
        'vault_id',
        'key_epoch',
      ].any((k) => c[k] != owner._context[k]) ||
      pins['recipient_device_id'] != owner._context['device_id'] ||
      _object(_object(o['anchor'])['view'])['root'] !=
          owner._context['state_root'] ||
      !_anchorEqual(o['registry'], owner._registry)) {
    _bootstrapFail();
  }
  final reader = AtlasVaultAnchoredSyncState(
    file: owner._file.file,
    encryptionKey: owner._key,
    trust: {
      for (final k in ['account_id', 'vault_id', 'collection_id', 'key_epoch'])
        k: c[k],
      'trusted_signer_b64': c['signing_public_b64'],
      'registry': o['registry'],
      ...pins,
    },
  );
  if (!_anchorEqual(reader._context, c)) _bootstrapFail();
  reader._store = _AnchoredEpochComponent(owner, reader);
  return reader;
}
