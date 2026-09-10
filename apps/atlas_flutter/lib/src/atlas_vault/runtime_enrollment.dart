part of 'sync_queue.dart';

extension AtlasVaultRuntimeEnrollment on AtlasVaultEpochVault {
  Future<Map<String, Object?>> prepareRuntimeEnrollment({
    required identity.AtlasVaultDeviceIdentity issuer,
    required identity.AtlasVaultSignedDeviceDescriptor target,
    required String confirmedTranscript,
    required Map<String, Object?> expectedContext,
    required List<Map<String, Object?>> signedDescriptors,
    required Future<void> Function() beforePublish,
  }) => _run(() async {
    final s = await _load();
    await _active(s);
    final descriptor = await identity.verifyAtlasVaultSignedDeviceDescriptor(
      target,
    );
    final registry = _epochRows(s['registry']);
    if (issuer.deviceId != _context['device_id'] ||
        !registry.any(
          (r) =>
              r['device_id'] == issuer.deviceId &&
              r['state'] == 'ACTIVE' &&
              r['signing_public_b64'] ==
                  base64Encode(issuer.signingPublicKey) &&
              r['agreement_public_b64'] ==
                  base64Encode(issuer.agreementPublicKey),
        )) {
      _epochFail('ATLAS_ENROLLMENT_REJECTED');
    }
    final stored = _object(s['components'])['enrollment_delivery'];
    if (stored != null) {
      final packet = _object(stored), a = _object(packet['anchor']);
      final p = _object(a['checkpoint']), e = _object(a['enrollment']);
      if (p['recipient_device_id'] != descriptor.deviceId ||
          p['recipient_agreement_sha256'] !=
              _sha256Hex(descriptor.agreementPublicKey) ||
          p['transcript_sha256'] != confirmedTranscript ||
          !_anchorEqual({
            for (final k in enrollment.AtlasVaultDeviceEnrollment.contextFields)
              k: e[k],
          }, expectedContext) ||
          !_anchorEqual({
            for (final k in enrollment.AtlasVaultDeviceEnrollment.contextFields)
              k: p[k],
          }, _enrollmentContext(s))) {
        _epochFail('ATLAS_ENROLLMENT_REJECTED');
      }
      await beforePublish();
      return _epochCopy(packet);
    }
    if (!_anchorEqual(expectedContext, _enrollmentContext(s))) {
      _epochFail('ATLAS_ENROLLMENT_REJECTED');
    }
    final secret = issuer.secretBundle();
    final seed = _base64(
      secret.toJson()['signing_private_key'],
      exactLength: 32,
    );
    secret.destroy();
    final signer = await Ed25519().newKeyPairFromSeed(seed);
    seed.fillRange(0, seed.length, 0);
    _enrollmentStaging = s;
    try {
      final admitted =
          [
            ...registry,
            <String, Object?>{
              'device_id': descriptor.deviceId,
              'state': 'ACTIVE',
              'signing_public_b64': base64Encode(descriptor.signingPublicKey),
              'agreement_public_b64': base64Encode(
                descriptor.agreementPublicKey,
              ),
            },
          ]..sort(
            (a, b) => (a['device_id']! as String).compareTo(
              b['device_id']! as String,
            ),
          );
      final proof = await enrollment.AtlasVaultDeviceEnrollment.create(
        {
          'format': 'atlasvault-device-enrollment',
          'version': 1,
          ...expectedContext,
          'next_registry_generation':
              (expectedContext['registry_generation']! as int) + 1,
          'prior_registry_root': AtlasVaultRevocation.registryRoot(registry),
          'resulting_registry_root': AtlasVaultRevocation.registryRoot(
            admitted,
          ),
          'target_device_id': descriptor.deviceId,
          'target_signing_public_b64': base64Encode(
            descriptor.signingPublicKey,
          ),
          'target_agreement_public_b64': base64Encode(
            descriptor.agreementPublicKey,
          ),
          'target_agreement_sha256': _sha256Hex(descriptor.agreementPublicKey),
          'transcript_sha256': confirmedTranscript,
          'issuer_device_id': issuer.deviceId,
          'authorization_category': 'SAS_CONFIRMED',
          'signature_algorithm': 'Ed25519',
        },
        registry: registry,
        context: expectedContext,
        confirmedTranscript: confirmedTranscript,
        status: 'ACTIVE',
        signingKey: signer,
      );
      final history = await _history(s)._stageEpoch({
        'format': 'atlasvault-enrollment-bridge',
        'version': 1,
        'enrollment': proof,
      });
      (s['components']! as Map)['history'] = history;
      s['registry'] = admitted;
      s['recipients'] =
          (admitted
              .where((r) => r['state'] == 'ACTIVE')
              .map((r) => r['device_id']! as String)
              .toList()
            ..sort());
      final records = await _records(s);
      final bytes = _canonicalJsonBytes({
        'format': 'atlasvault-guarded-collection',
        'version': 1,
        'route': 'patch',
        'records': records.map((r) => r.operation.envelope.toJson()).toList(),
      });
      if (bytes.length > 1024 * 1024 || records.length > 256) _bootstrapFail();
      final current = await _createCommitment(s, bytes, signingKey: signer);
      final view = _object(current['view']),
          collection = _object(current['collection']);
      final p = <String, Object?>{
        'format': 'atlasvault-history-bootstrap',
        'version': 1,
        ..._enrollmentContext(s),
        'registry_root': AtlasVaultRevocation.registryRoot(admitted),
        'sequence': view['sequence'],
        'collection_id': collection['collection_id'],
        'collection_root': collection['root'],
        'collection_sha256': _sha256Hex(bytes),
        'enrollment_root': proof['root'],
        'recipient_device_id': descriptor.deviceId,
        'recipient_agreement_sha256': proof['target_agreement_sha256'],
        'transcript_sha256': confirmedTranscript,
        'issuer_device_id': issuer.deviceId,
        'signature_algorithm': 'Ed25519',
      };
      p['root'] = _sha256Hex([
        ...ascii.encode('atlasvault-history-bootstrap-v1\n'),
        ..._canonicalJsonBytes(p),
      ]);
      p['signature_b64'] = base64Encode(
        await issuer.signBytes([
          ...ascii.encode('atlasvault-history-bootstrap-signature-v1\x00'),
          for (var i = 0; i < 64; i += 2)
            int.parse((p['root']! as String).substring(i, i + 2), radix: 16),
        ]),
      );
      final anchor = <String, Object?>{
        'checkpoint': p,
        'enrollment': proof,
        'view': view,
      };
      final trust = <String, Object?>{
        for (final k in [
          'account_id',
          'vault_id',
          'collection_id',
          'key_epoch',
        ])
          k: p[k],
        'trusted_signer_b64': base64Encode(issuer.signingPublicKey),
        'registry': admitted,
        'anchor_root': p['root'],
        'recipient_device_id': descriptor.deviceId,
        'confirmed_transcript': confirmedTranscript,
        'current_context': _enrollmentContext(s),
      };
      await AtlasVaultAnchoredSyncState(
        file: _file.file,
        encryptionKey: _key,
        trust: trust,
      )._verifyAnchor(anchor);
      final epochs = {
        s['epoch']! as int,
        ...records.map((r) => r.operation.envelope.keyEpoch),
      }.toList()..sort();
      Map<String, Object?>? authority;
      if (epochs.length > 1) {
        final rotations = _epochBridgeRecords(
          _object(_object(s['components'])['history']),
        ).where((r) => r['format'] != 'atlasvault-enrollment-bridge').toList();
        if (rotations.length != 1) {
          _authorityFail('ATLAS_HISTORY_PREIMAGE_REQUIRED');
        }
        final prior = _bridgeProof(rotations.single);
        authority = {
          'format': 'atlasvault-historical-authority',
          'version': 1,
          'anchor_root': p['root'],
          for (final k in _authorityBindings) k: p[k],
          'signed_descriptors': signedDescriptors,
          'prior_registry': prior['registry'],
          'revocation': prior['revocation'],
          'views': _object(_object(s['components'])['history'])['views'],
        };
        await _verifyHistoricalAuthority(
          authority,
          anchor,
          issuer.signingPublicKey,
          admitted,
          trust,
          collection,
          bytes,
        );
      }
      final packet = <String, Object?>{
        'format': 'atlasvault-enrollment-delivery',
        'version': 1,
        'hpke_suite': '0x0020/0x0001/0x0002',
        'anchor': anchor,
        'registry': admitted,
        'collection': collection,
        'opaque_b64': base64Encode(bytes),
        'historical_authority': authority,
        'deliveries': <Object?>[],
      };
      if (epochs.length > 32) _bootstrapFail();
      final ring = _ring(s);
      for (final epoch in epochs) {
        final key = ring.vaultKeyForEpoch(epoch);
        try {
          final sealed =
              await AtlasVaultKeyEpochRing.fromEntries(
                currentKeyEpoch: epoch,
                keys: {epoch: key},
              ).sealCurrentHPKEV2(
                recipientPublicKey: descriptor.agreementPublicKey,
                context: AtlasVaultEnrollmentDelivery._context(packet),
              );
          (packet['deliveries']! as List).add({
            'key_epoch': epoch,
            'encapsulated_key_b64': base64Encode(sealed.encapsulatedKey),
            'ciphertext_b64': base64Encode(sealed.ciphertext),
          });
        } finally {
          key.fillRange(0, key.length, 0);
        }
      }
      packet['root'] = AtlasVaultEnrollmentDelivery._root(packet);
      packet['signature_b64'] = base64Encode(
        await issuer.signBytes(
          AtlasVaultEnrollmentDelivery._message(packet['root']! as String),
        ),
      );
      if (_canonicalJsonBytes(packet).length > 2 * 1024 * 1024) {
        _bootstrapFail();
      }
      (s['components']! as Map)['enrollment_delivery'] = packet;
      s['generation'] = (s['generation']! as int) + 1;
      await beforePublish();
      await _file.write(s);
      return _epochCopy(packet);
    } finally {
      _enrollmentStaging = null;
      signer.destroy();
    }
  });

