part of 'sync_queue.dart';

/// Decrypted display data is confined to the unlocked runtime, never diagnostics.
final class AtlasVaultRuntimeRecord {
  const AtlasVaultRuntimeRecord(this.operation, this.payload);
  final AtlasVaultEncryptedPatchOperation operation;
  final AtlasVaultPayloadEnvelope? payload;
  @override
  String toString() => 'AtlasVaultRuntimeRecord(<redacted>)';
}

final class _RuntimeStagedFile extends _EncryptedQueueFile {
  _RuntimeStagedFile(AtlasVaultEpochVault owner, this.state, this.component)
    : super(owner._file.file, owner._key, kind: 'runtime-staged');
  final Map<String, Object?> state;
  final String component;
  @override
  Future<Map<String, Object?>> read(Map<String, Object?> fallback) async =>
      _epochCopy(_object(_object(state['components'])[component] ?? fallback));
  @override
  Future<void> write(
    Map<String, Object?> value, {
    FutureOr<void> Function()? beforeReplace,
  }) async {
    await beforeReplace?.call();
    (state['components']! as Map)[component] = _epochCopy(value);
    if (component == 'history' && value['status'] != 'ACTIVE') {
      state['status'] = 'RECOVERY_PENDING';
    }
  }
}

extension AtlasVaultRuntimeRecords on AtlasVaultEpochVault {
  AtlasVaultDurableEncryptedConvergentReplica _replica(Map<String, Object?> s) {
    final h = _object(_object(_object(s['components'])['history'])['context']);
    return AtlasVaultDurableEncryptedConvergentReplica(
      _file.file,
      encryptionKey: _key,
      authenticationKey: _key,
      collectionId: h['collection_id']! as String,
    ).._store = _RuntimeStagedFile(this, s, 'runtime');
  }

  Future<AtlasVaultRuntimeRecord> _runtimeRecord(
    Map<String, Object?> s,
    AtlasVaultEncryptedPatchOperation op,
  ) async {
    final bytes = await _open(s, op.envelope);
    try {
      final body = _object(jsonDecode(utf8.decode(bytes)));
      _exact(body, {
        'format',
        'version',
        'operation_id',
        'author_device_id',
        'author_sequence',
        'lamport',
        'object_id',
        'revision',
        'parent_revision',
        'tombstone',
        'payload',
      });
      final outer = {...op.toJson(), ...op.envelope.toJson()};
      final authenticated = _object(
        jsonDecode(utf8.decode(_base64(op.envelope.aadBase64))),
      );
      final revisionPattern = RegExp(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
      );
      if (body['format'] != 'atlasvault-runtime-record' ||
          body['version'] is! int ||
          body['version'] != 1 ||
          op.envelope.version != 1 ||
          body['author_sequence'] is! int ||
          body['lamport'] is! int ||
          !revisionPattern.hasMatch(op.envelope.revision) ||
          (op.envelope.parentRevision != null &&
              !revisionPattern.hasMatch(op.envelope.parentRevision!)) ||
          authenticated['device_id'] != op.authorDeviceId ||
          authenticated['kind'] != 'patch' ||
          [
            'operation_id',
            'author_device_id',
            'author_sequence',
            'lamport',
            'object_id',
            'revision',
            'parent_revision',
            'tombstone',
          ].any((k) => body[k] != outer[k]) ||
          (body['payload'] == null) != op.envelope.tombstone) {
        _epochFail('ATLAS_RUNTIME_RECORD_REJECTED');
      }
      return AtlasVaultRuntimeRecord(
        op,
        body['payload'] == null
            ? null
            : AtlasVaultPayloadEnvelope.fromJson(_object(body['payload'])),
      );
    } finally {
      bytes.fillRange(0, bytes.length, 0);
    }
  }

  Future<List<AtlasVaultRuntimeRecord>> _records(Map<String, Object?> s) async {
    final replica = _replica(s), history = await replica._load();
    final winners = await replica.currentRecords();
    final result = <AtlasVaultRuntimeRecord>[];
    for (final winner in winners) {
      final op = history.operations.singleWhere(
        (o) =>
            o.envelope.objectId == winner.objectId &&
            o.envelope.revision == winner.revision,
      );
      result.add(await _runtimeRecord(s, op));
    }
    return List.unmodifiable(result);
  }

  Future<List<AtlasVaultRuntimeRecord>> runtimeRecords() => _run(() async {
    final s = await _load();
    await _active(s);
    return _records(s);
  });

  /// One epoch-generation commit publishes the projection and signed P5 outbox.
  Future<AtlasVaultEncryptedPatchOperation> commitRuntimeRecord({
    required AtlasVaultPayloadEnvelope? payload,
    required String objectID,
    String? expectedRevision,
    required SimpleKeyPair signingKey,
  }) => commitRuntimeRecordForTesting(
    payload: payload,
    objectID: objectID,
    expectedRevision: expectedRevision,
    signingKey: signingKey,
  );

