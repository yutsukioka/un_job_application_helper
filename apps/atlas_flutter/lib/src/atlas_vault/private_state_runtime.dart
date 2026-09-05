import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import '../../atlas.dart';
import '../../atlas_vault.dart' as vault;
import 'android_storage.dart';
import 'local_store_io.dart';
import 'sync_queue.dart' as sync;
import 'epoch_rotation.dart' show AtlasVaultRotationException;

enum AtlasVaultActivationResult { activated, migrationRequired, failed }

final class AtlasVaultPrivateStateException implements Exception {
  const AtlasVaultPrivateStateException();

  @override
  String toString() => 'AtlasVault private-state operation failed.';
}

final class AtlasVaultPrivateStateSnapshot {
  AtlasVaultPrivateStateSnapshot({
    required List<AtlasSavedSearch> savedSearches,
    required List<AtlasApplicationRecord> trackerRecords,
    List<AtlasVaultPrivateRecord> records = const <AtlasVaultPrivateRecord>[],
    List<AtlasVaultPrivateTombstone> tombstones =
        const <AtlasVaultPrivateTombstone>[],
  }) : savedSearches = List<AtlasSavedSearch>.unmodifiable(savedSearches),
       trackerRecords = List<AtlasApplicationRecord>.unmodifiable(
         trackerRecords,
       ),
       records = List<AtlasVaultPrivateRecord>.unmodifiable(records),
       tombstones = List<AtlasVaultPrivateTombstone>.unmodifiable(tombstones);

  final List<AtlasSavedSearch> savedSearches;
  final List<AtlasApplicationRecord> trackerRecords;
  final List<AtlasVaultPrivateRecord> records;
  final List<AtlasVaultPrivateTombstone> tombstones;

  @override
  String toString() => 'AtlasVaultPrivateStateSnapshot(<redacted>)';
}

final class AtlasVaultPrivateRecord {
  const AtlasVaultPrivateRecord({
    required this.recordId,
    required this.revision,
    required this.parentRevision,
    required this.keyId,
    required this.envelope,
  });

  final String recordId;
  final String revision;
  final String? parentRevision;
  final String keyId;
  final vault.AtlasVaultPayloadEnvelope envelope;

  @override
  String toString() => 'AtlasVaultPrivateRecord(<redacted>)';
}

final class AtlasVaultPrivateTombstone {
  const AtlasVaultPrivateTombstone({
    required this.recordId,
    required this.revision,
    required this.parentRevision,
    required this.keyId,
  });

  final String recordId;
  final String revision;
  final String? parentRevision;
  final String keyId;

  @override
  String toString() => 'AtlasVaultPrivateTombstone(<redacted>)';
}

abstract interface class AtlasVaultPrivateStatePersistence {
  bool get isActive;

  Future<AtlasVaultActivationResult> activateExisting(String vaultId);

  Future<AtlasVaultPrivateStateSnapshot> read();

  Future<AtlasVaultPrivateStateSnapshot> saveSearch(AtlasSavedSearch value);

  Future<AtlasVaultPrivateStateSnapshot> saveTrackerRecord(
    AtlasApplicationRecord value,
  );

  Future<void> deactivate();
}