  Future<void> _stageEnrollmentRuntime(
    Map<String, Object?> state,
    Uint8List bytes,
  ) async {
    final checkpoint = _object(
      _object(_historyOrigin!['anchor'])['checkpoint'],
    );
    if (_sha256Hex(bytes) != checkpoint['collection_sha256']) _bootstrapFail();
    final body = _object(jsonDecode(utf8.decode(bytes)));
    final replica = _replica(state);
    await replica._write(
      operations: [],
      snapshots: [],
      pendingOperationIds: [],
    );
    for (final raw in _epochRows(body['records'])) {
      final envelope = AtlasVaultOpaqueCiphertextEnvelope.fromJson(raw);
      final clear = await _open(state, envelope);
      late Map<String, Object?> metadata;
      try {
        metadata = _object(jsonDecode(utf8.decode(clear)));
      } finally {
        clear.fillRange(0, clear.length, 0);
      }
      final op = AtlasVaultEncryptedPatchOperation.fromJson({
        'format': _patchFormat,
        'version': 1,
        'operation_type': envelope.tombstone ? 'delete' : 'upsert',
        for (final k in [
          'operation_id',
          'author_device_id',
          'author_sequence',
          'lamport',
        ])
          k: metadata[k],
        'envelope': envelope.toJson(),
      });
      await _runtimeRecord(state, op);
      await replica.ingestRemote(op);
    }
    if (!_anchorEqual(
      (await replica.currentRecords()).map((r) => r.toJson()).toList(),
      body['records'],
    )) {
      _bootstrapFail();
    }
  }