  Future<AtlasVaultEncryptedPatchOperation> commitRuntimeRecordForTesting({
    required AtlasVaultPayloadEnvelope? payload,
    required String objectID,
    String? expectedRevision,
    required SimpleKeyPair signingKey,
    FutureOr<void> Function(String)? checkpoint,
  }) => _run(() async {
    final s = await _load();
    await _active(s);
    final existing = (await _records(
      s,
    )).where((r) => r.operation.envelope.objectId == objectID).toList();
    if ((existing.isEmpty && (expectedRevision != null || payload == null)) ||
        (existing.isNotEmpty &&
            (existing.single.operation.envelope.revision != expectedRevision ||
                existing.single.payload == null ||
                (payload != null &&
                    existing.single.payload!.type != payload.type)))) {
      _epochFail('ATLAS_RUNTIME_REVISION_CONFLICT');
    }
    final op = await _stageRuntimeRecord(
      s,
      payload: payload,
      objectID: objectID,
      revision: _runtimeUUID(),
      parentRevision: expectedRevision,
      signingKey: signingKey,
    );
    await checkpoint?.call('runtime_staged');
    await _file.write(
      s,
      beforeReplace: () => checkpoint?.call('before_local_commit'),
      afterRecord: () => checkpoint?.call('after_recovery_record'),
    );
    await checkpoint?.call('after_local_commit');
    return op;
  });

  Future<AtlasVaultEncryptedPatchOperation> _stageRuntimeRecord(
    Map<String, Object?> s, {
    required AtlasVaultPayloadEnvelope? payload,
    required String objectID,
    required String revision,
    required String? parentRevision,
    required SimpleKeyPair signingKey,
  }) async {
    final replica = _replica(s), history = await replica._load();
    final author = _context['device_id']! as String;
    final own = history.operations.where((o) => o.authorDeviceId == author);
    final seq =
        own.fold<int>(
          0,
          (n, o) => o.authorSequence > n ? o.authorSequence : n,
        ) +
        1;
    final lamport =
        history.operations.fold<int>(
          0,
          (n, o) => o.lamport > n ? o.lamport : n,
        ) +
        1;
    final operationID = _runtimeUUID();
    final body = Uint8List.fromList(
      utf8.encode(
        jsonEncode({
          'format': 'atlasvault-runtime-record',
          'version': 1,
          'operation_id': operationID,
          'author_device_id': author,
          'author_sequence': seq,
          'lamport': lamport,
          'object_id': objectID,
          'revision': revision,
          'parent_revision': parentRevision,
          'tombstone': payload == null,
          'payload': payload?.toJson(),
        }),
      ),
    );
    late AtlasVaultOpaqueCiphertextEnvelope sealed;
    try {
      sealed = await _seal(
        s,
        'patch',
        body,
        objectID: objectID,
        revision: revision,
        signingKey: signingKey,
      );
    } finally {
      body.fillRange(0, body.length, 0);
    }
    final op = AtlasVaultEncryptedPatchOperation.fromJson({
      'format': _patchFormat,
      'version': 1,
      'operation_id': operationID,
      'operation_type': payload == null ? 'delete' : 'upsert',
      'author_device_id': author,
      'author_sequence': seq,
      'lamport': lamport,
      'envelope': {
        ...sealed.toJson(),
        'parent_revision': parentRevision,
        'tombstone': payload == null,
      },
    });
    await _runtimeRecord(s, op);
    await replica.ingestRemote(op);
    final outbox = AtlasVaultDurableEncryptedOutbox(
      _file.file,
      encryptionKey: _key,
    ).._store = _RuntimeStagedFile(this, s, 'outbox');
    await outbox.enqueue(op);
    return op;
  }

