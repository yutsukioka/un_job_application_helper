import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import '../../atlas.dart';
import '../../atlas_vault.dart' as vault;
import 'android_storage.dart';
import 'local_store_io.dart';

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
  }) : savedSearches = List<AtlasSavedSearch>.unmodifiable(savedSearches),
       trackerRecords = List<AtlasApplicationRecord>.unmodifiable(
         trackerRecords,
       );

  final List<AtlasSavedSearch> savedSearches;
  final List<AtlasApplicationRecord> trackerRecords;

  @override
  String toString() => 'AtlasVaultPrivateStateSnapshot(<redacted>)';
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
  }) : // Keep public constructor parameter names stable.
       // ignore: prefer_initializing_formals
       _secureKeyStore = secureKeyStore,
       // ignore: prefer_initializing_formals
       _localStoreIO = localStoreIO,
       _now = now ?? DateTime.now,
       _uuidProvider = uuidProvider ?? _secureUuidV4,
       _nonceProvider = nonceProvider ?? _secureNonce;

  static const _recordKeyId = 'primary-android-local-key-v1';

  final AtlasVaultSecureKeyStore _secureKeyStore;
  final AtlasVaultLocalStoreIO _localStoreIO;
  final DateTime Function() _now;
  final String Function() _uuidProvider;
  final Uint8List Function() _nonceProvider;

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
  Future<void> _mutationTail = Future<void>.value();
  int _pendingMutationCount = 0;
  Future<void>? _interoperabilityOperation;

  @override
  bool get isActive => _active && !_deactivating;

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
    return _copySnapshot(_snapshot);
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> saveSearch(AtlasSavedSearch value) {
    return _enqueueMutation(
      (_MutationSession session) => _saveSearch(session, value),
    );
  }

  @override
  Future<AtlasVaultPrivateStateSnapshot> saveTrackerRecord(
    AtlasApplicationRecord value,
  ) {
    return _enqueueMutation(
      (_MutationSession session) => _saveTrackerRecord(session, value),
    );
  }

  Future<T> withInteroperabilitySession<T>(
    Future<T> Function(AtlasVaultInteroperabilitySession session) operation,
  ) {
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
            'deleted': false,
            'key_id': existing?.record.keyId ?? _recordKeyId,
            'nonce': base64Encode(nonce),
            'ciphertext': base64Encode(
              Uint8List(vault.AtlasVaultEncryptedRecord.gcmTagByteCount),
            ),
          });
      plaintext = envelope.canonicalBytes();
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
      final committedMetadata = _metadataForRecordId(
        committedHydrated,
        recordId,
      );
      if (committedMetadata == null ||
          committedMetadata.record.revision != revision ||
          committedMetadata.record.parentRevision !=
              existing?.record.revision ||
          committedMetadata.record.keyId !=
              (existing?.record.keyId ?? _recordKeyId) ||
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

    for (final record in store.records) {
      Uint8List? plaintext;
      try {
        plaintext = await vault.openAtlasVaultRecord(
          vaultKey: vaultKey,
          vaultId: vaultId,
          record: record,
        );
        if (record.deleted) {
          continue;
        }
        final envelope = vault.AtlasVaultPayloadEnvelope.decodeJson(
          utf8.decode(plaintext, allowMalformed: false),
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
      ),
      savedSearchMetadata: Map<String, _PrivateRecordMetadata>.unmodifiable(
        savedMetadata,
      ),
      trackerMetadata: Map<String, _PrivateRecordMetadata>.unmodifiable(
        trackerMetadata,
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
    for (final metadata in hydrated.savedSearchMetadata.values) {
      if (metadata.record.id == recordId) {
        return metadata;
      }
    }
    for (final metadata in hydrated.trackerMetadata.values) {
      if (metadata.record.id == recordId) {
        return metadata;
      }
    }
    return null;
  }

  void _installHydrated(_HydratedPrivateState hydrated) {
    _snapshot = hydrated.snapshot;
    _savedSearchMetadata = hydrated.savedSearchMetadata;
    _trackerMetadata = hydrated.trackerMetadata;
  }

  void _clearSession() {
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
  }

  void _requireActive() {
    if (!isActive || _vaultId == null || _vaultKey == null) {
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
  });

  final AtlasVaultPrivateStateSnapshot snapshot;
  final Map<String, _PrivateRecordMetadata> savedSearchMetadata;
  final Map<String, _PrivateRecordMetadata> trackerMetadata;
}