  Future<void> verifyEnrollmentRuntime(Uint8List bytes) => _run(() async {
    final s = await _load();
    await _active(s);
    if (_historyOrigin == null ||
        !_object(s['components']).containsKey('runtime') ||
        !_anchorEqual(
          (await _records(
            s,
          )).map((r) => r.operation.envelope.toJson()).toList(),
          _object(jsonDecode(utf8.decode(bytes)))['records'],
        )) {
      _bootstrapFail();
    }
  });

  /// Read-only retry after native publication. Never reinstall the old projection
  /// over authenticated edits made after activation.
  Future<void> verifyEnrollmentReceipt(
    Map<String, Object?> packet, {
    required Map<String, Object?> pins,
    required Uint8List trustedSigner,
    required identity.AtlasVaultDeviceIdentity recipient,
  }) => _run(() async {
    if (_canonicalJsonBytes(packet).length > 2 * 1024 * 1024) _bootstrapFail();
    final s = await _load();
    await _active(s);
    final origin = _historyOrigin;
    final expectedAnchor = {
      ..._object(packet['anchor']),
      if (packet['historical_authority'] != null)
        'historical_authority': {
          'proof': packet['historical_authority'],
          'collection': packet['collection'],
          'opaque_b64': packet['opaque_b64'],
        },
    };
    if (origin == null ||
        !_anchorEqual(origin['pins'], pins) ||
        !_anchorEqual(origin['anchor'], expectedAnchor) ||
        _object(origin['context'])['signing_public_b64'] !=
            base64Encode(trustedSigner) ||
        _context['device_id'] != recipient.deviceId ||
        _object(
              _object(origin['anchor'])['checkpoint'],
            )['recipient_agreement_sha256'] !=
            _sha256Hex(recipient.agreementPublicKey) ||
        packet['root'] != AtlasVaultEnrollmentDelivery._root(packet) ||
        !await Ed25519().verify(
          AtlasVaultEnrollmentDelivery._message(packet['root']! as String),
          signature: Signature(
            _base64(packet['signature_b64'], exactLength: 64),
            publicKey: SimplePublicKey(
              trustedSigner,
              type: KeyPairType.ed25519,
            ),
          ),
        )) {
      _bootstrapFail();
    }
    final receipt = await _EncryptedQueueFile(
      File('${_file.file.parent.path}/enrollment-receipt'),
      _key,
      kind: 'enrollment-receipt-v1',
    ).read({});
    if (!_anchorEqual(receipt, {'root': packet['root']})) _bootstrapFail();
    // Loading and reading validate the current anchored history and terminal
    // tombstones; neither the accepted root nor the runtime is replaced.
    await _records(s);
  });
}