final class AtlasVaultPrivateStateRuntime
    implements AtlasVaultPrivateStatePersistence {
  AtlasVaultPrivateStateRuntime({
    required AtlasVaultSecureKeyStore secureKeyStore,
    required AtlasVaultLocalStoreIO localStoreIO,
    DateTime Function()? now,
    String Function()? uuidProvider,
    Uint8List Function()? nonceProvider,
    Future<sync.AtlasVaultRuntimeSession> Function(String)? epochSessionFactory,
  }) : // Keep public constructor parameter names stable.
       // ignore: prefer_initializing_formals
       _secureKeyStore = secureKeyStore,
       // ignore: prefer_initializing_formals
       _localStoreIO = localStoreIO,
       _now = now ?? DateTime.now,
       _uuidProvider = uuidProvider ?? _secureUuidV4,
       _nonceProvider = nonceProvider ?? _secureNonce,
       // ignore: prefer_initializing_formals
       _epochSessionFactory = epochSessionFactory;

  static const _recordKeyId = 'primary-android-local-key-v1';

  final AtlasVaultSecureKeyStore _secureKeyStore;
  final AtlasVaultLocalStoreIO _localStoreIO;
  final DateTime Function() _now;
  final String Function() _uuidProvider;
  final Uint8List Function() _nonceProvider;
  final Future<sync.AtlasVaultRuntimeSession> Function(String)?
  _epochSessionFactory;
  sync.AtlasVaultRuntimeSession? _epochSession;
  String _runtimeState = 'LOCKED';
  String get runtimeState => _runtimeState;

  bool _active = false;
  bool _activating = false;
  bool _deactivating = false;
  int _generation = 0;
  int _activationGeneration = 0;
  String? _vaultId;
  Uint8List? _vaultKey;
  AtlasVaultPrivateStateSnapshot _snapshot = AtlasVaultPrivateStateSnapshot(
    savedSearches: const <AtlasSavedSearch>[],
    trackerRecords: const <AtlasApplicationRecord>[],
  );
  Map<String, _PrivateRecordMetadata> _savedSearchMetadata =
      const <String, _PrivateRecordMetadata>{};
  Map<String, _PrivateRecordMetadata> _trackerMetadata =
      const <String, _PrivateRecordMetadata>{};
  Map<String, _PrivateRecordMetadata> _recordMetadata =
      const <String, _PrivateRecordMetadata>{};
  Future<void> _mutationTail = Future<void>.value();
  int _pendingMutationCount = 0;
  Future<void>? _interoperabilityOperation;

  @override
  bool get isActive =>
      _active &&
      !_deactivating &&
      (_epochSessionFactory == null || _runtimeState == 'ACTIVE');

  bool isActiveVault(String vaultId) {
    return isActive && _vaultId == vaultId;
  }

  Future<void> validateImportProjection({
    required String vaultId,
    required Uint8List vaultKey,
    required vault.AtlasVaultLocalStore store,
  }) async {
    try {
      if (vaultKey.length != 32) {
        throw const AtlasVaultPrivateStateException();
      }
      await _hydrate(vaultId: vaultId, vaultKey: vaultKey, store: store);
    } catch (_) {
      throw const AtlasVaultPrivateStateException();
    }
  }

  @override
  Future<AtlasVaultActivationResult> activateExisting(String vaultId) async {
    if (_active || _activating || _deactivating) {
      return AtlasVaultActivationResult.failed;
    }
    _activationGeneration += 1;
    final activationGeneration = _activationGeneration;
    _activating = true;
    Uint8List? candidateKey;
    try {
      validateAtlasVaultAndroidVaultIdInternal(vaultId);
      if (_epochSessionFactory != null) {
        final candidate = await _epochSessionFactory(vaultId);
        try {
          _requireCurrentActivation(activationGeneration);
        } catch (_) {
          candidate.close();
          rethrow;
        }
        _epochSession = candidate;
        final legacy = await _localStoreIO.read(vaultId);
        _requireCurrentActivation(activationGeneration);
        if (legacy != null && legacy.records.isNotEmpty) {
          candidateKey = await _secureKeyStore.loadVaultKey(vaultId);
          _requireCurrentActivation(activationGeneration);
          if (candidateKey == null || candidateKey.length != 32) {
            _clearSession();
            return AtlasVaultActivationResult.migrationRequired;
          }
          try {
            await candidate.importLegacy(store: legacy, vaultKey: candidateKey);
            final confirmed = await _localStoreIO.read(vaultId);
            if (confirmed == null ||
                !_sameJson(confirmed.toJson(), legacy.toJson())) {
              throw const AtlasVaultPrivateStateException();
            }
          } catch (_) {
            _clearSession();
            return AtlasVaultActivationResult.migrationRequired;
          }
        }
        final projected = await _readEpochSnapshot();
        _requireCurrentActivation(activationGeneration);
        _generation += 1;
        _vaultId = vaultId;
        _snapshot = projected;
        _runtimeState = 'ACTIVE';
        _active = true;
        return AtlasVaultActivationResult.activated;
      }
      candidateKey = await _secureKeyStore.loadVaultKey(vaultId);
      _requireCurrentActivation(activationGeneration);
      if (candidateKey == null || candidateKey.length != 32) {
        return AtlasVaultActivationResult.failed;
      }
      final store = await _localStoreIO.read(vaultId);
      _requireCurrentActivation(activationGeneration);
      if (store == null || store.vaultMetadata.vaultId != vaultId) {
        return AtlasVaultActivationResult.failed;
      }
      final hydrated = await _hydrate(
        vaultId: vaultId,
        vaultKey: candidateKey,
        store: store,
      );
      _requireCurrentActivation(activationGeneration);

      _generation += 1;
      _vaultId = vaultId;
      _vaultKey = Uint8List.fromList(candidateKey);
      _installHydrated(hydrated);
      _active = true;
      return AtlasVaultActivationResult.activated;
    } catch (_) {
      if (_activationGeneration == activationGeneration) {
        _clearSession();
      }
      return AtlasVaultActivationResult.failed;
    } finally {
      if (_activationGeneration == activationGeneration) {
        _activating = false;
      }
      _wipe(candidateKey);
    }
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> read() async {
    _requireActive();
    if (_epochSession != null) {
      final generation = _generation, session = _epochSession;
      final snapshot = await _readEpochSnapshot();
      if (!isActive ||
          generation != _generation ||
          !identical(session, _epochSession)) {
        throw const AtlasVaultPrivateStateException();
      }
      _snapshot = snapshot;
    }
    return _copySnapshot(_snapshot);
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> saveSearch(AtlasSavedSearch value) {
    if (_epochSession != null) {
      return _enqueueEpochMutation(() async {
        _snapshot = await _readEpochSnapshot();
        final existing = _snapshot.records
            .where(
              (r) =>
                  r.envelope.payload is vault.AtlasSavedSearchPayload &&
                  (r.envelope.payload as vault.AtlasSavedSearchPayload).name ==
                      value.name,
            )
            .firstOrNull;
        return _commitEpochPayload(
          _savedSearchEnvelope(
            value,
            timestamp: _utcSeconds(_now()),
            existing: null,
          ),
          existing,
        );
      });
    }
    return _enqueueMutation(
      (_MutationSession session) => _saveSearch(session, value),
    );
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> saveTrackerRecord(
    AtlasApplicationRecord value,
  ) {
    if (_epochSession != null) {
      return _enqueueEpochMutation(() async {
        _snapshot = await _readEpochSnapshot();
        final existing = _snapshot.records
            .where(
              (r) =>
                  r.envelope.payload is vault.AtlasSavedJobPayload &&
                  (r.envelope.payload as vault.AtlasSavedJobPayload).jobKey ==
                      value.jobKey,
            )
            .firstOrNull;
        return _commitEpochPayload(
          _savedJobEnvelope(
            value,
            timestamp: _utcSeconds(_now()),
            existing: null,
          ),
          existing,
        );
      });
    }
    return _enqueueMutation(
      (_MutationSession session) => _saveTrackerRecord(session, value),
    );
  }

  Future<AtlasVaultPrivateStateSnapshot> createRecord(
    vault.AtlasVaultPayloadEnvelope envelope,
  ) {
    if (_epochSession != null) {
      return _enqueueEpochMutation(() => _commitEpochPayload(envelope, null));
    }
    return _enqueueMutation(
      (session) => _commitMutation(
        session,
        envelope: envelope,
        existing: null,
        updatedAt: _utcSeconds(_now()),
        currentLogicalMetadata: (_) => null,
        verify: (_) => true,
      ),
    );
  }

  Future<AtlasVaultPrivateStateSnapshot> updateRecord({
    required String recordId,
    required String currentRevision,
    required vault.AtlasVaultPayloadEnvelope envelope,
  }) {
    if (_epochSession != null) {
      return _enqueueEpochMutation(
        () => _commitEpochRecord(
          envelope: envelope,
          recordId: recordId,
          expectedRevision: currentRevision,
        ),
      );
    }
    final existing = _recordMetadata[recordId];
    if (existing == null || existing.record.revision != currentRevision) {
      return Future<AtlasVaultPrivateStateSnapshot>.error(
        const AtlasVaultPrivateStateException(),
      );
    }
    return _enqueueMutation(
      (session) => _commitMutation(
        session,
        envelope: envelope,
        existing: existing,
        updatedAt: _utcSeconds(_now()),
        currentLogicalMetadata: (hydrated) => hydrated.recordMetadata[recordId],
        verify: (hydrated) =>
            hydrated.recordMetadata[recordId]?.envelope == envelope,
      ),
    );
  }

  Future<AtlasVaultPrivateStateSnapshot> deleteRecord({
    required String recordId,
    required String currentRevision,
  }) {
    if (_epochSession != null) {
      return _enqueueEpochMutation(
        () => _commitEpochRecord(
          envelope: null,
          recordId: recordId,
          expectedRevision: currentRevision,
        ),
      );
    }
    final existing = _recordMetadata[recordId];
    if (existing == null || existing.record.revision != currentRevision) {
      return Future<AtlasVaultPrivateStateSnapshot>.error(
        const AtlasVaultPrivateStateException(),
      );
    }
    return _enqueueMutation(
      (session) => _commitMutation(
        session,
        envelope: existing.envelope,
        existing: existing,
        deleted: true,
        updatedAt: _utcSeconds(_now()),
        currentLogicalMetadata: (hydrated) => hydrated.recordMetadata[recordId],
        verify: (hydrated) =>
            hydrated.tombstoneRecords[recordId]?.parentRevision ==
            currentRevision,
      ),
    );
  }

  Future<T> withInteroperabilitySession<T>(
    Future<T> Function(AtlasVaultInteroperabilitySession session) operation,
  ) {
    // Legacy backup transport cannot export a stale pre-P5 projection as current.
    if (_epochSession != null) {
      return Future<T>.error(const AtlasVaultPrivateStateException());
    }
    final generation = _generation;
    final vaultId = _vaultId;
    final key = _vaultKey;
    if (!isActive ||
        vaultId == null ||
        key == null ||
        _pendingMutationCount != 0 ||
        _interoperabilityOperation != null) {
      return Future<T>.error(const AtlasVaultPrivateStateException());
    }

    final keyCopy = Uint8List.fromList(key);
    final completer = Completer<T>();
    late final Future<void> retained;
    retained = () async {
      AtlasVaultInteroperabilitySession? session;
      try {
        final store = await _localStoreIO.read(vaultId);
        if (store == null ||
            store.vaultMetadata.vaultId != vaultId ||
            !_active ||
            _generation != generation ||
            _vaultId != vaultId) {
          throw const AtlasVaultPrivateStateException();
        }
        session = AtlasVaultInteroperabilitySession._(
          generation: generation,
          vaultId: vaultId,
          vaultKey: keyCopy,
          localStore: store,
          readLocalStore: () => _localStoreIO.read(vaultId),
          replaceLocalStore:
              (
                vault.AtlasVaultLocalStore updated, {
                required String expectedSha256,
              }) {
                if (!_active ||
                    _generation != generation ||
                    _vaultId != vaultId ||
                    updated.vaultMetadata.vaultId != vaultId) {
                  throw const AtlasVaultPrivateStateException();
                }
                return _localStoreIO.replace(
                  vaultId,
                  updated,
                  expectedSha256: expectedSha256,
                );
              },
        );
        final value = await operation(session);
        if (!_active || _generation != generation || _vaultId != vaultId) {
          throw const AtlasVaultPrivateStateException();
        }
        completer.complete(value);
      } catch (_) {
        if (!completer.isCompleted) {
          completer.completeError(const AtlasVaultPrivateStateException());
        }
      } finally {
        session?.destroy();
        _wipe(keyCopy);
        if (identical(_interoperabilityOperation, retained)) {
          _interoperabilityOperation = null;
        }
      }
    }();
    _interoperabilityOperation = retained;
    return completer.future;
  }

  @override
  Future<void> deactivate() async {
    _activationGeneration += 1;
    _activating = false;
    if (_deactivating) {
      await _mutationTail;
      await _interoperabilityOperation;
      return;
    }
    _deactivating = true;
    try {
      await _mutationTail;
      await _interoperabilityOperation;
    } finally {
      _generation += 1;
      _clearSession();
      _deactivating = false;
    }
  }

  Future<AtlasVaultPrivateStateSnapshot> _enqueueEpochMutation(
    Future<AtlasVaultPrivateStateSnapshot> Function() operation,
  ) {
    final generation = _generation;
    if (!isActive || _epochSession == null) {
      return Future.error(const AtlasVaultPrivateStateException());
    }
    _pendingMutationCount++;
    final result = _mutationTail.then((_) async {
      if (!isActive || _generation != generation) {
        throw const AtlasVaultPrivateStateException();
      }
      final value = await operation();
      if (!isActive || _generation != generation) {
        throw const AtlasVaultPrivateStateException();
      }
      return value;
    });
    _mutationTail = result
        .then<void>((_) {}, onError: (Object _) {})
        .whenComplete(() => _pendingMutationCount--);
    return result;
  }

  Future<AtlasVaultPrivateStateSnapshot> _enqueueMutation(
    Future<AtlasVaultPrivateStateSnapshot> Function(_MutationSession session)
    operation,
  ) {
    final generation = _generation;
    final vaultId = _vaultId;
    final key = _vaultKey;
    if (!isActive ||
        vaultId == null ||
        key == null ||
        _interoperabilityOperation != null) {
      return Future<AtlasVaultPrivateStateSnapshot>.error(
        const AtlasVaultPrivateStateException(),
      );
    }
    _pendingMutationCount += 1;
    final keyCopy = Uint8List.fromList(key);
    final previous = _mutationTail;
    final completer = Completer<AtlasVaultPrivateStateSnapshot>();
    final work = () async {
      try {
        await previous;
        if (!isActive || _generation != generation || _vaultId != vaultId) {
          throw const AtlasVaultPrivateStateException();
        }
        final result = await operation(
          _MutationSession(
            generation: generation,
            vaultId: vaultId,
            vaultKey: keyCopy,
          ),
        );
        completer.complete(result);
      } catch (_) {
        completer.completeError(const AtlasVaultPrivateStateException());
      } finally {
        _pendingMutationCount -= 1;
        _wipe(keyCopy);
      }
    }();
    _mutationTail = work.then<void>((_) {}, onError: (_) {});
    return completer.future;
  }

  Future<AtlasVaultPrivateStateSnapshot> _saveSearch(
    _MutationSession session,
    AtlasSavedSearch value,
  ) async {
    final existing = _savedSearchMetadata[value.name];
    final timestamp = _utcSeconds(_now());
    final envelope = _savedSearchEnvelope(
      value,
      timestamp: timestamp,
      existing: existing,
    );
    return _commitMutation(
      session,
      envelope: envelope,
      existing: existing,
      updatedAt: timestamp,
      currentLogicalMetadata: (hydrated) =>
          hydrated.savedSearchMetadata[value.name],
      verify: (hydrated) {
        final committed = hydrated.savedSearchMetadata[value.name];
        final projected = hydrated.snapshot.savedSearches
            .where((candidate) => candidate.name == value.name)
            .toList(growable: false);
        return committed != null &&
            projected.length == 1 &&
            _sameJson(
              projected.single.toJson(),
              _searchForCommit(value, timestamp, existing).toJson(),
            );
      },
    );
  }

  Future<AtlasVaultPrivateStateSnapshot> _saveTrackerRecord(
    _MutationSession session,
    AtlasApplicationRecord value,
  ) async {
    final existing = _trackerMetadata[value.jobKey];
    final timestamp = _utcSeconds(_now());
    final committedValue = _trackerForCommit(value, timestamp, existing);
    final envelope = _savedJobEnvelope(
      committedValue,
      timestamp: timestamp,
      existing: existing,
    );
    return _commitMutation(
      session,
      envelope: envelope,
      existing: existing,
      updatedAt: timestamp,
      currentLogicalMetadata: (hydrated) =>
          hydrated.trackerMetadata[value.jobKey],
      verify: (hydrated) {
        final committed = hydrated.trackerMetadata[value.jobKey];
        final projected = hydrated.snapshot.trackerRecords
            .where((candidate) => candidate.jobKey == value.jobKey)
            .toList(growable: false);
        return committed != null &&
            projected.length == 1 &&
            _sameJson(projected.single.toJson(), committedValue.toJson());
      },
    );
  }

  Future<AtlasVaultPrivateStateSnapshot> _commitMutation(
    _MutationSession session, {
    required vault.AtlasVaultPayloadEnvelope envelope,
    required _PrivateRecordMetadata? existing,
    bool deleted = false,
    required String updatedAt,
    required _PrivateRecordMetadata? Function(_HydratedPrivateState hydrated)
    currentLogicalMetadata,
    required bool Function(_HydratedPrivateState hydrated) verify,
  }) async {
    Uint8List? nonce;
    Uint8List? plaintext;
    try {
      final current = await _localStoreIO.read(session.vaultId);
      if (current == null || current.vaultMetadata.vaultId != session.vaultId) {
        throw const AtlasVaultPrivateStateException();
      }
      final currentHydrated = await _hydrate(
        vaultId: session.vaultId,
        vaultKey: session.vaultKey,
        store: current,
      );
      if (existing == null && currentLogicalMetadata(currentHydrated) != null) {
        throw const AtlasVaultPrivateStateException();
      }
      final currentMetadata = existing == null
          ? null
          : _metadataForRecordId(currentHydrated, existing.record.id);
      if (existing != null &&
          (currentMetadata == null ||
              currentMetadata.record.revision != existing.record.revision ||
              currentMetadata.record.keyId != existing.record.keyId)) {
        throw const AtlasVaultPrivateStateException();
      }

      final recordId = existing?.record.id ?? _uuidProvider();
      final revision = _uuidProvider();
      nonce = Uint8List.fromList(_nonceProvider());
      if (nonce.length != vault.AtlasVaultEncryptedRecord.nonceByteCount) {
        throw const AtlasVaultPrivateStateException();
      }
      final template =
          vault.AtlasVaultEncryptedRecord.fromJson(<String, Object?>{
            'id': recordId,
            'schema_version':
                vault.AtlasVaultEncryptedRecord.supportedSchemaVersion,
            'revision': revision,
            'parent_revision': existing?.record.revision,
            'deleted': deleted,
            'key_id': existing?.record.keyId ?? _recordKeyId,
            'nonce': base64Encode(nonce),
            'ciphertext': base64Encode(
              Uint8List(vault.AtlasVaultEncryptedRecord.gcmTagByteCount),
            ),
          });
      plaintext = deleted ? Uint8List(0) : envelope.canonicalBytes();
      final encrypted = await vault.sealAtlasVaultRecord(
        plaintext: plaintext,
        vaultKey: session.vaultKey,
        vaultId: session.vaultId,
        record: template,
      );
      final records = current.records.toList(growable: true);
      if (existing == null) {
        records.add(encrypted);
      } else {
        final index = records.indexWhere((record) => record.id == recordId);
        if (index < 0) {
          throw const AtlasVaultPrivateStateException();
        }
        records[index] = encrypted;
      }
      final updatedStore = vault.AtlasVaultLocalStore.fromJson(
        <String, Object?>{
          ...current.toJson(),
          'updated_at': updatedAt,
          'records': <Object?>[for (final record in records) record.toJson()],
        },
      );
      final expectedDigest = await vault.atlasVaultSha256Hex(
        current.canonicalBytes(),
      );
      await _localStoreIO.replace(
        session.vaultId,
        updatedStore,
        expectedSha256: expectedDigest,
      );
      final committedStore = await _localStoreIO.read(session.vaultId);
      if (committedStore == null) {
        throw const AtlasVaultPrivateStateException();
      }
      final committedHydrated = await _hydrate(
        vaultId: session.vaultId,
        vaultKey: session.vaultKey,
        store: committedStore,
      );
      final committedRecord = deleted
          ? committedHydrated.tombstoneRecords[recordId]
          : committedHydrated.recordMetadata[recordId]?.record;
      if (committedRecord == null ||
          committedRecord.revision != revision ||
          committedRecord.parentRevision != existing?.record.revision ||
          committedRecord.keyId != (existing?.record.keyId ?? _recordKeyId) ||
          !verify(committedHydrated) ||
          !_active ||
          _generation != session.generation ||
          _vaultId != session.vaultId) {
        throw const AtlasVaultPrivateStateException();
      }
      _installHydrated(committedHydrated);
      return _copySnapshot(_snapshot);
    } catch (_) {
      throw const AtlasVaultPrivateStateException();
    } finally {
      _wipe(nonce);
      _wipe(plaintext);
    }
  }

  Future<_HydratedPrivateState> _hydrate({
    required String vaultId,
    required Uint8List vaultKey,
    required vault.AtlasVaultLocalStore store,
  }) async {
    if (store.vaultMetadata.vaultId != vaultId) {
      throw const AtlasVaultPrivateStateException();
    }
    final savedSearches = <AtlasSavedSearch>[];
    final trackerRecords = <AtlasApplicationRecord>[];
    final savedMetadata = <String, _PrivateRecordMetadata>{};
    final trackerMetadata = <String, _PrivateRecordMetadata>{};
    final recordMetadata = <String, _PrivateRecordMetadata>{};
    final tombstoneRecords = <String, vault.AtlasVaultEncryptedRecord>{};
    final records = <AtlasVaultPrivateRecord>[];
    final tombstones = <AtlasVaultPrivateTombstone>[];

    for (final record in store.records) {
      Uint8List? plaintext;
      try {
        plaintext = await vault.openAtlasVaultRecord(
          vaultKey: vaultKey,
          vaultId: vaultId,
          record: record,
        );
        if (record.deleted) {
          tombstoneRecords[record.id] = record;
          tombstones.add(
            AtlasVaultPrivateTombstone(
              recordId: record.id,
              revision: record.revision,
              parentRevision: record.parentRevision,
              keyId: record.keyId,
            ),
          );
          continue;
        }
        final envelope = vault.AtlasVaultPayloadEnvelope.decodeJson(
          utf8.decode(plaintext, allowMalformed: false),
        );
        if (recordMetadata.containsKey(record.id)) {
          throw const AtlasVaultPrivateStateException();
        }
        final metadata = _PrivateRecordMetadata(
          record: record,
          envelope: envelope,
        );
        recordMetadata[record.id] = metadata;
        records.add(
          AtlasVaultPrivateRecord(
            recordId: record.id,
            revision: record.revision,
            parentRevision: record.parentRevision,
            keyId: record.keyId,
            envelope: envelope,
          ),
        );
        switch (envelope.type) {
          case vault.AtlasVaultPayloadType.savedSearch:
            final payload = envelope.payload as vault.AtlasSavedSearchPayload;
            final value = AtlasSavedSearch(
              name: payload.name,
              description: payload.description,
              request: AtlasSearchRequest.fromJson(payload.request.toJson()),
              createdAt: payload.createdAt,
              updatedAt: payload.updatedAt,
            );
            if (savedMetadata.containsKey(value.name)) {
              throw const AtlasVaultPrivateStateException();
            }
            savedSearches.add(value);
            savedMetadata[value.name] = _PrivateRecordMetadata(
              record: record,
              envelope: envelope,
            );
          case vault.AtlasVaultPayloadType.savedJob:
            final payload = envelope.payload as vault.AtlasSavedJobPayload;
            final value = AtlasApplicationRecord(
              id: payload.id ?? '',
              jobKey: payload.jobKey,
              status: payload.status,
              notes: payload.notes,
              appliedAt: payload.appliedAt,
              updatedAt: payload.updatedAt,
            );
            if (trackerMetadata.containsKey(value.jobKey)) {
              throw const AtlasVaultPrivateStateException();
            }
            trackerRecords.add(value);
            trackerMetadata[value.jobKey] = _PrivateRecordMetadata(
              record: record,
              envelope: envelope,
            );
          case vault.AtlasVaultPayloadType.applicationNote:
          case vault.AtlasVaultPayloadType.profileSnippet:
          case vault.AtlasVaultPayloadType.draftMetadata:
            break;
        }
      } finally {
        _wipe(plaintext);
      }
    }
    return _HydratedPrivateState(
      snapshot: AtlasVaultPrivateStateSnapshot(
        savedSearches: savedSearches,
        trackerRecords: trackerRecords,
        records: records,
        tombstones: tombstones,
      ),
      savedSearchMetadata: Map<String, _PrivateRecordMetadata>.unmodifiable(
        savedMetadata,
      ),
      trackerMetadata: Map<String, _PrivateRecordMetadata>.unmodifiable(
        trackerMetadata,
      ),
      recordMetadata: Map<String, _PrivateRecordMetadata>.unmodifiable(
        recordMetadata,
      ),
      tombstoneRecords:
          Map<String, vault.AtlasVaultEncryptedRecord>.unmodifiable(
            tombstoneRecords,
          ),
    );
  }

  vault.AtlasVaultPayloadEnvelope _savedSearchEnvelope(
    AtlasSavedSearch value, {
    required String timestamp,
    required _PrivateRecordMetadata? existing,
  }) {
    final committed = _searchForCommit(value, timestamp, existing);
    final existingEnvelope = existing?.envelope;
    return vault.AtlasVaultPayloadEnvelope.fromJson(<String, Object?>{
      'type': vault.AtlasVaultPayloadType.savedSearch.wireName,
      'payload_schema': vault.AtlasVaultPayloadEnvelope.supportedPayloadSchema,
      'payload': <String, Object?>{
        'name': committed.name,
        'summary': _savedSearchSummary(committed.request),
        if (committed.description != null) 'description': committed.description,
        'request': committed.request.toJson(),
        if (committed.createdAt != null) 'created_at': committed.createdAt,
        if (committed.updatedAt != null) 'updated_at': committed.updatedAt,
      },
      'client_created_at': existingEnvelope?.clientCreatedAt ?? timestamp,
      'client_updated_at': timestamp,
    });
  }

  AtlasSavedSearch _searchForCommit(
    AtlasSavedSearch value,
    String timestamp,
    _PrivateRecordMetadata? existing,
  ) {
    final existingPayload = existing?.envelope.payload;
    final prior = existingPayload is vault.AtlasSavedSearchPayload
        ? existingPayload
        : null;
    return AtlasSavedSearch(
      name: value.name,
      description: value.description,
      request: AtlasSearchRequest.fromJson(value.request.toJson()),
      createdAt: prior?.createdAt ?? value.createdAt ?? timestamp,
      updatedAt: timestamp,
    );
  }

  vault.AtlasVaultPayloadEnvelope _savedJobEnvelope(
    AtlasApplicationRecord value, {
    required String timestamp,
    required _PrivateRecordMetadata? existing,
  }) {
    final payload = <String, Object?>{
      if (value.id.isNotEmpty) 'id': value.id,
      'job_key': value.jobKey,
      'status': value.status,
      if (value.notes != null) 'notes': value.notes,
      if (value.appliedAt != null) 'applied_at': value.appliedAt,
      if (value.updatedAt != null) 'updated_at': value.updatedAt,
    };
    return vault.AtlasVaultPayloadEnvelope.fromJson(<String, Object?>{
      'type': vault.AtlasVaultPayloadType.savedJob.wireName,
      'payload_schema': vault.AtlasVaultPayloadEnvelope.supportedPayloadSchema,
      'payload': payload,
      'client_created_at': existing?.envelope.clientCreatedAt ?? timestamp,
      'client_updated_at': timestamp,
    });
  }

  AtlasApplicationRecord _trackerForCommit(
    AtlasApplicationRecord value,
    String timestamp,
    _PrivateRecordMetadata? existing,
  ) {
    final existingPayload = existing?.envelope.payload;
    final prior = existingPayload is vault.AtlasSavedJobPayload
        ? existingPayload
        : null;
    return AtlasApplicationRecord(
      id: value.id.isEmpty ? prior?.id ?? '' : value.id,
      jobKey: value.jobKey,
      status: value.status,
      notes: value.notes,
      appliedAt: value.appliedAt,
      updatedAt: timestamp,
    );
  }

  String _savedSearchSummary(AtlasSearchRequest request) {
    final text = request.text?.trim();
    return text == null || text.isEmpty ? 'All open jobs' : text;
  }

  _PrivateRecordMetadata? _metadataForRecordId(
    _HydratedPrivateState hydrated,
    String recordId,
  ) {
    return hydrated.recordMetadata[recordId];
  }

  void _installHydrated(_HydratedPrivateState hydrated) {
    _snapshot = hydrated.snapshot;
    _savedSearchMetadata = hydrated.savedSearchMetadata;
    _trackerMetadata = hydrated.trackerMetadata;
    _recordMetadata = hydrated.recordMetadata;
  }

  Future<AtlasVaultPrivateStateSnapshot> _commitEpochPayload(
    vault.AtlasVaultPayloadEnvelope envelope,
    AtlasVaultPrivateRecord? existing,
  ) => _commitEpochRecord(
    envelope: existing == null
        ? envelope
        : vault.AtlasVaultPayloadEnvelope.fromJson({
            ...envelope.toJson(),
            'client_created_at': existing.envelope.clientCreatedAt,
          }),
    recordId: existing?.recordId ?? _uuidProvider(),
    expectedRevision: existing?.revision,
  );

  Future<AtlasVaultPrivateStateSnapshot> _commitEpochRecord({
    required vault.AtlasVaultPayloadEnvelope? envelope,
    required String recordId,
    String? expectedRevision,
  }) async {
    _requireActive();
    final generation = _generation, session = _epochSession;
    try {
      if (session == null) throw const AtlasVaultPrivateStateException();
      await session.commit(
        payload: envelope,
        objectID: recordId,
        expectedRevision: expectedRevision,
      );
      _snapshot = await _readEpochSnapshot();
      return _copySnapshot(_snapshot);
    } catch (error) {
      if (_generation == generation && identical(session, _epochSession)) {
        _recordEpochFailure(error);
      }
      throw const AtlasVaultPrivateStateException();
    }
  }

  Future<AtlasVaultPrivateStateSnapshot> _readEpochSnapshot() async {
    final generation = _generation, session = _epochSession;
    try {
      if (session == null) throw const AtlasVaultPrivateStateException();
      final rows = await session.read();
      final records = <AtlasVaultPrivateRecord>[],
          tombstones = <AtlasVaultPrivateTombstone>[];
      final searches = <AtlasSavedSearch>[], jobs = <AtlasApplicationRecord>[];
      for (final row in rows) {
        final e = row.operation.envelope, payload = row.payload;
        if (payload == null) {
          tombstones.add(
            AtlasVaultPrivateTombstone(
              recordId: e.objectId,
              revision: e.revision,
              parentRevision: e.parentRevision,
              keyId: 'epoch-${e.keyEpoch}',
            ),
          );
          continue;
        }
        records.add(
          AtlasVaultPrivateRecord(
            recordId: e.objectId,
            revision: e.revision,
            parentRevision: e.parentRevision,
            keyId: 'epoch-${e.keyEpoch}',
            envelope: payload,
          ),
        );
        final value = payload.payload;
        if (value is vault.AtlasSavedSearchPayload) {
          searches.add(
            AtlasSavedSearch(
              name: value.name,
              description: value.description,
              request: AtlasSearchRequest.fromJson(value.request.toJson()),
              createdAt: value.createdAt,
              updatedAt: value.updatedAt,
            ),
          );
        } else if (value is vault.AtlasSavedJobPayload) {
          jobs.add(
            AtlasApplicationRecord(
              id: value.id ?? '',
              jobKey: value.jobKey,
              status: value.status,
              notes: value.notes,
              appliedAt: value.appliedAt,
              updatedAt: value.updatedAt,
            ),
          );
        }
      }
      return AtlasVaultPrivateStateSnapshot(
        savedSearches: searches,
        trackerRecords: jobs,
        records: records,
        tombstones: tombstones,
      );
    } catch (error) {
      if (_generation == generation && identical(session, _epochSession)) {
        _recordEpochFailure(error);
      }
      throw const AtlasVaultPrivateStateException();
    }
  }

  void _recordEpochFailure(Object error) {
    final code = error is AtlasVaultRotationException ? error.code : '';
    _runtimeState = switch (code) {
      'ATLAS_ACTIVATION_PENDING' => 'ACTIVATION_PENDING',
      'ATLAS_CATCH_UP_PENDING' => 'CATCH_UP_PENDING',
      'ATLAS_CLEANUP_PENDING' => 'CLEANUP_PENDING',
      'ATLAS_DEVICE_REVOKED' => 'REVOKED',
      _ => 'RECOVERY_PENDING',
    };
  }

  void _clearSession() {
    _epochSession?.close();
    _epochSession = null;
    _runtimeState = 'LOCKED';
    _active = false;
    _vaultId = null;
    _wipe(_vaultKey);
    _vaultKey = null;
    _snapshot = AtlasVaultPrivateStateSnapshot(
      savedSearches: const <AtlasSavedSearch>[],
      trackerRecords: const <AtlasApplicationRecord>[],
    );
    _savedSearchMetadata = const <String, _PrivateRecordMetadata>{};
    _trackerMetadata = const <String, _PrivateRecordMetadata>{};
    _recordMetadata = const <String, _PrivateRecordMetadata>{};
  }

  void _requireActive() {
    if (!isActive ||
        _vaultId == null ||
        (_vaultKey == null && _epochSession == null)) {
      throw const AtlasVaultPrivateStateException();
    }
  }

  void _requireCurrentActivation(int activationGeneration) {
    if (!_activating ||
        _deactivating ||
        _activationGeneration != activationGeneration) {
      throw const AtlasVaultPrivateStateException();
    }
  }

  static AtlasVaultPrivateStateSnapshot _copySnapshot(
    AtlasVaultPrivateStateSnapshot value,
  ) {
    return AtlasVaultPrivateStateSnapshot(
      savedSearches: value.savedSearches,
      trackerRecords: value.trackerRecords,
      records: value.records,
      tombstones: value.tombstones,
    );
  }

  static bool _sameJson(Map<String, Object?> left, Map<String, Object?> right) {
    return jsonEncode(left) == jsonEncode(right);
  }

  static void _wipe(Uint8List? value) {
    value?.fillRange(0, value.length, 0);
  }

  static String _utcSeconds(DateTime value) {
    final utc = value.toUtc();
    String two(int number) => number.toString().padLeft(2, '0');
    return '${utc.year.toString().padLeft(4, '0')}-'
        '${two(utc.month)}-${two(utc.day)}T'
        '${two(utc.hour)}:${two(utc.minute)}:${two(utc.second)}Z';
  }

  static String _secureUuidV4() {
    final random = Random.secure();
    final bytes = Uint8List.fromList(
      List<int>.generate(16, (_) => random.nextInt(256)),
    );
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    final hex = bytes
        .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
        .join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-'
        '${hex.substring(20)}';
  }

  static Uint8List _secureNonce() {
    final random = Random.secure();
    return Uint8List.fromList(
      List<int>.generate(
        vault.AtlasVaultEncryptedRecord.nonceByteCount,
        (_) => random.nextInt(256),
      ),
    );
  }

  @override
  String toString() => 'AtlasVaultPrivateStateRuntime(<redacted>)';
}

final class AtlasVaultInteroperabilitySession {
  AtlasVaultInteroperabilitySession._({
    required this.generation,
    required this.vaultId,
    required Uint8List vaultKey,
    required this.localStore,
    required Future<vault.AtlasVaultLocalStore?> Function() readLocalStore,
    required Future<void> Function(
      vault.AtlasVaultLocalStore store, {
      required String expectedSha256,
    })
    replaceLocalStore,
  }) : _vaultKey = Uint8List.fromList(vaultKey),
       // Keep callback labels readable at the session boundary.
       // ignore: prefer_initializing_formals
       _readLocalStore = readLocalStore,
       // ignore: prefer_initializing_formals
       _replaceLocalStore = replaceLocalStore;

  final int generation;
  final String vaultId;
  final vault.AtlasVaultLocalStore localStore;
  final Uint8List _vaultKey;
  final Future<vault.AtlasVaultLocalStore?> Function() _readLocalStore;
  final Future<void> Function(
    vault.AtlasVaultLocalStore store, {
    required String expectedSha256,
  })
  _replaceLocalStore;
  bool _destroyed = false;

  Uint8List copyVaultKey() {
    _requireActive();
    return Uint8List.fromList(_vaultKey);
  }

  Future<vault.AtlasVaultLocalStore> readCurrentLocalStore() async {
    _requireActive();
    final value = await _readLocalStore();
    _requireActive();
    if (value == null || value.vaultMetadata.vaultId != vaultId) {
      throw const AtlasVaultPrivateStateException();
    }
    return value;
  }

  Future<void> replaceLocalStore(
    vault.AtlasVaultLocalStore store, {
    required String expectedSha256,
  }) async {
    _requireActive();
    if (store.vaultMetadata.vaultId != vaultId) {
      throw const AtlasVaultPrivateStateException();
    }
    await _replaceLocalStore(store, expectedSha256: expectedSha256);
    _requireActive();
  }

  void destroy() {
    if (_destroyed) {
      return;
    }
    _wipe(_vaultKey);
    _destroyed = true;
  }

  void _requireActive() {
    if (_destroyed) {
      throw const AtlasVaultPrivateStateException();
    }
  }

  static void _wipe(Uint8List value) {
    value.fillRange(0, value.length, 0);
  }

  @override
  String toString() => 'AtlasVaultInteroperabilitySession(<redacted>)';
}

final class _MutationSession {
  const _MutationSession({
    required this.generation,
    required this.vaultId,
    required this.vaultKey,
  });

  final int generation;
  final String vaultId;
  final Uint8List vaultKey;
}

final class _PrivateRecordMetadata {
  const _PrivateRecordMetadata({required this.record, required this.envelope});

  final vault.AtlasVaultEncryptedRecord record;
  final vault.AtlasVaultPayloadEnvelope envelope;
}

final class _HydratedPrivateState {
  const _HydratedPrivateState({
    required this.snapshot,
    required this.savedSearchMetadata,
    required this.trackerMetadata,
    required this.recordMetadata,
    required this.tombstoneRecords,
  });

  final AtlasVaultPrivateStateSnapshot snapshot;
  final Map<String, _PrivateRecordMetadata> savedSearchMetadata;
  final Map<String, _PrivateRecordMetadata> trackerMetadata;
  final Map<String, _PrivateRecordMetadata> recordMetadata;
  final Map<String, vault.AtlasVaultEncryptedRecord> tombstoneRecords;
}