  /// Authenticated one-time import; old source bytes are never rewritten here.
  Future<int> importLegacyRuntime({
    required legacy.AtlasVaultLocalStore store,
    required Uint8List vaultKey,
    required SimpleKeyPair signingKey,
  }) => _run(() async {
    final s = await _load();
    await _active(s);
    if (store.vaultMetadata.vaultId != _context['vault_id'] ||
        vaultKey.length != 32) {
      _epochFail('ATLAS_RUNTIME_MIGRATION_REQUIRED');
    }
    var count = 0;
    for (final record
        in store.records.toList()..sort((a, b) => a.id.compareTo(b.id))) {
      final plaintext = await record_crypto.openAtlasVaultRecord(
        record: record,
        vaultKey: vaultKey,
        vaultId: store.vaultMetadata.vaultId,
      );
      try {
        if (record.deleted && plaintext.isNotEmpty) {
          _epochFail('ATLAS_RUNTIME_MIGRATION_REQUIRED');
        }
        final payload = record.deleted
            ? null
            : AtlasVaultPayloadEnvelope.fromJson(
                _object(jsonDecode(utf8.decode(plaintext))),
              );
        final prior = (await _replica(s)._load()).operations
            .where((op) => op.envelope.objectId == record.id)
            .toList();
        final matching = prior
            .where((op) => op.envelope.revision == record.revision)
            .toList();
        if (matching.isNotEmpty) {
          final found = await _runtimeRecord(s, matching.single);
          if (found.payload != payload ||
              found.operation.envelope.parentRevision !=
                  record.parentRevision ||
              found.operation.envelope.tombstone != record.deleted) {
            _epochFail('ATLAS_RUNTIME_MIGRATION_REQUIRED');
          }
          continue;
        }
        if (prior.isNotEmpty) _epochFail('ATLAS_RUNTIME_MIGRATION_REQUIRED');
        await _stageRuntimeRecord(
          s,
          payload: payload,
          objectID: record.id,
          revision: record.revision,
          parentRevision: record.parentRevision,
          signingKey: signingKey,
        );
        count++;
      } finally {
        plaintext.fillRange(0, plaintext.length, 0);
      }
    }
    if (count > 0) await _file.write(s);
    return count;
  });

  /// P6 history admission and P5 convergence publish together. No cursor-only success.
  Future<int> ingestRuntimePage({
    required Map<String, Object?> view,
    required List<Map<String, Object?>> registry,
    required Map<String, Object?> collection,
    required Uint8List opaqueState,
    required List<Map<String, Object?>> operations,
  }) => _run(() async {
    final s = await _load();
    await _active(s);
    final history = _history(s);
    await history._load();
    history._store = _RuntimeStagedFile(this, s, 'history');
    try {
      await history.ingest(view, registry, collection, opaqueState);
      final payload = _object(jsonDecode(utf8.decode(opaqueState)));
      if (operations.length > _maximumQueueOperations) _epochFail();
      final remoteState = _epochCopy(s);
      (remoteState['components']! as Map).remove('runtime');
      final remote = _replica(remoteState);
      final replica = _replica(s);
      var count = 0;
      for (final raw in operations) {
        final op = AtlasVaultEncryptedPatchOperation.fromJson(raw);
        await _runtimeRecord(s, op);
        await remote.ingestRemote(op);
      }
      // P6 authenticates the complete collection. P5 operations are independently
      // authenticated inside each ciphertext and must reconstruct that same view.
      final remoteRecords = (await remote.currentRecords())
          .map((r) => r.toJson())
          .toList();
      if (jsonEncode(_canonicalValue(remoteRecords)) !=
          jsonEncode(_canonicalValue(payload['records']))) {
        _epochFail('ATLAS_RUNTIME_RECORD_REJECTED');
      }
      final existing = await replica._load();
      for (final raw in operations) {
        final op = AtlasVaultEncryptedPatchOperation.fromJson(raw);
        if (op.envelope.keyEpoch != s['epoch'] &&
            !existing.operations.contains(op)) {
          _epochFail('ATLAS_EPOCH_WRITE_REJECTED');
        }
        if (await replica.ingestRemote(op)) count++;
      }
      await _file.write(s);
      return count;
    } catch (_) {
      // Contradictory authenticated history is evidence, not a disposable parse error.
      if (s['status'] == 'RECOVERY_PENDING') await _file.write(s);
      rethrow;
    }
  });

  Future<Map<String, Object?>> runtimePublication({
    required SimpleKeyPair signingKey,
    List<Map<String, Object?>>? authenticatedRegistry,
  }) => _run(() async {
    final s = await _load();
    await _active(s);
    final replica = _replica(s), history = await replica._load();
    for (final op in history.operations) {
      await _runtimeRecord(s, op);
    }
    final bytes = _canonicalJsonBytes({
      'format': 'atlasvault-guarded-collection',
      'version': 1,
      'route': 'patch',
      'records': (await replica.currentRecords())
          .map((r) => r.toJson())
          .toList(),
    });
    final commitment = await _createCommitment(
      s,
      bytes,
      signingKey: signingKey,
      authenticatedRegistry: authenticatedRegistry,
    );
    return {
      ...commitment,
      'opaque_state_b64': base64Encode(bytes),
      'operations': history.operations.map((o) => o.toJson()).toList(),
    };
  });
}

String _runtimeUUID() {
  final b = AesGcm.with256bits().newNonce().toList()
    ..addAll(AesGcm.with256bits().newNonce().take(4));
  b[6] = (b[6] & 15) | 64;
  b[8] = (b[8] & 63) | 128;
  final h = b.map((n) => n.toRadixString(16).padLeft(2, '0')).join();
  return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
}
