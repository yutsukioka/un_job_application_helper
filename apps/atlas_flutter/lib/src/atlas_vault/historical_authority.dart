part of 'sync_queue.dart';

const _authorityBindings = [
  'account_id',
  'vault_id',
  'registry_root',
  'key_epoch',
  'registry_generation',
  'collection_sha256',
  'recipient_device_id',
  'transcript_sha256',
];
Never _authorityFail([String code = 'ATLAS_HISTORICAL_AUTHORITY_REJECTED']) =>
    throw AtlasVaultStateViewException(code);

// Only the proven two-view lineage is supported. No old ciphertext preimage
// or transport assertion can supply missing authenticated history.
Future<Map<String, Object?>> _verifyHistoricalAuthority(
  Map<String, Object?> proof,
  Map<String, Object?> anchor,
  Uint8List public,
  List<Map<String, Object?>> registry,
  Map<String, Object?> trust,
  Map<String, Object?> collection,
  Uint8List bytes,
) async {
  try {
    _exact(proof, {
      'format',
      'version',
      'anchor_root',
      ..._authorityBindings,
      'signed_descriptors',
      'prior_registry',
      'revocation',
      'views',
    });
    if (_canonicalJsonBytes(proof).length > 128 * 1024 ||
        proof['format'] != 'atlasvault-historical-authority' ||
        proof['version'] is! int ||
        proof['version'] != 1) {
      _authorityFail();
    }
    final p = _object(anchor['checkpoint']);
    if (proof['anchor_root'] != p['root'] ||
        proof['anchor_root'] != trust['anchor_root'] ||
        _authorityBindings.any((k) => !_anchorEqual(proof[k], p[k]))) {
      _authorityFail();
    }
    final c = AtlasVaultSignedStateCommitment.fromJson(collection);
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
            publicKey: SimplePublicKey(public, type: KeyPairType.ed25519),
          ),
        )) {
      _authorityFail();
    }
    final body = _object(jsonDecode(utf8.decode(bytes)));
    _exact(body, {'format', 'version', 'route', 'records'});
    if (body['format'] != 'atlasvault-guarded-collection' ||
        body['version'] is! int ||
        body['version'] != 1 ||
        !['patch', 'snapshot', 'compaction'].contains(body['route'])) {
      _authorityFail();
    }
    final rows = _anchorRows(body['records']);
    if (rows.length > _viewLimit) _authorityFail();
    final ids = <String>{};
    for (final raw in rows) {
      final r = AtlasVaultOpaqueCiphertextEnvelope.fromJson(raw);
      if (r.version != 1 ||
          r.keyEpoch > (p['key_epoch']! as int) ||
          !ids.add(r.objectId)) {
        _authorityFail();
      }
    }
    final views = _anchorRows(proof['views']);
    if (views.length != 2) _authorityFail('ATLAS_HISTORY_PREIMAGE_REQUIRED');
    final prior = await AtlasVaultAuthenticatedStateView._verify(
          views[0],
          public,
        ),
        current = await AtlasVaultAuthenticatedStateView._verify(
          views[1],
          public,
        );
    if (!_anchorEqual(current, anchor['view'])) _authorityFail();
    final old = _anchorRows(proof['prior_registry']),
        removal = _object(proof['revocation']),
        admission = _object(anchor['enrollment']);
    final after = await AtlasVaultRevocation.verify(removal, old);
    final admitted = await enrollment.AtlasVaultDeviceEnrollment.verify(
      admission,
      registry: after,
      context: {
        for (final k in enrollment.AtlasVaultDeviceEnrollment.contextFields)
          k: admission[k],
      },
      confirmedTranscript: trust['confirmed_transcript']! as String,
      status: 'ACTIVE',
    );
    if (AtlasVaultRevocation.registryRoot(admitted) != p['registry_root'] ||
        AtlasVaultRevocation.registryRoot(registry) != p['registry_root']) {
      _authorityFail();
    }
    final descriptors = _anchorRows(proof['signed_descriptors']);
    if (descriptors.isEmpty || descriptors.length > 32) _authorityFail();
    final entries = <Map<String, Object?>>[],
        reconstructed = <Map<String, Object?>>[];
    for (final raw in descriptors) {
      final d = await identity.verifyAtlasVaultSignedDeviceDescriptor(
        identity.AtlasVaultSignedDeviceDescriptor.fromJson(raw),
      );
      entries.add({
        'device_id': _sha256Hex(utf8.encode(d.deviceId)),
        'descriptor_sha256': _sha256Hex(_canonicalJsonBytes(d.toJson())),
      });
      reconstructed.add({
        'device_id': d.deviceId,
        'state': 'ACTIVE',
        'signing_public_b64': base64Encode(d.signingPublicKey),
        'agreement_public_b64': base64Encode(d.agreementPublicKey),
      });
    }
    if (AtlasVaultAuthenticatedStateView.registryRoot(entries) !=
            prior['registry_root'] ||
        AtlasVaultRevocation.registryRoot(reconstructed) !=
            AtlasVaultRevocation.registryRoot(old)) {
      _authorityFail();
    }
    final signer = old.firstWhere(
      (e) => e['device_id'] == p['issuer_device_id'] && e['state'] == 'ACTIVE',
    );
    if (!_anchorEqual(
          _base64(signer['signing_public_b64'], exactLength: 32),
          public,
        ) ||
        removal['initiator_device_id'] != p['issuer_device_id']) {
      _authorityFail();
    }
    if ([
          'account_id',
          'vault_id',
        ].any((k) => prior[k] != current[k] || removal[k] != current[k]) ||
        prior['sequence'] != 1 ||
        current['sequence'] != 2 ||
        prior['previous_root'] != '0' * 64 ||
        prior['previous_registry_root'] != _emptyRegistryRoot ||
        current['previous_root'] != prior['root'] ||
        current['previous_registry_root'] != prior['registry_root'] ||
        prior['key_epoch'] != removal['key_epoch'] ||
        current['key_epoch'] != (prior['key_epoch']! as int) + 1 ||
        removal['sequence'] != 1 ||
        admission['state_root'] != prior['root'] ||
        admission['registry_generation'] != current['key_epoch']) {
      _authorityFail();
    }
    if (_commitmentRoot(
          p['collection_id']! as String,
          prior['sequence']! as int,
          '0' * 64,
          _sha256Hex(bytes),
        ) !=
        prior['collection_root']) {
      _authorityFail('ATLAS_HISTORY_PREIMAGE_REQUIRED');
    }
    final result = <String, Object?>{};
    for (final r in rows) {
      if ((r['key_epoch']! as int) >= (p['key_epoch']! as int)) continue;
      if (r['key_epoch'] != prior['key_epoch']) {
        _authorityFail('ATLAS_HISTORY_PREIMAGE_REQUIRED');
      }
      final aad = _base64(r['aad_b64']),
          m = _object(jsonDecode(utf8.decode(_base64(r['aad_b64']))));
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
      if (!_anchorEqual(_canonicalJsonBytes(m), aad) ||
          m['format'] != 'atlasvault-epoch-ciphertext' ||
          m['version'] is! int ||
          m['version'] != 1 ||
          !['patch', 'snapshot'].contains(m['kind']) ||
          [
            'account_id',
            'vault_id',
            'key_epoch',
          ].any((k) => m[k] != prior[k]) ||
          ['object_id', 'revision', 'key_epoch'].any((k) => m[k] != r[k])) {
        _authorityFail();
      }
      final author = old.firstWhere(
        (e) => e['device_id'] == m['device_id'] && e['state'] == 'ACTIVE',
      );
      if (!await Ed25519().verify(
        [
          ...ascii.encode('atlasvault-epoch-ciphertext-signature-v1\x00'),
          ...aad,
          ..._base64(r['nonce_b64']),
          ..._base64(r['ciphertext_b64']),
        ],
        signature: Signature(
          _base64(r['signature_b64'], exactLength: 64),
          publicKey: SimplePublicKey(
            _base64(author['signing_public_b64'], exactLength: 32),
            type: KeyPairType.ed25519,
          ),
        ),
      )) {
        _authorityFail();
      }
      result[r['object_id']! as String] = {
        'key_epoch': r['key_epoch'],
        'envelope_sha256': _sha256Hex(_canonicalJsonBytes(r)),
        'author': _anchorCopy(author),
      };
    }
    return result;
  } on AtlasVaultStateViewException catch (e) {
    if (e.code == 'ATLAS_HISTORY_PREIMAGE_REQUIRED') rethrow;
    _authorityFail();
  } catch (_) {
    _authorityFail();
  }
}
